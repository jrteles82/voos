import asyncio
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from telegram import Bot

from main import _build_user_routes, build_scan_results_image, run_scan_for_routes, filter_rows_by_max_price

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / '.env'
DB_PATH = BASE_DIR / 'flight_tracker_browser.db'
INTERVAL_SECONDS = 180


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env(ENV_PATH)
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def was_sent_recently(last_sent_at: str, window_seconds: int = 600) -> bool:
    if not last_sent_at:
        return False
    try:
        dt = datetime.fromisoformat(last_sent_at.replace(' ', 'T'))
    except ValueError:
        return False
    return (datetime.now() - dt).total_seconds() < window_seconds


def mark_sent(conn, user_id: int):
    conn.execute(
        "UPDATE bot_settings SET last_sent_at = datetime('now'), updated_at = datetime('now') WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()


def run_for_user(conn, bot: Bot, user_id: int, chat_id: str, max_price: float, sources: dict):
    routes = _build_user_routes(conn, user_id)
    if not routes:
        return False

    parsed = run_scan_for_routes(routes, sources=sources)
    filtered = filter_rows_by_max_price(parsed, max_price)
    if not filtered:
        return False

    image_path = build_scan_results_image(filtered)
    if not image_path:
        return False

    try:
        with open(image_path, 'rb') as image_file:
            asyncio.run(bot.send_photo(chat_id=chat_id, photo=image_file))
        return True
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
                        continue
                    sent = run_for_user(
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
                except Exception as exc:
                    print(f'[bot-scheduler] erro no user {user["user_id"]}: {exc}')
        finally:
            conn.close()

        print(f'[bot-scheduler] ciclo concluído em {datetime.now().isoformat()}, aguardando próximo slot de 3 min')
        sleep_until_next_slot(INTERVAL_SECONDS)


if __name__ == '__main__':
    main()
