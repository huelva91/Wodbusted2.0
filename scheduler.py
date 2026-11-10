import asyncio
import datetime
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import TIMEZONE, DAYS_OF_WEEK
from database import (
    get_all_active_rules,
    update_rule_last_booked,
    log_booking_action,
    update_user_session,
    get_all_active_caza_plazas,
    deactivate_caza_plaza,
    add_caza_plaza,
)
from wodbuster_client import WodBusterClient

logger = logging.getLogger("scheduler")


def matches_filter(class_name: str, filter_name: str) -> bool:
    """Check if class name matches the given filter."""
    class_upper = class_name.upper()
    filt = (filter_name or "ALL").strip().upper()
    if filt == "ALL":
        return True
    if filt in ("GYMNASTIC", "GYMNASTICS", "GYM"):
        return "GYMNASTIC" in class_upper or "GIMNAST" in class_upper
    if filt in ("WEIGHTLIFTING", "WEIGHT", "HALTEROFILIA", "HALTERO"):
        return "WEIGHT" in class_upper or "HALTERO" in class_upper
    if filt in ("HYBRID", "HIBRIDO"):
        return "HYBRID" in class_upper or "HIBRID" in class_upper
    if filt == "WOD":
        return "WOD" in class_upper
    if filt == "OPEN":
        return "OPEN" in class_upper
    return filt in class_upper


def get_next_weekday(base_date: datetime.date, target_weekday: int) -> datetime.date:
    """Find next occurrence of weekday (0=Lunes...6=Domingo). If today is target, returns today if time is in future or today."""
    days_ahead = target_weekday - base_date.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return base_date + datetime.timedelta(days=days_ahead)


class AutoBookingScheduler:
    def __init__(self, bot=None):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone=TIMEZONE)
        self.is_running = False

    def start(self):
        if not self.is_running:
            # Check rules every 30 seconds
            self.scheduler.add_job(
                self.check_and_execute_rules,
                "interval",
                seconds=30,
                id="auto_booking_checker",
                max_instances=3,
                replace_existing=True,
            )
            # Check Caza-Plazas every 20 seconds for newly freed spots
            self.scheduler.add_job(
                self.check_caza_plazas,
                "interval",
                seconds=20,
                id="caza_plazas_checker",
                max_instances=3,
                replace_existing=True,
            )
            self.scheduler.start()
            self.is_running = True
            logger.info("AutoBookingScheduler started with rules (30s) and Caza-Plazas (20s).")

    def stop(self):
        if self.is_running:
            self.scheduler.shutdown(wait=False)
            self.is_running = False
            logger.info("AutoBookingScheduler stopped.")

    async def notify_user(self, chat_id: int, message: str, reply_markup=None):
        """Send asynchronous Telegram notification to user with quick access menu."""
        if not self.bot:
            logger.warning(f"No bot instance set to send notification to {chat_id}")
            return
        if reply_markup is None:
            reply_markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main"),
                    InlineKeyboardButton("📋 Mis Reservas", callback_data="menu:my_bookings"),
                ]
            ])
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram message to {chat_id}: {e}")

    async def check_and_execute_rules(self):
        """Evaluate all active rules and execute bookings when opening window arrives."""
        try:
            rules = get_all_active_rules()
            if not rules:
                return

            now = datetime.datetime.now(TIMEZONE)
            today = now.date()

            # Helper to calculate target class date for a rule
            def get_rule_target_date(rule: dict) -> datetime.date:
                target_dow = rule["day_of_week"]
                class_time_str = rule["class_time"]
                c_parts = [int(p) for p in class_time_str.split(":")[:2]]
                c_min = c_parts[1] if len(c_parts) > 1 else 0
                c_time = datetime.time(c_parts[0], c_min)
                target_date = get_next_weekday(today, target_dow)
                target_dt = TIMEZONE.localize(datetime.datetime.combine(target_date, c_time))
                if now >= target_dt:
                    target_date = target_date + datetime.timedelta(days=7)
                return target_date

            # Group rules by (chat_id, target_class_date)
            groups = {}
            for r in rules:
                target_date = get_rule_target_date(r)
                key = (r["chat_id"], target_date)
                if key not in groups:
                    groups[key] = []
                groups[key].append(r)

            # Process each user/date group concurrently
            await asyncio.gather(
                *(self._process_rule_group(chat_id, target_date, group_rules, now)
                  for (chat_id, target_date), group_rules in groups.items()),
                return_exceptions=True
            )
        except Exception as e:
            logger.exception(f"Unexpected error in scheduler check_and_execute_rules: {e}")

    async def _process_rule_group(
        self,
        chat_id: int,
        target_class_date: datetime.date,
        rules: list,
        now: datetime.datetime,
    ):
        target_date_str = target_class_date.strftime("%Y-%m-%d")

        # Skip if all rules in group already marked booked for this date
        pending_rules = [r for r in rules if r.get("last_booked_date") != target_date_str]
        if not pending_rules:
            return

        # Sort pending rules: Special classes (Gymnastics, Weightlifting, Hybrid) first, then WOD, then ALL, then by time
        def rule_sort_key(r):
            filt = (r.get("class_name_filter") or "ALL").upper()
            is_special = any(k in filt for k in ("GYM", "WEIGHT", "HALTERO", "HYBRID"))
            prio = 0 if is_special else (1 if "WOD" in filt else 2)
            return (prio, r["class_time"])

        pending_rules.sort(key=rule_sort_key)

        # Calculate opening datetime for the highest priority rule
        primary_rule = pending_rules[0]
        days_advance = primary_rule.get("days_advance", 1)
        opening_time_str = primary_rule.get("opening_time", "22:30:00")
        o_parts = [int(p) for p in opening_time_str.split(":")[:2]]
        o_min = o_parts[1] if len(o_parts) > 1 else 0
        o_time = datetime.time(o_parts[0], o_min)
        opening_date = target_class_date - datetime.timedelta(days=days_advance)
        opening_dt = TIMEZONE.localize(datetime.datetime.combine(opening_date, o_time))

        time_to_open = (opening_dt - now).total_seconds()
        if time_to_open > 60:
            return

        if 0 < time_to_open <= 60:
            logger.info(
                f"Group ({chat_id}, {target_date_str}) opens in {time_to_open:.1f}s. "
                f"Preparing precision snipe for primary rule {primary_rule['id']} ({primary_rule.get('class_name_filter')})..."
            )
            await asyncio.sleep(time_to_open)
            now = datetime.datetime.now(TIMEZONE)

        booked_success = False
        loop = asyncio.get_running_loop()

        for idx, rule in enumerate(pending_rules):
            if booked_success:
                logger.info(f"User {chat_id} already booked for {target_date_str}. Skipping rule {rule['id']} ({rule['class_time']}).")
                break

            class_time_str = rule["class_time"]
            filter_name = (rule.get("class_name_filter") or "ALL").strip().upper()

            logger.info(
                f"Attempting booking for rule {rule['id']} "
                f"(User: {chat_id}, Date: {target_date_str}, Time: {class_time_str}, Filter: '{filter_name}')..."
            )
            result = await loop.run_in_executor(
                None,
                self._execute_booking_sync,
                rule,
                target_class_date,
                class_time_str,
                filter_name,
            )

            status, msg, class_name, class_id = result

            if status == "SUCCESS":
                update_rule_last_booked(rule["id"], target_date_str)
                booked_success = True
                log_booking_action(
                    chat_id=chat_id,
                    action="AUTO_BOOK",
                    class_name=class_name,
                    class_datetime=f"{target_date_str} {class_time_str}",
                    status="SUCCESS",
                    message=msg,
                )
                day_name = DAYS_OF_WEEK[target_class_date.weekday()]
                await self.notify_user(
                    chat_id,
                    f"🎯 *¡RESERVA AUTOMÁTICA COMPLETADA!* 🏋️‍♂️\n\n"
                    f"📅 *Día:* {day_name} ({target_class_date.strftime('%d/%m/%Y')})\n"
                    f"⏰ *Hora:* {class_time_str[:5]}\n"
                    f"🏷️ *Clase:* {class_name}\n"
                    f"📍 *Box:* Drakkar WodBuster\n\n"
                    f"✅ _Confirmado con éxito en tu cuenta._ ¡A darlo todo en el box!"
                )
            elif status == "ALREADY_BOOKED":
                update_rule_last_booked(rule["id"], target_date_str)
                booked_success = True
                logger.info(f"User {chat_id} was already booked for {target_date_str} {class_time_str} ({class_name})")
                log_booking_action(
                    chat_id=chat_id,
                    action="AUTO_BOOK",
                    class_name=class_name or "Clase",
                    class_datetime=f"{target_date_str} {class_time_str}",
                    status="ALREADY_BOOKED",
                    message=msg,
                )
            elif status == "FULL":
                update_rule_last_booked(rule["id"], target_date_str)
                log_booking_action(
                    chat_id=chat_id,
                    action="AUTO_BOOK",
                    class_name=class_name,
                    class_datetime=f"{target_date_str} {class_time_str}",
                    status="FULL",
                    message=msg,
                )
                if class_id:
                    add_caza_plaza(
                        chat_id=chat_id,
                        class_id=class_id,
                        class_date=target_date_str,
                        class_time=class_time_str,
                        class_name=class_name,
                    )
                    logger.info(f"🦅 Auto-activated Caza-Plazas for user {chat_id} on {class_name} (ID {class_id}, {target_date_str} {class_time_str})")

                day_name = DAYS_OF_WEEK[target_class_date.weekday()]
                has_fallback = (idx + 1 < len(pending_rules))
                fallback_msg = "\n🔄 _Intentando tu siguiente regla de respaldo para hoy..._" if has_fallback else ""
                await self.notify_user(
                    chat_id,
                    f"⚠️ *AVISO AUTOMÁTICO: Clase Llena*\n\n"
                    f"La clase de *{class_name}* para el *{day_name} {target_class_date.strftime('%d/%m/%Y')}* "
                    f"a las *{class_time_str[:5]}* está actualmente al completo sin plazas libres.\n\n"
                    f"🦅 *¡Modo Caza-Plazas activado automáticamente!*\n"
                    f"El bot vigilará cada 20 segundos y atrapará la primera plaza que alguien cancele.{fallback_msg}"
                )
            elif status == "NOT_FOUND":
                logger.warning(f"Class not found for rule {rule['id']} at {target_date_str} {class_time_str}: {msg}")
            elif status == "NOT_OPEN":
                logger.info(f"Classes for {target_date_str} not opened yet on server.")
            else:
                update_rule_last_booked(rule["id"], target_date_str)
                if "excedido" in (msg or "").lower() or "máximo de clases" in (msg or "").lower():
                    logger.info(f"User {chat_id} reached daily booking limit for {target_date_str}. Halting remaining rules.")
                    booked_success = True

                log_booking_action(
                    chat_id=chat_id,
                    action="AUTO_BOOK",
                    class_name=class_name or "Desconocida",
                    class_datetime=f"{target_date_str} {class_time_str}",
                    status="FAILED",
                    message=msg,
                )
                await self.notify_user(
                    chat_id,
                    f"❌ *Error al intentar reservar automáticamente*\n\n"
                    f"📅 Fecha: {target_class_date.strftime('%d/%m/%Y')} a las {class_time_str[:5]}\n"
                    f"⚠️ Mensaje del servidor: _{msg}_"
                )

    def _execute_booking_sync(
        self,
        rule: dict,
        target_date: datetime.date,
        class_time_str: str,
        filter_name: str,
    ) -> tuple:
        """Synchronous booking execution to run inside executor thread."""
        chat_id = rule["chat_id"]
        email = rule["wodbuster_email"]
        password = rule["wodbuster_password"]
        athlete_id = rule.get("athlete_id")
        cookies_blob = rule.get("cookies_blob")

        client = WodBusterClient(
            email=email,
            password=password,
            cookies_blob=cookies_blob,
            athlete_id=athlete_id,
        )

        try:
            client.login()
            # Save updated cookies & athlete_id
            update_user_session(
                chat_id=chat_id,
                cookies_blob=client.get_cookies_blob(),
                athlete_id=client.athlete_id,
            )
        except Exception as e:
            return "ERROR", f"Error de autenticación: {e}", "", None

        try:
            classes_info = client.get_classes_for_date(target_date)
        except Exception as e:
            return "ERROR", f"Error al cargar clases: {e}", "", None

        if not classes_info.get("is_open"):
            return "NOT_OPEN", "Clases aún no publicadas.", "", None

        # Format target_time_clean as HH:MM
        p = class_time_str.split(":")
        h = int(p[0])
        m = int(p[1]) if len(p) > 1 else 0
        target_time_clean = f"{h:02d}:{m:02d}"

        available_at_hour = [c for c in classes_info.get("classes", []) if c["hora"][:5] == target_time_clean]
        avail_summary = [f"{c['nombre']} (id={c['id']}, estado={c.get('tipo_estado')}, libres={c['libres']}/{c['plazas']})" for c in available_at_hour]
        logger.info(
            f"Rule {rule['id']} (User {chat_id}): Checking date {target_date} at {target_time_clean} (Filter: '{filter_name}'). "
            f"Classes found at this hour: {avail_summary}"
        )

        # Exclude NoTarifa classes from auto-booking candidates unless user explicitly requested OPEN and has tarifa
        matching_classes = []
        for c in available_at_hour:
            if c.get("tipo_estado") == "NoTarifa":
                continue
            if matches_filter(c["nombre"], filter_name):
                matching_classes.append(c)

        if not matching_classes:
            names_found = [c["nombre"] for c in available_at_hour]
            logger.warning(
                f"Rule {rule['id']}: No matching bookable class for filter '{filter_name}' at {target_date} {target_time_clean}. "
                f"Available class names: {names_found}"
            )
            return "NOT_FOUND", f"No se encontró clase disponible con filtro '{filter_name}' a las {target_time_clean} (Disponibles: {', '.join(names_found) or 'ninguna'}).", "", None

        # Prioritize inscribible / cambiable / borrable and classes with free spots
        matching_classes.sort(
            key=lambda x: (
                0 if x.get("tipo_estado") in ("Inscribible", "Cambiable", "Borrable") else 1,
                0 if not x.get("is_full") else 1,
                -x.get("libres", 0)
            )
        )

        chosen_class = matching_classes[0]
        class_name = chosen_class["nombre"]
        class_id = chosen_class["id"]
        logger.info(f"Rule {rule['id']}: Selected class '{class_name}' (ID: {class_id}, Estado: {chosen_class.get('tipo_estado')}, Booked: {chosen_class.get('is_booked')})")

        if chosen_class.get("is_booked"):
            return "ALREADY_BOOKED", "Ya estás inscrito en esta clase.", class_name, class_id

        if chosen_class.get("is_full"):
            return "FULL", "La clase está completa.", class_name, class_id

        # Perform booking!
        success, book_msg = client.book_class(
            class_id=class_id,
            target_date=target_date,
            is_change=chosen_class.get("can_change", False),
        )

        if success:
            return "SUCCESS", book_msg, class_name, class_id
        else:
            return "FAILED", book_msg, class_name, class_id

    async def check_caza_plazas(self):
        """Monitor full classes and immediately snag any spot that is freed by a cancellation."""
        try:
            watches = get_all_active_caza_plazas()
            if not watches:
                return

            now = datetime.datetime.now(TIMEZONE)
            loop = asyncio.get_running_loop()

            # Periodic heartbeat log for Caza-Plazas every 15 checks (~5 min)
            caza_counter = getattr(self, "_caza_check_count", 0)
            if caza_counter % 15 == 0:
                summary = [f"{w.get('class_name', 'Clase')} ({w['class_date']} {w['class_time']})" for w in watches]
                logger.info(f"🦅 Caza-Plazas activo comprobando {len(watches)} clase(s) cada 20s: {summary}")
            self._caza_check_count = caza_counter + 1

            for watch in watches:
                # Check if target class datetime is already in the past
                class_date_obj = datetime.datetime.strptime(watch["class_date"], "%Y-%m-%d").date()
                h_parts = [int(p) for p in str(watch["class_time"]).split(":")[:2]]
                h_hour = h_parts[0]
                h_min = h_parts[1] if len(h_parts) > 1 else 0
                class_dt = TIMEZONE.localize(datetime.datetime.combine(class_date_obj, datetime.time(h_hour, h_min)))

                if now >= class_dt:
                    logger.info(f"Caza-Plazas watch {watch['id']} ({watch.get('class_name')}) is in the past ({class_dt}). Deactivating.")
                    deactivate_caza_plaza(watch["id"])
                    continue

                # Run spot check in executor
                result = await loop.run_in_executor(None, self._check_and_hunt_spot_sync, watch, class_date_obj)
                status, msg = result

                if status == "CAUGHT":
                    deactivate_caza_plaza(watch["id"])
                    log_booking_action(
                        chat_id=watch["chat_id"],
                        action="CAZA_PLAZAS",
                        class_name=watch["class_name"],
                        class_datetime=f"{watch['class_date']} {watch['class_time']}",
                        status="SUCCESS",
                        message=msg,
                    )
                    await self.notify_user(
                        watch["chat_id"],
                        f"🦅 *¡PLAZA CAZADA CON ÉXITO!* 🎯\n\n"
                        f"¡Alguien ha cancelado su plaza y el bot te ha inscrito al instante!\n\n"
                        f"📅 *Fecha:* {watch['class_date']}\n"
                        f"⏰ *Hora:* {watch['class_time']}\n"
                        f"🏷️ *Clase:* {watch['class_name']}\n"
                        f"📍 *Box:* Drakkar CrossFit\n\n"
                        f"✅ _Inscripción confirmada automáticamente._ ¡A entrenar!"
                    )
                elif status == "ALREADY_BOOKED":
                    deactivate_caza_plaza(watch["id"])
                elif status == "ERROR":
                    logger.warning(f"Error in Caza-Plazas watch {watch['id']}: {msg}")

        except Exception as e:
            logger.exception(f"Unexpected error in check_caza_plazas: {e}")

    def _check_and_hunt_spot_sync(self, watch: dict, class_date_obj: datetime.date) -> tuple:
        """Check availability and snag spot if freed."""
        chat_id = watch["chat_id"]
        client = WodBusterClient(
            email=watch["wodbuster_email"],
            password=watch["wodbuster_password"],
            cookies_blob=watch.get("cookies_blob"),
            athlete_id=watch.get("athlete_id"),
        )

        try:
            client.login()
            update_user_session(
                chat_id=chat_id,
                cookies_blob=client.get_cookies_blob(),
                athlete_id=client.athlete_id,
            )
        except Exception as e:
            return "ERROR", f"Error de login: {e}"

        try:
            info = client.get_classes_for_date(class_date_obj)
        except Exception as e:
            return "ERROR", f"Error consultando calendario: {e}"

        target_class_id = watch["class_id"]
        target_class = None
        for c in info.get("classes", []):
            if c["id"] == target_class_id:
                target_class = c
                break

        if not target_class:
            return "NOT_FOUND", "Clase no encontrada."

        watch["class_name"] = target_class["nombre"]

        if target_class.get("is_booked"):
            return "ALREADY_BOOKED", "Ya estabas apuntado."

        # If a spot is free (not full!), immediately snipe it!
        if not target_class.get("is_full") and target_class.get("libres", 0) > 0:
            logger.info(f"SPOT FREED! Caza-Plazas attempting booking for class {target_class_id} ({watch['class_name']})...")
            success, book_msg = client.book_class(
                class_id=target_class_id,
                target_date=class_date_obj,
                is_change=target_class.get("can_change", False),
            )
            if success:
                return "CAUGHT", book_msg
            else:
                return "FAILED", book_msg

        return "STILL_FULL", "Aún completa."
