import fs from "node:fs";
import { appConfig, config } from "./config";
import {
  createUserRun,
  finishUserRun,
  getAllDbRoutes,
  getGlobalMaxPriceLimit,
  getUserCron,
  getUserMaxDisplayPrice,
  getUserRoutes,
  getUserTelegram,
  listSchedulableUsers,
  saveResult,
  touchUserCronRun,
  userHasRunningScan
} from "./db";
import { searchGoogleFlights } from "./scrapers/googleFlights";
import { searchMaxMilhas } from "./scrapers/maxMilhas";
import { buildScanResultsImage, buildFullScanMessage } from "./scanSummary";
import { sendTelegramMessage, sendTelegramPhoto } from "./telegram";
import { FlightResult, RouteQuery, ScanRow } from "./types";
import { filterRowsByMaxPrice } from "./utils";

type ProgressPayload = { route: RouteQuery; source: string };

let scanLocked = false;

function requestedSources(route: RouteQuery): string[] {
  return route.inboundDate ? ["google_flights"] : ["google_flights", "maxmilhas"];
}

async function searchSources(route: RouteQuery, sources: string[]): Promise<ScanRow[]> {
  const rows: ScanRow[] = [];
  for (const source of sources) {
    const result: FlightResult | null = source === "google_flights"
      ? await searchGoogleFlights(route)
      : await searchMaxMilhas(route);
    if (result) {
      rows.push(await saveResult(result));
    }
  }
  return rows;
}

export async function runScanForRoutes(
  routes: RouteQuery[],
  onRow?: (index: number, total: number, row: ScanRow) => Promise<void> | void,
  onProgress?: (index: number, total: number, payload: ProgressPayload) => Promise<void> | void
): Promise<ScanRow[]> {
  const total = routes.reduce((sum, route) => sum + requestedSources(route).length, 0);
  const parsed: ScanRow[] = [];
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
  } finally {
    scanLocked = false;
  }
}

async function notifyRows(rows: ScanRow[], trigger: string, token?: string, chatId?: string): Promise<void> {
  if (!rows.length) {
    return;
  }

  await sendTelegramMessage(buildFullScanMessage(rows, trigger), token, chatId);
  const imagePath = await buildScanResultsImage(rows).catch(() => null);
  if (imagePath) {
    try {
      await sendTelegramPhoto(imagePath, "", token, chatId);
    } finally {
      fs.unlink(imagePath, () => undefined);
    }
  }
}

export async function runUserScan(userId: number, trigger: string, notify = true): Promise<{ status: string; summary: string; parsed: ScanRow[] }> {
  const runId = await createUserRun(userId, trigger);
  try {
    if (trigger.startsWith("agendada")) {
      await touchUserCronRun(userId);
    }

    const userRoutes = await getUserRoutes(userId);
    const routes = userRoutes.length ? userRoutes : await getAllDbRoutes();
    const parsed = await runScanForRoutes(routes);
    const maxPrice = await getUserMaxDisplayPrice(userId);
    const filtered = filterRowsByMaxPrice(parsed, maxPrice);

    let telegramStatus = "";
    if (notify && filtered.length) {
      try {
        const telegram = await getUserTelegram(userId);
        await notifyRows(filtered, trigger, telegram?.bot_token, telegram?.chat_id);
        telegramStatus = " | telegram=ok";
      } catch (error) {
        telegramStatus = ` | telegram=${error instanceof Error ? error.message.slice(0, 120) : "erro"}`;
      }
    }

    const summary = `ok: ${filtered.filter((row) => row.price !== null).length}/${filtered.length} exibidos${telegramStatus}`;
    await finishUserRun(runId, "ok", summary);
    return { status: "ok", summary, parsed: filtered };
  } catch (error) {
    await finishUserRun(runId, "error", error instanceof Error ? error.message.slice(0, 500) : "Erro");
    throw error;
  }
}

async function shouldRunUserNow(userId: number, scheduleMinutes: number): Promise<boolean> {
  if (await userHasRunningScan(userId)) {
    return false;
  }
  const cron = await getUserCron(userId);
  const lastRunAt = typeof cron?.last_run_at === "string" ? cron.last_run_at : null;
  if (!lastRunAt) {
    return true;
  }
  return Date.now() - new Date(lastRunAt).getTime() >= Math.max(60, scheduleMinutes * 60) * 1000;
}

export async function processDueUserScans(): Promise<Array<{ userId: number; status: string; summary: string }>> {
  const users = await listSchedulableUsers();
  const results: Array<{ userId: number; status: string; summary: string }> = [];
  for (const user of users) {
    if (!user.enabled) {
      continue;
    }

    const minutes = user.schedule_minutes ?? (user.every_hours ? user.every_hours * 60 : config.schedule_minutes);
    if (!(await shouldRunUserNow(user.user_id, minutes))) {
      continue;
    }

    try {
      const result = await runUserScan(user.user_id, "agendada-usuario");
      results.push({ userId: user.user_id, status: result.status, summary: result.summary });
    } catch (error) {
      results.push({
        userId: user.user_id,
        status: "error",
        summary: error instanceof Error ? error.message.slice(0, 200) : "Erro"
      });
    }
  }
  return results;
}

export async function runGlobalScan(): Promise<void> {
  const parsed = await runScanForRoutes(await getAllDbRoutes()).catch(() => []);
  const maxPrice = await getGlobalMaxPriceLimit();
  const filtered = filterRowsByMaxPrice(parsed, maxPrice);
  if (filtered.length) {
    await notifyRows(filtered, "agendada").catch(() => undefined);
  }
}

export function startWorkerSchedulers(): void {
  setInterval(async () => {
    await processDueUserScans().catch(() => undefined);
  }, Math.max(30, appConfig.userScanPollSeconds) * 1000);

  if (appConfig.autoScanEnabled) {
    setInterval(async () => {
      await runGlobalScan().catch(() => undefined);
    }, appConfig.fullScanSeconds * 1000);
  }
}
