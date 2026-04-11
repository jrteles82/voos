import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

replacement_destino = """    if action == 'destino':
        context.user_data['destination'] = code
        await query.edit_message_text(f"Destino escolhido: {AIRPORT_LABELS.get(code, code)}")
        
        calendar, step = DetailedTelegramCalendar(calendar_id=1, min_date=datetime.now().date()).build()
        steps_pt = {'y': 'o ano', 'm': 'o mês', 'd': 'o dia'}
        await query.message.reply_text(f'Data de ida? Selecione {steps_pt[step]}:', reply_markup=calendar)
        return ASK_OUTBOUND
"""

content = re.sub(r"    if action == 'destino':\n        context.user_data\['destination'\] = code\n        await query\.edit_message_text\(f\"Destino escolhido: \{AIRPORT_LABELS\.get\(code, code\)\}\"\)\n        await query\.message\.reply_text\('Data de ida\? Envie em DD/MM/AAAA ou YYYY/MM/DD'\)\n        return ASK_OUTBOUND", replacement_destino, content)

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
