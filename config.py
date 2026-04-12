import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


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


def _env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variável obrigatória ausente no .env: {name}")
    return value


def _env_required_int(name: str) -> int:
    raw = _env_required(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Variável {name} deve ser inteira. Valor recebido: {raw!r}") from exc


DB_PATH = Path(_env_required("DB_PATH"))
OWNER_TELEGRAM_ID = _env_required("OWNER_TELEGRAM_ID")
MAX_ROUTES_DEFAULT = _env_required_int("MAX_ROUTES_DEFAULT")
FREE_USES_LIMIT = _env_required_int("FREE_USES_LIMIT")
PIX_PENDING_EXPIRATION_HOURS = _env_required_int("PIX_PENDING_EXPIRATION_HOURS")

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

TOKEN = _env_required("TELEGRAM_BOT_TOKEN")
MP_ACCESS_TOKEN = _env_required("MP_ACCESS_TOKEN")
TELEGRAM_CHAT_ID = _env_required("TELEGRAM_CHAT_ID")
