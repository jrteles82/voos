#!/usr/bin/env python3
"""
Monitor local de passagens usando automação do navegador.

Fonte alvo: Google Flights (sem API key)
Tecnologia: Playwright + SQLite + Telegram opcional

O que faz:
- abre o navegador como um usuário normal
- pesquisa várias combinações de datas e destinos
- extrai preço visível da página
- salva histórico em SQLite
- classifica preço por comparação com histórico
- envia alerta por Telegram opcionalmente
- roda uma vez ou em loop a cada 3 horas

Instalação:
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    PLAYWRIGHT_BROWSERS_PATH=.playwright-browsers .venv/bin/playwright install chromium

Uso:
    .venv/bin/python skyscanner.py run-once
    .venv/bin/python skyscanner.py daemon

Telegram:
- opcional, configurado apenas em `skyscanner-config.json`
- sem `telegram_bot_token` e `telegram_chat_id`, o script não envia alerta

Observações:
- Como o site pode mudar, os seletores podem precisar de ajustes.
- O script tenta ser conservador, com poucas consultas e pausas.
- Use apenas para suas consultas pessoais e respeite os termos do serviço do site.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib.parse import quote

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(__file__).with_name(".playwright-browsers")))

import requests
from db import DatabaseOperationalError, connect_db, ensure_column, mysql_enabled

try:
    from playwright.sync_api import Browser, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright
except ImportError as exc:
    raise SystemExit(
        "Playwright não está instalado neste ambiente.\n"
        "Use um virtualenv local:\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/pip install -r requirements.txt\n"
        "  .venv/bin/playwright install chromium\n"
        "  .venv/bin/python skyscanner.py run-once"
    ) from exc


CONFIG_FILE = Path(__file__).with_name("skyscanner-config.json")

DEFAULT_CONFIG = {
    "origin": "PVH",
    "destinations_br": ["JPA", "REC", "NAT"],
    "destinations_sa": [],
    "enable_south_america": False,
    "outbound_dates": ["2026-06-04", "2026-06-05"],
    "inbound_dates": ["2026-06-15", "2026-06-16"],
    "check_every_hours": 3,
    "full_scan_seconds": 10800,
    "schedule_minutes": 180,
    "headless": True,
    "timeout_ms": 45000,
    "settle_seconds": 2,
    "request_pause_seconds": 0.2,
    "db_path": str(Path(__file__).with_name("flight_tracker_browser.db")),
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "price_alert_brl": 1800.0,
    "drop_alert_percent": 8.0,
    "target_site": "google_flights",
}


def _normalize_list(value):
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return value
    return []


def _load_user_config():
    if not CONFIG_FILE.exists():
        return {}
    try:
        with CONFIG_FILE.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}

    normalized = {}
    array_keys = {"destinations_br", "destinations_sa", "outbound_dates", "inbound_dates"}
    for key, value in payload.items():
        if key in array_keys:
            normalized[key] = _normalize_list(value)
        else:
            normalized[key] = value
    return normalized


CONFIG = dict(DEFAULT_CONFIG)
CONFIG.update(_load_user_config())

@dataclass
class RouteQuery:
    origin: str
    destination: str
    outbound_date: str
    inbound_date: str = ""
    trip_type: str = "oneway"


@dataclass
class FlightResult:
    site: str
    origin: str
    destination: str
    outbound_date: str
    price: Optional[float]
    inbound_date: str = ""
    trip_type: str = "oneway"
    currency: str = "BRL"
    url: str = ""
    notes: str = ""
    best_vendor: str = ""
    best_vendor_price: Optional[float] = None
    booking_options_json: str = ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_brl(value: Optional[float]) -> str:
    if value is None:
        return "sem preço"
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class Database:
    def __init__(self, path: str):
        self.conn = connect_db(path)
        self._init_schema()

    def _init_schema(self) -> None:
        if self.conn.backend == "mysql":
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS results (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NULL,
                    created_at TEXT NOT NULL,
                    site VARCHAR(50) NOT NULL,
                    origin VARCHAR(10) NOT NULL,
                    destination VARCHAR(10) NOT NULL,
                    outbound_date VARCHAR(32) NOT NULL,
                    inbound_date VARCHAR(32) NOT NULL,
                    price DOUBLE NULL,
                    currency VARCHAR(10),
                    url TEXT,
                    notes TEXT,
                    price_band VARCHAR(32),
                    best_vendor VARCHAR(255),
                    best_vendor_price DOUBLE NULL,
                    booking_options_json LONGTEXT
                )
                """
            )
            try:
                self.conn.execute(
                    """
                    CREATE INDEX idx_results_route
                    ON results (origin, destination, outbound_date, inbound_date, created_at(255))
                    """
                )
            except Exception:
                pass
            self.conn.commit()
        else:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    created_at TEXT NOT NULL,
                    site TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    outbound_date TEXT NOT NULL,
                    inbound_date TEXT NOT NULL,
                    price REAL,
                    currency TEXT,
                    url TEXT,
                    notes TEXT,
                    price_band TEXT,
                    best_vendor TEXT,
                    best_vendor_price REAL,
                    booking_options_json TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_results_route
                ON results (origin, destination, outbound_date, inbound_date, created_at)
                """
            )
            self.conn.commit()

        for column, definition in [
            ("user_id", "user_id INT NULL" if self.conn.backend == "mysql" else "user_id INTEGER"),
            ("best_vendor", "best_vendor VARCHAR(255)" if self.conn.backend == "mysql" else "best_vendor TEXT"),
            ("best_vendor_price", "best_vendor_price DOUBLE NULL"),
            ("booking_options_json", "booking_options_json LONGTEXT" if self.conn.backend == "mysql" else "booking_options_json TEXT"),
        ]:
            try:
                ensure_column(self.conn, "results", column, definition)
            except DatabaseOperationalError:
                pass

    def save(self, result: FlightResult, price_band: str, user_id: Optional[int] = None) -> None:
        self.conn.execute(
            """
            INSERT INTO results (
                user_id, created_at, site, origin, destination, outbound_date, inbound_date,
                price, currency, url, notes, price_band,
                best_vendor, best_vendor_price, booking_options_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                utc_now_iso(),
                result.site,
                result.origin,
                result.destination,
                result.outbound_date,
                result.inbound_date,
                result.price,
                result.currency,
                result.url,
                result.notes,
                price_band,
                result.best_vendor,
                result.best_vendor_price,
                result.booking_options_json,
            ),
        )
        self.conn.commit()

    def clear_results_for_user(self, user_id: int) -> int:
        deleted = self.conn.execute("DELETE FROM results WHERE user_id = ?", (user_id,)).rowcount
        self.conn.commit()
        return int(deleted or 0)

    def stats_for(self, route: RouteQuery) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        row = self.conn.execute(
            """
            SELECT MIN(price) AS min_price, AVG(price) AS avg_price
            FROM results
            WHERE origin = ? AND destination = ? AND outbound_date = ? AND inbound_date = ? AND price IS NOT NULL
            """,
            (route.origin, route.destination, route.outbound_date, route.inbound_date),
        ).fetchone()
        last = self.conn.execute(
            """
            SELECT price FROM results
            WHERE origin = ? AND destination = ? AND outbound_date = ? AND inbound_date = ? AND price IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (route.origin, route.destination, route.outbound_date, route.inbound_date),
        ).fetchone()
        min_price = float(row["min_price"]) if row and row["min_price"] is not None else None
        avg_price = float(row["avg_price"]) if row and row["avg_price"] is not None else None
        last_price = float(last["price"]) if last and last["price"] is not None else None
        return min_price, avg_price, last_price


def build_config_queries() -> List[RouteQuery]:
    destinations = list(CONFIG["destinations_br"])
    if CONFIG["enable_south_america"]:
        destinations.extend(CONFIG["destinations_sa"])

    queries: List[RouteQuery] = []
    seen = set()
    for dest in destinations:
        for outbound in CONFIG["outbound_dates"]:
            key = (CONFIG["origin"], dest, outbound, "", "oneway")
            if key not in seen:
                seen.add(key)
                queries.append(
                    RouteQuery(
                        origin=CONFIG["origin"],
                        destination=dest,
                        outbound_date=outbound,
                        trip_type="oneway",
                    )
                )
        for inbound in CONFIG["inbound_dates"]:
            key = (dest, CONFIG["origin"], inbound, "", "oneway")
            if key not in seen:
                seen.add(key)
                queries.append(
                    RouteQuery(
                        origin=dest,
                        destination=CONFIG["origin"],
                        outbound_date=inbound,
                        trip_type="oneway",
                    )
                )
    return queries


def build_db_routes_from_rows(rows):
    queries = []
    for row in rows:
        origin = (row["origin"] or "").strip().upper()
        destination = (row["destination"] or "").strip().upper()
        outbound = (row["outbound_date"] or "").strip()
        inbound = (row["inbound_date"] or "").strip()
        if not origin or not destination or not outbound:
            continue
        trip_type = "roundtrip" if inbound else "oneway"
        queries.append(RouteQuery(
            origin=origin,
            destination=destination,
            outbound_date=outbound,
            inbound_date=inbound,
            trip_type=trip_type,
        ))
    return queries


def load_user_routes_from_db(path: str) -> List[RouteQuery]:
    if not mysql_enabled() and not Path(path).exists():
        return []
    conn = connect_db(path)
    try:
        rows = conn.execute("SELECT origin, destination, outbound_date, inbound_date FROM user_routes WHERE active = 1").fetchall()
        return build_db_routes_from_rows(rows)
    finally:
        conn.close()


def build_db_queries(path: str) -> List[RouteQuery]:
    return load_user_routes_from_db(path)


def classify_price(price: Optional[float], min_price: Optional[float], avg_price: Optional[float]) -> str:
    if price is None:
        return "sem_preco"
    if min_price is None and avg_price is None:
        return "novo"
    if min_price is not None and price <= min_price:
        return "excelente"
    if avg_price is not None and price <= avg_price * 0.92:
        return "bom"
    if avg_price is not None and price >= avg_price * 1.15:
        return "caro"
    return "normal"


def should_alert(price: Optional[float], min_price: Optional[float], last_price: Optional[float]) -> Tuple[bool, str]:
    if price is None:
        return False, "sem preço"
    if price <= float(CONFIG["price_alert_brl"]):
        return True, f"abaixo do teto configurado ({format_brl(CONFIG['price_alert_brl'])})"
    if min_price is not None and price <= min_price:
        return True, "novo menor preço"
    if last_price is not None and last_price > 0:
        drop = ((last_price - price) / last_price) * 100.0
        if drop >= float(CONFIG["drop_alert_percent"]):
            return True, f"queda de {drop:.1f}%"
    return False, "sem gatilho"


def send_telegram_message(text: str) -> None:
    token = CONFIG["telegram_bot_token"]
    chat_id = CONFIG["telegram_chat_id"]
    if not token or not chat_id:
        print("[alerta] Telegram não configurado")
        print(text)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=30)
    resp.raise_for_status()


def parse_price_brl(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = text.replace("\xa0", " ")
    patterns = [
        r"R\$\s*([\d\.]+,\d{2})",
        r"R\$\s*([\d\.]+)",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, cleaned)
        if matches:
            raw = matches[0].replace(".", "").replace(",", ".")
            try:
                return float(raw)
            except ValueError:
                pass
    return None


def build_google_flights_url(route: RouteQuery) -> str:
    if route.trip_type == "oneway":
        q = f"{route.origin} to {route.destination} {route.outbound_date} one way"
    else:
        q = f"{route.origin} to {route.destination} {route.outbound_date} return {route.inbound_date}"
    return f"https://www.google.com/travel/flights?q={quote(q)}&hl=pt-BR&gl=BR&curr=BRL"


def describe_trip(route: RouteQuery) -> str:
    if route.trip_type == "oneway":
        return f"{route.origin}->{route.destination} | {route.outbound_date} | ida simples"
    return f"{route.origin}->{route.destination} | {route.outbound_date}/{route.inbound_date} | ida e volta"


class GoogleFlightsScraper:
    def __init__(self, browser):
        self.browser = browser

    def _accept_cookies_if_present(self, page) -> None:
        labels = ["Aceitar tudo", "Aceito", "I agree", "Accept all"]
        for label in labels:
            try:
                page.get_by_role("button", name=label).click(timeout=2000)
                time.sleep(1)
                return
            except Exception:
                pass

    def _wait_briefly_for_results(self, page) -> None:
        ready = False
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
            ready = True
        except Exception:
            pass
        try:
            page.locator("text=Menores preços").first.wait_for(timeout=5000)
            ready = True
        except Exception:
            pass
        if not ready:
            for selector in [
                "[role='main'] [role='listitem']",
                "[role='main'] li",
                "[role='main'] div[role='button']",
            ]:
                try:
                    page.locator(selector).first.wait_for(timeout=2500)
                    ready = True
                    break
                except Exception:
                    pass

        settle_seconds = float(CONFIG.get("settle_seconds", 2))
        # Se a página já mostrou sinais de resultado, evita a espera cheia.
        extra_wait = min(1.0, settle_seconds) if ready else settle_seconds
        if extra_wait > 0:
            time.sleep(extra_wait)

    def _extract_summary_price(self, page) -> Optional[float]:
        patterns = [
            r"Menores preços\s+a partir de\s+R\$\s*([\d\.]+(?:,\d{2})?)",
            r"Menores preços.*?R\$\s*([\d\.]+(?:,\d{2})?)",
        ]
        for sel in ["body", "main", "[role='main']"]:
            try:
                txt = page.locator(sel).first.inner_text(timeout=3000)
                if not txt:
                    continue
                for pattern in patterns:
                    m = re.search(pattern, txt, flags=re.IGNORECASE | re.DOTALL)
                    if m:
                        try:
                            return float(m.group(1).replace(".", "").replace(",", "."))
                        except ValueError:
                            pass
            except Exception:
                pass
        return None

    def _click_lowest_prices_tab(self, page) -> bool:
        candidates = [
            lambda: page.get_by_text("Menores preços", exact=False),
            lambda: page.get_by_role("button", name=re.compile(r"Menores preços", re.I)),
            lambda: page.get_by_role("tab", name=re.compile(r"Menores preços", re.I)),
        ]
        for factory in candidates:
            try:
                loc = factory()
                if loc.count() > 0:
                    loc.first.click(timeout=4000)
                    self._wait_briefly_for_results(page)
                    try:
                        current = loc.first.inner_text(timeout=1500)
                    except Exception:
                        current = ""
                    if "R$" in (current or ""):
                        return True
                    return True
            except Exception:
                pass
        return False

    def _is_probable_flight_card(self, text: str) -> bool:
        low = text.lower()
        if not text or "R$" not in text:
            return False
        if any(x in low for x in ["menores preços", "histórico", "gráfico", "monitorar", "explorar"]):
            return False
        return any(x in low for x in ["parada", "escalas", "co2", "emissões", "voo", "aeroporto"])

    def _extract_visible_flight_cards(self, page) -> List[dict]:
        cards = []
        selectors = [
            "[role='main'] [role='listitem']",
            "[role='main'] li",
            "[role='main'] div[jscontroller]",
            "[role='main'] div[role='button']",
            "[role='listitem']",
            "li",
            "div[jscontroller]",
            "div[role='button']",
        ]
        seen = set()

        for sel in selectors:
            try:
                loc = page.locator(sel)
                count = min(loc.count(), 220)
                for i in range(count):
                    card = loc.nth(i)
                    try:
                        txt = card.inner_text(timeout=1200).strip()
                    except Exception:
                        continue

                    if not self._is_probable_flight_card(txt):
                        continue

                    nums = re.findall(r"R\$\s*([\d\.]+(?:,\d{2})?)", txt)
                    if not nums:
                        continue
                    parsed_prices = []
                    for raw in nums:
                        try:
                            parsed_prices.append(float(raw.replace('.', '').replace(',', '.')))
                        except Exception:
                            pass
                    if not parsed_prices:
                        continue

                    card_price = min(parsed_prices)
                    key = (round(card_price, 2), txt[:200])
                    if key in seen:
                        continue
                    seen.add(key)

                    cards.append({
                        "selector": sel,
                        "index": i,
                        "price": card_price,
                        "prices": parsed_prices,
                        "text": txt[:400],
                        "loc": card,
                    })
            except Exception:
                pass
        return sorted(cards, key=lambda item: item.get("price") if item.get("price") is not None else 10**12)

    def _sort_candidate_cards(self, cards: List[dict], summary_price: Optional[float]) -> List[dict]:
        def _score(item: dict):
            price = item.get("price")
            if price is None:
                return (10**12, 10**12)
            if summary_price is None:
                return (0, price)
            return (abs(price - summary_price), price)

        return sorted(cards, key=_score)

    def _try_click(self, target) -> bool:
        strategies = [
            lambda: target.click(timeout=3500),
            lambda: target.click(timeout=3500, force=True),
        ]
        for fn in strategies:
            try:
                fn()
                return True
            except Exception:
                pass
        return False

    def _wait_for_booking_page(self, page) -> bool:
        if "/travel/flights/booking" in (page.url or ""):
            return True
        try:
            page.wait_for_url(re.compile(r".*/travel/flights/booking.*"), timeout=12000)
            return True
        except Exception:
            return "/travel/flights/booking" in (page.url or "")

    def _open_booking_from_card(self, page, card) -> bool:
        try:
            card.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass

        card_clicked = False
        try:
            card.click(timeout=4000)
            card_clicked = True
            time.sleep(1.5)
        except Exception:
            pass

        if self._wait_for_booking_page(page):
            return True

        action_labels = [
            "Selecionar voo", "Ver voos", "Selecionar", "Reservar", "Opções de reserva",
            "Continuar", "Ver opção", "Escolher"
        ]

        targets = [card, page]
        for target in targets:
            for label in action_labels:
                for role in ["button", "link"]:
                    try:
                        loc = target.get_by_role(role, name=re.compile(label, re.I))
                        if loc.count() > 0 and self._try_click(loc.first):
                            time.sleep(1.8)
                            if self._wait_for_booking_page(page):
                                return True
                    except Exception:
                        pass

        if card_clicked:
            try:
                card.dblclick(timeout=2500)
                time.sleep(1.5)
            except Exception:
                pass

        return self._wait_for_booking_page(page)

    def _collect_booking_text_blocks(self, page) -> List[str]:
        blocks = []
        selectors = [
            "[role='main'] [role='listitem']",
            "[role='main'] li",
            "[role='main'] div",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel)
                count = min(loc.count(), 180)
                for i in range(count):
                    try:
                        txt = loc.nth(i).inner_text(timeout=800).strip()
                    except Exception:
                        continue
                    if txt and "R$" in txt:
                        blocks.append(txt)
                if blocks:
                    break
            except Exception:
                pass
        if not blocks:
            try:
                body = page.locator("body").inner_text(timeout=5000)
                if body:
                    blocks = [body]
            except Exception:
                pass
        return blocks

    def _extract_vendor_options_from_text(self, text: str) -> List[dict]:
        options = []
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        known_vendors = [
            "maxmilhas", "zupper", "decolar", "booking", "gol", "latam", "azul",
            "123 milhas", "123milhas", "viajanet", "voeazul", "smiles", "kayak",
            "mytrip", "trip.com", "edreams", "kiwi", "cvc", "submarino viagens",
        ]

        for idx, line in enumerate(lines):
            low = line.lower()
            prices = re.findall(r"R\$\s*([\d\.]+(?:,\d{2})?)", line)
            vendor = ""
            context = " ".join(lines[max(0, idx - 1): min(len(lines), idx + 2)])
            for name in known_vendors:
                if name in context.lower():
                    vendor = name
                    break

            if not vendor:
                m = re.search(r"(?:Reserve com a|Reservar com|Comprar com|Emitido por|Vendido por)\s+([^\n\r]+)", context, re.I)
                if m:
                    vendor = m.group(1).strip(" :-")

            if prices:
                try:
                    price = float(prices[-1].replace('.', '').replace(',', '.'))
                except Exception:
                    continue
            else:
                price = None
                for probe in lines[idx + 1: min(len(lines), idx + 4)]:
                    probe_prices = re.findall(r"R\$\s*([\d\.]+(?:,\d{2})?)", probe)
                    if not probe_prices:
                        continue
                    try:
                        price = float(probe_prices[-1].replace('.', '').replace(',', '.'))
                        break
                    except Exception:
                        continue

            if vendor and price is not None:
                options.append({"vendor": vendor.strip(), "price": price})

        dedup = []
        seen = set()
        for item in options:
            key = (item["vendor"].lower(), item["price"])
            if key not in seen:
                seen.add(key)
                dedup.append(item)
        return dedup

    def _extract_booking_total_price(self, page) -> Optional[float]:
        patterns = [
            r"Menor preço total\s*R\$\s*([\d\.]+(?:,\d{2})?)",
            r"Menor preço total.*?R\$\s*([\d\.]+(?:,\d{2})?)",
            r"Menores preços\s+a partir de\s+R\$\s*([\d\.]+(?:,\d{2})?)",
        ]
        for sel in ["body", "main", "[role='main']"]:
            try:
                txt = page.locator(sel).first.inner_text(timeout=4000)
            except Exception:
                continue
            if not txt:
                continue
            for pattern in patterns:
                m = re.search(pattern, txt, flags=re.IGNORECASE | re.DOTALL)
                if not m:
                    continue
                try:
                    return float(m.group(1).replace(".", "").replace(",", "."))
                except Exception:
                    pass
        return None

    def _extract_booking_options(self, page) -> Tuple[str, Optional[float], List[dict]]:
        blocks = self._collect_booking_text_blocks(page)
        options = []
        for block in blocks:
            options.extend(self._extract_vendor_options_from_text(block))

        if not options:
            try:
                body = page.locator("body").inner_text(timeout=7000)
            except Exception:
                total_price = self._extract_booking_total_price(page)
                return "", total_price, []
            for pattern in [
                r"Reserve com(?: a)?\s+([^\n\r]+?)\s+R\$\s*([\d\.]+(?:,\d{2})?)",
                r"Reservar com(?: a)?\s+([^\n\r]+?)\s+R\$\s*([\d\.]+(?:,\d{2})?)",
                r"Comprar com(?: a)?\s+([^\n\r]+?)\s+R\$\s*([\d\.]+(?:,\d{2})?)",
                r"Vendido por\s+([^\n\r]+?)\s+R\$\s*([\d\.]+(?:,\d{2})?)",
                r"Reserve com(?: a)?\s+([^\n\r]+?)[\s\S]{0,120}?R\$\s*([\d\.]+(?:,\d{2})?)",
                r"Reservar com(?: a)?\s+([^\n\r]+?)[\s\S]{0,120}?R\$\s*([\d\.]+(?:,\d{2})?)",
            ]:
                for vendor, raw_price in re.findall(pattern, body, flags=re.IGNORECASE):
                    try:
                        price = float(raw_price.replace(".", "").replace(",", "."))
                    except Exception:
                        continue
                    options.append({"vendor": vendor.strip(), "price": price})

        cleaned = []
        seen = set()
        for item in options:
            vendor = (item.get("vendor") or "").strip()
            price = item.get("price")
            if not vendor or price is None:
                continue
            key = (vendor.lower(), price)
            if key not in seen:
                seen.add(key)
                cleaned.append({"vendor": vendor, "price": price})

        booking_total_price = self._extract_booking_total_price(page)
        if not cleaned:
            return "", booking_total_price, []
        best = sorted(cleaned, key=lambda x: x["price"])[0]
        best_price = best["price"]
        if booking_total_price is not None:
            best_price = min(best_price, booking_total_price)
        return best["vendor"], best_price, cleaned

    def search(self, route: RouteQuery) -> FlightResult:
        context = getattr(self.browser, "new_context", None)
        ctx = None
        if callable(context):
            ctx = self.browser.new_context(
                locale="pt-BR",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = ctx.new_page()
        else:
            page = self.browser.new_page()
        page.set_default_timeout(int(CONFIG["timeout_ms"]))
        url = build_google_flights_url(route)
        notes = []
        try:
            page.goto(url, wait_until="domcontentloaded")
            self._accept_cookies_if_present(page)
            self._wait_briefly_for_results(page)

            summary_price = self._extract_summary_price(page)
            notes.append(f"summary_price={format_brl(summary_price)}" if summary_price is not None else "summary_price=N/D")

            clicked_lowest = self._click_lowest_prices_tab(page)
            notes.append(f"clicou_menores_precos={'sim' if clicked_lowest else 'nao'}")

            cards = self._extract_visible_flight_cards(page)
            notes.append(f"cards_encontrados={len(cards)}")

            best_vendor = ""
            best_vendor_price = None
            booking_options = []
            final_price = None
            booking_opened = False
            visible_min_price = min((item.get("price") for item in cards if item.get("price") is not None), default=None)
            if visible_min_price is not None:
                notes.append(f"visible_min_price={format_brl(visible_min_price)}")

            ranked_cards = self._sort_candidate_cards(cards, summary_price)
            max_attempts = min(len(ranked_cards), 4)
            for idx, item in enumerate(ranked_cards[:max_attempts], start=1):
                price = item.get("price")
                notes.append(f"tentativa_card_{idx}={format_brl(price)}")
                if self._open_booking_from_card(page, item["loc"]):
                    booking_opened = True
                    notes.append(f"booking_aberto_no_card={idx}")
                    best_vendor, best_vendor_price, booking_options = self._extract_booking_options(page)
                    if best_vendor:
                        notes.append(f"booking_best_price_card_{idx}={format_brl(best_vendor_price)}")
                        break
                    if best_vendor_price is not None:
                        notes.append(f"booking_total_sem_vendor_card_{idx}={format_brl(best_vendor_price)}")
                    notes.append(f"booking_sem_vendor_no_card={idx}")
                    try:
                        page.go_back(wait_until="domcontentloaded")
                        self._wait_briefly_for_results(page)
                    except Exception:
                        break
                else:
                    notes.append(f"falha_abrir_booking_card={idx}")

            if not booking_opened and ranked_cards:
                fallback = ranked_cards[0]
                notes.append(f"fallback_primeira_lista={format_brl(fallback.get('price'))}")

            if best_vendor:
                notes.append(f"melhor_vendedor={best_vendor} ({format_brl(best_vendor_price)})")
                notes.append(f"opcoes_reserva={len(booking_options)}")

            summary_matches_visible = False
            if summary_price is not None and visible_min_price is not None:
                summary_matches_visible = abs(summary_price - visible_min_price) < 0.01
                if not summary_matches_visible:
                    notes.append(
                        f"summary_visible_mismatch={format_brl(summary_price)}!={format_brl(visible_min_price)}"
                    )

            if best_vendor_price is not None and visible_min_price is not None and abs(best_vendor_price - visible_min_price) >= 0.01:
                notes.append(
                    f"booking_visible_mismatch={format_brl(best_vendor_price)}!={format_brl(visible_min_price)}"
                )

            # Regra do usuário: sempre preferir o menor valor visível na busca.
            # O booking/vendedor fica como detalhe complementar, porque pode refletir
            # outra etapa do fluxo e não o menor preço mostrado na tela principal.
            if visible_min_price is not None:
                final_price = visible_min_price
                notes.append("final_price_source=visible_list")
            elif summary_price is not None:
                final_price = summary_price
                notes.append("final_price_source=summary_fallback")
            elif best_vendor_price is not None:
                final_price = best_vendor_price
                notes.append("final_price_source=booking_fallback")
            elif ranked_cards:
                final_price = ranked_cards[0].get("price")
                notes.append("final_price_source=ranked_fallback")

            if final_price is None:
                notes.append("Preço não identificado automaticamente.")

            return FlightResult(
                site="google_flights",
                origin=route.origin,
                destination=route.destination,
                outbound_date=route.outbound_date,
                inbound_date=route.inbound_date,
                trip_type=route.trip_type,
                price=final_price,
                currency="BRL",
                url=page.url,
                notes=" | ".join(notes),
                best_vendor=best_vendor,
                best_vendor_price=best_vendor_price,
                booking_options_json=json.dumps(booking_options, ensure_ascii=False) if booking_options else "",
            )
        except PlaywrightTimeoutError:
            return FlightResult(
                site="google_flights",
                origin=route.origin,
                destination=route.destination,
                outbound_date=route.outbound_date,
                inbound_date=route.inbound_date,
                trip_type=route.trip_type,
                price=None,
                currency="BRL",
                url=page.url if page else url,
                notes="timeout na página",
            )
        finally:
            try:
                page.close()
            except Exception:
                pass
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass

class Monitor:
    def __init__(self) -> None:
        self.db = Database(CONFIG["db_path"])

    def run_once(self) -> List[FlightResult]:
        routes = build_queries()
        results: List[FlightResult] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=bool(CONFIG["headless"]))
            scraper = GoogleFlightsScraper(browser)

            for route in routes:
                result = scraper.search(route)
                min_price, avg_price, last_price = self.db.stats_for(route)
                band = classify_price(result.price, min_price, avg_price)
                self.db.save(result, band)
                results.append(result)
                print(
                    f"[coleta] {describe_trip(route)} | {format_brl(result.price)} | {band}"
                )

                do_alert, reason = should_alert(result.price, min_price, last_price)
                if do_alert:
                    msg = (
                        f"✈️ Alerta de passagem\n"
                        f"Rota: {route.origin} → {route.destination}\n"
                        f"Data: {route.outbound_date}\n"
                        f"Tipo: {'ida simples' if route.trip_type == 'oneway' else 'ida e volta'}\n"
                        f"Preço: {format_brl(result.price)}\n"
                        f"Motivo: {reason}\n"
                        f"Site: {result.site}"
                    )
                    try:
                        send_telegram_message(msg)
                    except Exception as exc:
                        print(f"[erro] telegram: {exc}")

                time.sleep(CONFIG["request_pause_seconds"])

            browser.close()

        return results

    def daemon(self) -> None:
        interval = int(CONFIG["check_every_hours"]) * 3600
        while True:
            started = time.time()
            try:
                self.run_once()
            except Exception as exc:
                print(f"[erro] execução: {exc}")
            elapsed = time.time() - started
            sleep_for = max(60, interval - int(elapsed))
            print(f"[daemon] próxima execução em {sleep_for // 60} min")
            time.sleep(sleep_for)


def print_summary(results: Iterable[FlightResult]) -> None:
    sorted_results = sorted(
        [r for r in results if r.price is not None],
        key=lambda x: x.price if x.price is not None else 10**12,
    )
    print("\n=== MELHORES RESULTADOS ===")
    for item in sorted_results[:10]:
        print(
            f"{describe_trip(RouteQuery(item.origin, item.destination, item.outbound_date, item.inbound_date, item.trip_type))} | "
            f"{format_brl(item.price)} | {item.site}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor local de passagens via navegador")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run-once")
    sub.add_parser("daemon")
    sub.add_parser("show-config")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    monitor = Monitor()

    if args.command == "run-once":
        results = monitor.run_once()
        print_summary(results)
        return 0

    if args.command == "daemon":
        monitor.daemon()
        return 0

    if args.command == "show-config":
        for k, v in CONFIG.items():
            print(f"{k} = {v}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())

build_queries = build_config_queries
