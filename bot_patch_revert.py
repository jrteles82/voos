import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

replacement_destino = """    if action == 'destino':
        context.user_data['destination'] = code
        await query.edit_message_text(f"Destino escolhido: {AIRPORT_LABELS.get(code, code)}")
        await query.message.reply_text('Data de ida? Envie em DD/MM/AAAA ou YYYY/MM/DD')
        return ASK_OUTBOUND"""

content = re.sub(r"    if action == 'destino':.*?return ASK_OUTBOUND", replacement_destino, content, flags=re.DOTALL)

replacement_outbound = """async def addrota_outbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['outbound_date'] = normalize_date(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return ASK_OUTBOUND
    await update.message.reply_text('Data de volta? Envie em DD/MM/AAAA ou YYYY/MM/DD, ou digite somente ida')
    return ASK_INBOUND"""

content = re.sub(r"async def addrota_outbound\(update: Update, context: ContextTypes\.DEFAULT_TYPE\):.*?return ASK_OUTBOUND\n\n\n", replacement_outbound + "\n\n\n", content, flags=re.DOTALL)


replacement_inbound = """async def addrota_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    inbound_raw = update.message.text.strip()
    if inbound_raw.lower() in {'somente ida', 'ida', 'oneway'}:
        inbound_date = ''
    else:
        try:
            inbound_date = normalize_date(inbound_raw)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return ASK_INBOUND

    conn = get_db()"""

content = re.sub(r"async def addrota_inbound\(update: Update, context: ContextTypes\.DEFAULT_TYPE\):.*?conn = get_db\(\)", replacement_inbound, content, flags=re.DOTALL)

# Fix conversation handler
content = content.replace("ASK_OUTBOUND: [CallbackQueryHandler(addrota_outbound, pattern=r'^cbcal_1')],", "ASK_OUTBOUND: [MessageHandler(filters.TEXT & ~filters.COMMAND, addrota_outbound)],")
content = content.replace("ASK_INBOUND: [CallbackQueryHandler(addrota_inbound, pattern=r'^cbcal_2')],", "ASK_INBOUND: [MessageHandler(filters.TEXT & ~filters.COMMAND, addrota_inbound)],")

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
