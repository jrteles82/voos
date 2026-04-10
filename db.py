from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import mysql.connector
    from mysql.connector import errorcode
    from mysql.connector.cursor import MySQLCursorDict
    from mysql.connector.errors import IntegrityError as MySQLIntegrityError
    from mysql.connector.errors import OperationalError as MySQLOperationalError
except ImportError:  # pragma: no cover
    mysql = None
    errorcode = None
    MySQLCursorDict = Any
    MySQLIntegrityError = Exception
    MySQLOperationalError = Exception


DatabaseIntegrityError = (sqlite3.IntegrityError, MySQLIntegrityError)
DatabaseOperationalError = (sqlite3.OperationalError, MySQLOperationalError)


def mysql_enabled() -> bool:
    return bool(os.getenv("MYSQL_HOST"))


def _mysql_config() -> dict[str, Any]:
    database = (
        os.getenv("MYSQL_DATABASE")
        or os.getenv("MYSQL_DB")
        or os.getenv("MYSQL_AUTH_DATABASE")
        or os.getenv("MYSQL_RESULTS_DATABASE")
    )
    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", ""),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": database,
    }


class DBCursorWrapper:
    def __init__(self, cursor, backend: str):
        self._cursor = cursor
        self._backend = backend

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if self._backend == "mysql":
            return row
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        return rows


class DBConnection:
    def __init__(self, conn, backend: str, database_name: Optional[str] = None):
        self._conn = conn
        self.backend = backend
        self.database_name = database_name

    def _normalize_query(self, query: str) -> str:
        if self.backend == "mysql":
            return query.replace("?", "%s")
        return query

    def execute(self, query: str, params: Iterable[Any] = ()):
        sql = self._normalize_query(query)
        if self.backend == "mysql":
            cursor = self._conn.cursor(dictionary=True)
            cursor.execute(sql, tuple(params))
            return DBCursorWrapper(cursor, self.backend)
        cursor = self._conn.execute(sql, tuple(params))
        return DBCursorWrapper(cursor, self.backend)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def connect_db(sqlite_path: Optional[str] = None) -> DBConnection:
    if mysql_enabled():
        if mysql is None:
            raise RuntimeError("mysql-connector-python não está instalado.")
        config = _mysql_config()
        database_name = config.get("database")
        if not database_name:
            raise RuntimeError("Defina MYSQL_DATABASE para usar MySQL.")
        conn = mysql.connector.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            database=database_name,
            autocommit=False,
        )
        return DBConnection(conn, "mysql", database_name=database_name)

    path = sqlite_path or "flight_tracker_browser.db"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return DBConnection(conn, "sqlite")


def ensure_column(conn: DBConnection, table: str, column: str, definition_sql: str) -> None:
    if conn.backend == "mysql":
        row = conn.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s AND column_name = %s
            LIMIT 1
            """,
            (conn.database_name, table, column),
        ).fetchone()
        if row:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition_sql}")
        conn.commit()
        return

    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if any(str(row["name"]) == column for row in rows):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition_sql}")
    conn.commit()
