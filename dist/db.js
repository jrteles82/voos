"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.db = void 0;
exports.initDb = initDb;
exports.saveResult = saveResult;
exports.statsForRoute = statsForRoute;
exports.getHistory = getHistory;
exports.clearHistory = clearHistory;
exports.getUserByEmail = getUserByEmail;
exports.getUserById = getUserById;
exports.createUser = createUser;
exports.updateUserPasswordHash = updateUserPasswordHash;
exports.ensureUserDefaults = ensureUserDefaults;
exports.getUserRoutes = getUserRoutes;
exports.getAllDbRoutes = getAllDbRoutes;
exports.addUserRoute = addUserRoute;
exports.updateUserRoute = updateUserRoute;
exports.deleteUserRoute = deleteUserRoute;
exports.saveUserTelegram = saveUserTelegram;
exports.getUserTelegram = getUserTelegram;
exports.saveUserCron = saveUserCron;
exports.getUserCron = getUserCron;
exports.getUserMaxDisplayPrice = getUserMaxDisplayPrice;
exports.getGlobalMaxPriceLimit = getGlobalMaxPriceLimit;
exports.createUserRun = createUserRun;
exports.finishUserRun = finishUserRun;
exports.touchUserCronRun = touchUserCronRun;
exports.userHasRunningScan = userHasRunningScan;
exports.getLastUserRun = getLastUserRun;
exports.listSchedulableUsers = listSchedulableUsers;
exports.listPanelRoutes = listPanelRoutes;
const promise_1 = __importDefault(require("mysql2/promise"));
const config_1 = require("./config");
const utils_1 = require("./utils");
exports.db = promise_1.default.createPool({
    host: config_1.appConfig.mysql.host,
    port: config_1.appConfig.mysql.port,
    user: config_1.appConfig.mysql.user,
    password: config_1.appConfig.mysql.password,
    database: config_1.appConfig.mysql.database,
    waitForConnections: true,
    connectionLimit: config_1.appConfig.mysql.connectionLimit,
    queueLimit: 0,
    charset: "utf8mb4"
});
function normalizeTelegramValue(value) {
    const normalized = String(value ?? "").trim();
    if (!normalized || normalized === "TELEGRAM_CHAT_ID" || normalized === "TELEGRAM_BOT_TOKEN") {
        return "";
    }
    return normalized;
}
async function queryRows(sql, params = []) {
    const [rows] = await exports.db.query(sql, params);
    return rows;
}
async function queryOne(sql, params = []) {
    const rows = await queryRows(sql, params);
    return rows[0];
}
async function exec(sql, params = []) {
    const [result] = await exports.db.execute(sql, params);
    return result;
}
async function initDb() {
    await exports.db.query(`
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
    await exports.db.query(`
    CREATE TABLE IF NOT EXISTS users (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      email VARCHAR(255) NOT NULL UNIQUE,
      password_hash VARCHAR(255) NOT NULL,
      created_at VARCHAR(40) NOT NULL
    )
  `);
    await exports.db.query(`
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
    await exports.db.query(`
    CREATE TABLE IF NOT EXISTS user_telegram (
      user_id BIGINT PRIMARY KEY,
      bot_token TEXT NULL,
      chat_id TEXT NULL,
      updated_at VARCHAR(40) NOT NULL
    )
  `);
    await exports.db.query(`
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
    await exports.db.query(`
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
async function saveResult(result) {
    const stats = await statsForRoute({
        origin: result.origin,
        destination: result.destination,
        outboundDate: result.outboundDate,
        inboundDate: result.inboundDate,
        tripType: result.tripType
    });
    const priceBand = (0, utils_1.classifyPrice)(result.price, stats.minPrice, stats.avgPrice);
    await exec(`
    INSERT INTO results (
      created_at, site, origin, destination, outbound_date, inbound_date,
      price, currency, url, notes, price_band, best_vendor, best_vendor_price, booking_options_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `, [
        (0, utils_1.utcNowIso)(),
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
        price_fmt: (0, utils_1.formatBrl)(result.price),
        site: result.site,
        currency: result.currency,
        url: result.url,
        notes: result.notes,
        price_band: priceBand,
        best_vendor: result.bestVendor,
        best_vendor_price: result.bestVendorPrice,
        final_price_source: (0, utils_1.extractFinalPriceSource)(result.notes),
        booking_options_json: result.bookingOptionsJson,
        screenshot_path: result.screenshotPath
    };
}
async function statsForRoute(route) {
    const agg = await queryOne(`
    SELECT MIN(price) AS min_price, AVG(price) AS avg_price
    FROM results
    WHERE origin = ? AND destination = ? AND outbound_date = ? AND inbound_date = ? AND price IS NOT NULL
  `, [route.origin, route.destination, route.outboundDate, route.inboundDate]);
    const last = await queryOne(`
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
async function getHistory(limit) {
    const rows = await queryRows(`
    SELECT created_at, site, origin, destination, outbound_date, inbound_date,
           price, currency, price_band, notes, url, best_vendor, best_vendor_price, booking_options_json
    FROM results
    ORDER BY id DESC
    LIMIT ?
  `, [limit]);
    return rows.map((row) => ({
        ...row,
        final_price_source: (0, utils_1.extractFinalPriceSource)(String(row.notes ?? ""))
    }));
}
async function clearHistory() {
    const result = await exec("DELETE FROM results");
    return result.affectedRows;
}
async function getUserByEmail(email) {
    return queryOne("SELECT id, email, password_hash FROM users WHERE email = ?", [email]);
}
async function getUserById(id) {
    return queryOne("SELECT id, email FROM users WHERE id = ?", [id]);
}
async function createUser(email, passwordHash) {
    await exec("INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)", [email, passwordHash, (0, utils_1.utcNowIso)()]);
}
async function updateUserPasswordHash(userId, passwordHash) {
    await exec("UPDATE users SET password_hash = ? WHERE id = ?", [passwordHash, userId]);
}
async function ensureUserDefaults(userId, routes) {
    const routeExists = await queryOne("SELECT 1 AS ok FROM user_routes WHERE user_id = ? LIMIT 1", [userId]);
    if (!routeExists) {
        for (const route of routes) {
            await exec(`
        INSERT INTO user_routes (user_id, origin, destination, outbound_date, inbound_date, active, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
      `, [userId, route.origin, route.destination, route.outboundDate, route.inboundDate, (0, utils_1.utcNowIso)()]);
        }
    }
    const defaultBotToken = normalizeTelegramValue(process.env.TELEGRAM_BOT_TOKEN ?? config_1.config.telegram_bot_token ?? "");
    const defaultChatId = normalizeTelegramValue(process.env.TELEGRAM_CHAT_ID ?? config_1.config.telegram_chat_id ?? "");
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
  `, [userId, defaultBotToken, defaultChatId, (0, utils_1.utcNowIso)()]);
    await exec(`
    INSERT INTO user_cron (user_id, enabled, every_hours, schedule_minutes, max_price_display, updated_at, last_run_at)
    VALUES (?, 1, ?, ?, NULL, ?, ?)
    ON DUPLICATE KEY UPDATE user_id = user_id
  `, [userId, Math.floor(config_1.config.schedule_minutes / 60), config_1.config.schedule_minutes, (0, utils_1.utcNowIso)(), (0, utils_1.utcNowIso)()]);
}
async function getUserRoutes(userId) {
    const rows = await queryRows(`
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
async function getAllDbRoutes() {
    const rows = await queryRows("SELECT origin, destination, outbound_date, inbound_date FROM user_routes WHERE active = 1");
    return rows.map((row) => ({
        origin: row.origin.toUpperCase(),
        destination: row.destination.toUpperCase(),
        outboundDate: row.outbound_date,
        inboundDate: row.inbound_date ?? "",
        tripType: row.inbound_date ? "roundtrip" : "oneway"
    }));
}
async function addUserRoute(userId, route) {
    await exec(`
    INSERT INTO user_routes (user_id, origin, destination, outbound_date, inbound_date, active, created_at)
    VALUES (?, ?, ?, ?, ?, 1, ?)
  `, [userId, route.origin, route.destination, route.outboundDate, route.inboundDate, (0, utils_1.utcNowIso)()]);
}
async function updateUserRoute(userId, routeId, route) {
    await exec(`
    UPDATE user_routes
    SET origin = ?, destination = ?, outbound_date = ?, inbound_date = ?
    WHERE id = ? AND user_id = ?
  `, [route.origin, route.destination, route.outboundDate, route.inboundDate, routeId, userId]);
}
async function deleteUserRoute(userId, routeId) {
    await exec("DELETE FROM user_routes WHERE id = ? AND user_id = ?", [routeId, userId]);
}
async function saveUserTelegram(userId, botToken, chatId) {
    const normalizedBotToken = normalizeTelegramValue(botToken);
    const normalizedChatId = normalizeTelegramValue(chatId);
    await exec(`
    INSERT INTO user_telegram (user_id, bot_token, chat_id, updated_at)
    VALUES (?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE
      bot_token = VALUES(bot_token),
      chat_id = VALUES(chat_id),
      updated_at = VALUES(updated_at)
  `, [userId, normalizedBotToken, normalizedChatId, (0, utils_1.utcNowIso)()]);
}
async function getUserTelegram(userId) {
    return queryOne("SELECT bot_token, chat_id FROM user_telegram WHERE user_id = ?", [userId]);
}
async function saveUserCron(userId, enabled, scheduleMinutes, maxPriceDisplay) {
    await exec(`
    INSERT INTO user_cron (user_id, enabled, every_hours, schedule_minutes, max_price_display, updated_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE
      enabled = VALUES(enabled),
      every_hours = VALUES(every_hours),
      schedule_minutes = VALUES(schedule_minutes),
      max_price_display = VALUES(max_price_display),
      updated_at = VALUES(updated_at)
  `, [userId, enabled ? 1 : 0, scheduleMinutes % 60 === 0 ? Math.floor(scheduleMinutes / 60) : 0, scheduleMinutes, maxPriceDisplay, (0, utils_1.utcNowIso)()]);
}
async function getUserCron(userId) {
    return queryOne("SELECT enabled, every_hours, schedule_minutes, max_price_display, last_run_at FROM user_cron WHERE user_id = ?", [userId]);
}
async function getUserMaxDisplayPrice(userId) {
    if (!userId) {
        return null;
    }
    const row = await queryOne("SELECT max_price_display FROM user_cron WHERE user_id = ?", [userId]);
    return row?.max_price_display ?? null;
}
async function getGlobalMaxPriceLimit() {
    const rows = await queryRows("SELECT max_price_display FROM user_cron WHERE max_price_display IS NOT NULL");
    if (!rows.length) {
        return null;
    }
    return Math.min(...rows.map((row) => Number(row.max_price_display)));
}
async function createUserRun(userId, trigger) {
    const result = await exec(`
    INSERT INTO user_runs (user_id, started_at, status, summary, run_trigger)
    VALUES (?, ?, 'running', '', ?)
  `, [userId, (0, utils_1.utcNowIso)(), trigger]);
    return Number(result.insertId);
}
async function finishUserRun(runId, status, summary) {
    await exec("UPDATE user_runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?", [(0, utils_1.utcNowIso)(), status, summary, runId]);
}
async function touchUserCronRun(userId) {
    await exec("UPDATE user_cron SET last_run_at = ?, updated_at = ? WHERE user_id = ?", [(0, utils_1.utcNowIso)(), (0, utils_1.utcNowIso)(), userId]);
}
async function userHasRunningScan(userId) {
    return Boolean(await queryOne("SELECT 1 AS ok FROM user_runs WHERE user_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1", [userId]));
}
async function getLastUserRun(userId) {
    return queryOne("SELECT started_at, finished_at, status, summary FROM user_runs WHERE user_id = ? ORDER BY id DESC LIMIT 1", [userId]);
}
async function listSchedulableUsers() {
    return queryRows(`
    SELECT u.id AS user_id, COALESCE(c.enabled, 1) AS enabled, c.schedule_minutes, c.every_hours
    FROM users u
    LEFT JOIN user_cron c ON c.user_id = u.id
  `);
}
async function listPanelRoutes(userId) {
    return queryRows("SELECT id, origin, destination, outbound_date, inbound_date, active FROM user_routes WHERE user_id = ? ORDER BY id DESC", [userId]);
}
