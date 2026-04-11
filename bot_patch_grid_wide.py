import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

# Using unicode spaces to force the button width: " ㅤ " (Hangul Filler U+3164) is highly effective in Telegram for padding inline buttons.
pad = "ㅤㅤㅤ"

replacement = f"""def full_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('{pad}➕ Adicionar{pad}', callback_data='menu:addrota'), InlineKeyboardButton('{pad}🗑️ Remover{pad}', callback_data='menu:removerrota')],
        [InlineKeyboardButton('{pad}📋 Minhas rotas{pad}', callback_data='menu:minhasrotas'), InlineKeyboardButton('{pad}💰 Limite{pad}', callback_data='menu:limite')],
        [InlineKeyboardButton('{pad}🔎 Fontes{pad}', callback_data='menu:fontes'), InlineKeyboardButton('{pad}🖼️ Manual{pad}', callback_data='menu:agora')],
        [InlineKeyboardButton('{pad}ℹ️ Ajuda e instruções do robô{pad}', callback_data='menu:manual')],
    ])"""

content = re.sub(r"def full_menu_markup\(\) -> InlineKeyboardMarkup:.*?\n    \]\)", replacement, content, flags=re.DOTALL)

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
