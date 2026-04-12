import asyncio
import os
import sqlite3
import time
from datetime import datetime

from telegram import Bot

from config import DB_PATH, FREE_USES_LIMIT, OWNER_TELEGRAM_ID, TOKEN
from main import _build_user_routes, build_scan_results_image, run_scan_for_routes, filter_rows_by_max_price

INTERVAL_SECONDS = 1800
SEND_COOLDOWN_SECONDS = 1700


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_user_access(conn, chat_id: str):
    conn.execute(
        '''
        INSERT OR IGNORE INTO user_access (chat_id, status, free_uses, test_charge, total_paid, updated_at)
        VALUES (?, 'free', 0, 0, 0, datetime('now'))
        ''',
        (chat_id,)
    )
    conn.commit()
    return conn.execute('SELECT * FROM user_access WHERE chat_id = ?', (chat_id,)).fetchone()


def get_monetization_settings(conn):
    row = conn.execute('SELECT * FROM monetization_settings WHERE id = 1').fetchone()
    return row


def should_charge_user(conn, chat_id: str, access_row) -> bool:
    settings = get_monetization_settings(conn)
    if not settings:
        return False
    if chat_id == OWNER_TELEGRAM_ID:
        return bool(int(settings['charge_admin_only']) or int(access_row['test_charge'] or 0) or int(settings['charge_global']))
    if int(settings['charge_admin_only']) == 1:
        return False
    return bool(int(settings['charge_global']) or int(access_row['test_charge'] or 0))


def is_active_access(access_row) -> bool:
    if not access_row:
        return False
    if (access_row['status'] or '') != 'active':
        return False
    expires_at = (access_row['expires_at'] or '').strip()
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) > datetime.now()
    except ValueError:
        return False


def iter_users(conn):
    return conn.execute(
        '''
        SELECT bu.user_id, bu.chat_id,
               COALESCE(bs.max_price, 1200) AS max_price,
               COALESCE(bs.enable_google_flights, 1) AS enable_google_flights,
               COALESCE(bs.enable_maxmilhas, 0) AS enable_maxmilhas,
               COALESCE(bs.last_sent_at, '') AS last_sent_at
        FROM bot_users bu
        LEFT JOIN bot_settings bs ON bs.user_id = bu.user_id
        WHERE bu.confirmed = 1
        ORDER BY bu.user_id
        '''
    ).fetchall()


def was_sent_recently(last_sent_at: str, window_seconds: int = SEND_COOLDOWN_SECONDS) -> bool:
    if not last_sent_at:
        return False
    try:
        dt = datetime.fromisoformat(last_sent_at.replace(' ', 'T'))
    except ValueError:
        return False
    delta_seconds = (datetime.now() - dt).total_seconds()
    if delta_seconds < -60:
        return False
    return delta_seconds < window_seconds


def mark_sent(conn, user_id: int):
    now_txt = datetime.now().isoformat(sep=' ', timespec='seconds')
    conn.execute(
        "UPDATE bot_settings SET last_sent_at = ?, updated_at = ? WHERE user_id = ?",
        (now_txt, now_txt, user_id),
    )
    conn.commit()


def run_for_user(conn, bot: Bot, user_id: int, chat_id: str, max_price: float, sources: dict) -> tuple[bool, str]:
    access = ensure_user_access(conn, chat_id)
    charge_now = should_charge_user(conn, chat_id, access) and not is_active_access(access)
    if charge_now:
        free_uses = int(access['free_uses'] or 0)
        if free_uses >= FREE_USES_LIMIT:
            return False, 'bloqueado_por_monetizacao'

    routes = _build_user_routes(conn, user_id)
    if not routes:
        return False, 'sem_rotas_ativas'

    parsed = run_scan_for_routes(routes, sources=sources)
    filtered = filter_rows_by_max_price(parsed, max_price)
    if not filtered:
        asyncio.run(bot.send_message(chat_id=chat_id, text='⚠️ Encontrei resultados, mas todos ficaram acima do valor máximo do seu filtro.'))
        if charge_now:
            conn.execute(
                "UPDATE user_access SET free_uses = free_uses + 1, updated_at = datetime('now') WHERE chat_id = ?",
                (chat_id,)
            )
            conn.commit()
        return False, 'sem_resultado_no_limite'

    image_path = build_scan_results_image(filtered)
    if not image_path:
        return False, 'sem_imagem'

    try:
        with open(image_path, 'rb') as image_file:
            asyncio.run(bot.send_photo(chat_id=chat_id, photo=image_file))
        if charge_now:
            conn.execute(
                "UPDATE user_access SET free_uses = free_uses + 1, updated_at = datetime('now') WHERE chat_id = ?",
                (chat_id,)
            )
            conn.commit()
        return True, 'enviado'
    finally:
        try:
            os.remove(image_path)
        except OSError:
            pass


def sleep_until_next_slot(interval_seconds: int):
    now = time.time()
    next_slot = ((int(now) // interval_seconds) + 1) * interval_seconds
    time.sleep(max(1, next_slot - now))


def main():
    if not TOKEN:
        raise SystemExit('Defina TELEGRAM_BOT_TOKEN no .env')

    bot = Bot(token=TOKEN)
    while True:
        conn = get_db()
        try:
            users = iter_users(conn)
            for user in users:
                try:
                    if was_sent_recently(str(user['last_sent_at'])):
                        print(
                            f"[bot-scheduler] user {user['user_id']} ignorado: cooldown ativo "
                            f"(last_sent_at={user['last_sent_at']})"
                        )
                        continue
                    sent, reason = run_for_user(
                        conn,
                        bot,
                        int(user['user_id']),
                        str(user['chat_id']),
                        float(user['max_price']),
                        {
                            'google_flights': bool(user['enable_google_flights']),
                            'maxmilhas': bool(user['enable_maxmilhas']),
                        },
                    )
                    if sent:
                        mark_sent(conn, int(user['user_id']))
                        print(f"[bot-scheduler] user {user['user_id']} envio concluído")
                    else:
                        print(f"[bot-scheduler] user {user['user_id']} sem envio: {reason}")
                except Exception as exc:
                    print(f'[bot-scheduler] erro no user {user["user_id"]}: {exc}')
        finally:
            conn.close()

        print(f'[bot-scheduler] ciclo concluído em {datetime.now().isoformat()}, aguardando próximo slot de 30 min')
        sleep_until_next_slot(INTERVAL_SECONDS)


if __name__ == '__main__':
    main()
