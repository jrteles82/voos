import re

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'r') as f:
    content = f.read()

def inject_markup(match):
    return match.group(1) + ", reply_markup=main_menu_markup()" + match.group(2)

content = re.sub(r"(await update\.message\.reply_text\('📋 \*Suas rotas ativas\*\n\n' \+ '\n'\.join\(linhas\), parse_mode='Markdown')(\))", inject_markup, content)
content = re.sub(r"(await update\.message\.reply_text\('📋 Você ainda não tem rotas ativas cadastradas\.\\n💰 Limite atual: R\$ \{limite:\.2f\}')(\))", inject_markup, content)
content = re.sub(r"(await update\.message\.reply_text\('📋 Você não tem rotas ativas cadastradas\.')(\))", inject_markup, content)

content = content.replace("await update.message.reply_text('🕒 Consulta recebida. Vou te enviar o print assim que terminar.')", "await update.message.reply_text('🕒 Consulta recebida. Vou te enviar o print assim que terminar.', reply_markup=main_menu_markup())")

content = content.replace("await update.message.reply_text('🗑️ Você não tem rotas ativas para remover.')", "await update.message.reply_text('🗑️ Você não tem rotas ativas para remover.', reply_markup=main_menu_markup())")

content = content.replace("await query.edit_message_text('🗑️ ' + texto)", "await query.edit_message_text('🗑️ ' + texto)\n    await query.message.reply_text('✈️ *Menu principal*', parse_mode='Markdown', reply_markup=main_menu_markup())")

with open('/home/teles/dev/python/skyscanner-bot/bot.py', 'w') as f:
    f.write(content)
