#!/usr/bin/env bash
# =====================================================================
# Script para desplegar Drakkar WodBuster Bot a la Raspberry Pi
# =====================================================================

RPI_USER="${1:-$USER}"
RPI_IP="${2:-raspberrypi.local}"
RPI_DEST_DIR="/home/${RPI_USER}/drakkar-wodbuster-bot"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}======================================================================${NC}"
echo -e "${BLUE}       Despliegue a Raspberry Pi (${RPI_USER}@${RPI_IP})              ${NC}"
echo -e "${BLUE}======================================================================${NC}"

# Comprobar ping
echo -e "Verificando conexión con ${RPI_IP}..."
if ! ping -c 1 -W 2 "$RPI_IP" > /dev/null 2>&1; then
    echo -e "${RED}❌ Error: No se puede alcanzar la IP ${RPI_IP}. Asegúrate de que la Raspberry Pi está encendida y en la misma red.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Raspberry Pi alcanzable en ${RPI_IP}.${NC}\n"

# Sincronizar archivos excluyendo el entorno virtual x86_64 y temporales
echo -e "${YELLOW}Copiando archivos del proyecto a ${RPI_USER}@${RPI_IP}:${RPI_DEST_DIR}...${NC}"
echo -e "_(Se te pedirá la contraseña de ${RPI_USER} en la Raspberry Pi si no tienes clave SSH configurada)_"

rsync -avz --progress \
    --exclude "venv" \
    --exclude "__pycache__" \
    --exclude "*.pyc" \
    --exclude "bot.log" \
    --exclude ".secret.key" \
    --exclude "wodbuster_bot.db" \
    "$SCRIPT_DIR/" "${RPI_USER}@${RPI_IP}:${RPI_DEST_DIR}/"

echo -e "\n${GREEN}✅ Archivos transferidos correctamente a ${RPI_DEST_DIR}.${NC}\n"

echo -e "${BLUE}======================================================================${NC}"
echo -e "Para completar la instalación en la Raspberry Pi, ejecuta:"
echo -e "  ${YELLOW}ssh ${RPI_USER}@${RPI_IP} 'bash ${RPI_DEST_DIR}/install_raspberry.sh'${NC}"
echo -e "${BLUE}======================================================================${NC}\n"

read -p "¿Deseas ejecutar la instalación remota en la Raspberry Pi ahora mismo? (s/n): " RUN_INSTALL
if [[ "$RUN_INSTALL" =~ ^[sSyY]$ ]]; then
    echo -e "\n${YELLOW}Ejecutando instalación en la Raspberry Pi...${NC}"
    ssh -t "${RPI_USER}@${RPI_IP}" "bash ${RPI_DEST_DIR}/install_raspberry.sh"
fi
