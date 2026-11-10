#!/usr/bin/env bash
# =====================================================================
# Script de inicio para Drakkar WodBuster Telegram Bot
# =====================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Export environment variables from .env if it exists
if [ -f .env ]; then
    echo "Cargando variables de entorno desde .env..."
    set -a
    source .env
    set +a
fi

# Check for virtual environment
if [ ! -d "venv" ]; then
    echo "Creando entorno virtual venv..."
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
fi

echo "Iniciando Drakkar WodBuster Bot..."
exec ./venv/bin/python main.py
