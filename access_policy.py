from __future__ import annotations
import db as sqlite3
from datetime import datetime

from config import (
    TELEGRAM_CHAT_ID,
    now_local,
)

DEFAULT_AIRPORT_OPTIONS = [
    ("PVH", "Porto Velho"),
    ("RIO", "Rio de Janeiro"),
    ("SAO", "São Paulo"),
    ("BSB", "Brasília"),
    ("CGB", "Cuiabá"),
    ("GYN", "Goiânia"),
    ("MCZ", "Maceió"),
    ("AJU", "Aracaju"),
    ("SSA", "Salvador"),
    ("FOR", "Fortaleza"),
    ("SLZ", "São Luís"),
    ("CGR", "Campo Grande"),
    ("BHZ", "Belo Horizonte"),
    ("BEL", "Belém"),
    ("JPA", "João Pessoa"),
    ("CWB", "Curitiba"),
    ("REC", "Recife"),
    ("THE", "Teresina"),
    ("NAT", "Natal"),
    ("POA", "Porto Alegre"),
    ("FLN", "Florianópolis"),
    ("VIX", "Vitória"),
    ("MAO", "Manaus"),
    ("RBR", "Rio Branco"),
    ("BVB", "Boa Vista"),
    ("MCP", "Macapá"),
    ("PMW", "Palmas"),
]
DEFAULT_FREE_USES_LIMIT = 20
DEFAULT_MAX_ROUTES_DEFAULT = 6
DEFAULT_PIX_PENDING_EXPIRATION_HOURS = 24


def ensure_policy_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS monetization_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            test_mode INTEGER DEFAULT 1,
            charge_global INTEGER DEFAULT 0,
            charge_admin_only INTEGER DEFAULT 1,
            weekly_price REAL DEFAULT 5,
            biweekly_price REAL DEFAULT 10,
            monthly_price REAL DEFAULT 15,
            free_uses_limit INTEGER DEFAULT 20,
            max_routes_default INTEGER DEFAULT 6,
            pix_pending_expiration_hours INTEGER DEFAULT 24
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_access (
            chat_id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'free',
            expires_at TEXT,
            free_uses INTEGER DEFAULT 0,
            test_charge INTEGER DEFAULT 0,
            total_paid REAL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            chat_id TEXT PRIMARY KEY,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS airports (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for ddl in [
        "ALTER TABLE monetization_settings ADD COLUMN free_uses_limit INTEGER DEFAULT 20",
        "ALTER TABLE monetization_settings ADD COLUMN max_routes_default INTEGER DEFAULT 6",
        "ALTER TABLE monetization_settings ADD COLUMN pix_pending_expiration_hours INTEGER DEFAULT 24",
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """
        INSERT OR IGNORE INTO monetization_settings (
            id, test_mode, charge_global, charge_admin_only, weekly_price, biweekly_price, monthly_price,
            free_uses_limit, max_routes_default, pix_pending_expiration_hours
        ) VALUES (1, 1, 0, 1, 5, 10, 15, ?, ?, ?)
        """,
        (
            DEFAULT_FREE_USES_LIMIT,
            DEFAULT_MAX_ROUTES_DEFAULT,
            DEFAULT_PIX_PENDING_EXPIRATION_HOURS,
        ),
    )
    conn.execute(
        """
        UPDATE monetization_settings
        SET free_uses_limit = COALESCE(free_uses_limit, ?),
            max_routes_default = COALESCE(max_routes_default, ?),
            pix_pending_expiration_hours = COALESCE(pix_pending_expiration_hours, ?)
        WHERE id = 1
        """,
        (
            DEFAULT_FREE_USES_LIMIT,
            DEFAULT_MAX_ROUTES_DEFAULT,
            DEFAULT_PIX_PENDING_EXPIRATION_HOURS,
        ),
    )
    admins_count = conn.execute("SELECT COUNT(*) AS total FROM admins").fetchone()["total"]
    if int(admins_count or 0) == 0:
        conn.execute(
            "INSERT INTO admins (chat_id, active) VALUES (?, 1)",
            (TELEGRAM_CHAT_ID,),
        )
    for idx, (code, name) in enumerate(DEFAULT_AIRPORT_OPTIONS, start=1):
        conn.execute(
            """
            INSERT OR IGNORE INTO airports (code, name, active, sort_order)
            VALUES (?, ?, 1, ?)
            """,
            (str(code).upper(), str(name), idx),
        )
    conn.commit()


def get_monetization_settings(conn: sqlite3.Connection):
    ensure_policy_schema(conn)
    return conn.execute("SELECT * FROM monetization_settings WHERE id = 1").fetchone()


def is_admin_chat(conn: sqlite3.Connection, chat_id: str) -> bool:
    ensure_policy_schema(conn)
    row = conn.execute(
        "SELECT 1 FROM admins WHERE chat_id = ? AND active = 1 LIMIT 1",
        (chat_id,),
    ).fetchone()
    return bool(row)


def list_active_admin_chat_ids(conn: sqlite3.Connection) -> list[str]:
    ensure_policy_schema(conn)
    rows = conn.execute(
        "SELECT chat_id FROM admins WHERE active = 1 ORDER BY created_at ASC"
    ).fetchall()
    return [str(row["chat_id"]) for row in rows]


def list_airports(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    ensure_policy_schema(conn)
    rows = conn.execute(
        """
        SELECT code, name
        FROM airports
        WHERE active = 1
        ORDER BY sort_order ASC, code ASC
        """
    ).fetchall()
    return [(str(row["code"]).upper(), str(row["name"])) for row in rows]


def get_airport_labels(conn: sqlite3.Connection) -> dict[str, str]:
    options = list_airports(conn)
    return {code: f"{code} — {name}" for code, name in options}


def get_free_uses_limit(conn: sqlite3.Connection) -> int:
    settings = get_monetization_settings(conn)
    return int(settings["free_uses_limit"] or DEFAULT_FREE_USES_LIMIT)


def get_max_routes_default(conn: sqlite3.Connection) -> int:
    settings = get_monetization_settings(conn)
    return int(settings["max_routes_default"] or DEFAULT_MAX_ROUTES_DEFAULT)


def get_pix_pending_expiration_hours(conn: sqlite3.Connection) -> int:
    settings = get_monetization_settings(conn)
    return int(
        settings["pix_pending_expiration_hours"] or DEFAULT_PIX_PENDING_EXPIRATION_HOURS
    )


def ensure_user_access(conn: sqlite3.Connection, chat_id: str):
    ensure_policy_schema(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO user_access (chat_id, status, free_uses, test_charge, total_paid, updated_at)
        VALUES (?, 'free', 0, 0, 0, datetime('now'))
        """,
        (chat_id,),
    )
    conn.commit()
    return conn.execute("SELECT * FROM user_access WHERE chat_id = ?", (chat_id,)).fetchone()


def is_active_access(access_row) -> bool:
    if not access_row:
        return False
    if (access_row["status"] or "") != "active":
        return False
    expires_at = (access_row["expires_at"] or "").strip()
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) > now_local()
    except ValueError:
        return False


def should_charge_user(conn: sqlite3.Connection, chat_id: str, access_row) -> bool:
    settings = get_monetization_settings(conn)
    admin_chat = is_admin_chat(conn, chat_id)
    if admin_chat:
        return bool(
            int(settings["charge_admin_only"])
            or int(access_row["test_charge"] or 0)
            or int(settings["charge_global"])
        )
    if int(settings["charge_admin_only"]) == 1:
        return False
    return bool(int(settings["charge_global"]) or int(access_row["test_charge"] or 0))
