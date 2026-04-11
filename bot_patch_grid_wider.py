import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

replacement = """def full_menu_markup() -> InlineKeyboardMarkup:
    pad = "ㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤㅤ"
    pad_small = "ㅤㅤㅤㅤㅤ"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f'{pad_small}➕ Adicionar{pad_small}', callback_data='menu:addrota'), InlineKeyboardButton(f'{pad_small}🗑️ Remover{pad_small}', callback_data='menu:removerrota')],
        [InlineKeyboardButton(f'{pad_small}📋 Minhas rotas{pad_small}', callback_data='menu:minhasrotas'), InlineKeyboardButton(f'{pad_small}💰 Limite{pad_small}', callback_data='menu:limite')],
        [InlineKeyboardButton(f'{pad_small}🔎 Fontes{pad_small}', callback_data='menu:fontes'), InlineKeyboardButton(f'{pad_small}🖼️ Manual{pad_small}', callback_data='menu:agora')],
        [InlineKeyboardButton(f'{pad}ℹ️ Ajuda e instruções do robô{pad}', callback_data='menu:manual')],
    ])"""

content = re.sub(r"def full_menu_markup\(\) -> InlineKeyboardMarkup:.*?\n    \]\)", replacement, content, flags=re.DOTALL)

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
