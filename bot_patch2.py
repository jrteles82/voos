import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

replacement_outbound = """async def addrota_outbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    result, key, step = DetailedTelegramCalendar(calendar_id=1, min_date=datetime.now().date()).process(query.data)
    steps_pt = {'y': 'o ano', 'm': 'o mês', 'd': 'o dia'}
    
    if not result and key:
        await query.edit_message_text(f'Data de ida? Selecione {steps_pt[step]}:', reply_markup=key)
        return ASK_OUTBOUND
    elif result:
        context.user_data['outbound_date'] = result.strftime('%Y-%m-%d')
        
        # Now ask for inbound
        calendar, step = DetailedTelegramCalendar(calendar_id=2, min_date=result).build()
        
        # Add "Somente ida" button
        inline_keyboard = calendar.inline_keyboard
        inline_keyboard.append([InlineKeyboardButton("Somente ida", callback_data="cbcal_2_somente_ida")])
        calendar = InlineKeyboardMarkup(inline_keyboard)
        
        await query.edit_message_text(
            f"Ida escolhida: {context.user_data['outbound_date']}\\nData de volta? Selecione {steps_pt[step]} ou clique em 'Somente ida':",
            reply_markup=calendar
        )
        return ASK_INBOUND
    return ASK_OUTBOUND
"""

content = re.sub(r"async def addrota_outbound\(update: Update, context: ContextTypes\.DEFAULT_TYPE\):.*?return ASK_INBOUND", replacement_outbound, content, flags=re.DOTALL)


replacement_inbound = """async def addrota_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(update.effective_chat.id)
    
    if query.data == "cbcal_2_somente_ida":
        inbound_date = ''
    else:
        outbound_dt = datetime.strptime(context.user_data['outbound_date'], '%Y-%m-%d').date()
        result, key, step = DetailedTelegramCalendar(calendar_id=2, min_date=outbound_dt).process(query.data)
        steps_pt = {'y': 'o ano', 'm': 'o mês', 'd': 'o dia'}
        
        if not result and key:
            inline_keyboard = key.inline_keyboard
            inline_keyboard.append([InlineKeyboardButton("Somente ida", callback_data="cbcal_2_somente_ida")])
            key = InlineKeyboardMarkup(inline_keyboard)
            await query.edit_message_text(f"Data de volta? Selecione {steps_pt[step]} ou clique em 'Somente ida':", reply_markup=key)
            return ASK_INBOUND
        elif result:
            inbound_date = result.strftime('%Y-%m-%d')
        else:
            return ASK_INBOUND

    conn = get_db()
"""

content = re.sub(r"async def addrota_inbound\(update: Update, context: ContextTypes\.DEFAULT_TYPE\):\n    chat_id = str\(update\.effective_chat\.id\)\n    inbound_raw = update\.message\.text\.strip\(\)\n    if inbound_raw\.lower\(\) in \{'somente ida', 'ida', 'oneway'\}:\n        inbound_date = ''\n    else:\n        try:\n            inbound_date = normalize_date\(inbound_raw\)\n        except ValueError as exc:\n            await update\.message\.reply_text\(str\(exc\)\)\n            return ASK_INBOUND\n\n    conn = get_db\(\)", replacement_inbound, content)

# Fix conversation handler
content = content.replace("ASK_OUTBOUND: [MessageHandler(filters.TEXT & ~filters.COMMAND, addrota_outbound)],", "ASK_OUTBOUND: [CallbackQueryHandler(addrota_outbound, pattern=r'^cbcal_1')],")
content = content.replace("ASK_INBOUND: [MessageHandler(filters.TEXT & ~filters.COMMAND, addrota_inbound)],", "ASK_INBOUND: [CallbackQueryHandler(addrota_inbound, pattern=r'^cbcal_2')],")

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
