# README de Manutenção de Configurações

Este documento centraliza **todas as configurações possíveis** do projeto, com:
- onde configurar
- quando configurar
- para que serve

## 1) Mapa rápido de configuração

- `.env`: segredos e parâmetros de infraestrutura/execução.
- `flight_tracker_browser.db` (SQLite): regras dinâmicas de negócio (admin, limites, monetização, usuários, cron global, pagamentos).
- `main.py` via variáveis de ambiente `SKYSCANNER_*`, `GOOGLE_*` e `SCAN_IMAGE_*`: tuning operacional do painel web/API.

## 2) Ordem de precedência (importante)

- Políticas de acesso (`admins`, `free_uses_limit`, `max_routes_default`, `pix_pending_expiration_hours`) são lidas do **banco**.
- O `.env` não é mais a fonte dessas políticas no dia a dia.
- Rotas de consulta vêm exclusivamente do banco (`user_routes`).

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
- `TELEGRAM_API_BASE_URL`
  - Para que: base da API Telegram (envio de mensagens/fotos).
  - Quando alterar: proxy/gateway da API Telegram.
### 3.2 Opcionais (operação)

- `PAYMENT_WEBHOOK_PORT` (default `8787`)
  - Onde usado: `payment_webhook.py`.
  - Para que: porta HTTP do webhook.
  - Quando alterar: conflito de porta/reverse proxy.
- `SKYSCANNER_SECRET_KEY` (default inseguro de dev)
  - Onde usado: sessão Flask em `main.py`.
  - Quando alterar: sempre em produção.
- `SCHEDULER_SEND_COOLDOWN_SECONDS` (default derivado de `30min - 100s`)
  - Onde usado: `bot_scheduler.py`.
  - Para que: janela mínima entre envios para o mesmo usuário.
- `SKYSCANNER_AUTO_SCAN` (`1`/`0`)
  - Para que: liga/desliga auto scan.
- `JOB_WORKER_POLL_SECONDS` (default `5`)
  - Onde usado: `job_worker.py`.
  - Para que: intervalo de polling da fila `scan_jobs`.
- `JOB_WORKER_CACHE_TTL_SECONDS` (default `600`)
  - Onde usado: `job_worker.py`.
  - Para que: TTL do cache de imagens em `scan_cache`.
- `SKYSCANNER_RESTART_COMMAND`
  - Para que: comando de restart pelo painel.
- `SKYSCANNER_SCAN_WORKERS`
  - Para que: número de workers de scraping.
- `SKYSCANNER_USER_DATA_DIR`
  - Para que: diretório de perfil do navegador Playwright.
- `PAYMENT_MONITOR_CHECK_INTERVAL_SECONDS` (default `20`)
  - Onde usado: `payment_monitor.py`.
  - Para que: frequência de varredura de pagamentos pendentes.
- `PAYMENT_MONITOR_MP_TIMEOUT_SECONDS` (default `30`)
  - Onde usado: `payment_monitor.py`.
  - Para que: timeout da consulta na API Mercado Pago.
- `MAXMILHAS_ORIGEM`, `MAXMILHAS_DESTINO`, `MAXMILHAS_DATA_IDA_ISO`, `MAXMILHAS_URL`
  - Onde usado: `maxmilhas.py`.
  - Para que: defaults da execução standalone do scraper.
- `GOOGLE_HEADLESS` (`1`/`0`)
  - Onde usado: `skyscanner.py` e `main.py` (via `CONFIG`).
  - Para que: execução headless do navegador para Google Flights.
- `GOOGLE_FLIGHTS_BASE_URL`, `GOOGLE_HL`, `GOOGLE_GL`, `GOOGLE_CURR`
  - Onde usado: `skyscanner.py`.
  - Para que: base da URL e parâmetros regionais/moeda do Google Flights.
- `GOOGLE_TIMEOUT_MS` (default `45000`)
  - Onde usado: `skyscanner.py`.
  - Para que: timeout padrão de operações Playwright no Google Flights.
- `GOOGLE_SETTLE_SECONDS` (default `2`)
  - Onde usado: `skyscanner.py`.
  - Para que: espera curta para estabilização da página antes da leitura.
- `GOOGLE_REQUEST_PAUSE_SECONDS` (default `0.2`)
  - Onde usado: `skyscanner.py`.
  - Para que: pausa entre consultas para reduzir pressão no site.
- `GOOGLE_CHECK_EVERY_HOURS`, `GOOGLE_FULL_SCAN_SECONDS`, `GOOGLE_SCHEDULE_MINUTES`
  - Onde usado: `skyscanner.py`/`main.py` (via `CONFIG`).
  - Para que: cadência do scraper Google/compatibilidade legada.
- `MAXMILHAS_HEADLESS` (default `1`)
  - Para que: roda navegador headless no scraper MaxMilhas.
- `MAXMILHAS_MAX_TENTATIVAS` (default `1`)
  - Para que: tentativas por rota no scraper MaxMilhas.
- `MAXMILHAS_TIMEOUT_PADRAO_MS` (default `30000`)
  - Para que: timeout padrão de operações Playwright no MaxMilhas.
- `MAXMILHAS_BUSCA_RESULT_TIMEOUT_MS` (default `20000`)
  - Para que: timeout para detectar resultados na página do MaxMilhas.
- `MAXMILHAS_MAX_ROTAS_SEGUNDOS` (default `45`)
  - Para que: limite de tempo por rota no MaxMilhas.
- `MAXMILHAS_SALVAR_DEBUG` (`1`/`0`)
  - Para que: salva screenshot/html de debug do MaxMilhas.
- `MAXMILHAS_LIMPAR_DEBUGS_ANTIGOS` (`1`/`0`)
  - Para que: remove arquivos antigos de debug do MaxMilhas.
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
- `users`, `user_routes`, `user_telegram`, `user_runs`, `app_settings`
  - Para que: painel web, rotas, telegram por usuário, histórico de execuções e cron global.

Configuração de cron global no banco:

```sql
UPDATE app_settings
SET cron_enabled = 1,
    scan_interval_minutes = 60,
    max_price_display = NULL,
    updated_at = datetime('now')
WHERE id = 1;
```

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

## 5) O que configurar em cada cenário

- Ambiente novo (primeiro deploy)
  - Ajustar `.env` completo.
  - Subir serviços (`run_all.py` ou `systemd`).
  - Validar bootstrap no banco (`admins` e `monetization_settings`).
- Mudança comercial (planos/limites)
  - Alterar direto no banco em `monetization_settings`.
- Troca de admin
  - Alterar tabela `admins` no banco.
- Ajuste de scraping/monitoramento
  - Alterar variáveis de ambiente de runtime (`GOOGLE_*`, `SKYSCANNER_*`, `SCAN_IMAGE_*`) e/ou banco (`app_settings`, `user_routes`).
- Rotação de segredos
  - Alterar `.env` (`TELEGRAM_BOT_TOKEN`, `MP_ACCESS_TOKEN`, `SKYSCANNER_SECRET_KEY`) e reiniciar processos.

## 6) Reinício após mudanças

- Se usa `run_all.py`, reinicie o processo principal.
- Se usa `systemd`, reinicie o serviço baseado em `skyscanner-bot.service.example`.
- Mudanças em banco geralmente têm efeito imediato, mas é recomendado reiniciar workers para consistência.

## 7) Checklist de segurança e operação

- Nunca commitar `.env` com tokens reais.
- Manter backup periódico de `flight_tracker_browser.db`.
- Validar permissões de arquivo do banco e `.env`.
- Em produção, definir `SKYSCANNER_SECRET_KEY` forte.
- Registrar mudanças de preços/limites/admin em histórico operacional.

## 8) Logs e monitoramento

Se estiver rodando via `systemd`:

- Acompanhar logs em tempo real:

```bash
sudo journalctl -u skyscanner-bot.service -f
```

- Ver últimas 200 linhas:

```bash
sudo journalctl -u skyscanner-bot.service -n 200 --no-pager
```

- Ver logs de hoje:

```bash
sudo journalctl -u skyscanner-bot.service --since today --no-pager
```

- Ver apenas warnings e erros:

```bash
sudo journalctl -u skyscanner-bot.service -p warning --since today --no-pager
```

- Filtrar erros comuns em tempo real:

```bash
sudo journalctl -u skyscanner-bot.service -f | rg -i "erro|error|traceback|exception|warning"
```

Logs locais úteis no projeto:

```bash
tail -f /home/teles/dev/python/skyscanner-bot/run_all.log
tail -f /home/teles/dev/python/skyscanner-bot/main.log
```
