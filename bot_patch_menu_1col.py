import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

replacement = """def full_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Adicionar Rota', callback_data='menu:addrota')],
        [InlineKeyboardButton('🗑️ Remover Rota', callback_data='menu:removerrota')],
        [InlineKeyboardButton('📋 Minhas Rotas Ativas', callback_data='menu:minhasrotas')],
        [InlineKeyboardButton('💰 Definir Limite de Preço', callback_data='menu:limite')],
        [InlineKeyboardButton('🔎 Fontes de Consultas', callback_data='menu:fontes')],
        [InlineKeyboardButton('🖼️ Gerar Print Agora (Manual)', callback_data='menu:agora')],
        [InlineKeyboardButton('ℹ️ Ajuda e Instruções', callback_data='menu:manual')],
    ])"""

content = re.sub(r"def full_menu_markup\(\) -> InlineKeyboardMarkup:.*?\n    \]\)", replacement, content, flags=re.DOTALL)

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
