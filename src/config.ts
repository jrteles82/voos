import dotenv from "dotenv";
import path from "node:path";
import fs from "node:fs";

dotenv.config();

const CONFIG_FILE = path.resolve(process.cwd(), "skyscanner-config.json");

type ConfigShape = {
  origin: string;
  destinations_br: string[];
  destinations_sa: string[];
  enable_south_america: boolean;
  outbound_dates: string[];
  inbound_dates: string[];
  check_every_hours: number;
  full_scan_seconds: number;
  schedule_minutes: number;
  headless: boolean;
  timeout_ms: number;
  settle_seconds: number;
  request_pause_seconds: number;
  db_path: string;
  telegram_bot_token: string;
  telegram_chat_id: string;
  price_alert_brl: number;
  drop_alert_percent: number;
  target_site: string;
  maxmilhas_min_price: number;
  maxmilhas_final_price_threshold: number;
  scan_workers: number;
};

const defaults: ConfigShape = {
  origin: "PVH",
  destinations_br: ["JPA", "REC", "NAT"],
  destinations_sa: [],
  enable_south_america: false,
  outbound_dates: ["2026-06-04", "2026-06-05"],
  inbound_dates: ["2026-06-15", "2026-06-16"],
  check_every_hours: 3,
  full_scan_seconds: 10800,
  schedule_minutes: 180,
  headless: true,
  timeout_ms: 45000,
  settle_seconds: 2,
  request_pause_seconds: 0.2,
  db_path: path.resolve(process.cwd(), "flight_tracker_browser.db"),
  telegram_bot_token: process.env.TELEGRAM_BOT_TOKEN ?? "",
  telegram_chat_id: process.env.TELEGRAM_CHAT_ID ?? "",
  price_alert_brl: 1800,
  drop_alert_percent: 8,
  target_site: "google_flights",
  maxmilhas_min_price: 400,
  maxmilhas_final_price_threshold: 1000,
  scan_workers: 2
};

function normalizeList(value: unknown): string[] {
  if (typeof value === "string") {
    return value.split(",").map((item) => item.trim()).filter(Boolean);
  }
  return Array.isArray(value) ? value.map((item) => String(item).trim()).filter(Boolean) : [];
}

function loadConfig(): ConfigShape {
  if (!fs.existsSync(CONFIG_FILE)) {
    return defaults;
  }

  try {
    const raw = JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8")) as Record<string, unknown>;
    return {
      ...defaults,
      ...raw,
      destinations_br: normalizeList(raw.destinations_br ?? defaults.destinations_br),
      destinations_sa: normalizeList(raw.destinations_sa ?? defaults.destinations_sa),
      outbound_dates: normalizeList(raw.outbound_dates ?? defaults.outbound_dates),
      inbound_dates: normalizeList(raw.inbound_dates ?? defaults.inbound_dates)
    };
  } catch {
    return defaults;
  }
}

export const config = loadConfig();
export const appConfig = {
  host: process.env.HOST ?? "0.0.0.0",
  port: Number(process.env.PORT ?? 3000),
  sessionSecret: process.env.SKYSCANNER_SECRET_KEY ?? "dev-change-this-secret",
  fullScanSeconds: Number(process.env.SKYSCANNER_FULL_SCAN_EVERY_SECONDS ?? config.full_scan_seconds),
  autoScanEnabled: (process.env.SKYSCANNER_AUTO_SCAN ?? "1") === "1",
  internalSchedulerEnabled: (process.env.SKYSCANNER_INTERNAL_SCHEDULER ?? "0") === "1",
  userScanPollSeconds: Number(process.env.SKYSCANNER_USER_SCAN_POLL_SECONDS ?? 60),
  restartCommand: (process.env.SKYSCANNER_RESTART_COMMAND ?? "").trim(),
  cronSecret: (process.env.CRON_SECRET ?? "").trim(),
  mysql: {
    host: process.env.MYSQL_HOST ?? "127.0.0.1",
    port: Number(process.env.MYSQL_PORT ?? 3306),
    user: process.env.MYSQL_USER ?? "root",
    password: process.env.MYSQL_PASSWORD ?? "",
    database: process.env.MYSQL_DATABASE ?? "skyscanner",
    connectionLimit: Number(process.env.MYSQL_CONNECTION_LIMIT ?? 10)
  }
};

export const AIRPORT_OPTIONS: Array<[string, string]> = [
  ["PVH", "PVH — Porto Velho (RO)"],
  ["BPS", "BPS — Porto Seguro (BA)"],
  ["RIO", "RIO — Rio de Janeiro (RJ)"],
  ["SAO", "SAO — São Paulo (SP)"],
  ["BSB", "BSB — Brasília (DF)"],
  ["CGB", "CGB — Cuiabá (MT)"],
  ["GYN", "GYN — Goiânia (GO)"],
  ["MCZ", "MCZ — Maceió (AL)"],
  ["AJU", "AJU — Aracaju (SE)"],
  ["SSA", "SSA — Salvador (BA)"],
  ["FOR", "FOR — Fortaleza (CE)"],
  ["SLZ", "SLZ — São Luís (MA)"],
  ["CGR", "CGR — Campo Grande (MS)"],
  ["BHZ", "BHZ — Belo Horizonte (MG)"],
  ["BEL", "BEL — Belém (PA)"],
  ["JPA", "JPA — João Pessoa (PB)"],
  ["CWB", "CWB — Curitiba (PR)"],
  ["REC", "REC — Recife (PE)"],
  ["THE", "THE — Teresina (PI)"],
  ["NAT", "NAT — Natal (RN)"],
  ["POA", "POA — Porto Alegre (RS)"],
  ["FLN", "FLN — Florianópolis (SC)"],
  ["VIX", "VIX — Vitória (ES)"],
  ["MAO", "MAO — Manaus (AM)"],
  ["RBR", "RBR — Rio Branco (AC)"],
  ["BVB", "BVB — Boa Vista (RR)"],
  ["MCP", "MCP — Macapá (AP)"],
  ["PMW", "PMW — Palmas (TO)"]
];
