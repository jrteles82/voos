import asyncio
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime

from telegram import Bot

from access_policy import (
    ensure_policy_schema,
    ensure_user_access,
    get_free_uses_limit,
    is_active_access,
    should_charge_user,
)
from config import DB_PATH, TOKEN
from main import _build_user_routes, build_scan_results_image, run_scan_for_routes, filter_rows_by_max_price

POLL_SECONDS = 5
CACHE_TTL_SECONDS = 600


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_job_tables(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS scan_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id TEXT NOT NULL,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            finished_at TEXT,
            error_message TEXT
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS scan_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_key TEXT UNIQUE NOT NULL,
            image_path TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    conn.commit()


def fetch_next_job(conn):
    row = conn.execute(
        """
        SELECT *
        FROM scan_jobs
        WHERE status = 'pending'
        ORDER BY CASE WHEN job_type = 'manual_now' THEN 0 ELSE 1 END, id
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE scan_jobs SET status = 'running', started_at = datetime('now') WHERE id = ?",
        (row['id'],),
    )
    conn.commit()
    return row


def finish_job(conn, job_id: int):
    conn.execute(
        "UPDATE scan_jobs SET status = 'done', finished_at = datetime('now') WHERE id = ?",
        (job_id,),
    )
    conn.commit()


def fail_job(conn, job_id: int, error_message: str):
    conn.execute(
        "UPDATE scan_jobs SET status = 'error', finished_at = datetime('now'), error_message = ? WHERE id = ?",
        (error_message[:500], job_id),
    )
    conn.commit()


def get_user_settings(conn, user_id: int):
    row = conn.execute(
        '''
        SELECT COALESCE(max_price, 1200) AS max_price,
               COALESCE(enable_google_flights, 1) AS enable_google_flights,
               COALESCE(enable_maxmilhas, 0) AS enable_maxmilhas,
               COALESCE(last_sent_at, '') AS last_sent_at
        FROM bot_settings
        WHERE user_id = ?
        ''',
        (user_id,),
    ).fetchone()
    if row:
        return row
    return {'max_price': 1200, 'enable_google_flights': 1, 'enable_maxmilhas': 0}


def build_cache_key(user_id: int, routes, settings) -> str:
    payload = {
        'user_id': user_id,
        'routes': [
            {
                'origin': route.origin,
                'destination': route.destination,
                'outbound_date': route.outbound_date,
                'inbound_date': route.inbound_date,
                'trip_type': route.trip_type,
            }
            for route in routes
        ],
        'settings': {
            'max_price': float(settings['max_price']),
            'enable_google_flights': int(settings['enable_google_flights']),
            'enable_maxmilhas': int(settings['enable_maxmilhas']),
        },
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def get_cached_image(conn, cache_key: str):
    row = conn.execute(
        '''
        SELECT image_path, created_at
        FROM scan_cache
        WHERE cache_key = ?
        ''',
        (cache_key,),
    ).fetchone()
    if not row:
        return None
    try:
        created_at = datetime.fromisoformat(str(row['created_at']).replace(' ', 'T'))
    except ValueError:
        return None
    age = (datetime.now() - created_at).total_seconds()
    image_path = row['image_path']
    if age > CACHE_TTL_SECONDS or not os.path.exists(image_path):
        return None
    return image_path


def save_cache(conn, cache_key: str, image_path: str):
    conn.execute(
        '''
        INSERT INTO scan_cache (cache_key, image_path, created_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(cache_key) DO UPDATE SET
            image_path = excluded.image_path,
            created_at = datetime('now')
        ''',
        (cache_key, image_path),
    )
    conn.commit()


def send_photo(bot: Bot, chat_id: str, image_path: str):
    with open(image_path, 'rb') as image_file:
        asyncio.run(bot.send_photo(chat_id=chat_id, photo=image_file))


def mark_sent(conn, user_id: int):
    conn.execute(
        "UPDATE bot_settings SET last_sent_at = datetime('now'), updated_at = datetime('now') WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()


def process_job(conn, bot: Bot, job):
    user_id = int(job['user_id'])
    chat_id = str(job['chat_id'])
    settings = get_user_settings(conn, user_id)
    routes = _build_user_routes(conn, user_id)
    if not routes:
        raise RuntimeError('Usuário sem rotas ativas')

    access = ensure_user_access(conn, chat_id)
    charge_now = should_charge_user(conn, chat_id, access) and not is_active_access(access)
    if charge_now:
        free_uses = int(access['free_uses'] or 0)
        free_uses_limit = get_free_uses_limit(conn)
        if free_uses >= free_uses_limit:
            raise RuntimeError('bloqueado_por_monetizacao')

    cache_key = build_cache_key(user_id, routes, settings)
    cached_image = get_cached_image(conn, cache_key)
    if cached_image:
        send_photo(bot, chat_id, cached_image)
        mark_sent(conn, user_id)
        if charge_now:
            conn.execute(
                "UPDATE user_access SET free_uses = free_uses + 1, updated_at = datetime('now') WHERE chat_id = ?",
                (chat_id,)
            )
            conn.commit()
        return

    parsed = run_scan_for_routes(
        routes,
        sources={
            'google_flights': bool(settings['enable_google_flights']),
            'maxmilhas': bool(settings['enable_maxmilhas']),
        },
    )
    filtered = filter_rows_by_max_price(parsed, float(settings['max_price']))
    if not filtered:
        mensagem = '⚠️ Encontrei resultados, mas todos ficaram acima do valor máximo do seu filtro.'
        asyncio.run(bot.send_message(chat_id=chat_id, text=mensagem))
        if charge_now:
            conn.execute(
                "UPDATE user_access SET free_uses = free_uses + 1, updated_at = datetime('now') WHERE chat_id = ?",
                (chat_id,)
            )
            conn.commit()
        raise RuntimeError('Consulta sem resultados filtrados')

    image_path = build_scan_results_image(filtered)
    if not image_path:
        raise RuntimeError('Falha ao gerar print da consulta')

    save_cache(conn, cache_key, image_path)
    send_photo(bot, chat_id, image_path)
    mark_sent(conn, user_id)
    if charge_now:
        conn.execute(
            "UPDATE user_access SET free_uses = free_uses + 1, updated_at = datetime('now') WHERE chat_id = ?",
            (chat_id,)
        )
        conn.commit()


def main():
    if not TOKEN:
        raise SystemExit('Defina TELEGRAM_BOT_TOKEN no .env')

    bot = Bot(token=TOKEN)
    while True:
        conn = get_db()
        try:
            ensure_policy_schema(conn)
            ensure_job_tables(conn)
            job = fetch_next_job(conn)
            if not job:
                time.sleep(POLL_SECONDS)
                continue
            try:
                process_job(conn, bot, job)
                finish_job(conn, int(job['id']))
            except Exception as exc:
                fail_job(conn, int(job['id']), str(exc))
        finally:
            conn.close()


if __name__ == '__main__':
    main()
