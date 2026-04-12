from __future__ import annotations

from flask import Flask, Response, jsonify, request, stream_with_context, session, redirect, url_for, render_template_string, g
from pathlib import Path
from tempfile import NamedTemporaryFile
import json
import os
import re
import shlex
import subprocess
import sys
import time
import random
import requests
import threading
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from datetime import datetime
from math import ceil
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageDraw, ImageFont

from skyscanner import (
    CONFIG,
    Database,
    FlightResult,
    GoogleFlightsScraper,
    RouteQuery,
    build_db_queries,
    classify_price,
    format_brl,
    parse_price_brl,
    sync_playwright,
)

CITY_HIGHLIGHT_EMOJIS = {
    "NAT": "🟡",
    "FOR": "🔵",
    "REC": "🟢",
    "JPA": "🟣",
}

CITY_HIGHLIGHT_COLORS = {
    "NAT": "#fbbf24",
    "FOR": "#60a5fa",
    "REC": "#34d399",
    "JPA": "#a855f7",
}

FALLBACK_AIRPORT_COLORS = [
    "#2563eb",
    "#16a34a",
    "#ea580c",
    "#7c3aed",
    "#0891b2",
    "#be123c",
    "#0f766e",
    "#1d4ed8",
]
from maxmilhas import (
    buscar_menor_preco as buscar_menor_preco_maxmilhas,
    filtrar_precos_parcelados,
)
from config import load_env, now_local, now_local_iso

load_env()

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = os.getenv("SKYSCANNER_SECRET_KEY", "dev-change-this-secret")


def _env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variável obrigatória ausente no .env: {name}")
    return value


TELEGRAM_API_BASE_URL = _env_required("TELEGRAM_API_BASE_URL").rstrip("/")


DEFAULT_SCAN_INTERVAL = int(CONFIG.get("full_scan_seconds", 3 * 60 * 60))
DEFAULT_SCHEDULE_MINUTES = max(1, int(CONFIG.get("schedule_minutes", DEFAULT_SCAN_INTERVAL // 60)))
DEFAULT_SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", str(DEFAULT_SCHEDULE_MINUTES)))
AUTO_SCAN_ENABLED = os.getenv("SKYSCANNER_AUTO_SCAN", "1") == "1"
USER_SCAN_POLL_SECONDS = int(os.getenv("SKYSCANNER_USER_SCAN_POLL_SECONDS", "60"))
PANEL_RESTART_COMMAND = os.getenv("SKYSCANNER_RESTART_COMMAND", "").strip()
_scan_lock = threading.Lock()
_scan_last_run_at = None
SCAN_IMAGE_MAX_ASPECT = float(os.getenv("SCAN_IMAGE_MAX_ASPECT", "4.0"))
SCAN_IMAGE_SCALE = max(1.0, float(os.getenv("SCAN_IMAGE_SCALE", "1.25")))
SCAN_IMAGE_TARGET_WIDTH = max(720, int(os.getenv("SCAN_IMAGE_TARGET_WIDTH", "1280")))

AIRPORT_OPTIONS = [
    ("PVH", "PVH — Porto Velho (RO)"),
    ("BPS", "BPS — Porto Seguro (BA)"),
    ("RIO", "RIO — Rio de Janeiro (RJ)"),
    ("SAO", "SAO — São Paulo (SP)"),
    ("BSB", "BSB — Brasília (DF)"),
    ("CGB", "CGB — Cuiabá (MT)"),
    ("GYN", "GYN — Goiânia (GO)"),
    ("MCZ", "MCZ — Maceió (AL)"),
    ("AJU", "AJU — Aracaju (SE)"),
    ("SSA", "SSA — Salvador (BA)"),
    ("FOR", "FOR — Fortaleza (CE)"),
    ("SLZ", "SLZ — São Luís (MA)"),
    ("CGR", "CGR — Campo Grande (MS)"),
    ("BHZ", "BHZ — Belo Horizonte (MG)"),
    ("BEL", "BEL — Belém (PA)"),
    ("JPA", "JPA — João Pessoa (PB)"),
    ("CWB", "CWB — Curitiba (PR)"),
    ("REC", "REC — Recife (PE)"),
    ("THE", "THE — Teresina (PI)"),
    ("NAT", "NAT — Natal (RN)"),
    ("POA", "POA — Porto Alegre (RS)"),
    ("FLN", "FLN — Florianópolis (SC)"),
    ("VIX", "VIX — Vitória (ES)"),
    ("MAO", "MAO — Manaus (AM)"),
    ("RBR", "RBR — Rio Branco (AC)"),
    ("BVB", "BVB — Boa Vista (RR)"),
    ("MCP", "MCP — Macapá (AP)"),
    ("PMW", "PMW — Palmas (TO)"),
]


def build_restart_redirect(message: str, level: str = "info"):
    return redirect(url_for("painel", _anchor="cron", restart_status=level, restart_message=message))


def trigger_service_restart() -> tuple[bool, str, bool]:
    command = PANEL_RESTART_COMMAND
    if command:
        try:
            completed = subprocess.run(
                shlex.split(command),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if completed.returncode != 0:
                error_details = (completed.stderr or completed.stdout or "").strip()
                suffix = f" Detalhes: {error_details}" if error_details else ""
                return False, f"Falha ao executar reinício.{suffix}", False
            return True, "Comando de reinício executado.", False
        except Exception as exc:
            return False, f"Falha ao executar reinício: {exc}", False

    if os.getenv("WERKZEUG_RUN_MAIN") == "true":
        return False, "Reinício pelo próprio processo não é suportado com o reloader do Flask.", False

    try:
        python_bin = sys.executable
        subprocess.Popen([python_bin, *sys.argv], cwd=os.getcwd(), start_new_session=True)
        return True, "Novo processo iniciado. O processo atual será encerrado.", True
    except Exception as exc:
        return False, f"Falha ao iniciar novo processo: {exc}", False


def date_color_token(date_iso: str | None) -> tuple[str, str]:
    txt = (date_iso or "").strip()
    palette = [
        ("🔵", "azul"),
        ("🟢", "verde"),
        ("🟠", "laranja"),
        ("🟣", "roxo"),
        ("🟡", "amarelo"),
        ("🔴", "vermelho"),
        ("🟤", "marrom"),
    ]
    digits = [int(ch) for ch in txt if ch.isdigit()]
    if not digits:
        return "⚪", "cinza"
    return palette[sum(digits) % len(palette)]


def format_date_display(raw: str | None) -> str:
    txt = (raw or "").strip()
    if not txt:
        return txt
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(txt, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    return txt


def build_full_scan_message(parsed: list[dict], trigger: str = "manual") -> str:
    def _price_num(row):
        v = row.get("price")
        return v if isinstance(v, (int, float)) and v is not None else 10**12

    def _dedupe_sorted_rows(rows: list[dict]) -> list[dict]:
        seen = set()
        result = []
        for row in rows:
            key = (
                str(row.get("origin", "")).upper(),
                str(row.get("destination", "")).upper(),
                row.get("outbound_date", ""),
                row.get("inbound_date", "") or "",
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result

    PRICE_BAND_COLORS = {
        "excelente": "🟢",
        "bom": "🟡",
        "normal": "🔵",
        "caro": "🟤",
        "sem_preco": "⚪️",
        "novo": "🔵",
    }

    def _format_direction(rows: list[dict], best_row: dict | None, section_title: str, highlight_axis: str) -> list[str]:
        if not rows:
            return [section_title, "N/D"]

        grouped: dict[str, list[dict]] = {}
        for row in rows:
            date = row.get("outbound_date", "") or ""
            grouped.setdefault(date, []).append(row)

        ordered_dates = sorted(grouped.keys())
        section_lines = [section_title]
        for date_idx, date in enumerate(ordered_dates):
            group = grouped[date]
            header = f"📅 {format_date_display(date)}" if date else "📅 data pendente"
            section_lines.append(header)
            for row in group:
                is_best = best_row is row
                color = PRICE_BAND_COLORS.get((row.get("price_band") or "").lower(), "🔵")
                vendor = (row.get("best_vendor") or "").strip()
                vendor_txt = f" | vendedor: {vendor}" if vendor else ""
                best_note = " | melhor preço" if is_best else ""
                highlight_value = (row.get(highlight_axis) or "").upper()
                route_label = f"{row.get('origin')}→{row.get('destination')}"
                section_lines.append(
                    f"{route_label} | {color} {format_date_display(row.get('outbound_date'))} | {row.get('price_fmt')}{vendor_txt}{best_note}"
                )
            if date_idx != len(ordered_dates) - 1:
                section_lines.append("")
        return section_lines

    if not parsed:
        return (
            "- ────────── ✈️ CONSULTA COMPLETA ✈️ ────────── -\n"
            "Sem dados nesta execução."
        )

    rows = _dedupe_sorted_rows(parsed)

    lines = [
        "- ────────── ✈️ CONSULTA COMPLETA ✈️ ────────── -",
        f"Execução: {trigger}",
        "",
        *(_format_direction(rows, rows[0] if rows else None, "ROTAS (ordem de cadastro):", "destination")),
    ]

    total_ok = len([r for r in parsed if r.get("price") is not None])
    lines += ["", f"Resumo: {total_ok}/{len(parsed)} rotas com preço válido."]
    return "\n".join(lines)


def notify_full_scan(parsed: list[dict], trigger: str = "manual", send_fn=None, max_price: float | None = None) -> None:
    filtered = filter_rows_by_max_price(parsed, max_price)
    msg = build_full_scan_message(filtered, trigger=trigger)
    sender = send_fn or send_telegram_message
    try:
        sender(msg, image_rows=filtered)
    except TypeError:
        try:
            sender(msg)
        except Exception:
            pass
    except Exception:
        pass


def _build_user_routes(conn, user_id: int) -> list[RouteQuery]:
    rows = conn.execute(
        """
        SELECT origin, destination, outbound_date, inbound_date
        FROM user_routes
        WHERE user_id = ? AND active = 1
        ORDER BY id ASC
        """,
        (user_id,),
    ).fetchall()
    routes = []
    for r in rows:
        inbound = (r["inbound_date"] or "").strip()
        routes.append(
            RouteQuery(
                origin=(r["origin"] or "").upper(),
                destination=(r["destination"] or "").upper(),
                outbound_date=r["outbound_date"],
                inbound_date=inbound,
                trip_type="roundtrip" if inbound else "oneway",
            )
        )
    return routes


def _routes_for_request_user() -> list[RouteQuery]:
    user = current_user()
    if user:
        conn = get_auth_db()
        routes = _build_user_routes(conn, int(user["id"]))
        if routes:
            return routes
    return build_db_queries(get_db_path())


def _result_to_row(result: FlightResult, price_band: str) -> dict:
    return {
        "origin": result.origin,
        "destination": result.destination,
        "outbound_date": result.outbound_date,
        "inbound_date": result.inbound_date,
        "trip_type": result.trip_type,
        "price": result.price,
        "price_fmt": format_brl(result.price),
        "site": result.site,
        "currency": result.currency,
        "url": result.url,
        "notes": result.notes,
        "price_band": price_band,
        "best_vendor": getattr(result, "best_vendor", ""),
        "best_vendor_price": getattr(result, "best_vendor_price", None),
        "final_price_source": extract_final_price_source(result.notes),
    }


def _search_google_result(scraper: GoogleFlightsScraper, route: RouteQuery) -> FlightResult:
    return scraper.search(route)


def _search_maxmilhas_result(playwright, route: RouteQuery) -> FlightResult | None:
    if (route.inbound_date or "").strip():
        return None

    resultado = buscar_menor_preco_maxmilhas(
        origem=route.origin,
        destino=route.destination,
        data_ida_iso=route.outbound_date,
        playwright=playwright,
        salvar_arquivo_json=False,
        max_tentativas=1,
    )

    ok = bool(resultado and resultado.get("ok"))
    menor_preco = resultado.get("menor_preco") if resultado else None
    filtered_threshold = None
    final_threshold = None
    if ok and resultado:
        valores = []
        for raw in resultado.get("precos_encontrados") or []:
            try:
                valores.append(float(raw))
            except (TypeError, ValueError):
                pass
        valores = sorted(set(valores))
        limiar = float(CONFIG.get("maxmilhas_min_price", 400))
        candidatos = [valor for valor in valores if valor >= limiar]
        total_limit = float(CONFIG.get("maxmilhas_final_price_threshold", 1000))
        selected_price = None
        if candidatos:
            for price in candidatos:
                if price >= total_limit:
                    selected_price = price
                    break
            if selected_price is None:
                selected_price = candidatos[-1]
        if selected_price is not None:
            menor_preco = selected_price
            filtered_threshold = limiar
            final_threshold = total_limit
    vendedor = "MaxMilhas" if ok and menor_preco is not None else ""
    notes_parts = []
    if resultado:
        if resultado.get("motivo"):
            notes_parts.append(f"motivo={resultado['motivo']}")
        if resultado.get("url_final"):
            notes_parts.append(f"url_final={resultado['url_final']}")
        if ok and menor_preco is not None:
            notes_parts.append("final_price_source=maxmilhas")
            notes_parts.append(f"precos={resultado.get('precos_encontrados', [])}")
            if filtered_threshold is not None:
                notes_parts.append(f"maxmilhas_min_price={filtered_threshold}")
            if final_threshold is not None:
                notes_parts.append(f"maxmilhas_final_price_threshold={final_threshold}")

    return FlightResult(
        site="maxmilhas",
        origin=route.origin,
        destination=route.destination,
        outbound_date=route.outbound_date,
        inbound_date=route.inbound_date,
        trip_type=route.trip_type,
        price=menor_preco if ok else None,
        currency="BRL",
        url=(resultado or {}).get("url_final", ""),
        notes=" | ".join(notes_parts),
        best_vendor=vendedor,
        best_vendor_price=menor_preco if ok else None,
        booking_options_json=json.dumps(
            [{"vendor": "MaxMilhas", "price": menor_preco}] if ok and menor_preco is not None else [],
            ensure_ascii=False,
        ),
    )


def _store_result(db: Database, route: RouteQuery, result: FlightResult) -> dict:
    min_price, avg_price, _last_price = db.stats_for(route)
    band = classify_price(result.price, min_price, avg_price)
    db.save(result, band)
    return _result_to_row(result, band)


def _split_routes(routes: list[RouteQuery], chunks: int) -> list[list[RouteQuery]]:
    if not routes or chunks <= 0:
        return []
    chunk_size = ceil(len(routes) / chunks)
    return [routes[i * chunk_size:(i + 1) * chunk_size] for i in range(chunks)]


def run_scan_for_routes(routes: list[RouteQuery], on_row=None, sources: dict | None = None):
    if not routes:
        return []

    total = sum(2 if not (route.inbound_date or "").strip() else 1 for route in routes)
    requested_workers = CONFIG.get("scan_workers", 2)
    try:
        requested_workers = int(requested_workers)
    except (TypeError, ValueError):
        requested_workers = 2
    try:
        override_workers = int(os.getenv("SKYSCANNER_SCAN_WORKERS", requested_workers))
    except ValueError:
        override_workers = requested_workers
    worker_count = max(1, min(len(routes), override_workers))
    route_chunks = _split_routes(routes, worker_count)
    chunk_results: list[list[tuple[RouteQuery, FlightResult]] | None] = [None] * len(route_chunks)

    source_flags = sources or {"google_flights": True, "maxmilhas": True}

    def _scan_chunk(chunk_idx: int, chunk_routes: list[RouteQuery]) -> list[tuple[RouteQuery, FlightResult]]:
        if not chunk_routes:
            return []
        worker_results: list[tuple[RouteQuery, FlightResult]] = []
        user_data_dir = os.getenv("SKYSCANNER_USER_DATA_DIR", "/tmp/skyscanner-profile")
        chunk_user_dir = f"{user_data_dir}-worker-{chunk_idx}"
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=chunk_user_dir,
                headless=bool(CONFIG.get("headless", True)),
                locale="pt-BR",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            )
            scraper = GoogleFlightsScraper(browser)
            try:
                for route in chunk_routes:
                    if source_flags.get("google_flights", True):
                        google_result = _search_google_result(scraper, route)
                        worker_results.append((route, google_result))
                    if source_flags.get("maxmilhas", True):
                        maxmilhas_result = _search_maxmilhas_result(p, route)
                        if maxmilhas_result is not None:
                            worker_results.append((route, maxmilhas_result))
            finally:
                browser.close()
        return worker_results

    with _scan_lock:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(_scan_chunk, idx, chunk): idx
                for idx, chunk in enumerate(route_chunks)
            }
            for future in as_completed(futures):
                chunk_idx = futures[future]
                chunk_results[chunk_idx] = future.result()

    db = Database(get_db_path())
    parsed: list[dict] = []
    idx = 0
    try:
        for chunk in chunk_results:
            if not chunk:
                continue
            for route, result in chunk:
                row = _store_result(db, route, result)
                parsed.append(row)
                idx += 1
                if on_row:
                    on_row(idx, total, row)
        return parsed
    finally:
        db.conn.close()


def run_full_scan(on_row=None):
    global _scan_last_run_at
    parsed = run_scan_for_routes(build_db_queries(get_db_path()), on_row=on_row)
    _scan_last_run_at = now_local_iso(sep="T")
    return parsed


def _create_user_run(conn, user_id: int, trigger: str = "manual-user") -> int:
    cur = conn.execute(
        "INSERT INTO user_runs (user_id, started_at, status, summary, trigger) VALUES (?, ?, ?, ?, ?)",
        (user_id, now_local_iso(sep="T"), "running", "", trigger),
    )
    conn.commit()
    return int(cur.lastrowid)


def _finish_user_run(conn, run_id: int, status: str, summary: str) -> None:
    conn.execute(
        "UPDATE user_runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?",
        (now_local_iso(sep="T"), status, summary, run_id),
    )
    conn.commit()


def get_scheduler_settings() -> tuple[int, int, float | None]:
    conn = sqlite3.connect(auth_db_path())
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT cron_enabled, scan_interval_minutes, max_price_display FROM app_settings WHERE id = 1"
        ).fetchone()
        if not row:
            return 1, max(1, DEFAULT_SCAN_INTERVAL_MINUTES), None
        enabled = 1 if int(row["cron_enabled"] or 0) == 1 else 0
        interval = max(1, int(row["scan_interval_minutes"] or DEFAULT_SCAN_INTERVAL_MINUTES))
        max_price = row["max_price_display"]
        return enabled, interval, float(max_price) if max_price is not None else None
    finally:
        conn.close()


def _user_has_running_scan(conn, user_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM user_runs
        WHERE user_id = ? AND status = 'running'
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    return bool(row)


def run_user_scan(user_id: int, trigger: str = "manual-user", notify: bool = True):
    conn = sqlite3.connect(auth_db_path())
    conn.row_factory = sqlite3.Row
    run_id = _create_user_run(conn, user_id, trigger=trigger)
    try:
        routes = _build_user_routes(conn, user_id)
        if not routes:
            routes = build_db_queries(get_db_path())
        parsed = run_scan_for_routes(routes)
        max_price = get_global_max_price_limit()
        parsed_for_display = filter_rows_by_max_price(parsed, max_price)
        msg = build_full_scan_message(parsed_for_display, trigger=trigger)
        if notify:
            send_user_telegram_message(user_id, msg, image_rows=parsed_for_display)
        total_ok = len([r for r in parsed_for_display if r.get("price") is not None])
        summary = f"ok: {total_ok}/{len(parsed_for_display)} exibidos"
        _finish_user_run(conn, run_id, "ok", summary)
        return {"status": "ok", "summary": summary, "parsed": parsed_for_display}
    except Exception as e:
        _finish_user_run(conn, run_id, "error", str(e)[:500])
        raise
    finally:
        conn.close()




def _auto_scan_loop():
    while True:
        try:
            enabled, interval_minutes, max_price = get_scheduler_settings()
            if enabled != 1:
                time.sleep(max(30, USER_SCAN_POLL_SECONDS))
                continue
            parsed = run_full_scan()
            notify_full_scan(parsed, trigger="agendada", max_price=max_price)
            print(f"[auto-scan] consulta completa executada em {_scan_last_run_at}")
        except Exception as e:
            print(f"[auto-scan] erro: {e}")
        _, interval_minutes, _ = get_scheduler_settings()
        time.sleep(max(60, interval_minutes * 60))


def start_auto_scan_if_needed():
    if not AUTO_SCAN_ENABLED:
        print("[auto-scan] desativado por SKYSCANNER_AUTO_SCAN=0")
        return

    is_reloader_main = os.getenv("WERKZEUG_RUN_MAIN") == "true"
    is_debug = os.getenv("FLASK_DEBUG") == "1"
    if is_debug and not is_reloader_main:
        return

    t = threading.Thread(target=_auto_scan_loop, daemon=True)
    t.start()
    print("[auto-scan] ligado: intervalo global pelo BD (app_settings.scan_interval_minutes)")


def get_db_path() -> str:
    configured = str(CONFIG.get("db_path", "flight_tracker_browser.db"))
    # Em Vercel/Lambda, /var/task é read-only; use /tmp (gravável)
    if os.getenv("VERCEL") or configured.startswith("/var/task"):
        return "/tmp/flight_tracker_browser.db"
    return configured


def send_telegram_message_to(text: str, token: str | None = None, chat_id: str | None = None) -> None:
    token = token or os.getenv("TELEGRAM_BOT_TOKEN") or CONFIG.get("telegram_bot_token")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID") or CONFIG.get("telegram_chat_id")
    if not token or not chat_id:
        return
    base_url = TELEGRAM_API_BASE_URL
    url = f"{base_url}/bot{token}/sendMessage"
    requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=20).raise_for_status()


def _load_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    return ImageFont.load_default()


def _group_scan_rows_for_image(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    return [("ROTAS", rows)] if rows else []


def _best_vendor_label(row: dict) -> str:
    def _pretty_vendor_name(raw: str) -> str:
        txt = (raw or "").strip()
        if not txt:
            return "N/D"
        normalized = txt.lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "google_flights": "Google Flights",
            "google": "Google Flights",
            "maxmilhas": "MaxMilhas",
            "latam": "LATAM",
            "gol": "GOL",
            "azul": "Azul",
            "decolar": "Decolar",
            "zupper": "Zupper",
            "booking": "Booking.com",
            "kayak": "KAYAK",
            "123milhas": "123 Milhas",
            "123_milhas": "123 Milhas",
            "viajanet": "ViajaNet",
            "voeazul": "Azul",
            "smiles": "Smiles",
        }
        if normalized in aliases:
            return aliases[normalized]
        return txt.replace("_", " ").strip().title()

    vendor = (row.get("best_vendor") or "").strip()
    if not vendor:
        raw_booking = row.get("booking_options_json")
        if isinstance(raw_booking, str) and raw_booking.strip():
            try:
                booking_options = json.loads(raw_booking)
                if isinstance(booking_options, list) and booking_options:
                    first_vendor = (booking_options[0] or {}).get("vendor")
                    if isinstance(first_vendor, str):
                        vendor = first_vendor.strip()
            except Exception:
                pass
    if not vendor:
        vendor = (row.get("site") or "").strip() or "N/D"
    vendor_label = _pretty_vendor_name(vendor)
    vendor_price = row.get("best_vendor_price")
    if isinstance(vendor_price, (int, float)):
        return f"{vendor_label} ({format_brl(vendor_price)})"
    return vendor_label


def _airport_code_color(code: str, default_color: str) -> str:
    airport = (code or "").strip().upper()
    if not airport:
        return default_color
    if airport in CITY_HIGHLIGHT_COLORS:
        return CITY_HIGHLIGHT_COLORS[airport]
    idx = sum(ord(ch) for ch in airport) % len(FALLBACK_AIRPORT_COLORS)
    return FALLBACK_AIRPORT_COLORS[idx]


def build_scan_results_image(rows: list[dict]) -> str | None:
    groups = _group_scan_rows_for_image(rows)
    if not groups:
        return None

    def scaled(value: int) -> int:
        return max(1, int(round(value * SCAN_IMAGE_SCALE)))

    title_font = _load_font(scaled(20), bold=True)
    header_font = _load_font(scaled(16), bold=True)
    body_font = _load_font(scaled(15))
    small_font = _load_font(scaled(14))

    padding_x = scaled(14)
    padding_y = scaled(14)
    row_h = scaled(36)
    section_h = scaled(34)
    title_h = scaled(30)
    meta_h = scaled(24)
    col_widths = [scaled(170), scaled(130), scaled(125), scaled(290)]
    headers = ["Rota", "Data voo", "Preço", "Onde comprar mais barato"]
    height = (
        padding_y * 2
        + title_h
        + meta_h
        + row_h
        + sum(section_h + len(items) * row_h for _, items in groups)
        + 24
    )

    table_w = sum(col_widths)
    width = table_w + padding_x * 2
    max_aspect = max(1.0, SCAN_IMAGE_MAX_ASPECT)
    if width / max(height, 1) > max_aspect:
        height = int(width / max_aspect)

    image = Image.new("RGB", (width, height), "#f4f6f8")
    draw = ImageDraw.Draw(image)

    colors = {
        "text": "#1f2937",
        "muted": "#6b7280",
        "header_bg": "#e5e7eb",
        "section_bg": "#d1d5db",
        "section_return_bg": "#f4e7bd",
        "border": "#cbd5e1",
        "row_a": "#ffffff",
        "row_b": "#f8fafc",
        "price": "#0f8a5f",
        "date_badge": "#dbeafe",
        "date_badge_return": "#fef3c7",
    }

    x0 = padding_x
    y = padding_y
    draw.text((x0, y), "Consulta completa", font=title_font, fill=colors["text"])
    y += title_h
    draw.text((x0, y), now_local().strftime("%Y-%m-%d %H:%M"), font=small_font, fill=colors["muted"])
    y += meta_h

    x = x0
    for idx, header in enumerate(headers):
        w = col_widths[idx]
        draw.rectangle([x, y, x + w, y + row_h], fill=colors["header_bg"], outline=colors["border"])
        draw.text((x + scaled(10), y + scaled(9)), header, font=header_font, fill=colors["text"])
        x += w
    y += row_h

    for group_idx, (title, items) in enumerate(groups):
        section_bg = colors["section_return_bg"] if title.startswith("VOLTAS") else colors["section_bg"]
        draw.rectangle([x0, y, x0 + table_w, y + section_h], fill=section_bg, outline=colors["border"])
        caption = f"{title} (ordem de cadastro)"
        caption_bbox = draw.textbbox((0, 0), caption, font=header_font)
        caption_width = caption_bbox[2] - caption_bbox[0]
        caption_x = x0 + max(0, int((table_w - caption_width) / 2))
        draw.text((caption_x, y + scaled(7)), caption, font=header_font, fill=colors["text"])
        y += section_h

        for item_idx, row in enumerate(items):
            fill = colors["row_a"] if item_idx % 2 == 0 else colors["row_b"]
            draw.rectangle([x0, y, x0 + table_w, y + row_h], fill=fill, outline=colors["border"])

            origin_txt = (row.get("origin") or "").upper()
            destination_txt = (row.get("destination") or "").upper()
            origin_color = _airport_code_color(origin_txt, colors["text"])
            destination_color = _airport_code_color(destination_txt, colors["text"])
            origin_part = f"{origin_txt} → "
            draw.text((x0 + scaled(10), y + scaled(9)), origin_part, font=body_font, fill=origin_color)
            dest_x = int(x0 + scaled(10) + draw.textlength(origin_part, font=body_font))
            draw.text((dest_x, y + scaled(9)), destination_txt, font=body_font, fill=destination_color)

            date_txt = format_date_display(str(row.get("outbound_date") or ""))
            price_txt = row.get("price_fmt") or format_brl(row.get("price"))
            vendor_txt = _best_vendor_label(row)

            date_x = x0 + col_widths[0] + scaled(10)
            badge_fill = colors["date_badge_return"] if title.startswith("VOLTAS") else colors["date_badge"]
            badge_bbox = draw.textbbox((0, 0), date_txt, font=small_font)
            badge_w = (badge_bbox[2] - badge_bbox[0]) + scaled(16)
            draw.rounded_rectangle(
                [date_x, y + scaled(7), date_x + badge_w, y + scaled(28)],
                radius=scaled(8),
                fill=badge_fill,
            )
            draw.text((date_x + scaled(8), y + scaled(10)), date_txt, font=small_font, fill=colors["text"])

            price_x = x0 + col_widths[0] + col_widths[1] + scaled(10)
            draw.text((price_x, y + scaled(9)), price_txt, font=header_font, fill=colors["price"])

            vendor_x = x0 + col_widths[0] + col_widths[1] + col_widths[2] + scaled(10)
            draw.text((vendor_x, y + scaled(9)), vendor_txt[:34], font=body_font, fill=colors["text"])
            y += row_h

        if group_idx != len(groups) - 1:
            y += scaled(8)
    if image.width < SCAN_IMAGE_TARGET_WIDTH:
        target_h = int(round(image.height * (SCAN_IMAGE_TARGET_WIDTH / image.width)))
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        image = image.resize((SCAN_IMAGE_TARGET_WIDTH, max(1, target_h)), resample=resample)

    tmp = NamedTemporaryFile(prefix="telegram_scan_", suffix=".png", delete=False)
    tmp.close()
    image.save(tmp.name, format="PNG")
    return tmp.name


def send_telegram_photo_to(image_path: str, caption: str | None = None, token: str | None = None, chat_id: str | None = None) -> None:
    token = token or os.getenv("TELEGRAM_BOT_TOKEN") or CONFIG.get("telegram_bot_token")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID") or CONFIG.get("telegram_chat_id")
    if not token or not chat_id or not image_path or not os.path.exists(image_path):
        return
    base_url = TELEGRAM_API_BASE_URL
    url = f"{base_url}/bot{token}/sendPhoto"
    with open(image_path, "rb") as image_file:
        requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption or ""},
            files={"photo": image_file},
            timeout=60,
        ).raise_for_status()


def send_telegram_message(text: str, image_rows: list[dict] | None = None) -> None:
    send_telegram_message_to(text)
    image_path = build_scan_results_image(image_rows or [])
    if not image_path:
        return
    try:
        send_telegram_photo_to(image_path)
    finally:
        try:
            os.remove(image_path)
        except OSError:
            pass


def send_user_telegram_message(user_id: int, text: str, image_rows: list[dict] | None = None) -> None:
    conn = sqlite3.connect(auth_db_path())
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT bot_token, chat_id FROM user_telegram WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return
        token = (row["bot_token"] or "").strip()
        chat_id = (row["chat_id"] or "").strip()
        if not token or not chat_id:
            return
        send_telegram_message_to(text, token=token, chat_id=chat_id)
        image_path = build_scan_results_image(image_rows or [])
        if not image_path:
            return
        try:
            send_telegram_photo_to(image_path, token=token, chat_id=chat_id)
        finally:
            try:
                os.remove(image_path)
            except OSError:
                pass
    finally:
        conn.close()


def extract_final_price_source(notes: str | None) -> str:
    txt = (notes or "")
    m = re.search(r"final_price_source=([^|]+)", txt)
    if not m:
        return ""
    return (m.group(1) or "").strip()


def _extract_maxmilhas_prices_from_notes(notes: str | None) -> list[float]:
    txt = notes or ""
    match = re.search(r"precos=\[([^\]]+)\]", txt)
    if not match:
        return []

    prices = []
    for raw in match.group(1).split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            prices.append(float(raw))
        except ValueError:
            continue
    return prices


def normalize_maxmilhas_history() -> int:
    db = Database(get_db_path())
    rows = db.conn.execute(
        """
        SELECT id, price, best_vendor_price, notes
        FROM results
        WHERE site = 'maxmilhas' AND notes LIKE '%precos=[%'
        """
    ).fetchall()

    updated = 0
    for row in rows:
        prices = _extract_maxmilhas_prices_from_notes(row["notes"])
        if not prices:
            continue
        filtered = filtrar_precos_parcelados(prices)
        if not filtered:
            continue
        expected = min(filtered)
        current = row["price"]
        if current is None or abs(float(current) - float(expected)) < 0.01:
            continue
        db.conn.execute(
            "UPDATE results SET price = ?, best_vendor_price = ? WHERE id = ?",
            (expected, expected, row["id"]),
        )
        updated += 1

    if updated:
        db.conn.commit()
    return updated


def get_user_max_display_price(user_id: int | None) -> float | None:
    _ = user_id
    return get_global_max_price_limit()


def filter_rows_by_max_price(rows: list[dict], max_price: float | None) -> list[dict]:
    if max_price is None:
        return rows
    return [
        row for row in rows
        if row.get("price") is None or float(row["price"]) <= max_price
    ]


def get_global_max_price_limit() -> float | None:
    _, _, max_price = get_scheduler_settings()
    return max_price


def _to_route(query_args) -> RouteQuery:
    origin = query_args.get("origin", CONFIG.get("origin", "PVH")).upper()
    destination = query_args.get("destination", "JPA").upper()
    outbound_date = query_args.get("outbound_date", "")
    inbound_date = query_args.get("inbound_date", "")
    trip_type = "roundtrip" if inbound_date else "oneway"

    if not outbound_date:
        raise ValueError("Parâmetro obrigatório: outbound_date (YYYY-MM-DD)")

    return RouteQuery(
        origin=origin,
        destination=destination,
        outbound_date=outbound_date,
        inbound_date=inbound_date,
        trip_type=trip_type,
    )


def _resolve_requested_sources(query_args, route: RouteQuery) -> list[str]:
    fonte = (query_args.get("fonte") or "").strip().lower()
    if fonte in {"maxmilhas"}:
        return [] if (route.inbound_date or "").strip() else ["maxmilhas"]
    if fonte in {"google", "google_flights"}:
        return ["google_flights"]
    sources = ["google_flights"]
    if not (route.inbound_date or "").strip():
        sources.append("maxmilhas")
    return sources


@app.route("/", methods=["GET"])
def index():
    if session.get("user_id"):
        return redirect(url_for("painel"))
    return redirect(url_for("auth_login"))


@app.route("/app", methods=["GET"])
def app_front():
    static_path = Path(app.static_folder or "static") / "index.html"
    html = static_path.read_text(encoding="utf-8")
    return render_template_string(html)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "voobot-monitor"})


@app.route("/rotas", methods=["GET"])
def rotas():
    routes = _routes_for_request_user()
    return jsonify(
        {
            "count": len(routes),
            "rotas": [
                {
                    "origin": r.origin,
                    "destination": r.destination,
                    "outbound_date": r.outbound_date,
                    "inbound_date": r.inbound_date,
                    "trip_type": r.trip_type,
                }
                for r in routes
            ],
        }
    )




@app.route("/consulta", methods=["GET"])
def consulta():
    try:
        route = _to_route(request.args)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    user = current_user()
    max_price = get_user_max_display_price(int(user["id"])) if user else None

    db = Database(get_db_path())
    requested_sources = _resolve_requested_sources(request.args, route)
    if not requested_sources:
        return jsonify({"error": "A MaxMilhas atualmente só está habilitada para consultas somente ida."}), 400

    results = []
    with sync_playwright() as p:
        browser = None
        scraper = None
        if "google_flights" in requested_sources:
            browser = p.chromium.launch(headless=bool(CONFIG.get("headless", True)))
            scraper = GoogleFlightsScraper(browser)

        try:
            for source in requested_sources:
                if source == "google_flights":
                    result = _search_google_result(scraper, route)
                else:
                    result = _search_maxmilhas_result(p, route)

                if result is None:
                    continue

                row = _store_result(db, route, result)
                results.append(row)
        finally:
            if browser:
                browser.close()

    if not results:
        return jsonify({"error": "Nenhum resultado foi retornado para a rota consultada."}), 502

    results = filter_rows_by_max_price(results, max_price)
    if not results:
        return jsonify({"error": "Nenhum resultado está dentro do valor máximo configurado."}), 200

    chosen = min(
        results,
        key=lambda item: item["price"] if isinstance(item.get("price"), (int, float)) and item.get("price") is not None else 10**12,
    )
    min_price, avg_price, last_price = db.stats_for(route)

    try:
        detalhes = []
        for item in results:
            vendor_line = ""
            if item.get("best_vendor"):
                vendor_line = f" | comprar: {item['best_vendor']} ({format_brl(item.get('best_vendor_price'))})"
            detalhes.append(f"{item['site']}: {item['price_fmt']}{vendor_line}")
        resumo = (
            "────────── ✈️ CONSULTA RÁPIDA ✈️ ──────────\n"
            f"Rota: {route.origin} → {route.destination}\n"
            f"Data: {date_color_token(route.outbound_date)[0]} {format_date_display(route.outbound_date)}\n"
            + (f" / {format_date_display(route.inbound_date)}" if route.inbound_date else "")
            + "\n"
            + "Resultados:\n"
            + "\n".join(detalhes)
        )
        send_telegram_message(resumo)
    except Exception:
        pass

    return jsonify(
        {
            "rota": {
                "origin": route.origin,
                "destination": route.destination,
                "outbound_date": route.outbound_date,
                "inbound_date": route.inbound_date,
                "trip_type": route.trip_type,
            },
            "resultado": {
                "price": chosen["price"],
                "price_fmt": chosen["price_fmt"],
                "price_band": chosen["price_band"],
                "site": chosen["site"],
                "currency": "BRL",
                "url": chosen.get("url", ""),
                "notes": chosen["notes"],
                "best_vendor": chosen["best_vendor"],
                "best_vendor_price": chosen["best_vendor_price"],
                "final_price_source": chosen["final_price_source"],
            },
            "resultados": results,
            "historico": {
                "min_price": min_price,
                "avg_price": avg_price,
                "last_price": last_price,
            },
        }
    )


@app.route("/consulta-maxmilhas", methods=["GET"])
def consulta_maxmilhas():
    args = request.args.to_dict(flat=True)
    args["fonte"] = "maxmilhas"
    with app.test_request_context(query_string=args):
        return consulta()


@app.route("/historico", methods=["GET"])
def historico():
    limit = request.args.get("limit", default=20, type=int)
    limit = max(1, min(limit, 200))

    db_path = Path(get_db_path())
    if not db_path.exists():
        return jsonify({"total": 0, "items": []})

    db = Database(str(db_path))
    rows = db.conn.execute(
        """
        SELECT created_at, site, origin, destination, outbound_date, inbound_date,
               price, currency, price_band, notes, url,
               best_vendor, best_vendor_price, booking_options_json
        FROM results
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    items = [dict(r) for r in rows]
    for item in items:
        item["final_price_source"] = extract_final_price_source(item.get("notes"))
    user = current_user()
    max_price = get_user_max_display_price(int(user["id"])) if user else None
    items = filter_rows_by_max_price(items, max_price)
    return jsonify({"total": len(items), "items": items})


@app.route("/historico/limpar", methods=["POST"])
def limpar_historico():
    db = Database(get_db_path())
    deleted = db.conn.execute("DELETE FROM results").rowcount
    db.conn.commit()
    return jsonify({"status": "ok", "deleted": deleted})


@app.route("/cron", methods=["GET"])
def cron():
    parsed = run_full_scan()
    user = current_user()
    max_price = get_user_max_display_price(int(user["id"])) if user else None
    parsed_filtered = filter_rows_by_max_price(parsed, max_price)
    notify_full_scan(parsed, trigger="manual", max_price=max_price)
    return jsonify({"status": "ok", "resultados": parsed_filtered, "last_run_at": _scan_last_run_at})


@app.route("/cron-stream", methods=["GET"])
def cron_stream():
    def event_stream():
        user = current_user()
        max_price = get_user_max_display_price(int(user["id"])) if user else None
        routes = _routes_for_request_user()
        total = sum(2 if not (route.inbound_date or "").strip() else 1 for route in routes)
        yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"

        # evita concorrência com auto-scan/execuções manuais
        if not _scan_lock.acquire(blocking=False):
            yield f"data: {json.dumps({'type': 'error', 'message': 'Já existe uma varredura em andamento. Tente novamente em instantes.'})}\n\n"
            return

        try:
            parsed = []
            db = Database(get_db_path())
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=bool(CONFIG.get("headless", True)))
                scraper = GoogleFlightsScraper(browser)

                for idx, route in enumerate(routes, start=1):
                    result = _search_google_result(scraper, route)
                    row = _store_result(db, route, result)
                    parsed.append(row)
                    if row.get("price") is None or max_price is None or float(row["price"]) <= max_price:
                        payload = {"type": "row", "index": len(parsed), "total": total, "item": row}
                        yield f"data: {json.dumps(payload)}\n\n"
                        time.sleep(0.05)

                    maxmilhas_result = _search_maxmilhas_result(p, route)
                    if maxmilhas_result is not None:
                        row = _store_result(db, route, maxmilhas_result)
                        parsed.append(row)
                        if row.get("price") is None or max_price is None or float(row["price"]) <= max_price:
                            payload = {"type": "row", "index": len(parsed), "total": total, "item": row}
                            yield f"data: {json.dumps(payload)}\n\n"
                            time.sleep(0.05)

                browser.close()

            notify_full_scan(parsed, trigger="completa", max_price=max_price)
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            if _scan_lock.locked():
                _scan_lock.release()

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )




@app.route("/app-page", methods=["GET"])
def app_page():
    if not session.get("user_id"):
        return redirect(url_for("auth_login"))
    return render_template_string(
        """
        <!doctype html>
        <html lang='pt-BR'>
        <head>
          <meta charset='utf-8'>
          <meta name='viewport' content='width=device-width, initial-scale=1'>
          <title>App Consultas</title>
          <link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css' rel='stylesheet'>
        </head>
        <body class='bg-light'>
          <nav class='navbar navbar-dark bg-dark'>
            <div class='container-fluid'>
              <span class='navbar-brand mb-0 h1'>App Consultas</span>
              <a class='btn btn-outline-light btn-sm' href='{{ url_for("painel") }}'>Voltar ao Painel</a>
            </div>
          </nav>
          <div class='container-fluid p-0'>
            <iframe src='{{ url_for("app_front") }}' style='width:100%;height:92vh;border:0;'></iframe>
          </div>
        </body>
        </html>
        """,
    )

def auth_db_path() -> str:
    return get_db_path()


def get_auth_db():
    if "auth_db" not in g:
        conn = sqlite3.connect(auth_db_path())
        conn.row_factory = sqlite3.Row
        g.auth_db = conn
    return g.auth_db


def _current_iso_ts() -> str:
    return now_local_iso(sep="T")

def _ensure_user_telegram_defaults(conn, user_id: int) -> None:
    exists = conn.execute("SELECT 1 FROM user_telegram WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
    if exists:
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN") or CONFIG.get("telegram_bot_token")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or CONFIG.get("telegram_chat_id")
    if not token and not chat_id:
        return
    conn.execute("INSERT INTO user_telegram (user_id, bot_token, chat_id, updated_at) VALUES (?, ?, ?, ?)",
        (user_id, token or "", chat_id or "", _current_iso_ts()),
    )
    conn.commit()

def ensure_user_defaults(conn, user_id: int) -> None:
    _ensure_user_telegram_defaults(conn, user_id)






def init_auth_tables():
    db = sqlite3.connect(auth_db_path())
    cur = db.cursor()
    cur.execute("DROP TABLE IF EXISTS user_cron")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            outbound_date TEXT NOT NULL,
            inbound_date TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_telegram (
            user_id INTEGER PRIMARY KEY,
            bot_token TEXT,
            chat_id TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cron_enabled INTEGER DEFAULT 1,
            scan_interval_minutes INTEGER DEFAULT 60,
            max_price_display REAL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            summary TEXT,
            trigger TEXT DEFAULT 'manual-user',
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    for ddl in [
        "ALTER TABLE user_runs ADD COLUMN trigger TEXT DEFAULT 'manual-user'",
    ]:
        try:
            cur.execute(ddl)
        except sqlite3.OperationalError:
            pass
    cur.execute(
        """
        INSERT OR IGNORE INTO app_settings (id, cron_enabled, scan_interval_minutes, max_price_display, updated_at)
        VALUES (1, 1, ?, NULL, ?)
        """,
        (max(1, DEFAULT_SCAN_INTERVAL_MINUTES), now_local_iso(sep="T")),
    )
    cur.execute(
        """
        UPDATE app_settings
        SET scan_interval_minutes = COALESCE(scan_interval_minutes, ?),
            cron_enabled = COALESCE(cron_enabled, 1),
            updated_at = COALESCE(updated_at, ?)
        WHERE id = 1
        """,
        (max(1, DEFAULT_SCAN_INTERVAL_MINUTES), now_local_iso(sep="T")),
    )

    db.commit()
    db.close()


@app.teardown_appcontext
def close_auth_db(_exc):
    db = g.pop("auth_db", None)
    if db is not None:
        db.close()


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth_login"))
        return fn(*args, **kwargs)

    return wrapper


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_auth_db()
    return db.execute("SELECT id, email FROM users WHERE id = ?", (uid,)).fetchone()


@app.route("/auth/register", methods=["GET", "POST"])
def auth_register():
    error = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or len(password) < 6:
            error = "Informe email válido e senha com pelo menos 6 caracteres."
        else:
            db = get_auth_db()
            try:
                db.execute(
                    "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                    (email, generate_password_hash(password), now_local_iso(sep="T")),
                )
                db.commit()
                return redirect(url_for("auth_login"))
            except sqlite3.IntegrityError:
                error = "Esse email já está cadastrado."

    return render_template_string(
        """
        <!doctype html>
        <html lang='pt-BR'>
        <head>
          <meta charset='utf-8'>
          <meta name='viewport' content='width=device-width, initial-scale=1'>
          <title>Cadastro | VooBot Admin</title>
          <link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css' rel='stylesheet'>
        </head>
        <body class='bg-light d-flex align-items-center' style='min-height:100vh;'>
          <div class='container'>
            <div class='row justify-content-center'>
              <div class='col-md-5'>
                <div class='card shadow-sm'>
                  <div class='card-header bg-primary text-white'>Cadastro</div>
                  <div class='card-body'>
                    <form method='post'>
                      <div class='mb-3'><input class='form-control' name='email' type='email' placeholder='Email' required></div>
                      <div class='mb-3'><input class='form-control' name='password' type='password' placeholder='Senha (mín 6)' required></div>
                      <button class='btn btn-primary w-100' type='submit'>Cadastrar</button>
                    </form>
                    {% if error %}<div class='alert alert-danger mt-3 mb-0'>{{error}}</div>{% endif %}
                    <div class='mt-3 text-center'><a href='{{ url_for("auth_login") }}'>Já tenho login</a></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </body>
        </html>
        """,
        error=error,
    )


@app.route("/auth/login", methods=["GET", "POST"])
def auth_login():
    error = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_auth_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            error = "Login inválido."
        else:
            session["user_id"] = user["id"]
            return redirect(url_for("painel"))

    return render_template_string(
        """
        <!doctype html>
        <html lang='pt-BR'>
        <head>
          <meta charset='utf-8'>
          <meta name='viewport' content='width=device-width, initial-scale=1'>
          <title>Login | VooBot Admin</title>
          <link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css' rel='stylesheet'>
        </head>
        <body class='bg-light d-flex align-items-center' style='min-height:100vh;'>
          <div class='container'>
            <div class='row justify-content-center'>
              <div class='col-md-5'>
                <div class='card shadow-sm'>
                  <div class='card-header bg-dark text-white'>VooBot Admin</div>
                  <div class='card-body'>
                    <form method='post'>
                      <div class='mb-3'><input class='form-control' name='email' type='email' placeholder='Email' required></div>
                      <div class='mb-3'><input class='form-control' name='password' type='password' placeholder='Senha' required></div>
                      <button class='btn btn-dark w-100' type='submit'>Entrar</button>
                    </form>
                    {% if error %}<div class='alert alert-danger mt-3 mb-0'>{{error}}</div>{% endif %}
                    <div class='mt-3 text-center'><a href='{{ url_for("auth_register") }}'>Criar conta</a></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </body>
        </html>
        """,
        error=error,
    )


@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect(url_for("auth_login"))


@app.route("/painel", methods=["GET"])
@login_required
def painel():
    db = get_auth_db()
    user = current_user()
    restart_status = (request.args.get("restart_status") or "").strip().lower()
    restart_message = (request.args.get("restart_message") or "").strip()
    ensure_user_defaults(db, user["id"])
    routes = db.execute(
        "SELECT id, origin, destination, outbound_date, inbound_date, active FROM user_routes WHERE user_id = ? ORDER BY id DESC",
        (user["id"],),
    ).fetchall()
    tg = db.execute("SELECT bot_token, chat_id FROM user_telegram WHERE user_id = ?", (user["id"],)).fetchone()
    cron = db.execute("SELECT cron_enabled, scan_interval_minutes, max_price_display FROM app_settings WHERE id = 1").fetchone()
    cron_minutes = max(1, DEFAULT_SCAN_INTERVAL_MINUTES)
    cron_max_price = ""
    if cron is not None:
        schedule_minutes = cron["scan_interval_minutes"]
        if schedule_minutes is not None:
            cron_minutes = max(1, int(schedule_minutes))
        if cron["max_price_display"] is not None:
            cron_max_price = str(int(cron["max_price_display"])) if float(cron["max_price_display"]).is_integer() else str(cron["max_price_display"])
    last_run = db.execute("SELECT started_at, finished_at, status, summary FROM user_runs WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
    default_tg_bot = os.getenv("TELEGRAM_BOT_TOKEN") or CONFIG.get("telegram_bot_token", "")
    default_tg_chat = os.getenv("TELEGRAM_CHAT_ID") or CONFIG.get("telegram_chat_id", "")

    return render_template_string(
        """
        <!doctype html>
        <html lang='pt-BR'>
        <head>
          <meta charset='utf-8'>
          <meta name='viewport' content='width=device-width, initial-scale=1'>
          <title>Painel Admin | VooBot</title>
          <link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css' rel='stylesheet'>
          <link href='https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css' rel='stylesheet'>
          <style>
            body { background:#f4f6f9; }
            .sidebar { min-height: 100vh; background: #343a40; }
            .sidebar a { color: #c2c7d0; text-decoration: none; display:block; padding:.65rem 1rem; }
            .sidebar a:hover { background:#495057; color:#fff; }
            .brand { color:#fff; font-weight:700; padding:1rem; border-bottom:1px solid #495057; }
            .topbar { background:#fff; border-bottom:1px solid #dee2e6; }
            .kpi { border-left:4px solid #0d6efd; }
            body.dark-mode { background:#1f2d3d; color:#dee2e6; }
            body.dark-mode .card, body.dark-mode .topbar { background:#2c3b4b; color:#dee2e6; border-color:#3d4b5a; }
            body.dark-mode .text-muted { color:#adb5bd !important; }
            body.sidebar-collapsed .sidebar { width: 72px; }
            body.sidebar-collapsed .sidebar .brand, body.sidebar-collapsed .sidebar a { text-align:center; }
            body.sidebar-collapsed .sidebar a { font-size:0; }
            body.sidebar-collapsed .sidebar a i { font-size:1rem; margin:0 !important; }
          </style>
        </head>
        <body class='bg-light'>
          <div class='container-fluid'>
            <div class='row'>
              <aside class='col-md-3 col-lg-2 p-0 sidebar'>
                <div class='brand'><i class='bi bi-activity'></i> VooBot Admin</div>
                <a href='#rotas'><i class='bi bi-signpost-split me-2'></i>Rotas</a>
                <a href='#consultas'><i class='bi bi-window me-2'></i>Consultas</a>
                <a href='#telegram'><i class='bi bi-telegram me-2'></i>Telegram</a>
                <a href='#cron'><i class='bi bi-clock-history me-2'></i>Cron</a>
                <a href='{{ url_for("auth_logout") }}'><i class='bi bi-box-arrow-right me-2'></i>Sair</a>
              </aside>
              <main class='col-md-9 col-lg-10 p-0'>
                <div class='topbar d-flex justify-content-between align-items-center px-4 py-3'>
                  <div><strong>Painel</strong> <span class='text-muted'>/ Dashboard</span></div>
                  <div class='d-flex align-items-center gap-2'>
                    <button class='btn btn-sm btn-outline-secondary' type='button' onclick='toggleSidebar()'><i class='bi bi-list'></i></button>
                    <button class='btn btn-sm btn-outline-secondary' type='button' onclick='toggleTheme()'><i class='bi bi-moon-stars'></i></button>
                    <div class='text-muted small'>{{user['email']}}</div>
                  </div>
                </div>
                <div class='p-4'>
                {% if restart_message %}
                  <div class='alert alert-{% if restart_status == "success" %}success{% else %}danger{% endif %} mb-3'>{{ restart_message }}</div>
                {% endif %}
                <div class='row g-3 mb-3'>
                  <div class='col-md-4'><div class='card kpi'><div class='card-body'><div class='text-muted'>Rotas</div><div class='h4 mb-0'>{{ routes|length }}</div></div></div></div>
                  <div class='col-md-4'><div class='card kpi'><div class='card-body'><div class='text-muted'>Cron</div><div class='h6 mb-0'>{% if not cron or cron['cron_enabled'] %}Ativo{% else %}Inativo{% endif %} ({{ cron_minutes }} min)</div></div></div></div>
                  <div class='col-md-4'><div class='card kpi'><div class='card-body'><div class='text-muted'>Última execução</div><div class='small mb-0'>{% if last_run %}{{last_run['status']}}{% else %}sem execução{% endif %}</div></div></div></div>
                </div>

                <div class='card mb-3 shadow-sm dashboard-section' id='rotas'>
                  <div class='card-header'><i class='bi bi-signpost-split me-2'></i>Rotas configuradas</div>
                  <div class='card-body'>
                    <form method='post' action='{{ url_for("add_route") }}' class='row g-2 mb-3 align-items-end'>
                      <div class='col-md-2'>
                        <label class='form-label small text-uppercase mb-1'>Origem</label>
                        <select class='form-select form-select-sm' name='origin' required>
                          {% for code, label in airport_options %}
                            <option value='{{ code }}' {% if code == 'PVH' %}selected{% endif %}>{{ label }}</option>
                          {% endfor %}
                        </select>
                      </div>
                      <div class='col-md-2'>
                        <label class='form-label small text-uppercase mb-1'>Destino</label>
                        <select class='form-select form-select-sm' name='destination' required>
                          {% for code, label in airport_options %}
                            <option value='{{ code }}' {% if code == 'JPA' %}selected{% endif %}>{{ label }}</option>
                          {% endfor %}
                        </select>
                      </div>
                      <div class='col-md-3'>
                        <label class='form-label small text-uppercase mb-1'>Ida</label>
                        <input class='form-control form-control-sm' name='outbound_date' type='date' required>
                      </div>
                      <div class='col-md-3'>
                        <label class='form-label small text-uppercase mb-1'>Volta</label>
                        <input class='form-control form-control-sm' name='inbound_date' type='date'>
                      </div>
                      <div class='col-md-2 d-grid'>
                        <button class='btn btn-primary btn-sm' type='submit'>Adicionar</button>
                      </div>
                    </form>
                    <div class='small text-muted mb-3'>As datas padrão globais de ida foram reduzidas para 04 e 05 de junho.</div>
                    <div class='table-responsive border rounded'>
                      <table class='table table-hover table-striped mb-0 align-middle'>
                        <thead class='table-light'>
                          <tr>
                            <th>Origem</th>
                            <th>Destino</th>
                            <th>Data de Ida</th>
                            <th>Data de Volta</th>
                            <th class='text-end'>Ações</th>
                          </tr>
                        </thead>
                        <tbody>
                          {% for r in routes %}
                            <tr>
                              <form method='post' action='{{ url_for("update_route", route_id=r["id"]) }}'>
                                <td>
                                  <select class='form-select form-select-sm' name='origin' required>
                                    {% for code, label in airport_options %}
                                      <option value='{{ code }}' {% if code == (r["origin"] or "").upper() %}selected{% endif %}>{{ label }}</option>
                                    {% endfor %}
                                  </select>
                                </td>
                                <td>
                                  <select class='form-select form-select-sm' name='destination' required>
                                    {% for code, label in airport_options %}
                                      <option value='{{ code }}' {% if code == (r["destination"] or "").upper() %}selected{% endif %}>{{ label }}</option>
                                    {% endfor %}
                                  </select>
                                </td>
                                <td><input class='form-control form-control-sm' name='outbound_date' type='date' value='{{r["outbound_date"]}}' required></td>
                                <td><input class='form-control form-control-sm' name='inbound_date' type='date' value='{{r["inbound_date"] if r["inbound_date"] else ""}}'></td>
                                <td class='text-end text-nowrap'>
                                  <button class='btn btn-sm btn-outline-primary' type='submit'><i class='bi bi-save'></i> Salvar</button>
                                  <a class='btn btn-sm btn-outline-danger' href='{{ url_for("delete_route", route_id=r["id"]) }}'>
                                    <i class='bi bi-trash'></i> Excluir
                                  </a>
                                </td>
                              </form>
                            </tr>
                          {% else %}
                            <tr><td colspan='5' class='text-center text-muted py-3'>Nenhuma rota cadastrada.</td></tr>
                          {% endfor %}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>

                <div class='card mb-3 shadow-sm dashboard-section d-none' id='consultas'>
                  <div class='card-header d-flex justify-content-between align-items-center'>
                    <div>
                      <i class='bi bi-window me-2'></i>App Consultas
                      <small class='text-muted d-block'>Executa buscas com o cron integrando histórico, rotas e consultas manuais.</small>
                    </div>
                    <button class='btn btn-sm btn-outline-secondary' type='button' onclick='document.getElementById('btn-consultar').scrollIntoView({behavior: "smooth"});'>Ir para consulta</button>
                  </div>
                  <div class='card-body'>
                    <section class='mb-4'>
                      <div class='row g-2 align-items-end'>
                        <div class='col-md-3'>
                          <label class='form-label small text-uppercase'>Origem</label>
                          <select id='origin' class='form-select form-select-sm'>
                            <option value='PVH' selected>PVH — Porto Velho (RO)</option>
                            <option value='BPS'>BPS — Porto Seguro (BA)</option>
                            <option value='RIO'>RIO — Rio de Janeiro (RJ)</option>
                            <option value='SAO'>SAO — São Paulo (SP)</option>
                            <option value='BSB'>BSB — Brasília (DF)</option>
                            <option value='CGB'>CGB — Cuiabá (MT)</option>
                            <option value='GYN'>GYN — Goiânia (GO)</option>
                            <option value='MCZ'>MCZ — Maceió (AL)</option>
                            <option value='AJU'>AJU — Aracaju (SE)</option>
                            <option value='SSA'>SSA — Salvador (BA)</option>
                            <option value='FOR'>FOR — Fortaleza (CE)</option>
                            <option value='SLZ'>SLZ — São Luís (MA)</option>
                            <option value='CGR'>CGR — Campo Grande (MS)</option>
                            <option value='BHZ'>BHZ — Belo Horizonte (MG)</option>
                            <option value='BEL'>BEL — Belém (PA)</option>
                            <option value='JPA'>JPA — João Pessoa (PB)</option>
                            <option value='CWB'>CWB — Curitiba (PR)</option>
                            <option value='REC'>REC — Recife (PE)</option>
                            <option value='THE'>THE — Teresina (PI)</option>
                            <option value='NAT'>NAT — Natal (RN)</option>
                            <option value='POA'>POA — Porto Alegre (RS)</option>
                            <option value='FLN'>FLN — Florianópolis (SC)</option>
                            <option value='VIX'>VIX — Vitória (ES)</option>
                            <option value='MAO'>MAO — Manaus (AM)</option>
                            <option value='RBR'>RBR — Rio Branco (AC)</option>
                            <option value='BVB'>BVB — Boa Vista (RR)</option>
                            <option value='MCP'>MCP — Macapá (AP)</option>
                            <option value='PMW'>PMW — Palmas (TO)</option>
                          </select>
                        </div>
                        <div class='col-md-3'>
                          <label class='form-label small text-uppercase'>Destino</label>
                          <select id='destination' class='form-select form-select-sm'>
                            <option value='JPA' selected>JPA — João Pessoa (PB)</option>
                            <option value='BPS'>BPS — Porto Seguro (BA)</option>
                            <option value='REC'>REC — Recife (PE)</option>
                            <option value='NAT'>NAT — Natal (RN)</option>
                            <option value='SLZ'>SLZ — São Luís (MA)</option>
                            <option value='THE'>THE — Teresina (PI)</option>
                            <option value='FOR'>FOR — Fortaleza (CE)</option>
                            <option value='MCZ'>MCZ — Maceió (AL)</option>
                            <option value='AJU'>AJU — Aracaju (SE)</option>
                            <option value='SSA'>SSA — Salvador (BA)</option>
                            <option value='PVH'>PVH — Porto Velho (RO)</option>
                            <option value='RIO'>RIO — Rio de Janeiro (RJ)</option>
                            <option value='SAO'>SAO — São Paulo (SP)</option>
                            <option value='BSB'>BSB — Brasília (DF)</option>
                            <option value='CGB'>CGB — Cuiabá (MT)</option>
                            <option value='GYN'>GYN — Goiânia (GO)</option>
                            <option value='CGR'>CGR — Campo Grande (MS)</option>
                            <option value='BHZ'>BHZ — Belo Horizonte (MG)</option>
                            <option value='BEL'>BEL — Belém (PA)</option>
                            <option value='CWB'>CWB — Curitiba (PR)</option>
                            <option value='POA'>POA — Porto Alegre (RS)</option>
                            <option value='FLN'>FLN — Florianópolis (SC)</option>
                            <option value='VIX'>VIX — Vitória (ES)</option>
                            <option value='MAO'>MAO — Manaus (AM)</option>
                            <option value='RBR'>RBR — Rio Branco (AC)</option>
                            <option value='BVB'>BVB — Boa Vista (RR)</option>
                            <option value='MCP'>MCP — Macapá (AP)</option>
                            <option value='PMW'>PMW — Palmas (TO)</option>
                          </select>
                        </div>
                        <div class='col-md-2'>
                          <label class='form-label small text-uppercase'>Ida</label>
                          <input id='outbound_date' type='date' class='form-control form-control-sm' value='2026-06-05' />
                        </div>
                        <div class='col-md-2'>
                          <label class='form-label small text-uppercase'>Volta</label>
                          <input id='inbound_date' type='date' class='form-control form-control-sm' value='' />
                        </div>
                        <div class='col-12 col-md-1 d-grid'>
                          <button id='btn-consultar' class='btn btn-primary btn-sm' onclick='consultar()'>Consultar</button>
                        </div>
                      </div>
                      <small class='text-muted d-block mt-2'>Se preencher volta, consulta como ida e volta.</small>
                    </section>
                    <section class='mb-4'>
                      <h6 class='text-uppercase text-muted mb-3'>Resultados da consulta</h6>
                      <div class='table-responsive'>
                        <table class='table table-striped table-hover align-middle text-center mb-0' id='consulta-table'>
                          <thead class='table-light'>
                            <tr>
                              <th>Rota</th>
                              <th>Data voo</th>
                              <th>Preço</th>
                              <th>Onde comprar mais barato</th>
                              <th>Fonte</th>
                              <th>Origem preço</th>
                              <th>Data/Hora</th>
                            </tr>
                          </thead>
                          <tbody id='consulta-body'>
                            <tr>
                              <td colspan='7' class='text-center text-muted'>Faça uma consulta para ver resultados.</td>
                            </tr>
                          </tbody>
                        </table>
                      </div>
                    </section>
                    <section class='mb-4'>
                      <div class='d-flex justify-content-between align-items-center mb-2'>
                        <h6 class='text-uppercase text-muted m-0'>Buscar todos (cron)</h6>
                        <button id='btn-cron' class='btn btn-warning btn-sm' type='button' onclick='executarCron()'>Executar busca completa</button>
                      </div>
                      <div id='cron-loading' class='text-muted mb-2' style='display:none;'>Buscando rotas... isso pode levar alguns minutos.</div>
                      <div class='table-responsive'>
                        <table class='table table-striped table-hover align-middle text-center mb-0' id='cron-table'>
                          <thead class='table-light'>
                            <tr>
                              <th>Rota</th>
                              <th>Data voo</th>
                              <th>Preço</th>
                              <th>Onde comprar mais barato</th>
                              <th>Fonte</th>
                              <th>Origem preço</th>
                              <th>Data/Hora</th>
                            </tr>
                          </thead>
                          <tbody id='cron-body'>
                            <tr><td colspan='7' class='text-center text-muted'>Clique em “Executar busca completa”.</td></tr>
                          </tbody>
                        </table>
                      </div>
                    </section>
                    <section class='mb-4'>
                      <div class='d-flex justify-content-between align-items-center mb-2'>
                        <h6 class='text-uppercase text-muted m-0'>Histórico</h6>
                        <div class='d-flex gap-2'>
                          <input id='historico-limit' type='number' class='form-control form-control-sm' value='20' min='1' max='200' style='width: 90px;' />
                          <button class='btn btn-outline-success btn-sm' type='button' onclick='historico()'>Atualizar</button>
                          <button class='btn btn-outline-danger btn-sm' type='button' onclick='limparHistorico()'>Limpar</button>
                        </div>
                      </div>
                      <div id='historico-loading' class='text-muted mb-2' style='display:none;'>Carregando histórico...</div>
                      <div class='table-responsive'>
                        <table class='table table-striped table-hover align-middle text-center mb-0' id='historico-table'>
                          <thead class='table-light'>
                            <tr>
                              <th>Rota</th>
                              <th>Data voo</th>
                              <th>Preço</th>
                              <th>Onde comprar mais barato</th>
                              <th>Fonte</th>
                              <th>Origem preço</th>
                              <th>Data/Hora</th>
                            </tr>
                          </thead>
                          <tbody id='historico-body'>
                            <tr>
                              <td colspan='7' class='text-center text-muted'>Clique em “Atualizar” para carregar.</td>
                            </tr>
                          </tbody>
                        </table>
                      </div>
                    </section>
                    <section>
                      <div class='d-flex justify-content-between align-items-center mb-2'>
                        <h6 class='text-uppercase text-muted m-0'>Rotas configuradas</h6>
                        <button class='btn btn-outline-secondary btn-sm' type='button' onclick='rotas()'>Atualizar</button>
                      </div>
                      <div id='rotas-loading' class='text-muted mb-2' style='display:none;'>Carregando rotas...</div>
                      <div class='table-responsive'>
                        <table class='table table-striped table-hover align-middle text-center mb-0' id='rotas-table'>
                          <thead class='table-light'>
                            <tr>
                              <th>Origem</th>
                              <th>Destino</th>
                              <th>Ida</th>
                              <th>Volta</th>
                              <th>Tipo</th>
                            </tr>
                          </thead>
                          <tbody id='rotas-body'>
                            <tr>
                              <td colspan='5' class='text-center text-muted'>Clique em “Atualizar” para carregar.</td>
                            </tr>
                          </tbody>
                        </table>
                      </div>
                    </section>
                  </div>
                </div>

                <div class='card mb-3 shadow-sm dashboard-section d-none' id='telegram'>
                  <div class='card-header'><i class='bi bi-telegram me-2'></i>Telegram do usuário</div>
                  <div class='card-body'>
                    <form method='post' action='{{ url_for("save_telegram") }}' class='row g-2'>
                      <div class='col-md-6'><input class='form-control' name='bot_token' placeholder='Bot token' value='{{ tg["bot_token"] if tg and tg["bot_token"] else default_tg_bot }}'></div>
                      <div class='col-md-4'><input class='form-control' name='chat_id' placeholder='Chat ID' value='{{ tg["chat_id"] if tg and tg["chat_id"] else default_tg_chat }}'></div>
                      <div class='col-md-2 d-grid'><button class='btn btn-success' type='submit'>Salvar</button></div>
                    </form>
                  </div>
                </div>

                <div class='card shadow-sm dashboard-section d-none' id='cron'>
                  <div class='card-header'><i class='bi bi-clock-history me-2'></i>Cron do usuário</div>
                  <div class='card-body'>
                    <form method='post' action='{{ url_for("save_cron") }}' class='row g-2 align-items-center'>
                      <div class='col-md-2 form-check ms-2'>
                        <input class='form-check-input' type='checkbox' name='enabled' id='enabled' {% if not cron or cron['cron_enabled'] %}checked{% endif %}>
                        <label class='form-check-label' for='enabled'>Ativo</label>
                      </div>
                      <div class='col-md-3'><input class='form-control' name='schedule_minutes' type='number' min='1' max='1440' step='1' value='{{ cron_minutes }}'></div>
                      <div class='col-md-4'><input class='form-control' name='max_price_display' type='number' min='0' step='0.01' placeholder='Preço máximo exibido por trecho' value='{{ cron_max_price }}'></div>
                      <div class='col-md-2 d-grid'><button class='btn btn-primary' type='submit'>Salvar</button></div>
                    </form>
                    <form method='post' action='{{ url_for("run_now_user") }}' class='mt-3'>
                      <button class='btn btn-warning' type='submit'>Executar agora</button>
                    </form>
                    <form method='post' action='{{ url_for("restart_service") }}' class='mt-2' onsubmit='return confirm("Reiniciar o serviço agora?");'>
                      <button class='btn btn-outline-danger' type='submit'>Reiniciar serviço</button>
                    </form>
                    <div class='small text-muted mt-2'>
                      {% if restart_command_configured %}
                        O painel usará o comando configurado em <code>SKYSCANNER_RESTART_COMMAND</code>.
                      {% else %}
                        Nenhum comando de reinício configurado. O painel tentará reabrir o processo Python atual.
                      {% endif %}
                    </div>
                    <div class='mt-3'><strong>Última execução:</strong><br>
                      {% if last_run %}
                        {{last_run['started_at']}} → {{last_run['finished_at']}} | {{last_run['status']}} | {{last_run['summary']}}
                      {% else %}
                        sem execução
                      {% endif %}
                    </div>
                  </div>
                </div>


                </div>
              </main>
            </div>
          </div>
        <script src='{{ url_for("static", filename="consulta-app.js") }}'></script>
        <script>
          function showSection(hash) {
            document.querySelectorAll('.dashboard-section').forEach(el => el.classList.add('d-none'));
            var target = document.getElementById(hash);
            if (target) {
              target.classList.remove('d-none');
              localStorage.setItem('adminActiveTab', hash);
            } else {
              document.getElementById('rotas').classList.remove('d-none');
              localStorage.setItem('adminActiveTab', 'rotas');
            }
            document.querySelectorAll('.sidebar a').forEach(el => el.classList.remove('fw-bold', 'text-white'));
            var activeLink = document.querySelector('.sidebar a[href="#' + hash + '"]');
            if (activeLink) activeLink.classList.add('fw-bold', 'text-white');
          }
          window.addEventListener('hashchange', () => {
            let hash = window.location.hash.substring(1);
            if(hash) {
              showSection(hash);
            }
          });
          window.addEventListener('load', () => {
            let hash = window.location.hash.substring(1) || localStorage.getItem('adminActiveTab') || 'rotas';
            showSection(hash);
          });
          function toggleTheme() {
            document.body.classList.toggle('dark-mode');
            localStorage.setItem('adminThemeDark', document.body.classList.contains('dark-mode') ? '1' : '0');
          }
          function toggleSidebar() {
            document.body.classList.toggle('sidebar-collapsed');
            localStorage.setItem('adminSidebarCollapsed', document.body.classList.contains('sidebar-collapsed') ? '1' : '0');
          }
          (function restoreUiState() {
            if (localStorage.getItem('adminThemeDark') === '1') document.body.classList.add('dark-mode');
            if (localStorage.getItem('adminSidebarCollapsed') === '1') document.body.classList.add('sidebar-collapsed');
          })();
        </script>
        </body>
        </html>
        """,
        user=user,
        routes=routes,
        tg=tg,
        cron=cron,
        cron_minutes=cron_minutes,
        cron_max_price=cron_max_price,
        last_run=last_run,
        default_tg_bot=default_tg_bot,
        default_tg_chat=default_tg_chat,
        airport_options=AIRPORT_OPTIONS,
        restart_command_configured=bool(PANEL_RESTART_COMMAND),
    )


@app.route("/painel/route/add", methods=["POST"])
@login_required
def add_route():
    db = get_auth_db()
    user = current_user()
    db.execute(
        "INSERT INTO user_routes (user_id, origin, destination, outbound_date, inbound_date, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
        (
            user["id"],
            request.form.get("origin", "").strip().upper(),
            request.form.get("destination", "").strip().upper(),
            request.form.get("outbound_date", "").strip(),
            request.form.get("inbound_date", "").strip(),
            now_local_iso(sep="T"),
        ),
    )
    db.commit()
    return redirect(url_for("painel"))


@app.route("/painel/route/delete/<int:route_id>", methods=["GET"])
@login_required
def delete_route(route_id: int):
    db = get_auth_db()
    user = current_user()
    db.execute("DELETE FROM user_routes WHERE id = ? AND user_id = ?", (route_id, user["id"]))
    db.commit()
    return redirect(url_for("painel"))

@app.route("/painel/route/update/<int:route_id>", methods=["POST"])
@login_required
def update_route(route_id: int):
    db = get_auth_db()
    user = current_user()
    db.execute(
        """
        UPDATE user_routes
        SET origin = ?, destination = ?, outbound_date = ?, inbound_date = ?
        WHERE id = ? AND user_id = ?
        """,
        (
            request.form.get("origin", "").strip().upper(),
            request.form.get("destination", "").strip().upper(),
            request.form.get("outbound_date", "").strip(),
            request.form.get("inbound_date", "").strip(),
            route_id,
            user["id"],
        ),
    )
    db.commit()
    return redirect(url_for("painel", _anchor="rotas"))


@app.route("/painel/telegram", methods=["POST"])
@login_required
def save_telegram():
    db = get_auth_db()
    user = current_user()
    db.execute(
        """
        INSERT INTO user_telegram (user_id, bot_token, chat_id, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          bot_token = excluded.bot_token,
          chat_id = excluded.chat_id,
          updated_at = excluded.updated_at
        """,
        (
            user["id"],
            request.form.get("bot_token", "").strip(),
            request.form.get("chat_id", "").strip(),
            now_local_iso(sep="T"),
        ),
    )
    db.commit()
    return redirect(url_for("painel"))


@app.route("/painel/run-now", methods=["POST"])
@login_required
def run_now_user():
    user = current_user()
    run_user_scan(int(user["id"]), trigger="painel-manual", notify=True)
    return redirect(url_for("painel", _anchor="cron"))


@app.route("/painel/restart", methods=["POST"])
@login_required
def restart_service():
    ok, message, should_exit = trigger_service_restart()
    if not ok:
        return build_restart_redirect(message, level="error")

    if should_exit:
        def _shutdown_later():
            time.sleep(1)
            os._exit(0)

        threading.Thread(target=_shutdown_later, daemon=True).start()
    return build_restart_redirect(message, level="success")


@app.route("/painel/cron", methods=["POST"])
@login_required
def save_cron():
    db = get_auth_db()
    enabled = 1 if request.form.get("enabled") else 0
    schedule_minutes = max(1, min(1440, int(request.form.get("schedule_minutes", DEFAULT_SCAN_INTERVAL_MINUTES))))
    max_price_display_raw = request.form.get("max_price_display", "").strip()
    max_price_display = None
    if max_price_display_raw:
        max_price_display = max(0.0, float(max_price_display_raw))
    db.execute(
        """
        INSERT INTO app_settings (id, cron_enabled, scan_interval_minutes, max_price_display, updated_at)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          cron_enabled = excluded.cron_enabled,
          scan_interval_minutes = excluded.scan_interval_minutes,
          max_price_display = excluded.max_price_display,
          updated_at = excluded.updated_at
        """,
        (enabled, schedule_minutes, max_price_display, now_local_iso(sep="T")),
    )
    db.commit()
    return redirect(url_for("painel", _anchor="cron"))


if __name__ == "__main__":
    init_auth_tables()
    normalize_maxmilhas_history()
    start_auto_scan_if_needed()
    debug_mode = os.getenv("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
    app.run(debug=debug_mode)
