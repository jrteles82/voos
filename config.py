import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "flight_tracker_browser.db"


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()

OWNER_TELEGRAM_ID = os.getenv("OWNER_TELEGRAM_ID", "1748352987").strip() or "1748352987"
MAX_ROUTES_DEFAULT = int(os.getenv("MAX_ROUTES_DEFAULT", "6"))
FREE_USES_LIMIT = int(os.getenv("FREE_USES_LIMIT", "20"))
PIX_PENDING_EXPIRATION_HOURS = int(os.getenv("PIX_PENDING_EXPIRATION_HOURS", "24"))

PANEL_DIVIDER = "──────────────────────────"
PANEL_TEXT = (
    "✈️ *Painel de Controle*\n"
    f"{PANEL_DIVIDER}\n"
    "🤖 *Automático:* buscas a cada 30 min\n"
    "🖼️ *Manual:* print imediato\n\n"
    "_Escolha uma opção:_"
)

AIRPORT_OPTIONS = [
    ("PVH", "Porto Velho"),
    ("RIO", "Rio de Janeiro"),
    ("SAO", "São Paulo"),
    ("BSB", "Brasília"),
    ("CGB", "Cuiabá"),
    ("GYN", "Goiânia"),
    ("MCZ", "Maceió"),
    ("AJU", "Aracaju"),
    ("SSA", "Salvador"),
    ("FOR", "Fortaleza"),
    ("SLZ", "São Luís"),
    ("CGR", "Campo Grande"),
    ("BHZ", "Belo Horizonte"),
    ("BEL", "Belém"),
    ("JPA", "João Pessoa"),
    ("CWB", "Curitiba"),
    ("REC", "Recife"),
    ("THE", "Teresina"),
    ("NAT", "Natal"),
    ("POA", "Porto Alegre"),
    ("FLN", "Florianópolis"),
    ("VIX", "Vitória"),
    ("MAO", "Manaus"),
    ("RBR", "Rio Branco"),
    ("BVB", "Boa Vista"),
    ("MCP", "Macapá"),
    ("PMW", "Palmas"),
]
AIRPORT_LABELS = {code: f"{code} — {name}" for code, name in AIRPORT_OPTIONS}

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", OWNER_TELEGRAM_ID).strip() or OWNER_TELEGRAM_ID
