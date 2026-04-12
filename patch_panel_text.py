import re

with open('bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

helper_func = """
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
        msg_text += "\\n\\n⚠️ *Atenção:* Você ainda não tem nenhuma rota cadastrada.\\nClique em *➕ Adicionar nova rota* abaixo para começar."
    return msg_text
"""

if "def get_panel_text" not in content:
    content = content.replace('PANEL_TEXT = (\n    "✈️ *Painel de Controle*\\n"\n    f"{PANEL_DIVIDER}\\n"\n    "🤖 *Automático:* buscas a cada 30 min\\n"\n    "🖼️ *Manual:* print imediato\\n\\n"\n    "_Escolha uma opção:_"\n)',
        'PANEL_TEXT = (\n    "✈️ *Painel de Controle*\\n"\n    f"{PANEL_DIVIDER}\\n"\n    "🤖 *Automático:* buscas a cada 30 min\\n"\n    "🖼️ *Manual:* print imediato\\n\\n"\n    "_Escolha uma opção:_"\n)' + helper_func)

content = re.sub(
    r"await query\.message\.reply_text\(\s*PANEL_TEXT,\s*parse_mode='Markdown',\s*reply_markup=main_menu_markup\(\)\s*\)",
    r"await query.message.reply_text(get_panel_text(str(query.message.chat.id)), parse_mode='Markdown', reply_markup=main_menu_markup())",
    content
)

content = re.sub(
    r"await query\.message\.reply_text\(\s*PANEL_TEXT,\s*parse_mode='Markdown',\s*reply_markup=full_menu_markup\(\)\s*\)",
    r"await query.message.reply_text(get_panel_text(str(query.message.chat.id)), parse_mode='Markdown', reply_markup=full_menu_markup())",
    content
)

content = re.sub(
    r"msg_text = PANEL_TEXT\n\s*if routes_count == 0:\n\s*msg_text \+=.*?começar.\"",
    r"msg_text = get_panel_text(chat_id)",
    content, flags=re.DOTALL
)

with open('bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patch applied.")
