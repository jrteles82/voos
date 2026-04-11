import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

# Inline Keyboards don't have a "resize_keyboard" param because they automatically try to fit the content or divide space equally.
# But there's a trick to make a single button take full width: just put one button per row, and use long text, 
# or use ReplyKeyboardMarkup (which appears where the system keyboard is) if we wanted full width, but since we are using InlineKeyboardMarkup (under messages), 
# they size to the message width or the content width.
# If we pad the text with spaces (or invisible characters like '⠀' U+2800), they can be forced to be wider.
# Or we can just revert to one per line which naturally stretches to the message width.

replacement = """def full_menu_markup() -> InlineKeyboardMarkup:
    # Use one button per row with zero-width characters/spaces to force it to stretch if needed,
    # but normally one per row is enough to make it as wide as the message bubble.
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Adicionar rota', callback_data='menu:addrota')],
        [InlineKeyboardButton('📋 Minhas rotas', callback_data='menu:minhasrotas')],
        [InlineKeyboardButton('🗑️ Remover rota', callback_data='menu:removerrota')],
        [InlineKeyboardButton('💰 Definir limite de alerta', callback_data='menu:limite')],
        [InlineKeyboardButton('🔎 Fontes de consultas', callback_data='menu:fontes')],
        [InlineKeyboardButton('🖼️ Gerar print agora (Manual)', callback_data='menu:agora')],
        [InlineKeyboardButton('ℹ️ Ajuda e instruções', callback_data='menu:manual')],
    ])"""

content = re.sub(r"def full_menu_markup\(\) -> InlineKeyboardMarkup:.*?\n    \]\)", replacement, content, flags=re.DOTALL)

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
