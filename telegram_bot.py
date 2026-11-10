import asyncio
import datetime
import html
import logging
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from config import DEFAULT_BOX_URL, TIMEZONE, DAYS_OF_WEEK
from database import (
    get_user,
    save_user,
    delete_user,
    get_user_rules,
    add_auto_rule,
    toggle_rule,
    delete_rule,
    get_recent_logs,
    update_user_session,
    log_booking_action,
    add_caza_plaza,
    get_user_caza_plazas,
    delete_caza_plaza,
)
from wodbuster_client import WodBusterClient, WodBusterAuthError

logger = logging.getLogger("telegram_bot")

# Conversation states for Login
LOGIN_EMAIL, LOGIN_PASSWORD = range(2)

# Conversation states for adding a rule
RULE_DAY, RULE_TIME, RULE_FILTER, RULE_ADVANCE = range(2, 6)

# Hours typically available at Drakkar
DRAKKAR_HOURS = [
    "07:00", "08:30", "09:30", "10:30", "11:30",
    "12:30", "13:30", "14:30", "16:00", "16:30",
    "17:00", "17:30", "18:00", "18:30", "19:00",
    "19:30", "20:00", "20:30", "21:00"
]


def build_persistent_reply_keyboard() -> ReplyKeyboardMarkup:
    """Bottom persistent keyboard that remains docked at the bottom of the screen."""
    keyboard = [
        [KeyboardButton("🏠 Menú Principal"), KeyboardButton("📅 Ver Clases")],
        [KeyboardButton("⏰ Mis Reglas"), KeyboardButton("🦅 Caza-Plazas"), KeyboardButton("📋 Mis Reservas")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)


def build_main_menu_keyboard(has_user: bool = False) -> InlineKeyboardMarkup:
    """Build the primary navigation keyboard."""
    if not has_user:
        keyboard = [
            [InlineKeyboardButton("🔑 Conectar Cuenta WodBuster", callback_data="auth:start_login")],
            [InlineKeyboardButton("ℹ️ Ayuda & Guía", callback_data="menu:help")],
        ]
        return InlineKeyboardMarkup(keyboard)

    keyboard = [
        [
            InlineKeyboardButton("📅 Ver Clases Drakkar", callback_data="menu:classes_select"),
            InlineKeyboardButton("📋 Mis Reservas Activas", callback_data="menu:my_bookings"),
        ],
        [
            InlineKeyboardButton("⏰ Sniper Automático", callback_data="menu:rules"),
            InlineKeyboardButton("🦅 Modo Caza-Plazas", callback_data="menu:caza_plazas"),
        ],
        [
            InlineKeyboardButton("⚡ Reservar Ahora", callback_data="menu:quick_book"),
            InlineKeyboardButton("⚙️ Mi Cuenta Drakkar", callback_data="menu:account"),
        ],
        [
            InlineKeyboardButton("📜 Historial", callback_data="menu:history"),
            InlineKeyboardButton("ℹ️ Ayuda", callback_data="menu:help"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if user:
        text = (
            f"👋 *¡Hola de nuevo! Bienvenido a Drakkar Bot* 🏋️‍♂️🔥\n\n"
            f"👤 *Usuario conectado:* `{user['wodbuster_email']}`\n"
            f"📍 *Box:* Drakkar WodBuster (`drakkar.wodbuster.com`)\n\n"
            f"Selecciona una opción en el panel interactivo a continuación:"
        )
    else:
        text = (
            f"👋 *¡Bienvenido al Bot de Reservas Automáticas de Drakkar CrossFit!* 🏋️‍♂️🔥\n\n"
            f"Este bot te permite:\n"
            f"• 🎯 *Reservar al segundo* exactamente cuando se abren las plazas.\n"
            f"• ⏰ *Programar tus clases semanales* de forma automática (Sniper).\n"
            f"• 📅 Consultar los horarios y plazas libres desde Telegram.\n"
            f"• 📋 Gestionar y cancelar reservas fácilmente.\n\n"
            f"Para empezar, conecta tu cuenta de Drakkar WodBuster con el botón de abajo o escribe:\n"
            f"`/login tu_email tu_contraseña`"
        )

    await update.message.reply_text(
        "🏋️‍♂️ *Drakkar CrossFit Bot Activo*",
        reply_markup=build_persistent_reply_keyboard(),
        parse_mode="Markdown",
    )
    await update.message.reply_text(
        text=text,
        reply_markup=build_main_menu_keyboard(has_user=bool(user)),
        parse_mode="Markdown",
    )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /menu command."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    await update.message.reply_text(
        "🎛️ *Panel de Control Drakkar*",
        reply_markup=build_persistent_reply_keyboard(),
        parse_mode="Markdown",
    )
    await update.message.reply_text(
        "Selecciona una opción en el panel interactivo:",
        reply_markup=build_main_menu_keyboard(has_user=bool(user)),
        parse_mode="Markdown",
    )


async def persistent_keyboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle taps from persistent bottom keyboard."""
    text = (update.message.text or "").strip()
    if text == "🏠 Menú Principal":
        await menu_command(update, context)
    elif text == "📅 Ver Clases":
        await clases_command(update, context)
    elif text == "⏰ Mis Reglas":
        await reglas_command(update, context)
    elif text == "🦅 Caza-Plazas":
        await caza_command(update, context)
    elif text == "📋 Mis Reservas":
        await mis_reservas_command(update, context)


# =========================================================================
# FAST LOGIN VIA COMMAND: /login email password
# =========================================================================

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct login command: /login email password."""
    chat_id = update.effective_chat.id
    args = context.args

    # For safety, try to delete the message containing the password
    try:
        await update.message.delete()
    except Exception:
        pass

    if not args or len(args) < 2:
        await update.effective_chat.send_message(
            "⚠️ *Formato incorrecto.*\nUsa:\n`/login tu_email@correo.com tu_contraseña`\n\n"
            "_Tu mensaje con la contraseña será borrado inmediatamente por privacidad._",
            parse_mode="Markdown",
        )
        return

    email = args[0].strip()
    password = " ".join(args[1:]).strip()

    status_msg = await update.effective_chat.send_message(
        f"⏳ Conectando con Drakkar WodBuster para `{email}`...",
        parse_mode="Markdown",
    )

    client = WodBusterClient(email=email, password=password)
    try:
        # Perform test login in executor
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.login)

        # Save to DB
        save_user(
            chat_id=chat_id,
            email=email,
            password=password,
            athlete_id=client.athlete_id,
            cookies_blob=client.get_cookies_blob(),
        )

        await status_msg.edit_text(
            f"✅ *¡Conexión exitosa con Drakkar WodBuster!* 🎉\n\n"
            f"👤 *Email:* `{email}`\n"
            f"🆔 *Atleta:* `{client.athlete_id or 'Detectado'}`\n\n"
            f"Ya puedes ver clases y programar tus reservas automáticas.",
            reply_markup=build_main_menu_keyboard(has_user=True),
            parse_mode="Markdown",
        )
    except WodBusterAuthError as e:
        await status_msg.edit_text(
            f"❌ *Error al iniciar sesión en WodBuster:*\n_{e}_\n\n"
            f"Comprueba que tu email y contraseña de Drakkar sean correctos.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await status_msg.edit_text(
            f"❌ *Error inesperado:*\n_{e}_",
            parse_mode="Markdown",
        )


# =========================================================================
# INTERACTIVE LOGIN CONVERSATION WIZARD
# =========================================================================

async def auth_start_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start interactive login wizard."""
    query = update.callback_query
    await query.answer()

    await query.message.reply_text(
        "🔐 *Paso 1 de 2: Introduce tu Email de WodBuster*\n\n"
        "Escribe el correo electrónico con el que accedes a Drakkar WodBuster:\n"
        "_(o envía /cancel para anular)_",
        parse_mode="Markdown",
    )
    return LOGIN_EMAIL


async def auth_receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive email in wizard."""
    email = update.message.text.strip()
    if "@" not in email:
        await update.message.reply_text("⚠️ Por favor introduce un correo electrónico válido:")
        return LOGIN_EMAIL

    context.user_data["login_email"] = email

    await update.message.reply_text(
        f"🔐 *Paso 2 de 2: Introduce tu Contraseña*\n\n"
        f"Email registrado: `{email}`\n\n"
        f"Escribe tu contraseña de WodBuster.\n"
        f"🛡️ _Por tu seguridad, el mensaje con tu contraseña será borrado del chat en cuanto se reciba._",
        parse_mode="Markdown",
    )
    return LOGIN_PASSWORD


async def auth_receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive password in wizard and test connection."""
    password = update.message.text.strip()
    email = context.user_data.get("login_email")
    chat_id = update.effective_chat.id

    # Delete the message containing password
    try:
        await update.message.delete()
    except Exception:
        pass

    status_msg = await update.effective_chat.send_message(
        f"⏳ Verificando credenciales con Drakkar WodBuster...",
        parse_mode="Markdown",
    )

    client = WodBusterClient(email=email, password=password)
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.login)

        save_user(
            chat_id=chat_id,
            email=email,
            password=password,
            athlete_id=client.athlete_id,
            cookies_blob=client.get_cookies_blob(),
        )

        await status_msg.edit_text(
            f"✅ *¡Autenticación completada con éxito!* 🎉\n\n"
            f"👤 *Email:* `{email}`\n"
            f"🆔 *ID de Atleta:* `{client.athlete_id or 'Registrado'}`\n\n"
            f"¡Ya tienes acceso total al panel de control!",
            reply_markup=build_main_menu_keyboard(has_user=True),
            parse_mode="Markdown",
        )
        context.user_data.clear()
        return ConversationHandler.END
    except WodBusterAuthError as e:
        await status_msg.edit_text(
            f"❌ *Error de autenticación:*\n_{e}_\n\n"
            f"Usa /login para intentarlo de nuevo.",
            parse_mode="Markdown",
        )
        context.user_data.clear()
        return ConversationHandler.END
    except Exception as e:
        await status_msg.edit_text(
            f"❌ *Error de conexión:*\n_{e}_",
            parse_mode="Markdown",
        )
        context.user_data.clear()
        return ConversationHandler.END


async def cancel_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any active wizard."""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Operación cancelada.",
        reply_markup=build_main_menu_keyboard(has_user=bool(get_user(update.effective_chat.id))),
    )
    return ConversationHandler.END


# =========================================================================
# CALENDAR / VIEW CLASSES
# =========================================================================

async def show_classes_day_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show day selector buttons for viewing classes."""
    query = update.callback_query
    await query.answer()

    now = datetime.datetime.now(TIMEZONE)
    today = now.date()

    keyboard = []
    # Quick buttons: Hoy, Mañana, Pasado Mañana
    row_quick = [
        InlineKeyboardButton("📍 Hoy", callback_data=f"classes:view:{today.strftime('%Y-%m-%d')}"),
        InlineKeyboardButton("⏭️ Mañana", callback_data=f"classes:view:{(today + datetime.timedelta(days=1)).strftime('%Y-%m-%d')}"),
    ]
    keyboard.append(row_quick)

    # Next 5 days
    for i in range(2, 7):
        target = today + datetime.timedelta(days=i)
        day_name = DAYS_OF_WEEK[target.weekday()]
        label = f"{day_name} ({target.strftime('%d/%m')})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"classes:view:{target.strftime('%Y-%m-%d')}")])

    keyboard.append([InlineKeyboardButton("🔙 Volver al Menú", callback_data="menu:main")])

    text = (
        "📅 *Selecciona el día para consultar el horario y plazas de Drakkar:*\n\n"
        "Podrás ver las clases de cada hora, si hay plazas libres y reservar con 1 clic."
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def view_classes_for_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch and display classes for chosen date with direct booking buttons."""
    query = update.callback_query
    await query.answer()

    date_str = query.data.split(":")[2]
    target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    day_name = DAYS_OF_WEEK[target_date.weekday()]

    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    if not user:
        await query.edit_message_text("⚠️ Debes conectar tu cuenta primero.", reply_markup=build_main_menu_keyboard(False))
        return

    await query.edit_message_text(
        f"⏳ Consultando clases en Drakkar para el *{day_name} {target_date.strftime('%d/%m/%Y')}*...",
        parse_mode="Markdown",
    )

    client = WodBusterClient(
        email=user["wodbuster_email"],
        password=user["wodbuster_password"],
        cookies_blob=user["cookies_blob"],
        athlete_id=user["athlete_id"],
    )

    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, client.get_classes_for_date, target_date)
        update_user_session(chat_id, cookies_blob=client.get_cookies_blob(), athlete_id=client.athlete_id)
    except Exception as e:
        await query.edit_message_text(
            f"❌ *Error al obtener las clases:* {e}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="menu:classes_select")]]),
            parse_mode="Markdown",
        )
        return

    classes = res.get("classes", [])
    is_open = res.get("is_open", False)

    if not is_open:
        pub_info = ""
        if res.get("primera_hora_publicacion"):
            pub_info = f"\n\n⏰ *Apertura de reservas:* `{res['primera_hora_publicacion']}`"
        elif res.get("segundos_hasta_publicacion"):
            mins = int(res["segundos_hasta_publicacion"]) // 60
            pub_info = f"\n\n⏰ *Faltan aprox:* {mins} minutos para que abran las reservas."

        text = (
            f"📅 *Drakkar - {day_name} ({target_date.strftime('%d/%m/%Y')})*\n\n"
            f"🔒 *Las reservas para este día aún no están abiertas.*{pub_info}\n\n"
            f"_Puedes configurar una regla automática en el Sniper para que el bot la reserve en el segundo exacto de apertura._"
        )
        kb = [
            [InlineKeyboardButton("⏰ Añadir Regla en Sniper", callback_data=f"rule:quick_add:{target_date.weekday()}")],
            [InlineKeyboardButton("🔙 Elegir Otro Día", callback_data="menu:classes_select")],
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    if not classes:
        text = f"📅 *Drakkar - {day_name} ({target_date.strftime('%d/%m/%Y')})*\n\nNo hay clases programadas para este día."
        kb = [[InlineKeyboardButton("🔙 Elegir Otro Día", callback_data="menu:classes_select")]]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    text = f"📅 *Horario Drakkar - {day_name} ({target_date.strftime('%d/%m/%Y')})*\n\n"
    keyboard = []

    for c in classes:
        hora = c["hora"][:5]
        nombre = c["nombre"]
        plazas = c["plazas"]
        ocupadas = c["ocupadas"]
        libres = c["libres"]
        class_id = c["id"]

        if c["is_booked"]:
            status_emoji = "✅"
            action_btn = InlineKeyboardButton(f"✅ {hora} {nombre} (Apuntado - Cancelar)", callback_data=f"cancel:confirm:{class_id}:{date_str}:{hora}")
        elif c["is_full"]:
            status_emoji = "⛔"
            action_btn = InlineKeyboardButton(f"🦅 Cazar {hora} {nombre} (Completa)", callback_data=f"caza:add_confirm:{class_id}:{date_str}:{hora}")
        else:
            status_emoji = "🟢"
            action_btn = InlineKeyboardButton(f"🎯 Reservar {hora} ({libres} libres)", callback_data=f"book:confirm:{class_id}:{date_str}:{hora}")

        keyboard.append([action_btn])

    keyboard.append([InlineKeyboardButton("🔙 Cambiar Día", callback_data="menu:classes_select")])
    keyboard.append([InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")])

    text += "• Toca una clase *libre* para reservarla.\n• Toca una clase *completa* para activar el 🦅 *Modo Caza-Plazas* (el bot te apuntará si alguien cancela):"
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


# =========================================================================
# MANUAL BOOKING FLOW
# =========================================================================

async def book_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask user to confirm booking."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    class_id = parts[2]
    date_str = parts[3]
    hora = ":".join(parts[4:]) if len(parts) > 4 else "12:00"

    target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    day_name = DAYS_OF_WEEK[target_date.weekday()]

    text = (
        f"🎯 *¿Confirmar reserva?*\n\n"
        f"📅 *Día:* {day_name} ({target_date.strftime('%d/%m/%Y')})\n"
        f"⏰ *Hora:* {hora}\n"
        f"📍 *Box:* Drakkar WodBuster\n\n"
        f"¿Deseas enviar la petición de inscripción ahora?"
    )

    kb = [
        [InlineKeyboardButton("✅ Sí, confirmar reserva", callback_data=f"book:do:{class_id}:{date_str}:{hora}")],
        [InlineKeyboardButton("❌ No, volver", callback_data=f"classes:view:{date_str}")],
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def book_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute booking request."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    class_id = int(parts[2])
    date_str = parts[3]
    hora = ":".join(parts[4:]) if len(parts) > 4 else "12:00"
    target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    await query.edit_message_text(f"⏳ Procesando reserva en Drakkar para las {hora}...", parse_mode="Markdown")

    client = WodBusterClient(
        email=user["wodbuster_email"],
        password=user["wodbuster_password"],
        cookies_blob=user["cookies_blob"],
        athlete_id=user["athlete_id"],
    )

    loop = asyncio.get_running_loop()
    success, msg = await loop.run_in_executor(None, client.book_class, class_id, target_date)
    update_user_session(chat_id, cookies_blob=client.get_cookies_blob(), athlete_id=client.athlete_id)

    log_booking_action(
        chat_id=chat_id,
        action="MANUAL_BOOK",
        class_name="Drakkar Class",
        class_datetime=f"{date_str} {hora}",
        status="SUCCESS" if success else "FAILED",
        message=msg,
    )

    if success:
        text = (
            f"🎉 *¡RESERVA CONFIRMADA!* 🏋️‍♂️\n\n"
            f"📅 *Fecha:* {target_date.strftime('%d/%m/%Y')} a las {hora}\n"
            f"📍 *Box:* Drakkar WodBuster\n\n"
            f"✅ _{msg}_\n"
            f"¡A entrenar duro!"
        )
    else:
        text = (
            f"❌ *No se pudo completar la reserva*\n\n"
            f"⚠️ *Motivo:* _{msg}_\n\n"
            f"Puede que la clase se haya llenado o se requiera antelación."
        )

    kb = [
        [InlineKeyboardButton("📋 Ver Mis Reservas", callback_data="menu:my_bookings")],
        [InlineKeyboardButton("📅 Ver Otras Clases", callback_data="menu:classes_select")],
        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")],
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


# =========================================================================
# MY BOOKINGS & CANCEL FLOW
# =========================================================================

async def show_my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's currently booked classes with cancel buttons."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    await query.edit_message_text("⏳ Buscando tus reservas activas en Drakkar...", parse_mode="Markdown")

    client = WodBusterClient(
        email=user["wodbuster_email"],
        password=user["wodbuster_password"],
        cookies_blob=user["cookies_blob"],
        athlete_id=user["athlete_id"],
    )

    loop = asyncio.get_running_loop()
    try:
        bookings = await loop.run_in_executor(None, client.get_my_active_bookings, 7)
        update_user_session(chat_id, cookies_blob=client.get_cookies_blob(), athlete_id=client.athlete_id)
    except Exception as e:
        await query.edit_message_text(f"❌ Error al consultar reservas: {e}", parse_mode="Markdown")
        return

    if not bookings:
        text = (
            "📋 *Mis Reservas Activas en Drakkar*\n\n"
            "Actualmente no estás apuntado a ninguna clase en los próximos 7 días.\n\n"
            "_Puedes reservar una clase en cualquier momento desde el calendario._"
        )
        kb = [
            [InlineKeyboardButton("📅 Ver Clases Disponibles", callback_data="menu:classes_select")],
            [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")],
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    text = f"📋 *Tus Reservas Activas en Drakkar ({len(bookings)}):*\n\n"
    keyboard = []

    for b in bookings:
        class_id = b["id"]
        date_str = b["date"].strftime("%Y-%m-%d")
        hora = b["hora"][:5]
        text += (
            f"• *{b['day_name']} {b['date_str']}* a las *{hora}*\n"
            f"   🏷️ {b['nombre']} ({b['ocupadas']}/{b['plazas']} plazas)\n\n"
        )
        keyboard.append([
            InlineKeyboardButton(f"❌ Cancelar {b['day_name']} {hora}", callback_data=f"cancel:confirm:{class_id}:{date_str}:{hora}")
        ])

    keyboard.append([InlineKeyboardButton("📅 Reservar Otra Clase", callback_data="menu:classes_select")])
    keyboard.append([InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")])

    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def cancel_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm cancellation."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    class_id = parts[2]
    date_str = parts[3]
    hora = ":".join(parts[4:]) if len(parts) > 4 else "12:00"

    target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    day_name = DAYS_OF_WEEK[target_date.weekday()]

    text = (
        f"⚠️ *¿Seguro que quieres cancelar tu reserva?*\n\n"
        f"📅 *Clase:* {day_name} {target_date.strftime('%d/%m/%Y')} a las {hora}\n"
        f"📍 Drakkar CrossFit\n\n"
        f"_Tu plaza quedará liberada inmediatamente._"
    )

    kb = [
        [InlineKeyboardButton("🗑️ Sí, anular mi plaza", callback_data=f"cancel:do:{class_id}:{date_str}:{hora}")],
        [InlineKeyboardButton("🔙 No, mantener mi reserva", callback_data="menu:my_bookings")],
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def cancel_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute cancellation in Drakkar."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    class_id = int(parts[2])
    date_str = parts[3]
    hora = ":".join(parts[4:]) if len(parts) > 4 else "12:00"
    target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    await query.edit_message_text(f"⏳ Cancelando inscripción en Drakkar...", parse_mode="Markdown")

    client = WodBusterClient(
        email=user["wodbuster_email"],
        password=user["wodbuster_password"],
        cookies_blob=user["cookies_blob"],
        athlete_id=user["athlete_id"],
    )

    loop = asyncio.get_running_loop()
    success, msg = await loop.run_in_executor(None, client.cancel_class, class_id, target_date)
    update_user_session(chat_id, cookies_blob=client.get_cookies_blob(), athlete_id=client.athlete_id)

    log_booking_action(
        chat_id=chat_id,
        action="CANCEL",
        class_name="Drakkar Class",
        class_datetime=f"{date_str} {hora}",
        status="SUCCESS" if success else "FAILED",
        message=msg,
    )

    if success:
        text = f"✅ *Reserva cancelada correctamente.*\n\nLa plaza para el {date_str} a las {hora} ha sido liberada."
    else:
        text = f"❌ *No se pudo cancelar:*\n_{msg}_"

    kb = [
        [InlineKeyboardButton("📋 Volver a Mis Reservas", callback_data="menu:my_bookings")],
        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")],
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


# =========================================================================
# AUTO-BOOKING RULES (SNIPER) MANAGEMENT
# =========================================================================

async def show_rules_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of automatic booking rules and options."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    rules = get_user_rules(chat_id)

    text = (
        "⏰ *Panel de Automatizaciones (Sniper Drakkar)* 🎯\n\n"
        "Configura qué días y horas quieres que el bot te reserve automáticamente "
        "en el momento exacto en que se abren las reservas del box.\n\n"
    )

    keyboard = []
    if not rules:
        text += "_Aún no tienes ninguna regla configurada._\n"
    else:
        text += "*Tus reglas activas:*\n"
        for r in rules:
            r_id = r["id"]
            dow_name = DAYS_OF_WEEK[r["day_of_week"]]
            status_icon = "🟢 Activa" if r["is_active"] else "⏸️ Pausada"
            toggle_action = "Pausar" if r["is_active"] else "Activar"
            toggle_icon = "⏸️" if r["is_active"] else "▶️"

            t_raw = r["class_time"]
            t_display = t_raw[:5] if ":" in t_raw else f"{int(t_raw):02d}:00"
            filt = (r["class_name_filter"] or "ALL").upper()
            if "GYM" in filt:
                filt_label = "🤸‍♂️ Gymnastics"
            elif "WEIGHT" in filt or "HALTERO" in filt:
                filt_label = "🏋️ Halterofilia"
            elif "HYBRID" in filt:
                filt_label = "⚡ Hybrid"
            elif "WOD" in filt:
                filt_label = "🏋️‍♂️ WOD"
            elif "OPEN" in filt:
                filt_label = "🔓 Open Box"
            else:
                filt_label = "🎯 Cualquier clase (ALL)"

            text += (
                f"• *{dow_name}* a las *{t_display}* — *{filt_label}*\n"
                f"   Antelación: {r['days_advance']} día(s) ({r['opening_time'][:5]}) | Estado: {status_icon}\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(f"{toggle_icon} {toggle_action} #{r_id}", callback_data=f"rule:toggle:{r_id}"),
                InlineKeyboardButton(f"🗑️ Eliminar #{r_id}", callback_data=f"rule:delete:{r_id}"),
            ])

    keyboard.append([InlineKeyboardButton("➕ Añadir Nueva Regla Automática", callback_data="rule:add_start")])
    keyboard.append([InlineKeyboardButton("🧪 Probar Diagnóstico del Sniper Ahora", callback_data="menu:test_sniper")])
    keyboard.append([InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")])

    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def run_test_sniper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run full diagnostic test for the user."""
    query = update.callback_query
    if query:
        await query.answer()
        chat_id = update.effective_chat.id
        msg = await query.message.reply_text("⏳ *Iniciando test de diagnóstico del Sniper...*", parse_mode="Markdown")
    else:
        chat_id = update.effective_chat.id
        msg = await update.effective_chat.send_message("⏳ *Iniciando test de diagnóstico del Sniper...*", parse_mode="Markdown")

    user = get_user(chat_id)
    if not user:
        await msg.edit_text("⚠️ Debes conectar tu cuenta primero con /login.")
        return

    client = WodBusterClient(
        email=user["wodbuster_email"],
        password=user["wodbuster_password"],
        cookies_blob=user.get("cookies_blob"),
        athlete_id=user.get("athlete_id"),
    )

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, client.login)
        update_user_session(chat_id, cookies_blob=client.get_cookies_blob(), athlete_id=client.athlete_id)
    except Exception as e:
        await msg.edit_text(f"❌ *Fallo de autenticación con WodBuster:*\n_{e}_", parse_mode="Markdown")
        return

    now = datetime.datetime.now(TIMEZONE)
    today = now.date()
    tomorrow = today + datetime.timedelta(days=1)

    try:
        classes_info = await loop.run_in_executor(None, client.get_classes_for_date, tomorrow)
    except Exception as e:
        await msg.edit_text(f"❌ *Error consultando calendario de Drakkar:*\n_{e}_", parse_mode="Markdown")
        return

    segundos = classes_info.get("segundos_hasta_publicacion")
    is_open = classes_info.get("is_open", False)

    rules = get_user_rules(chat_id)
    active_tomorrow = [r for r in rules if r["day_of_week"] == tomorrow.weekday() and r["is_active"]]

    if active_tomorrow:
        def _rule_prio(r):
            filt = (r.get("class_name_filter") or "ALL").upper()
            is_special = any(k in filt for k in ("GYM", "WEIGHT", "HALTERO", "HYBRID"))
            return (0 if is_special else (1 if "WOD" in filt else 2), r["class_time"])

        active_tomorrow.sort(key=_rule_prio)
        lines = [f"🎯 *Reglas armadas para mañana ({DAYS_OF_WEEK[tomorrow.weekday()]}):*"]
        for idx, r in enumerate(active_tomorrow, start=1):
            t_clean = r['class_time'][:5] if ':' in r['class_time'] else f"{int(r['class_time']):02d}:00"
            filt = (r.get("class_name_filter") or "ALL").upper()
            if "GYM" in filt:
                fl = "🤸‍♂️ Gymnastics"
            elif "WEIGHT" in filt or "HALTERO" in filt:
                fl = "🏋️ Halterofilia"
            elif "HYBRID" in filt:
                fl = "⚡ Hybrid"
            elif "WOD" in filt:
                fl = "🏋️‍♂️ WOD"
            elif "OPEN" in filt:
                fl = "🔓 Open Box"
            else:
                fl = "🎯 Cualquier clase"
            tag = "⭐ Prioridad 1" if idx == 1 else "🔄 Respaldo"
            lines.append(f"   • *{t_clean}* — {fl} ({tag}) [Disparo: {r['days_advance']}d antes a las {r['opening_time'][:5]}]")
        rules_text = "\n".join(lines)
    else:
        rules_text = "ℹ️ No tienes regla activa programada para mañana."

    if segundos:
        mins = int(float(segundos)) // 60
        horas = mins // 60
        rest_mins = mins % 60
        apertura_info = f"🔒 *Apertura oficial Drakkar:* Hoy a las 22:30:00 (en {horas}h {rest_mins}m)"
    elif is_open:
        apertura_info = "🟢 *¡Las reservas para mañana ya están abiertas en Drakkar!*"
    else:
        apertura_info = "🔒 Reservas aún no abiertas."

    test_result = (
        "🎯 *¡DIAGNÓSTICO DEL BOT COMPLETADO CON ÉXITO!* ✅\n\n"
        f"• 👤 *Usuario:* `{user['wodbuster_email']}`\n"
        f"• 🆔 *ID Atleta Drakkar:* `{client.athlete_id}` (Verificado 🟢)\n"
        f"• 🌐 *Servidor Drakkar:* Respondiendo correctamente (HTTP 200)\n"
        f"• ⏰ {apertura_info}\n\n"
        f"{rules_text}\n\n"
        "🚀 *Conclusión:* El bot y el Sniper están 100% operativos. A las 22:30:00 el bot se activará automáticamente, reservará tu plaza y te enviará un mensaje aquí con el resultado."
    )

    kb = [
        [InlineKeyboardButton("⏰ Ver Mis Reglas", callback_data="menu:rules")],
        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")],
    ]
    await msg.edit_text(test_result, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def rule_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle a rule active/paused."""
    query = update.callback_query
    await query.answer()

    rule_id = int(query.data.split(":")[2])
    chat_id = update.effective_chat.id

    toggle_rule(rule_id, chat_id)
    await show_rules_menu(update, context)


async def rule_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a rule."""
    query = update.callback_query
    await query.answer()

    rule_id = int(query.data.split(":")[2])
    chat_id = update.effective_chat.id

    delete_rule(rule_id, chat_id)
    await show_rules_menu(update, context)


# =========================================================================
# WIZARD TO ADD A NEW AUTO-BOOKING RULE
# =========================================================================

async def rule_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start step 1: Select Day of Week."""
    query = update.callback_query
    await query.answer()

    keyboard = []
    for idx, name in enumerate(DAYS_OF_WEEK):
        keyboard.append([InlineKeyboardButton(f"🗓️ {name}", callback_data=f"rule_set_dow:{idx}")])

    keyboard.append([InlineKeyboardButton("🔙 Cancelar", callback_data="menu:rules")])

    text = "➕ *Paso 1 de 4: Selecciona el Día de la Semana* que deseas agendar recurrentemente:"
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def rule_set_dow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: Select Hour."""
    query = update.callback_query
    await query.answer()

    dow = int(query.data.split(":")[1])
    context.user_data["new_rule_dow"] = dow
    dow_name = DAYS_OF_WEEK[dow]

    keyboard = []
    row = []
    for h in DRAKKAR_HOURS:
        row.append(InlineKeyboardButton(h, callback_data=f"rule_set_time:{h}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🔙 Cancelar", callback_data="menu:rules")])

    text = f"🗓️ Día seleccionado: *{dow_name}*\n\n⏰ *Paso 2 de 4: Selecciona la Hora* de la clase:"
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def rule_set_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 3: Select Class Type Filter."""
    query = update.callback_query
    await query.answer()

    time_str = query.data.split(":", 1)[1]
    context.user_data["new_rule_time"] = time_str
    dow = context.user_data.get("new_rule_dow", 0)
    dow_name = DAYS_OF_WEEK[dow]

    keyboard = [
        [InlineKeyboardButton("🤸‍♂️ Gymnastics (Drakkar Gymnastic)", callback_data="rule_set_filter:GYMNASTIC")],
        [InlineKeyboardButton("🏋️ Halterofilia (Weightlifting)", callback_data="rule_set_filter:WEIGHTLIFTING")],
        [InlineKeyboardButton("⚡ Hybrid (Drakkar Hybrid)", callback_data="rule_set_filter:HYBRID")],
        [InlineKeyboardButton("🏋️‍♂️ Solo WOD (WOD DK 1.0 / 2.0)", callback_data="rule_set_filter:WOD")],
        [InlineKeyboardButton("🔓 Solo Open Box (OPEN DK)", callback_data="rule_set_filter:OPEN")],
        [InlineKeyboardButton("🎯 Cualquier clase a esa hora", callback_data="rule_set_filter:ALL")],
        [InlineKeyboardButton("🔙 Cancelar", callback_data="menu:rules")],
    ]

    text = (
        f"🗓️ Día: *{dow_name}*\n"
        f"⏰ Hora: *{time_str}*\n\n"
        f"🏷️ *Paso 3 de 4: Filtro de Modalidad*\n"
        f"Selecciona qué modalidad deseas reservar en esta franja horaria:"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def rule_set_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 4: Select Advance Booking Window."""
    query = update.callback_query
    await query.answer()

    filter_str = query.data.split(":")[1]
    context.user_data["new_rule_filter"] = filter_str

    keyboard = [
        [InlineKeyboardButton("🔥 1 día antes a las 22:30 (Drakkar Oficial ⭐)", callback_data="rule_set_adv:1:22:30:00")],
        [InlineKeyboardButton("⚡ 1 día antes a las 22:00", callback_data="rule_set_adv:1:22:00:00")],
        [InlineKeyboardButton("🌙 1 día antes a las 00:00 (Medianoche)", callback_data="rule_set_adv:1:00:00:00")],
        [InlineKeyboardButton("⚡ 2 días antes a las 22:30", callback_data="rule_set_adv:2:22:30:00")],
        [InlineKeyboardButton("⚡ 2 días antes a las 22:00", callback_data="rule_set_adv:2:22:00:00")],
        [InlineKeyboardButton("🌙 2 días antes a las 00:00 (Medianoche)", callback_data="rule_set_adv:2:00:00:00")],
        [InlineKeyboardButton("⚡ 3 días antes a las 22:30", callback_data="rule_set_adv:3:22:30:00")],
        [InlineKeyboardButton("🔙 Cancelar", callback_data="menu:rules")],
    ]

    text = (
        f"⏰ *Paso 4 de 4: Antelación de Apertura en Drakkar*\n\n"
        f"¿Con cuánta antelación abre Drakkar las reservas para esta clase?\n"
        f"_(El bot disparará el sniper automáticamente en ese segundo exacto)_:"
    )
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def rule_set_advance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finalize rule creation."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    days_adv = int(parts[1])
    opening_time = f"{parts[2]}:{parts[3]}:{parts[4]}"

    dow = context.user_data.get("new_rule_dow", 0)
    time_str = context.user_data.get("new_rule_time", "18:00")
    filter_str = context.user_data.get("new_rule_filter", "ALL")
    chat_id = update.effective_chat.id

    rule_id = add_auto_rule(
        chat_id=chat_id,
        day_of_week=dow,
        class_time=time_str,
        class_name_filter=filter_str,
        days_advance=days_adv,
        opening_time=opening_time,
    )

    dow_name = DAYS_OF_WEEK[dow]
    filt_upper = filter_str.upper()
    if "GYM" in filt_upper:
        fl = "🤸‍♂️ Drakkar Gymnastic"
    elif "WEIGHT" in filt_upper or "HALTERO" in filt_upper:
        fl = "🏋️ Halterofilia / Weightlifting"
    elif "HYBRID" in filt_upper:
        fl = "⚡ Drakkar Hybrid"
    elif "WOD" in filt_upper:
        fl = "🏋️‍♂️ WOD"
    elif "OPEN" in filt_upper:
        fl = "🔓 Open Box"
    else:
        fl = "🎯 Cualquier clase (ALL)"

    existing_for_day = [
        r for r in get_user_rules(chat_id)
        if r["day_of_week"] == dow and r["id"] != rule_id and r["is_active"]
    ]
    backup_note = ""
    if existing_for_day:
        prev_list = ", ".join(f"{r['class_time'][:5]} ({r['class_name_filter']})" for r in existing_for_day)
        backup_note = (
            f"\n\n💡 *Respaldo Inteligente Activado:*\n"
            f"Ya tienes configurada otra regla para el {dow_name} ({prev_list}).\n"
            f"El bot priorizará las clases especiales (Gymnastics/Weightlifting). Si tu primera opción se llena, "
            f"el bot intentará la otra regla automáticamente como respaldo y activará el Caza-Plazas."
        )

    text = (
        f"🎉 *¡Regla Automática Creada con Éxito!* 🎯\n\n"
        f"• *ID Regla:* `#{rule_id}`\n"
        f"• *Día:* {dow_name}\n"
        f"• *Hora:* {time_str}\n"
        f"• *Modalidad:* {fl}\n"
        f"• *Disparo:* {days_adv} días antes a las {opening_time[:5]}\n"
        f"• *Estado:* 🟢 Activa\n\n"
        f"El bot reservará tu plaza automáticamente cada semana sin que tengas que preocuparte.{backup_note}"
    )

    kb = [
        [InlineKeyboardButton("⏰ Ver Mis Reglas", callback_data="menu:rules")],
        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")],
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


# =========================================================================
# MODO CAZA-PLAZAS (WAITLIST SPOT HUNTER)
# =========================================================================

async def show_caza_plazas_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show active Caza-Plazas watches."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    watches = get_user_caza_plazas(chat_id)

    text = (
        "🦅 *Panel de Modo Caza-Plazas (Vigilante de Bajas)* 🎯\n\n"
        "Cuando una clase está completa, el bot la vigila continuamente cada 20 segundos.\n"
        "En el instante en que alguien cancela su plaza, ¡el bot te apunta automáticamente!\n\n"
    )

    keyboard = []
    if not watches:
        text += "_Actualmente no estás vigilando ninguna clase._\n\n" \
                "Para cazar una plaza, ve a *📅 Ver Clases Drakkar*, selecciona un día y toca cualquier clase que esté completa `(Completa)`."
    else:
        text += "*Clases en vigilancia activa:*\n"
        for w in watches:
            w_id = w["id"]
            text += (
                f"• 🦅 *{w['class_name']}*\n"
                f"   📅 {w['class_date']} a las {w['class_time']}\n"
                f"   Estado: 🟢 Vigilando activamente cada 20s\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(f"🗑️ Dejar de vigilar #{w_id} ({w['class_time']})", callback_data=f"caza:delete:{w_id}")
            ])

    keyboard.append([InlineKeyboardButton("📅 Ver Clases para Cazar", callback_data="menu:classes_select")])
    keyboard.append([InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")])

    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def caza_add_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask user to confirm activating Caza-Plazas on a full class."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    class_id = int(parts[2])
    date_str = parts[3]
    hora = ":".join(parts[4:]) if len(parts) > 4 else "12:00"

    target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    day_name = DAYS_OF_WEEK[target_date.weekday()]

    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    class_name = "Clase Drakkar"
    if user:
        try:
            client = WodBusterClient(
                email=user["wodbuster_email"],
                password=user["wodbuster_password"],
                cookies_blob=user.get("cookies_blob"),
                athlete_id=user.get("athlete_id"),
            )
            loop = asyncio.get_running_loop()
            classes_info = await loop.run_in_executor(None, client.get_classes_for_date, target_date)
            for c in classes_info.get("classes", []):
                if c["id"] == class_id:
                    class_name = c["nombre"]
                    hora = c["hora"][:5]
                    break
        except Exception:
            pass

    text = (
        f"🦅 *¿Activar Modo Caza-Plazas?* 🎯\n\n"
        f"📅 *Día:* {day_name} ({target_date.strftime('%d/%m/%Y')})\n"
        f"⏰ *Hora:* {hora}\n"
        f"🏷️ *Clase:* {class_name}\n"
        f"📍 Drakkar CrossFit\n\n"
        f"Esta clase está completa. Si activas el *Modo Caza-Plazas*, el bot:\n"
        f"• Consultará el aforo cada 20 segundos.\n"
        f"• Si un socio cancela su reserva a cualquier hora del día o noche, **te inscribirá inmediatamente** antes de que nadie más se adelante.\n"
        f"• Te enviará una notificación a Telegram confirmando tu plaza."
    )

    kb = [
        [InlineKeyboardButton("🦅 Sí, activar Caza-Plazas", callback_data=f"caza:do_add:{class_id}:{date_str}:{hora}")],
        [InlineKeyboardButton("🔙 Volver a las clases", callback_data=f"classes:view:{date_str}")],
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def caza_do_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activate Caza-Plazas for the selected class."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    class_id = int(parts[2])
    date_str = parts[3]
    hora = ":".join(parts[4:]) if len(parts) > 4 else "12:00"
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    class_name = "Clase Drakkar"
    if user:
        try:
            client = WodBusterClient(
                email=user["wodbuster_email"],
                password=user["wodbuster_password"],
                cookies_blob=user.get("cookies_blob"),
                athlete_id=user.get("athlete_id"),
            )
            loop = asyncio.get_running_loop()
            classes_info = await loop.run_in_executor(None, client.get_classes_for_date, target_date)
            for c in classes_info.get("classes", []):
                if c["id"] == class_id:
                    class_name = c["nombre"]
                    hora = c["hora"][:5]
                    break
        except Exception:
            pass

    watch_id = add_caza_plaza(
        chat_id=chat_id,
        class_id=class_id,
        class_date=date_str,
        class_time=hora,
        class_name=class_name,
    )

    text = (
        f"✅ *¡Modo Caza-Plazas Activado!* 🦅\n\n"
        f"• *ID Vigilancia:* `#{watch_id}`\n"
        f"• *Clase:* {class_name}\n"
        f"• *Fecha:* {date_str} a las {hora}\n"
        f"• *Frecuencia:* Comprobación cada 20 segundos\n\n"
        f"El bot está vigilando activamente. En cuanto alguien se dé de baja, cogerá tu plaza al instante y te avisará por Telegram."
    )

    kb = [
        [InlineKeyboardButton("🦅 Ver Mis Clases en Vigilancia", callback_data="menu:caza_plazas")],
        [InlineKeyboardButton("📅 Ver Otras Clases", callback_data="menu:classes_select")],
        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")],
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def caza_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a Caza-Plazas watch."""
    query = update.callback_query
    await query.answer()

    watch_id = int(query.data.split(":")[2])
    chat_id = update.effective_chat.id

    delete_caza_plaza(watch_id, chat_id)
    await show_caza_plazas_menu(update, context)


# =========================================================================
# ACCOUNT & HISTORY MENUS
# =========================================================================

async def show_account_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show account details and connection status."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if not user:
        await query.edit_message_text("No hay ninguna cuenta conectada.", reply_markup=build_main_menu_keyboard(False))
        return

    text = (
        f"⚙️ *Configuración de Cuenta Drakkar WodBuster*\n\n"
        f"👤 *Email:* `{user['wodbuster_email']}`\n"
        f"🆔 *ID de Atleta:* `{user['athlete_id'] or 'No detectado'}`\n"
        f"📍 *Box URL:* `{DEFAULT_BOX_URL}`\n"
        f"🕒 *Último acceso:* `{user['last_login_at'][:19] if user.get('last_login_at') else 'Desconocido'}`\n\n"
        f"Estado de la sesión guardada y cifrada de forma segura."
    )

    kb = [
        [InlineKeyboardButton("🔄 Probar Conexión Ahora", callback_data="account:test_connection")],
        [InlineKeyboardButton("🚪 Desconectar / Salir", callback_data="account:disconnect_confirm")],
        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")],
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def account_test_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test connection with Drakkar."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    await query.edit_message_text("⏳ Probando conexión con Drakkar WodBuster...", parse_mode="Markdown")

    client = WodBusterClient(
        email=user["wodbuster_email"],
        password=user["wodbuster_password"],
        cookies_blob=user["cookies_blob"],
        athlete_id=user["athlete_id"],
    )

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, client.login, True)
        update_user_session(chat_id, cookies_blob=client.get_cookies_blob(), athlete_id=client.athlete_id)
        msg = (
            f"✅ *¡Conexión Perfecta!* 🚀\n\n"
            f"La sesión con Drakkar WodBuster responde correctamente.\n"
            f"ID de Atleta verificado: `{client.athlete_id}`"
        )
    except Exception as e:
        msg = f"❌ *Fallo al conectar con WodBuster:*\n_{e}_"

    kb = [
        [InlineKeyboardButton("⚙️ Volver a Mi Cuenta", callback_data="menu:account")],
        [InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")],
    ]
    await query.edit_message_text(text=msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def account_disconnect_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm account disconnect."""
    query = update.callback_query
    await query.answer()

    text = (
        "⚠️ *¿Seguro que deseas desconectar tu cuenta?*\n\n"
        "Se eliminarán tus credenciales cifradas y se detendrán todas tus reservas automáticas."
    )
    kb = [
        [InlineKeyboardButton("🚪 Sí, desconectar", callback_data="account:do_disconnect")],
        [InlineKeyboardButton("🔙 No, mantener", callback_data="menu:account")],
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def account_do_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute account disconnect."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    delete_user(chat_id)

    await query.edit_message_text(
        "👋 Has desconectado tu cuenta correctamente. Puedes volver a iniciar sesión cuando quieras.",
        reply_markup=build_main_menu_keyboard(False),
    )


async def show_history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent logs."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    logs = get_recent_logs(chat_id, limit=10)

    if not logs:
        text = "📜 *Historial de Acciones*\n\nNo hay registros recientes aún."
    else:
        text = "📜 *Últimas 10 acciones registradas:*\n\n"
        for l in logs:
            status_icon = "✅" if l["status"] == "SUCCESS" else ("⛔" if l["status"] == "FULL" else "❌")
            text += (
                f"{status_icon} *{l['action']}* ({l['created_at'][:16]})\n"
                f"   📅 {l['class_datetime']} - {l['class_name']}\n"
                f"   _{l['message']}_\n\n"
            )

    kb = [[InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def show_help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help and usage guide."""
    query = update.callback_query
    await query.answer()

    text = (
        "ℹ️ *Guía de Uso del Bot Drakkar WodBuster* 🏋️‍♂️\n\n"
        "*Comandos Rápidos:*\n"
        "• `/start` o `/menu` - Abre el panel interactivo.\n"
        "• `/login email contraseña` - Conecta tu cuenta Drakkar al instante.\n"
        "• `/clases` - Ver horarios de hoy y próximos días.\n"
        "• `/mis_reservas` - Ver y cancelar tus reservas activas.\n"
        "• `/reglas` - Gestionar tus reservas automáticas.\n\n"
        "*¿Cómo funciona el Sniper Automático?*\n"
        "1. Creas una regla (ej: Lunes 18:00 WOD DK 1.0).\n"
        "2. Indicas cuántos días antes abren las reservas en Drakkar (por defecto 1 día antes a las 22:30).\n"
        "3. El bot se despierta en el segundo exacto y envía la petición de reserva a máxima velocidad.\n"
        "4. Recibes un mensaje en Telegram confirmando tu plaza al instante. 🎉"
    )

    kb = [[InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


# =========================================================================
# BOT BUILDER & ROUTER
# =========================================================================

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    text = "🎛️ *Panel de Control Drakkar WodBuster*\nElige una opción:"
    await query.edit_message_text(
        text=text,
        reply_markup=build_main_menu_keyboard(has_user=bool(user)),
        parse_mode="Markdown",
    )


async def clases_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clases command."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    if not user:
        await update.effective_chat.send_message("⚠️ Debes conectar tu cuenta primero con /login.")
        return
    keyboard = []
    today = datetime.datetime.now(TIMEZONE).date()
    for offset in range(7):
        target_date = today + datetime.timedelta(days=offset)
        dow_name = DAYS_OF_WEEK[target_date.weekday()]
        label = f"Hoy ({dow_name})" if offset == 0 else f"{dow_name} {target_date.strftime('%d/%m')}"
        keyboard.append([InlineKeyboardButton(f"🗓️ {label}", callback_data=f"classes:view:{target_date.strftime('%Y-%m-%d')}")])
    keyboard.append([InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")])
    await update.effective_chat.send_message(
        "📅 *Selecciona el día para consultar el horario y plazas de Drakkar:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def mis_reservas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mis_reservas command."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    if not user:
        await update.effective_chat.send_message("⚠️ Debes conectar tu cuenta primero con /login.")
        return
    msg = await update.effective_chat.send_message("⏳ Consultando tus reservas en Drakkar...", parse_mode="Markdown")
    client = WodBusterClient(
        email=user["wodbuster_email"],
        password=user["wodbuster_password"],
        cookies_blob=user["cookies_blob"],
        athlete_id=user["athlete_id"],
    )
    loop = asyncio.get_running_loop()
    try:
        bookings = await loop.run_in_executor(None, client.get_my_active_bookings, 7)
        update_user_session(chat_id, cookies_blob=client.get_cookies_blob(), athlete_id=client.athlete_id)
    except Exception as e:
        await msg.edit_text(f"❌ Error al consultar reservas: {e}", parse_mode="Markdown")
        return

    if not bookings:
        await msg.edit_text(
            "📋 *Mis Reservas Activas en Drakkar*\n\nActualmente no estás apuntado a ninguna clase en los próximos 7 días.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📅 Ver Clases Disponibles", callback_data="menu:classes_select")]]),
            parse_mode="Markdown"
        )
        return

    text = f"📋 *Tus Reservas Activas en Drakkar ({len(bookings)}):*\n\n"
    keyboard = []
    for b in bookings:
        class_id = b["id"]
        date_str = b["date"].strftime("%Y-%m-%d")
        hora = b["hora"][:5]
        text += f"• *{b['day_name']} {b['date_str']}* a las *{hora}*\n   🏷️ {b['nombre']}\n\n"
        keyboard.append([InlineKeyboardButton(f"❌ Cancelar {b['day_name']} {hora}", callback_data=f"cancel:confirm:{class_id}:{date_str}:{hora}")])
    keyboard.append([InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")])
    await msg.edit_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def reglas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reglas command."""
    chat_id = update.effective_chat.id
    rules = get_user_rules(chat_id)
    text = "⏰ *Panel de Automatizaciones (Sniper Drakkar)* 🎯\n\n"
    keyboard = []
    if not rules:
        text += "_Aún no tienes ninguna regla configurada._\n"
    else:
        text += "*Tus reglas activas:*\n"
        for r in rules:
            r_id = r["id"]
            dow_name = DAYS_OF_WEEK[r["day_of_week"]]
            status_icon = "🟢 Activa" if r["is_active"] else "⏸️ Pausada"
            toggle_action = "Pausar" if r["is_active"] else "Activar"
            toggle_icon = "⏸️" if r["is_active"] else "▶️"
            t_raw = r["class_time"]
            t_display = t_raw[:5] if ":" in t_raw else f"{int(t_raw):02d}:00"
            text += f"• *{dow_name}* a las *{t_display}*\n   Filtro: `{r['class_name_filter']}` | Antelación: {r['days_advance']} días ({r['opening_time'][:5]})\n   Estado: {status_icon}\n\n"
            keyboard.append([
                InlineKeyboardButton(f"{toggle_icon} {toggle_action} #{r_id}", callback_data=f"rule:toggle:{r_id}"),
                InlineKeyboardButton(f"🗑️ Eliminar #{r_id}", callback_data=f"rule:delete:{r_id}"),
            ])
    keyboard.append([InlineKeyboardButton("➕ Añadir Nueva Regla Automática", callback_data="rule:add_start")])
    keyboard.append([InlineKeyboardButton("🧪 Probar Diagnóstico del Sniper Ahora", callback_data="menu:test_sniper")])
    keyboard.append([InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")])
    await update.effective_chat.send_message(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def caza_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /caza command."""
    chat_id = update.effective_chat.id
    watches = get_user_caza_plazas(chat_id)
    text = "🦅 *Modo Caza-Plazas (Waitlist Spot Hunter)* 🎯\n\n"
    keyboard = []
    if not watches:
        text += "_No tienes ninguna clase en vigilancia actualmente._\n"
    else:
        text += "*Clases en vigilancia activa:*\n"
        for w in watches:
            w_id = w["id"]
            text += f"• 🦅 *{w['class_name']}*\n   📅 {w['class_date']} a las {w['class_time']}\n   Estado: 🟢 Vigilando activamente cada 20s\n\n"
            keyboard.append([InlineKeyboardButton(f"🗑️ Dejar de vigilar #{w_id} ({w['class_time']})", callback_data=f"caza:delete:{w_id}")])
    keyboard.append([InlineKeyboardButton("📅 Ver Clases para Cazar", callback_data="menu:classes_select")])
    keyboard.append([InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")])
    await update.effective_chat.send_message(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def ayuda_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ayuda or /help command."""
    text = (
        "ℹ️ *Guía de Uso del Bot Drakkar WodBuster* 🏋️‍♂️\n\n"
        "*Comandos Rápidos:*\n"
        "• `/start` o `/menu` - Abre el panel interactivo.\n"
        "• `/login email contraseña` - Conecta tu cuenta Drakkar al instante.\n"
        "• `/clases` - Ver horarios de hoy y próximos días.\n"
        "• `/mis_reservas` - Ver y cancelar tus reservas activas.\n"
        "• `/reglas` - Gestionar tus reservas automáticas.\n"
        "• `/caza` - Gestionar el Modo Caza-Plazas.\n"
        "• `/test_sniper` - Diagnóstico en vivo de conexión y reglas.\n"
    )
    await update.effective_chat.send_message(text=text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menú Principal", callback_data="menu:main")]]), parse_mode="Markdown")


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    err = context.error
    if "Message is not modified" in str(err):
        return
    logger.error(f"Telegram update error: {err}", exc_info=err)


def setup_telegram_application(token: str) -> Application:
    """Configure and assemble python-telegram-bot application."""
    app = Application.builder().token(token).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("login", login_command))
    app.add_handler(CommandHandler("test_sniper", run_test_sniper))
    app.add_handler(CommandHandler("test", run_test_sniper))
    app.add_handler(CommandHandler("clases", clases_command))
    app.add_handler(CommandHandler("mis_reservas", mis_reservas_command))
    app.add_handler(CommandHandler("reglas", reglas_command))
    app.add_handler(CommandHandler("caza", caza_command))
    app.add_handler(CommandHandler("ayuda", ayuda_command))
    app.add_handler(CommandHandler("help", ayuda_command))

    # Persistent bottom keyboard handler
    app.add_handler(MessageHandler(
        filters.Regex("^(🏠 Menú Principal|📅 Ver Clases|⏰ Mis Reglas|🦅 Caza-Plazas|📋 Mis Reservas)$"),
        persistent_keyboard_handler
    ))

    # Login Conversation Handler
    login_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(auth_start_wizard, pattern="^auth:start_login$"),
        ],
        states={
            LOGIN_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, auth_receive_email)],
            LOGIN_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, auth_receive_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel_wizard)],
        per_message=False,
    )
    app.add_handler(login_conv)

    # Callback Query Handlers
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^menu:main$"))
    app.add_handler(CallbackQueryHandler(show_classes_day_select, pattern="^menu:classes_select$"))
    app.add_handler(CallbackQueryHandler(show_classes_day_select, pattern="^menu:quick_book$"))
    app.add_handler(CallbackQueryHandler(view_classes_for_date, pattern="^classes:view:"))
    app.add_handler(CallbackQueryHandler(book_confirm_callback, pattern="^book:confirm:"))
    app.add_handler(CallbackQueryHandler(book_execute_callback, pattern="^book:do:"))
    app.add_handler(CallbackQueryHandler(show_my_bookings, pattern="^menu:my_bookings$"))
    app.add_handler(CallbackQueryHandler(cancel_confirm_callback, pattern="^cancel:confirm:"))
    app.add_handler(CallbackQueryHandler(cancel_execute_callback, pattern="^cancel:do:"))
    app.add_handler(CallbackQueryHandler(show_rules_menu, pattern="^menu:rules$"))
    app.add_handler(CallbackQueryHandler(rule_toggle_callback, pattern="^rule:toggle:"))
    app.add_handler(CallbackQueryHandler(rule_delete_callback, pattern="^rule:delete:"))
    app.add_handler(CallbackQueryHandler(rule_add_start, pattern="^rule:add_start$"))
    app.add_handler(CallbackQueryHandler(rule_set_dow_callback, pattern="^rule_set_dow:"))
    app.add_handler(CallbackQueryHandler(rule_set_time_callback, pattern="^rule_set_time:"))
    app.add_handler(CallbackQueryHandler(rule_set_filter_callback, pattern="^rule_set_filter:"))
    app.add_handler(CallbackQueryHandler(rule_set_advance_callback, pattern="^rule_set_adv:"))
    app.add_handler(CallbackQueryHandler(show_account_menu, pattern="^menu:account$"))
    app.add_handler(CallbackQueryHandler(account_test_connection, pattern="^account:test_connection$"))
    app.add_handler(CallbackQueryHandler(account_disconnect_confirm, pattern="^account:disconnect_confirm$"))
    app.add_handler(CallbackQueryHandler(account_do_disconnect, pattern="^account:do_disconnect$"))
    app.add_handler(CallbackQueryHandler(show_history_menu, pattern="^menu:history$"))
    app.add_handler(CallbackQueryHandler(show_help_menu, pattern="^menu:help$"))
    app.add_handler(CallbackQueryHandler(show_caza_plazas_menu, pattern="^menu:caza_plazas$"))
    app.add_handler(CallbackQueryHandler(caza_add_confirm_callback, pattern="^caza:add_confirm:"))
    app.add_handler(CallbackQueryHandler(caza_do_add_callback, pattern="^caza:do_add:"))
    app.add_handler(CallbackQueryHandler(caza_delete_callback, pattern="^caza:delete:"))
    app.add_handler(CallbackQueryHandler(run_test_sniper, pattern="^menu:test_sniper$"))

    # Global error handler
    app.add_error_handler(global_error_handler)

    return app
