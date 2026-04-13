#!/usr/bin/env python3
"""Migrate SQLite schema/data to MySQL.

Usage examples:
  python3 scripts/migrate_sqlite_to_mysql.py \
      --sqlite flight_tracker_browser.db \
      --mysql-db skyscanner_bot \
      --output mysql_migration.sql

  python3 scripts/migrate_sqlite_to_mysql.py \
      --sqlite flight_tracker_browser.db \
      --mysql-host 127.0.0.1 --mysql-port 3306 \
      --mysql-user root --mysql-password 'secret' \
      --mysql-db skyscanner_bot --apply
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable


@dataclass
class ColumnDef:
    name: str
    sqlite_type: str
    notnull: bool
    default_value: str | None
    pk_position: int


def map_sqlite_type_to_mysql(sqlite_type: str) -> str:
    t = (sqlite_type or "").strip().upper()
    if "INT" in t:
        return "BIGINT"
    if any(x in t for x in ["REAL", "FLOA", "DOUB"]):
        return "DOUBLE"
    if "BLOB" in t:
        return "LONGBLOB"
    if any(x in t for x in ["CHAR", "CLOB", "TEXT"]):
        return "TEXT"
    if "DATE" in t and "TIME" in t:
        return "DATETIME"
    if "DATE" in t:
        return "DATE"
    if "TIME" in t:
        return "TIME"
    if "NUM" in t or "DEC" in t:
        return "DECIMAL(20,6)"
    return "TEXT"


def mysql_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return "0x" + value.hex()

    txt = str(value)
    txt = txt.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{txt}'"


def normalize_default(default_value: str | None) -> str | None:
    if default_value is None:
        return None
    raw = str(default_value).strip()
    if not raw:
        return None

    upper = raw.upper()
    if upper in {"CURRENT_TIMESTAMP", "(CURRENT_TIMESTAMP)"}:
        return "CURRENT_TIMESTAMP"

    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        raw = raw[1:-1]
    return mysql_literal(raw)


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [r[0] for r in rows]


def read_columns(conn: sqlite3.Connection, table: str) -> list[ColumnDef]:
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return [
        ColumnDef(
            name=r[1],
            sqlite_type=r[2] or "",
            notnull=bool(r[3]),
            default_value=r[4],
            pk_position=int(r[5] or 0),
        )
        for r in rows
    ]


def read_unique_indexes(conn: sqlite3.Connection, table: str) -> list[tuple[str, list[str]]]:
    uniques: list[tuple[str, list[str]]] = []
    for idx in conn.execute(f"PRAGMA index_list('{table}')").fetchall():
        idx_name = idx[1]
        is_unique = bool(idx[2])
        origin = idx[3] if len(idx) > 3 else ""
        if not is_unique:
            continue
        if origin == "pk":
            continue
        cols = [r[2] for r in conn.execute(f"PRAGMA index_info('{idx_name}')").fetchall()]
        if cols:
            uniques.append((idx_name, cols))
    return uniques


def read_foreign_keys(conn: sqlite3.Connection, table: str) -> list[dict]:
    fks = []
    for row in conn.execute(f"PRAGMA foreign_key_list('{table}')").fetchall():
        # (id, seq, table, from, to, on_update, on_delete, match)
        fks.append(
            {
                "id": row[0],
                "ref_table": row[2],
                "from_col": row[3],
                "to_col": row[4],
                "on_update": row[5],
                "on_delete": row[6],
            }
        )
    return fks


def table_ddl(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if not row or not row[0]:
        return ""
    return str(row[0])


def has_autoincrement_pk(conn: sqlite3.Connection, table: str) -> bool:
    ddl = table_ddl(conn, table).upper()
    return "AUTOINCREMENT" in ddl


def build_create_table_sql(conn: sqlite3.Connection, table: str) -> str:
    cols = read_columns(conn, table)
    unique_indexes = read_unique_indexes(conn, table)
    fk_rows = read_foreign_keys(conn, table)
    has_autoinc = has_autoincrement_pk(conn, table)

    pk_cols = [c.name for c in cols if c.pk_position > 0]
    if len(pk_cols) > 1:
        pk_cols = [name for _, name in sorted((c.pk_position, c.name) for c in cols if c.pk_position > 0)]

    indexed_cols = set(pk_cols)
    for _, idx_cols in unique_indexes:
        indexed_cols.update(idx_cols)
    indexed_cols.update(str(fk["from_col"]) for fk in fk_rows)

    lines: list[str] = []
    for col in cols:
        mysql_type = map_sqlite_type_to_mysql(col.sqlite_type)
        if mysql_type == "TEXT" and col.name in indexed_cols:
            mysql_type = "VARCHAR(255)"

        is_single_pk_int = (
            len(pk_cols) == 1
            and pk_cols[0] == col.name
            and "INT" in (col.sqlite_type or "").upper()
            and has_autoinc
        )

        pieces = [f"`{col.name}`", mysql_type]
        if is_single_pk_int:
            pieces = [f"`{col.name}`", "BIGINT", "NOT NULL", "AUTO_INCREMENT"]
        else:
            if col.notnull:
                pieces.append("NOT NULL")
            default_sql = normalize_default(col.default_value)
            if default_sql is not None:
                pieces.append(f"DEFAULT {default_sql}")
        lines.append(" ".join(pieces))

    if pk_cols:
        pk_expr = ", ".join(f"`{c}`" for c in pk_cols)
        lines.append(f"PRIMARY KEY ({pk_expr})")

    for idx_name, cols_for_idx in unique_indexes:
        uniq_expr = ", ".join(f"`{c}`" for c in cols_for_idx)
        safe_name = idx_name.replace("`", "")
        lines.append(f"UNIQUE KEY `{safe_name}` ({uniq_expr})")

    fk_by_id: dict[int, list[dict]] = {}
    for fk in fk_rows:
        fk_by_id.setdefault(int(fk["id"]), []).append(fk)

    for fk_id, parts in fk_by_id.items():
        from_cols = ", ".join(f"`{p['from_col']}`" for p in parts)
        ref_table = parts[0]["ref_table"]
        to_cols = ", ".join(f"`{p['to_col']}`" for p in parts)
        on_update = parts[0].get("on_update") or "NO ACTION"
        on_delete = parts[0].get("on_delete") or "NO ACTION"
        lines.append(
            f"CONSTRAINT `fk_{table}_{fk_id}` FOREIGN KEY ({from_cols}) "
            f"REFERENCES `{ref_table}` ({to_cols}) "
            f"ON UPDATE {on_update} ON DELETE {on_delete}"
        )

    body = ",\n  ".join(lines)
    return f"CREATE TABLE `{table}` (\n  {body}\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"


def chunked_rows(conn: sqlite3.Connection, table: str, chunk_size: int = 500) -> Iterable[list[sqlite3.Row]]:
    cur = conn.execute(f"SELECT * FROM '{table}'")
    while True:
        rows = cur.fetchmany(chunk_size)
        if not rows:
            return
        yield rows


def build_insert_sql(conn: sqlite3.Connection, table: str) -> list[str]:
    conn.row_factory = sqlite3.Row
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]
    if not cols:
        return []

    col_expr = ", ".join(f"`{c}`" for c in cols)
    stmts: list[str] = []
    for chunk in chunked_rows(conn, table):
        values_sql = []
        for row in chunk:
            values = ", ".join(mysql_literal(row[c]) for c in cols)
            values_sql.append(f"({values})")
        stmts.append(f"INSERT INTO `{table}` ({col_expr}) VALUES\n  " + ",\n  ".join(values_sql) + ";")
    return stmts


def generate_sql(sqlite_path: Path, mysql_db: str) -> str:
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    try:
        tables = list_tables(conn)
        parts: list[str] = [
            "-- Generated by scripts/migrate_sqlite_to_mysql.py",
            "SET NAMES utf8mb4;",
            "SET FOREIGN_KEY_CHECKS=0;",
            f"CREATE DATABASE IF NOT EXISTS `{mysql_db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
            f"USE `{mysql_db}`;",
            "",
        ]

        for table in tables:
            parts.append(f"DROP TABLE IF EXISTS `{table}`;")
            parts.append(build_create_table_sql(conn, table))
            parts.append("")

        for table in tables:
            insert_stmts = build_insert_sql(conn, table)
            if insert_stmts:
                parts.append(f"-- Data for `{table}`")
                parts.extend(insert_stmts)
                parts.append("")

        parts.append("SET FOREIGN_KEY_CHECKS=1;")
        return "\n".join(parts)
    finally:
        conn.close()


def apply_sql_directly(sql_text: str, host: str, port: int, user: str, password: str, database: str) -> None:
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("pymysql não instalado. Rode: pip install pymysql") from exc

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=True,
        client_flag=0,
    )
    try:
        with conn.cursor() as cur:
            for statement in sql_text.split(";\n"):
                stmt = statement.strip()
                if not stmt:
                    continue
                cur.execute(stmt)
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate SQLite DB to MySQL")
    parser.add_argument("--sqlite", required=True, help="Path to SQLite database file")
    parser.add_argument("--mysql-db", required=True, help="Target MySQL database name")
    parser.add_argument("--output", default="mysql_migration.sql", help="Output SQL file path")

    parser.add_argument("--apply", action="store_true", help="Apply directly to MySQL")
    parser.add_argument("--mysql-host", default="127.0.0.1")
    parser.add_argument("--mysql-port", type=int, default=3306)
    parser.add_argument("--mysql-user", default="root")
    parser.add_argument("--mysql-password", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite DB não encontrado: {sqlite_path}")

    sql_text = generate_sql(sqlite_path=sqlite_path, mysql_db=args.mysql_db)
    output_path = Path(args.output)
    output_path.write_text(sql_text, encoding="utf-8")
    print(f"SQL de migração gerado em: {output_path}")

    if args.apply:
        apply_sql_directly(
            sql_text=sql_text,
            host=args.mysql_host,
            port=args.mysql_port,
            user=args.mysql_user,
            password=args.mysql_password,
            database=args.mysql_db,
        )
        print("Migração aplicada no MySQL com sucesso.")


if __name__ == "__main__":
    main()
