import re

with open('bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_cancel = """async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text('ℹ️ Cadastro cancelado.')
    return ConversationHandler.END"""

new_cancel = """async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    chat_id = str(update.message.chat.id)
    await update.message.reply_text(
        'ℹ️ Ação cancelada.\\n\\n' + get_panel_text(chat_id),
        parse_mode='Markdown',
        reply_markup=full_menu_markup()
    )
    return ConversationHandler.END"""

content = content.replace(old_cancel, new_cancel)

with open('bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patch applied for cancel.")
