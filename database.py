import sqlite3
import datetime
from typing import Optional, List, Dict, Any
from config import DB_PATH
from encryption import encrypt_password, decrypt_password


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Enable WAL mode and busy timeout for concurrent safety
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=30000;")

    # Users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        wodbuster_email TEXT NOT NULL,
        wodbuster_password_enc TEXT NOT NULL,
        athlete_id TEXT,
        cookies_blob BLOB,
        is_active INTEGER DEFAULT 1,
        last_login_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Auto rules table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS auto_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        day_of_week INTEGER NOT NULL, -- 0=Lunes, 6=Domingo
        class_time TEXT NOT NULL,      -- HH:MM:SS or HH:MM
        class_name_filter TEXT DEFAULT 'ALL',
        days_advance INTEGER DEFAULT 1,
        opening_time TEXT DEFAULT '22:30:00',
        is_active INTEGER DEFAULT 1,
        last_booked_date TEXT,         -- YYYY-MM-DD
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
    );
    """)

    # Booking logs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS booking_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        action TEXT NOT NULL,          -- AUTO_BOOK, MANUAL_BOOK, CANCEL, SNIPER, CAZA_PLAZAS
        class_name TEXT,
        class_datetime TEXT,
        status TEXT NOT NULL,          -- SUCCESS, FAILED, FULL, NOT_FOUND
        message TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Caza-Plazas (waitlist watcher) table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS caza_plazas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        class_id INTEGER NOT NULL,
        class_date TEXT NOT NULL,      -- YYYY-MM-DD
        class_time TEXT NOT NULL,      -- HH:MM
        class_name TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
    );
    """)

    conn.commit()
    conn.close()


def save_user(
    chat_id: int,
    email: str,
    password: str,
    athlete_id: Optional[str] = None,
    cookies_blob: Optional[bytes] = None,
):
    """Save or update user credentials in the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    password_enc = encrypt_password(password)
    now = datetime.datetime.now().isoformat()

    cursor.execute("""
    INSERT INTO users (chat_id, wodbuster_email, wodbuster_password_enc, athlete_id, cookies_blob, is_active, last_login_at)
    VALUES (?, ?, ?, ?, ?, 1, ?)
    ON CONFLICT(chat_id) DO UPDATE SET
        wodbuster_email = excluded.wodbuster_email,
        wodbuster_password_enc = excluded.wodbuster_password_enc,
        athlete_id = COALESCE(excluded.athlete_id, users.athlete_id),
        cookies_blob = COALESCE(excluded.cookies_blob, users.cookies_blob),
        is_active = 1,
        last_login_at = excluded.last_login_at;
    """, (chat_id, email, password_enc, athlete_id, cookies_blob, now))

    conn.commit()
    conn.close()


def get_user(chat_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve user and decrypted password."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    user = dict(row)
    user["wodbuster_password"] = decrypt_password(user["wodbuster_password_enc"])
    return user


def update_user_session(chat_id: int, cookies_blob: Optional[bytes] = None, athlete_id: Optional[str] = None):
    """Update cookies and athlete id for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.datetime.now().isoformat()

    if cookies_blob and athlete_id:
        cursor.execute(
            "UPDATE users SET cookies_blob = ?, athlete_id = ?, last_login_at = ? WHERE chat_id = ?",
            (cookies_blob, athlete_id, now, chat_id)
        )
    elif cookies_blob:
        cursor.execute(
            "UPDATE users SET cookies_blob = ?, last_login_at = ? WHERE chat_id = ?",
            (cookies_blob, now, chat_id)
        )
    elif athlete_id:
        cursor.execute(
            "UPDATE users SET athlete_id = ? WHERE chat_id = ?",
            (athlete_id, chat_id)
        )

    conn.commit()
    conn.close()


def delete_user(chat_id: int):
    """Delete user and all associated rules/logs."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auto_rules WHERE chat_id = ?", (chat_id,))
    cursor.execute("DELETE FROM booking_logs WHERE chat_id = ?", (chat_id,))
    cursor.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()


def add_auto_rule(
    chat_id: int,
    day_of_week: int,
    class_time: str,
    class_name_filter: str = "ALL",
    days_advance: int = 1,
    opening_time: str = "22:30:00"
) -> int:
    """Add a recurring auto-booking rule."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Normalize time format to HH:MM:00
    parts = class_time.strip().split(":")
    if len(parts) == 1:
        class_time = f"{int(parts[0]):02d}:00:00"
    elif len(parts) == 2:
        class_time = f"{int(parts[0]):02d}:{int(parts[1]):02d}:00"
    elif len(parts) >= 3:
        class_time = f"{int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"

    # Normalize opening_time
    parts_op = opening_time.strip().split(":")
    if len(parts_op) == 1:
        opening_time = f"{int(parts_op[0]):02d}:00:00"
    elif len(parts_op) == 2:
        opening_time = f"{int(parts_op[0]):02d}:{int(parts_op[1]):02d}:00"
    elif len(parts_op) >= 3:
        opening_time = f"{int(parts_op[0]):02d}:{int(parts_op[1]):02d}:{int(parts_op[2]):02d}"

    cursor.execute("""
    INSERT INTO auto_rules (chat_id, day_of_week, class_time, class_name_filter, days_advance, opening_time, is_active)
    VALUES (?, ?, ?, ?, ?, ?, 1)
    """, (chat_id, day_of_week, class_time, class_name_filter, days_advance, opening_time))

    rule_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return rule_id


def get_user_rules(chat_id: int) -> List[Dict[str, Any]]:
    """Get all rules configured for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM auto_rules WHERE chat_id = ? ORDER BY day_of_week ASC, class_time ASC",
        (chat_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_active_rules() -> List[Dict[str, Any]]:
    """Get all active rules across all users."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.*, u.wodbuster_email, u.wodbuster_password_enc, u.athlete_id, u.cookies_blob
        FROM auto_rules r
        JOIN users u ON r.chat_id = u.chat_id
        WHERE r.is_active = 1 AND u.is_active = 1
    """)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        item = dict(r)
        item["wodbuster_password"] = decrypt_password(item["wodbuster_password_enc"])
        results.append(item)
    return results


def toggle_rule(rule_id: int, chat_id: int) -> Optional[bool]:
    """Toggle the active status of a rule. Returns new status or None if not found."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM auto_rules WHERE id = ? AND chat_id = ?", (rule_id, chat_id))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    new_status = 0 if row["is_active"] == 1 else 1
    cursor.execute("UPDATE auto_rules SET is_active = ? WHERE id = ?", (new_status, rule_id))
    conn.commit()
    conn.close()
    return bool(new_status)


def delete_rule(rule_id: int, chat_id: int) -> bool:
    """Delete an auto rule."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM auto_rules WHERE id = ? AND chat_id = ?", (rule_id, chat_id))
    affected = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return affected


def update_rule_last_booked(rule_id: int, date_str: str):
    """Update last booked date string (YYYY-MM-DD)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE auto_rules SET last_booked_date = ? WHERE id = ?", (date_str, rule_id))
    conn.commit()
    conn.close()


def log_booking_action(
    chat_id: int,
    action: str,
    class_name: str,
    class_datetime: str,
    status: str,
    message: str = ""
):
    """Insert a log record for any booking or cancel action."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO booking_logs (chat_id, action, class_name, class_datetime, status, message)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (chat_id, action, class_name, class_datetime, status, message))
    conn.commit()
    conn.close()


def get_recent_logs(chat_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve recent booking logs for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM booking_logs WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
        (chat_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# =========================================================================
# MODO CAZA-PLAZAS (WAITLIST SPOT HUNTER)
# =========================================================================

def add_caza_plaza(
    chat_id: int,
    class_id: int,
    class_date: str,
    class_time: str,
    class_name: str
) -> int:
    """Add a class watcher in Modo Caza-Plazas."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Normalize class_time to HH:MM:00
    p = str(class_time).strip().split(":")
    h = int(p[0])
    m = int(p[1]) if len(p) > 1 else 0
    s = int(p[2]) if len(p) > 2 else 0
    class_time = f"{h:02d}:{m:02d}:{s:02d}"

    # Check if already watching this class
    cursor.execute(
        "SELECT id FROM caza_plazas WHERE chat_id = ? AND class_id = ? AND class_date = ? AND is_active = 1",
        (chat_id, class_id, class_date)
    )
    existing = cursor.fetchone()
    if existing:
        conn.close()
        return existing["id"]

    cursor.execute("""
    INSERT INTO caza_plazas (chat_id, class_id, class_date, class_time, class_name, is_active)
    VALUES (?, ?, ?, ?, ?, 1)
    """, (chat_id, class_id, class_date, class_time, class_name))

    watch_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return watch_id


def get_user_caza_plazas(chat_id: int) -> List[Dict[str, Any]]:
    """Retrieve active watches for a specific user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM caza_plazas WHERE chat_id = ? AND is_active = 1 ORDER BY class_date ASC, class_time ASC",
        (chat_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_active_caza_plazas() -> List[Dict[str, Any]]:
    """Retrieve all active watches across all users with user credentials."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.*, u.wodbuster_email, u.wodbuster_password_enc, u.athlete_id, u.cookies_blob
        FROM caza_plazas c
        JOIN users u ON c.chat_id = u.chat_id
        WHERE c.is_active = 1 AND u.is_active = 1
        ORDER BY c.class_date ASC, c.class_time ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        item = dict(r)
        item["wodbuster_password"] = decrypt_password(item["wodbuster_password_enc"])
        results.append(item)
    return results


def delete_caza_plaza(watch_id: int, chat_id: int) -> bool:
    """Delete a caza_plaza watch."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM caza_plazas WHERE id = ? AND chat_id = ?", (watch_id, chat_id))
    affected = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return affected


def deactivate_caza_plaza(watch_id: int):
    """Mark a caza_plaza watch as inactive (after successful booking or expired)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE caza_plazas SET is_active = 0 WHERE id = ?", (watch_id,))
    conn.commit()
    conn.close()
