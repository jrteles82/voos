import re

with open('bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_confirm = """async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    )"""

new_confirm = """async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    msg_text = PANEL_TEXT
    if routes_count == 0:
        msg_text += "\\n\\n⚠️ *Atenção:* Você ainda não tem nenhuma rota cadastrada.\\nClique em *➕ Adicionar nova rota* abaixo para começar."

    await query.message.reply_text(
        msg_text,
        parse_mode='Markdown',
        reply_markup=full_menu_markup(),
    )"""

content = content.replace(old_confirm, new_confirm)

with open('bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done patching.")
