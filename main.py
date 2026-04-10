from __future__ import annotations
from typing import List, Dict, Tuple, Optional

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from datetime import datetime, timedelta
from math import ceil
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageDraw, ImageFont
from db import DatabaseIntegrityError, DatabaseOperationalError, connect_db, ensure_column, mysql_enabled


def _load_local_env_file() -> None:
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_local_env_file()

from skyscanner import (
    CONFIG,
    Database,
    FlightResult,
    GoogleFlightsScraper,
    RouteQuery,
    build_config_queries,
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
from maxmilhas import (
    buscar_menor_preco as buscar_menor_preco_maxmilhas,
    filtrar_precos_parcelados,
)

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = os.getenv("SKYSCANNER_SECRET_KEY", "dev-change-this-secret")


DEFAULT_SCAN_INTERVAL = int(CONFIG.get("full_scan_seconds", 3 * 60 * 60))
DEFAULT_SCHEDULE_MINUTES = max(1, int(CONFIG.get("schedule_minutes", DEFAULT_SCAN_INTERVAL // 60)))
SCAN_INTERVAL_SECONDS = int(os.getenv("SKYSCANNER_FULL_SCAN_EVERY_SECONDS", str(DEFAULT_SCAN_INTERVAL)))
AUTO_SCAN_ENABLED = os.getenv("SKYSCANNER_AUTO_SCAN", "1") == "1"
USER_SCAN_POLL_SECONDS = int(os.getenv("SKYSCANNER_USER_SCAN_POLL_SECONDS", "60"))
USER_RUN_STALE_SECONDS = int(os.getenv("SKYSCANNER_USER_RUN_STALE_SECONDS", "7200"))
PANEL_RESTART_COMMAND = os.getenv("SKYSCANNER_RESTART_COMMAND", "").strip()
SERVERLESS_ENV = any(os.getenv(name) for name in ("VERCEL", "AWS_LAMBDA_FUNCTION_NAME"))
INTERNAL_SCHEDULER_ENABLED = os.getenv("SKYSCANNER_INTERNAL_SCHEDULER", "0" if SERVERLESS_ENV else "1") == "1"
_scan_lock = threading.Lock()
_user_run_state_lock = threading.Lock()
_scan_last_run_at = None
_auto_scan_started = False
_user_scheduler_started = False
_runtime_bootstrap_done = False
MANUAL_QUERY_CACHE_TTL_SECONDS = int(os.getenv("MANUAL_QUERY_CACHE_TTL_SECONDS", "300"))
_manual_query_cache_lock = threading.Lock()
_manual_query_cache: dict[tuple[str, str, str, str, str], dict] = {}

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


def trigger_service_restart() -> Tuple[bool, str, bool]:
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

    argv_text = " ".join(sys.argv).lower()
    server_software = os.getenv("SERVER_SOFTWARE", "").lower()
    if "gunicorn" in argv_text or "gunicorn" in server_software:
        return (
            False,
            "Reinício pelo painel exige SKYSCANNER_RESTART_COMMAND quando a aplicação está sob gunicorn/systemd.",
            False,
        )

    if os.getenv("WERKZEUG_RUN_MAIN") == "true":
        return False, "Reinício pelo próprio processo não é suportado com o reloader do Flask.", False

    try:
        python_bin = sys.executable
        subprocess.Popen([python_bin, *sys.argv], cwd=os.getcwd(), start_new_session=True)
        return True, "Novo processo iniciado. O processo atual será encerrado.", True
    except Exception as exc:
        return False, f"Falha ao iniciar novo processo: {exc}", False


def date_color_token(date_iso: Optional[str]) -> Tuple[str, str]:
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


def build_full_scan_message(parsed: List[dict], trigger: str = "manual") -> str:
    def _price_num(row):
        v = row.get("price")
        return v if isinstance(v, (int, float)) and v is not None else 10**12

    def _dedupe_sorted_rows(rows: List[dict]) -> List[dict]:
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

    def _format_direction(rows: List[dict], best_row: dict | None, section_title: str, highlight_axis: str) -> List[str]:
        if not rows:
            return [section_title, "N/D"]

        grouped: Dict[str, List[dict]] = {}
        for row in rows:
            date = row.get("outbound_date", "") or ""
            grouped.setdefault(date, []).append(row)

        ordered_dates = sorted(grouped.keys())
        section_lines = [section_title]
        for date_idx, date in enumerate(ordered_dates):
            group = grouped[date]
            header = f"📅 {date}" if date else "📅 data pendente"
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
                    f"{route_label} | {color} {row.get('outbound_date')} | {row.get('price_fmt')}{vendor_txt}{best_note}"
                )
            if date_idx != len(ordered_dates) - 1:
                section_lines.append("")
        return section_lines

    if not parsed:
        return (
            "- ────────── ✈️ CONSULTA COMPLETA ✈️ ────────── -\n"
            "Sem dados nesta execução."
        )

    idas = [r for r in parsed if str(r.get("origin", "")).upper() == "PVH" and str(r.get("destination", "")).upper() != "PVH"]
    voltas = [r for r in parsed if str(r.get("destination", "")).upper() == "PVH"]

    idas_ok = _dedupe_sorted_rows(sorted([r for r in idas if r.get("price") is not None], key=_price_num))
    voltas_ok = _dedupe_sorted_rows(sorted([r for r in voltas if r.get("price") is not None], key=_price_num))

    lines = [
        "- ────────── ✈️ CONSULTA COMPLETA ✈️ ────────── -",
        f"Execução: {trigger}",
        "",
        *(_format_direction(idas_ok, idas_ok[0] if idas_ok else None, "IDAS (PVH -> destino):", "destination")),
        "",
        *(_format_direction(voltas_ok, voltas_ok[0] if voltas_ok else None, "VOLTAS (destino -> PVH):", "origin")),
    ]

    total_ok = len([r for r in parsed if r.get("price") is not None])
    lines += ["", f"Resumo: {total_ok}/{len(parsed)} rotas com preço válido."]
    return "\n".join(lines)


def notify_full_scan(parsed: List[dict], trigger: str = "manual", send_fn=None, max_price: Optional[float] = None) -> None:
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


def _build_user_routes(conn, user_id: int) -> List[RouteQuery]:
    rows = conn.execute(
        """
        SELECT origin, destination, outbound_date, inbound_date
        FROM user_routes
        WHERE user_id = ? AND active = 1
        ORDER BY id DESC
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


def _store_result(db: Database, route: RouteQuery, result: FlightResult, user_id: Optional[int] = None, persist: bool = True) -> dict:
    min_price, avg_price, _last_price = db.stats_for(route)
    band = classify_price(result.price, min_price, avg_price)
    if persist:
        db.save(result, band, user_id=user_id)
    return _result_to_row(result, band)


def _split_routes(routes: List[RouteQuery], chunks: int) -> List[list[RouteQuery]]:
    if not routes or chunks <= 0:
        return []
    chunk_size = ceil(len(routes) / chunks)
    return [routes[i * chunk_size:(i + 1) * chunk_size] for i in range(chunks)]


def _route_key(route: RouteQuery) -> tuple[str, str, str, str, str]:
    return (
        (route.origin or "").upper(),
        (route.destination or "").upper(),
        route.outbound_date or "",
        route.inbound_date or "",
        route.trip_type or "oneway",
    )


def _dedupe_routes(routes: List[RouteQuery]) -> List[RouteQuery]:
    unique: List[RouteQuery] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for route in routes:
        key = _route_key(route)
        if key in seen:
            continue
        seen.add(key)
        unique.append(route)
    return unique


def _manual_cache_key(route: RouteQuery, source: str) -> tuple[str, str, str, str, str]:
    return (
        (route.origin or "").upper(),
        (route.destination or "").upper(),
        route.outbound_date or "",
        route.inbound_date or "",
        source,
    )


def _get_manual_cached_result(route: RouteQuery, source: str) -> Optional[dict]:
    if MANUAL_QUERY_CACHE_TTL_SECONDS <= 0:
        return None
    key = _manual_cache_key(route, source)
    now = time.time()
    with _manual_query_cache_lock:
        entry = _manual_query_cache.get(key)
        if not entry:
            return None
        if now - float(entry["created_at"]) > MANUAL_QUERY_CACHE_TTL_SECONDS:
            _manual_query_cache.pop(key, None)
            return None
        return dict(entry["row"])


def _set_manual_cached_result(route: RouteQuery, source: str, row: dict) -> None:
    if MANUAL_QUERY_CACHE_TTL_SECONDS <= 0:
        return
    key = _manual_cache_key(route, source)
    with _manual_query_cache_lock:
        _manual_query_cache[key] = {
            "created_at": time.time(),
            "row": dict(row),
        }


def run_scan_for_routes(routes: List[RouteQuery], on_row=None, user_id: Optional[int] = None, persist: bool = True):
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
    chunk_results: Optional[List[list[Tuple[RouteQuery, FlightResult]]]] = [None] * len(route_chunks)

    def _scan_chunk(chunk_idx: int, chunk_routes: List[RouteQuery]) -> List[Tuple[RouteQuery, FlightResult]]:
        if not chunk_routes:
            return []
        worker_results: List[Tuple[RouteQuery, FlightResult]] = []
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
                    google_result = _search_google_result(scraper, route)
                    worker_results.append((route, google_result))
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
    parsed: List[dict] = []
    idx = 0
    try:
        for chunk in chunk_results:
            if not chunk:
                continue
            for route, result in chunk:
                row = _store_result(db, route, result, user_id=user_id, persist=persist)
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
    _scan_last_run_at = datetime.now().isoformat()
    return parsed


def _create_user_run(conn, user_id: int, trigger: str = "manual-user") -> int:
    cur = conn.execute(
        "INSERT INTO user_runs (user_id, started_at, status, summary, `trigger`) VALUES (?, ?, ?, ?, ?)",
        (user_id, datetime.now().isoformat(), "running", "", trigger),
    )
    conn.commit()
    return int(cur.lastrowid)


def _finish_user_run(conn, run_id: int, status: str, summary: str) -> None:
    conn.execute(
        "UPDATE user_runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?",
        (datetime.now().isoformat(), status, summary, run_id),
    )
    conn.commit()


def _touch_user_cron_run(conn, user_id: int) -> None:
    conn.execute(
        "UPDATE user_cron SET last_run_at = ?, updated_at = COALESCE(updated_at, ?) WHERE user_id = ?",
        (datetime.now().isoformat(), datetime.now().isoformat(), user_id),
    )
    conn.commit()


def _user_has_running_scan(conn, user_id: int) -> bool:
    row = conn.execute(
        """
        SELECT id, started_at
        FROM user_runs
        WHERE user_id = ? AND status = 'running'
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if not row:
        return False

    started_at = row["started_at"]
    if started_at:
        try:
            started = datetime.fromisoformat(started_at)
            age_seconds = (datetime.now() - started).total_seconds()
            if age_seconds >= max(300, USER_RUN_STALE_SECONDS):
                _finish_user_run(conn, int(row["id"]), "error", "Execucao anterior marcada como orfa por exceder o tempo limite")
                return False
        except Exception:
            pass

    return True


def _build_scheduled_user_scan_payloads(conn, schedule_minutes: int) -> List[dict]:
    payloads: List[dict] = []
    users = conn.execute(
        """
        SELECT u.id AS user_id,
               COALESCE(c.enabled, 1) AS enabled
        FROM users u
        LEFT JOIN user_cron c ON c.user_id = u.id
        """
    ).fetchall()
    for row in users:
        user_id = int(row["user_id"])
        if int(row["enabled"] or 0) != 1:
            continue
        if not _should_run_user_now(conn, user_id, schedule_minutes):
            continue
        routes = _build_user_routes(conn, user_id)
        if not routes:
            continue
        payloads.append({"user_id": user_id, "routes": _dedupe_routes(routes)})
    return payloads


def run_user_scan(user_id: int, trigger: str = "manual-user", notify: bool = True):
    conn = connect_db(auth_db_path())
    try:
        with _user_run_state_lock:
            if _user_has_running_scan(conn, user_id):
                raise RuntimeError("Ja existe uma varredura em andamento para este usuario.")
            run_id = _create_user_run(conn, user_id, trigger=trigger)
            if trigger.startswith("agendada"):
                _touch_user_cron_run(conn, user_id)
        routes = _build_user_routes(conn, user_id)
        if not routes:
            _finish_user_run(conn, run_id, "ok", "Nenhuma rota configurada para este usuario.")
            return {"status": "ok", "summary": "Nenhuma rota configurada para este usuario.", "parsed": []}
        db = Database(get_db_path())
        try:
            db.clear_results_for_user(user_id)
        finally:
            db.conn.close()
        parsed = run_scan_for_routes(routes, user_id=user_id, persist=True)
        max_price = get_user_max_display_price(user_id)
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
            parsed = run_full_scan()
            max_price = get_global_max_price_limit()
            notify_full_scan(parsed, trigger="agendada", max_price=max_price)
            print(f"[auto-scan] consulta completa executada em {_scan_last_run_at}")
        except Exception as e:
            print(f"[auto-scan] erro: {e}")
        time.sleep(SCAN_INTERVAL_SECONDS)


def start_auto_scan_if_needed():
    global _auto_scan_started

    start_user_scan_scheduler_if_needed()

    if _auto_scan_started:
        return

    if SERVERLESS_ENV:
        print("[auto-scan] indisponível em runtime serverless")
        return

    if not INTERNAL_SCHEDULER_ENABLED:
        print("[auto-scan] desativado por SKYSCANNER_INTERNAL_SCHEDULER=0")
        return

    if not AUTO_SCAN_ENABLED:
        print("[auto-scan] desativado por SKYSCANNER_AUTO_SCAN=0")
        return

    is_reloader_main = os.getenv("WERKZEUG_RUN_MAIN") == "true"
    is_debug = os.getenv("FLASK_DEBUG") == "1"
    if is_debug and not is_reloader_main:
        return

    t = threading.Thread(target=_auto_scan_loop, daemon=True)
    t.start()
    _auto_scan_started = True
    print(f"[auto-scan] ligado: intervalo {SCAN_INTERVAL_SECONDS}s + cron global")


def _should_run_user_now(conn, user_id: int, schedule_minutes: int) -> bool:
    if _user_has_running_scan(conn, user_id):
        return False

    row = conn.execute(
        "SELECT last_run_at, updated_at FROM user_cron WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    interval_seconds = max(60, schedule_minutes * 60)
    if row and row["last_run_at"]:
        try:
            last_started = datetime.fromisoformat(row["last_run_at"])
            return (datetime.now() - last_started).total_seconds() >= interval_seconds
        except Exception:
            pass

    fallback = conn.execute(
        "SELECT started_at FROM user_runs WHERE user_id = ? AND `trigger` LIKE 'agendada%' ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not fallback or not fallback["started_at"]:
        return True
    try:
        last_started = datetime.fromisoformat(fallback["started_at"])
    except Exception:
        return True
    return (datetime.now() - last_started).total_seconds() >= interval_seconds


def process_due_user_scans_once() -> List[dict]:
    conn = connect_db(auth_db_path())
    results: List[dict] = []
    try:
        settings = get_global_cron_settings()
        if int(settings["cron_enabled"]) != 1:
            return results
        schedule_minutes = max(1, int(settings["schedule_minutes"]))
        due_payloads = _build_scheduled_user_scan_payloads(conn, schedule_minutes)
        if not due_payloads:
            return results

        run_map: dict[int, int] = {}
        route_map_by_user: dict[int, set[tuple[str, str, str, str, str]]] = {}
        unique_routes: List[RouteQuery] = []
        unique_seen: set[tuple[str, str, str, str, str]] = set()

        with _user_run_state_lock:
            for payload in due_payloads:
                user_id = int(payload["user_id"])
                if _user_has_running_scan(conn, user_id):
                    results.append({"user_id": user_id, "status": "skipped", "summary": "Já existe uma varredura em andamento para este usuario."})
                    continue
                run_id = _create_user_run(conn, user_id, trigger="agendada-usuario")
                _touch_user_cron_run(conn, user_id)
                run_map[user_id] = run_id
                keys_for_user: set[tuple[str, str, str, str, str]] = set()
                for route in payload["routes"]:
                    key = _route_key(route)
                    keys_for_user.add(key)
                    if key in unique_seen:
                        continue
                    unique_seen.add(key)
                    unique_routes.append(route)
                route_map_by_user[user_id] = keys_for_user

        if not unique_routes:
            return results

        parsed = run_scan_for_routes(unique_routes, persist=False)
        parsed_by_key: dict[tuple[str, str, str, str, str], List[dict]] = {}
        for row in parsed:
            trip_type = "roundtrip" if (row.get("inbound_date") or "").strip() else "oneway"
            key = (
                str(row.get("origin") or "").upper(),
                str(row.get("destination") or "").upper(),
                row.get("outbound_date") or "",
                row.get("inbound_date") or "",
                trip_type,
            )
            parsed_by_key.setdefault(key, []).append(row)

        for user_id, run_id in run_map.items():
            try:
                user_rows: List[dict] = []
                user_db = Database(get_db_path())
                try:
                    user_db.clear_results_for_user(user_id)
                    for key in route_map_by_user.get(user_id, set()):
                        for row in parsed_by_key.get(key, []):
                            result = FlightResult(
                                site=str(row.get("site") or ""),
                                origin=str(row.get("origin") or ""),
                                destination=str(row.get("destination") or ""),
                                outbound_date=str(row.get("outbound_date") or ""),
                                inbound_date=str(row.get("inbound_date") or ""),
                                trip_type="roundtrip" if (row.get("inbound_date") or "").strip() else "oneway",
                                price=row.get("price"),
                                currency=str(row.get("currency") or "BRL"),
                                url=str(row.get("url") or ""),
                                notes=str(row.get("notes") or ""),
                                best_vendor=str(row.get("best_vendor") or ""),
                                best_vendor_price=row.get("best_vendor_price"),
                                booking_options_json=str(row.get("booking_options_json") or ""),
                            )
                            persisted_row = _store_result(
                                user_db,
                                RouteQuery(
                                    origin=result.origin,
                                    destination=result.destination,
                                    outbound_date=result.outbound_date,
                                    inbound_date=result.inbound_date,
                                    trip_type=result.trip_type,
                                ),
                                result,
                                user_id=user_id,
                                persist=True,
                            )
                            user_rows.append(persisted_row)
                finally:
                    user_db.conn.close()
                max_price = get_user_max_display_price(user_id)
                parsed_for_display = filter_rows_by_max_price(user_rows, max_price)
                msg = build_full_scan_message(parsed_for_display, trigger="agendada-usuario")
                send_user_telegram_message(user_id, msg, image_rows=parsed_for_display)
                total_ok = len([r for r in parsed_for_display if r.get("price") is not None])
                summary = f"ok: {total_ok}/{len(parsed_for_display)} exibidos"
                _finish_user_run(conn, run_id, "ok", summary)
                print(f"[user-scan] execução usuário={user_id} concluída")
                results.append({"user_id": user_id, "status": "ok", "summary": summary})
            except Exception as e:
                message = str(e)[:200]
                _finish_user_run(conn, run_id, "error", message)
                print(f"[user-scan] erro usuário={user_id}: {message}")
                results.append({"user_id": user_id, "status": "error", "summary": message})
    finally:
        conn.close()
    return results


def _user_scan_scheduler_loop():
    while True:
        process_due_user_scans_once()
        time.sleep(max(30, USER_SCAN_POLL_SECONDS))


def start_user_scan_scheduler_if_needed():
    global _user_scheduler_started
    if _user_scheduler_started:
        return

    if SERVERLESS_ENV:
        print("[user-scan] indisponível em runtime serverless")
        return

    if not INTERNAL_SCHEDULER_ENABLED:
        print("[user-scan] desativado por SKYSCANNER_INTERNAL_SCHEDULER=0")
        return

    is_reloader_main = os.getenv("WERKZEUG_RUN_MAIN") == "true"
    is_debug = os.getenv("FLASK_DEBUG") == "1"
    if is_debug and not is_reloader_main:
        return

    t = threading.Thread(target=_user_scan_scheduler_loop, daemon=True)
    t.start()
    _user_scheduler_started = True
    print(f"[user-scan] scheduler ligado: verificação a cada {USER_SCAN_POLL_SECONDS}s")


def get_db_path() -> str:
    configured = str(CONFIG.get("db_path", "flight_tracker_browser.db"))
    # Em Vercel/Lambda, /var/task é read-only; use /tmp (gravável)
    if os.getenv("VERCEL") or configured.startswith("/var/task"):
        return "/tmp/flight_tracker_browser.db"
    return configured


def send_telegram_message_to(text: str, token: Optional[str] = None, chat_id: Optional[str] = None) -> None:
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
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


def _group_scan_rows_for_image(rows: List[dict]) -> List[Tuple[str, list[dict]]]:
    idas = [
        r for r in rows
        if str(r.get("origin", "")).upper() == "PVH" and str(r.get("destination", "")).upper() != "PVH"
    ]
    voltas = [r for r in rows if str(r.get("destination", "")).upper() == "PVH"]

    idas_ok = sorted([r for r in idas if r.get("price") is not None], key=lambda r: float(r["price"]))
    voltas_ok = sorted([r for r in voltas if r.get("price") is not None], key=lambda r: float(r["price"]))

    groups = []
    if idas_ok:
        groups.append(("IDAS", idas_ok))
    if voltas_ok:
        groups.append(("VOLTAS PARA PVH", voltas_ok))
    return groups


def _best_vendor_label(row: dict) -> str:
    vendor = (row.get("best_vendor") or row.get("site") or "").strip()
    if not vendor:
        vendor = "N/D"
    vendor_price = row.get("best_vendor_price")
    if isinstance(vendor_price, (int, float)):
        return f"{vendor} ({format_brl(vendor_price)})"
    return vendor


def build_scan_results_image(rows: List[dict]) -> Optional[str]:
    groups = _group_scan_rows_for_image(rows)
    if not groups:
        return None

    title_font = _load_font(22, bold=True)
    header_font = _load_font(18, bold=True)
    body_font = _load_font(18)
    small_font = _load_font(15)

    padding_x = 18
    padding_y = 16
    row_h = 40
    section_h = 36
    title_h = 34
    meta_h = 28
    col_widths = [170, 140, 140, 290]
    headers = ["Rota", "Data voo", "Preço", "Onde comprar mais barato"]
    table_w = sum(col_widths)
    width = table_w + padding_x * 2

    row_count = sum(len(items) for _, items in groups)
    height = (
        padding_y * 2
        + title_h
        + meta_h
        + row_h
        + sum(section_h + len(items) * row_h for _, items in groups)
        + 24
    )

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
    draw.text((x0, y), datetime.now().strftime("%Y-%m-%d %H:%M"), font=small_font, fill=colors["muted"])
    y += meta_h

    x = x0
    for idx, header in enumerate(headers):
        w = col_widths[idx]
        draw.rectangle([x, y, x + w, y + row_h], fill=colors["header_bg"], outline=colors["border"])
        draw.text((x + 12, y + 10), header, font=header_font, fill=colors["text"])
        x += w
    y += row_h

    for group_idx, (title, items) in enumerate(groups):
        section_bg = colors["section_return_bg"] if title.startswith("VOLTAS") else colors["section_bg"]
        draw.rectangle([x0, y, x0 + table_w, y + section_h], fill=section_bg, outline=colors["border"])
        caption = f"{title} (menor → maior preço)"
        caption_bbox = draw.textbbox((0, 0), caption, font=header_font)
        caption_width = caption_bbox[2] - caption_bbox[0]
        caption_x = x0 + max(0, (table_w - caption_width) / 2)
        draw.text((caption_x, y + 8), caption, font=header_font, fill=colors["text"])
        y += section_h

        highlight_axis = "destination" if title.startswith("IDAS") else "origin"
        for item_idx, row in enumerate(items):
            fill = colors["row_a"] if item_idx % 2 == 0 else colors["row_b"]
            draw.rectangle([x0, y, x0 + table_w, y + row_h], fill=fill, outline=colors["border"])

            origin_txt = row.get('origin', '')
            destination_txt = row.get('destination', '')
            highlight_value = (row.get(highlight_axis) or "").upper()
            highlight_color = CITY_HIGHLIGHT_COLORS.get(highlight_value, colors["text"])
            if highlight_axis == "origin":
                draw.text((x0 + 12, y + 10), origin_txt, font=body_font, fill=highlight_color)
                origin_w = draw.textlength(origin_txt, font=body_font)
                draw.text((x0 + 12 + origin_w, y + 10), f" → {destination_txt}", font=body_font, fill=colors["text"])
            else:
                origin_part = f"{origin_txt} → "
                draw.text((x0 + 12, y + 10), origin_part, font=body_font, fill=colors["text"])
                dest_x = x0 + 12 + draw.textlength(origin_part, font=body_font)
                draw.text((dest_x, y + 10), destination_txt, font=body_font, fill=highlight_color)

            date_txt = str(row.get("outbound_date") or "")
            price_txt = row.get("price_fmt") or format_brl(row.get("price"))
            vendor_txt = _best_vendor_label(row)

            date_x = x0 + col_widths[0] + 12
            badge_fill = colors["date_badge_return"] if title.startswith("VOLTAS") else colors["date_badge"]
            badge_bbox = draw.textbbox((0, 0), date_txt, font=small_font)
            badge_w = (badge_bbox[2] - badge_bbox[0]) + 18
            draw.rounded_rectangle([date_x, y + 8, date_x + badge_w, y + 30], radius=8, fill=badge_fill)
            draw.text((date_x + 9, y + 11), date_txt, font=small_font, fill=colors["text"])

            price_x = x0 + col_widths[0] + col_widths[1] + 12
            draw.text((price_x, y + 10), price_txt, font=header_font, fill=colors["price"])

            vendor_x = x0 + col_widths[0] + col_widths[1] + col_widths[2] + 12
            draw.text((vendor_x, y + 10), vendor_txt[:40], font=body_font, fill=colors["text"])
            y += row_h

        if group_idx != len(groups) - 1:
            y += 10
    tmp = NamedTemporaryFile(prefix="telegram_scan_", suffix=".png", delete=False)
    tmp.close()
    image.save(tmp.name, format="PNG")
    return tmp.name


def send_telegram_photo_to(image_path: str, caption: Optional[str] = None, token: Optional[str] = None, chat_id: Optional[str] = None) -> None:
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id or not image_path or not os.path.exists(image_path):
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with open(image_path, "rb") as image_file:
        requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption or ""},
            files={"photo": image_file},
            timeout=60,
        ).raise_for_status()


def send_telegram_message(text: str, image_rows: Optional[List[dict]] = None) -> None:
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


def send_user_telegram_message(user_id: int, text: str, image_rows: Optional[List[dict]] = None) -> None:
    conn = connect_db(auth_db_path())
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


def extract_final_price_source(notes: Optional[str]) -> str:
    txt = (notes or "")
    m = re.search(r"final_price_source=([^|]+)", txt)
    if not m:
        return ""
    return (m.group(1) or "").strip()


def _extract_maxmilhas_prices_from_notes(notes: Optional[str]) -> List[float]:
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


def get_user_max_display_price(user_id: Optional[int]) -> Optional[float]:
    if not user_id:
        return None
    conn = connect_db(auth_db_path())
    try:
        row = conn.execute(
            "SELECT max_price_display FROM user_cron WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        value = row["max_price_display"]
        if value is None:
            return None
        return float(value)
    finally:
        conn.close()


def filter_rows_by_max_price(rows: List[dict], max_price: Optional[float]) -> List[dict]:
    if max_price is None:
        return rows
    return [
        row for row in rows
        if row.get("price") is None or float(row["price"]) <= max_price
    ]


def get_global_max_price_limit() -> Optional[float]:
    conn = connect_db(auth_db_path())
    try:
        rows = conn.execute(
            "SELECT max_price_display FROM user_cron WHERE max_price_display IS NOT NULL"
        ).fetchall()
        values = [float(row["max_price_display"]) for row in rows if row["max_price_display"] is not None]
        return min(values) if values else None
    finally:
        conn.close()


def get_global_cron_settings() -> dict:
    conn = connect_db(auth_db_path())
    try:
        ensure_app_settings_defaults(conn)
        row = conn.execute(
            "SELECT cron_enabled, schedule_minutes FROM app_settings WHERE id = 1"
        ).fetchone()
        return {
            "cron_enabled": int(row["cron_enabled"] or 0) if row else 1,
            "schedule_minutes": max(1, int(row["schedule_minutes"] or DEFAULT_SCHEDULE_MINUTES)) if row else DEFAULT_SCHEDULE_MINUTES,
        }
    finally:
        conn.close()


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _format_time_hhmm(value: Optional[str]) -> str:
    dt = _parse_iso_datetime(value)
    return dt.strftime("%H:%M") if dt else "sem execucao"


def _build_next_run_label(last_started_at: Optional[str], cron_enabled: int, schedule_minutes: int) -> str:
    if int(cron_enabled) != 1:
        return "cron desativado"
    last_started = _parse_iso_datetime(last_started_at)
    if not last_started:
        return "aguardando primeira"
    return (last_started + timedelta(minutes=max(1, schedule_minutes))).strftime("%H:%M")


def _format_brl_input(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _parse_brl_input(value: str) -> Optional[float]:
    raw = (value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    return max(0.0, float(normalized))


def _to_route(query_args) -> RouteQuery:
    origin = query_args.get("origin", "").strip().upper()
    destination = query_args.get("destination", "").strip().upper()
    outbound_date = query_args.get("outbound_date", "")
    inbound_date = query_args.get("inbound_date", "")
    trip_type = "roundtrip" if inbound_date else "oneway"

    if not origin:
        raise ValueError("Parâmetro obrigatório: origin")
    if not destination:
        raise ValueError("Parâmetro obrigatório: destination")
    if not outbound_date:
        raise ValueError("Parâmetro obrigatório: outbound_date (YYYY-MM-DD)")

    return RouteQuery(
        origin=origin,
        destination=destination,
        outbound_date=outbound_date,
        inbound_date=inbound_date,
        trip_type=trip_type,
    )


def _resolve_requested_sources(query_args, route: RouteQuery) -> List[str]:
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
    return app.send_static_file("index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "voobot-monitor"})


@app.route("/rotas", methods=["GET"])
def rotas():
    user = current_user()
    if not user:
        return jsonify({"error": "forbidden"}), 403
    conn = get_auth_db()
    routes = _build_user_routes(conn, int(user["id"]))
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

    if not user:
        return jsonify({"error": "forbidden"}), 403

    db = Database(get_db_path())
    db.clear_results_for_user(int(user["id"]))
    requested_sources = _resolve_requested_sources(request.args, route)
    if not requested_sources:
        return jsonify({"error": "A MaxMilhas atualmente só está habilitada para consultas somente ida."}), 400

    results = []
    cached_sources = []
    sources_to_fetch = []
    for source in requested_sources:
        cached = _get_manual_cached_result(route, source)
        if cached is not None:
            cached_result = FlightResult(
                site=str(cached.get("site") or ""),
                origin=str(cached.get("origin") or route.origin),
                destination=str(cached.get("destination") or route.destination),
                outbound_date=str(cached.get("outbound_date") or route.outbound_date),
                inbound_date=str(cached.get("inbound_date") or route.inbound_date),
                trip_type="roundtrip" if (cached.get("inbound_date") or "").strip() else "oneway",
                price=cached.get("price"),
                currency=str(cached.get("currency") or "BRL"),
                url=str(cached.get("url") or ""),
                notes=str(cached.get("notes") or ""),
                best_vendor=str(cached.get("best_vendor") or ""),
                best_vendor_price=cached.get("best_vendor_price"),
                booking_options_json=str(cached.get("booking_options_json") or ""),
            )
            cached = _store_result(db, route, cached_result, user_id=int(user["id"]), persist=True)
            cached["cache_hit"] = True
            results.append(cached)
            cached_sources.append(source)
        else:
            sources_to_fetch.append(source)

    if not sources_to_fetch:
        requested_sources = []
    else:
        requested_sources = sources_to_fetch

    if requested_sources:
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

                    row = _store_result(db, route, result, user_id=int(user["id"]), persist=True)
                    row["cache_hit"] = False
                    _set_manual_cached_result(route, source, row)
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
            f"Data: {date_color_token(route.outbound_date)[0]} {route.outbound_date}\n"
            + (f" / {route.inbound_date}" if route.inbound_date else "")
            + "\n"
            + "Resultados:\n"
            + "\n".join(detalhes)
        )
        if user:
            send_user_telegram_message(int(user["id"]), resumo)
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
                "cache_hit": bool(chosen.get("cache_hit")),
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
    user = current_user()
    if not user:
        return jsonify({"error": "forbidden"}), 403
    limit = request.args.get("limit", default=20, type=int)
    limit = max(1, min(limit, 200))

    db_path = Path(get_db_path())
    if not mysql_enabled() and not db_path.exists():
        return jsonify({"total": 0, "items": []})

    db = Database(str(db_path))
    rows = db.conn.execute(
        """
        SELECT created_at, site, origin, destination, outbound_date, inbound_date,
               price, currency, price_band, notes, url,
               best_vendor, best_vendor_price, booking_options_json
        FROM results
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(user["id"]), limit),
    ).fetchall()

    items = [dict(r) for r in rows]
    for item in items:
        item["final_price_source"] = extract_final_price_source(item.get("notes"))
    max_price = get_user_max_display_price(int(user["id"])) if user else None
    items = filter_rows_by_max_price(items, max_price)
    return jsonify({"total": len(items), "items": items})


@app.route("/historico/limpar", methods=["POST"])
def limpar_historico():
    user = current_user()
    if not user:
        return redirect(url_for("auth_login"))
    if not is_admin_user(user):
        return jsonify({"error": "forbidden"}), 403
    db = Database(get_db_path())
    deleted = db.clear_results_for_user(int(user["id"]))
    return jsonify({"status": "ok", "deleted": deleted})


@app.route("/cron", methods=["GET"])
def cron():
    user = current_user()
    if not user:
        return redirect(url_for("auth_login"))
    if not is_admin_user(user):
        return jsonify({"error": "forbidden"}), 403
    db = get_auth_db()
    routes = _build_user_routes(db, int(user["id"]))
    if not routes:
        return jsonify({"status": "ok", "resultados": [], "last_run_at": _scan_last_run_at, "summary": "Nenhuma rota configurada para este usuario."})
    results_db = Database(get_db_path())
    try:
        results_db.clear_results_for_user(int(user["id"]))
    finally:
        results_db.conn.close()
    parsed = run_scan_for_routes(routes, user_id=int(user["id"]), persist=True)
    max_price = get_user_max_display_price(int(user["id"]))
    parsed_filtered = filter_rows_by_max_price(parsed, max_price)
    notify_full_scan(parsed, trigger="manual", max_price=max_price)
    return jsonify({"status": "ok", "resultados": parsed_filtered, "last_run_at": _scan_last_run_at})


@app.route("/internal/cron", methods=["GET"])
def internal_cron():
    secret = os.getenv("CRON_SECRET", "").strip()
    provided = request.args.get("token", "").strip()
    if not secret or provided != secret:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    results = process_due_user_scans_once()
    return jsonify({"ok": True, "processed": len(results), "results": results})


@app.route("/cron-stream", methods=["GET"])
def cron_stream():
    user = current_user()
    if not user:
        return redirect(url_for("auth_login"))
    if not is_admin_user(user):
        return jsonify({"error": "forbidden"}), 403
    def event_stream():
        max_price = get_user_max_display_price(int(user["id"])) if user else None
        auth_conn = get_auth_db()
        routes = _build_user_routes(auth_conn, int(user["id"]))
        if not routes:
            yield f"data: {json.dumps({'type': 'start', 'total': 0})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'message': 'Nenhuma rota configurada para este usuario.'})}\n\n"
            return
        total = sum(2 if not (route.inbound_date or "").strip() else 1 for route in routes)
        yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"

        # evita concorrência com auto-scan/execuções manuais
        if not _scan_lock.acquire(blocking=False):
            yield f"data: {json.dumps({'type': 'error', 'message': 'Já existe uma varredura em andamento. Tente novamente em instantes.'})}\n\n"
            return

        try:
            parsed = []
            db = Database(get_db_path())
            db.clear_results_for_user(int(user["id"]))
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=bool(CONFIG.get("headless", True)))
                scraper = GoogleFlightsScraper(browser)

                for idx, route in enumerate(routes, start=1):
                    result = _search_google_result(scraper, route)
                    row = _store_result(db, route, result, user_id=int(user["id"]), persist=True)
                    parsed.append(row)
                    if row.get("price") is None or max_price is None or float(row["price"]) <= max_price:
                        payload = {"type": "row", "index": len(parsed), "total": total, "item": row}
                        yield f"data: {json.dumps(payload)}\n\n"
                        time.sleep(0.05)

                    maxmilhas_result = _search_maxmilhas_result(p, route)
                    if maxmilhas_result is not None:
                        row = _store_result(db, route, maxmilhas_result, user_id=int(user["id"]), persist=True)
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
        """
    )

def auth_db_path() -> str:
    return get_db_path()


def get_auth_db():
    if "auth_db" not in g:
        g.auth_db = connect_db(auth_db_path())
    return g.auth_db


def _current_iso_ts() -> str:
    return datetime.now().isoformat()

def _ensure_user_telegram_defaults(conn, user_id: int) -> None:
    exists = conn.execute("SELECT 1 FROM user_telegram WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
    if exists:
        return
    conn.execute("INSERT INTO user_telegram (user_id, bot_token, chat_id, updated_at) VALUES (?, ?, ?, ?)",
        (user_id, "", "", _current_iso_ts()),
    )
    conn.commit()

def _ensure_user_cron_defaults(conn, user_id: int) -> None:
    exists = conn.execute("SELECT 1 FROM user_cron WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
    if exists:
        return
    now = _current_iso_ts()
    conn.execute("INSERT INTO user_cron (user_id, enabled, max_price_display, updated_at, last_run_at) VALUES (?, 1, NULL, ?, ?)",
        (user_id, now, now),
    )
    conn.commit()


def ensure_app_settings_defaults(conn) -> None:
    exists = conn.execute("SELECT 1 FROM app_settings WHERE id = 1").fetchone()
    if exists:
        return
    conn.execute(
        "INSERT INTO app_settings (id, cron_enabled, schedule_minutes, updated_at) VALUES (1, 1, ?, ?)",
        (DEFAULT_SCHEDULE_MINUTES, _current_iso_ts()),
    )
    conn.commit()


def ensure_user_defaults(conn, user_id: int) -> None:
    _ensure_user_telegram_defaults(conn, user_id)
    _ensure_user_cron_defaults(conn, user_id)
    ensure_app_settings_defaults(conn)






def init_auth_tables():
    db = connect_db(auth_db_path())
    if db.backend == "mysql":
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255) NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role VARCHAR(16) NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_routes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                origin VARCHAR(10) NOT NULL,
                destination VARCHAR(10) NOT NULL,
                outbound_date VARCHAR(32) NOT NULL,
                inbound_date VARCHAR(32) DEFAULT '',
                active TINYINT(1) DEFAULT 1,
                created_at TEXT NOT NULL,
                INDEX idx_user_routes_user_id (user_id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_telegram (
                user_id INT PRIMARY KEY,
                bot_token TEXT,
                chat_id TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_cron (
                user_id INT PRIMARY KEY,
                enabled TINYINT(1) DEFAULT 1,
                every_hours INT DEFAULT 3,
                schedule_minutes INT DEFAULT 60,
                max_price_display DOUBLE NULL,
                updated_at TEXT NOT NULL,
                last_run_at TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                id INT PRIMARY KEY,
                cron_enabled TINYINT(1) DEFAULT 1,
                schedule_minutes INT DEFAULT 60,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_runs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status VARCHAR(32) NOT NULL,
                summary TEXT,
                `trigger` VARCHAR(64) DEFAULT 'manual-user',
                INDEX idx_user_runs_user_id (user_id)
            )
            """
        )
    else:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
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
        db.execute(
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
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_cron (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 1,
                every_hours INTEGER DEFAULT 3,
                schedule_minutes INTEGER DEFAULT 60,
                max_price_display REAL,
                updated_at TEXT NOT NULL,
                last_run_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                id INTEGER PRIMARY KEY,
                cron_enabled INTEGER DEFAULT 1,
                schedule_minutes INTEGER DEFAULT 60,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                summary TEXT,
                `trigger` TEXT DEFAULT 'manual-user',
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )

    for column, definition in [
        ("last_run_at", "last_run_at TEXT"),
        ("schedule_minutes", "schedule_minutes INT DEFAULT 60" if db.backend == "mysql" else "schedule_minutes INTEGER"),
        ("max_price_display", "max_price_display DOUBLE NULL" if db.backend == "mysql" else "max_price_display REAL"),
    ]:
        ensure_column(db, "user_cron", column, definition)

    ensure_column(
        db,
        "user_runs",
        "trigger",
        "`trigger` VARCHAR(64) DEFAULT 'manual-user'" if db.backend == "mysql" else "`trigger` TEXT DEFAULT 'manual-user'",
    )
    ensure_column(
        db,
        "users",
        "role",
        "role VARCHAR(16) NOT NULL DEFAULT 'user'" if db.backend == "mysql" else "role TEXT NOT NULL DEFAULT 'user'",
    )
    ensure_column(
        db,
        "app_settings",
        "cron_enabled",
        "cron_enabled TINYINT(1) DEFAULT 1" if db.backend == "mysql" else "cron_enabled INTEGER DEFAULT 1",
    )
    ensure_column(
        db,
        "app_settings",
        "schedule_minutes",
        "schedule_minutes INT DEFAULT 60" if db.backend == "mysql" else "schedule_minutes INTEGER DEFAULT 60",
    )

    try:
        db.execute("UPDATE users SET role = COALESCE(role, 'user') WHERE role IS NULL OR role = ''")
        db.execute(
            "UPDATE user_cron SET schedule_minutes = COALESCE(schedule_minutes, CASE WHEN every_hours > 0 THEN every_hours * 60 END, ?) WHERE schedule_minutes IS NULL",
            (DEFAULT_SCHEDULE_MINUTES,),
        )
        db.execute("UPDATE user_cron SET every_hours = 0 WHERE schedule_minutes < 60")
    except DatabaseOperationalError:
        pass

    db.commit()
    ensure_app_settings_defaults(db)
    db.close()


@app.teardown_appcontext
def close_auth_db(_exc):
    db = g.pop("auth_db", None)
    if db is not None:
        db.close()


def _user_field(user, field: str, default=None):
    if not user:
        return default
    try:
        value = user[field]
    except Exception:
        value = getattr(user, field, default)
    return default if value is None else value


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth_login"))
        if not current_user():
            session.clear()
            return redirect(url_for("auth_login"))
        return fn(*args, **kwargs)

    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("auth_login"))
        if str(_user_field(user, "role", "user")) != "admin":
            if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
                return jsonify({"error": "forbidden"}), 403
            return redirect(url_for("painel", _anchor="rotas"))
        return fn(*args, **kwargs)

    return wrapper


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_auth_db()
    return db.execute("SELECT id, email, role FROM users WHERE id = ?", (uid,)).fetchone()


def is_admin_user(user) -> bool:
    return bool(user and str(_user_field(user, "role", "user")) == "admin")


def _user_telegram_upsert_sql() -> str:
    if mysql_enabled():
        return (
            "INSERT INTO user_telegram (user_id, bot_token, chat_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON DUPLICATE KEY UPDATE "
            "bot_token = VALUES(bot_token), "
            "chat_id = VALUES(chat_id), "
            "updated_at = VALUES(updated_at)"
        )
    return """
        INSERT INTO user_telegram (user_id, bot_token, chat_id, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          bot_token = excluded.bot_token,
          chat_id = excluded.chat_id,
          updated_at = excluded.updated_at
    """


def _user_cron_upsert_sql() -> str:
    if mysql_enabled():
        return (
            "INSERT INTO user_cron (user_id, enabled, max_price_display, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON DUPLICATE KEY UPDATE "
            "enabled = VALUES(enabled), "
            "max_price_display = VALUES(max_price_display), "
            "updated_at = VALUES(updated_at)"
        )
    return """
        INSERT INTO user_cron (user_id, enabled, max_price_display, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          enabled = excluded.enabled,
          max_price_display = excluded.max_price_display,
          updated_at = excluded.updated_at
    """


def _app_settings_upsert_sql() -> str:
    if mysql_enabled():
        return (
            "INSERT INTO app_settings (id, cron_enabled, schedule_minutes, updated_at) "
            "VALUES (1, ?, ?, ?) "
            "ON DUPLICATE KEY UPDATE "
            "cron_enabled = VALUES(cron_enabled), "
            "schedule_minutes = VALUES(schedule_minutes), "
            "updated_at = VALUES(updated_at)"
        )
    return """
        INSERT INTO app_settings (id, cron_enabled, schedule_minutes, updated_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          cron_enabled = excluded.cron_enabled,
          schedule_minutes = excluded.schedule_minutes,
          updated_at = excluded.updated_at
    """


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
                    "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                    (email, generate_password_hash(password), "user", datetime.now().isoformat()),
                )
                db.commit()
                return redirect(url_for("auth_login"))
            except DatabaseIntegrityError:
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
    is_admin = is_admin_user(user)
    restart_status = (request.args.get("restart_status") or "").strip().lower()
    restart_message = (request.args.get("restart_message") or "").strip()
    ensure_user_defaults(db, user["id"])
    routes = db.execute(
        "SELECT id, origin, destination, outbound_date, inbound_date, active FROM user_routes WHERE user_id = ? ORDER BY id DESC",
        (user["id"],),
    ).fetchall()
    tg = db.execute("SELECT bot_token, chat_id FROM user_telegram WHERE user_id = ?", (user["id"],)).fetchone()
    cron = db.execute("SELECT enabled, max_price_display, last_run_at FROM user_cron WHERE user_id = ?", (user["id"],)).fetchone()
    cron_settings = get_global_cron_settings()
    cron_minutes = int(cron_settings["schedule_minutes"])
    cron_enabled = int(cron_settings["cron_enabled"])
    cron_max_price = ""
    if cron is not None:
        if cron["max_price_display"] is not None:
            cron_max_price = _format_brl_input(float(cron["max_price_display"]))
    last_run = db.execute("SELECT started_at, finished_at, status, summary FROM user_runs WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
    last_finished_label = _format_time_hhmm(last_run["finished_at"] if last_run else None)
    next_run_label = _build_next_run_label(cron["last_run_at"] if cron else None, cron_enabled, cron_minutes)

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
                <a href='#consultas'><i class='bi bi-window me-2'></i>Consultas</a>
                <a href='#rotas'><i class='bi bi-signpost-split me-2'></i>Rotas</a>
                <a href='#telegram'><i class='bi bi-telegram me-2'></i>Telegram</a>
                {% if is_admin %}
                <a href='#cron'><i class='bi bi-clock-history me-2'></i>Cron</a>
                {% endif %}
                <a href='{{ url_for("auth_logout") }}'><i class='bi bi-box-arrow-right me-2'></i>Sair</a>
              </aside>
              <main class='col-md-9 col-lg-10 p-0'>
                <div class='topbar d-flex justify-content-between align-items-center px-4 py-3'>
                  <div>
                    <strong>Painel</strong> <span class='text-muted'>/ Dashboard</span>
                    <div class='small text-muted'>Ultima consulta: {{ last_finished_label }} | Proxima: {{ next_run_label }}</div>
                  </div>
                  <div class='d-flex align-items-center gap-2'>
                    <button class='btn btn-sm btn-outline-secondary' type='button' onclick='toggleSidebar()'><i class='bi bi-list'></i></button>
                    <button class='btn btn-sm btn-outline-secondary' type='button' onclick='toggleTheme()'><i class='bi bi-moon-stars'></i></button>
                    <div class='text-muted small'>{{user['email']}}{% if is_admin %} · admin{% endif %}</div>
                  </div>
                </div>
                <div class='p-4'>
                {% if restart_message %}
                  <div class='alert alert-{% if restart_status == "success" %}success{% else %}danger{% endif %} mb-3'>{{ restart_message }}</div>
                {% endif %}
                <div class='row g-3 mb-3'>
                  <div class='col-md-4'><div class='card kpi'><div class='card-body'><div class='text-muted'>Rotas</div><div class='h4 mb-0'>{{ routes|length }}</div></div></div></div>
                  {% if is_admin %}
                  <div class='col-md-4'><div class='card kpi'><div class='card-body'><div class='text-muted'>Cron</div><div class='h6 mb-0'>{% if cron_enabled %}Ativo{% else %}Inativo{% endif %} ({{ cron_minutes }} min)</div></div></div></div>
                  {% endif %}
                  <div class='col-md-4'><div class='card kpi'><div class='card-body'><div class='text-muted'>Última execução</div><div class='small mb-0'>{% if last_run %}{{last_run['status']}}{% else %}sem execução{% endif %}</div></div></div></div>
                </div>

                <div class='card mb-3 shadow-sm dashboard-section' id='rotas'>
                  <div class='card-header'><i class='bi bi-signpost-split me-2'></i>Rotas configuradas</div>
                  <div class='card-body'>
                    <form method='post' action='{{ url_for("add_route") }}' class='row g-2 mb-3 align-items-end' autocomplete='off'>
                      <div class='col-md-2'>
                        <label class='form-label small text-uppercase mb-1'>Origem</label>
                        <select class='form-select form-select-sm' name='origin' required>
                          <option value='' selected>Selecione</option>
                          {% for code, label in airport_options %}
                            <option value='{{ code }}'>{{ label }}</option>
                          {% endfor %}
                        </select>
                      </div>
                      <div class='col-md-2'>
                        <label class='form-label small text-uppercase mb-1'>Destino</label>
                        <select class='form-select form-select-sm' name='destination' required>
                          <option value='' selected>Selecione</option>
                          {% for code, label in airport_options %}
                            <option value='{{ code }}'>{{ label }}</option>
                          {% endfor %}
                        </select>
                      </div>
                      <div class='col-md-3'>
                        <label class='form-label small text-uppercase mb-1'>Ida</label>
                          <input class='form-control form-control-sm' name='outbound_date' type='date' value='' autocomplete='off' required>
                      </div>
                      <div class='col-md-3'>
                        <label class='form-label small text-uppercase mb-1'>Volta</label>
                          <input class='form-control form-control-sm' name='inbound_date' type='date' value='' autocomplete='off'>
                      </div>
                      <div class='col-md-2 d-grid'>
                        <button class='btn btn-primary btn-sm' type='submit'>Adicionar</button>
                      </div>
                    </form>
                    <form method='post' action='{{ url_for("save_route_filters") }}' class='row g-2 mb-3 align-items-end'>
                      <div class='col-md-4'>
                        <label class='form-label small text-uppercase mb-1'>Filtro de valor</label>
                        <input class='form-control form-control-sm js-brl-input' name='max_price_display' type='text' inputmode='decimal' placeholder='Ex: 1.250,00' value='{{ cron_max_price }}'>
                      </div>
                      <div class='col-md-3 d-grid'>
                        <button class='btn btn-outline-primary btn-sm' type='submit'>Salvar filtro</button>
                      </div>
                    </form>
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
                      <div class='row g-2 align-items-end' autocomplete='off'>
                        <div class='col-md-3'>
                          <label class='form-label small text-uppercase'>Origem</label>
                          <select id='origin' class='form-select form-select-sm'>
                            <option value='' selected>Selecione a origem</option>
                            <option value='PVH'>PVH — Porto Velho (RO)</option>
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
                            <option value='' selected>Selecione o destino</option>
                            <option value='JPA'>JPA — João Pessoa (PB)</option>
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
                          <input id='outbound_date' type='date' class='form-control form-control-sm' value='' autocomplete='off' />
                        </div>
                        <div class='col-md-2'>
                          <label class='form-label small text-uppercase'>Volta</label>
                          <input id='inbound_date' type='date' class='form-control form-control-sm' value='' autocomplete='off' />
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
                    {% if is_admin %}
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
                    {% endif %}
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
                  </div>
                </div>

                <div class='card mb-3 shadow-sm dashboard-section d-none' id='telegram'>
                  <div class='card-header'><i class='bi bi-telegram me-2'></i>Telegram do usuário</div>
                  <div class='card-body'>
                    <form method='post' action='{{ url_for("save_telegram") }}' class='row g-2'>
                      <div class='col-md-6'><input class='form-control' name='bot_token' placeholder='Bot token' value='{{ tg["bot_token"] if tg and tg["bot_token"] else "" }}'></div>
                      <div class='col-md-4'><input class='form-control' name='chat_id' placeholder='Chat ID' value='{{ tg["chat_id"] if tg and tg["chat_id"] else "" }}'></div>
                      <div class='col-md-2 d-grid'><button class='btn btn-success' type='submit'>Salvar</button></div>
                    </form>
                  </div>
                </div>

                {% if is_admin %}
                <div class='card shadow-sm dashboard-section d-none' id='cron'>
                  <div class='card-header'><i class='bi bi-clock-history me-2'></i>Cron do usuário</div>
                  <div class='card-body'>
                    <form method='post' action='{{ url_for("save_cron") }}' class='row g-2 align-items-center'>
                      <div class='col-md-2 form-check ms-2'>
                        <input class='form-check-input' type='checkbox' name='enabled' id='enabled' {% if cron_enabled %}checked{% endif %}>
                        <label class='form-check-label' for='enabled'>Ativo</label>
                      </div>
                      <div class='col-md-3'><input class='form-control' name='schedule_minutes' type='number' min='1' max='1440' step='1' value='{{ cron_minutes }}'></div>
                      <div class='col-md-3 d-grid'><button class='btn btn-primary' type='submit'>Salvar</button></div>
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
                {% endif %}


                </div>
              </main>
            </div>
          </div>
        <script src='{{ url_for("static", filename="consulta-app.js") }}'></script>
        <script>
          function formatBrlInputValue(value) {
            const digits = String(value || '').replace(/\\D/g, '');
            if (!digits) return '';
            const cents = Number(digits) / 100;
            return cents.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
          }
          function bindBrlInputs() {
            document.querySelectorAll('.js-brl-input').forEach((el) => {
              el.addEventListener('input', () => {
                el.value = formatBrlInputValue(el.value);
              });
            });
          }
          function resetQuickConsultFields() {
            const ids = ['origin', 'destination', 'outbound_date', 'inbound_date'];
            ids.forEach((id) => {
              const el = document.getElementById(id);
              if (el) el.value = '';
            });
          }
          function showSection(hash) {
            document.querySelectorAll('.dashboard-section').forEach(el => el.classList.add('d-none'));
            var target = document.getElementById(hash);
            if (target) {
              target.classList.remove('d-none');
              localStorage.setItem('adminActiveTab', hash);
            } else {
              document.getElementById('consultas').classList.remove('d-none');
              localStorage.setItem('adminActiveTab', 'consultas');
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
            bindBrlInputs();
            resetQuickConsultFields();
            let hash = window.location.hash.substring(1) || localStorage.getItem('adminActiveTab') || 'consultas';
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
        cron_enabled=cron_enabled,
        cron_minutes=cron_minutes,
        cron_max_price=cron_max_price,
        last_run=last_run,
        last_finished_label=last_finished_label,
        next_run_label=next_run_label,
        airport_options=AIRPORT_OPTIONS,
        restart_command_configured=bool(PANEL_RESTART_COMMAND),
        is_admin=is_admin,
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
            datetime.now().isoformat(),
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
        _user_telegram_upsert_sql(),
        (
            user["id"],
            request.form.get("bot_token", "").strip(),
            request.form.get("chat_id", "").strip(),
            datetime.now().isoformat(),
        ),
    )
    db.commit()
    return redirect(url_for("painel"))


@app.route("/painel/filters", methods=["POST"])
@login_required
def save_route_filters():
    db = get_auth_db()
    user = current_user()
    max_price_display_raw = request.form.get("max_price_display", "").strip()
    max_price_display = _parse_brl_input(max_price_display_raw)
    current = db.execute("SELECT enabled FROM user_cron WHERE user_id = ?", (user["id"],)).fetchone()
    enabled = int(current["enabled"] or 1) if current else 1
    db.execute(
        _user_cron_upsert_sql(),
        (user["id"], enabled, max_price_display, datetime.now().isoformat()),
    )
    db.commit()
    return redirect(url_for("painel", _anchor="rotas"))


@app.route("/painel/run-now", methods=["POST"])
@login_required
@admin_required
def run_now_user():
    user = current_user()
    try:
        run_user_scan(int(user["id"]), trigger="painel-manual", notify=True)
    except RuntimeError as exc:
        return build_restart_redirect(str(exc), level="error")
    return redirect(url_for("painel", _anchor="cron"))


@app.route("/painel/restart", methods=["POST"])
@login_required
@admin_required
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
@admin_required
def save_cron():
    db = get_auth_db()
    enabled = 1 if request.form.get("enabled") else 0
    schedule_minutes = max(1, min(1440, int(request.form.get("schedule_minutes", DEFAULT_SCHEDULE_MINUTES))))
    db.execute(
        _app_settings_upsert_sql(),
        (enabled, schedule_minutes, datetime.now().isoformat()),
    )
    db.commit()
    return redirect(url_for("painel", _anchor="cron"))


def bootstrap_app_runtime() -> None:
    global _runtime_bootstrap_done
    if _runtime_bootstrap_done:
        return
    init_auth_tables()
    normalize_maxmilhas_history()
    start_auto_scan_if_needed()
    _runtime_bootstrap_done = True


bootstrap_app_runtime()


if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "0").strip().lower() in ("1", "true", "yes")
    app.run(debug=debug_mode)
