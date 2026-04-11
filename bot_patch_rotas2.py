import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

replacement_rotas = """    linhas = [f'💰 *Limite de alerta:* R$ {limite:.2f}', '']
    for row in rows:
        volta = f"\\n🔙 *Volta:* {row['inbound_date']}" if row['inbound_date'] else '\\n🔙 *Volta:* Somente ida'
        linhas.append(
            f"🛫 *{AIRPORT_LABELS.get(row['origin'], row['origin'])}* → 🛬 *{AIRPORT_LABELS.get(row['destination'], row['destination'])}*\\n"
            f"📅 *Ida:* {row['outbound_date']}{volta}\\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

    await update.message.reply_text('📋 *SUAS ROTAS ATIVAS*\\n\\n' + '\\n'.join(linhas), parse_mode='Markdown', reply_markup=main_menu_markup())"""

content = re.sub(r"    linhas = \[f'💰 Limite atual: R\$ \{limite:\.2f\}', ''\]\n    for row in rows:\n        volta = f\" \| volta \{row\['inbound_date'\]\}\" if row\['inbound_date'\] else ''\n        linhas\.append\(\n            f\"\{AIRPORT_LABELS\.get\(row\['origin'\], row\['origin'\]\)\} → \{AIRPORT_LABELS\.get\(row\['destination'\], row\['destination'\]\)\} \| ida \{row\['outbound_date'\]\}\{volta\}\"\n        \)\n\n    await update\.message\.reply_text\('📋 \*Suas rotas ativas\*\n\n' \+ '\n'\.join\(linhas\), parse_mode='Markdown'\)", replacement_rotas, content)
content = content.replace("await update.message.reply_text('📋 *Suas rotas ativas*\\n\\n' + '\\n'.join(linhas), parse_mode='Markdown', reply_markup=main_menu_markup())", replacement_rotas.split('await update.message.reply_text')[1].split('linhas)[1]')[0])

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
