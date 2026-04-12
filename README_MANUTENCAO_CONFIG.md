# README de Manutenção de Configurações

Este documento centraliza **todas as configurações possíveis** do projeto, com:
- onde configurar
- quando configurar
- para que serve

## 1) Mapa rápido de configuração

- `.env`: segredos e parâmetros de infraestrutura/execução.
- `flight_tracker_browser.db` (SQLite): regras dinâmicas de negócio (admin, limites, monetização, usuários, cron, pagamentos).
- `skyscanner-config.json`: comportamento de busca e monitoramento de voos.
- `main.py` via variáveis de ambiente `SKYSCANNER_*` e `SCAN_IMAGE_*`: tuning operacional do painel web/API.

## 2) Ordem de precedência (importante)

- Políticas de acesso (`admins`, `free_uses_limit`, `max_routes_default`, `pix_pending_expiration_hours`) são lidas do **banco**.
- O `.env` não é mais a fonte dessas políticas no dia a dia.
- Config de scraping vem de `skyscanner-config.json` (com alguns fallbacks para env no `main.py`).

## 3) Configuração no `.env`

Arquivo: `.env`

### 3.1 Obrigatórias (projeto atual)

- `DB_PATH`
  - Para que: caminho do SQLite principal.
  - Quando alterar: troca de ambiente/pasta ou migração de storage.
  - Exemplo: `DB_PATH=/home/teles/dev/python/skyscanner-bot/flight_tracker_browser.db`
- `TELEGRAM_BOT_TOKEN`
  - Para que: envio de mensagens/fotos Telegram.
  - Quando alterar: troca do bot.
- `TELEGRAM_CHAT_ID`
  - Para que: chat padrão usado por alguns fluxos.
  - Quando alterar: troca do chat padrão.
- `MP_ACCESS_TOKEN`
  - Para que: integração Mercado Pago (PIX).
  - Quando alterar: rotação de credencial.
### 3.2 Opcionais (operação)

- `PAYMENT_WEBHOOK_PORT` (default `8787`)
  - Onde usado: `payment_webhook.py`.
  - Para que: porta HTTP do webhook.
  - Quando alterar: conflito de porta/reverse proxy.
- `SKYSCANNER_SECRET_KEY` (default inseguro de dev)
  - Onde usado: sessão Flask em `main.py`.
  - Quando alterar: sempre em produção.
- `SKYSCANNER_FULL_SCAN_EVERY_SECONDS`
  - Para que: intervalo global de varredura automática.
- `SKYSCANNER_AUTO_SCAN` (`1`/`0`)
  - Para que: liga/desliga auto scan.
- `SKYSCANNER_USER_SCAN_POLL_SECONDS`
  - Para que: polling do scheduler de usuário.
- `SKYSCANNER_RESTART_COMMAND`
  - Para que: comando de restart pelo painel.
- `SKYSCANNER_SCAN_WORKERS`
  - Para que: número de workers de scraping.
- `SKYSCANNER_USER_DATA_DIR`
  - Para que: diretório de perfil do navegador Playwright.
- `SCAN_IMAGE_MAX_ASPECT`
  - Para que: limite de aspecto da imagem gerada.
- `SCAN_IMAGE_SCALE`
  - Para que: escala da imagem gerada.
- `SCAN_IMAGE_TARGET_WIDTH`
  - Para que: largura alvo da imagem.
- `FLASK_DEBUG`
  - Para que: modo debug Flask.

## 4) Configuração no banco (SQLite)

Banco: `DB_PATH` (normalmente `flight_tracker_browser.db`)

### 4.1 Tabelas de política/monetização

- `admins`
  - Campos: `chat_id`, `active`, `created_at`
  - Para que: define quem é admin.
  - Quando alterar: promover/rebaixar admin sem deploy.
- `monetization_settings`
  - Campos principais:
    - `test_mode`
    - `charge_global`
    - `charge_admin_only`
    - `weekly_price`
    - `biweekly_price`
    - `monthly_price`
    - `free_uses_limit`
    - `max_routes_default`
    - `pix_pending_expiration_hours`
  - Para que: regras e preços de monetização.
  - Quando alterar: mudança comercial/plano e limites operacionais.
- `user_access`
  - Campos: `status`, `expires_at`, `free_uses`, `test_charge`, `total_paid`
  - Para que: status de acesso por chat.
  - Quando alterar manualmente: correção de exceção/suporte.

### 4.2 Tabelas operacionais

- `bot_users`, `bot_settings`
  - Para que: vínculo Telegram->usuário e preferências por usuário (preço limite/fontes).
- `payments`
  - Para que: trilha dos PIX gerados/aprovados.
- `scan_jobs`, `scan_cache`
  - Para que: fila de consulta manual e cache de imagem.
- `users`, `user_routes`, `user_telegram`, `user_cron`, `user_runs`
  - Para que: painel web, rotas, telegram por usuário e agenda.

### 4.3 Alterações comuns no banco (exemplos)

Promover admin:

```sql
INSERT OR REPLACE INTO admins (chat_id, active) VALUES ('123456789', 1);
```

Rebaixar admin:

```sql
UPDATE admins SET active = 0 WHERE chat_id = '123456789';
```

Ajustar limites globais:

```sql
UPDATE monetization_settings
SET free_uses_limit = 30,
    max_routes_default = 8,
    pix_pending_expiration_hours = 12
WHERE id = 1;
```

Alterar preços:

```sql
UPDATE monetization_settings
SET weekly_price = 7,
    biweekly_price = 12,
    monthly_price = 20
WHERE id = 1;
```

## 5) Configuração em `skyscanner-config.json`

Arquivo: `skyscanner-config.json`

Chaves usadas:
- `origin`
  - Para que: aeroporto base.
- `destinations_br`, `destinations_sa`
  - Para que: destinos monitorados.
- `enable_south_america`
  - Para que: inclui destinos da América do Sul.
- `outbound_dates`, `inbound_dates`
  - Para que: datas consultadas.
- `check_every_hours`, `full_scan_seconds`, `schedule_minutes`
  - Para que: cadência de execução.
- `headless`, `timeout_ms`, `settle_seconds`, `request_pause_seconds`
  - Para que: estabilidade/performance do scraper.
- `maxmilhas_min_price`, `maxmilhas_final_price_threshold`
  - Para que: filtro e preço final no MaxMilhas.
- `scan_workers` (opcional)
  - Para que: paralelismo de varredura.
- `price_alert_brl`, `drop_alert_percent` (se usados)
  - Para que: critérios de alerta.
- `db_path`, `telegram_bot_token`, `telegram_chat_id` (podem existir)
  - Para que: fallback local para alguns fluxos.

Quando alterar:
- mudança de estratégia de busca
- mudança de datas/destinos
- tuning de performance

## 6) O que configurar em cada cenário

- Ambiente novo (primeiro deploy)
  - Ajustar `.env` completo.
  - Subir serviços (`run_all.py` ou `systemd`).
  - Validar bootstrap no banco (`admins` e `monetization_settings`).
- Mudança comercial (planos/limites)
  - Alterar direto no banco em `monetization_settings`.
- Troca de admin
  - Alterar tabela `admins` no banco.
- Ajuste de scraping/monitoramento
  - Alterar `skyscanner-config.json`.
- Rotação de segredos
  - Alterar `.env` (`TELEGRAM_BOT_TOKEN`, `MP_ACCESS_TOKEN`, `SKYSCANNER_SECRET_KEY`) e reiniciar processos.

## 7) Reinício após mudanças

- Se usa `run_all.py`, reinicie o processo principal.
- Se usa `systemd`, reinicie o serviço baseado em `skyscanner-bot.service.example`.
- Mudanças em banco geralmente têm efeito imediato, mas é recomendado reiniciar workers para consistência.

## 8) Checklist de segurança e operação

- Nunca commitar `.env` com tokens reais.
- Manter backup periódico de `flight_tracker_browser.db`.
- Validar permissões de arquivo do banco e `.env`.
- Em produção, definir `SKYSCANNER_SECRET_KEY` forte.
- Registrar mudanças de preços/limites/admin em histórico operacional.
