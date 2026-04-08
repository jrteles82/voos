"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.runScanForRoutes = runScanForRoutes;
exports.runUserScan = runUserScan;
exports.processDueUserScans = processDueUserScans;
exports.runGlobalScan = runGlobalScan;
exports.startWorkerSchedulers = startWorkerSchedulers;
const node_fs_1 = __importDefault(require("node:fs"));
const config_1 = require("./config");
const db_1 = require("./db");
const googleFlights_1 = require("./scrapers/googleFlights");
const maxMilhas_1 = require("./scrapers/maxMilhas");
const scanSummary_1 = require("./scanSummary");
const telegram_1 = require("./telegram");
const utils_1 = require("./utils");
let scanLocked = false;
function requestedSources(route) {
    return route.inboundDate ? ["google_flights"] : ["google_flights", "maxmilhas"];
}
async function searchSources(route, sources) {
    const rows = [];
    for (const source of sources) {
        const result = source === "google_flights"
            ? await (0, googleFlights_1.searchGoogleFlights)(route)
            : await (0, maxMilhas_1.searchMaxMilhas)(route);
        if (result) {
            rows.push(await (0, db_1.saveResult)(result));
        }
    }
    return rows;
}
async function runScanForRoutes(routes, onRow, onProgress) {
    const total = routes.reduce((sum, route) => sum + requestedSources(route).length, 0);
    const parsed = [];
    if (scanLocked) {
        throw new Error("Ja existe uma varredura em andamento.");
    }
    scanLocked = true;
    try {
        let index = 0;
        for (const route of routes) {
            const sources = requestedSources(route);
            for (const source of sources) {
                await onProgress?.(index + 1, total, { route, source });
            }
            for (const row of await searchSources(route, sources)) {
                parsed.push(row);
                index += 1;
                await onRow?.(index, total, row);
            }
        }
        return parsed;
    }
    finally {
        scanLocked = false;
    }
}
async function notifyRows(rows, trigger, token, chatId) {
    if (!rows.length) {
        return;
    }
    await (0, telegram_1.sendTelegramMessage)((0, scanSummary_1.buildFullScanMessage)(rows, trigger), token, chatId);
    const imagePath = await (0, scanSummary_1.buildScanResultsImage)(rows).catch(() => null);
    if (imagePath) {
        try {
            await (0, telegram_1.sendTelegramPhoto)(imagePath, "", token, chatId);
        }
        finally {
            node_fs_1.default.unlink(imagePath, () => undefined);
        }
    }
}
async function runUserScan(userId, trigger, notify = true) {
    const runId = await (0, db_1.createUserRun)(userId, trigger);
    try {
        if (trigger.startsWith("agendada")) {
            await (0, db_1.touchUserCronRun)(userId);
        }
        const userRoutes = await (0, db_1.getUserRoutes)(userId);
        const routes = userRoutes.length ? userRoutes : await (0, db_1.getAllDbRoutes)();
        const parsed = await runScanForRoutes(routes);
        const maxPrice = await (0, db_1.getUserMaxDisplayPrice)(userId);
        const filtered = (0, utils_1.filterRowsByMaxPrice)(parsed, maxPrice);
        let telegramStatus = "";
        if (notify && filtered.length) {
            try {
                const telegram = await (0, db_1.getUserTelegram)(userId);
                await notifyRows(filtered, trigger, telegram?.bot_token, telegram?.chat_id);
                telegramStatus = " | telegram=ok";
            }
            catch (error) {
                telegramStatus = ` | telegram=${error instanceof Error ? error.message.slice(0, 120) : "erro"}`;
            }
        }
        const summary = `ok: ${filtered.filter((row) => row.price !== null).length}/${filtered.length} exibidos${telegramStatus}`;
        await (0, db_1.finishUserRun)(runId, "ok", summary);
        return { status: "ok", summary, parsed: filtered };
    }
    catch (error) {
        await (0, db_1.finishUserRun)(runId, "error", error instanceof Error ? error.message.slice(0, 500) : "Erro");
        throw error;
    }
}
async function shouldRunUserNow(userId, scheduleMinutes) {
    if (await (0, db_1.userHasRunningScan)(userId)) {
        return false;
    }
    const cron = await (0, db_1.getUserCron)(userId);
    const lastRunAt = typeof cron?.last_run_at === "string" ? cron.last_run_at : null;
    if (!lastRunAt) {
        return true;
    }
    return Date.now() - new Date(lastRunAt).getTime() >= Math.max(60, scheduleMinutes * 60) * 1000;
}
async function processDueUserScans() {
    const users = await (0, db_1.listSchedulableUsers)();
    const results = [];
    for (const user of users) {
        if (!user.enabled) {
            continue;
        }
        const minutes = user.schedule_minutes ?? (user.every_hours ? user.every_hours * 60 : config_1.config.schedule_minutes);
        if (!(await shouldRunUserNow(user.user_id, minutes))) {
            continue;
        }
        try {
            const result = await runUserScan(user.user_id, "agendada-usuario");
            results.push({ userId: user.user_id, status: result.status, summary: result.summary });
        }
        catch (error) {
            results.push({
                userId: user.user_id,
                status: "error",
                summary: error instanceof Error ? error.message.slice(0, 200) : "Erro"
            });
        }
    }
    return results;
}
async function runGlobalScan() {
    const parsed = await runScanForRoutes(await (0, db_1.getAllDbRoutes)()).catch(() => []);
    const maxPrice = await (0, db_1.getGlobalMaxPriceLimit)();
    const filtered = (0, utils_1.filterRowsByMaxPrice)(parsed, maxPrice);
    if (filtered.length) {
        await notifyRows(filtered, "agendada").catch(() => undefined);
    }
}
function startWorkerSchedulers() {
    setInterval(async () => {
        await processDueUserScans().catch(() => undefined);
    }, Math.max(30, config_1.appConfig.userScanPollSeconds) * 1000);
    if (config_1.appConfig.autoScanEnabled) {
        setInterval(async () => {
            await runGlobalScan().catch(() => undefined);
        }, config_1.appConfig.fullScanSeconds * 1000);
    }
}
