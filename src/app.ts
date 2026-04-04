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
  deleteUserRoute,
  ensureUserDefaults,
  getAllDbRoutes,
  getHistory,
  getLastUserRun,
  getUserByEmail,
  getUserById,
  getUserCron,
  getUserMaxDisplayPrice,
  getUserTelegram,
  getUserRoutes,
  listPanelRoutes,
  saveUserCron,
  saveUserTelegram,
  updateUserRoute,
  updateUserPasswordHash,
} from "./db";
import { renderAppPage, renderAuthPage, renderPanel } from "./templates";
import { filterRowsByMaxPrice, toRoute, buildConfigQueries } from "./utils";
import { RouteQuery } from "./types";
import { hashPassword, verifyPassword } from "./auth";
import { spawn } from "node:child_process";

declare module "express-session" {
  interface SessionData {
    userId?: number;
  }
}

const app = express();
const staticDir = path.resolve(process.cwd(), "static");

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

  res.status(501).json({ ok: false, error: "Worker externo necessario. Execute src/worker.ts fora do Hostinger web." });
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
  const route = toRoute(req.query as Record<string, unknown>);
  res.status(501).json({
    error: "Consulta ao vivo indisponivel no web hosting. Rode o worker externo para coletar resultados.",
    rota: route,
    fontes: requestedSources(req.query as Record<string, unknown>, route)
  });
});

app.get("/consulta-maxmilhas", async (req, res) => {
  const route = toRoute({ ...req.query, fonte: "maxmilhas" });
  res.status(501).json({
    error: "Consulta MaxMilhas ao vivo indisponivel no web hosting. Rode o worker externo para coletar resultados.",
    rota: route,
    fontes: requestedSources({ ...req.query, fonte: "maxmilhas" }, route)
  });
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
  const maxPrice = await getUserMaxDisplayPrice(req.session.userId ?? null);
  const items = filterRowsByMaxPrice((await getHistory(200)).map((item) => ({
    ...item,
    price: typeof item.price === "number" ? item.price : item.price === null ? null : Number(item.price)
  })), maxPrice);
  res.status(501).json({
    status: "worker-required",
    message: "O web app nao executa mais scraping. Consulte o historico alimentado pelo worker externo.",
    resultados: items
  });
});

app.get("/cron-stream", async (req, res) => {
  res.status(501).json({ error: "Streaming de varredura indisponivel no web hosting. Use o worker externo." });
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
  res.redirect("/painel?restart_status=warning&restart_message=Use%20o%20worker%20externo%20para%20executar%20varreduras.#cron");
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
