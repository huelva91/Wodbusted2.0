# 🏋️‍♂️ WodBusted

> **Bot autónomo y panel de control en Telegram para reserva automática de clases, francotirador al segundo (Sniper) y caza-plazas en tiempo real para boxes de CrossFit con WodBuster.**

---

## 🌟 Características Destacadas

- 🎯 **Sniper de Precisión al Segundo:**
  - Sincroniza la petición HTTP con la apertura oficial del box (por defecto 22:30:00.000).
  - Cálculo de antelación con corrutinas de suspensión precisa (`asyncio.sleep`) para disparar en el instante exacto.
  
- 🦅 **Modo Caza-Plazas (Waitlist Spot Hunter):**
  - Monitorización asíncrona periódica (cada 20 segundos) para clases que se encuentren completas.
  - En cuanto un usuario cancela su plaza, el bot la captura de inmediato.
  - Soporte de reubicación automática (`Calendario_Mover.ashx`) si ya tenías otra clase reservada en el mismo día.

- 🧠 **Priorización Inteligente y Respaldo (Fallback):**
  - Orden de disparo priorizando modalidades con plazas muy reducidas (como *Gymnastics* o *Halterofilia*, de 8 plazas) sobre clases estándar (WOD de 12-24 plazas).
  - Si la opción preferida se llena al instante, activa automáticamente el Modo Caza-Plazas y ejecuta de forma inmediata la regla de respaldo para no quedarte sin entrenar.

- 📱 **Interfaz de Usuario Dual en Telegram:**
  - **Inline Keyboard Dinámico:** Navegación interactiva por calendarios, plazas libres, detalles de coach y confirmación de cancelaciones en 1 toque.
  - **Persistent Reply Keyboard:** Teclado inferior nativo fijo en pantalla (`is_persistent=True`), siempre accesible sin importar el flujo de notificaciones.
  - Acceso directo a menú y reservas adjunto al pie de cada notificación push.

- 🛡️ **Seguridad y Cifrado:**
  - Cifrado simétrico autenticado mediante **Fernet (AES-128-CBC)** para almacenar contraseñas de WodBuster de forma segura.
  - Eliminación automática de mensajes que contengan contraseñas en el chat de Telegram.
  - Base de datos SQLite optimizada con modo **WAL (Write-Ahead Logging)** y control de concurrencia (`busy_timeout`).

- 🔄 **Gestor de Sesión Resiliente:**
  - Manejo de cookies persistentes, verificación de dispositivo (`CtlConfiar`), ViewState/EventValidation y auto re-login ante respuestas `302 Found`.
  - Rotación automática de logs (`RotatingFileHandler`) para evitar saturación de almacenamiento en dispositivos embebidos.

---

## 🏗️ Arquitectura del Sistema

```text
┌────────────────────────┐
│     Usuario Telegram   │
└───────────┬────────────┘
            │ Interacción (Inline / Persistent Keyboard)
            ▼
┌────────────────────────┐         ┌────────────────────────┐
│  telegram_bot.py       │ ◄─────► │   database.py (SQLite) │
│  (python-telegram-bot) │         │   (WAL Mode + AES)     │
└───────────┬────────────┘         └────────────────────────┘
            │
            ▼
┌────────────────────────┐         ┌────────────────────────┐
│  scheduler.py          │ ◄─────► │  wodbuster_client.py   │
│  (APScheduler/AsyncIO) │         │  (HTTP Session / Auth) │
└────────────────────────┘         └───────────┬────────────┘
            │                                  │
            ▼                                  ▼
┌────────────────────────┐         ┌────────────────────────┐
│ Caza-Plazas & Sniper   │         │ Plataforma WodBuster   │
└────────────────────────┘         └────────────────────────┘
```

---

## 📁 Estructura del Repositorio

```text
wodbusted/
├── config.py                 # Configuración de box, zona horaria y rutas
├── database.py               # Capa de datos SQLite (WAL, usuarios, reglas, caza-plazas)
├── encryption.py             # Cifrado de credenciales con Fernet
├── wodbuster_client.py       # Cliente HTTP (scraping, tokens, reservas, cancelaciones)
├── scheduler.py              # Motor de planificación (Sniper de precisión y Caza-Plazas)
├── telegram_bot.py           # Bot de Telegram con menú inline y teclado persistente
├── main.py                   # Inicialización de servicios y ciclo de vida
├── run.sh                    # Script de arranque
├── install_raspberry.sh      # Instalador desatendido para Raspberry Pi / Linux
├── deploy_to_raspberry.sh    # Script de sincronización remota por SSH/rsync
├── requirements.txt          # Dependencias Python
├── .env.example              # Plantilla de variables de entorno
└── .gitignore                # Reglas de exclusión de ficheros sensibles y temporales
```

---

## 🚀 Instalación y Puesta en Marcha

### 1. Clonar el Repositorio
```bash
git clone https://github.com/huelva91/wodbusted.git
cd wodbusted
```

### 2. Configurar el Entorno
Copia la plantilla de entorno y edita tu token de Telegram:
```bash
cp .env.example .env
nano .env
```

Configura tus variables:
```ini
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRstuVWXyz
WODBUSTER_BOX_URL=https://drakkar.wodbuster.com
WODBUSTER_BOX_NAME=drakkar
TIMEZONE=Europe/Madrid
```

### 3. Instalar Dependencias
Crea un entorno virtual e instala los paquetes requeridos:
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Ejecución Local
```bash
./run.sh
```

---

## 🍓 Despliegue en Raspberry Pi / Servidor Linux (24/7)

El proyecto incluye un script de instalación automatizado para configurar el entorno virtual y registrar el servicio `systemd`:

```bash
bash install_raspberry.sh
```

El servicio `drakkar-bot.service` se iniciará automáticamente y se mantendrá activo tras reinicios del sistema:

```bash
# Consultar estado del servicio
sudo systemctl status drakkar-bot

# Ver logs en tiempo real
journalctl -u drakkar-bot -f
```

---

## 📱 Comandos Disponibles en Telegram

| Comando | Descripción |
| :--- | :--- |
| `/start` | Inicia el bot y despliega el menú principal interactivo junto con el teclado fijo. |
| `/menu` | Muestra el panel interactivo de opciones. |
| `/clases` | Muestra los horarios y disponibilidad del día de hoy y de la semana. |
| `/mis_reservas` | Consulta las clases reservadas en los próximos 7 días y permite cancelarlas. |
| `/reglas` | Muestra y gestiona las reglas recurrentes del Sniper. |
| `/caza` | Accede al estado del Modo Caza-Plazas y clases en vigilancia. |
| `/test` o `/test_sniper` | Ejecuta un test de diagnóstico de conexión, credenciales y reglas de apertura. |
| `/ayuda` | Guía de uso rápido del bot. |

---

## 📄 Licencia

Distribuido bajo la Licencia MIT. Consulta el archivo `LICENSE` para más información.
