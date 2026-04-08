"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const express_1 = __importDefault(require("express"));
const express_session_1 = __importDefault(require("express-session"));
const node_path_1 = __importDefault(require("node:path"));
const node_fs_1 = __importDefault(require("node:fs"));
const zod_1 = require("zod");
const config_1 = require("./config");
const db_1 = require("./db");
const templates_1 = require("./templates");
const utils_1 = require("./utils");
const auth_1 = require("./auth");
const node_child_process_1 = require("node:child_process");
const app = (0, express_1.default)();
const staticDir = node_path_1.default.resolve(process.cwd(), "static");
app.use(express_1.default.urlencoded({ extended: true }));
app.use(express_1.default.json());
app.use((0, express_session_1.default)({
    secret: config_1.appConfig.sessionSecret,
    resave: false,
    saveUninitialized: false,
    cookie: {
        httpOnly: true,
        sameSite: "lax",
        secure: false
    }
}));
app.use("/static", express_1.default.static(staticDir));
async function currentUser(req) {
    return req.session.userId ? (0, db_1.getUserById)(req.session.userId) : undefined;
}
async function getRequestRoutes(req) {
    if (req.session.userId) {
        const userRoutes = await (0, db_1.getUserRoutes)(req.session.userId);
        if (userRoutes.length) {
            return userRoutes;
        }
    }
    return (0, db_1.getAllDbRoutes)();
}
function loginRequired(req, res, next) {
    if (!req.session.userId) {
        res.redirect("/auth/login");
        return;
    }
    next();
}
function requestedSources(query, route) {
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
    res.sendFile(node_path_1.default.resolve(staticDir, "index.html"));
});
app.get("/app-page", loginRequired, (_req, res) => {
    res.send((0, templates_1.renderAppPage)());
});
app.get("/health", (_req, res) => {
    res.json({ ok: true, service: "voobot-monitor-node" });
});
app.get("/internal/cron", async (req, res) => {
    if (!config_1.appConfig.cronSecret || req.query.token !== config_1.appConfig.cronSecret) {
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
    const route = (0, utils_1.toRoute)(req.query);
    res.status(501).json({
        error: "Consulta ao vivo indisponivel no web hosting. Rode o worker externo para coletar resultados.",
        rota: route,
        fontes: requestedSources(req.query, route)
    });
});
app.get("/consulta-maxmilhas", async (req, res) => {
    const route = (0, utils_1.toRoute)({ ...req.query, fonte: "maxmilhas" });
    res.status(501).json({
        error: "Consulta MaxMilhas ao vivo indisponivel no web hosting. Rode o worker externo para coletar resultados.",
        rota: route,
        fontes: requestedSources({ ...req.query, fonte: "maxmilhas" }, route)
    });
});
app.get("/historico", async (req, res) => {
    const parsed = zod_1.z.coerce.number().min(1).max(200).catch(20).parse(req.query.limit ?? 20);
    const maxPrice = await (0, db_1.getUserMaxDisplayPrice)(req.session.userId ?? null);
    const items = (0, utils_1.filterRowsByMaxPrice)((await (0, db_1.getHistory)(parsed)).map((item) => ({
        ...item,
        price: typeof item.price === "number" ? item.price : item.price === null ? null : Number(item.price)
    })), maxPrice);
    res.json({ total: items.length, items });
});
app.post("/historico/limpar", async (_req, res) => {
    res.json({ status: "ok", deleted: await (0, db_1.clearHistory)() });
});
app.get("/cron", async (req, res) => {
    const maxPrice = await (0, db_1.getUserMaxDisplayPrice)(req.session.userId ?? null);
    const items = (0, utils_1.filterRowsByMaxPrice)((await (0, db_1.getHistory)(200)).map((item) => ({
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
    res.send((0, templates_1.renderAuthPage)("Cadastro | VooBot Admin", "/auth/register", "Cadastrar", "/auth/login", "Já tenho login"));
});
app.post("/auth/register", async (req, res) => {
    const email = String(req.body.email ?? "").trim().toLowerCase();
    const password = String(req.body.password ?? "");
    if (!email || password.length < 6) {
        res.send((0, templates_1.renderAuthPage)("Cadastro | VooBot Admin", "/auth/register", "Cadastrar", "/auth/login", "Já tenho login", "Informe email válido e senha com pelo menos 6 caracteres.", email));
        return;
    }
    try {
        await (0, db_1.createUser)(email, await (0, auth_1.hashPassword)(password));
        res.redirect("/auth/login");
    }
    catch {
        res.redirect(`/auth/login?email=${encodeURIComponent(email)}&error=${encodeURIComponent("Esse email já está cadastrado. Faça login com a senha já criada.")}`);
    }
});
app.get("/auth/login", (req, res) => {
    const error = typeof req.query.error === "string" ? req.query.error : "";
    const email = typeof req.query.email === "string" ? req.query.email : "";
    res.send((0, templates_1.renderAuthPage)("Login | VooBot Admin", "/auth/login", "Entrar", "/auth/register", "Criar conta", error, email));
});
app.post("/auth/login", async (req, res) => {
    const email = String(req.body.email ?? "").trim().toLowerCase();
    const password = String(req.body.password ?? "");
    const user = await (0, db_1.getUserByEmail)(email);
    if (!user || !(await (0, auth_1.verifyPassword)(password, user.password_hash))) {
        res.send((0, templates_1.renderAuthPage)("Login | VooBot Admin", "/auth/login", "Entrar", "/auth/register", "Criar conta", "Login inválido.", email));
        return;
    }
    if (!user.password_hash.startsWith("$2")) {
        await (0, db_1.updateUserPasswordHash)(user.id, await (0, auth_1.hashPassword)(password));
    }
    req.session.userId = user.id;
    await (0, db_1.ensureUserDefaults)(user.id, (0, utils_1.buildConfigQueries)());
    res.redirect("/painel");
});
app.get("/auth/logout", (req, res) => {
    req.session.destroy(() => res.redirect("/auth/login"));
});
app.get("/painel", loginRequired, async (req, res) => {
    const user = (await currentUser(req));
    await (0, db_1.ensureUserDefaults)(user.id, (0, utils_1.buildConfigQueries)());
    const routes = await (0, db_1.listPanelRoutes)(user.id);
    const tg = await (0, db_1.getUserTelegram)(user.id);
    const cron = await (0, db_1.getUserCron)(user.id);
    const cronMinutes = Number(cron?.schedule_minutes ?? (cron?.every_hours ? Number(cron.every_hours) * 60 : config_1.config.schedule_minutes));
    const rawMax = cron?.max_price_display;
    const cronMaxPrice = rawMax === null || rawMax === undefined ? "" : String(rawMax);
    const lastRun = await (0, db_1.getLastUserRun)(user.id);
    res.send((0, templates_1.renderPanel)({
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
    const user = (await currentUser(req));
    await (0, db_1.addUserRoute)(user.id, {
        origin: String(req.body.origin ?? "").trim().toUpperCase(),
        destination: String(req.body.destination ?? "").trim().toUpperCase(),
        outboundDate: String(req.body.outbound_date ?? "").trim(),
        inboundDate: String(req.body.inbound_date ?? "").trim(),
        tripType: String(req.body.inbound_date ?? "").trim() ? "roundtrip" : "oneway"
    });
    res.redirect("/painel");
});
app.get("/painel/route/delete/:routeId", loginRequired, async (req, res) => {
    await (0, db_1.deleteUserRoute)((await currentUser(req)).id, Number(req.params.routeId));
    res.redirect("/painel#rotas");
});
app.post("/painel/route/update/:routeId", loginRequired, async (req, res) => {
    await (0, db_1.updateUserRoute)((await currentUser(req)).id, Number(req.params.routeId), {
        origin: String(req.body.origin ?? "").trim().toUpperCase(),
        destination: String(req.body.destination ?? "").trim().toUpperCase(),
        outboundDate: String(req.body.outbound_date ?? "").trim(),
        inboundDate: String(req.body.inbound_date ?? "").trim(),
        tripType: String(req.body.inbound_date ?? "").trim() ? "roundtrip" : "oneway"
    });
    res.redirect("/painel#rotas");
});
app.post("/painel/telegram", loginRequired, async (req, res) => {
    await (0, db_1.saveUserTelegram)((await currentUser(req)).id, String(req.body.bot_token ?? "").trim(), String(req.body.chat_id ?? "").trim());
    res.redirect("/painel#telegram");
});
app.post("/painel/run-now", loginRequired, async (req, res) => {
    res.redirect("/painel?restart_status=warning&restart_message=Use%20o%20worker%20externo%20para%20executar%20varreduras.#cron");
});
app.post("/painel/restart", loginRequired, (_req, res) => {
    if (config_1.appConfig.restartCommand) {
        const [cmd, ...args] = config_1.appConfig.restartCommand.split(/\s+/);
        (0, node_child_process_1.spawn)(cmd, args, { cwd: process.cwd(), detached: true, stdio: "ignore" }).unref();
        res.redirect("/painel?restart_status=success&restart_message=Comando%20de%20rein%C3%ADcio%20executado.#cron");
        return;
    }
    const entry = node_fs_1.default.existsSync(node_path_1.default.resolve(process.cwd(), "dist/server.js")) ? "dist/server.js" : "src/server.ts";
    const args = entry.endsWith(".ts") ? [node_path_1.default.resolve(process.cwd(), "node_modules/tsx/dist/cli.mjs"), entry] : [entry];
    (0, node_child_process_1.spawn)(process.execPath, args, { cwd: process.cwd(), detached: true, stdio: "ignore" }).unref();
    res.redirect("/painel?restart_status=success&restart_message=Novo%20processo%20Node%20iniciado.#cron");
    process.nextTick(() => process.exit(0));
});
app.post("/painel/cron", loginRequired, async (req, res) => {
    const enabled = Boolean(req.body.enabled);
    const scheduleMinutes = Math.max(1, Math.min(1440, Number(req.body.schedule_minutes ?? config_1.config.schedule_minutes)));
    const maxPriceDisplay = String(req.body.max_price_display ?? "").trim();
    await (0, db_1.saveUserCron)((await currentUser(req)).id, enabled, scheduleMinutes, maxPriceDisplay ? Math.max(0, Number(maxPriceDisplay)) : null);
    res.redirect("/painel#cron");
});
exports.default = app;
