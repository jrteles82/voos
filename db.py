from __future__ import annotations

import os
import re
import sqlite3 as _sqlite3
from typing import Any

Row = _sqlite3.Row
Connection = _sqlite3.Connection
Cursor = _sqlite3.Cursor
OperationalError = _sqlite3.OperationalError
IntegrityError = _sqlite3.IntegrityError


def _db_engine() -> str:
    return os.getenv("DB_ENGINE", "sqlite").strip().lower()


class _RowProxy:
    def __init__(self, values: tuple[Any, ...], columns: list[str]):
        self._values = values
        self._columns = columns
        self._index = {c: i for i, c in enumerate(columns)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._index[key]]

    def get(self, key: str, default=None):
        idx = self._index.get(key)
        if idx is None:
            return default
        return self._values[idx]


class _MySQLCursorWrapper:
    def __init__(self, cursor, row_factory):
        self._cursor = cursor
        self._row_factory = row_factory

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    def _columns(self) -> list[str]:
        if not self._cursor.description:
            return []
        return [d[0] for d in self._cursor.description]

    def _convert(self, row):
        if row is None:
            return None
        if self._row_factory is Row:
            return _RowProxy(tuple(row), self._columns())
        return row

    def fetchone(self):
        return self._convert(self._cursor.fetchone())

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [self._convert(r) for r in rows]


class _MySQLConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn
        self.row_factory = None

    def _normalize_sql(self, sql: str) -> str:
        normalized = sql
        normalized = re.sub(r"\bdatetime\('now'\)", "NOW()", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT IGNORE", normalized, flags=re.IGNORECASE)
        normalized = normalized.replace("?", "%s")

        # SQLite upsert variations used in this codebase.
        normalized = re.sub(
            r"ON\s+CONFLICT\s*\(([^\)]+)\)\s+DO\s+NOTHING",
            r"ON DUPLICATE KEY UPDATE \1=\1",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"ON\s+CONFLICT\s*\(([^\)]+)\)\s+DO\s+UPDATE\s+SET",
            "ON DUPLICATE KEY UPDATE",
            normalized,
            flags=re.IGNORECASE,
        )

        # Basic DDL compatibility for table bootstrap paths.
        normalized = re.sub(r"\bAUTOINCREMENT\b", "AUTO_INCREMENT", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTO_INCREMENT", "BIGINT PRIMARY KEY AUTO_INCREMENT", normalized, flags=re.IGNORECASE)
        return normalized

    def execute(self, sql: str, params: tuple | list | None = None):
        cur = self._conn.cursor()
        query = self._normalize_sql(sql)
        cur.execute(query, tuple(params or ()))
        return _MySQLCursorWrapper(cur, self.row_factory)

    def executemany(self, sql: str, seq_of_params):
        cur = self._conn.cursor()
        query = self._normalize_sql(sql)
        cur.executemany(query, seq_of_params)
        return _MySQLCursorWrapper(cur, self.row_factory)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()



def connect(path: str | None = None):
    if _db_engine() != "mysql":
        return _sqlite3.connect(path or "")

    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("DB_ENGINE=mysql exige pymysql instalado") from exc

    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DB", "")
    if not database:
        raise RuntimeError("Defina MYSQL_DB no .env para usar DB_ENGINE=mysql")

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.Cursor,
    )
    return _MySQLConnectionWrapper(conn)
