import sqlite3
import uuid
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, ForceReply
from telegram_bot_calendar import DetailedTelegramCalendar, LSTEP
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from config import (
    DB_PATH,
    PANEL_TEXT,
    TOKEN,
    MP_ACCESS_TOKEN,
    MERCADOPAGO_API_BASE_URL,
    now_local,
)
from access_policy import (
    ensure_policy_schema,
    get_monetization_settings as ap_get_monetization_settings,
    ensure_user_access as ap_ensure_user_access,
    is_active_access as ap_is_active_access,
    should_charge_user as ap_should_charge_user,
    is_admin_chat,
    list_active_admin_chat_ids,
    get_free_uses_limit,
    get_max_routes_default,
    get_pix_pending_expiration_hours,
    list_airports,
    get_airport_labels,
)


ASK_ORIGIN, ASK_DESTINATION, ASK_OUTBOUND, ASK_LIMIT = range(4)

def get_panel_text(chat_id: str) -> str:
    conn = get_db()
    row = get_bot_user_by_chat(conn, chat_id)
    if not row:
        conn.close()
        return PANEL_TEXT
    
    cur = conn.execute('SELECT COUNT(*) FROM user_routes WHERE user_id = ? AND active = 1', (row['user_id'],))
    routes_count = cur.fetchone()[0]
    conn.close()
    
    msg_text = PANEL_TEXT
    if routes_count == 0:
        msg_text += "\n\n⚠️ *Atenção:* Você ainda não tem nenhuma rota cadastrada.\nClique em *➕ Adicionar nova rota* abaixo para começar."
    return msg_text


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_date(raw: str) -> str:
    raw = raw.strip()
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%Y/%m/%d'):
        try:
            return datetime.strptime(raw, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    raise ValueError('Formato inválido, use DD/MM/AAAA, DD-MM-AAAA, YYYY/MM/DD ou YYYY-MM-DD')


def format_date_br(raw: str) -> str:
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return raw


def format_money_br(value: float) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _load_airport_labels() -> dict[str, str]:
    conn = get_db()
    try:
        return get_airport_labels(conn)
    finally:
        conn.close()


def airport_label(code: str) -> str:
    labels = _load_airport_labels()
    return labels.get((code or "").upper(), code)


def ensure_bot_tables() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS bot_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            chat_id TEXT UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            confirmed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    try:
        cur.execute("ALTER TABLE bot_users ADD COLUMN confirmed INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS bot_settings (
            user_id INTEGER PRIMARY KEY,
            max_price REAL DEFAULT 1200,
            enable_google_flights INTEGER DEFAULT 1,
            enable_maxmilhas INTEGER DEFAULT 0,
            last_sent_at TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    cur.execute(
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
    cur.execute(
        '''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mp_payment_id TEXT UNIQUE,
            chat_id TEXT NOT NULL,
            plan_name TEXT,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            qr_code TEXT,
            ticket_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            approved_at TEXT
        )
        '''
    )
    ensure_policy_schema(conn)
    for ddl in [
        "ALTER TABLE bot_settings ADD COLUMN enable_google_flights INTEGER DEFAULT 1",
        "ALTER TABLE bot_settings ADD COLUMN enable_maxmilhas INTEGER DEFAULT 0",
        "ALTER TABLE bot_settings ADD COLUMN last_sent_at TEXT",
    ]:
        try:
            cur.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def get_monetization_settings(conn):
    return ap_get_monetization_settings(conn)


def ensure_user_access(conn, chat_id: str):
    return ap_ensure_user_access(conn, chat_id)


def ensure_owner_test_access(conn):
    settings = get_monetization_settings(conn)
    desired_test = 1 if int(settings['test_mode']) == 1 else 0
    admin_chat_ids = list_active_admin_chat_ids(conn)
    for admin_chat_id in admin_chat_ids:
        access = ensure_user_access(conn, admin_chat_id)
        if int(access['test_charge'] or 0) == desired_test:
            continue
        conn.execute(
            "UPDATE user_access SET test_charge = ?, updated_at = datetime('now') WHERE chat_id = ?",
            (desired_test, admin_chat_id),
        )
        conn.commit()


def should_charge_user(conn, chat_id: str, access_row) -> bool:
    return ap_should_charge_user(conn, chat_id, access_row)


def plan_catalog(settings_row):
    return [
        ('Semanal', float(settings_row['weekly_price']), 7),
        ('Quinzenal', float(settings_row['biweekly_price']), 15),
        ('Mensal', float(settings_row['monthly_price']), 30),
    ]


def plan_amount_by_name(settings_row, plan_name: str) -> float:
    mapping = {
        'Semanal': float(settings_row['weekly_price']),
        'Quinzenal': float(settings_row['biweekly_price']),
        'Mensal': float(settings_row['monthly_price']),
        'Teste Admin': 1.0,
    }
    return float(mapping.get(plan_name, 1.0))


def plan_days(plan_name: str) -> int:
    mapping = {
        'Semanal': 7,
        'Quinzenal': 15,
        'Mensal': 30,
        'Teste Admin': 7,
    }
    return int(mapping.get(plan_name, 7))


def offer_paid_plans_text(conn, chat_id: str) -> str:
    access = ensure_user_access(conn, chat_id)
    settings = get_monetization_settings(conn)
    free_uses_limit = get_free_uses_limit(conn)
    block_text = '⏰ Seu acesso venceu. Escolha um plano para renovar.' if (access['status'] or '') == 'expired' else f'🚫 Seus {free_uses_limit} usos grátis acabaram.'
    weekly, biweekly, monthly = plan_catalog(settings)
    return (
        f"{block_text}\n\n"
        "💰 *Escolha um plano para continuar*\n\n"
        f"🥉 {weekly[0]}: R$ {format_money_br(weekly[1])} ({weekly[2]} dias)\n"
        f"🥈 {biweekly[0]}: R$ {format_money_br(biweekly[1])} ({biweekly[2]} dias)\n"
        f"🥇 {monthly[0]}: R$ {format_money_br(monthly[1])} ({monthly[2]} dias)"
    )


def user_payments_markup(rows) -> InlineKeyboardMarkup:
    keyboard = []
    for row in rows:
        label = f"{row['plan_name'] or '-'} | R$ {format_money_br(row['amount'])} | {row['status']}"
        keyboard.append([InlineKeyboardButton(label[:60], callback_data=f"payment:view:{row['mp_payment_id']}")])
        if row['status'] == 'pending':
            keyboard.append([InlineKeyboardButton('✅ Atualizar este pagamento', callback_data=f"payment:check:{row['mp_payment_id']}")])
    keyboard.append([InlineKeyboardButton('⬅️ Voltar ao menu', callback_data='menu:back')])
    return InlineKeyboardMarkup(keyboard)


def is_active_access(access_row) -> bool:
    return ap_is_active_access(access_row)


def get_valid_pending_payment(conn, chat_id: str):
    row = conn.execute(
        '''
        SELECT mp_payment_id, plan_name, amount, status, qr_code, ticket_url, created_at
        FROM payments
        WHERE chat_id = ? AND status = 'pending'
        ORDER BY created_at DESC
        LIMIT 1
        ''',
        (chat_id,)
    ).fetchone()
    if not row:
        return None
    created_at = (row['created_at'] or '').strip()
    if not created_at:
        return None
    try:
        created_dt = datetime.fromisoformat(created_at.replace(' ', 'T'))
    except ValueError:
        return None
    pix_pending_expiration_hours = get_pix_pending_expiration_hours(conn)
    if now_local() - created_dt > __import__('datetime').timedelta(hours=pix_pending_expiration_hours):
        return None
    return row


def ensure_app_user(conn, first_name: str) -> int:
    row = conn.execute(
        "SELECT id FROM users WHERE email = ?",
        (f"telegram:{first_name.lower()}@local",),
    ).fetchone()
    if row:
        return int(row['id'])

    cur = conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, datetime('now'))",
        (f"telegram:{first_name.lower()}@local", 'telegram-bot'),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_bot_user_by_chat(conn, chat_id: str):
    return conn.execute(
        "SELECT user_id, confirmed, first_name FROM bot_users WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()


def create_mp_pix_payment(chat_id: str, plan_name: str, amount: float) -> dict:
    if not MP_ACCESS_TOKEN:
        raise RuntimeError('MP_ACCESS_TOKEN não configurado no .env')

    headers = {
        'Authorization': f'Bearer {MP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
        'X-Idempotency-Key': str(uuid.uuid4()),
    }
    payload = {
        'transaction_amount': float(amount),
        'description': f'Plano {plan_name}',
        'payment_method_id': 'pix',
        'external_reference': f'{chat_id}:{plan_name}:{int(now_local().timestamp())}',
        'payer': {
            'email': f'admin{chat_id}@gmail.com'
        }
    }
    response = requests.post(f'{MERCADOPAGO_API_BASE_URL}/v1/payments', headers=headers, json=payload, timeout=30)
    data = response.json()
    if response.status_code >= 400:
        raise RuntimeError(data.get('message') or 'Erro ao gerar pagamento Pix')
    return data


def save_payment(conn, mp_payment_id: str, chat_id: str, plan_name: str, amount: float, status: str, qr_code: str, ticket_url: str):
    conn.execute(
        '''
        INSERT OR REPLACE INTO payments (mp_payment_id, chat_id, plan_name, amount, status, qr_code, ticket_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ''',
        (mp_payment_id, chat_id, plan_name, amount, status, qr_code, ticket_url),
    )
    conn.commit()


def get_mp_payment(payment_id: str) -> dict:
    if not MP_ACCESS_TOKEN:
        raise RuntimeError('MP_ACCESS_TOKEN não configurado no .env')
    headers = {
        'Authorization': f'Bearer {MP_ACCESS_TOKEN}',
        'Content-Type': 'application/json',
    }
    response = requests.get(f'{MERCADOPAGO_API_BASE_URL}/v1/payments/{payment_id}', headers=headers, timeout=30)
    data = response.json()
    if response.status_code >= 400:
        raise RuntimeError(data.get('message') or 'Erro ao consultar pagamento Pix')
    return data


def add_days_to_expiration(current_expiration: str | None, days: int) -> str:
    base = now_local()
    if current_expiration:
        try:
            parsed = datetime.fromisoformat(current_expiration)
            if parsed > base:
                base = parsed
        except ValueError:
            pass
    return (base + __import__('datetime').timedelta(days=days)).replace(microsecond=0).isoformat(sep=' ')


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


def get_user_id_by_chat(conn, chat_id: str):
    row = get_bot_user_by_chat(conn, chat_id)
    if not row:
        return None
    return int(row['user_id'])


def is_confirmed(conn, chat_id: str) -> bool:
    row = get_bot_user_by_chat(conn, chat_id)
    return bool(row and int(row['confirmed']) == 1)


def ensure_user_settings(conn, user_id: int) -> None:
    conn.execute(
        '''
        INSERT INTO bot_settings (user_id, max_price, enable_google_flights, enable_maxmilhas)
        VALUES (?, 1200, 1, 0)
        ON CONFLICT(user_id) DO NOTHING
        ''',
        (user_id,),
    )
    conn.commit()


def get_user_settings(conn, user_id: int):
    ensure_user_settings(conn, user_id)
    return conn.execute(
        'SELECT max_price, enable_google_flights, enable_maxmilhas FROM bot_settings WHERE user_id = ?',
        (user_id,),
    ).fetchone()


def require_confirmation(conn, chat_id: str):
    row = get_bot_user_by_chat(conn, chat_id)
    if not row:
        return '⚠️ Use /start para iniciar seu cadastro.'
    if int(row['confirmed']) != 1:
        return '⚠️ Confirme seu cadastro primeiro para liberar as funções. Use o botão em /start.'
    return None


def start_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Confirmar cadastro', callback_data='confirm:cadastro')],
        [InlineKeyboardButton('ℹ️ Como funciona', callback_data='menu:manual')],
    ])


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('⬅️ Voltar ao menu', callback_data='menu:back')],
    ])


def cancel_markup(callback_data: str, label: str = '❌ Cancelar') -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=callback_data)]])


def force_reply_markup(placeholder: str) -> ForceReply:
    return ForceReply(selective=False, input_field_placeholder=placeholder)


def full_menu_markup(chat_id: str | None = None) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton('➕ Adicionar nova rota', callback_data='menu:addrota')],
        [InlineKeyboardButton('➖ Remover rota cadastrada', callback_data='menu:removerrota')],
        [InlineKeyboardButton('📋 Ver minhas rotas ativas', callback_data='menu:minhasrotas')],
        [InlineKeyboardButton('💰 Ajustar limite de preço', callback_data='menu:limite')],
        [InlineKeyboardButton('🔎 Configurar fontes de busca', callback_data='menu:fontes')],
        [InlineKeyboardButton('🖼️ Gerar consulta manual agora', callback_data='menu:agora')],
        [InlineKeyboardButton('💳 Meus pagamentos', callback_data='menu:pagamentos')],
        [InlineKeyboardButton('ℹ️ Ajuda e instruções', callback_data='menu:manual')],
    ]
    if chat_id:
        conn = get_db()
        admin = is_admin_chat(conn, str(chat_id))
        conn.close()
    else:
        admin = False
    if admin:
        keyboard.append([InlineKeyboardButton('🛠 Painel', callback_data='menu:adminpainel')])
    return InlineKeyboardMarkup(keyboard)


def admin_panel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('👤 Ver Usuários', callback_data='painel:usuarios')],
        [InlineKeyboardButton('💰 Ver Vendas', callback_data='painel:vendas')],
        [InlineKeyboardButton('⚙️ Planos', callback_data='painel:planos')],
        [InlineKeyboardButton('🧪 Modo Teste', callback_data='painel:modo_teste')],
        [InlineKeyboardButton('🌐 Cobrança Geral', callback_data='painel:cobranca_global')],
        [InlineKeyboardButton('👤 Cobrança Admin', callback_data='painel:cobranca_admin')],
        [InlineKeyboardButton('💳 Gerar Pix', callback_data='painel:pix')],
    ])


def plans_adjust_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('Semanal +R$1', callback_data='painel:plan:weekly:up'),
            InlineKeyboardButton('Semanal -R$1', callback_data='painel:plan:weekly:down'),
        ],
        [
            InlineKeyboardButton('Quinzenal +R$1', callback_data='painel:plan:biweekly:up'),
            InlineKeyboardButton('Quinzenal -R$1', callback_data='painel:plan:biweekly:down'),
        ],
        [
            InlineKeyboardButton('Mensal +R$1', callback_data='painel:plan:monthly:up'),
            InlineKeyboardButton('Mensal -R$1', callback_data='painel:plan:monthly:down'),
        ],
        [InlineKeyboardButton('🔙 Voltar ao Painel', callback_data='painel:back')],
    ])


def user_plan_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('Pix Semanal', callback_data='userpix:Semanal'),
            InlineKeyboardButton('Pix Quinzenal', callback_data='userpix:Quinzenal')
        ],
        [InlineKeyboardButton('Pix Mensal', callback_data='userpix:Mensal')],
    ])


def pending_payment_markup(payment_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Verificar pagamento', callback_data=f'painel:checkpay:{payment_id}')],
        [InlineKeyboardButton('❌ Cancelar pagamento', callback_data=f'payment:cancel:{payment_id}')],
        [InlineKeyboardButton('⬅️ Voltar ao painel', callback_data='menu:back')],
    ])


def airport_keyboard(prefix: str) -> InlineKeyboardMarkup:
    conn = get_db()
    try:
        options = list_airports(conn)
    finally:
        conn.close()

    buttons = []
    row = []
    for code, name in options:
        row.append(InlineKeyboardButton(f'{code} — {name}', callback_data=f'{prefix}:{code}'))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton('❌ Cancelar cadastro de rota', callback_data='addrota:cancel')])
    return InlineKeyboardMarkup(buttons)


def sources_menu_markup(enable_google: bool, enable_maxmilhas: bool) -> InlineKeyboardMarkup:
    google_label = '✅ Google Voos (fixo)' if enable_google else 'Google Voos (fixo)'
    max_label = '✅ MaxMilhas' if enable_maxmilhas else '⬜ MaxMilhas'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(google_label, callback_data='sources:noop')],
        [InlineKeyboardButton(max_label, callback_data='sources:toggle_maxmilhas')],
        [InlineKeyboardButton('⬅️ Voltar ao menu', callback_data='menu:back')],
    ])


def removerrota_list_markup(rows) -> InlineKeyboardMarkup:
    keyboard = []
    for row in rows:
        label = f"{row['origin']}→{row['destination']} | {format_date_br(row['outbound_date'])}"
        if row['inbound_date']:
            label += f" | {format_date_br(row['inbound_date'])}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"removerrota:{row['id']}")])
    keyboard.append([InlineKeyboardButton('❌ Cancelar remoção', callback_data='removerrota:cancel_list')])
    return InlineKeyboardMarkup(keyboard)


async def manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '📘 *Como usar o VooBot*\n\n'
        '✅ *1. Confirme seu cadastro*\n'
        'Antes de usar, toque em confirmar para liberar as funções.\n\n'
        '✈️ *2. Cadastre suas rotas*\n'
        'Escolha origem, destino e datas em poucos toques.\n\n'
        '📋 *3. Acompanhe suas consultas*\n'
        'Veja rapidamente todas as rotas ativas no seu cadastro.\n\n'
        '💰 *4. Defina seu limite*\n'
        'Escolha o valor máximo que faz sentido para você receber alertas.\n\n'
        '🔎 *5. Escolha as fontes*\n'
        'Google Voos fica sempre ativo. MaxMilhas é opcional e começa desmarcado.\n\n'
        '🖼️ *6. Consulte na hora*\n'
        'Use /agora para gerar imediatamente o print mais recente.\n\n'
        '🔔 *7. Receba atualizações automáticas*\n'
        'Os envios seguem os blocos do relógio: 1:00, 1:30, 2:00...',
        parse_mode='Markdown',
        reply_markup=main_menu_markup(),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = str(update.effective_chat.id)
    first_name = user.first_name or 'telegram'

    conn = get_db()
    row = get_bot_user_by_chat(conn, chat_id)
    if row is None:
        user_id = ensure_app_user(conn, first_name)
        conn.execute(
            '''
            INSERT INTO bot_users (user_id, chat_id, username, first_name, confirmed)
            VALUES (?, ?, ?, ?, 0)
            ''',
            (user_id, chat_id, user.username or '', first_name),
        )
        ensure_user_settings(conn, user_id)
    else:
        conn.execute(
            '''
            UPDATE bot_users
            SET username = ?, first_name = ?
            WHERE chat_id = ?
            ''',
            (user.username or '', first_name, chat_id),
        )
    ensure_user_access(conn, chat_id)
    ensure_owner_test_access(conn)
    conn.commit()
    conn.close()

    await update.message.reply_text(
        '✈️ *Bem-vindo ao VooBot*\n\n'
        'Para liberar o menu e começar a usar, confirme seu cadastro no botão abaixo.',
        parse_mode='Markdown',
        reply_markup=start_markup(),
    )


async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat.id)

    conn = get_db()
    row = get_bot_user_by_chat(conn, chat_id)
    if row is None:
        conn.close()
        await query.edit_message_text('⚠️ Use /start antes para iniciar seu cadastro.')
        return

    conn.execute('UPDATE bot_users SET confirmed = 1 WHERE chat_id = ?', (chat_id,))
    ensure_user_settings(conn, int(row['user_id']))
    
    cur = conn.execute('SELECT COUNT(*) FROM user_routes WHERE user_id = ? AND active = 1', (row['user_id'],))
    routes_count = cur.fetchone()[0]
    
    conn.commit()
    conn.close()

    await query.edit_message_text('✅ Cadastro confirmado com sucesso!')
    
    msg_text = get_panel_text(chat_id)

    await query.message.reply_text(
        msg_text,
        parse_mode='Markdown',
        reply_markup=full_menu_markup(chat_id),
    )


async def cmd_painel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_db()
    if not is_admin_chat(conn, chat_id):
        conn.close()
        await update.message.reply_text('🚫 Comando restrito a administradores.')
        return

    ensure_owner_test_access(conn)
    settings = get_monetization_settings(conn)
    conn.close()

    texto = (
        '🛠 *Painel Administrativo*\n\n'
        f"🧪 Modo teste: {'ATIVADO ✅' if int(settings['test_mode']) == 1 else 'DESATIVADO ❌'}\n"
        f"🌐 Cobrança geral: {'ATIVA ✅' if int(settings['charge_global']) == 1 else 'DESATIVADA ❌'}\n"
        f"👤 Cobrança só admin: {'ATIVA ✅' if int(settings['charge_admin_only']) == 1 else 'DESATIVADA ❌'}"
    )

    await update.message.reply_text(
        texto,
        parse_mode='Markdown',
        reply_markup=admin_panel_markup()
    )

async def painel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat.id)
    conn = get_db()
    if not is_admin_chat(conn, chat_id):
        conn.close()
        await query.answer('Não autorizado', show_alert=True)
        return

    parts = query.data.split(':')
    action = parts[1] if len(parts) > 1 else ''
    ensure_owner_test_access(conn)

    if query.data.startswith('userpix:'):
        plan_name = query.data.split(':', 1)[1]
        access = ensure_user_access(conn, chat_id)
        if not should_charge_user(conn, chat_id, access):
            conn.close()
            await query.answer('Cobrança não disponível para este usuário.', show_alert=True)
            return
        if is_active_access(access):
            conn.close()
            await query.message.reply_text(f"✅ Você já tem um plano ativo até {access['expires_at']}. Não é necessário gerar outro Pix agora.")
            await query.answer()
            return
        existing_pending = get_valid_pending_payment(conn, chat_id)
        if existing_pending:
            conn.close()
            await query.edit_message_text(
                f"💳 *Você já tem um Pix pendente válido*\n\n*Plano:* {existing_pending['plan_name']}\n*Valor:* R$ {format_money_br(existing_pending['amount'])}\n*ID:* `{existing_pending['mp_payment_id']}`",
                parse_mode='Markdown'
            )
            await query.message.reply_text(existing_pending['qr_code'] or 'Código Pix indisponível no momento.')
            if existing_pending['ticket_url']:
                await query.message.reply_text(existing_pending['ticket_url'])
            await query.message.reply_text(
                'Selecione uma opção:',
                reply_markup=pending_payment_markup(str(existing_pending['mp_payment_id']))
            )
            await query.answer()
            return
        settings = get_monetization_settings(conn)
        amount = plan_amount_by_name(settings, plan_name)
        payment = create_mp_pix_payment(chat_id, plan_name, amount)
        qr_code = payment.get('point_of_interaction', {}).get('transaction_data', {}).get('qr_code', '')
        ticket_url = payment.get('point_of_interaction', {}).get('transaction_data', {}).get('ticket_url', '')
        save_payment(conn, str(payment.get('id')), chat_id, plan_name, amount, payment.get('status', 'pending'), qr_code, ticket_url)
        conn.close()
        await query.edit_message_text(
            f"💳 *Pix gerado com sucesso!*\n\n*Valor:* R$ {format_money_br(amount)}\n*ID:* `{payment.get('id')}`",
            parse_mode='Markdown'
        )
        await query.message.reply_text(qr_code or 'Código Pix indisponível no momento.')
        if ticket_url:
            await query.message.reply_text(ticket_url)
        await query.message.reply_text(
            'Selecione uma opção:',
            reply_markup=pending_payment_markup(str(payment.get("id")))
        )
        await query.answer()
        return

    if action == 'usuarios':
        free_uses_limit = get_free_uses_limit(conn)
        users = conn.execute(
            """
            SELECT b.user_id, b.chat_id, b.first_name, b.username, b.confirmed,
                   ua.status, ua.expires_at, ua.free_uses, ua.total_paid
            FROM bot_users b
            LEFT JOIN user_access ua ON ua.chat_id = b.chat_id
            ORDER BY b.id DESC
            """
        ).fetchall()
        lines = [
            f"• {u['first_name'] or 'Sem nome'} (chat: {u['chat_id']})\n  Confirmado: {u['confirmed']} | Status: {u['status'] or 'free'} | Vencimento: {u['expires_at'] or '-'} | Grátis: {int(u['free_uses'] or 0)}/{free_uses_limit} | Total pago: R$ {format_money_br(u['total_paid'] or 0)}"
            for u in users
        ]
        text = "👤 *Usuários Registrados*\n\n" + ("\n\n".join(lines) if lines else "_Nenhum_")
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton('🔙 Voltar ao Painel', callback_data='painel:back')]
        ]))

    elif action == 'vendas':
        rows = conn.execute("SELECT mp_payment_id, plan_name, amount, status, created_at FROM payments ORDER BY created_at DESC LIMIT 10").fetchall()
        total_aprovado = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'approved'").fetchone()[0]
        total_pendente = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'pending'").fetchone()[0]
        aprovados = conn.execute("SELECT COUNT(*) FROM payments WHERE status = 'approved'").fetchone()[0]
        pendentes = conn.execute("SELECT COUNT(*) FROM payments WHERE status = 'pending'").fetchone()[0]
        outros = conn.execute("SELECT COUNT(*) FROM payments WHERE status NOT IN ('approved', 'pending')").fetchone()[0]
        if rows:
            lines = [f"• {r['mp_payment_id'] or '-'} | {r['plan_name'] or '-'} | R$ {format_money_br(r['amount'])} | {r['status']}" for r in rows]
            texto = (
                "💰 *Relatório de Vendas*\n\n"
                f"Receita aprovada: *R$ {format_money_br(total_aprovado)}*\n"
                f"Valor pendente: *R$ {format_money_br(total_pendente)}*\n\n"
                f"Pagamentos aprovados: *{aprovados}*\n"
                f"Pagamentos pendentes: *{pendentes}*\n"
                f"Outros status: *{outros}*\n\n"
                "Últimos registros:\n" + "\n".join(lines)
            )
        else:
            texto = "💰 *Relatório de Vendas*\n\n_Nenhum pagamento registrado ainda._"
        await query.edit_message_text(
            texto,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔙 Voltar ao Painel', callback_data='painel:back')]])
        )

    elif action == 'planos':
        settings = get_monetization_settings(conn)
        texto = (
            "⚙️ *Configuração de Planos*\n\n"
            f"🥉 Semanal: R$ {format_money_br(settings['weekly_price'])} (7 dias)\n"
            f"🥈 Quinzenal: R$ {format_money_br(settings['biweekly_price'])} (15 dias)\n"
            f"🥇 Mensal: R$ {format_money_br(settings['monthly_price'])} (30 dias)"
        )
        await query.edit_message_text(texto, parse_mode='Markdown', reply_markup=plans_adjust_markup())

    elif action == 'modo_teste':
        settings = get_monetization_settings(conn)
        novo = 0 if int(settings['test_mode']) == 1 else 1
        conn.execute('UPDATE monetization_settings SET test_mode = ? WHERE id = 1', (novo,))
        for admin_chat_id in list_active_admin_chat_ids(conn):
            ensure_user_access(conn, admin_chat_id)
            conn.execute(
                "UPDATE user_access SET test_charge = ?, updated_at = datetime('now') WHERE chat_id = ?",
                (novo, admin_chat_id),
            )
        conn.commit()
        settings = get_monetization_settings(conn)
        texto = (
            '🛠 *Painel Administrativo*\n\n'
            f"🧪 Modo teste: {'ATIVADO ✅' if int(settings['test_mode']) == 1 else 'DESATIVADO ❌'}\n"
            f"🌐 Cobrança geral: {'ATIVA ✅' if int(settings['charge_global']) == 1 else 'DESATIVADA ❌'}\n"
            f"👤 Cobrança só admin: {'ATIVA ✅' if int(settings['charge_admin_only']) == 1 else 'DESATIVADA ❌'}"
        )
        await query.edit_message_text(texto, parse_mode='Markdown', reply_markup=admin_panel_markup())

    elif action == 'cobranca_global':
        settings = get_monetization_settings(conn)
        novo = 0 if int(settings['charge_global']) == 1 else 1
        conn.execute('UPDATE monetization_settings SET charge_global = ? WHERE id = 1', (novo,))
        conn.commit()
        settings = get_monetization_settings(conn)
        texto = (
            '🛠 *Painel Administrativo*\n\n'
            f"🧪 Modo teste: {'ATIVADO ✅' if int(settings['test_mode']) == 1 else 'DESATIVADO ❌'}\n"
            f"🌐 Cobrança geral: {'ATIVA ✅' if int(settings['charge_global']) == 1 else 'DESATIVADA ❌'}\n"
            f"👤 Cobrança só admin: {'ATIVA ✅' if int(settings['charge_admin_only']) == 1 else 'DESATIVADA ❌'}"
        )
        await query.edit_message_text(texto, parse_mode='Markdown', reply_markup=admin_panel_markup())

    elif action == 'cobranca_admin':
        settings = get_monetization_settings(conn)
        novo = 0 if int(settings['charge_admin_only']) == 1 else 1
        conn.execute('UPDATE monetization_settings SET charge_admin_only = ? WHERE id = 1', (novo,))
        conn.commit()
        settings = get_monetization_settings(conn)
        texto = (
            '🛠 *Painel Administrativo*\n\n'
            f"🧪 Modo teste: {'ATIVADO ✅' if int(settings['test_mode']) == 1 else 'DESATIVADO ❌'}\n"
            f"🌐 Cobrança geral: {'ATIVA ✅' if int(settings['charge_global']) == 1 else 'DESATIVADA ❌'}\n"
            f"👤 Cobrança só admin: {'ATIVA ✅' if int(settings['charge_admin_only']) == 1 else 'DESATIVADA ❌'}"
        )
        await query.edit_message_text(texto, parse_mode='Markdown', reply_markup=admin_panel_markup())

    elif action == 'pix':
        settings = get_monetization_settings(conn)
        amount = 1.0 if int(settings['test_mode']) == 1 else float(settings['monthly_price'])
        plan_name = 'Teste Admin' if int(settings['test_mode']) == 1 else 'Mensal'
        payment = create_mp_pix_payment(chat_id, plan_name, amount)
        qr_code = payment.get('point_of_interaction', {}).get('transaction_data', {}).get('qr_code', '')
        ticket_url = payment.get('point_of_interaction', {}).get('transaction_data', {}).get('ticket_url', '')
        save_payment(conn, str(payment.get('id')), chat_id, plan_name, amount, payment.get('status', 'pending'), qr_code, ticket_url)
        await query.edit_message_text(
            f"💳 *Pix gerado com sucesso!*\n\n*Valor:* R$ {format_money_br(amount)}\n*ID:* `{payment.get('id')}`",
            parse_mode='Markdown'
        )
        await query.message.reply_text(qr_code or 'Código Pix indisponível no momento.')
        if ticket_url:
            await query.message.reply_text(ticket_url)
        await query.message.reply_text(
            'Selecione uma opção:',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton('✅ Verificar pagamento', callback_data=f'painel:checkpay:{payment.get("id")}')],
                [InlineKeyboardButton('Voltar ao painel', callback_data='painel:back')]
            ])
        )

    elif action == 'checkpay' and len(parts) >= 3:
        payment_id = parts[2]
        approved, info = apply_approved_payment(conn, payment_id)
        if approved:
            await query.message.reply_text(f'🎉 Pagamento aprovado! Acesso liberado até {info}.')
        else:
            await query.message.reply_text(f'⏳ Pagamento ainda não aprovado. Status atual: {info}')

    elif action == 'cancel' and len(parts) >= 3:
        payment_id = parts[2]
        conn.execute(
            "UPDATE payments SET status = 'cancelled' WHERE mp_payment_id = ? AND chat_id = ? AND status = 'pending'",
            (payment_id, chat_id)
        )
        conn.commit()
        await query.message.reply_text(
            '❌ Pagamento cancelado. Escolha um novo plano:',
            reply_markup=user_plan_markup()
        )

    elif action == 'plan' and len(parts) >= 4:
        field = parts[2]
        direction = parts[3]
        mapping = {
            'weekly': 'weekly_price',
            'biweekly': 'biweekly_price',
            'monthly': 'monthly_price',
        }
        column = mapping.get(field)
        settings = get_monetization_settings(conn)
        current = float(settings[column])
        new_value = current + 1 if direction == 'up' else max(1, current - 1)
        conn.execute(f'UPDATE monetization_settings SET {column} = ? WHERE id = 1', (new_value,))
        conn.commit()
        settings = get_monetization_settings(conn)
        texto = (
            "⚙️ *Configuração de Planos*\n\n"
            f"🥉 Semanal: R$ {format_money_br(settings['weekly_price'])} (7 dias)\n"
            f"🥈 Quinzenal: R$ {format_money_br(settings['biweekly_price'])} (15 dias)\n"
            f"🥇 Mensal: R$ {format_money_br(settings['monthly_price'])} (30 dias)"
        )
        await query.edit_message_text(texto, parse_mode='Markdown', reply_markup=plans_adjust_markup())

    elif action == 'back':
        settings = get_monetization_settings(conn)
        texto = (
            '🛠 *Painel Administrativo*\n\n'
            f"🧪 Modo teste: {'ATIVADO ✅' if int(settings['test_mode']) == 1 else 'DESATIVADO ❌'}\n"
            f"🌐 Cobrança geral: {'ATIVA ✅' if int(settings['charge_global']) == 1 else 'DESATIVADA ❌'}\n"
            f"👤 Cobrança só admin: {'ATIVA ✅' if int(settings['charge_admin_only']) == 1 else 'DESATIVADA ❌'}"
        )
        await query.edit_message_text(texto, parse_mode='Markdown', reply_markup=admin_panel_markup())

    conn.close()
    await query.answer()

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_db()
    msg = require_confirmation(conn, chat_id)

    if msg:
        conn.close()
        await update.message.reply_text(msg, reply_markup=start_markup())
        return

    ensure_user_access(conn, chat_id)
    ensure_owner_test_access(conn)
    access = ensure_user_access(conn, chat_id)
    should_charge = should_charge_user(conn, chat_id, access)

    if should_charge:
        expires_at = (access['expires_at'] or '').strip()
        if access['status'] == 'active' and expires_at:
            try:
                if datetime.fromisoformat(expires_at) < now_local():
                    conn.execute("UPDATE user_access SET status = 'expired', updated_at = datetime('now') WHERE chat_id = ?", (chat_id,))
                    conn.commit()
                    access = ensure_user_access(conn, chat_id)
            except ValueError:
                pass

    row = get_bot_user_by_chat(conn, chat_id)
    cur = conn.execute('SELECT COUNT(*) FROM user_routes WHERE user_id = ? AND active = 1', (row['user_id'],))
    routes_count = cur.fetchone()[0]
    conn.close()

    msg_text = get_panel_text(chat_id)

    await update.message.reply_text(
        msg_text,
        parse_mode='Markdown',
        reply_markup=full_menu_markup(chat_id),
    )


async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '🤖 *Comandos do VooBot*\n\n'
        '/start — iniciar e confirmar cadastro\n'
        '/menu — abrir o menu principal\n'
        '/manual — ver o guia rápido\n'
        '/addrota — cadastrar nova rota\n'
        '/minhasrotas — listar suas rotas\n'
        '/removerrota — remover uma rota\n'
        '/limite — definir valor máximo\n'
        '/agora — gerar o print na hora\n'
        '/cancelar — cancelar a ação atual',
        parse_mode='Markdown',
        reply_markup=main_menu_markup(),
    )


async def minhas_rotas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_db()
    msg = require_confirmation(conn, chat_id)
    if msg:
        conn.close()
        await update.message.reply_text(msg, reply_markup=start_markup())
        return

    user_id = get_user_id_by_chat(conn, chat_id)
    rows = conn.execute(
        '''
        SELECT origin, destination, outbound_date, inbound_date, active
        FROM user_routes
        WHERE user_id = ? AND active = 1
        ORDER BY outbound_date, origin, destination
        LIMIT 20
        ''',
        (user_id,),
    ).fetchall()
    setting = get_user_settings(conn, user_id)
    conn.close()

    limite = setting['max_price'] if setting else 1200
    limite_txt = format_money_br(float(limite))
    if not rows:
        await update.message.reply_text(
            f'📋 *Você ainda não tem rotas ativas.*\n'
            f'💰 Limite atual: *R$ {limite_txt}*',
            parse_mode='Markdown',
            reply_markup=main_menu_markup(),
        )
        return

    linhas = [
        '📋 *Suas Rotas Ativas*',
        '══════════════════════',
        f'💰 *Limite de alerta:* R$ {limite_txt}',
        f'🧭 *Total de rotas:* {len(rows)}',
        '',
    ]
    for idx, row in enumerate(rows, start=1):
        origem = airport_label(row['origin'])
        destino = airport_label(row['destination'])
        linhas.append(f'*Rota {idx}*')
        linhas.append(f'🛫 {origem} → {destino}')
        linhas.append(f'📅 Ida: {format_date_br(row["outbound_date"])}')
        if row['inbound_date']:
            linhas.append(f'📅 Volta: {format_date_br(row["inbound_date"])}')
        if idx != len(rows):
            linhas.append('')

    await update.message.reply_text('\n'.join(linhas), parse_mode='Markdown', reply_markup=main_menu_markup())


async def fontes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_db()
    msg = require_confirmation(conn, chat_id)
    if msg:
        conn.close()
        await update.message.reply_text(msg, reply_markup=start_markup())
        return
    user_id = get_user_id_by_chat(conn, chat_id)
    setting = get_user_settings(conn, user_id)
    conn.close()

    await update.message.reply_text(
        '🔎 *Fontes de consultas*\n\nGoogle Voos é sempre obrigatório. Você pode ligar ou desligar o MaxMilhas.',
        parse_mode='Markdown',
        reply_markup=sources_menu_markup(bool(setting['enable_google_flights']), bool(setting['enable_maxmilhas'])),
    )


async def sources_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat.id)
    action = query.data.split(':', 1)[1]

    conn = get_db()
    msg = require_confirmation(conn, chat_id)
    if msg:
        conn.close()
        await query.edit_message_text(msg)
        return

    user_id = get_user_id_by_chat(conn, chat_id)
    setting = get_user_settings(conn, user_id)

    if action == 'toggle_maxmilhas':
        new_value = 0 if int(setting['enable_maxmilhas']) == 1 else 1
        conn.execute(
            'UPDATE bot_settings SET enable_maxmilhas = ?, updated_at = datetime(\'now\') WHERE user_id = ?',
            (new_value, user_id),
        )
        conn.commit()
        setting = get_user_settings(conn, user_id)
        conn.close()
        await query.edit_message_text(
            '🔎 *Fontes de consultas*\n\nGoogle Voos é sempre obrigatório. Você pode ligar ou desligar o MaxMilhas.',
            parse_mode='Markdown',
            reply_markup=sources_menu_markup(bool(setting['enable_google_flights']), bool(setting['enable_maxmilhas'])),
        )
        return

    conn.close()


async def addrota_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_db()
    msg = require_confirmation(conn, chat_id)
    if msg:
        conn.close()
        await update.message.reply_text(msg, reply_markup=start_markup())
        return ConversationHandler.END

    user_id = get_user_id_by_chat(conn, chat_id)
    total_rotas = conn.execute(
        'SELECT COUNT(*) FROM user_routes WHERE user_id = ? AND active = 1',
        (user_id,),
    ).fetchone()[0]
    max_routes_default = get_max_routes_default(conn)
    admin = is_admin_chat(conn, chat_id)
    conn.close()

    if (not admin) and total_rotas >= max_routes_default:
        await update.message.reply_text(f'⚠️ Você atingiu o limite de {max_routes_default} rotas ativas.')
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text('Escolha a origem:', reply_markup=airport_keyboard('origem'))
    return ASK_ORIGIN


async def addrota_origin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Use os botões para escolher a origem.')
    return ASK_ORIGIN


async def addrota_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Use os botões para escolher o destino.')
    return ASK_DESTINATION


async def addrota_outbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['outbound_date'] = normalize_date(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return ASK_OUTBOUND
    return await _save_route_with_inbound(update, context, '')



async def _save_route_with_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE, inbound_date: str):
    chat_id = str(update.effective_chat.id)
    msg_target = update.message or (update.callback_query.message if update.callback_query else None)
    if msg_target is None:
        return ConversationHandler.END
    conn = get_db()

    msg = require_confirmation(conn, chat_id)
    if msg:
        conn.close()
        await msg_target.reply_text(msg, reply_markup=start_markup())
        return ConversationHandler.END

    user_id = get_user_id_by_chat(conn, chat_id)
    conn.execute(
        '''
        INSERT INTO user_routes (user_id, origin, destination, outbound_date, inbound_date, active, created_at)
        VALUES (?, ?, ?, ?, ?, 1, datetime('now'))
        ''',
        (
            user_id,
            context.user_data['origin'],
            context.user_data['destination'],
            context.user_data['outbound_date'],
            inbound_date,
        ),
    )
    conn.commit()
    conn.close()

    await msg_target.reply_text(
        f"✅ *Rota cadastrada*\n{airport_label(context.user_data['origin'])} → {airport_label(context.user_data['destination'])} | {format_date_br(context.user_data['outbound_date'])}" +
        (f" | {format_date_br(inbound_date)}" if inbound_date else ''),
        parse_mode='Markdown',
        reply_markup=main_menu_markup(),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def addrota_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text('❌ Cadastro de rota cancelado.')
    await query.message.reply_text(get_panel_text(str(query.message.chat.id)), parse_mode='Markdown', reply_markup=main_menu_markup())
    return ConversationHandler.END


async def aeroporto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action, code = query.data.split(':', 1)

    if action == 'origem':
        await query.answer('Agora selecione o destino para continuar.', show_alert=True)
        context.user_data['origin'] = code
        await query.edit_message_text(
            f"✅ Origem: {airport_label(code)}\n\nEscolha o destino:",
            reply_markup=airport_keyboard('destino'),
        )
        return ASK_DESTINATION

    if action == 'destino':
        await query.answer('Digite a data de ida no chat para finalizar.', show_alert=True)
        context.user_data['destination'] = code
        await query.edit_message_text(
            f"✅ Destino: {airport_label(code)}\n\nData de ida? Envie em DD/MM/AAAA ou YYYY/MM/DD",
            reply_markup=cancel_markup('addrota:cancel', '❌ Cancelar cadastro de rota'),
        )
        await query.message.reply_text(
            '📅 Responda esta mensagem com a data de ida (DD/MM/AAAA).\nPara cancelar: /cancelar',
            reply_markup=force_reply_markup('Ex.: 25/12/2026'),
        )
        return ASK_OUTBOUND


    return ConversationHandler.END


async def removerrota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_db()
    msg = require_confirmation(conn, chat_id)
    if msg:
        conn.close()
        await update.message.reply_text(msg, reply_markup=start_markup())
        return

    user_id = get_user_id_by_chat(conn, chat_id)
    rows = conn.execute(
        '''
        SELECT id, origin, destination, outbound_date, inbound_date
        FROM user_routes
        WHERE user_id = ? AND active = 1
        ORDER BY outbound_date, origin, destination
        LIMIT 20
        ''',
        (user_id,),
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text('🗑️ Você não tem rotas ativas para remover.', reply_markup=main_menu_markup())
        return

    await update.message.reply_text(
        '🗑️ Escolha a rota que deseja remover:',
        reply_markup=removerrota_list_markup(rows),
    )


async def removerrota_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat.id)
    route_id_str = query.data.split(':', 1)[1]
    
    conn = get_db()
    user_id = get_user_id_by_chat(conn, chat_id)

    if route_id_str == 'cancel_list':
        conn.close()
        await query.edit_message_text('❌ Remoção cancelada.')
        await query.message.reply_text(get_panel_text(str(query.message.chat.id)), parse_mode='Markdown', reply_markup=main_menu_markup())
        return
    
    if route_id_str.startswith('confirm_'):
        route_id = int(route_id_str.split('_')[1])
        row = conn.execute(
            'SELECT origin, destination, outbound_date, inbound_date FROM user_routes WHERE id = ? AND user_id = ?',
            (route_id, user_id),
        ).fetchone()
        if not row:
            conn.close()
            await query.edit_message_text('Rota não encontrada ou já removida.')
            return

        conn.execute(
            'UPDATE user_routes SET active = 0 WHERE id = ? AND user_id = ?',
            (route_id, user_id),
        )
        conn.commit()
        conn.close()

        texto = f"Rota removida com sucesso: {row['origin']}→{row['destination']} | {format_date_br(row['outbound_date'])}"
        if row['inbound_date']:
            texto += f" | {format_date_br(row['inbound_date'])}"
        await query.edit_message_text('🗑️ ' + texto)
        conn2 = get_db()
        remaining_rows = conn2.execute(
            '''
            SELECT id, origin, destination, outbound_date, inbound_date
            FROM user_routes
            WHERE user_id = ? AND active = 1
            ORDER BY outbound_date, origin, destination
            LIMIT 20
            ''',
            (user_id,),
        ).fetchall()
        conn2.close()

        if remaining_rows:
            await query.message.reply_text(
                '🗑️ Escolha a próxima rota que deseja remover:',
                reply_markup=removerrota_list_markup(remaining_rows),
            )
        else:
            await query.message.reply_text(
                '✅ Não há mais rotas ativas para remover.',
                reply_markup=main_menu_markup(),
            )
        return
        
    elif route_id_str.startswith('cancel_'):
        conn.close()
        await query.edit_message_text('❌ Remoção cancelada.')
        await query.message.reply_text(get_panel_text(str(query.message.chat.id)), parse_mode='Markdown', reply_markup=main_menu_markup())
        return

    route_id = int(route_id_str)

    msg = require_confirmation(conn, chat_id)
    if msg:
        conn.close()
        await query.edit_message_text(msg)
        return

    row = conn.execute(
        'SELECT origin, destination, outbound_date, inbound_date FROM user_routes WHERE id = ? AND user_id = ?',
        (route_id, user_id),
    ).fetchone()
    conn.close()
    
    if not row:
        await query.edit_message_text('Rota não encontrada ou já removida.')
        return

    texto = f"{row['origin']}→{row['destination']} | {format_date_br(row['outbound_date'])}"
    if row['inbound_date']:
        texto += f" | {format_date_br(row['inbound_date'])}"
        
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton('⚠️ Sim, quero remover', callback_data=f"removerrota:confirm_{route_id}")],
        [InlineKeyboardButton('❌ Não, cancelar', callback_data=f"removerrota:cancel_{route_id}")],
    ])
    
    await query.edit_message_text(f"Tem certeza que deseja remover esta rota?\n\n{texto}", reply_markup=keyboard)



async def agora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_db()
    conn.row_factory = sqlite3.Row
    msg = require_confirmation(conn, chat_id)
    if msg:
        conn.close()
        await update.message.reply_text(msg, reply_markup=start_markup())
        return

    ensure_user_access(conn, chat_id)
    ensure_owner_test_access(conn)
    access = ensure_user_access(conn, chat_id)
    should_charge = should_charge_user(conn, chat_id, access)

    if should_charge and not is_active_access(access):
        free_uses = int(access['free_uses'] or 0)
        free_uses_limit = get_free_uses_limit(conn)
        if free_uses >= free_uses_limit:
            texto = offer_paid_plans_text(conn, chat_id)
            conn.close()
            await update.message.reply_text(texto, parse_mode='Markdown', reply_markup=user_plan_markup())
            return

    user_id = get_user_id_by_chat(conn, chat_id)
    routes = conn.execute(
        'SELECT 1 FROM user_routes WHERE user_id = ? AND active = 1 LIMIT 1',
        (user_id,),
    ).fetchone()
    if not routes:
        conn.close()
        await update.message.reply_text('📋 Você não tem rotas ativas cadastradas.', reply_markup=main_menu_markup())
        return

    conn.execute(
        "INSERT INTO scan_jobs (user_id, chat_id, job_type, status, payload) VALUES (?, ?, 'manual_now', 'pending', ?)",
        (user_id, chat_id, '{}'),
    )
    conn.commit()
    conn.close()
    await update.message.reply_text('🕒 Consulta recebida. Vou te enviar o print assim que terminar.', reply_markup=main_menu_markup())


async def limite_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_db()
    msg = require_confirmation(conn, chat_id)
    if msg:
        conn.close()
        await update.message.reply_text(msg, reply_markup=start_markup())
        return ConversationHandler.END

    conn.close()
    await update.message.reply_text(
        'Qual o novo limite máximo? Exemplo: 1200 ou 1200,50',
        reply_markup=cancel_markup('limite:cancel', '❌ Cancelar ajuste de limite'),
    )
    await update.message.reply_text(
        '💰 Responda esta mensagem com o novo limite.\nPara cancelar: /cancelar',
        reply_markup=force_reply_markup('Ex.: 1200,50'),
    )
    return ASK_LIMIT


async def limite_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text('❌ Ajuste de limite cancelado.')
    await query.message.reply_text(get_panel_text(str(query.message.chat.id)), parse_mode='Markdown', reply_markup=main_menu_markup())
    return ConversationHandler.END


async def limite_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    texto = update.message.text.strip().replace(',', '.')
    try:
        valor = float(texto)
    except ValueError:
        await update.message.reply_text('Valor inválido. Envie um número, ex: 1200')
        return ASK_LIMIT

    conn = get_db()
    msg = require_confirmation(conn, chat_id)
    if msg:
        conn.close()
        await update.message.reply_text(msg, reply_markup=start_markup())
        return ConversationHandler.END

    user_id = get_user_id_by_chat(conn, chat_id)
    ensure_user_settings(conn, user_id)
    conn.execute(
        '''
        UPDATE bot_settings
        SET max_price = ?, updated_at = datetime('now')
        WHERE user_id = ?
        ''',
        (valor, user_id),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(f'💰 Limite atualizado para R$ {valor:.2f}', reply_markup=main_menu_markup())
    return ConversationHandler.END


async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(':')
    action = parts[1] if len(parts) > 1 else ''
    payment_id = parts[2] if len(parts) > 2 else None
    chat_id = str(query.message.chat.id)
    conn = get_db()
    try:
        if action == 'view' and payment_id:
            row = conn.execute(
                'SELECT mp_payment_id, plan_name, amount, status, created_at, approved_at FROM payments WHERE mp_payment_id = ? AND chat_id = ?',
                (payment_id, chat_id)
            ).fetchone()
            if not row:
                await query.message.reply_text('Pagamento não encontrado.')
                return
            texto = (
                '💳 *Detalhes do pagamento*\n\n'
                f"ID: `{row['mp_payment_id']}`\n"
                f"Plano: {row['plan_name'] or '-'}\n"
                f"Valor: R$ {format_money_br(row['amount'])}\n"
                f"Status: {row['status']}\n"
                f"Criado em: {row['created_at'] or '-'}\n"
                f"Aprovado em: {row['approved_at'] or '-'}"
            )
            await query.message.reply_text(
                texto,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Voltar aos pagamentos', callback_data='menu:pagamentos')]])
            )
        elif action == 'check' and payment_id:
            approved, info = apply_approved_payment(conn, payment_id)
            if approved:
                await query.message.reply_text(f'🎉 Pagamento aprovado! Acesso liberado até {info}.')
            else:
                await query.message.reply_text(f'⏳ Pagamento ainda não aprovado. Status atual: {info}')
        elif action == 'cancel' and payment_id:
            conn.execute(
                "UPDATE payments SET status = 'cancelled' WHERE mp_payment_id = ? AND chat_id = ? AND status = 'pending'",
                (payment_id, chat_id)
            )
            conn.commit()
            texto = offer_paid_plans_text(conn, chat_id)
            await query.edit_message_text(
                texto,
                parse_mode='Markdown',
                reply_markup=user_plan_markup()
            )
        elif action == 'changeplan':
            await query.edit_message_text(
                '💰 Escolha um plano:',
                reply_markup=user_plan_markup()
            )
    finally:
        conn.close()


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action = query.data.split(':', 1)[1]
    chat_id = str(query.message.chat.id)

    conn = get_db()
    msg = require_confirmation(conn, chat_id) if action != 'manual' else None
    conn.close()
    if msg:
        await query.answer('Confirme seu cadastro para continuar.', show_alert=True)
        await query.message.reply_text(msg, reply_markup=start_markup())
        return ConversationHandler.END

    if action == 'addrota':
        await query.answer('Selecione a origem da rota.', show_alert=True)
        await query.message.reply_text('Escolha a origem:', reply_markup=airport_keyboard('origem'))
        return ASK_ORIGIN
    if action == 'minhasrotas':
        await query.answer('Carregando suas rotas...')
        fake_update = Update(update.update_id, message=query.message)
        await minhas_rotas(fake_update, context)
    elif action == 'removerrota':
        await query.answer('Selecione a rota que deseja remover.', show_alert=True)
        fake_update = Update(update.update_id, message=query.message)
        await removerrota(fake_update, context)
    elif action == 'limite':
        await query.answer('Digite o novo limite no chat.', show_alert=True)
        await query.message.reply_text(
            'Qual o novo limite máximo? Exemplo: 1200 ou 1200,50',
            reply_markup=cancel_markup('limite:cancel', '❌ Cancelar ajuste de limite'),
        )
        await query.message.reply_text(
            '💰 Responda esta mensagem com o novo limite.\nPara cancelar: /cancelar',
            reply_markup=force_reply_markup('Ex.: 1200,50'),
        )
        return ASK_LIMIT
    elif action == 'fontes':
        await query.answer('Abrindo fontes de consulta...')
        fake_update = Update(update.update_id, message=query.message)
        await fontes(fake_update, context)
    elif action == 'agora':
        await query.answer('Consulta manual iniciada...')
        fake_update = Update(update.update_id, message=query.message)
        await agora(fake_update, context)
    elif action == 'manual':
        await query.answer('Abrindo instruções...')
        fake_update = Update(update.update_id, message=query.message)
        await manual(fake_update, context)
    elif action == 'pagamentos':
        await query.answer('Abrindo pagamentos...')
        conn = get_db()
        rows = conn.execute(
            '''
            SELECT mp_payment_id, plan_name, amount, status, created_at
            FROM payments
            WHERE chat_id = ?
              AND NOT (status = 'pending' AND datetime(created_at) < datetime('now', '-24 hours'))
            ORDER BY created_at DESC
            LIMIT 15
            ''',
            (chat_id,)
        ).fetchall()
        conn.close()
        if rows:
            texto = '💳 *Meus pagamentos*\n\nSelecione um pagamento para ver detalhes ou atualizar.'
            await query.message.reply_text(texto, parse_mode='Markdown', reply_markup=user_payments_markup(rows))
        else:
            await query.message.reply_text('💳 Você ainda não tem pagamentos registrados.', reply_markup=full_menu_markup(chat_id))
    elif action == 'adminpainel':
        await query.answer('Abrindo painel...')
        conn = get_db()
        admin = is_admin_chat(conn, chat_id)
        conn.close()
        if not admin:
            await query.message.reply_text('🚫 Comando restrito a administradores.')
            return ConversationHandler.END
        fake_update = Update(update.update_id, message=query.message)
        await cmd_painel(fake_update, context)
    elif action == 'back':
        await query.answer('Voltando ao menu...')
        await query.message.reply_text(get_panel_text(str(query.message.chat.id)), parse_mode='Markdown', reply_markup=full_menu_markup(chat_id))

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    chat_id = str(update.message.chat.id)
    await update.message.reply_text(
        'ℹ️ Ação cancelada.\n\n' + get_panel_text(chat_id),
        parse_mode='Markdown',
        reply_markup=full_menu_markup(chat_id)
    )
    return ConversationHandler.END


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand('start', 'Iniciar e confirmar cadastro'),
        BotCommand('menu', 'Abrir o menu principal'),
        BotCommand('addrota', 'Cadastrar uma nova rota'),
        BotCommand('minhasrotas', 'Listar suas rotas'),
        BotCommand('removerrota', 'Remover uma rota'),
        BotCommand('limite', 'Definir limite máximo'),
        BotCommand('agora', 'Rodar consulta e enviar print'),
        BotCommand('manual', 'Ver manual de uso'),
        BotCommand('ajuda', 'Ver comandos disponíveis'),
    ])


def main():
    if not TOKEN:
        raise SystemExit('Defina TELEGRAM_BOT_TOKEN no .env')

    ensure_bot_tables()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('menu', menu))
    app.add_handler(CommandHandler('manual', manual))
    app.add_handler(CommandHandler('ajuda', ajuda))
    app.add_handler(CommandHandler('minhasrotas', minhas_rotas))
    app.add_handler(CommandHandler('agora', agora))
    app.add_handler(CommandHandler('fontes', fontes))
    app.add_handler(CommandHandler('painel', cmd_painel))

    conv = ConversationHandler(
        entry_points=[CommandHandler('addrota', addrota_start), CallbackQueryHandler(menu_callback, pattern=r'^menu:addrota$')],
        states={
            ASK_ORIGIN: [
                CallbackQueryHandler(addrota_cancel_callback, pattern=r'^addrota:cancel$'),
                CallbackQueryHandler(aeroporto_callback, pattern=r'^(origem|destino):'),
            ],
            ASK_DESTINATION: [
                CallbackQueryHandler(addrota_cancel_callback, pattern=r'^addrota:cancel$'),
                CallbackQueryHandler(aeroporto_callback, pattern=r'^(origem|destino):'),
            ],
            ASK_OUTBOUND: [
                CallbackQueryHandler(addrota_cancel_callback, pattern=r'^addrota:cancel$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, addrota_outbound),
            ],
            ASK_LIMIT: [
                CallbackQueryHandler(limite_cancel_callback, pattern=r'^limite:cancel$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, limite_save),
            ],
        },
        fallbacks=[CommandHandler('cancelar', cancel)],
    )
    limite_conv = ConversationHandler(
        entry_points=[CommandHandler('limite', limite_start), CallbackQueryHandler(menu_callback, pattern=r'^menu:limite$')],
        states={
            ASK_LIMIT: [
                CallbackQueryHandler(limite_cancel_callback, pattern=r'^limite:cancel$'),
                MessageHandler(filters.TEXT & ~filters.COMMAND, limite_save),
            ],
        },
        fallbacks=[CommandHandler('cancelar', cancel)],
    )

    app.add_handler(conv)
    app.add_handler(limite_conv)
    app.add_handler(CommandHandler('removerrota', removerrota))
    app.add_handler(CallbackQueryHandler(confirm_callback, pattern=r'^confirm:cadastro$'))
    app.add_handler(CallbackQueryHandler(removerrota_callback, pattern=r'^removerrota:'))
    app.add_handler(CallbackQueryHandler(sources_callback, pattern=r'^sources:'))
    app.add_handler(CallbackQueryHandler(painel_callback, pattern=r'^painel:'))
    app.add_handler(CallbackQueryHandler(painel_callback, pattern=r'^userpix:'))
    app.add_handler(CallbackQueryHandler(payment_callback, pattern=r'^payment:'))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r'^menu:'))
    app.run_polling()


if __name__ == '__main__':
    main()
