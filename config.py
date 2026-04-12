import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

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


DB_PATH = Path(_env_required("DB_PATH"))

PANEL_DIVIDER = "──────────────────────────"
PANEL_TEXT = (
    "✈️ *Painel de Controle*\n"
    f"{PANEL_DIVIDER}\n"
    "🤖 *Automático:* buscas a cada 30 min\n"
    "🖼️ *Manual:* print imediato\n\n"
    "_Escolha uma opção:_"
)

TOKEN = _env_required("TELEGRAM_BOT_TOKEN")
MP_ACCESS_TOKEN = _env_required("MP_ACCESS_TOKEN")
TELEGRAM_CHAT_ID = _env_required("TELEGRAM_CHAT_ID")
TELEGRAM_API_BASE_URL = _env_required("TELEGRAM_API_BASE_URL").rstrip("/")
MERCADOPAGO_API_BASE_URL = _env_required("MERCADOPAGO_API_BASE_URL").rstrip("/")

APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/Porto_Velho").strip() or "America/Porto_Velho"
APP_TZ = ZoneInfo(APP_TIMEZONE)


def now_local() -> datetime:
    return datetime.now(APP_TZ).replace(tzinfo=None)


def now_local_iso(timespec: str = "seconds", sep: str = " ") -> str:
    return now_local().isoformat(sep=sep, timespec=timespec)
