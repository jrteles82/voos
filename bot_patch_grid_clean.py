import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

replacement = """def full_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Adicionar', callback_data='menu:addrota'), InlineKeyboardButton('🗑️ Remover', callback_data='menu:removerrota')],
        [InlineKeyboardButton('📋 Minhas rotas', callback_data='menu:minhasrotas'), InlineKeyboardButton('💰 Limite', callback_data='menu:limite')],
        [InlineKeyboardButton('🔎 Fontes', callback_data='menu:fontes'), InlineKeyboardButton('🖼️ Manual', callback_data='menu:agora')],
        [InlineKeyboardButton('ℹ️ Ajuda e instruções', callback_data='menu:manual')],
    ])"""

content = re.sub(r"def full_menu_markup\(\) -> InlineKeyboardMarkup:.*?\n    \]\)", replacement, content, flags=re.DOTALL)

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
