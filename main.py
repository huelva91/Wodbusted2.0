import asyncio
import logging
import os
import sys

from config import TELEGRAM_BOT_TOKEN
from database import init_db
from scheduler import AutoBookingScheduler
from telegram_bot import setup_telegram_application

# Configure logging with automatic rotation: max 5MB per file, max 3 backups (total <= 15MB)
from logging.handlers import RotatingFileHandler

rotating_handler = RotatingFileHandler(
    "bot.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        rotating_handler,
    ],
)

# Silence repetitive polling noise from external libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger("main")


def print_banner():
    banner = """
    ╔═════════════════════════════════════════════════════════════════════╗
    ║                 DRAKKAR WODBUSTER AUTOMA-BOT 🏋️‍♂️                     ║
    ║        Automatización de clases y panel de control por Telegram     ║
    ║                  Box: https://drakkar.wodbuster.com                 ║
    ╚═════════════════════════════════════════════════════════════════════╝
    """
    print(banner)


def main():
    print_banner()

    # Initialize database
    logger.info("Initializing SQLite database...")
    init_db()
    logger.info("Database initialized successfully.")

    # Check Telegram Token
    token = TELEGRAM_BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("=" * 65)
        logger.error("¡ATENCIÓN! Falta configurar el TELEGRAM_BOT_TOKEN")
        logger.error("=" * 65)
        logger.error("Para poner en marcha el bot necesitas un token de Telegram:")
        logger.error("1. Abre Telegram y habla con @BotFather.")
        logger.error("2. Envía el comando /newbot y sigue las instrucciones.")
        logger.error("3. Copia el token que te dé BotFather.")
        logger.error("4. Pega el token en el archivo .env:")
        logger.error("   TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRstuVWXyz")
        logger.error("=" * 65)
        print("\nEjecución detenida: Configura TELEGRAM_BOT_TOKEN en el archivo .env o variable de entorno.\n")
        sys.exit(1)

    logger.info("Setting up Telegram application...")
    application = setup_telegram_application(token)

    async def post_init(app):
        scheduler = AutoBookingScheduler(bot=app.bot)
        scheduler.start()
        app.bot_data["scheduler"] = scheduler
        logger.info("Scheduler started successfully inside active event loop.")

    async def post_shutdown(app):
        scheduler = app.bot_data.get("scheduler")
        if scheduler:
            scheduler.stop()
            logger.info("Scheduler stopped.")

    application.post_init = post_init
    application.post_shutdown = post_shutdown

    logger.info("Starting Telegram Bot polling loop...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
