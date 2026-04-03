import mysql from "mysql2/promise";
import { config, appConfig } from "./config";
import { FlightResult, RouteQuery, ScanRow } from "./types";
import { classifyPrice, extractFinalPriceSource, formatBrl, utcNowIso } from "./utils";

export const db = mysql.createPool({
  host: appConfig.mysql.host,
  port: appConfig.mysql.port,
  user: appConfig.mysql.user,
  password: appConfig.mysql.password,
  database: appConfig.mysql.database,
  waitForConnections: true,
  connectionLimit: appConfig.mysql.connectionLimit,
  queueLimit: 0,
  charset: "utf8mb4"
});

function normalizeTelegramValue(value: string | null | undefined): string {
  const normalized = String(value ?? "").trim();
  if (!normalized || normalized === "TELEGRAM_CHAT_ID" || normalized === "TELEGRAM_BOT_TOKEN") {
    return "";
  }
  return normalized;
}

async function queryRows<T>(sql: string, params: any[] = []): Promise<T[]> {
  const [rows] = await db.query(sql, params);
  return rows as T[];
}

async function queryOne<T>(sql: string, params: any[] = []): Promise<T | undefined> {
  const rows = await queryRows<T>(sql, params);
  return rows[0];
}

async function exec(sql: string, params: any[] = []): Promise<mysql.ResultSetHeader> {
  const [result] = await db.execute(sql, params);
  return result as mysql.ResultSetHeader;
}

export async function initDb(): Promise<void> {
  await db.query(`
    CREATE TABLE IF NOT EXISTS results (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      created_at VARCHAR(40) NOT NULL,
      site VARCHAR(50) NOT NULL,
      origin VARCHAR(10) NOT NULL,
      destination VARCHAR(10) NOT NULL,
      outbound_date VARCHAR(20) NOT NULL,
      inbound_date VARCHAR(20) NOT NULL DEFAULT '',
      price DOUBLE NULL,
      currency VARCHAR(10) NULL,
      url TEXT NULL,
      notes TEXT NULL,
      price_band VARCHAR(20) NULL,
      best_vendor VARCHAR(100) NULL,
      best_vendor_price DOUBLE NULL,
      booking_options_json LONGTEXT NULL,
      INDEX idx_results_route (origin, destination, outbound_date, inbound_date, created_at)
    )
  `);
  await db.query(`
    CREATE TABLE IF NOT EXISTS users (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      email VARCHAR(255) NOT NULL UNIQUE,
      password_hash VARCHAR(255) NOT NULL,
      created_at VARCHAR(40) NOT NULL
    )
  `);
  await db.query(`
    CREATE TABLE IF NOT EXISTS user_routes (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      user_id BIGINT NOT NULL,
      origin VARCHAR(10) NOT NULL,
      destination VARCHAR(10) NOT NULL,
      outbound_date VARCHAR(20) NOT NULL,
      inbound_date VARCHAR(20) NOT NULL DEFAULT '',
      active TINYINT(1) NOT NULL DEFAULT 1,
      created_at VARCHAR(40) NOT NULL,
      INDEX idx_user_routes_user (user_id, active, id)
    )
  `);
  await db.query(`
    CREATE TABLE IF NOT EXISTS user_telegram (
      user_id BIGINT PRIMARY KEY,
      bot_token TEXT NULL,
      chat_id TEXT NULL,
      updated_at VARCHAR(40) NOT NULL
    )
  `);
  await db.query(`
    CREATE TABLE IF NOT EXISTS user_cron (
      user_id BIGINT PRIMARY KEY,
      enabled TINYINT(1) NOT NULL DEFAULT 1,
      every_hours INT NOT NULL DEFAULT 0,
      schedule_minutes INT NULL,
      max_price_display DOUBLE NULL,
      updated_at VARCHAR(40) NOT NULL,
      last_run_at VARCHAR(40) NULL
    )
  `);
  await db.query(`
    CREATE TABLE IF NOT EXISTS user_runs (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      user_id BIGINT NOT NULL,
      started_at VARCHAR(40) NOT NULL,
      finished_at VARCHAR(40) NULL,
      status VARCHAR(20) NOT NULL,
      summary TEXT NULL,
      run_trigger VARCHAR(50) NOT NULL DEFAULT 'manual-user',
      INDEX idx_user_runs_user_status (user_id, status, id)
    )
  `);
}

export async function saveResult(result: FlightResult): Promise<ScanRow> {
  const stats = await statsForRoute({
    origin: result.origin,
    destination: result.destination,
    outboundDate: result.outboundDate,
    inboundDate: result.inboundDate,
    tripType: result.tripType
  });
  const priceBand = classifyPrice(result.price, stats.minPrice, stats.avgPrice);
  await exec(`
    INSERT INTO results (
      created_at, site, origin, destination, outbound_date, inbound_date,
      price, currency, url, notes, price_band, best_vendor, best_vendor_price, booking_options_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `, [
    utcNowIso(),
    result.site,
    result.origin,
    result.destination,
    result.outboundDate,
    result.inboundDate,
    result.price,
    result.currency,
    result.url,
    result.notes,
    priceBand,
    result.bestVendor,
    result.bestVendorPrice,
    result.bookingOptionsJson
  ]);

  return {
    origin: result.origin,
    destination: result.destination,
    outbound_date: result.outboundDate,
    inbound_date: result.inboundDate,
    trip_type: result.tripType,
    price: result.price,
    price_fmt: formatBrl(result.price),
    site: result.site,
    currency: result.currency,
    url: result.url,
    notes: result.notes,
    price_band: priceBand,
    best_vendor: result.bestVendor,
    best_vendor_price: result.bestVendorPrice,
    final_price_source: extractFinalPriceSource(result.notes),
    booking_options_json: result.bookingOptionsJson,
    screenshot_path: result.screenshotPath
  };
}

export async function statsForRoute(route: RouteQuery): Promise<{ minPrice: number | null; avgPrice: number | null; lastPrice: number | null }> {
  const agg = await queryOne<{ min_price: number | null; avg_price: number | null }>(`
    SELECT MIN(price) AS min_price, AVG(price) AS avg_price
    FROM results
    WHERE origin = ? AND destination = ? AND outbound_date = ? AND inbound_date = ? AND price IS NOT NULL
  `, [route.origin, route.destination, route.outboundDate, route.inboundDate]);
  const last = await queryOne<{ price: number }>(`
    SELECT price
    FROM results
    WHERE origin = ? AND destination = ? AND outbound_date = ? AND inbound_date = ? AND price IS NOT NULL
    ORDER BY id DESC
    LIMIT 1
  `, [route.origin, route.destination, route.outboundDate, route.inboundDate]);
  return {
    minPrice: agg?.min_price ?? null,
    avgPrice: agg?.avg_price ?? null,
    lastPrice: last?.price ?? null
  };
}

export async function getHistory(limit: number): Promise<Array<Record<string, unknown>>> {
  const rows = await queryRows<Record<string, unknown>>(`
    SELECT created_at, site, origin, destination, outbound_date, inbound_date,
           price, currency, price_band, notes, url, best_vendor, best_vendor_price, booking_options_json
    FROM results
    ORDER BY id DESC
    LIMIT ?
  `, [limit]);
  return rows.map((row) => ({
    ...row,
    final_price_source: extractFinalPriceSource(String(row.notes ?? ""))
  }));
}

export async function clearHistory(): Promise<number> {
  const result = await exec("DELETE FROM results");
  return result.affectedRows;
}

export async function getUserByEmail(email: string): Promise<{ id: number; email: string; password_hash: string } | undefined> {
  return queryOne<{ id: number; email: string; password_hash: string }>("SELECT id, email, password_hash FROM users WHERE email = ?", [email]);
}

export async function getUserById(id: number): Promise<{ id: number; email: string } | undefined> {
  return queryOne<{ id: number; email: string }>("SELECT id, email FROM users WHERE id = ?", [id]);
}

export async function createUser(email: string, passwordHash: string): Promise<void> {
  await exec("INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)", [email, passwordHash, utcNowIso()]);
}

export async function updateUserPasswordHash(userId: number, passwordHash: string): Promise<void> {
  await exec("UPDATE users SET password_hash = ? WHERE id = ?", [passwordHash, userId]);
}

export async function ensureUserDefaults(userId: number, routes: RouteQuery[]): Promise<void> {
  const routeExists = await queryOne<{ ok: number }>("SELECT 1 AS ok FROM user_routes WHERE user_id = ? LIMIT 1", [userId]);
  if (!routeExists) {
    for (const route of routes) {
      await exec(`
        INSERT INTO user_routes (user_id, origin, destination, outbound_date, inbound_date, active, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
      `, [userId, route.origin, route.destination, route.outboundDate, route.inboundDate, utcNowIso()]);
    }
  }

  const defaultBotToken = normalizeTelegramValue(process.env.TELEGRAM_BOT_TOKEN ?? config.telegram_bot_token ?? "");
  const defaultChatId = normalizeTelegramValue(process.env.TELEGRAM_CHAT_ID ?? config.telegram_chat_id ?? "");
  await exec(`
    INSERT INTO user_telegram (user_id, bot_token, chat_id, updated_at)
    VALUES (?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE
      bot_token = CASE WHEN TRIM(COALESCE(bot_token, '')) IN ('', 'TELEGRAM_BOT_TOKEN') THEN VALUES(bot_token) ELSE bot_token END,
      chat_id = CASE WHEN TRIM(COALESCE(chat_id, '')) IN ('', 'TELEGRAM_CHAT_ID') THEN VALUES(chat_id) ELSE chat_id END,
      updated_at = CASE
        WHEN TRIM(COALESCE(bot_token, '')) IN ('', 'TELEGRAM_BOT_TOKEN')
          OR TRIM(COALESCE(chat_id, '')) IN ('', 'TELEGRAM_CHAT_ID')
        THEN VALUES(updated_at)
        ELSE updated_at
      END
  `, [userId, defaultBotToken, defaultChatId, utcNowIso()]);

  await exec(`
    INSERT INTO user_cron (user_id, enabled, every_hours, schedule_minutes, max_price_display, updated_at, last_run_at)
    VALUES (?, 1, ?, ?, NULL, ?, ?)
    ON DUPLICATE KEY UPDATE user_id = user_id
  `, [userId, Math.floor(config.schedule_minutes / 60), config.schedule_minutes, utcNowIso(), utcNowIso()]);
}

export async function getUserRoutes(userId: number): Promise<RouteQuery[]> {
  const rows = await queryRows<{ origin: string; destination: string; outbound_date: string; inbound_date: string }>(`
    SELECT origin, destination, outbound_date, inbound_date
    FROM user_routes
    WHERE user_id = ? AND active = 1
    ORDER BY id DESC
  `, [userId]);
  return rows.map((row) => ({
    origin: row.origin.toUpperCase(),
    destination: row.destination.toUpperCase(),
    outboundDate: row.outbound_date,
    inboundDate: row.inbound_date ?? "",
    tripType: row.inbound_date ? "roundtrip" : "oneway"
  }));
}

export async function getAllDbRoutes(): Promise<RouteQuery[]> {
  const rows = await queryRows<{ origin: string; destination: string; outbound_date: string; inbound_date: string }>(
    "SELECT origin, destination, outbound_date, inbound_date FROM user_routes WHERE active = 1"
  );
  return rows.map((row) => ({
    origin: row.origin.toUpperCase(),
    destination: row.destination.toUpperCase(),
    outboundDate: row.outbound_date,
    inboundDate: row.inbound_date ?? "",
    tripType: row.inbound_date ? "roundtrip" : "oneway"
  }));
}

export async function addUserRoute(userId: number, route: RouteQuery): Promise<void> {
  await exec(`
    INSERT INTO user_routes (user_id, origin, destination, outbound_date, inbound_date, active, created_at)
    VALUES (?, ?, ?, ?, ?, 1, ?)
  `, [userId, route.origin, route.destination, route.outboundDate, route.inboundDate, utcNowIso()]);
}

export async function updateUserRoute(userId: number, routeId: number, route: RouteQuery): Promise<void> {
  await exec(`
    UPDATE user_routes
    SET origin = ?, destination = ?, outbound_date = ?, inbound_date = ?
    WHERE id = ? AND user_id = ?
  `, [route.origin, route.destination, route.outboundDate, route.inboundDate, routeId, userId]);
}

export async function deleteUserRoute(userId: number, routeId: number): Promise<void> {
  await exec("DELETE FROM user_routes WHERE id = ? AND user_id = ?", [routeId, userId]);
}

export async function saveUserTelegram(userId: number, botToken: string, chatId: string): Promise<void> {
  const normalizedBotToken = normalizeTelegramValue(botToken);
  const normalizedChatId = normalizeTelegramValue(chatId);
  await exec(`
    INSERT INTO user_telegram (user_id, bot_token, chat_id, updated_at)
    VALUES (?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE
      bot_token = VALUES(bot_token),
      chat_id = VALUES(chat_id),
      updated_at = VALUES(updated_at)
  `, [userId, normalizedBotToken, normalizedChatId, utcNowIso()]);
}

export async function getUserTelegram(userId: number): Promise<{ bot_token: string; chat_id: string } | undefined> {
  return queryOne<{ bot_token: string; chat_id: string }>("SELECT bot_token, chat_id FROM user_telegram WHERE user_id = ?", [userId]);
}

export async function saveUserCron(userId: number, enabled: boolean, scheduleMinutes: number, maxPriceDisplay: number | null): Promise<void> {
  await exec(`
    INSERT INTO user_cron (user_id, enabled, every_hours, schedule_minutes, max_price_display, updated_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE
      enabled = VALUES(enabled),
      every_hours = VALUES(every_hours),
      schedule_minutes = VALUES(schedule_minutes),
      max_price_display = VALUES(max_price_display),
      updated_at = VALUES(updated_at)
  `, [userId, enabled ? 1 : 0, scheduleMinutes % 60 === 0 ? Math.floor(scheduleMinutes / 60) : 0, scheduleMinutes, maxPriceDisplay, utcNowIso()]);
}

export async function getUserCron(userId: number): Promise<Record<string, unknown> | undefined> {
  return queryOne<Record<string, unknown>>(
    "SELECT enabled, every_hours, schedule_minutes, max_price_display, last_run_at FROM user_cron WHERE user_id = ?",
    [userId]
  );
}

export async function getUserMaxDisplayPrice(userId: number | null): Promise<number | null> {
  if (!userId) {
    return null;
  }
  const row = await queryOne<{ max_price_display: number | null }>("SELECT max_price_display FROM user_cron WHERE user_id = ?", [userId]);
  return row?.max_price_display ?? null;
}

export async function getGlobalMaxPriceLimit(): Promise<number | null> {
  const rows = await queryRows<{ max_price_display: number }>("SELECT max_price_display FROM user_cron WHERE max_price_display IS NOT NULL");
  if (!rows.length) {
    return null;
  }
  return Math.min(...rows.map((row) => Number(row.max_price_display)));
}

export async function createUserRun(userId: number, trigger: string): Promise<number> {
  const result = await exec(`
    INSERT INTO user_runs (user_id, started_at, status, summary, run_trigger)
    VALUES (?, ?, 'running', '', ?)
  `, [userId, utcNowIso(), trigger]);
  return Number(result.insertId);
}

export async function finishUserRun(runId: number, status: string, summary: string): Promise<void> {
  await exec("UPDATE user_runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?", [utcNowIso(), status, summary, runId]);
}

export async function touchUserCronRun(userId: number): Promise<void> {
  await exec("UPDATE user_cron SET last_run_at = ?, updated_at = ? WHERE user_id = ?", [utcNowIso(), utcNowIso(), userId]);
}

export async function userHasRunningScan(userId: number): Promise<boolean> {
  return Boolean(await queryOne<{ ok: number }>("SELECT 1 AS ok FROM user_runs WHERE user_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1", [userId]));
}

export async function getLastUserRun(userId: number): Promise<Record<string, unknown> | undefined> {
  return queryOne<Record<string, unknown>>(
    "SELECT started_at, finished_at, status, summary FROM user_runs WHERE user_id = ? ORDER BY id DESC LIMIT 1",
    [userId]
  );
}

export async function listSchedulableUsers(): Promise<Array<{ user_id: number; enabled: number; schedule_minutes: number | null; every_hours: number | null }>> {
  return queryRows<{ user_id: number; enabled: number; schedule_minutes: number | null; every_hours: number | null }>(`
    SELECT u.id AS user_id, COALESCE(c.enabled, 1) AS enabled, c.schedule_minutes, c.every_hours
    FROM users u
    LEFT JOIN user_cron c ON c.user_id = u.id
  `);
}

export async function listPanelRoutes(userId: number): Promise<Array<{ id: number; origin: string; destination: string; outbound_date: string; inbound_date: string; active: number }>> {
  return queryRows<{ id: number; origin: string; destination: string; outbound_date: string; inbound_date: string; active: number }>(
    "SELECT id, origin, destination, outbound_date, inbound_date, active FROM user_routes WHERE user_id = ? ORDER BY id DESC",
    [userId]
  );
}
