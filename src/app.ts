import express, { Request, Response, NextFunction } from "express";
import session from "express-session";
import path from "node:path";
import fs from "node:fs";
import { z } from "zod";
import { appConfig, config } from "./config";
import {
  addUserRoute,
  clearHistory,
  createUser,
  createUserRun,
  deleteUserRoute,
  ensureUserDefaults,
  finishUserRun,
  getAllDbRoutes,
  getGlobalMaxPriceLimit,
  getHistory,
  getLastUserRun,
  getUserByEmail,
  getUserById,
  getUserCron,
  getUserMaxDisplayPrice,
  getUserRoutes,
  getUserTelegram,
  listPanelRoutes,
  listSchedulableUsers,
  saveResult,
  saveUserCron,
  saveUserTelegram,
  statsForRoute,
  touchUserCronRun,
  updateUserRoute,
  updateUserPasswordHash,
  userHasRunningScan
} from "./db";
import { renderAppPage, renderAuthPage, renderPanel } from "./templates";
import { searchGoogleFlights } from "./scrapers/googleFlights";
import { searchMaxMilhas } from "./scrapers/maxMilhas";
import { filterRowsByMaxPrice, toRoute, buildConfigQueries } from "./utils";
import { RouteQuery, ScanRow } from "./types";
import { sendTelegramMessage, sendTelegramPhoto } from "./telegram";
import { hashPassword, verifyPassword } from "./auth";
import { spawn } from "node:child_process";
import { buildFullScanMessage, buildScanResultsImage } from "./scanSummary";

declare module "express-session" {
  interface SessionData {
    userId?: number;
  }
}

const app = express();
const staticDir = path.resolve(process.cwd(), "static");
const scanState = {
  locked: false,
  lastRunAt: null as string | null,
  userSchedulerStarted: false,
  autoSchedulerStarted: false
};

app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(session({
  secret: appConfig.sessionSecret,
  resave: false,
  saveUninitialized: false,
  cookie: {
    httpOnly: true,
    sameSite: "lax",
    secure: false
  }
}));
app.use("/static", express.static(staticDir));

async function currentUser(req: Request) {
  return req.session.userId ? getUserById(req.session.userId) : undefined;
}

async function getRequestRoutes(req: Request): Promise<RouteQuery[]> {
  if (req.session.userId) {
    const userRoutes = await getUserRoutes(req.session.userId);
    if (userRoutes.length) {
      return userRoutes;
    }
  }
  return getAllDbRoutes();
}

function loginRequired(req: Request, res: Response, next: NextFunction): void {
  if (!req.session.userId) {
    res.redirect("/auth/login");
    return;
  }
  next();
}

async function searchSources(route: RouteQuery, requestedSources: string[]): Promise<ScanRow[]> {
  const rows: ScanRow[] = [];
  for (const source of requestedSources) {
    const result = source === "google_flights" ? await searchGoogleFlights(route) : await searchMaxMilhas(route);
    if (result) {
      rows.push(await saveResult(result));
    }
  }
  return rows;
}

function requestedSources(query: Record<string, unknown>, route: RouteQuery): string[] {
  const source = String(query.fonte ?? "").trim().toLowerCase();
  if (source === "maxmilhas") {
    return route.inboundDate ? [] : ["maxmilhas"];
  }
  if (source === "google" || source === "google_flights") {
    return ["google_flights"];
  }
  return route.inboundDate ? ["google_flights"] : ["google_flights", "maxmilhas"];
}

async function runScanForRoutes(
  routes: RouteQuery[],
  onRow?: (index: number, total: number, row: ScanRow) => Promise<void> | void,
  onProgress?: (index: number, total: number, payload: { route: RouteQuery; source: string }) => Promise<void> | void
): Promise<ScanRow[]> {
  const total = routes.reduce((sum, route) => sum + (route.inboundDate ? 1 : 2), 0);
  const parsed: ScanRow[] = [];
  if (scanState.locked) {
    throw new Error("Já existe uma varredura em andamento. Tente novamente em instantes.");
  }
  scanState.locked = true;
  try {
    let index = 0;
    for (const route of routes) {
      const sources = requestedSources({}, route);
      for (const source of sources) {
        if (onProgress) {
          await onProgress(index + 1, total, { route, source });
        }
      }
      for (const row of await searchSources(route, sources)) {
        parsed.push(row);
        index += 1;
        if (onRow) {
          await onRow(index, total, row);
        }
      }
    }
    scanState.lastRunAt = new Date().toISOString();
    return parsed;
  } finally {
    scanState.locked = false;
  }
}

async function runUserScan(userId: number, trigger: string, notify = true): Promise<{ status: string; summary: string; parsed: ScanRow[] }> {
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
        const token = telegram?.bot_token;
        const chatId = telegram?.chat_id;
        await sendTelegramMessage(buildFullScanMessage(filtered, trigger), token, chatId);
        const imagePath = await buildScanResultsImage(filtered);
        if (imagePath) {
          try {
            await sendTelegramPhoto(imagePath, "", token, chatId);
          } finally {
            fs.unlink(imagePath, () => undefined);
          }
        }
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

async function executeConsulta(query: Record<string, unknown>) {
  const route = toRoute(query);
  const sources = requestedSources(query, route);
  if (!sources.length) {
    throw new Error("A MaxMilhas atualmente só está habilitada para consultas somente ida.");
  }
  const results = await searchSources(route, sources);
  if (!results.length) {
    return { empty: true as const, route };
  }
  const chosen = [...results].sort((a, b) => (a.price ?? Number.POSITIVE_INFINITY) - (b.price ?? Number.POSITIVE_INFINITY))[0];
  const stats = await statsForRoute(route);
  return {
    empty: false as const,
    route,
    chosen,
    results,
    stats
  };
}

async function notifyQuickConsulta(sessionUserId: number | undefined, payload: {
  route: RouteQuery;
  chosen: ScanRow;
  results: ScanRow[];
}): Promise<void> {
  const telegram = sessionUserId ? await getUserTelegram(sessionUserId) : undefined;
  const token = telegram?.bot_token;
  const chatId = telegram?.chat_id;
  const lines = [
    "────────── ✈️ CONSULTA RÁPIDA ✈️ ──────────",
    `Rota: ${payload.route.origin} → ${payload.route.destination}`,
    `Data: ${payload.route.outboundDate}${payload.route.inboundDate ? ` / ${payload.route.inboundDate}` : ""}`,
    "Resultados:",
    ...payload.results.map((item) => {
      const vendor = item.best_vendor ? ` | comprar: ${item.best_vendor}${item.best_vendor_price != null ? ` (${item.best_vendor_price})` : ""}` : "";
      return `${item.site}: ${item.price_fmt}${vendor}`;
    })
  ];

  await sendTelegramMessage(lines.join("\n"), token, chatId);
  if (payload.chosen.screenshot_path && fs.existsSync(payload.chosen.screenshot_path)) {
    try {
      await sendTelegramPhoto(payload.chosen.screenshot_path, `${payload.route.origin} → ${payload.route.destination}`, token, chatId);
    } finally {
      fs.unlink(payload.chosen.screenshot_path, () => undefined);
    }
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

async function processDueUserScans(): Promise<Array<{ userId: number; status: string; summary: string }>> {
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

export function startSchedulers(): void {
  if (!scanState.userSchedulerStarted) {
    scanState.userSchedulerStarted = true;
    setInterval(async () => {
      await processDueUserScans().catch(() => undefined);
    }, Math.max(30, appConfig.userScanPollSeconds) * 1000);
  }

  if (!scanState.autoSchedulerStarted && appConfig.autoScanEnabled) {
    scanState.autoSchedulerStarted = true;
    setInterval(async () => {
      const parsed = await runScanForRoutes(await getAllDbRoutes()).catch(() => []);
      const maxPrice = await getGlobalMaxPriceLimit();
      const filtered = filterRowsByMaxPrice(parsed, maxPrice);
      if (filtered.length) {
        await sendTelegramMessage(buildFullScanMessage(filtered, "agendada")).catch(() => undefined);
        const imagePath = await buildScanResultsImage(filtered).catch(() => null);
        if (imagePath) {
          await sendTelegramPhoto(imagePath).catch(() => undefined);
          fs.unlink(imagePath, () => undefined);
        }
      }
    }, appConfig.fullScanSeconds * 1000);
  }
}

app.get("/", (req, res) => {
  res.redirect(req.session.userId ? "/painel" : "/auth/login");
});

app.get("/app", (_req, res) => {
  res.sendFile(path.resolve(staticDir, "index.html"));
});

app.get("/app-page", loginRequired, (_req, res) => {
  res.send(renderAppPage());
});

app.get("/health", (_req, res) => {
  res.json({ ok: true, service: "voobot-monitor-node" });
});

app.get("/internal/cron", async (req, res) => {
  if (!appConfig.cronSecret || req.query.token !== appConfig.cronSecret) {
    res.status(403).json({ ok: false, error: "forbidden" });
    return;
  }

  const results = await processDueUserScans();
  res.json({
    ok: true,
    checked_at: new Date().toISOString(),
    ran: results.length,
    users: results
  });
});

app.get("/rotas", async (req, res) => {
  const routes = await getRequestRoutes(req);
  res.json({
    count: routes.length,
    rotas: routes.map((route) => ({
      origin: route.origin,
      destination: route.destination,
      outbound_date: route.outboundDate,
      inbound_date: route.inboundDate,
      trip_type: route.tripType
    }))
  });
});

app.get("/consulta", async (req, res) => {
  try {
    const result = await executeConsulta(req.query as Record<string, unknown>);
    if (result.empty) {
      res.status(200).json({ error: "Nenhum resultado está dentro do valor máximo configurado." });
      return;
    }
    await notifyQuickConsulta(req.session.userId, {
      route: result.route,
      chosen: result.chosen,
      results: result.results
    }).catch(() => undefined);
    res.json({
      rota: {
        origin: result.route.origin,
        destination: result.route.destination,
        outbound_date: result.route.outboundDate,
        inbound_date: result.route.inboundDate,
        trip_type: result.route.tripType
      },
      resultado: {
        price: result.chosen.price,
        price_fmt: result.chosen.price_fmt,
        price_band: result.chosen.price_band,
        site: result.chosen.site,
        currency: "BRL",
        url: result.chosen.url,
        notes: result.chosen.notes,
        best_vendor: result.chosen.best_vendor,
        best_vendor_price: result.chosen.best_vendor_price,
        final_price_source: result.chosen.final_price_source
      },
      resultados: result.results,
      historico: {
        min_price: result.stats.minPrice,
        avg_price: result.stats.avgPrice,
        last_price: result.stats.lastPrice
      }
    });
  } catch (error) {
    res.status(400).json({ error: error instanceof Error ? error.message : "Erro na consulta" });
  }
});

app.get("/consulta-maxmilhas", async (req, res) => {
  try {
    const result = await executeConsulta({ ...req.query, fonte: "maxmilhas" });
    if (result.empty) {
      res.status(200).json({ error: "Nenhum resultado está dentro do valor máximo configurado." });
      return;
    }
    await notifyQuickConsulta(req.session.userId, {
      route: result.route,
      chosen: result.chosen,
      results: result.results
    }).catch(() => undefined);
    res.json({
      rota: {
        origin: result.route.origin,
        destination: result.route.destination,
        outbound_date: result.route.outboundDate,
        inbound_date: result.route.inboundDate,
        trip_type: result.route.tripType
      },
      resultado: result.chosen,
      resultados: result.results,
      historico: {
        min_price: result.stats.minPrice,
        avg_price: result.stats.avgPrice,
        last_price: result.stats.lastPrice
      }
    });
  } catch (error) {
    res.status(400).json({ error: error instanceof Error ? error.message : "Erro na consulta" });
  }
});

app.get("/historico", async (req, res) => {
  const parsed = z.coerce.number().min(1).max(200).catch(20).parse(req.query.limit ?? 20);
  const maxPrice = await getUserMaxDisplayPrice(req.session.userId ?? null);
  const items = filterRowsByMaxPrice((await getHistory(parsed)).map((item) => ({
    ...item,
    price: typeof item.price === "number" ? item.price : item.price === null ? null : Number(item.price)
  })), maxPrice);
  res.json({ total: items.length, items });
});

app.post("/historico/limpar", async (_req, res) => {
  res.json({ status: "ok", deleted: await clearHistory() });
});

app.get("/cron", async (req, res) => {
  const parsed = await runScanForRoutes(await getRequestRoutes(req));
  const maxPrice = await getUserMaxDisplayPrice(req.session.userId ?? null);
  const filtered = filterRowsByMaxPrice(parsed, maxPrice);
  const telegram = req.session.userId ? await getUserTelegram(req.session.userId) : undefined;
  const token = telegram?.bot_token;
  const chatId = telegram?.chat_id;
  await sendTelegramMessage(buildFullScanMessage(filtered, "manual"), token, chatId).catch(() => undefined);
  const imagePath = await buildScanResultsImage(filtered).catch(() => null);
  if (imagePath) {
    await sendTelegramPhoto(imagePath, "", token, chatId).catch(() => undefined);
    fs.unlink(imagePath, () => undefined);
  }
  res.json({ status: "ok", resultados: filtered, last_run_at: scanState.lastRunAt });
});

app.get("/cron-stream", async (req, res) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  const userId = req.session.userId ?? null;
  const maxPrice = await getUserMaxDisplayPrice(userId);
  const routes = await getRequestRoutes(req);
  const total = routes.reduce((sum, route) => sum + (route.inboundDate ? 1 : 2), 0);
  res.write(`data: ${JSON.stringify({ type: "start", total })}\n\n`);
  try {
    const parsed = await runScanForRoutes(
      routes,
      async (index, all, row) => {
        if (row.price === null || maxPrice === null || row.price <= maxPrice) {
          res.write(`data: ${JSON.stringify({ type: "row", index, total: all, item: row })}\n\n`);
        }
      },
      async (index, all, payload) => {
        res.write(`data: ${JSON.stringify({
          type: "progress",
          index,
          total: all,
          route: {
            origin: payload.route.origin,
            destination: payload.route.destination,
            outbound_date: payload.route.outboundDate,
            inbound_date: payload.route.inboundDate,
            trip_type: payload.route.tripType
          },
          source: payload.source
        })}\n\n`);
      }
    );
    const filtered = filterRowsByMaxPrice(parsed, maxPrice);
    const telegram = userId ? await getUserTelegram(userId) : undefined;
    const token = telegram?.bot_token;
    const chatId = telegram?.chat_id;
    await sendTelegramMessage(buildFullScanMessage(filtered, "manual"), token, chatId).catch(() => undefined);
    const imagePath = await buildScanResultsImage(filtered).catch(() => null);
    if (imagePath) {
      await sendTelegramPhoto(imagePath, "", token, chatId).catch(() => undefined);
      fs.unlink(imagePath, () => undefined);
    }
    res.write(`data: ${JSON.stringify({ type: "done" })}\n\n`);
  } catch (error) {
    res.write(`data: ${JSON.stringify({ type: "error", message: error instanceof Error ? error.message : "Erro na varredura" })}\n\n`);
  } finally {
    res.end();
  }
});

app.get("/auth/register", (_req, res) => {
  res.send(renderAuthPage("Cadastro | VooBot Admin", "/auth/register", "Cadastrar", "/auth/login", "Já tenho login"));
});

app.post("/auth/register", async (req, res) => {
  const email = String(req.body.email ?? "").trim().toLowerCase();
  const password = String(req.body.password ?? "");
  if (!email || password.length < 6) {
    res.send(renderAuthPage("Cadastro | VooBot Admin", "/auth/register", "Cadastrar", "/auth/login", "Já tenho login", "Informe email válido e senha com pelo menos 6 caracteres.", email));
    return;
  }
  try {
    await createUser(email, await hashPassword(password));
    res.redirect("/auth/login");
  } catch {
    res.redirect(`/auth/login?email=${encodeURIComponent(email)}&error=${encodeURIComponent("Esse email já está cadastrado. Faça login com a senha já criada.")}`);
  }
});

app.get("/auth/login", (req, res) => {
  const error = typeof req.query.error === "string" ? req.query.error : "";
  const email = typeof req.query.email === "string" ? req.query.email : "";
  res.send(renderAuthPage("Login | VooBot Admin", "/auth/login", "Entrar", "/auth/register", "Criar conta", error, email));
});

app.post("/auth/login", async (req, res) => {
  const email = String(req.body.email ?? "").trim().toLowerCase();
  const password = String(req.body.password ?? "");
  const user = await getUserByEmail(email);
  if (!user || !(await verifyPassword(password, user.password_hash))) {
    res.send(renderAuthPage("Login | VooBot Admin", "/auth/login", "Entrar", "/auth/register", "Criar conta", "Login inválido.", email));
    return;
  }
  if (!user.password_hash.startsWith("$2")) {
    await updateUserPasswordHash(user.id, await hashPassword(password));
  }
  req.session.userId = user.id;
  await ensureUserDefaults(user.id, buildConfigQueries());
  res.redirect("/painel");
});

app.get("/auth/logout", (req, res) => {
  req.session.destroy(() => res.redirect("/auth/login"));
});

app.get("/painel", loginRequired, async (req, res) => {
  const user = (await currentUser(req))!;
  await ensureUserDefaults(user.id, buildConfigQueries());
  const routes = await listPanelRoutes(user.id);
  const tg = await getUserTelegram(user.id);
  const cron = await getUserCron(user.id);
  const cronMinutes = Number(cron?.schedule_minutes ?? (cron?.every_hours ? Number(cron.every_hours) * 60 : config.schedule_minutes));
  const rawMax = cron?.max_price_display;
  const cronMaxPrice = rawMax === null || rawMax === undefined ? "" : String(rawMax);
  const lastRun = await getLastUserRun(user.id);
  res.send(renderPanel({
    user,
    routes,
    tg,
    cron,
    cronMinutes,
    cronMaxPrice,
    lastRun,
    restartMessage: typeof req.query.restart_message === "string" ? req.query.restart_message : undefined,
    restartStatus: typeof req.query.restart_status === "string" ? req.query.restart_status : undefined
  }));
});

app.post("/painel/route/add", loginRequired, async (req, res) => {
  const user = (await currentUser(req))!;
  await addUserRoute(user.id, {
    origin: String(req.body.origin ?? "").trim().toUpperCase(),
    destination: String(req.body.destination ?? "").trim().toUpperCase(),
    outboundDate: String(req.body.outbound_date ?? "").trim(),
    inboundDate: String(req.body.inbound_date ?? "").trim(),
    tripType: String(req.body.inbound_date ?? "").trim() ? "roundtrip" : "oneway"
  });
  res.redirect("/painel");
});

app.get("/painel/route/delete/:routeId", loginRequired, async (req, res) => {
  await deleteUserRoute((await currentUser(req))!.id, Number(req.params.routeId));
  res.redirect("/painel#rotas");
});

app.post("/painel/route/update/:routeId", loginRequired, async (req, res) => {
  await updateUserRoute((await currentUser(req))!.id, Number(req.params.routeId), {
    origin: String(req.body.origin ?? "").trim().toUpperCase(),
    destination: String(req.body.destination ?? "").trim().toUpperCase(),
    outboundDate: String(req.body.outbound_date ?? "").trim(),
    inboundDate: String(req.body.inbound_date ?? "").trim(),
    tripType: String(req.body.inbound_date ?? "").trim() ? "roundtrip" : "oneway"
  });
  res.redirect("/painel#rotas");
});

app.post("/painel/telegram", loginRequired, async (req, res) => {
  await saveUserTelegram((await currentUser(req))!.id, String(req.body.bot_token ?? "").trim(), String(req.body.chat_id ?? "").trim());
  res.redirect("/painel#telegram");
});

app.post("/painel/run-now", loginRequired, async (req, res) => {
  await runUserScan((await currentUser(req))!.id, "painel-manual", true).catch(() => undefined);
  res.redirect("/painel#cron");
});

app.post("/painel/restart", loginRequired, (_req, res) => {
  if (appConfig.restartCommand) {
    const [cmd, ...args] = appConfig.restartCommand.split(/\s+/);
    spawn(cmd, args, { cwd: process.cwd(), detached: true, stdio: "ignore" }).unref();
    res.redirect("/painel?restart_status=success&restart_message=Comando%20de%20rein%C3%ADcio%20executado.#cron");
    return;
  }
  const entry = fs.existsSync(path.resolve(process.cwd(), "dist/server.js")) ? "dist/server.js" : "src/server.ts";
  const args = entry.endsWith(".ts") ? [path.resolve(process.cwd(), "node_modules/tsx/dist/cli.mjs"), entry] : [entry];
  spawn(process.execPath, args, { cwd: process.cwd(), detached: true, stdio: "ignore" }).unref();
  res.redirect("/painel?restart_status=success&restart_message=Novo%20processo%20Node%20iniciado.#cron");
  process.nextTick(() => process.exit(0));
});

app.post("/painel/cron", loginRequired, async (req, res) => {
  const enabled = Boolean(req.body.enabled);
  const scheduleMinutes = Math.max(1, Math.min(1440, Number(req.body.schedule_minutes ?? config.schedule_minutes)));
  const maxPriceDisplay = String(req.body.max_price_display ?? "").trim();
  await saveUserCron((await currentUser(req))!.id, enabled, scheduleMinutes, maxPriceDisplay ? Math.max(0, Number(maxPriceDisplay)) : null);
  res.redirect("/painel#cron");
});

export default app;
