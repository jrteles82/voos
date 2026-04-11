import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

# Inline Keyboards in Telegram stretch to the width of the message bubble.
# To make them full screen width (or close to it), the text inside the message bubble itself needs to be wide enough.
# The user wants it to look like it spans the screen. 
# We can pad the message text with invisible spaces or a long line.
# In the screenshot, it shows a very clean layout but the buttons end right where the text "Escolha uma opção..." ends.
# Let's add a very long invisible line or visible line at the bottom of the text to force the bubble to be full screen width.

replacement_text = "'✈️ *Painel de Controle — VooBot*\\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\\n🤖 *Automático:* Buscas de 30 em 30 min.\\n🖼️ *Manual:* Print imediato na hora.\\n\\n_Escolha uma opção abaixo para gerenciar:_'"

content = content.replace(
    "'✈️ *Painel de Controle — VooBot*\\n━━━━━━━━━━━━━━━━━━\\n🤖 *Automático:* Buscas de 30 em 30 min.\\n🖼️ *Manual:* Print imediato na hora.\\n\\n_Escolha uma opção abaixo para gerenciar:_'",
    replacement_text
)

# If it's still not wide enough, we add a bunch of invisible spaces to the end of the line
invisible_line = "ㅤ" * 40
replacement_text2 = f"'✈️ *Painel de Controle — VooBot*\\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\\n🤖 *Automático:* Buscas de 30 em 30 min.\\n🖼️ *Manual:* Print imediato na hora.\\n\\n_Escolha uma opção abaixo para gerenciar:_\\n{invisible_line}'"

content = content.replace(replacement_text, replacement_text2)

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
