"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.renderAuthPage = renderAuthPage;
exports.renderAppPage = renderAppPage;
exports.renderPanel = renderPanel;
const config_1 = require("./config");
function optionsHtml(selected) {
    return config_1.AIRPORT_OPTIONS.map(([code, label]) => `<option value="${code}"${code === selected ? " selected" : ""}>${label}</option>`).join("");
}
function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}
function renderAuthPage(title, action, buttonLabel, footerHref, footerLabel, error = "", email = "") {
    return `<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(title)}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light d-flex align-items-center" style="min-height:100vh;">
  <div class="container">
    <div class="row justify-content-center">
      <div class="col-md-5">
        <div class="card shadow-sm">
          <div class="card-header bg-dark text-white">${escapeHtml(title)}</div>
          <div class="card-body">
            <form method="post" action="${escapeHtml(action)}">
              <div class="mb-3"><input class="form-control" name="email" type="email" placeholder="Email" value="${escapeHtml(email)}" required></div>
              <div class="mb-3"><input class="form-control" name="password" type="password" placeholder="Senha" required></div>
              <button class="btn btn-dark w-100" type="submit">${escapeHtml(buttonLabel)}</button>
            </form>
            ${error ? `<div class="alert alert-danger mt-3 mb-0">${escapeHtml(error)}</div>` : ""}
            <div class="mt-3 text-center"><a href="${escapeHtml(footerHref)}">${escapeHtml(footerLabel)}</a></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</body>
</html>`;
}
function renderAppPage() {
    return `<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>App Consultas</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
  <nav class="navbar navbar-dark bg-dark">
    <div class="container-fluid">
      <span class="navbar-brand mb-0 h1">App Consultas</span>
      <a class="btn btn-outline-light btn-sm" href="/painel">Voltar ao Painel</a>
    </div>
  </nav>
  <div class="container-fluid p-0">
    <iframe src="/app" style="width:100%;height:92vh;border:0;"></iframe>
  </div>
</body>
</html>`;
}
function renderPanel(input) {
    const routesRows = input.routes.length ? input.routes.map((route) => `
    <tr>
      <form method="post" action="/painel/route/update/${route.id}">
        <td><select class="form-select form-select-sm" name="origin" required>${optionsHtml(route.origin)}</select></td>
        <td><select class="form-select form-select-sm" name="destination" required>${optionsHtml(route.destination)}</select></td>
        <td><input class="form-control form-control-sm" name="outbound_date" type="date" value="${escapeHtml(route.outbound_date)}" required></td>
        <td><input class="form-control form-control-sm" name="inbound_date" type="date" value="${escapeHtml(route.inbound_date ?? "")}"></td>
        <td class="text-end text-nowrap">
          <button class="btn btn-sm btn-outline-primary" type="submit">Salvar</button>
          <a class="btn btn-sm btn-outline-danger" href="/painel/route/delete/${route.id}">Excluir</a>
        </td>
      </form>
    </tr>
  `).join("") : `<tr><td colspan="5" class="text-center text-muted py-3">Nenhuma rota cadastrada.</td></tr>`;
    return `<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Painel Admin | VooBot</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
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
  </style>
</head>
<body class="bg-light">
  <div class="container-fluid">
    <div class="row">
      <aside class="col-md-3 col-lg-2 p-0 sidebar">
        <div class="brand">VooBot Admin</div>
        <a href="#rotas">Rotas</a>
        <a href="#consultas">Consultas</a>
        <a href="#telegram">Telegram</a>
        <a href="#cron">Cron</a>
        <a href="/auth/logout">Sair</a>
      </aside>
      <main class="col-md-9 col-lg-10 p-0">
        <div class="topbar d-flex justify-content-between align-items-center px-4 py-3">
          <div><strong>Painel</strong> <span class="text-muted">/ Dashboard</span></div>
          <div class="text-muted small">${escapeHtml(input.user.email)}</div>
        </div>
        <div class="p-4">
          ${input.restartMessage ? `<div class="alert alert-${input.restartStatus === "success" ? "success" : "danger"} mb-3">${escapeHtml(input.restartMessage)}</div>` : ""}
          <div class="row g-3 mb-3">
            <div class="col-md-4"><div class="card kpi"><div class="card-body"><div class="text-muted">Rotas</div><div class="h4 mb-0">${input.routes.length}</div></div></div></div>
            <div class="col-md-4"><div class="card kpi"><div class="card-body"><div class="text-muted">Cron</div><div class="h6 mb-0">${Number(input.cron?.enabled ?? 1) ? "Ativo" : "Inativo"} (${input.cronMinutes} min)</div></div></div></div>
            <div class="col-md-4"><div class="card kpi"><div class="card-body"><div class="text-muted">Última execução</div><div class="small mb-0">${escapeHtml(String(input.lastRun?.status ?? "sem execução"))}</div></div></div></div>
          </div>

          <div class="card mb-3 shadow-sm dashboard-section" id="rotas">
            <div class="card-header">Rotas configuradas</div>
            <div class="card-body">
              <form method="post" action="/painel/route/add" class="row g-2 mb-3 align-items-end">
                <div class="col-md-2"><label class="form-label small text-uppercase mb-1">Origem</label><select class="form-select form-select-sm" name="origin" required>${optionsHtml("PVH")}</select></div>
                <div class="col-md-2"><label class="form-label small text-uppercase mb-1">Destino</label><select class="form-select form-select-sm" name="destination" required>${optionsHtml("JPA")}</select></div>
                <div class="col-md-3"><label class="form-label small text-uppercase mb-1">Ida</label><input class="form-control form-control-sm" name="outbound_date" type="date" required></div>
                <div class="col-md-3"><label class="form-label small text-uppercase mb-1">Volta</label><input class="form-control form-control-sm" name="inbound_date" type="date"></div>
                <div class="col-md-2 d-grid"><button class="btn btn-primary btn-sm" type="submit">Adicionar</button></div>
              </form>
              <div class="table-responsive border rounded">
                <table class="table table-hover table-striped mb-0 align-middle">
                  <thead class="table-light"><tr><th>Origem</th><th>Destino</th><th>Data de Ida</th><th>Data de Volta</th><th class="text-end">Ações</th></tr></thead>
                  <tbody>${routesRows}</tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="card mb-3 shadow-sm dashboard-section d-none" id="consultas">
            <div class="card-header d-flex justify-content-between align-items-center">
              <div>App Consultas</div>
              <a class="btn btn-sm btn-outline-secondary" href="/app-page">Abrir app</a>
            </div>
            <div class="card-body">
              <iframe src="/app" style="width:100%;height:72vh;border:0;"></iframe>
            </div>
          </div>

          <div class="card mb-3 shadow-sm dashboard-section d-none" id="telegram">
            <div class="card-header">Telegram do usuário</div>
            <div class="card-body">
              <form method="post" action="/painel/telegram" class="row g-2">
                <div class="col-md-6"><input class="form-control" name="bot_token" placeholder="Bot token" value="${escapeHtml(input.tg?.bot_token ?? "")}"></div>
                <div class="col-md-4"><input class="form-control" name="chat_id" placeholder="Chat ID" value="${escapeHtml(input.tg?.chat_id ?? "")}"></div>
                <div class="col-md-2 d-grid"><button class="btn btn-success" type="submit">Salvar</button></div>
              </form>
            </div>
          </div>

          <div class="card shadow-sm dashboard-section d-none" id="cron">
            <div class="card-header">Cron do usuário</div>
            <div class="card-body">
              <form method="post" action="/painel/cron" class="row g-2 align-items-center">
                <div class="col-md-2 form-check ms-2">
                  <input class="form-check-input" type="checkbox" name="enabled" id="enabled"${Number(input.cron?.enabled ?? 1) ? " checked" : ""}>
                  <label class="form-check-label" for="enabled">Ativo</label>
                </div>
                <div class="col-md-3"><input class="form-control" name="schedule_minutes" type="number" min="1" max="1440" step="1" value="${input.cronMinutes}"></div>
                <div class="col-md-4"><input class="form-control" name="max_price_display" type="number" min="0" step="0.01" placeholder="Preço máximo exibido por trecho" value="${escapeHtml(input.cronMaxPrice)}"></div>
                <div class="col-md-2 d-grid"><button class="btn btn-primary" type="submit">Salvar</button></div>
              </form>
              <form method="post" action="/painel/run-now" class="mt-3"><button class="btn btn-warning" type="submit">Executar agora</button></form>
              <form method="post" action="/painel/restart" class="mt-2"><button class="btn btn-outline-danger" type="submit">Reiniciar serviço</button></form>
              <div class="small text-muted mt-2">${config_1.appConfig.restartCommand ? "O painel usará SKYSCANNER_RESTART_COMMAND." : "Nenhum comando de reinício configurado."}</div>
            </div>
          </div>
        </div>
      </main>
    </div>
  </div>
  <script>
    function showSection(hash) {
      document.querySelectorAll('.dashboard-section').forEach(el => el.classList.add('d-none'));
      var target = document.getElementById(hash);
      if (target) {
        target.classList.remove('d-none');
        localStorage.setItem('adminActiveTab', hash);
      } else {
        document.getElementById('rotas').classList.remove('d-none');
      }
      document.querySelectorAll('.sidebar a').forEach(el => el.classList.remove('fw-bold', 'text-white'));
      var activeLink = document.querySelector('.sidebar a[href="#' + hash + '"]');
      if (activeLink) activeLink.classList.add('fw-bold', 'text-white');
    }
    window.addEventListener('hashchange', () => showSection(window.location.hash.substring(1)));
    window.addEventListener('load', () => showSection(window.location.hash.substring(1) || localStorage.getItem('adminActiveTab') || 'rotas'));
  </script>
</body>
</html>`;
}
