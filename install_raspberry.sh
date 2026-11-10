#!/usr/bin/env bash
set -e

# =====================================================================
# Script de instalación y configuración en la Raspberry Pi
# =====================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$(whoami)"

echo -e "${BLUE}======================================================================${NC}"
echo -e "${BLUE}      Instalando Drakkar WodBuster Bot en Raspberry Pi 🍓             ${NC}"
echo -e "${BLUE}======================================================================${NC}"
echo -e "Directorio de la app: ${YELLOW}${APP_DIR}${NC}"
echo -e "Usuario: ${YELLOW}${CURRENT_USER}${NC}\n"

cd "$APP_DIR"

# 1. Comprobar e instalar paquetes del sistema si es Debian / Raspberry Pi OS
if command -v apt-get &> /dev/null; then
    echo -e "${YELLOW}[1/4] Comprobando paquetes de sistema (python3-venv, python3-pip)...${NC}"
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-venv python3-pip libxml2-dev libxslt1-dev
fi

# 2. Crear entorno virtual si no existe
echo -e "${YELLOW}[2/4] Creando entorno virtual Python...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# 3. Instalar librerías de requirements.txt
echo -e "${YELLOW}[3/4] Instalando dependencias en venv (esto puede tardar 1-2 min en Raspberry Pi)...${NC}"
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q

# 4. Configurar servicio systemd para que arranque siempre al encender la Raspberry
echo -e "${YELLOW}[4/4] Configurando servicio systemd 'drakkar-bot' para arranque 24/7...${NC}"

SERVICE_FILE="/etc/systemd/system/drakkar-bot.service"

sudo bash -c "cat <<EOF > ${SERVICE_FILE}
[Unit]
Description=Drakkar WodBuster Telegram Bot
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/run.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable drakkar-bot.service

echo -e "\n${GREEN}======================================================================${NC}"
echo -e "${GREEN}  ¡Instalación en la Raspberry Pi completada con éxito! 🎉            ${NC}"
echo -e "${GREEN}======================================================================${NC}"
echo -e "Para controlar el bot en la Raspberry Pi:"
echo -e "  • Iniciar servicio:   ${YELLOW}sudo systemctl start drakkar-bot${NC}"
echo -e "  • Ver estado:         ${YELLOW}sudo systemctl status drakkar-bot${NC}"
echo -e "  • Ver logs en vivo:   ${YELLOW}journalctl -u drakkar-bot -f${NC}"
echo -e "  • Detener servicio:   ${YELLOW}sudo systemctl stop drakkar-bot${NC}\n"

# Iniciar el servicio automáticamente si el .env tiene el token configurado
if grep -q "YOUR_TELEGRAM_BOT_TOKEN_HERE" .env 2>/dev/null; then
    echo -e "${RED}⚠️  AVISO:${NC} Recuerda editar el archivo ${YELLOW}${APP_DIR}/.env${NC} con tu TELEGRAM_BOT_TOKEN real antes de iniciar el bot con: ${YELLOW}sudo systemctl start drakkar-bot${NC}"
else
    echo -e "${BLUE}Iniciando el servicio ahora...${NC}"
    sudo systemctl restart drakkar-bot.service
    sleep 2
    sudo systemctl status drakkar-bot.service --no-pager
fi
