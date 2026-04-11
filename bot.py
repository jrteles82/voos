import os
import sqlite3
from datetime import datetime
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / '.env'
DB_PATH = BASE_DIR / 'flight_tracker_browser.db'

ASK_ORIGIN, ASK_DESTINATION, ASK_OUTBOUND, ASK_LIMIT = range(4)
OWNER_TELEGRAM_ID = "1748352987"
MAX_ROUTES_DEFAULT = 4
PANEL_DIVIDER = "──────────────────────────"
PANEL_TEXT = (
    "✈️ *Painel de Controle*\n"
    f"{PANEL_DIVIDER}\n"
    "🤖 *Automático:* buscas a cada 30 min\n"
    "🖼️ *Manual:* print imediato\n\n"
    "_Escolha uma opção:_"
)

AIRPORT_OPTIONS = [
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
AIRPORT_LABELS = {code: f"{code} — {name}" for code, name in AIRPORT_OPTIONS}


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

def full_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Adicionar nova rota', callback_data='menu:addrota')],
        [InlineKeyboardButton('➖ Remover rota cadastrada', callback_data='menu:removerrota')],
        [InlineKeyboardButton('📋 Ver minhas rotas ativas', callback_data='menu:minhasrotas')],
        [InlineKeyboardButton('💰 Ajustar limite de preço', callback_data='menu:limite')],
        [InlineKeyboardButton('🔎 Configurar fontes de busca', callback_data='menu:fontes')],
        [InlineKeyboardButton('🖼️ Gerar consulta manual agora', callback_data='menu:agora')],
        [InlineKeyboardButton('ℹ️ Ajuda e instruções', callback_data='menu:manual')],
    ])


def airport_keyboard(prefix: str) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for code, name in AIRPORT_OPTIONS:
        row.append(InlineKeyboardButton(f'{code} — {name}', callback_data=f'{prefix}:{code}'))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def sources_menu_markup(enable_google: bool, enable_maxmilhas: bool) -> InlineKeyboardMarkup:
    google_label = '✅ Google Voos (fixo)' if enable_google else 'Google Voos (fixo)'
    max_label = '✅ MaxMilhas' if enable_maxmilhas else '⬜ MaxMilhas'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(google_label, callback_data='sources:noop')],
        [InlineKeyboardButton(max_label, callback_data='sources:toggle_maxmilhas')],
        [InlineKeyboardButton('⬅️ Voltar ao menu', callback_data='menu:back')],
    ])


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
    conn.commit()
    conn.close()

    await query.edit_message_text('✅ Cadastro confirmado com sucesso!')
    await query.message.reply_text(
        PANEL_TEXT,
        parse_mode='Markdown',
        reply_markup=main_menu_markup(),
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_db()
    msg = require_confirmation(conn, chat_id)
    conn.close()
    if msg:
        await update.message.reply_text(msg, reply_markup=start_markup())
        return

    await update.message.reply_text(
        PANEL_TEXT,
        parse_mode='Markdown',
        reply_markup=full_menu_markup(),
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
    if not rows:
        await update.message.reply_text(f'📋 Você ainda não tem rotas ativas cadastradas.\n💰 Limite atual: R$ {limite:.2f}')
        return

    linhas = [f'💰 *Limite de alerta:* R$ {limite:.2f}', '']
    for row in rows:
        route_line = (
            f"{AIRPORT_LABELS.get(row['origin'], row['origin'])} → "
            f"{AIRPORT_LABELS.get(row['destination'], row['destination'])} | {format_date_br(row['outbound_date'])}"
        )
        linhas.append(
            f"🛫 {route_line}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

    await update.message.reply_text('📋 *SUAS ROTAS ATIVAS*\n\n' + '\n'.join(linhas), parse_mode='Markdown', reply_markup=main_menu_markup())


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
    telegram_user_id = str(update.effective_user.id)
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
    conn.close()

    if telegram_user_id != OWNER_TELEGRAM_ID and total_rotas >= MAX_ROUTES_DEFAULT:
        await update.message.reply_text('⚠️ Você atingiu o limite de 4 rotas ativas.')
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
        f"✅ *Rota cadastrada*\n{AIRPORT_LABELS.get(context.user_data['origin'], context.user_data['origin'])} → {AIRPORT_LABELS.get(context.user_data['destination'], context.user_data['destination'])} | {format_date_br(context.user_data['outbound_date'])}" +
        (f" | {format_date_br(inbound_date)}" if inbound_date else ''),
        parse_mode='Markdown',
        reply_markup=main_menu_markup(),
    )
    context.user_data.clear()
    return ConversationHandler.END


async def aeroporto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, code = query.data.split(':', 1)

    if action == 'origem':
        context.user_data['origin'] = code
        await query.edit_message_text(
            f"✅ Origem: {AIRPORT_LABELS.get(code, code)}\n\nEscolha o destino:",
            reply_markup=airport_keyboard('destino'),
        )
        return ASK_DESTINATION

    if action == 'destino':
        context.user_data['destination'] = code
        await query.edit_message_text(
            f"✅ Destino: {AIRPORT_LABELS.get(code, code)}\n\nData de ida? Envie em DD/MM/AAAA ou YYYY/MM/DD"
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

    keyboard = []
    for row in rows:
        label = f"{row['origin']}→{row['destination']} | {format_date_br(row['outbound_date'])}"
        if row['inbound_date']:
            label += f" | {format_date_br(row['inbound_date'])}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"removerrota:{row['id']}")])

    await update.message.reply_text(
        '🗑️ Escolha a rota que deseja remover:',
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def removerrota_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat.id)
    route_id_str = query.data.split(':', 1)[1]
    
    conn = get_db()
    user_id = get_user_id_by_chat(conn, chat_id)
    
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
        await query.message.reply_text(PANEL_TEXT, parse_mode='Markdown', reply_markup=main_menu_markup())
        return
        
    elif route_id_str.startswith('cancel_'):
        conn.close()
        await query.edit_message_text('❌ Remoção cancelada.')
        await query.message.reply_text(PANEL_TEXT, parse_mode='Markdown', reply_markup=main_menu_markup())
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
    await update.message.reply_text('Qual o novo limite máximo? Exemplo: 1200 ou 1200,50')
    return ASK_LIMIT


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


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split(':', 1)[1]
    chat_id = str(query.message.chat.id)

    conn = get_db()
    msg = require_confirmation(conn, chat_id) if action != 'manual' else None
    conn.close()
    if msg:
        await query.message.reply_text(msg, reply_markup=start_markup())
        return ConversationHandler.END

    if action == 'addrota':
        await query.message.reply_text('Escolha a origem:', reply_markup=airport_keyboard('origem'))
        return ASK_ORIGIN
    if action == 'minhasrotas':
        fake_update = Update(update.update_id, message=query.message)
        await minhas_rotas(fake_update, context)
    elif action == 'removerrota':
        fake_update = Update(update.update_id, message=query.message)
        await removerrota(fake_update, context)
    elif action == 'limite':
        await query.message.reply_text('Qual o novo limite máximo? Exemplo: 1200 ou 1200,50')
        return ASK_LIMIT
    elif action == 'fontes':
        fake_update = Update(update.update_id, message=query.message)
        await fontes(fake_update, context)
    elif action == 'agora':
        fake_update = Update(update.update_id, message=query.message)
        await agora(fake_update, context)
    elif action == 'manual':
        fake_update = Update(update.update_id, message=query.message)
        await manual(fake_update, context)
    elif action == 'back':
        await query.message.reply_text(PANEL_TEXT, parse_mode='Markdown', reply_markup=full_menu_markup())

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text('ℹ️ Cadastro cancelado.')
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

    conv = ConversationHandler(
        entry_points=[CommandHandler('addrota', addrota_start), CallbackQueryHandler(menu_callback, pattern=r'^menu:addrota$')],
        states={
            ASK_ORIGIN: [CallbackQueryHandler(aeroporto_callback, pattern=r'^(origem|destino):')],
            ASK_DESTINATION: [CallbackQueryHandler(aeroporto_callback, pattern=r'^(origem|destino):')],
            ASK_OUTBOUND: [MessageHandler(filters.TEXT & ~filters.COMMAND, addrota_outbound)],
            ASK_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, limite_save)],
        },
        fallbacks=[CommandHandler('cancelar', cancel)],
    )
    limite_conv = ConversationHandler(
        entry_points=[CommandHandler('limite', limite_start), CallbackQueryHandler(menu_callback, pattern=r'^menu:limite$')],
        states={
            ASK_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, limite_save)],
        },
        fallbacks=[CommandHandler('cancelar', cancel)],
    )

    app.add_handler(conv)
    app.add_handler(limite_conv)
    app.add_handler(CommandHandler('removerrota', removerrota))
    app.add_handler(CallbackQueryHandler(confirm_callback, pattern=r'^confirm:cadastro$'))
    app.add_handler(CallbackQueryHandler(removerrota_callback, pattern=r'^removerrota:'))
    app.add_handler(CallbackQueryHandler(sources_callback, pattern=r'^sources:'))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r'^menu:'))
    app.run_polling()


if __name__ == '__main__':
    main()
