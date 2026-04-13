# Migração SQLite -> MySQL (VPS)

## 1) Gerar SQL de migração

```bash
python3 scripts/migrate_sqlite_to_mysql.py \
  --sqlite flight_tracker_browser.db \
  --mysql-db skyscanner_bot \
  --output mysql_migration.sql
```

## 2) Enviar arquivo para o VPS

```bash
scp mysql_migration.sql usuario@SEU_VPS:/tmp/mysql_migration.sql
```

## 3) Importar no MySQL do VPS

```bash
ssh usuario@SEU_VPS
mysql -h 127.0.0.1 -P 3306 -u SEU_USUARIO -p < /tmp/mysql_migration.sql
```

## 4) Validação rápida

```sql
USE skyscanner_bot;
SHOW TABLES;
SELECT COUNT(*) FROM users;
SELECT COUNT(*) FROM results;
```

## Aplicação direta (opcional)

Se quiser aplicar sem usar `mysql < arquivo.sql`, use `--apply` (requer `pymysql`):

```bash
pip install pymysql
python3 scripts/migrate_sqlite_to_mysql.py \
  --sqlite flight_tracker_browser.db \
  --mysql-db skyscanner_bot \
  --output mysql_migration.sql \
  --apply \
  --mysql-host 127.0.0.1 \
  --mysql-port 3306 \
  --mysql-user root \
  --mysql-password 'SENHA'
```

## Importante

- Esta migração transfere estrutura e dados do banco atual SQLite.
- O projeto agora suporta `DB_ENGINE=mysql` via `db.py` (compat layer).
- No `.env`, configure:
  - `DB_ENGINE=mysql`
  - `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`
