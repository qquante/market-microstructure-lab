const DATA = window.BULK_DASHBOARD_DATA || { runs: {} };
let LIVE = {};
let WS = {};
let HIST = {};
const state = {
  runId: localStorage.getItem("bulkDashboardRun") || Object.keys(DATA.runs || {})[0],
  strategy: null,
};

const fmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const pctFmt = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function cls(value) {
  const n = Number(value || 0);
  if (n > 0) return "positive";
  if (n < 0) return "negative";
  return "neutral";
}

function signed(value, money = false) {
  const n = Number(value || 0);
  const body = money ? usd.format(Math.abs(n)) : fmt.format(Math.abs(n));
  return `${n > 0 ? "+" : n < 0 ? "-" : ""}${body}`;
}

function pct(value) {
  return `${pctFmt.format(Number(value || 0) * 100)}%`;
}

function run() {
  return DATA.runs[state.runId] || Object.values(DATA.runs || {})[0] || {};
}

function allSymbols() {
  return run().symbols || [];
}

function pathToken() {
  const match = window.location.pathname.match(/^\/token\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function pathPaperToken() {
  const match = window.location.pathname.match(/^\/paper-live\/token\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function pathPaperStrategy() {
  const match = window.location.pathname.match(/^\/paper-live\/token\/([^/]+)\/strategy\/([^/]+)/);
  return match ? { symbol: decodeURIComponent(match[1]), strategy: decodeURIComponent(match[2]) } : null;
}

function pathPaperTrade() {
  const match = window.location.pathname.match(/^\/paper-live\/trade\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function isPaperLive() {
  return window.location.pathname === "/paper-live" || window.location.pathname.startsWith("/paper-live/");
}

function isWSLive() {
  return window.location.pathname === "/ws-live" || window.location.pathname.startsWith("/ws-live/");
}

function pathWSToken() {
  const match = window.location.pathname.match(/^\/ws-live\/token\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function pathWSTokenExit() {
  const match = window.location.pathname.match(/^\/ws-live\/token\/([^/]+)\/exit\/(tx_on|tx_off)$/);
  return match ? { symbol: decodeURIComponent(match[1]), exitProfile: match[2] } : null;
}

function pathWSStrategy() {
  const match = window.location.pathname.match(/^\/ws-live\/token\/([^/]+)\/strategy\/([^/]+)/);
  return match ? { symbol: decodeURIComponent(match[1]), strategy: decodeURIComponent(match[2]) } : null;
}

function isHistorical() {
  return window.location.pathname === "/research/historical" || window.location.pathname.startsWith("/research/historical/");
}

function pathHistoricalToken() {
  const match = window.location.pathname.match(/^\/research\/historical\/token\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function pathHistoricalStrategy() {
  const match = window.location.pathname.match(/^\/research\/historical\/token\/([^/]+)\/strategy\/([^/]+)/);
  return match ? { symbol: decodeURIComponent(match[1]), strategy: decodeURIComponent(match[2]) } : null;
}

async function loadLiveData() {
  try {
    const response = await fetch(`/live_data.json?ts=${Date.now()}`, { cache: "no-store" });
    if (response.ok) LIVE = await response.json();
  } catch (_error) {
    LIVE = LIVE || {};
  }
}

async function loadWSData() {
  try {
    const response = await fetch(`/ws_data.json?ts=${Date.now()}`, { cache: "no-store" });
    if (response.ok) WS = await response.json();
  } catch (_error) {
    WS = WS || {};
  }
}

async function loadHistoricalData() {
  try {
    const response = await fetch(`/historical_data.json?ts=${Date.now()}`, { cache: "no-store" });
    if (response.ok) HIST = await response.json();
  } catch (_error) {
    HIST = HIST || {};
  }
}

function go(path) {
  history.pushState({}, "", path);
  render();
}

function setTitle(label, title) {
  document.getElementById("routeLabel").textContent = label;
  document.getElementById("pageTitle").textContent = title;
}

function setRunControlsVisible(visible) {
  const field = document.querySelector(".sidebar .field");
  if (field) field.style.display = visible ? "" : "none";
}

function initShell() {
  const runSelect = document.getElementById("runSelect");
  runSelect.innerHTML = Object.keys(DATA.runs || {})
    .map((id) => `<option value="${esc(id)}">${esc(id)}</option>`)
    .join("");
  runSelect.value = state.runId;
  runSelect.addEventListener("change", () => {
    state.runId = runSelect.value;
    state.strategy = null;
    localStorage.setItem("bulkDashboardRun", state.runId);
    render();
  });
  document.getElementById("refreshBtn").addEventListener("click", () => window.location.reload());
  window.addEventListener("popstate", render);
}

function renderNav(activeToken) {
  setRunControlsVisible(true);
  const bestBySymbol = Object.fromEntries((run().best || []).map((x) => [x.symbol, x]));
  const nav = document.getElementById("tokenNav");
  nav.innerHTML = [
    `<a class="token-link ${!activeToken && !isPaperLive() && !isWSLive() ? "active" : ""}" href="/" data-route="/"><span>Overview</span><small>all</small></a>`,
    `<a class="token-link ${isPaperLive() && !activeToken ? "active" : ""}" href="/paper-live" data-route="/paper-live"><span>Paper Live</span><small>${esc((LIVE.open || []).length || 0)} open</small></a>`,
    `<a class="token-link ${isWSLive() ? "active" : ""}" href="/ws-live" data-route="/ws-live"><span>WS Paper</span><small>${esc(WS.health || "offline")}</small></a>`,
    ...allSymbols().map((symbol) => {
      const best = bestBySymbol[symbol];
      const label = best ? `${signed(best.net_pnl, true)}` : "sem";
      return `<a class="token-link ${activeToken === symbol ? "active" : ""}" href="/token/${encodeURIComponent(symbol)}" data-route="/token/${encodeURIComponent(symbol)}"><span>${esc(symbol)}</span><small>${esc(label)}</small></a>`;
    }),
  ].join("");
  nav.querySelectorAll("a[data-route]").forEach((a) => {
    a.addEventListener("click", (event) => {
      event.preventDefault();
      state.strategy = null;
      go(a.getAttribute("data-route"));
    });
  });
}

function kpi(label, value, sub = "", klass = "") {
  return `<div class="card pad kpi"><span>${esc(label)}</span><strong class="${klass}">${value}</strong>${sub ? `<small>${esc(sub)}</small>` : ""}</div>`;
}

function table(columns, rows, opts = {}) {
  if (!rows.length) return `<div class="card empty">Sem dados para este filtro.</div>`;
  const head = columns.map((c) => `<th>${esc(c.label)}</th>`).join("");
  const body = rows
    .map((row) => {
      const attrs = opts.rowAttrs ? opts.rowAttrs(row) : "";
      const cells = columns.map((c) => `<td>${c.render ? c.render(row) : esc(row[c.key])}</td>`).join("");
      return `<tr ${attrs}>${cells}</tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderOverview() {
  const r = run();
  const symbols = r.symbols || [];
  const coverageRows = Object.values(r.coverage || {});
  const totalSignals = coverageRows.reduce((sum, row) => sum + Number(row.candidate_signals || 0), 0);
  const profitable = (r.best || []).filter((x) => Number(x.net_pnl) > 0).length;
  const bestNet = (r.best || []).reduce((sum, row) => sum + Number(row.net_pnl || 0), 0);

  setTitle("Overview", "Dashboard de estrategias");
  renderNav(null);

  document.getElementById("content").innerHTML = `
    <div class="grid cols-4">
      ${kpi("Tokens", fmt.format(symbols.length), `${DATA.mainScenario} cap ${DATA.mainCap}`)}
      ${kpi("Sinais candidatos", fmt.format(totalSignals), "antes do filtro estrategia/dia")}
      ${kpi("Tokens positivos", `${profitable}/${symbols.length}`, "melhor estrategia por token")}
      ${kpi("Soma melhores", signed(bestNet, true), "nao e carteira final", cls(bestNet))}
    </div>

    <section class="section">
      <div class="toolbar">
        <div><h3>Melhor estrategia por token</h3><p>Ranking do cenario principal, cap 40, phase1_tier1.</p></div>
      </div>
      ${table(
        [
          { label: "Token", key: "symbol", render: (x) => `<a class="pill" href="/token/${encodeURIComponent(x.symbol)}" data-route="/token/${encodeURIComponent(x.symbol)}">${esc(x.symbol)}</a>` },
          { label: "Estrategia", key: "strategy" },
          { label: "Trades", key: "trades" },
          { label: "Conf.", key: "sample_confidence", render: (x) => esc(x.sample_confidence || "baixa") },
          { label: "PnL", key: "net_pnl", render: (x) => `<span class="${cls(x.net_pnl)}">${signed(x.net_pnl, true)}</span>` },
          { label: "Win", key: "win_rate", render: (x) => `${fmt.format(x.win_rate)}%` },
          { label: "PF", key: "profit_factor" },
          { label: "DD", key: "max_drawdown", render: (x) => usd.format(x.max_drawdown) },
          { label: "Avg", key: "avg_trade", render: (x) => signed(x.avg_trade, true) },
        ],
        r.best || []
      )}
    </section>

    <section class="section grid cols-2">
      <div class="card pad">
        <div class="toolbar"><div><h3>Ranking tradeable</h3><p>Filtra PF > 1.15 e minimo de trades.</p></div></div>
        ${table(
          [
            { label: "Token", key: "symbol" },
            { label: "Estrategia", key: "strategy" },
            { label: "Trades", key: "trades" },
            { label: "Conf.", key: "sample_confidence", render: (x) => esc(x.sample_confidence || "baixa") },
            { label: "Readiness", key: "readiness", render: (x) => esc(x.readiness || "observacao") },
            { label: "PnL", key: "net_pnl", render: (x) => `<span class="${cls(x.net_pnl)}">${signed(x.net_pnl, true)}</span>` },
            { label: "PF", key: "profit_factor" },
            { label: "Score", key: "research_score" },
          ],
          (r.tradeable || []).slice(0, 15)
        )}
      </div>
      <div class="card pad">
        <div class="toolbar"><div><h3>Motores no agregado</h3><p>Apenas diagnostico, nao ligar tudo junto.</p></div></div>
        ${table(
          [
            { label: "Estrategia", key: "group" },
            { label: "Trades", key: "trades" },
            { label: "PnL", key: "net_pnl", render: (x) => `<span class="${cls(x.net_pnl)}">${signed(x.net_pnl, true)}</span>` },
            { label: "PF", key: "profit_factor" },
          ],
          (r.strategyTotals || []).slice(0, 15)
        )}
      </div>
    </section>
  `;

  document.querySelectorAll("a[data-route]").forEach((a) => {
    a.addEventListener("click", (event) => {
      event.preventDefault();
      go(a.getAttribute("data-route"));
    });
  });
}

function renderToken(symbol) {
  const r = run();
  const coverage = (r.coverage || {})[symbol] || {};
  const strategies = (r.pairStrategy || {})[symbol] || [];
  const best = (r.best || []).find((x) => x.symbol === symbol);
  const selected = state.strategy && strategies.some((x) => x.strategy === state.strategy)
    ? state.strategy
    : (best && best.strategy) || (strategies[0] && strategies[0].strategy);

  state.strategy = selected;
  setTitle(symbol, selected ? `Avaliando ${selected}` : "Sem estrategia aprovada");
  renderNav(symbol);

  document.getElementById("content").innerHTML = `
    <div class="grid cols-4">
      ${kpi("Cobertura", `${fmt.format(coverage.coverage_days || 0)} dias`, `${coverage.candles_5m || 0} candles 5m`)}
      ${kpi("Candidatos", fmt.format(coverage.candidate_signals || 0), "sinais brutos gerados")}
      ${kpi("Melhor PnL", best ? signed(best.net_pnl, true) : "$0.00", best ? best.strategy : "sem dados", best ? cls(best.net_pnl) : "")}
      ${kpi("Best PF", best ? fmt.format(best.profit_factor) : "0", best ? `${best.trades} trades` : "sem trades")}
    </div>

    <section class="section">
      <div class="toolbar">
        <div><h3>Estrategias do token</h3><p>Clique em uma linha para abrir o detalhe.</p></div>
        <label class="field">
          <span>Estrategia</span>
          <select id="strategySelect" class="strategy-select">
            ${strategies.map((x) => `<option value="${esc(x.strategy)}" ${x.strategy === selected ? "selected" : ""}>${esc(x.strategy)}</option>`).join("")}
          </select>
        </label>
      </div>
      ${table(
        [
          { label: "Estrategia", key: "strategy" },
          { label: "Trades", key: "trades" },
          { label: "Conf.", key: "sample_confidence", render: (x) => esc(x.sample_confidence || "baixa") },
          { label: "PnL", key: "net_pnl", render: (x) => `<span class="${cls(x.net_pnl)}">${signed(x.net_pnl, true)}</span>` },
          { label: "Win", key: "win_rate", render: (x) => `${fmt.format(x.win_rate)}%` },
          { label: "PF", key: "profit_factor" },
          { label: "DD", key: "max_drawdown", render: (x) => usd.format(x.max_drawdown) },
          { label: "Loss seq", key: "max_losing_streak" },
          { label: "Avg", key: "avg_trade", render: (x) => signed(x.avg_trade, true) },
        ],
        strategies,
        { rowAttrs: (x) => `class="clickable" data-strategy="${esc(x.strategy)}"` }
      )}
    </section>

    <section id="strategyDetail" class="section"></section>
  `;

  const select = document.getElementById("strategySelect");
  if (select) {
    select.addEventListener("change", () => {
      state.strategy = select.value;
      setTitle(symbol, `Avaliando ${state.strategy}`);
      renderStrategyDetail(symbol, state.strategy);
    });
  }
  document.querySelectorAll("tr[data-strategy]").forEach((tr) => {
    tr.addEventListener("click", () => {
      state.strategy = tr.getAttribute("data-strategy");
      if (select) select.value = state.strategy;
      setTitle(symbol, `Avaliando ${state.strategy}`);
      renderStrategyDetail(symbol, state.strategy);
      document.getElementById("strategyDetail").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
  renderStrategyDetail(symbol, selected);
}

function renderStrategyDetail(symbol, strategy) {
  const detailEl = document.getElementById("strategyDetail");
  if (!detailEl) return;
  if (!strategy) {
    detailEl.innerHTML = `<div class="card empty">Este token nao tem trades no filtro atual.</div>`;
    return;
  }

  const r = run();
  const key = `${symbol}|${strategy}`;
  const metric = ((r.pairStrategy || {})[symbol] || []).find((x) => x.strategy === strategy) || {};
  const robust = (r.robustness || {})[key] || {};
  const detail = (r.strategyDetails || {})[key] || { equity: [], daily: [], exits: {}, reasons: {}, sides: {}, liquidity: {}, trades: [] };
  const matrix = Object.values((r.scenarioMatrix || {})[key] || {}).sort((a, b) => a.cap - b.cap || String(a.scenario).localeCompare(String(b.scenario)));

  detailEl.innerHTML = `
    <div class="grid cols-4">
      ${kpi("PnL liquido", signed(metric.net_pnl, true), `${metric.trades || 0} trades`, cls(metric.net_pnl))}
      ${kpi("Profit factor", fmt.format(metric.profit_factor || 0), `${fmt.format(metric.win_rate || 0)}% win`)}
      ${kpi("Drawdown", usd.format(metric.max_drawdown || 0), `${metric.max_losing_streak || 0} perdas seguidas`, Number(metric.max_drawdown || 0) > 5 ? "negative" : "")}
      ${kpi("Dias positivos", `${fmt.format(robust.positive_day_rate || 0)}%`, `${robust.positive_days || 0}/${robust.days_active || 0} dias`)}
    </div>

    <section class="section split">
      <div class="card pad">
        <div class="toolbar"><div><h3>Equity da estrategia</h3><p>${esc(symbol)} / ${esc(strategy)}</p></div></div>
        ${lineChart(detail.equity || [])}
      </div>
      <div class="card pad">
        <div class="toolbar"><div><h3>Resumo operacional</h3><p>Saidas, lados e liquidez.</p></div></div>
        <div class="bar-list">
          ${barList("Exit", detail.exits)}
          ${barList("Side", detail.sides)}
          ${barList("Liq", detail.liquidity)}
        </div>
      </div>
    </section>

    <section class="section grid cols-2">
      <div class="card pad">
        <div class="toolbar"><div><h3>PnL por dia</h3><p>Robustez curta da amostra.</p></div></div>
        ${barChart(detail.daily || [])}
      </div>
      <div class="card pad">
        <div class="toolbar"><div><h3>Motivos de entrada</h3><p>Top razoes registradas pelo motor.</p></div></div>
        <div class="bar-list">${barList("Reason", detail.reasons)}</div>
      </div>
    </section>

    <section class="section">
      <div class="toolbar"><div><h3>Cenarios e caps</h3><p>Mesma estrategia comparada em custos e limites diarios.</p></div></div>
      ${table(
        [
          { label: "Cap", key: "cap" },
          { label: "Cenario", key: "scenario" },
          { label: "Trades", key: "trades" },
          { label: "PnL", key: "net_pnl", render: (x) => `<span class="${cls(x.net_pnl)}">${signed(x.net_pnl, true)}</span>` },
          { label: "Custos", key: "costs", render: (x) => usd.format(x.costs) },
          { label: "Win", key: "win_rate", render: (x) => `${fmt.format(x.win_rate)}%` },
          { label: "PF", key: "profit_factor" },
          { label: "DD", key: "max_drawdown", render: (x) => usd.format(x.max_drawdown) },
        ],
        matrix
      )}
    </section>

    <section class="section">
      <div class="toolbar"><div><h3>Trades</h3><p>Lista completa da estrategia selecionada no cenario principal.</p></div></div>
      ${table(
        [
          { label: "Entrada", key: "time" },
          { label: "Side", key: "side" },
          { label: "Liq", key: "liquidity" },
          { label: "Entry", key: "entry" },
          { label: "Exit", key: "exit" },
          { label: "Hold", key: "hold", render: (x) => `${fmt.format(x.hold)}m` },
          { label: "PnL", key: "pnl", render: (x) => `<span class="${cls(x.pnl)}">${signed(x.pnl, true)}</span>` },
          { label: "Saida", key: "exit_reason" },
          { label: "Razao", key: "reason" },
        ],
        detail.trades || []
      )}
    </section>
  `;
  document.querySelectorAll("a[data-route]").forEach((a) => {
    a.addEventListener("click", (event) => {
      event.preventDefault();
      go(a.getAttribute("data-route"));
    });
  });
}

function wsSymbols() {
  const set = new Set();
  for (const row of WS.accounts || []) set.add(row.symbol);
  for (const row of WS.books || []) set.add(row.symbol);
  for (const row of WS.open || []) set.add(row.symbol);
  for (const row of WS.closed || []) set.add(row.symbol);
  return [...set].filter(Boolean).sort();
}

function exitProfile(rowOrStrategy) {
  if (typeof rowOrStrategy === "string") {
    const parts = rowOrStrategy.split("__");
    const last = parts[parts.length - 1];
    return last === "tx_off" ? "tx_off" : "tx_on";
  }
  return rowOrStrategy?.exit_profile === "tx_off" ? "tx_off" : "tx_on";
}

function hasExplicitExitReplicas(rows) {
  return (rows || []).some((row) => String(row.strategy || "").endsWith("__tx_on") || String(row.strategy || "").endsWith("__tx_off"));
}

function isExitReplica(row, profile, explicitOnly = false) {
  if (exitProfile(row) !== profile) return false;
  const strategy = String(row.strategy || "");
  const suffix = profile === "tx_off" ? "__tx_off" : "__tx_on";
  return !explicitOnly || strategy.endsWith(suffix);
}

function exitLabel(profile) {
  return profile === "tx_off" ? "OFF" : "ON";
}

function wsRowsByExit(rows, profile) {
  if (!profile) return rows || [];
  const explicitOnly = hasExplicitExitReplicas(rows);
  return (rows || []).filter((row) => isExitReplica(row, profile, explicitOnly));
}

function wsTokenRoute(symbol, profile = null) {
  const base = `/ws-live/token/${encodeURIComponent(symbol)}`;
  return profile ? `${base}/exit/${profile}` : base;
}

function wsTotals(rows = WS.accounts || []) {
  return rows.reduce(
    (acc, row) => {
      const start = Number(row.start_equity || 0);
      const equityNow = Number(row.equity ?? start);
      acc.accounts += 1;
      acc.start += start;
      acc.equity += equityNow;
      acc.realized += Number(row.realized_pnl || 0);
      acc.openPnl += Number(row.open_pnl || 0);
      acc.closed += Number(row.closed_trades || 0);
      acc.open += Number(row.open_positions || 0);
      acc.wins += Number(row.closed_trades || 0) * Number(row.win_rate || 0) / 100;
      return acc;
    },
    { accounts: 0, start: 0, equity: 0, realized: 0, openPnl: 0, closed: 0, open: 0, wins: 0 }
  );
}

function wsAggregate(rows, key) {
  const grouped = {};
  for (const row of rows || []) {
    const id = row[key] || "";
    if (!id) continue;
    if (!grouped[id]) {
      grouped[id] = {
        key: id,
        symbol: row.symbol,
        variant_base: row.variant_base,
        exit_profile: row.exit_profile,
        accounts: 0,
        start: 0,
        equity: 0,
        realized_pnl: 0,
        open_pnl: 0,
        closed_trades: 0,
        open_positions: 0,
        wins: 0,
        hold_sum: 0,
        time_exits: 0,
        safety_exits: 0,
        stop_exits: 0,
        take_exits: 0,
      };
    }
    const item = grouped[id];
    const start = Number(row.start_equity || 0);
    item.accounts += 1;
    item.start += start;
    item.equity += Number(row.equity ?? start);
    item.realized_pnl += Number(row.realized_pnl || 0);
    item.open_pnl += Number(row.open_pnl || 0);
    item.closed_trades += Number(row.closed_trades || 0);
    item.open_positions += Number(row.open_positions || 0);
    item.wins += Number(row.closed_trades || 0) * Number(row.win_rate || 0) / 100;
    item.hold_sum += Number(row.closed_trades || 0) * Number(row.avg_hold_minutes || 0);
    item.time_exits += Number(row.time_exits || 0);
    item.safety_exits += Number(row.safety_exits || 0);
    item.stop_exits += Number(row.stop_exits || 0);
    item.take_exits += Number(row.take_exits || 0);
  }
  return Object.values(grouped).map((item) => ({
    ...item,
    pnl: item.equity - item.start,
    win_rate: item.closed_trades ? item.wins / item.closed_trades * 100 : 0,
    avg_hold_minutes: item.closed_trades ? item.hold_sum / item.closed_trades : 0,
  }));
}

function wsBook(symbol) {
  return (WS.books || []).find((row) => row.symbol === symbol) || {};
}

function renderWSNav(activeToken = null, activeExitProfile = null) {
  setRunControlsVisible(false);
  const nav = document.getElementById("tokenNav");
  const byTokenExit = {};
  for (const profile of ["tx_on", "tx_off"]) {
    for (const row of wsAggregate(wsRowsByExit(WS.accounts || [], profile), "symbol")) {
      byTokenExit[`${row.key}|${profile}`] = row;
    }
  }
  nav.innerHTML = [
    `<a class="token-link ${activeToken ? "" : "active"}" href="/ws-live" data-route="/ws-live"><span>WS Live</span><small>${esc(WS.health || "OFFLINE")}</small></a>`,
    `<a class="token-link" href="/research/historical" data-route="/research/historical"><span>Research</span><small>historical</small></a>`,
    ...wsSymbols().flatMap((symbol) =>
      ["tx_on", "tx_off"].map((profile) => {
        const row = byTokenExit[`${symbol}|${profile}`] || {};
        const active = activeToken === symbol && activeExitProfile === profile;
        return `<a class="token-link ws-exit-link ${profile === "tx_off" ? "tx-off" : "tx-on"} ${active ? "active" : ""}" href="/ws-live/token/${encodeURIComponent(symbol)}/exit/${profile}" data-route="/ws-live/token/${encodeURIComponent(symbol)}/exit/${profile}"><span>${esc(symbol)} <em>${exitLabel(profile)}</em></span><small>${signed(row.pnl || 0, true)}</small></a>`;
      })
    ),
  ].join("");
  bindRoutes();
}

function historicalSymbols() {
  const set = new Set();
  for (const row of HIST.best || []) set.add(row.symbol);
  for (const row of HIST.coverage || []) set.add(row.symbol);
  for (const row of HIST.trades || []) set.add(row.symbol);
  return [...set].filter(Boolean).sort();
}

function renderHistoricalNav(activeToken = null) {
  setRunControlsVisible(false);
  const byToken = Object.fromEntries((HIST.best || []).map((row) => [row.symbol, row]));
  const nav = document.getElementById("tokenNav");
  nav.innerHTML = [
    `<a class="token-link" href="/ws-live" data-route="/ws-live"><span>WS Live</span><small>${esc(WS.health || "OFFLINE")}</small></a>`,
    `<a class="token-link ${activeToken ? "" : "active"}" href="/research/historical" data-route="/research/historical"><span>Research</span><small>${esc(HIST.scenario || "history")}</small></a>`,
    ...historicalSymbols().map((symbol) => {
      const row = byToken[symbol] || {};
      return `<a class="token-link ${activeToken === symbol ? "active" : ""}" href="/research/historical/token/${encodeURIComponent(symbol)}" data-route="/research/historical/token/${encodeURIComponent(symbol)}"><span>${esc(symbol)}</span><small>${signed(row.net_pnl || 0, true)}</small></a>`;
    }),
  ].join("");
  bindRoutes();
}

function renderWSLive() {
  setTitle("WS Live", "Strategy Lab");
  renderWSNav(null);
  const accounts = WS.accounts || [];
  const totals = wsTotals(accounts);
  const tokenRows = wsAggregate(accounts, "symbol").sort((a, b) => b.pnl - a.pnl);
  const baseRows = wsAggregate(accounts, "variant_base").sort((a, b) => b.pnl - a.pnl).slice(0, 30);
  const exitRows = wsAggregate(accounts, "exit_profile").sort((a, b) => b.pnl - a.pnl);
  const bestAccounts = accounts.slice().sort((a, b) => (Number(b.equity || 0) - Number(b.start_equity || 0)) - (Number(a.equity || 0) - Number(a.start_equity || 0))).slice(0, 30);
  const worstAccounts = accounts.slice().sort((a, b) => (Number(a.equity || 0) - Number(a.start_equity || 0)) - (Number(b.equity || 0) - Number(b.start_equity || 0))).slice(0, 15);
  const signals = WS.signals || [];
  const blocked = signals.filter((row) => String(row.status || "").startsWith("blocked")).length;
  const open = WS.open || [];
  const closed = WS.closed || [];

  document.getElementById("content").innerHTML = `
    <div class="ws-hero card pad">
      <div>
        <p class="eyebrow">WebSocket paper exchange</p>
        <h3>${esc(WS.health || "OFFLINE")} <span>${esc(WS.status || "unknown")} | ${esc(WS.lastMessageAgeSeconds ?? "?")}s</span></h3>
      </div>
      <div class="summary-strip">
        <span class="pill">${fmt.format(WS.messages || 0)} mensagens</span>
        <span class="pill">5m ${esc(WS.last5mCandleAt || "-")}</span>
        <span class="pill">REST ${esc(WS.lastRestCandleSyncAt || "-")}</span>
        <span class="pill">${fmt.format(accounts.length)} contas</span>
        <span class="pill">${fmt.format(open.length)} abertas recentes</span>
        <span class="pill">${fmt.format(closed.length)} fechadas recentes</span>
      </div>
    </div>
    <div class="grid cols-4 section">
      ${kpi("Equity", signed(totals.equity - totals.start, true), `${usd.format(totals.equity)} / ${usd.format(totals.start)}`, cls(totals.equity - totals.start))}
      ${kpi("Realizado", signed(totals.realized, true), `${fmt.format(totals.closed)} fechadas`, cls(totals.realized))}
      ${kpi("Aberto", signed(totals.openPnl, true), `${fmt.format(totals.open)} posicoes`, cls(totals.openPnl))}
      ${kpi("Sinais", fmt.format(signals.length), `${fmt.format(blocked)} bloqueados`)}
    </div>

    <section class="section grid cols-2">
      <div class="card pad">
        <div class="toolbar"><div><h3>Tokens</h3><p>PnL agregado de todas as contas isoladas.</p></div></div>
        ${wsTokenTable(tokenRows)}
      </div>
      <div class="card pad">
        <div class="toolbar"><div><h3>Market watch</h3><p>Bid/ask, spread e profundidade do L2.</p></div></div>
        ${wsBookTable(WS.books || [])}
      </div>
    </section>

    <section class="section grid cols-2">
      <div class="card pad">
        <div class="toolbar"><div><h3>Melhores estrategias-base</h3><p>Agregado por motor, todas as variantes e tokens.</p></div></div>
        ${wsBaseTable(baseRows)}
      </div>
      <div class="card pad">
        <div class="toolbar"><div><h3>Time exit ON/OFF</h3><p>Comparacao agregada das replicas tx_on e tx_off.</p></div></div>
        ${wsBaseTable(exitRows)}
      </div>
    </section>

    <section class="section grid cols-2">
      <div class="card pad">
        <div class="toolbar"><div><h3>Melhores contas</h3><p>Ranking por token + estrategia + variante.</p></div></div>
        ${wsAccountsTable(bestAccounts)}
      </div>
      <div class="card pad">
        <div class="toolbar"><div><h3>Risco aberto</h3><p>Posicoes abertas marcadas pelo bid/ask.</p></div></div>
        ${wsOpenTable(open.slice(0, 80))}
      </div>
      <div class="card pad">
        <div class="toolbar"><div><h3>Piores contas</h3><p>Candidatas a bloqueio se persistirem.</p></div></div>
        ${wsAccountsTable(worstAccounts)}
      </div>
    </section>

    <section class="section grid cols-2">
      <div class="card pad">
        <div class="toolbar"><div><h3>Fechamentos recentes</h3><p>Stops, takes e time exits do paper WS.</p></div></div>
        ${wsClosedTable(closed.slice(0, 80))}
      </div>
      <div class="card pad">
        <div class="toolbar"><div><h3>Trades publicos</h3><p>Fills reais do mercado para auditoria.</p></div></div>
        ${wsPublicTradesTable((WS.publicTrades || []).slice(0, 80))}
      </div>
    </section>
  `;
  bindRoutes();
}

function wsTokenTable(rows) {
  return table(
    [
      { label: "Token", key: "key", render: (x) => `<a class="pill" href="${wsTokenRoute(x.key)}" data-route="${wsTokenRoute(x.key)}">${esc(x.key)}</a>` },
      { label: "Contas", key: "accounts" },
      { label: "Equity", key: "equity", render: (x) => `<span class="${cls(x.pnl)}">${usd.format(x.equity || 0)}</span>` },
      { label: "PnL", key: "pnl", render: (x) => `<span class="${cls(x.pnl)}">${signed(x.pnl, true)}</span>` },
      { label: "Real", key: "realized_pnl", render: (x) => `<span class="${cls(x.realized_pnl)}">${signed(x.realized_pnl, true)}</span>` },
      { label: "Aberto", key: "open_pnl", render: (x) => `<span class="${cls(x.open_pnl)}">${signed(x.open_pnl, true)}</span>` },
      { label: "Fechadas", key: "closed_trades" },
      { label: "Abertas", key: "open_positions" },
      { label: "Hold", key: "avg_hold_minutes", render: (x) => `${fmt.format(x.avg_hold_minutes || 0)}m` },
      { label: "Time", key: "time_exits" },
      { label: "Safety", key: "safety_exits" },
      { label: "Win", key: "win_rate", render: (x) => `${fmt.format(x.win_rate || 0)}%` },
    ],
    rows
  );
}

function wsBaseTable(rows, symbol = null) {
  return table(
    [
      { label: "Motor", key: "key" },
      { label: "Contas", key: "accounts" },
      { label: "PnL", key: "pnl", render: (x) => `<span class="${cls(x.pnl)}">${signed(x.pnl, true)}</span>` },
      { label: "Real", key: "realized_pnl", render: (x) => `<span class="${cls(x.realized_pnl)}">${signed(x.realized_pnl, true)}</span>` },
      { label: "Aberto", key: "open_pnl", render: (x) => `<span class="${cls(x.open_pnl)}">${signed(x.open_pnl, true)}</span>` },
      { label: "Fechadas", key: "closed_trades" },
      { label: "Abertas", key: "open_positions" },
      { label: "Hold", key: "avg_hold_minutes", render: (x) => `${fmt.format(x.avg_hold_minutes || 0)}m` },
      { label: "Time", key: "time_exits" },
      { label: "Safety", key: "safety_exits" },
      { label: "Stop", key: "stop_exits" },
      { label: "Take", key: "take_exits" },
      { label: "Win", key: "win_rate", render: (x) => `${fmt.format(x.win_rate || 0)}%` },
    ],
    symbol ? rows.map((row) => ({ ...row, symbol })) : rows
  );
}

function renderWSToken(symbol, selectedExitProfile = null) {
  const exitSuffix = selectedExitProfile ? ` ${exitLabel(selectedExitProfile)}` : "";
  setTitle(`WS ${symbol}${exitSuffix}`, "Analise por token");
  renderWSNav(symbol, selectedExitProfile);
  const accounts = wsRowsByExit(WS.accounts || [], selectedExitProfile).filter((row) => row.symbol === symbol);
  const totals = wsTotals(accounts);
  const book = wsBook(symbol);
  const baseRows = wsAggregate(accounts, "variant_base").sort((a, b) => b.pnl - a.pnl);
  const exitRows = wsAggregate(accounts, "exit_profile").sort((a, b) => b.pnl - a.pnl);
  const bestAccounts = accounts.slice().sort((a, b) => (Number(b.equity || 0) - Number(b.start_equity || 0)) - (Number(a.equity || 0) - Number(a.start_equity || 0))).slice(0, 80);
  const open = wsRowsByExit(WS.open || [], selectedExitProfile).filter((row) => row.symbol === symbol);
  const closed = wsRowsByExit(WS.closed || [], selectedExitProfile).filter((row) => row.symbol === symbol);
  const signals = wsRowsByExit(WS.signals || [], selectedExitProfile).filter((row) => row.symbol === symbol);
  const publicTrades = (WS.publicTrades || []).filter((row) => row.symbol === symbol);
  const blocked = signals.filter((row) => String(row.status || "").startsWith("blocked")).length;

  document.getElementById("content").innerHTML = `
    <div class="ws-hero card pad">
      <div>
        <p class="eyebrow">Token WS paper</p>
        <h3>${esc(symbol)}${selectedExitProfile ? ` <em class="exit-title ${selectedExitProfile === "tx_off" ? "tx-off" : "tx-on"}">${exitLabel(selectedExitProfile)}</em>` : ""} <span>${esc(WS.health || "OFFLINE")} | ${esc(WS.lastMessageAgeSeconds ?? "?")}s</span></h3>
      </div>
      <div class="summary-strip">
        <span class="pill">Bid ${esc(book.bid ?? "-")}</span>
        <span class="pill">Ask ${esc(book.ask ?? "-")}</span>
        <span class="pill">Spread ${fmt.format(book.spread_bps || 0)} bps</span>
      </div>
    </div>

    <div class="grid cols-4 section">
      ${kpi("PnL total", signed(totals.equity - totals.start, true), `${usd.format(totals.equity)} equity`, cls(totals.equity - totals.start))}
      ${kpi("Realizado", signed(totals.realized, true), `${fmt.format(totals.closed)} fechadas`, cls(totals.realized))}
      ${kpi("Aberto", signed(totals.openPnl, true), `${fmt.format(totals.open)} posicoes`, cls(totals.openPnl))}
      ${kpi("Sinais", fmt.format(signals.length), `${fmt.format(blocked)} bloqueados`)}
    </div>

    <section class="section grid cols-2">
      <div class="card pad">
        <div class="toolbar"><div><h3>Motores do token</h3><p>Agregado por estrategia-base ${selectedExitProfile ? `com ${exitLabel(selectedExitProfile)}` : "neste ativo"}.</p></div></div>
        ${wsBaseTable(baseRows, symbol)}
      </div>
      <div class="card pad">
        <div class="toolbar"><div><h3>Livro atual</h3><p>Preco executavel usado nos fills paper.</p></div></div>
        ${wsBookTable(book.symbol ? [book] : [])}
      </div>
    </section>

    <section class="section">
      <div class="toolbar"><div><h3>Time exit ON/OFF</h3><p>${esc(symbol)} comparando tx_on contra tx_off.</p></div></div>
      ${wsBaseTable(exitRows, symbol)}
    </section>

    <section class="section">
      <div class="toolbar"><div><h3>Contas isoladas</h3><p>Cada estrategia + variante tem banca propria de $100.</p></div></div>
      ${wsAccountsTable(bestAccounts)}
    </section>

    <section class="section grid cols-2">
      <div class="card pad"><div class="toolbar"><div><h3>Posicoes abertas</h3><p>Marcadas em bid/ask WS.</p></div></div>${wsOpenTable(open)}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Trades fechados</h3><p>Saidas por stop, take ou time exit.</p></div></div>${wsClosedTable(closed)}</div>
    </section>

    <section class="section grid cols-2">
      <div class="card pad"><div class="toolbar"><div><h3>Sinais recentes</h3><p>O que abriu e o que foi bloqueado.</p></div></div>${wsSignalsTable(signals)}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Trades reais do mercado</h3><p>Fluxo publico recebido via WebSocket.</p></div></div>${wsPublicTradesTable(publicTrades)}</div>
    </section>
  `;
  bindRoutes();
}

function renderWSTokenStrategy(symbol, strategy) {
  setTitle(`${symbol} / ${strategy}`, "Conta isolada WS");
  renderWSNav(symbol, exitProfile(strategy));
  const account = (WS.accounts || []).find((row) => row.symbol === symbol && row.strategy === strategy) || { symbol, strategy, start_equity: 100, equity: 100 };
  const open = (WS.open || []).filter((row) => row.symbol === symbol && row.strategy === strategy);
  const closed = (WS.closed || []).filter((row) => row.symbol === symbol && row.strategy === strategy);
  const signals = (WS.signals || []).filter((row) => row.symbol === symbol && row.strategy === strategy);
  const pnl = Number(account.equity || account.start_equity || 0) - Number(account.start_equity || 0);
  const book = wsBook(symbol);

  document.getElementById("content").innerHTML = `
    <div class="ws-hero card pad">
      <div>
        <p class="eyebrow">Conta paper isolada</p>
        <h3>${esc(strategy)} <span>${esc(symbol)} | ${esc(account.variant_profile || "variant")}</span></h3>
      </div>
      <div class="summary-strip">
        <span class="pill">Start ${usd.format(account.start_equity || 100)}</span>
        <span class="pill">Equity ${usd.format(account.equity || account.start_equity || 100)}</span>
        <span class="pill">${esc(account.status || "active")}</span>
      </div>
    </div>

    <div class="grid cols-4 section">
      ${kpi("PnL", signed(pnl, true), `${usd.format(account.equity || 0)} equity`, cls(pnl))}
      ${kpi("Realizado", signed(account.realized_pnl || 0, true), `${fmt.format(account.closed_trades || 0)} fechadas`, cls(account.realized_pnl))}
      ${kpi("Aberto", signed(account.open_pnl || 0, true), `${fmt.format(account.open_positions || 0)} abertas`, cls(account.open_pnl))}
      ${kpi("Win / PF", `${fmt.format(account.win_rate || 0)}% / ${fmt.format(account.profit_factor || 0)}`, `${fmt.format(signals.length)} sinais`)}
    </div>

    <section class="section grid cols-2">
      <div class="card pad"><div class="toolbar"><div><h3>Book atual</h3><p>Fill paper: long ask/bid, short bid/ask.</p></div></div>${wsBookTable(book.symbol ? [book] : [])}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Resumo da conta</h3><p>Uma estrategia, um token, uma banca.</p></div></div>
        ${wsAccountsTable([account])}
      </div>
    </section>

    <section class="section grid cols-2">
      <div class="card pad"><div class="toolbar"><div><h3>Posicoes abertas</h3><p>Preco executavel do book.</p></div></div>${wsOpenTable(open)}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Trades fechados</h3><p>Historico recente da conta.</p></div></div>${wsClosedTable(closed)}</div>
    </section>

    <section class="section">
      <div class="toolbar"><div><h3>Sinais da estrategia</h3><p>Inclui bloqueio por spread/limite/posicao aberta.</p></div></div>
      ${wsSignalsTable(signals)}
    </section>
  `;
  bindRoutes();
}

function histTotals(rows = HIST.trades || []) {
  return rows.reduce(
    (acc, row) => {
      const pnl = Number(row.net_pnl || 0);
      acc.trades += 1;
      acc.net += pnl;
      acc.gross += Number(row.gross_pnl || 0);
      acc.costs += Number(row.costs || 0);
      if (pnl > 0) acc.wins += 1;
      if (pnl < 0) acc.losses += 1;
      return acc;
    },
    { trades: 0, net: 0, gross: 0, costs: 0, wins: 0, losses: 0 }
  );
}

function histSummaryTable(rows) {
  return table(
    [
      { label: "Token", key: "symbol", render: (x) => `<a class="pill" href="/research/historical/token/${encodeURIComponent(x.symbol)}" data-route="/research/historical/token/${encodeURIComponent(x.symbol)}">${esc(x.symbol)}</a>` },
      { label: "Estrategia", key: "strategy", render: (x) => `<a class="pill" href="/research/historical/token/${encodeURIComponent(x.symbol)}/strategy/${encodeURIComponent(x.strategy || x.best_strategy)}" data-route="/research/historical/token/${encodeURIComponent(x.symbol)}/strategy/${encodeURIComponent(x.strategy || x.best_strategy)}">${esc(x.strategy || x.best_strategy || "")}</a>` },
      { label: "Base", key: "variant_base" },
      { label: "Var", key: "variant_profile" },
      { label: "Exit", key: "exit_profile" },
      { label: "Trades", key: "trades" },
      { label: "PnL", key: "net_pnl", render: (x) => `<span class="${cls(x.net_pnl)}">${signed(x.net_pnl, true)}</span>` },
      { label: "Custos", key: "costs", render: (x) => usd.format(x.costs || 0) },
      { label: "Win", key: "win_rate", render: (x) => `${fmt.format(x.win_rate || 0)}%` },
      { label: "PF", key: "profit_factor", render: (x) => fmt.format(x.profit_factor || 0) },
      { label: "DD", key: "max_drawdown", render: (x) => usd.format(x.max_drawdown || 0) },
      { label: "Avg", key: "avg_trade", render: (x) => signed(x.avg_trade || 0, true) },
      { label: "Hold", key: "avg_hold_minutes", render: (x) => `${fmt.format(x.avg_hold_minutes || 0)}m` },
      { label: "Time", key: "time_exits" },
      { label: "Safety", key: "safety_exits" },
    ],
    rows
  );
}

function histTradesTable(rows) {
  return table(
    [
      { label: "Entrada", key: "entry_time" },
      { label: "Saida", key: "exit_time" },
      { label: "Token", key: "symbol" },
      { label: "Estrategia", key: "strategy" },
      { label: "Exit", key: "exit_profile" },
      { label: "Side", key: "side" },
      { label: "Entry", key: "entry_price" },
      { label: "Exit", key: "exit_price" },
      { label: "PnL", key: "net_pnl", render: (x) => `<span class="${cls(x.net_pnl)}">${signed(x.net_pnl, true)}</span>` },
      { label: "Custos", key: "costs", render: (x) => usd.format(x.costs || 0) },
      { label: "Hold", key: "hold_minutes", render: (x) => `${fmt.format(x.hold_minutes || 0)}m` },
      { label: "Motivo", key: "exit_reason" },
      { label: "Razao", key: "reason" },
    ],
    rows
  );
}

function renderHistoricalOverview() {
  setTitle("Research", "Historical backtest");
  renderHistoricalNav(null);
  const rows = HIST.trades || [];
  const totals = histTotals(rows);
  const coverageDays = (HIST.coverage || []).map((row) => Number(row.coverage_days || 0));
  const minCoverage = coverageDays.length ? Math.min(...coverageDays) : 0;
  const winRate = totals.trades ? totals.wins / totals.trades : 0;

  document.getElementById("content").innerHTML = `
    <div class="ws-hero card pad">
      <div>
        <p class="eyebrow">Historical candle research</p>
        <h3>${esc(HIST.scenario || "phase1_tier1")} <span>cap ${esc(HIST.cap || 40)} | gerado ${esc(HIST.generatedAt || "aguardando")}</span></h3>
      </div>
      <div class="summary-strip">
        <span class="pill">${fmt.format(rows.length)} trades</span>
        <span class="pill">${fmt.format(historicalSymbols().length)} tokens</span>
        <span class="pill">${fmt.format(minCoverage)} dias min</span>
      </div>
    </div>

    <div class="grid cols-4 section">
      ${kpi("PnL liquido", signed(totals.net, true), `bruto ${signed(totals.gross, true)}`, cls(totals.net))}
      ${kpi("Custos", usd.format(totals.costs), "fees/slippage simulados")}
      ${kpi("Win rate", pct(winRate), `${totals.wins} wins / ${totals.losses} losses`)}
      ${kpi("Amostra", fmt.format(totals.trades), `cap ${HIST.cap || 40} por estrategia`)}
    </div>

    <section class="section grid cols-2">
      <div class="card pad">
        <div class="toolbar"><div><h3>Melhor por token</h3><p>Resultado historico do mesmo universo de estrategias.</p></div></div>
        ${histSummaryTable(HIST.best || [])}
      </div>
      <div class="card pad">
        <div class="toolbar"><div><h3>Tradeable ranking</h3><p>Filtra amostra, PF e robustez diaria.</p></div></div>
        ${histSummaryTable((HIST.tradeable || []).slice(0, 30))}
      </div>
    </section>

    <section class="section">
      <div class="toolbar"><div><h3>Time exit ON/OFF</h3><p>Backtest agregado das replicas tx_on e tx_off.</p></div></div>
      ${table(
        [
          { label: "Exit", key: "group" },
          { label: "Trades", key: "trades" },
          { label: "PnL", key: "net_pnl", render: (x) => `<span class="${cls(x.net_pnl)}">${signed(x.net_pnl, true)}</span>` },
          { label: "Win", key: "win_rate", render: (x) => `${fmt.format(x.win_rate || 0)}%` },
          { label: "PF", key: "profit_factor", render: (x) => fmt.format(x.profit_factor || 0) },
          { label: "DD", key: "max_drawdown", render: (x) => usd.format(x.max_drawdown || 0) },
          { label: "Avg", key: "avg_trade", render: (x) => signed(x.avg_trade || 0, true) },
          { label: "Hold", key: "avg_hold_minutes", render: (x) => `${fmt.format(x.avg_hold_minutes || 0)}m` },
          { label: "Time", key: "time_exits" },
          { label: "Safety", key: "safety_exits" },
        ],
        HIST.exitProfileTotals || []
      )}
    </section>

    <section class="section">
      <div class="toolbar"><div><h3>Ranking token + estrategia</h3><p>Use isto para escolher o que vale comparar contra o WS live.</p></div></div>
      ${histSummaryTable((HIST.pairStrategy || []).slice(0, 80))}
    </section>

    <section class="section grid cols-2">
      <div class="card pad"><div class="toolbar"><div><h3>Cobertura de dados</h3><p>Candles historicos disponiveis na BULK staging.</p></div></div>
        ${table(
          [
            { label: "Token", key: "symbol" },
            { label: "Inicio", key: "first_5m" },
            { label: "Fim", key: "last_5m" },
            { label: "5m", key: "candles_5m" },
            { label: "15m", key: "candles_15m" },
            { label: "Dias", key: "coverage_days" },
            { label: "Sinais", key: "candidate_signals" },
          ],
          HIST.coverage || []
        )}
      </div>
      <div class="card pad"><div class="toolbar"><div><h3>PnL diario</h3><p>Todos os tokens juntos no cenario principal.</p></div></div>
        ${barChart((HIST.daily || []).map((row) => [row.day, Number(row.net_pnl || 0)]))}
      </div>
    </section>
  `;
  bindRoutes();
}

function renderHistoricalToken(symbol) {
  setTitle(`Research ${symbol}`, "Historico por token");
  renderHistoricalNav(symbol);
  const trades = (HIST.trades || []).filter((row) => row.symbol === symbol);
  const totals = histTotals(trades);
  const pairRows = (HIST.pairStrategy || []).filter((row) => row.symbol === symbol);
  const best = (HIST.best || []).find((row) => row.symbol === symbol);
  const curve = (HIST.equityBySymbol || {})[symbol] || [];
  const winRate = totals.trades ? totals.wins / totals.trades : 0;

  document.getElementById("content").innerHTML = `
    <div class="ws-hero card pad">
      <div>
        <p class="eyebrow">Historical token</p>
        <h3>${esc(symbol)} <span>${esc(HIST.scenario || "")} | cap ${esc(HIST.cap || "")}</span></h3>
      </div>
      <div class="summary-strip">
        <span class="pill">Best ${esc(best?.best_strategy || "-")}</span>
        <span class="pill">${fmt.format(trades.length)} trades</span>
      </div>
    </div>

    <div class="grid cols-4 section">
      ${kpi("PnL liquido", signed(totals.net, true), `bruto ${signed(totals.gross, true)}`, cls(totals.net))}
      ${kpi("Custos", usd.format(totals.costs), "modelo historico")}
      ${kpi("Win rate", pct(winRate), `${totals.wins} wins / ${totals.losses} losses`)}
      ${kpi("Melhor estrategia", best ? signed(best.net_pnl, true) : "$0.00", best?.best_strategy || "sem dados", best ? cls(best.net_pnl) : "")}
    </div>

    <section class="section split">
      <div class="card pad"><div class="toolbar"><div><h3>Equity historica</h3><p>Trades fechados do token.</p></div></div>${lineChart(curve)}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Top estrategias</h3><p>Ranking isolado deste token.</p></div></div>${histSummaryTable(pairRows.slice(0, 20))}</div>
    </section>

    <section class="section">
      <div class="toolbar"><div><h3>Todas as estrategias do token</h3><p>Clique na estrategia para ver trades e curva.</p></div></div>
      ${histSummaryTable(pairRows)}
    </section>

    <section class="section">
      <div class="toolbar"><div><h3>Trades recentes do backtest</h3><p>Simulados em candle 5m/15m, nao em book WS.</p></div></div>
      ${histTradesTable(trades.slice(-120).reverse())}
    </section>
  `;
  bindRoutes();
}

function renderHistoricalStrategy(symbol, strategy) {
  setTitle(`${symbol} / ${strategy}`, "Historico da estrategia");
  renderHistoricalNav(symbol);
  const key = `${symbol}|${strategy}`;
  const trades = (HIST.trades || []).filter((row) => row.symbol === symbol && row.strategy === strategy);
  const metric = (HIST.pairStrategy || []).find((row) => row.symbol === symbol && row.strategy === strategy) || {};
  const curve = (HIST.equityByPairStrategy || {})[key] || [];
  const totals = histTotals(trades);
  const liveAccount = (WS.accounts || []).find((row) => row.symbol === symbol && row.strategy === strategy);
  const livePnl = liveAccount ? Number(liveAccount.equity || 0) - Number(liveAccount.start_equity || 0) : 0;

  document.getElementById("content").innerHTML = `
    <div class="ws-hero card pad">
      <div>
        <p class="eyebrow">Historical strategy</p>
        <h3>${esc(strategy)} <span>${esc(symbol)} | candle backtest</span></h3>
      </div>
      <div class="summary-strip">
        <span class="pill">Hist ${signed(totals.net, true)}</span>
        <span class="pill">WS ${liveAccount ? signed(livePnl, true) : "sem match"}</span>
      </div>
    </div>

    <div class="grid cols-4 section">
      ${kpi("PnL historico", signed(totals.net, true), `${fmt.format(trades.length)} trades`, cls(totals.net))}
      ${kpi("PF / Win", `${fmt.format(metric.profit_factor || 0)} / ${fmt.format(metric.win_rate || 0)}%`, `DD ${usd.format(metric.max_drawdown || 0)}`)}
      ${kpi("Custos", usd.format(totals.costs), `avg ${signed(metric.avg_trade || 0, true)}`)}
      ${kpi("WS live match", liveAccount ? signed(livePnl, true) : "sem conta", liveAccount ? `${liveAccount.closed_trades || 0} fechadas` : "nome nao bate", liveAccount ? cls(livePnl) : "neutral")}
    </div>

    <section class="section split">
      <div class="card pad"><div class="toolbar"><div><h3>Curva historica</h3><p>Somente trades fechados no backtest.</p></div></div>${lineChart(curve)}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Resumo historico</h3><p>Metricas da estrategia isolada.</p></div></div>${histSummaryTable(metric.symbol ? [metric] : [])}</div>
    </section>

    <section class="section">
      <div class="toolbar"><div><h3>Trades da estrategia</h3><p>Entradas, saidas e motivo do backtest.</p></div></div>
      ${histTradesTable(trades)}
    </section>
  `;
  bindRoutes();
}

function wsAccountsTable(rows) {
  return table(
    [
      { label: "Token", key: "symbol", render: (x) => `<a class="pill" href="${wsTokenRoute(x.symbol, exitProfile(x))}" data-route="${wsTokenRoute(x.symbol, exitProfile(x))}">${esc(x.symbol)}</a>` },
      { label: "Estrategia", key: "strategy", render: (x) => `<a class="pill" href="/ws-live/token/${encodeURIComponent(x.symbol)}/strategy/${encodeURIComponent(x.strategy)}" data-route="/ws-live/token/${encodeURIComponent(x.symbol)}/strategy/${encodeURIComponent(x.strategy)}">${esc(x.strategy)}</a>` },
      { label: "Base", key: "variant_base" },
      { label: "Var", key: "variant_profile" },
      { label: "Exit", key: "exit_profile" },
      { label: "Equity", key: "equity", render: (x) => `<span class="${cls(Number(x.equity || 0) - Number(x.start_equity || 0))}">${usd.format(x.equity || 0)}</span>` },
      { label: "Real", key: "realized_pnl", render: (x) => `<span class="${cls(x.realized_pnl)}">${signed(x.realized_pnl, true)}</span>` },
      { label: "Aberto", key: "open_pnl", render: (x) => `<span class="${cls(x.open_pnl)}">${signed(x.open_pnl, true)}</span>` },
      { label: "Fechadas", key: "closed_trades" },
      { label: "Abertas", key: "open_positions" },
      { label: "Hold", key: "avg_hold_minutes", render: (x) => `${fmt.format(x.avg_hold_minutes || 0)}m` },
      { label: "Time", key: "time_exits" },
      { label: "Safety", key: "safety_exits" },
      { label: "Win", key: "win_rate", render: (x) => `${fmt.format(x.win_rate || 0)}%` },
      { label: "PF", key: "profit_factor", render: (x) => fmt.format(x.profit_factor || 0) },
    ],
    rows
  );
}

function wsBookTable(rows) {
  const tickersBySymbol = Object.fromEntries((WS.tickers || []).map((row) => [row.symbol, row]));
  return table(
    [
      { label: "Token", key: "symbol" },
      { label: "Bid", key: "bid" },
      { label: "Ask", key: "ask" },
      { label: "Mid", key: "mid" },
      { label: "Spread", key: "spread_bps", render: (x) => `${fmt.format(x.spread_bps || 0)} bps` },
      { label: "Funding", key: "funding_rate", render: (x) => pct((tickersBySymbol[x.symbol] || {}).funding_rate || 0) },
      { label: "Premium", key: "premium", render: (x) => pct((tickersBySymbol[x.symbol] || {}).premium || 0) },
      { label: "Bid depth", key: "bid_depth" },
      { label: "Ask depth", key: "ask_depth" },
      { label: "Time", key: "time" },
    ],
    rows
  );
}

function wsOpenTable(rows) {
  return table(
    [
      { label: "Trade", key: "trade_id" },
      { label: "Token", key: "symbol" },
      { label: "Estrategia", key: "strategy" },
      { label: "Exit", key: "exit_profile" },
      { label: "Side", key: "side" },
      { label: "Entry", key: "entry_price" },
      { label: "Mark", key: "mark_price" },
      { label: "PnL", key: "open_pnl", render: (x) => `<span class="${cls(x.open_pnl)}">${signed(x.open_pnl, true)}</span>` },
      { label: "Stop", key: "stop_price" },
      { label: "Take", key: "tp_price" },
      { label: "Spread", key: "spread_bps_now", render: (x) => `${fmt.format(x.spread_bps_now || 0)} bps` },
      { label: "Entrada", key: "entry_time" },
    ],
    rows
  );
}

function wsClosedTable(rows) {
  return table(
    [
      { label: "Trade", key: "trade_id" },
      { label: "Token", key: "symbol" },
      { label: "Estrategia", key: "strategy" },
      { label: "Side", key: "side" },
      { label: "Entry", key: "entry_price" },
      { label: "Exit", key: "exit_price" },
      { label: "PnL", key: "net_pnl", render: (x) => `<span class="${cls(x.net_pnl)}">${signed(x.net_pnl, true)}</span>` },
      { label: "Hold", key: "hold_minutes", render: (x) => `${fmt.format(x.hold_minutes || 0)}m` },
      { label: "Motivo", key: "exit_reason" },
      { label: "Saida", key: "exit_time" },
    ],
    rows
  );
}

function wsSignalsTable(rows) {
  return table(
    [
      { label: "Time", key: "time" },
      { label: "Token", key: "symbol" },
      { label: "Estrategia", key: "strategy" },
      { label: "Side", key: "side" },
      { label: "Status", key: "status" },
      { label: "Spread", key: "spread_bps", render: (x) => `${fmt.format(x.spread_bps || 0)} bps` },
      { label: "Bloqueio", key: "blocked_reason" },
    ],
    rows
  );
}

function wsPublicTradesTable(rows) {
  return table(
    [
      { label: "Time", key: "time" },
      { label: "Token", key: "symbol" },
      { label: "Price", key: "price" },
      { label: "Size", key: "size" },
      { label: "Side", key: "side" },
    ],
    rows
  );
}

function liveSymbols() {
  const set = new Set([...(LIVE.symbols || [])]);
  for (const row of LIVE.summary || []) set.add(row.symbol);
  for (const row of LIVE.open || []) set.add(row.symbol);
  for (const row of LIVE.closed || []) set.add(row.symbol);
  return [...set].filter(Boolean).sort();
}

function renderPaperLiveNav(activeToken) {
  setRunControlsVisible(false);
  const nav = document.getElementById("tokenNav");
  const openBySymbol = {};
  for (const row of LIVE.open || []) openBySymbol[row.symbol] = (openBySymbol[row.symbol] || 0) + 1;
  nav.innerHTML = [
    `<a class="token-link" href="/" data-route="/"><span>Backtest</span><small>lab</small></a>`,
    `<a class="token-link ${activeToken ? "" : "active"}" href="/paper-live" data-route="/paper-live"><span>Paper Live</span><small>${esc((LIVE.open || []).length || 0)} open</small></a>`,
    ...liveSymbols().map((symbol) => `<a class="token-link ${activeToken === symbol ? "active" : ""}" href="/paper-live/token/${encodeURIComponent(symbol)}" data-route="/paper-live/token/${encodeURIComponent(symbol)}"><span>${esc(symbol)}</span><small>${openBySymbol[symbol] || 0} open</small></a>`),
  ].join("");
  nav.querySelectorAll("a[data-route]").forEach((a) => {
    a.addEventListener("click", (event) => {
      event.preventDefault();
      go(a.getAttribute("data-route"));
    });
  });
}

function liveTotals(rows) {
  return rows.reduce(
    (acc, row) => {
      acc.closed += Number(row.closed_trades || 0);
      acc.open += Number(row.open_positions || 0);
      acc.closedPnl += Number(row.closed_net_pnl || 0);
      acc.openPnl += Number(row.open_net_pnl || 0);
      acc.targetPnl += Number(row.target_net_pnl || 0);
      acc.stopPnl += Number(row.stop_net_pnl || 0);
      acc.totalPnl += Number(row.total_net_pnl || 0);
      return acc;
    },
    { closed: 0, open: 0, closedPnl: 0, openPnl: 0, targetPnl: 0, stopPnl: 0, totalPnl: 0 }
  );
}

function rankRows(rows, key, direction = "desc") {
  const sorted = (rows || [])
    .filter((row) => Number(row[key] || 0) !== 0 || Number(row.open_positions || 0) || Number(row.closed_trades || 0))
    .slice()
    .sort((a, b) => Number(b[key] || 0) - Number(a[key] || 0));
  return direction === "asc" ? sorted.reverse().slice(0, 10) : sorted.slice(0, 10);
}

function variantRows(symbol = null) {
  return (LIVE.variantMatrix || []).filter((row) => !symbol || row.symbol === symbol);
}

function renderPaperLive() {
  const summary = LIVE.summary || [];
  const totals = liveTotals(summary);
  const blocked = (LIVE.signals || []).filter((x) => x.status === "blocked_by_spread").length;
  const realizedTop = rankRows(summary.filter((x) => Number(x.closed_trades || 0) > 0), "closed_net_pnl");
  const openTop = rankRows(summary.filter((x) => Number(x.open_positions || 0) > 0), "open_net_pnl");
  const targetTop = rankRows(summary.filter((x) => Number(x.open_positions || 0) > 0), "target_net_pnl");
  setTitle("Paper Live", "Monitor com bid/ask real");
  renderPaperLiveNav(null);
  document.getElementById("content").innerHTML = `
    <div class="grid cols-4">
      ${kpi("Status", esc(LIVE.health || LIVE.status || "aguardando"), `ultimo ciclo ${LIVE.lastCycleAt || "sem ciclo"} | ${LIVE.lastCycleAgeSeconds || 0}s`, LIVE.health === "LIVE" ? "positive" : LIVE.health === "STALE" ? "warn" : "negative")}
      ${kpi("Abertas", fmt.format(totals.open), `${fmt.format(totals.closed)} fechadas`)}
      ${kpi("Realizado", signed(totals.closedPnl, true), `aberto ${signed(totals.openPnl, true)}`, cls(totals.closedPnl))}
      ${kpi("Alvo / Risco", `${signed(totals.targetPnl, true)} / ${signed(totals.stopPnl, true)}`, `${blocked} sinais bloqueados`, cls(totals.targetPnl + totals.stopPnl))}
    </div>
    <section class="section grid cols-3">
      <div class="card pad"><div class="toolbar"><div><h3>Ranking realizado</h3><p>Somente trades fechados.</p></div></div>${liveMiniRank(realizedTop, "closed_net_pnl")}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Ranking aberto</h3><p>Mark-to-market atual.</p></div></div>${liveMiniRank(openTop, "open_net_pnl")}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Ranking alvo</h3><p>PnL se bater take nas abertas.</p></div></div>${liveMiniRank(targetTop, "target_net_pnl")}</div>
    </section>
    <section class="section">
      <div class="toolbar"><div><h3>Resumo por token + estrategia</h3><p>Fechado, aberto, alvo e risco separados.</p></div></div>
      ${table(
        [
          { label: "Token", key: "symbol", render: (x) => `<a class="pill" href="/paper-live/token/${encodeURIComponent(x.symbol)}" data-route="/paper-live/token/${encodeURIComponent(x.symbol)}">${esc(x.symbol)}</a>` },
          { label: "Estrategia", key: "strategy" },
          { label: "Base", key: "variant_base" },
          { label: "Var", key: "variant_profile" },
          { label: "Fechadas", key: "closed_trades" },
          { label: "Abertas", key: "open_positions" },
          { label: "Fechado", key: "closed_net_pnl", render: (x) => `<span class="${cls(x.closed_net_pnl)}">${signed(x.closed_net_pnl, true)}</span>` },
          { label: "Aberto", key: "open_net_pnl", render: (x) => `<span class="${cls(x.open_net_pnl)}">${signed(x.open_net_pnl, true)}</span>` },
          { label: "Alvo", key: "target_net_pnl", render: (x) => `<span class="${cls(x.target_net_pnl)}">${signed(x.target_net_pnl, true)}</span>` },
          { label: "Risco", key: "stop_net_pnl", render: (x) => `<span class="${cls(x.stop_net_pnl)}">${signed(x.stop_net_pnl, true)}</span>` },
          { label: "PF", key: "profit_factor" },
        ],
        summary
      )}
    </section>
    <section class="section">
      <div class="toolbar"><div><h3>Matriz de variantes</h3><p>Comparacao por token, estrategia base e perfil de stop/take.</p></div></div>
      ${variantMatrixTable(variantRows())}
    </section>
    <section class="section grid cols-2">
      <div class="card pad"><div class="toolbar"><div><h3>Posicoes abertas</h3><p>Entry, mark, stop e take pelo bid/ask.</p></div></div>${liveOpenTable(LIVE.open || [])}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Ultimos fechamentos</h3><p>Entrada e saida simuladas pelo preco executavel.</p></div></div>${liveClosedTable(LIVE.closed || [])}</div>
    </section>
    <section class="section">
      <div class="toolbar"><div><h3>Ultimo book</h3><p>Bid/ask e spread por token.</p></div></div>
      ${liveBookTable(Object.values(LIVE.books || {}))}
    </section>
  `;
  document.querySelectorAll("a[data-route]").forEach((a) => {
    a.addEventListener("click", (event) => {
      event.preventDefault();
      go(a.getAttribute("data-route"));
    });
  });
}

function renderPaperLiveToken(symbol) {
  const rows = (LIVE.summary || []).filter((x) => x.symbol === symbol);
  const totals = liveTotals(rows);
  const open = (LIVE.open || []).filter((x) => x.symbol === symbol);
  const closed = (LIVE.closed || []).filter((x) => x.symbol === symbol);
  const signals = (LIVE.signals || []).filter((x) => x.symbol === symbol);
  const book = (LIVE.books || {})[symbol] || {};
  const equity = ((LIVE.equityBySymbol || {})[symbol] || []);
  const bookHistory = ((LIVE.bookHistory || {})[symbol] || []);
  const blocked = signals.filter((x) => x.status === "blocked_by_spread");
  setTitle(`Paper ${symbol}`, "Precos reais do book");
  renderPaperLiveNav(symbol);
  document.getElementById("content").innerHTML = `
    <div class="grid cols-4">
      ${kpi("Bid / Ask", `${fmt.format(book.bid || 0)} / ${fmt.format(book.ask || 0)}`, `spread ${fmt.format(book.spread_bps || 0)} bps`)}
      ${kpi("Abertas", fmt.format(open.length), `${closed.length} fechadas`)}
      ${kpi("Realizado", signed(totals.closedPnl, true), `aberto ${signed(totals.openPnl, true)}`, cls(totals.closedPnl))}
      ${kpi("Alvo / Risco", `${signed(totals.targetPnl, true)} / ${signed(totals.stopPnl, true)}`, "abertas", cls(totals.targetPnl + totals.stopPnl))}
      ${kpi("Sinais", fmt.format(signals.length), `${blocked.length} bloqueados spread`)}
    </div>
    <section class="section split">
      <div class="card pad"><div class="toolbar"><div><h3>Equity live fechada</h3><p>${esc(symbol)}</p></div></div>${lineChart(equity)}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Posicoes abertas</h3><p>Mark bid/ask real.</p></div></div>${liveOpenTable(open)}</div>
    </section>
    <section class="section grid cols-2">
      <div class="card pad"><div class="toolbar"><div><h3>Historico de preco</h3><p>Mid do book nos ciclos coletados.</p></div></div>${bookMetricChart(bookHistory, "mid", "preco")}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Historico de spread</h3><p>Spread em bps, essencial para scalping.</p></div></div>${bookMetricChart(bookHistory, "spread_bps", "spread")}</div>
    </section>
    <section class="section">
      <div class="toolbar"><div><h3>Estrategias live</h3><p>Todos os motores em paralelo para este token.</p></div></div>
      ${table(
        [
          { label: "Estrategia", key: "strategy", render: (x) => `<a class="pill" href="/paper-live/token/${encodeURIComponent(symbol)}/strategy/${encodeURIComponent(x.strategy)}" data-route="/paper-live/token/${encodeURIComponent(symbol)}/strategy/${encodeURIComponent(x.strategy)}">${esc(x.strategy)}</a>` },
          { label: "Base", key: "variant_base" },
          { label: "Var", key: "variant_profile" },
          { label: "Fechadas", key: "closed_trades" },
          { label: "Abertas", key: "open_positions" },
          { label: "Fechado", key: "closed_net_pnl", render: (x) => `<span class="${cls(x.closed_net_pnl)}">${signed(x.closed_net_pnl, true)}</span>` },
          { label: "Aberto", key: "open_net_pnl", render: (x) => `<span class="${cls(x.open_net_pnl)}">${signed(x.open_net_pnl, true)}</span>` },
          { label: "Alvo", key: "target_net_pnl", render: (x) => `<span class="${cls(x.target_net_pnl)}">${signed(x.target_net_pnl, true)}</span>` },
          { label: "Risco", key: "stop_net_pnl", render: (x) => `<span class="${cls(x.stop_net_pnl)}">${signed(x.stop_net_pnl, true)}</span>` },
          { label: "Win", key: "win_rate", render: (x) => `${fmt.format(x.win_rate || 0)}%` },
          { label: "PF", key: "profit_factor" },
        ],
        rows
      )}
    </section>
    <section class="section">
      <div class="toolbar"><div><h3>Matriz de variantes</h3><p>${esc(symbol)} por estrategia base e perfil.</p></div></div>
      ${variantMatrixTable(variantRows(symbol))}
    </section>
    <section class="section grid cols-2">
      <div class="card pad"><div class="toolbar"><div><h3>Trades fechados</h3><p>Saida pelo bid/ask real.</p></div></div>${liveClosedTable(closed.slice().reverse())}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Sinais recentes</h3><p>Inclui sinais bloqueados por spread.</p></div></div>${liveSignalsTable(signals.slice().reverse())}</div>
    </section>
  `;
  bindRoutes();
}

function liveMiniRank(rows, key = "total_net_pnl") {
  if (!rows.length) return `<div class="empty">Aguardando dados live.</div>`;
  return `<div class="bar-list">${rows
    .map((row) => `<div class="bar-row"><span>${esc(row.symbol)} / ${esc(row.strategy)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(5, Math.min(100, Math.abs(Number(row[key] || 0)) * 20))}%;background:${Number(row[key] || 0) >= 0 ? "#167a4a" : "#b63d32"}"></div></div><strong class="${cls(row[key])}">${signed(row[key], true)}</strong></div>`)
    .join("")}</div>`;
}

function variantMatrixTable(rows) {
  return table(
    [
      { label: "Token", key: "symbol", render: (x) => `<a class="pill" href="/paper-live/token/${encodeURIComponent(x.symbol)}" data-route="/paper-live/token/${encodeURIComponent(x.symbol)}">${esc(x.symbol)}</a>` },
      { label: "Base", key: "variant_base" },
      { label: "Variante", key: "variant_profile" },
      { label: "Stop %", key: "stop_pct", render: (x) => pct(x.stop_pct) },
      { label: "Take %", key: "tp_pct", render: (x) => pct(x.tp_pct) },
      { label: "Abertas", key: "open_positions" },
      { label: "Fechadas", key: "closed_trades" },
      { label: "Fechado", key: "closed_net_pnl", render: (x) => `<span class="${cls(x.closed_net_pnl)}">${signed(x.closed_net_pnl, true)}</span>` },
      { label: "Aberto", key: "open_net_pnl", render: (x) => `<span class="${cls(x.open_net_pnl)}">${signed(x.open_net_pnl, true)}</span>` },
      { label: "No Take", key: "target_net_pnl", render: (x) => `<span class="${cls(x.target_net_pnl)}">${signed(x.target_net_pnl, true)}</span>` },
      { label: "No Stop", key: "stop_net_pnl", render: (x) => `<span class="${cls(x.stop_net_pnl)}">${signed(x.stop_net_pnl, true)}</span>` },
      { label: "Win", key: "win_rate", render: (x) => `${fmt.format(x.win_rate || 0)}%` },
      { label: "PF", key: "profit_factor" },
    ],
    rows
  );
}

function renderPaperLiveStrategy(symbol, strategy) {
  const key = `${symbol}|${strategy}`;
  const summary = (LIVE.summary || []).find((x) => x.symbol === symbol && x.strategy === strategy) || {};
  const open = (LIVE.open || []).filter((x) => x.symbol === symbol && x.strategy === strategy);
  const closed = (LIVE.closed || []).filter((x) => x.symbol === symbol && x.strategy === strategy);
  const signals = (LIVE.signals || []).filter((x) => x.symbol === symbol && x.strategy === strategy);
  const trades = (LIVE.trades || []).filter((x) => x.symbol === symbol && x.strategy === strategy);
  const equity = ((LIVE.equityByStrategy || {})[key] || []);
  const allEvents = [];
  for (const trade of trades) {
    allEvents.push(...((LIVE.tradeEvents || {})[trade.trade_id] || []));
  }
  setTitle(`${symbol} / ${strategy}`, "Historico da estrategia");
  renderPaperLiveNav(symbol);
  document.getElementById("content").innerHTML = `
    <div class="grid cols-4">
      ${kpi("Fechado", signed(summary.closed_net_pnl || 0, true), `${summary.closed_trades || 0} fechadas`, cls(summary.closed_net_pnl))}
      ${kpi("PnL aberto", signed(summary.open_net_pnl || 0, true), "mark bid/ask", cls(summary.open_net_pnl))}
      ${kpi("Take / Stop", `${signed(summary.target_net_pnl || 0, true)} / ${signed(summary.stop_net_pnl || 0, true)}`, `${summary.open_positions || 0} abertas`, cls((summary.target_net_pnl || 0) + (summary.stop_net_pnl || 0)))}
      ${kpi("Win / PF", `${fmt.format(summary.win_rate || 0)}% / ${fmt.format(summary.profit_factor || 0)}`, `${summary.wins || 0} wins / ${summary.losses || 0} losses`)}
    </div>
    <section class="section split">
      <div class="card pad"><div class="toolbar"><div><h3>Equity da estrategia</h3><p>Somente trades fechados.</p></div></div>${lineChart(equity)}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Posicoes abertas</h3><p>Atualizacao por minuto quando o coletor esta live.</p></div></div>${liveOpenTable(open)}</div>
    </section>
    <section class="section">
      <div class="toolbar"><div><h3>Trades da estrategia</h3><p>Clique no trade ID para ver a linha do tempo completa.</p></div></div>
      ${liveTradesTable(trades)}
    </section>
    <section class="section grid cols-2">
      <div class="card pad"><div class="toolbar"><div><h3>Eventos recentes</h3><p>Marks, aberturas, fechamentos e erros.</p></div></div>${liveEventsTable(allEvents.slice().sort((a, b) => Number(b.event_time_ms || 0) - Number(a.event_time_ms || 0)).slice(0, 200))}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Sinais</h3><p>Inclui bloqueios por spread ou posicao ja aberta.</p></div></div>${liveSignalsTable(signals.slice().reverse())}</div>
    </section>
  `;
  bindRoutes();
}

function renderPaperLiveTrade(tradeId) {
  const trade = (LIVE.open || []).find((x) => x.trade_id === tradeId)
    || (LIVE.closed || []).find((x) => x.trade_id === tradeId)
    || (LIVE.trades || []).find((x) => x.trade_id === tradeId);
  const events = ((LIVE.tradeEvents || {})[tradeId] || []);
  if (!trade) {
    setTitle("Trade", tradeId);
    renderPaperLiveNav(null);
    document.getElementById("content").innerHTML = `<div class="card empty">Trade ID nao encontrado no snapshot atual.</div>`;
    return;
  }
  const pnl = trade.net_pnl ?? trade.net_unrealized_pnl ?? 0;
  setTitle(`Trade ${tradeId}`, `${trade.symbol} / ${trade.strategy}`);
  renderPaperLiveNav(trade.symbol);
  document.getElementById("content").innerHTML = `
    <div class="grid cols-4">
      ${kpi("Status", esc(trade.status || "open"), `${trade.side || ""}`, trade.status === "closed" ? "neutral" : "positive")}
      ${kpi("PnL", signed(pnl, true), `entry ${trade.entry_price || ""}`, cls(pnl))}
      ${kpi("Stop / TP", `${fmt.format(trade.stop_price || 0)} / ${fmt.format(trade.tp_price || 0)}`, `sem time exit`)}
      ${kpi("Stop % / Take %", `${pct(trade.stop_pct)} / ${pct(trade.tp_pct)}`, esc(trade.variant_profile || ""))}
      ${kpi("Take / Stop PnL", `${signed(trade.target_net_pnl || 0, true)} / ${signed(trade.stop_net_pnl || 0, true)}`, `${fmt.format(trade.distance_to_take_bps || 0)} / ${fmt.format(trade.distance_to_stop_bps || 0)} bps`, cls((trade.target_net_pnl || 0) + (trade.stop_net_pnl || 0)))}
    </div>
    <section class="section split">
      <div class="card pad"><div class="toolbar"><div><h3>PnL do trade</h3><p>Mark-to-market por evento.</p></div></div>${lineChart(events.map((x) => [x.event_time, Number(x.unrealized_pnl || x.realized_pnl || 0)]))}</div>
      <div class="card pad"><div class="toolbar"><div><h3>Resumo</h3><p>Precos executaveis usados para simular fill.</p></div></div>
        <div class="summary-strip">
          <span class="pill">Entry ${esc(trade.entry_price || "")}</span>
          <span class="pill">Exit ${esc(trade.exit_price || trade.mark_price || "")}</span>
          <span class="pill">Reason ${esc(trade.exit_reason || trade.reason || "")}</span>
        </div>
      </div>
    </section>
    <section class="section">
      <div class="toolbar"><div><h3>Linha do tempo</h3><p>Cada mark/fechamento gravado no SQLite.</p></div></div>
      ${liveEventsTable(events)}
    </section>
  `;
}

function bindRoutes() {
  document.querySelectorAll("a[data-route]").forEach((a) => {
    a.addEventListener("click", (event) => {
      event.preventDefault();
      go(a.getAttribute("data-route"));
    });
  });
}

function liveTradesTable(rows) {
  return table(
    [
      { label: "Trade ID", key: "trade_id", render: (x) => `<a class="pill" href="/paper-live/trade/${encodeURIComponent(x.trade_id)}" data-route="/paper-live/trade/${encodeURIComponent(x.trade_id)}">${esc(x.trade_id)}</a>` },
      { label: "Token", key: "symbol" },
      { label: "Status", key: "status" },
      { label: "Side", key: "side" },
      { label: "Entry", key: "entry_price" },
      { label: "Exit/Mark", key: "exit_price", render: (x) => esc(x.exit_price || x.mark_price || "") },
      { label: "Stop", key: "stop_price" },
      { label: "Take", key: "tp_price" },
      { label: "Stop %", key: "stop_pct", render: (x) => pct(x.stop_pct) },
      { label: "Take %", key: "tp_pct", render: (x) => pct(x.tp_pct) },
      { label: "PnL Take", key: "target_net_pnl", render: (x) => `<span class="${cls(x.target_net_pnl)}">${signed(x.target_net_pnl || 0, true)}</span>` },
      { label: "PnL Stop", key: "stop_net_pnl", render: (x) => `<span class="${cls(x.stop_net_pnl)}">${signed(x.stop_net_pnl || 0, true)}</span>` },
      { label: "PnL", key: "net_pnl", render: (x) => `<span class="${cls(x.net_pnl ?? x.net_unrealized_pnl)}">${signed(x.net_pnl ?? x.net_unrealized_pnl ?? 0, true)}</span>` },
      { label: "Entrada", key: "entry_time" },
      { label: "Saida", key: "exit_time" },
      { label: "Motivo", key: "exit_reason", render: (x) => esc(x.exit_reason || x.reason || "") },
    ],
    rows
  );
}

function liveEventsTable(rows) {
  return table(
    [
      { label: "Time", key: "event_time" },
      { label: "Evento", key: "event_type" },
      { label: "Bid", key: "bid" },
      { label: "Ask", key: "ask" },
      { label: "Mark", key: "mark_price" },
      { label: "Spread", key: "spread_bps", render: (x) => `${fmt.format(x.spread_bps || 0)} bps` },
      { label: "Unreal", key: "unrealized_pnl", render: (x) => `<span class="${cls(x.unrealized_pnl)}">${signed(x.unrealized_pnl || 0, true)}</span>` },
      { label: "Real", key: "realized_pnl", render: (x) => `<span class="${cls(x.realized_pnl)}">${signed(x.realized_pnl || 0, true)}</span>` },
      { label: "Razao", key: "reason" },
    ],
    rows
  );
}

function liveOpenTable(rows) {
  return table(
    [
      { label: "Trade", key: "trade_id", render: (x) => `<a class="pill" href="/paper-live/trade/${encodeURIComponent(x.trade_id)}" data-route="/paper-live/trade/${encodeURIComponent(x.trade_id)}">${esc(x.trade_id)}</a>` },
      { label: "Token", key: "symbol" },
      { label: "Estrategia", key: "strategy" },
      { label: "Side", key: "side" },
      { label: "Entry", key: "entry_price" },
      { label: "Mark", key: "mark_price" },
      { label: "Stop", key: "stop_price" },
      { label: "Take", key: "tp_price" },
      { label: "Stop %", key: "stop_pct", render: (x) => pct(x.stop_pct) },
      { label: "Take %", key: "tp_pct", render: (x) => pct(x.tp_pct) },
      { label: "PnL aberto", key: "net_unrealized_pnl", render: (x) => `<span class="${cls(x.net_unrealized_pnl)}">${signed(x.net_unrealized_pnl, true)}</span>` },
      { label: "PnL Take", key: "target_net_pnl", render: (x) => `<span class="${cls(x.target_net_pnl)}">${signed(x.target_net_pnl || 0, true)}</span>` },
      { label: "PnL Stop", key: "stop_net_pnl", render: (x) => `<span class="${cls(x.stop_net_pnl)}">${signed(x.stop_net_pnl || 0, true)}</span>` },
      { label: "Dist Take", key: "distance_to_take_bps", render: (x) => `${fmt.format(x.distance_to_take_bps || 0)} bps` },
      { label: "Dist Stop", key: "distance_to_stop_bps", render: (x) => `${fmt.format(x.distance_to_stop_bps || 0)} bps` },
      { label: "Spread", key: "spread_bps_now", render: (x) => `${fmt.format(x.spread_bps_now || 0)} bps` },
      { label: "Entrada", key: "entry_time" },
    ],
    rows
  );
}

function liveClosedTable(rows) {
  return table(
    [
      { label: "Trade", key: "trade_id", render: (x) => `<a class="pill" href="/paper-live/trade/${encodeURIComponent(x.trade_id)}" data-route="/paper-live/trade/${encodeURIComponent(x.trade_id)}">${esc(x.trade_id)}</a>` },
      { label: "Token", key: "symbol" },
      { label: "Estrategia", key: "strategy" },
      { label: "Side", key: "side" },
      { label: "Entry", key: "entry_price" },
      { label: "Exit", key: "exit_price" },
      { label: "Stop", key: "stop_price" },
      { label: "Take", key: "tp_price" },
      { label: "Stop %", key: "stop_pct", render: (x) => pct(x.stop_pct) },
      { label: "Take %", key: "tp_pct", render: (x) => pct(x.tp_pct) },
      { label: "PnL", key: "net_pnl", render: (x) => `<span class="${cls(x.net_pnl)}">${signed(x.net_pnl, true)}</span>` },
      { label: "Hold", key: "hold_minutes", render: (x) => `${fmt.format(x.hold_minutes || 0)}m` },
      { label: "Motivo", key: "exit_reason" },
      { label: "Entrada", key: "entry_time" },
      { label: "Saida", key: "exit_time" },
    ],
    rows
  );
}

function liveSignalsTable(rows) {
  return table(
    [
      { label: "Time", key: "time" },
      { label: "Estrategia", key: "strategy" },
      { label: "Side", key: "side" },
      { label: "Status", key: "status" },
      { label: "Spread", key: "spread_bps", render: (x) => `${fmt.format(x.spread_bps || 0)} bps` },
      { label: "Razao", key: "reason" },
    ],
    rows
  );
}

function liveBookTable(rows) {
  return table(
    [
      { label: "Token", key: "symbol", render: (x) => `<a class="pill" href="/paper-live/token/${encodeURIComponent(x.symbol)}" data-route="/paper-live/token/${encodeURIComponent(x.symbol)}">${esc(x.symbol)}</a>` },
      { label: "Bid", key: "bid" },
      { label: "Ask", key: "ask" },
      { label: "Mid", key: "mid" },
      { label: "Spread", key: "spread_bps", render: (x) => `${fmt.format(x.spread_bps || 0)} bps` },
      { label: "Mark", key: "mark_price" },
      { label: "Funding", key: "funding_rate", render: (x) => pct(x.funding_rate || 0) },
      { label: "Premium", key: "premium", render: (x) => pct(x.premium || 0) },
    ],
    rows
  );
}

function bookMetricChart(rows, key, label) {
  const points = (rows || [])
    .map((row) => [row.time, Number(row[key] || 0)])
    .filter((point) => Number.isFinite(point[1]) && point[1] > 0);
  if (!points.length) return `<div class="empty">Aguardando historico de ${esc(label)}.</div>`;
  const width = 760;
  const height = 250;
  const pad = 28;
  const ys = points.map((p) => p[1]);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const span = maxY - minY || 1;
  const step = points.length > 1 ? (width - pad * 2) / (points.length - 1) : 0;
  const d = points
    .map((p, idx) => {
      const x = pad + idx * step;
      const y = height - pad - ((p[1] - minY) / span) * (height - pad * 2);
      return `${idx ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
  const last = ys[ys.length - 1];
  return `
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)} history">
      <rect x="0" y="0" width="${width}" height="${height}" fill="#fff"></rect>
      <path d="${d}" fill="none" stroke="${key === "spread_bps" ? "#a26516" : "#275f9f"}" stroke-width="3" stroke-linecap="round"></path>
      <text x="${pad}" y="18" fill="#66736f" font-size="12">last ${fmt.format(last)}</text>
      <text x="${pad}" y="${height - 8}" fill="#66736f" font-size="12">min ${fmt.format(minY)} / max ${fmt.format(maxY)}</text>
    </svg>
  `;
}

function lineChart(points) {
  if (!points.length) return `<div class="empty">Sem trades para plotar.</div>`;
  const width = 760;
  const height = 250;
  const pad = 28;
  const ys = points.map((p) => Number(p[1]));
  const minY = Math.min(...ys, 0);
  const maxY = Math.max(...ys, 0);
  const span = maxY - minY || 1;
  const step = points.length > 1 ? (width - pad * 2) / (points.length - 1) : 0;
  const xy = points.map((p, idx) => {
    const x = pad + idx * step;
    const y = height - pad - ((Number(p[1]) - minY) / span) * (height - pad * 2);
    return [x, y];
  });
  const d = xy.map((p, idx) => `${idx ? "L" : "M"}${p[0].toFixed(2)} ${p[1].toFixed(2)}`).join(" ");
  const zeroY = height - pad - ((0 - minY) / span) * (height - pad * 2);
  const color = ys[ys.length - 1] >= 0 ? "#167a4a" : "#b63d32";
  return `
    <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Equity curve">
      <rect x="0" y="0" width="${width}" height="${height}" fill="#fff"></rect>
      <line x1="${pad}" x2="${width - pad}" y1="${zeroY}" y2="${zeroY}" stroke="#dce3e0"></line>
      <path d="${d}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round"></path>
      <text x="${pad}" y="18" fill="#66736f" font-size="12">max ${signed(maxY, true)}</text>
      <text x="${pad}" y="${height - 8}" fill="#66736f" font-size="12">min ${signed(minY, true)}</text>
    </svg>
  `;
}

function barChart(points) {
  if (!points.length) return `<div class="empty">Sem dias ativos.</div>`;
  const maxAbs = Math.max(...points.map((p) => Math.abs(Number(p[1]))), 1);
  return `<div class="bar-list">${points
    .map(([day, pnl]) => {
      const n = Number(pnl);
      const w = Math.max(4, Math.abs(n) / maxAbs * 100);
      return `<div class="bar-row"><span>${esc(day)}</span><div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${n >= 0 ? "#167a4a" : "#b63d32"}"></div></div><strong class="${cls(n)}">${signed(n, true)}</strong></div>`;
    })
    .join("")}</div>`;
}

function barList(label, values) {
  const entries = Object.entries(values || {}).filter(([, v]) => Number(v) > 0);
  if (!entries.length) return `<div class="muted">Sem ${esc(label)}.</div>`;
  const max = Math.max(...entries.map(([, v]) => Number(v)), 1);
  return entries
    .map(([name, value]) => {
      const w = Math.max(5, Number(value) / max * 100);
      return `<div class="bar-row"><span title="${esc(name)}">${esc(name)}</span><div class="bar-track"><div class="bar-fill" style="width:${w}%"></div></div><strong>${esc(value)}</strong></div>`;
    })
    .join("");
}

function render() {
  if (isHistorical()) {
    const histStrategy = pathHistoricalStrategy();
    if (histStrategy) {
      renderHistoricalStrategy(histStrategy.symbol, histStrategy.strategy);
      return;
    }
    const histToken = pathHistoricalToken();
    if (histToken) {
      renderHistoricalToken(histToken);
      return;
    }
    renderHistoricalOverview();
    return;
  }

  if (isWSLive()) {
    const wsStrategy = pathWSStrategy();
    if (wsStrategy) {
      renderWSTokenStrategy(wsStrategy.symbol, wsStrategy.strategy);
      return;
    }
    const wsTokenExit = pathWSTokenExit();
    if (wsTokenExit) {
      renderWSToken(wsTokenExit.symbol, wsTokenExit.exitProfile);
      return;
    }
    const wsToken = pathWSToken();
    if (wsToken) {
      renderWSToken(wsToken);
      return;
    }
    renderWSLive();
    return;
  }

  if (isPaperLive()) {
    const tradeId = pathPaperTrade();
    if (tradeId) {
      renderPaperLiveTrade(tradeId);
      return;
    }
    const liveStrategy = pathPaperStrategy();
    if (liveStrategy) {
      renderPaperLiveStrategy(liveStrategy.symbol, liveStrategy.strategy);
      return;
    }
    const liveToken = pathPaperToken();
    if (liveToken) {
      renderPaperLiveToken(liveToken);
    } else {
      renderPaperLive();
    }
    return;
  }

  const token = pathToken();
  if (!state.runId || !DATA.runs[state.runId]) {
    state.runId = Object.keys(DATA.runs || {})[0];
  }
  const runSelect = document.getElementById("runSelect");
  if (runSelect && runSelect.value !== state.runId) runSelect.value = state.runId;

  if (token) {
    renderToken(token);
  } else {
    renderOverview();
  }
}

initShell();
Promise.all([loadLiveData(), loadWSData(), loadHistoricalData()]).then(render);
setInterval(async () => {
  if (isPaperLive() || isWSLive() || isHistorical()) {
    if (isPaperLive()) await loadLiveData();
    if (isWSLive()) await loadWSData();
    if (isHistorical()) {
      await loadHistoricalData();
      await loadWSData();
    }
    render();
  }
}, 15000);
