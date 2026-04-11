import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

replacement = """async def removerrota_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat.id)
    route_id_str = query.data.split(':', 1)[1]
    
    conn = get_db()
    user_id = get_user_id_by_chat(conn, chat_id)
    
    if route_id_str.startswith('confirm_'):
        route_id = int(route_id_str.split('_')[1])
        row = conn.execute(
            'SELECT origin, destination, outbound_date, inbound_date FROM user_routes WHERE id = ? AND user_id = ?',
            (route_id, user_id),
        ).fetchone()
        if not row:
            conn.close()
            await query.edit_message_text('Rota não encontrada ou já removida.')
            return

        conn.execute(
            'UPDATE user_routes SET active = 0 WHERE id = ? AND user_id = ?',
            (route_id, user_id),
        )
        conn.commit()
        conn.close()

        texto = f"Rota removida com sucesso: {row['origin']}→{row['destination']} | ida {row['outbound_date']}"
        if row['inbound_date']:
            texto += f" | volta {row['inbound_date']}"
        await query.edit_message_text('🗑️ ' + texto)
        await query.message.reply_text('✈️ *Menu principal*', parse_mode='Markdown', reply_markup=main_menu_markup())
        return
        
    elif route_id_str.startswith('cancel_'):
        conn.close()
        await query.edit_message_text('❌ Remoção cancelada.')
        await query.message.reply_text('✈️ *Menu principal*', parse_mode='Markdown', reply_markup=main_menu_markup())
        return

    route_id = int(route_id_str)

    msg = require_confirmation(conn, chat_id)
    if msg:
        conn.close()
        await query.edit_message_text(msg)
        return

    row = conn.execute(
        'SELECT origin, destination, outbound_date, inbound_date FROM user_routes WHERE id = ? AND user_id = ?',
        (route_id, user_id),
    ).fetchone()
    conn.close()
    
    if not row:
        await query.edit_message_text('Rota não encontrada ou já removida.')
        return

    texto = f"{row['origin']}→{row['destination']} | ida {row['outbound_date']}"
    if row['inbound_date']:
        texto += f" | volta {row['inbound_date']}"
        
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton('⚠️ Sim, quero remover', callback_data=f"removerrota:confirm_{route_id}")],
        [InlineKeyboardButton('❌ Não, cancelar', callback_data=f"removerrota:cancel_{route_id}")],
    ])
    
    await query.edit_message_text(f'Tem certeza que deseja remover esta rota?\\n\\n{texto}', reply_markup=keyboard)"""

content = re.sub(r"async def removerrota_callback\(update: Update, context: ContextTypes\.DEFAULT_TYPE\):.*?await query\.message\.reply_text\('✈️ \*Menu principal\*', parse_mode='Markdown', reply_markup=main_menu_markup\(\)\)", replacement, content, flags=re.DOTALL)

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
