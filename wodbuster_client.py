import datetime
import json
import logging
import pickle
import re
import time
from typing import Optional, Tuple, List, Dict, Any

import pytz
import requests
from bs4 import BeautifulSoup

from config import DEFAULT_BOX_URL, LOGIN_URL, USER_AGENT, TIMEZONE

logger = logging.getLogger("wodbuster_client")

_UTC_TZ = pytz.timezone("UTC")


class WodBusterError(Exception):
    """Base exception for WodBuster operations."""
    pass


class WodBusterAuthError(WodBusterError):
    """Authentication or credential failure."""
    pass


class WodBusterClassFullError(WodBusterError):
    """Class has no available spots."""
    pass


class WodBusterNotOpenYetError(WodBusterError):
    """Booking window has not opened yet."""
    def __init__(self, message: str, available_at: Optional[datetime.datetime] = None, seconds_left: Optional[int] = None):
        super().__init__(message)
        self.available_at = available_at
        self.seconds_left = seconds_left


class WodBusterClient:
    def __init__(
        self,
        email: str,
        password: Optional[str] = None,
        cookies_blob: Optional[bytes] = None,
        box_url: str = DEFAULT_BOX_URL,
        athlete_id: Optional[str] = None,
    ):
        self.email = email.strip()
        self.password = password
        self.box_url = box_url.rstrip("/")
        self.athlete_id = athlete_id
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        })

        if cookies_blob:
            self.load_cookies_blob(cookies_blob)

    def get_cookies_blob(self) -> bytes:
        """Serialize current session cookies into bytes."""
        return pickle.dumps(self.session.cookies)

    def load_cookies_blob(self, cookies_blob: bytes):
        """Restore session cookies from bytes."""
        try:
            cookies = pickle.loads(cookies_blob)
            self.session.cookies.update(cookies)
        except Exception as e:
            logger.warning(f"Error restoring cookies: {e}")

    def test_session(self) -> bool:
        """Verify if current session cookies are still active and authorized."""
        try:
            url = f"{self.box_url}/user/preferences.aspx"
            resp = self.session.get(url, allow_redirects=False, timeout=10)
            if resp.status_code == 200 and "login" not in resp.url.lower():
                return True
            if resp.status_code in (301, 302):
                location = resp.headers.get("Location", "")
                if "login" in location.lower():
                    return False
                # Try following location if not login
                resp2 = self.session.get(location, allow_redirects=False, timeout=10)
                return resp2.status_code == 200 and "login" not in resp2.url.lower()
            return False
        except Exception as e:
            logger.warning(f"Session verification error: {e}")
            return False

    @staticmethod
    def _extract_header_val(text: str, header_name: str) -> str:
        """Extract ASP.NET async response hidden field values."""
        if header_name not in text:
            return ""
        try:
            idx = text.index(header_name)
            rest = text[idx + len(header_name) + 1:]
            return rest.split("|")[0]
        except Exception:
            return ""

    def login(self, force: bool = False) -> bool:
        """
        Authenticate with WodBuster for Drakkar.
        Returns True on success, raises WodBusterAuthError on bad credentials.
        """
        if not force and self.test_session():
            logger.info(f"Existing session valid for {self.email}")
            if not self.athlete_id:
                self.extract_athlete_id()
            return True

        if not self.password:
            raise WodBusterAuthError("No se proporcionó contraseña para iniciar sesión en WodBuster.")

        logger.info(f"Authenticating user {self.email} on Drakkar WodBuster...")
        login_url = f"{LOGIN_URL}?cb=drakkar&ReturnUrl={self.box_url}/user/"

        # Step 1: GET login page to obtain initial form tokens
        try:
            init_resp = self.session.get(login_url, timeout=15)
        except Exception as e:
            raise WodBusterAuthError(f"Error de red al conectar con WodBuster: {e}")

        soup = BeautifulSoup(init_resp.content, "lxml")

        token_tag = (
            soup.find(id="__RequestVerificationToken")
            or soup.find(attrs={"name": "__RequestVerificationToken"})
            or soup.find(id="CSRFToken")
        )
        token = token_tag["value"] if token_tag else ""

        viewstatec_tag = soup.find(id="__VIEWSTATEC")
        viewstatec = viewstatec_tag["value"] if viewstatec_tag else ""

        eventval_tag = soup.find(id="__EVENTVALIDATION")
        eventvalidation = eventval_tag["value"] if eventval_tag else ""

        # Step 2: POST credentials
        data_login = {
            "__RequestVerificationToken": token,
            "CSRFToken": token,
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATEC": viewstatec,
            "__VIEWSTATE": "",
            "__EVENTVALIDATION": eventvalidation,
            "__ASYNCPOST": "true",
            "ctl00$ctl00$body$ctl00": "ctl00$ctl00$body$ctl00|ctl00$ctl00$body$body$CtlLogin$CtlAceptar",
            "ctl00$ctl00$body$body$CtlLogin$IoTri": "",
            "ctl00$ctl00$body$body$CtlLogin$IoTrg": "",
            "ctl00$ctl00$body$body$CtlLogin$IoTra": "",
            "ctl00$ctl00$body$body$CtlLogin$IoEmail": self.email,
            "ctl00$ctl00$body$body$CtlLogin$IoPassword": self.password,
            "ctl00$ctl00$body$body$CtlLogin$cIoUid": "",
            "ctl00$ctl00$body$body$CtlLogin$CtlAceptar": "Aceptar\n",
        }

        login_resp = self.session.post(login_url, data=data_login, timeout=15)

        if "Warning" in login_resp.text or "Contraseña incorrecta" in login_resp.text:
            raise WodBusterAuthError("Credenciales incorrectas (email o contraseña inválidos en WodBuster).")

        # Step 3: Handle device trust confirmation if presented (CtlConfiar)
        if "CtlConfiar" in login_resp.text:
            v_conf = self._extract_header_val(login_resp.text, "__VIEWSTATEC") or viewstatec
            ev_conf = self._extract_header_val(login_resp.text, "__EVENTVALIDATION") or eventvalidation
            tok_conf = self._extract_header_val(login_resp.text, "__RequestVerificationToken") or token

            data_confirm = {
                "__RequestVerificationToken": tok_conf,
                "CSRFToken": tok_conf,
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "__VIEWSTATEC": v_conf,
                "__VIEWSTATE": "",
                "__EVENTVALIDATION": ev_conf,
                "__ASYNCPOST": "true",
                "ctl00$ctl00$body$ctl00": "ctl00$ctl00$body$ctl00|ctl00$ctl00$body$body$CtlConfiar$CtlSeguro",
                "ctl00$ctl00$body$body$CtlConfiar$dispositivo": "CtlSeguro",
            }
            conf_resp = self.session.post(login_url, data=data_confirm, timeout=15)
            if conf_resp.status_code != 200:
                logger.warning(f"Confirm request returned status {conf_resp.status_code}")

        # Step 4: Visit user landing to establish box cookies
        user_landing = self.session.get(f"{self.box_url}/user/", allow_redirects=True, timeout=15)
        if "login" in user_landing.url.lower():
            # Try roadtobox.aspx redirect
            road_resp = self.session.get("https://wodbuster.com/account/roadtobox.aspx", allow_redirects=True, timeout=15)
            if "login" in road_resp.url.lower():
                raise WodBusterAuthError("No se pudo iniciar sesión. WodBuster no reconoció las credenciales.")

        # Extract athlete info
        self.extract_athlete_id()
        logger.info(f"Login successfully completed for {self.email} (Athlete ID: {self.athlete_id})")
        return True

    def extract_athlete_id(self) -> Optional[str]:
        """Extract athlete UUID and profile info from user preferences."""
        try:
            pref_url = f"{self.box_url}/user/preferences.aspx"
            resp = self.session.get(pref_url, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "lxml")
                # Pattern: /static/atletas/{a}/{b}/{c}/{athlete_id}.jpg
                for img in soup.find_all("img"):
                    src = img.get("src", "")
                    match = re.search(r"/static/atletas/[^/]+/[^/]+/[^/]+/([a-f0-9\-]+)\.jpg", src, re.IGNORECASE)
                    if match:
                        self.athlete_id = match.group(1)
                        logger.info(f"Found athlete ID: {self.athlete_id}")
                        return self.athlete_id

                # Secondary check for athlete link
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    match = re.search(r"idu=([a-f0-9\-]+)", href, re.IGNORECASE)
                    if match:
                        self.athlete_id = match.group(1)
                        return self.athlete_id
        except Exception as e:
            logger.warning(f"Could not extract athlete ID: {e}")
        return self.athlete_id

    def get_classes_for_date(self, target_date: datetime.date) -> Dict[str, Any]:
        """
        Fetch classes and booking availability for a specific date from Drakkar.
        Returns a dict with 'classes' list and metadata.
        """
        self.login()

        # Epoch for midnight UTC
        midnight = _UTC_TZ.localize(datetime.datetime.combine(target_date, datetime.time.min))
        epoch = int(midnight.timestamp())

        idu_param = f"&idu={self.athlete_id.replace('-', '')}" if self.athlete_id else ""
        url = f"{self.box_url}/athlete/handlers/LoadClass.ashx?ticks={epoch}{idu_param}"

        try:
            resp = self.session.get(url, timeout=15)
        except Exception as e:
            raise WodBusterError(f"Error al conectar con el calendario de Drakkar: {e}")

        if resp.status_code == 302 and "login" in resp.headers.get("Location", ""):
            # Session expired, force re-login and retry once
            logger.info("Session expired during LoadClass, attempting re-login...")
            self.login(force=True)
            idu_param = f"&idu={self.athlete_id.replace('-', '')}" if self.athlete_id else ""
            url = f"{self.box_url}/athlete/handlers/LoadClass.ashx?ticks={epoch}{idu_param}"
            resp = self.session.get(url, timeout=15)

        if resp.status_code != 200:
            raise WodBusterError(f"WodBuster devolvió código de error HTTP {resp.status_code}")

        try:
            data = resp.json()
        except Exception:
            raise WodBusterError("Respuesta no válida (no JSON) recibida de WodBuster.")

        parsed_classes = []
        raw_data = data.get("Data", [])

        is_open = bool(raw_data)
        segundos_hasta_publicacion = data.get("SegundosHastaPublicacion")
        primera_hora_publicacion = data.get("PrimeraHoraPublicacion")

        athlete_id_clean = self.athlete_id.replace("-", "").lower() if self.athlete_id else ""

        for slot in raw_data:
            slot_hora = slot.get("Hora", "")
            valores = slot.get("Valores", [])

            for item in valores:
                tipo_estado = item.get("TipoEstado", "")  # Borrable, Cambiable, Inscribible
                valor = item.get("Valor", {}) or {}

                # The workout name is inside 'Valor': e.g. 'WOD DK 1.0', 'OPEN DK 1.0'
                nombre_entrenamiento = (
                    valor.get("Nombre")
                    or item.get("Nombre")
                    or item.get("NombreE")
                    or valor.get("NombreE")
                    or "Clase"
                ).strip()

                class_id = valor.get("Id")
                plazas = valor.get("Plazas", 0)
                atletas = valor.get("AtletasEntrenando", [])
                ocupadas = len(atletas)

                # Check if current user is enrolled in this class
                is_booked = tipo_estado == "Borrable"
                if not is_booked and athlete_id_clean:
                    for atleta in atletas:
                        atleta_url = atleta.get("Url", "").lower()
                        if athlete_id_clean in atleta_url:
                            is_booked = True
                            break

                can_change = tipo_estado == "Cambiable"
                is_full = ocupadas >= plazas and not is_booked

                parsed_classes.append({
                    "id": class_id,
                    "hora": slot_hora,
                    "nombre": nombre_entrenamiento,
                    "plazas": plazas,
                    "ocupadas": ocupadas,
                    "libres": max(0, plazas - ocupadas),
                    "is_full": is_full,
                    "is_booked": is_booked,
                    "can_change": can_change,
                    "tipo_estado": tipo_estado,
                    "date": target_date.strftime("%Y-%m-%d"),
                    "ticks": epoch,
                })

        return {
            "date": target_date,
            "epoch": epoch,
            "is_open": is_open,
            "segundos_hasta_publicacion": segundos_hasta_publicacion,
            "primera_hora_publicacion": primera_hora_publicacion,
            "classes": parsed_classes,
        }

    def book_class(
        self,
        class_id: int,
        target_date: datetime.date,
        is_change: bool = False,
    ) -> Tuple[bool, str]:
        """
        Book a class at Drakkar.
        Returns (success: bool, message: str).
        """
        self.login()

        midnight = _UTC_TZ.localize(datetime.datetime.combine(target_date, datetime.time.min))
        epoch = int(midnight.timestamp())

        handler = "Calendario_Mover.ashx" if is_change else "Calendario_Inscribir.ashx"
        url = f"{self.box_url}/athlete/handlers/{handler}?id={class_id}&ticks={epoch}"

        logger.info(f"Calling booking handler {handler} for class {class_id}...")
        try:
            resp = self.session.get(url, timeout=15)
        except Exception as e:
            return False, f"Error de red: {e}"

        if resp.status_code == 302 and "login" in resp.headers.get("Location", ""):
            self.login(force=True)
            resp = self.session.get(url, timeout=15)

        try:
            res_json = resp.json()
        except Exception:
            return False, "Respuesta inválida de WodBuster al reservar."

        res_body = res_json.get("Res", {})
        is_ok = bool(res_body.get("EsCorrecto"))
        err_msg = res_body.get("ErrorMsg", "")

        if is_ok:
            return True, "¡Reserva realizada con éxito!"
        else:
            return False, err_msg or "No se pudo realizar la reserva."

    def cancel_class(
        self,
        class_id: int,
        target_date: datetime.date,
    ) -> Tuple[bool, str]:
        """
        Cancel an existing booking at Drakkar.
        Returns (success: bool, message: str).
        """
        self.login()

        if not self.athlete_id:
            self.extract_athlete_id()

        if not self.athlete_id:
            return False, "No se pudo determinar el ID de atleta para cancelar la reserva."

        midnight = _UTC_TZ.localize(datetime.datetime.combine(target_date, datetime.time.min))
        epoch = int(midnight.timestamp())
        idu_clean = self.athlete_id.replace("-", "")
        cache_buster = int(time.time() * 1000)

        url = (
            f"{self.box_url}/athlete/handlers/Calendario_Borrar.ashx"
            f"?id={class_id}&ticks={epoch}&idu={idu_clean}&_={cache_buster}"
        )

        logger.info(f"Calling cancel handler for class {class_id}...")
        try:
            resp = self.session.get(url, timeout=15)
        except Exception as e:
            return False, f"Error de red al cancelar: {e}"

        if resp.status_code == 302 and "login" in resp.headers.get("Location", ""):
            self.login(force=True)
            resp = self.session.get(url, timeout=15)

        try:
            res_json = resp.json()
        except Exception as e:
            return False, f"Respuesta no válida de WodBuster al cancelar: {e}"

        res_body = res_json.get("Res", {})
        is_ok = bool(res_body.get("EsCorrecto"))
        err_msg = res_body.get("ErrorMsg", "")

        if is_ok:
            return True, "Reserva cancelada correctamente."
        else:
            return False, err_msg or "No se pudo cancelar la reserva."

    def get_my_active_bookings(self, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """
        Find all active reservations of the user within the upcoming days.
        """
        self.login()
        today = datetime.datetime.now(TIMEZONE).date()
        my_bookings = []

        for offset in range(days_ahead):
            curr_date = today + datetime.timedelta(days=offset)
            try:
                res = self.get_classes_for_date(curr_date)
                for c in res.get("classes", []):
                    if c.get("is_booked"):
                        my_bookings.append({
                            "id": c["id"],
                            "date": curr_date,
                            "date_str": curr_date.strftime("%d/%m/%Y"),
                            "day_name": ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"][curr_date.weekday()],
                            "hora": c["hora"],
                            "nombre": c["nombre"],
                            "ocupadas": c["ocupadas"],
                            "plazas": c["plazas"],
                        })
            except Exception as e:
                logger.warning(f"Error checking bookings for {curr_date}: {e}")

        return my_bookings
