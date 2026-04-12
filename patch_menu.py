import re

with open('bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_menu = """async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    )"""

new_menu = """async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    conn = get_db()
    msg = require_confirmation(conn, chat_id)
    
    if msg:
        conn.close()
        await update.message.reply_text(msg, reply_markup=start_markup())
        return
        
    row = get_bot_user_by_chat(conn, chat_id)
    cur = conn.execute('SELECT COUNT(*) FROM user_routes WHERE user_id = ? AND active = 1', (row['user_id'],))
    routes_count = cur.fetchone()[0]
    conn.close()

    msg_text = PANEL_TEXT
    if routes_count == 0:
        msg_text += "\\n\\n⚠️ *Atenção:* Você ainda não tem nenhuma rota cadastrada.\\nClique em *➕ Adicionar nova rota* abaixo para começar."

    await update.message.reply_text(
        msg_text,
        parse_mode='Markdown',
        reply_markup=full_menu_markup(),
    )"""

content = content.replace(old_menu, new_menu)

old_menu_callback = """    if query.data == 'menu:back':
        await query.answer('Voltando ao menu...')
        await query.message.reply_text(PANEL_TEXT, parse_mode='Markdown', reply_markup=full_menu_markup())"""

new_menu_callback = """    if query.data == 'menu:back':
        await query.answer('Voltando ao menu...')
        
        conn = get_db()
        row = get_bot_user_by_chat(conn, chat_id)
        cur = conn.execute('SELECT COUNT(*) FROM user_routes WHERE user_id = ? AND active = 1', (row['user_id'],))
        routes_count = cur.fetchone()[0]
        conn.close()
        
        msg_text = PANEL_TEXT
        if routes_count == 0:
            msg_text += "\\n\\n⚠️ *Atenção:* Você ainda não tem nenhuma rota cadastrada.\\nClique em *➕ Adicionar nova rota* abaixo para começar."
            
        await query.message.reply_text(msg_text, parse_mode='Markdown', reply_markup=full_menu_markup())"""

content = content.replace(old_menu_callback, new_menu_callback)

with open('bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done patching menu.")
