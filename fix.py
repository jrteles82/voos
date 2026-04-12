import re

with open('bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

bad_func = """def get_panel_text(chat_id: str) -> str:
    conn = get_db()
    row = get_bot_user_by_chat(conn, chat_id)
    if not row:
        conn.close()
        return PANEL_TEXT
    
    cur = conn.execute('SELECT COUNT(*) FROM user_routes WHERE user_id = ? AND active = 1', (row['user_id'],))
    routes_count = cur.fetchone()[0]
    conn.close()
    
    msg_text = get_panel_text(chat_id)
    return msg_text"""

good_func = """def get_panel_text(chat_id: str) -> str:
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
    return msg_text"""

content = content.replace(bad_func, good_func)

with open('bot.py', 'w', encoding='utf-8') as f:
    f.write(content)
