with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

old_block = """    linhas = [f'💰 Limite atual: R$ {limite:.2f}', '']
    for row in rows:
        volta = f" | volta {row['inbound_date']}" if row['inbound_date'] else ''
        linhas.append(
            f"{AIRPORT_LABELS.get(row['origin'], row['origin'])} → {AIRPORT_LABELS.get(row['destination'], row['destination'])} | ida {row['outbound_date']}{volta}"
        )

    await update.message.reply_text('📋 *Suas rotas ativas*\\n\\n' + '\\n'.join(linhas), parse_mode='Markdown')"""

new_block = """    linhas = [f'💰 *Limite de alerta:* R$ {limite:.2f}', '']
    for row in rows:
        volta = f"\\n🔙 *Volta:* {row['inbound_date']}" if row['inbound_date'] else '\\n🔙 *Volta:* Somente ida'
        linhas.append(
            f"🛫 *{AIRPORT_LABELS.get(row['origin'], row['origin'])}* → 🛬 *{AIRPORT_LABELS.get(row['destination'], row['destination'])}*\\n"
            f"📅 *Ida:* {row['outbound_date']}{volta}\\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

    await update.message.reply_text('📋 *SUAS ROTAS ATIVAS*\\n\\n' + '\\n'.join(linhas), parse_mode='Markdown', reply_markup=main_menu_markup())"""

content = content.replace(old_block, new_block)

# Wait, there was also a previous replace that we did for the main_menu_markup in the message. Let's see if that's there.
old_block_2 = """    await update.message.reply_text('📋 *Suas rotas ativas*\\n\\n' + '\\n'.join(linhas), parse_mode='Markdown', reply_markup=main_menu_markup())"""

if old_block_2 in content:
    # Just replace the whole method body part if needed
    pass

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
