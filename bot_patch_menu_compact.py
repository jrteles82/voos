import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

replacement = """def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✈️ Abrir Menu', callback_data='menu:back')],
    ])

def full_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Adicionar rota', callback_data='menu:addrota')],
        [InlineKeyboardButton('📋 Minhas rotas', callback_data='menu:minhasrotas')],
        [InlineKeyboardButton('🗑️ Remover rota', callback_data='menu:removerrota')],
        [InlineKeyboardButton('💰 Definir limite', callback_data='menu:limite')],
        [InlineKeyboardButton('🔎 Fontes de consultas', callback_data='menu:fontes')],
        [InlineKeyboardButton('🖼️ Consulta manual', callback_data='menu:agora')],
        [InlineKeyboardButton('ℹ️ Manual', callback_data='menu:manual')],
    ])"""

content = re.sub(r"def main_menu_markup\(\) -> InlineKeyboardMarkup:.*?\n    \]\)\n", replacement + "\n", content, flags=re.DOTALL)

# Wherever we send the menu options themselves, we should use full_menu_markup
content = content.replace("reply_markup=main_menu_markup()", "reply_markup=main_menu_markup()")
# We want to change the initial menu command and the "back" button to use full_menu_markup
content = content.replace(
"""    await update.message.reply_text(
        '✈️ *Menu principal*\\nEscolha uma opção abaixo.\\n\\n⏱️ As consultas automáticas são processadas em ciclos de cerca de 30 minutos, conforme a fila e o balanceamento da demanda.\\n🖼️ Se preferir, use *Consulta manual* para gerar o print imediatamente.',
        parse_mode='Markdown',
        reply_markup=main_menu_markup(),
    )""",
"""    await update.message.reply_text(
        '✈️ *Menu principal*\\nEscolha uma opção abaixo.\\n\\n⏱️ As consultas automáticas são processadas em ciclos de cerca de 30 minutos, conforme a fila e o balanceamento da demanda.\\n🖼️ Se preferir, use *Consulta manual* para gerar o print imediatamente.',
        parse_mode='Markdown',
        reply_markup=full_menu_markup(),
    )"""
)

content = content.replace(
"""    elif action == 'back':
        await query.message.reply_text('✈️ *Menu principal*\\nEscolha uma opção abaixo.', parse_mode='Markdown', reply_markup=main_menu_markup())""",
"""    elif action == 'back':
        await query.message.reply_text('✈️ *Menu principal*\\nEscolha uma opção abaixo.', parse_mode='Markdown', reply_markup=full_menu_markup())"""
)

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
