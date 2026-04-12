import asyncio
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from telegram import Bot

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / '.env'
DB_PATH = BASE_DIR / 'flight_tracker_browser.db'
CHECK_INTERVAL_SECONDS = 20
OWNER_TELEGRAM_ID = '1748352987'


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
MP_ACCESS_TOKEN = os.getenv('MP_ACCESS_TOKEN', '').strip()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_mp_payment(payment_id: str) -> dict:
    headers = {
        'Authorization': f'Bearer {MP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
    }
    response = requests.get(f'https://api.mercadopago.com/v1/payments/{payment_id}', headers=headers, timeout=30)
    data = response.json()
    if response.status_code >= 400:
        raise RuntimeError(data.get('message') or 'Erro ao consultar pagamento Pix')
    return data


def plan_days(plan_name: str) -> int:
    return {
        'Semanal': 7,
        'Quinzenal': 15,
        'Mensal': 30,
        'Teste Admin': 7,
    }.get(plan_name, 7)


def add_days_to_expiration(current_expiration: str | None, days: int) -> str:
    base = datetime.now()
    if current_expiration:
        try:
            parsed = datetime.fromisoformat(current_expiration)
            if parsed > base:
                base = parsed
        except ValueError:
            pass
    return (base + timedelta(days=days)).replace(microsecond=0).isoformat(sep=' ')


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


def apply_approved_payment(conn, payment_id: str) -> tuple[bool, str]:
    row = conn.execute(
        'SELECT mp_payment_id, chat_id, plan_name, amount, status FROM payments WHERE mp_payment_id = ?',
        (payment_id,)
    ).fetchone()
    if not row:
        return False, 'pagamento_nao_encontrado'

    payment = get_mp_payment(payment_id)
    status = payment.get('status', row['status'])
    approved_at = payment.get('date_approved')
    conn.execute(
        'UPDATE payments SET status = ?, approved_at = COALESCE(?, approved_at) WHERE mp_payment_id = ?',
        (status, approved_at, payment_id)
    )

    if status != 'approved':
        conn.commit()
        return False, status

    chat_id = str(row['chat_id'])
    plan_name = row['plan_name'] or 'Teste Admin'
    access = ensure_user_access(conn, chat_id)
    expires_at = add_days_to_expiration(access['expires_at'], plan_days(plan_name))
    conn.execute(
        '''
        UPDATE user_access
        SET status = ?, expires_at = ?, free_uses = 0, total_paid = COALESCE(total_paid, 0) + ?, updated_at = datetime('now')
        WHERE chat_id = ?
        ''',
        ('active', expires_at, float(row['amount'] or 0), chat_id)
    )
    conn.commit()
    return True, expires_at


def pending_payments(conn):
    return conn.execute(
        "SELECT mp_payment_id, chat_id FROM payments WHERE status = 'pending' ORDER BY created_at ASC LIMIT 20"
    ).fetchall()


def main():
    if not TOKEN or not MP_ACCESS_TOKEN:
        raise SystemExit('Defina TELEGRAM_BOT_TOKEN e MP_ACCESS_TOKEN no .env')

    bot = Bot(token=TOKEN)
    notified = set()

    while True:
        conn = get_db()
        try:
            for row in pending_payments(conn):
                payment_id = str(row['mp_payment_id'])
                chat_id = str(row['chat_id'])
                try:
                    approved, info = apply_approved_payment(conn, payment_id)
                    if approved and payment_id not in notified:
                        asyncio.run(bot.send_message(chat_id=chat_id, text=f'🎉 Pagamento aprovado automaticamente! Acesso liberado até {info}.'))
                        notified.add(payment_id)
                except Exception as exc:
                    print(f'[payment-monitor] erro no pagamento {payment_id}: {exc}')
        finally:
            conn.close()

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == '__main__':
    main()
