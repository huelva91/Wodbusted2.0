import os
from pathlib import Path
import pytz

# Base directory
BASE_DIR = Path(__file__).resolve().parent

# Drakkar WodBuster Settings
DEFAULT_BOX_URL = os.getenv("WODBUSTER_BOX_URL", "https://drakkar.wodbuster.com").rstrip("/")
DEFAULT_BOX_NAME = os.getenv("WODBUSTER_BOX_NAME", "drakkar")
LOGIN_URL = "https://wodbuster.com/account/login.aspx"
TIMEZONE = pytz.timezone(os.getenv("TIMEZONE", "Europe/Madrid"))

# Database path
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "wodbuster_bot.db"))

# Telegram Bot Token
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Secret key file for password encryption
SECRET_KEY_FILE = BASE_DIR / ".secret.key"

# User-Agent for mimicking modern browser
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Standard days of week in Spanish
DAYS_OF_WEEK = [
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo",
]
