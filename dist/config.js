"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.AIRPORT_OPTIONS = exports.appConfig = exports.config = void 0;
const dotenv_1 = __importDefault(require("dotenv"));
const node_path_1 = __importDefault(require("node:path"));
dotenv_1.default.config();
const defaults = {
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
    db_path: node_path_1.default.resolve(process.cwd(), "flight_tracker_browser.db"),
    telegram_bot_token: process.env.TELEGRAM_BOT_TOKEN ?? "",
    telegram_chat_id: process.env.TELEGRAM_CHAT_ID ?? "",
    price_alert_brl: 1800,
    drop_alert_percent: 8,
    target_site: "google_flights",
    maxmilhas_min_price: 400,
    maxmilhas_final_price_threshold: 1000,
    scan_workers: 2
};
exports.config = defaults;
exports.appConfig = {
    host: process.env.HOST ?? "0.0.0.0",
    port: Number(process.env.PORT ?? 3000),
    sessionSecret: process.env.SKYSCANNER_SECRET_KEY ?? "dev-change-this-secret",
    fullScanSeconds: Number(process.env.SKYSCANNER_FULL_SCAN_EVERY_SECONDS ?? exports.config.full_scan_seconds),
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
exports.AIRPORT_OPTIONS = [
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
