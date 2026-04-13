from __future__ import annotations
import os
import db as sqlite3
from datetime import datetime, timedelta

import requests
from flask import Flask, request
from telegram import Bot

from config import DB_PATH, MP_ACCESS_TOKEN, TOKEN, MERCADOPAGO_API_BASE_URL, now_local

PORT = int(os.getenv('PAYMENT_WEBHOOK_PORT', '8787'))
app = Flask(__name__)
bot = Bot(token=TOKEN) if TOKEN else None


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_mp_payment(payment_id: str) -> dict:
    headers = {
        'Authorization': f'Bearer {MP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
    }
    response = requests.get(f'{MERCADOPAGO_API_BASE_URL}/v1/payments/{payment_id}', headers=headers, timeout=30)
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
    base = now_local()
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


def apply_approved_payment(conn, payment_id: str):
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
        return False, status, str(row['chat_id'])

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
    return True, expires_at, chat_id


@app.post('/webhook')
def webhook():
    data = request.get_json(silent=True) or {}
    payment_id = None
    if isinstance(data.get('data'), dict):
        payment_id = data['data'].get('id')
    if not payment_id and data.get('resource'):
        payment_id = str(data['resource']).rstrip('/').split('/')[-1]
    if not payment_id:
        return 'OK', 200

    conn = get_db()
    try:
        approved, info, chat_id = apply_approved_payment(conn, str(payment_id))
        if approved and bot:
            bot.send_message(chat_id=chat_id, text=f'🎉 Pagamento aprovado automaticamente! Acesso liberado até {info}.')
    finally:
        conn.close()
    return 'OK', 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT)
