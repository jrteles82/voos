import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

new_menu_text = "✈️ *Painel de Controle — VooBot*\\n━━━━━━━━━━━━━━━━━━\\n🤖 *Automático:* Buscas de 30 em 30 min.\\n🖼️ *Manual:* Print imediato na hora.\\n\\n_Escolha uma opção abaixo para gerenciar:_"

# Replace long version
content = content.replace(
    "'✈️ *Menu principal*\\nEscolha uma opção abaixo.\\n\\n⏱️ As consultas automáticas são processadas em ciclos de cerca de 30 minutos, conforme a fila e o balanceamento da demanda.\\n🖼️ Se preferir, use *Consulta manual* para gerar o print imediatamente.'",
    f"'{new_menu_text}'"
)

# Replace short version in action == 'back'
content = content.replace(
    "'✈️ *Menu principal*\\nEscolha uma opção abaixo.'",
    f"'{new_menu_text}'"
)

# Replace the contracted one 
content = content.replace(
    "'✈️ *Menu principal*'",
    "'✈️ *Painel de Controle*'"
)

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
