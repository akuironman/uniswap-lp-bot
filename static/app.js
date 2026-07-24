// LP Hunter frontend — WebSocket driven realtime dashboard
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const state = {
  ws: null,
  reconnectTimer: null,
  feedCount: 0,
  snapshot: null,
};

// ---- Helpers ----
const fmt = {
  usd: (n) => {
    if (n == null || isNaN(n)) return "$0";
    const abs = Math.abs(n);
    if (abs >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
    if (abs >= 1e3) return "$" + (n / 1e3).toFixed(1) + "K";
    return "$" + n.toFixed(2);
  },
  pct: (n) => (n == null || isNaN(n) ? "0.00%" : (n >= 0 ? "+" : "") + n.toFixed(2) + "%"),
  time: (ts) => {
    if (!ts) return "never";
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-GB");
  },
  agoMin: (ts) => {
    if (!ts) return "-";
    const s = Math.floor(Date.now() / 1000 - ts);
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    return Math.floor(s / 3600) + "h ago";
  },
};

// ---- Connection ----
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${location.host}/ws`;
  const ws = new WebSocket(url);
  state.ws = ws;

  ws.onopen = () => {
    setConn(true, "live");
    fetchState();
  };
  ws.onclose = () => {
    setConn(false, "disconnected");
    if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
    state.reconnectTimer = setTimeout(connect, 2500);
  };
  ws.onerror = () => setConn(false, "error");
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      handleMessage(msg);
    } catch (e) { console.error(e); }
  };
}

function setConn(on, label) {
  const dot = $("#connDot");
  dot.classList.toggle("on", on);
  dot.classList.toggle("off", !on);
  $("#connText").textContent = label;
}

function handleMessage(msg) {
  if (msg.kind === "snapshot") {
    applySnapshot(msg);
    return;
  }
  if (msg.kind === "ping") return;
  addFeed(msg);
  // Refresh state on key events
  if (["scan.done", "position.open", "position.close", "position.rebalance", "position.tp", "position.sl"].includes(msg.kind)) {
    fetchState();
  }
}

// ---- API ----
async function fetchState() {
  try {
    const r = await fetch("/api/state");
    const s = await r.json();
    applySnapshot(s);
  } catch (e) { console.warn("fetchState fail", e); }
}

async function apiScan() {
  $("#btnScan").disabled = true;
  $("#btnScan").textContent = "⏳ SCANNING…";
  try {
    const r = await fetch("/api/scan", { method: "POST" });
    const j = await r.json();
    addFeed({ kind: "scan.manual", count: j.count, top: j.candidates.slice(0, 3) });
    await fetchState();
  } catch (e) {
    addFeed({ kind: "error", msg: String(e) });
  } finally {
    $("#btnScan").disabled = false;
    $("#btnScan").textContent = "⚡ SCAN NOW";
  }
}

async function apiStart() {
  const dry = $("#dryRun").checked;
  const r = await fetch(`/api/start?dry_run=${dry}`, { method: "POST" });
  const j = await r.json();
  $("#btnStart").disabled = true;
  $("#btnStop").disabled = false;
  addFeed({ kind: "bot.start", dry_run: dry });
}

async function apiStop() {
  await fetch("/api/stop", { method: "POST" });
  $("#btnStart").disabled = false;
  $("#btnStop").disabled = true;
  addFeed({ kind: "bot.stop" });
}

// ---- Rendering ----
function applySnapshot(s) {
  state.snapshot = s;
  // KPIs
  const pnl = s.pnl_usd ?? 0;
  $("#kpiPnl").textContent = fmt.usd(pnl);
  $("#kpiPnl").style.color = pnl >= 0 ? "var(--gold)" : "var(--danger)";
  const pnlPct = s.total_deployed_usd ? (pnl / s.total_deployed_usd) * 100 : 0;
  $("#kpiPnlPct").textContent = fmt.pct(pnlPct);
  $("#kpiPnlPct").className = "kpi-sub " + (pnl >= 0 ? "pos" : "neg");

  $("#kpiDeployed").textContent = fmt.usd(s.total_deployed_usd ?? 0);
  $("#kpiPositions").textContent = (s.positions?.length ?? 0) + " positions";
  $("#kpiScans").textContent = s.stats?.scans ?? 0;
  $("#kpiLastScan").textContent = fmt.agoMin(s.last_scan_ts);
  $("#kpiCandidates").textContent = s.candidates?.length ?? 0;
  $("#kpiRebalance").textContent = s.stats?.rebalances ?? 0;
  $("#kpiClose").textContent = (s.stats?.positions_closed ?? 0) + " closed";

  // Controls state
  if (typeof s.running === "boolean") {
    $("#btnStart").disabled = s.running;
    $("#btnStop").disabled = !s.running;
    if (typeof s.dry_run === "boolean") $("#dryRun").checked = s.dry_run;
  }

  renderCandidates(s.candidates ?? []);
  renderPositions(s.positions ?? []);
  renderConfig(s.config ?? {});
}

function renderCandidates(list) {
  const body = $("#candBody");
  $("#candCount").textContent = `${list.length} tokens`;
  if (!list.length) {
    body.innerHTML = '<tr><td colspan="10" class="empty">no candidates yet · press <b>SCAN NOW</b> or <b>START</b></td></tr>';
    return;
  }
  body.innerHTML = list.map((c) => {
    const scoreClass = c.score >= 60 ? "score-badge score-high" : "score-badge";
    const change24 = c.price_change_24h ?? 0;
    const change6 = c.price_change_6h ?? 0;
    return `
      <tr>
        <td><span class="${scoreClass}">${c.score.toFixed(1)}</span></td>
        <td><span class="symbol">${c.symbol}</span><div class="pos-meta">${c.name.slice(0, 20)}</div></td>
        <td><span class="chain-pill">${c.chain}</span></td>
        <td>${fmt.usd(c.market_cap)}</td>
        <td>${fmt.usd(c.liquidity_usd)}</td>
        <td>${fmt.usd(c.volume_24h)}</td>
        <td>${c.age_hours.toFixed(1)}h</td>
        <td class="${change6 >= 0 ? 'pos' : 'neg'}">${fmt.pct(change6)}</td>
        <td class="${change24 >= 0 ? 'pos' : 'neg'}">${fmt.pct(change24)}</td>
        <td><a class="btn-mini" href="${c.url || '#'}" target="_blank" rel="noopener">CHART&nbsp;↗</a></td>
      </tr>
    `;
  }).join("");
}

function renderPositions(list) {
  const el = $("#posList");
  $("#posCount").textContent = `${list.length} active`;
  if (!list.length) {
    el.innerHTML = '<div class="empty">no active positions</div>';
    return;
  }
  const cands = state.snapshot?.candidates ?? [];
  el.innerHTML = list.map((p) => {
    const ageMin = p.age_min ?? 0;
    // Live PnL vs entry, using the latest candidate price if we have it.
    const cur = cands.find((c) => c.pair_address === p.pair_address);
    const curPrice = cur ? cur.price_usd : p.entry_price;
    const change = p.entry_price ? ((curPrice - p.entry_price) / p.entry_price) * 100 : 0;
    const pnlUsd = (change / 100) * (p.size_usd ?? 0);
    const cls = change >= 0 ? "pos" : "neg";
    // Progress toward TP (green) or SL (red) target.
    const cfg = state.snapshot?.config ?? {};
    const tp = cfg.take_profit_pct ?? 30;
    const sl = cfg.stop_loss_pct ?? 25;
    const barPct = change >= 0
      ? Math.min(100, (change / tp) * 100)
      : Math.min(100, (Math.abs(change) / sl) * 100);
    return `
      <div class="pos-card ${cls}">
        <div class="row">
          <span class="pos-sym">${p.symbol}</span>
          <span class="pos-size">${fmt.usd(p.size_usd)}</span>
        </div>
        <div class="row">
          <span class="pos-meta">${p.chain} · entry $${(p.entry_price ?? 0).toFixed(6)}</span>
          <span class="pos-pnl ${cls}">${fmt.pct(change)} · ${fmt.usd(pnlUsd)}</span>
        </div>
        <div class="row">
          <span class="pos-meta">now $${(curPrice ?? 0).toFixed(6)}</span>
          <span class="pos-meta">${ageMin.toFixed(0)} min held</span>
        </div>
        <div class="pos-bar ${cls}"><span style="width: ${barPct.toFixed(0)}%"></span></div>
      </div>
    `;
  }).join("");
}

function renderConfig(cfg) {
  const rows = [
    ["MCAP MIN", fmt.usd(cfg.min_mcap)],
    ["AGE MIN", `${cfg.min_age_hours}h`],
    ["AGE MAX", `${cfg.max_age_hours}h`],
    ["VOL 24H MIN", fmt.usd(cfg.min_volume_24h)],
    ["SIZE / POS", fmt.usd(cfg.position_size_usd)],
    ["MAX POS", cfg.max_active_positions],
    ["RANGE ±", `${cfg.range_width_pct}%`],
    ["TP", `+${cfg.take_profit_pct}%`],
    ["SL", `-${cfg.stop_loss_pct}%`],
  ];
  $("#cfg").innerHTML = rows.map(([k, v]) => `
    <div class="cfg-row"><span class="k">${k}</span><span class="v">${v}</span></div>
  `).join("");
}

function addFeed(evt) {
  state.feedCount++;
  $("#feedCount").textContent = `${state.feedCount} events`;
  const feed = $("#feed");
  const kind = evt.kind || "event";
  let cls = "";
  if (kind.includes("tp") || kind === "position.close") cls = "tp";
  else if (kind.includes("sl") || kind.includes("error") || kind.includes("fail")) cls = "err";
  else if (kind.includes("open")) cls = "open";
  else if (kind === "error") cls = "err";

  const time = new Date().toLocaleTimeString("en-GB");
  const msg = summarizeEvent(evt);
  const item = document.createElement("div");
  item.className = "feed-item " + cls;
  item.innerHTML = `
    <span class="t">${time}</span>
    <span class="k">${kind}</span>
    <span class="m">${msg}</span>
  `;
  feed.prepend(item);
  // Cap 100
  while (feed.children.length > 100) feed.removeChild(feed.lastChild);
}

function summarizeEvent(e) {
  const k = e.kind;
  if (k === "bot.start") return `bot started · dry_run=${e.dry_run}`;
  if (k === "bot.stop") return "bot stopped";
  if (k === "scan.start") return "scanning DexScreener…";
  if (k === "scan.done") return `${e.count} candidates · top: ${(e.top ?? []).map(t => t.symbol).join(", ") || "-"}`;
  if (k === "scan.manual") return `manual scan · ${e.count} candidates`;
  if (k === "position.open") return `→ opened ${e.pos?.symbol} · ${fmt.usd(e.pos?.size_usd ?? 0)} @ $${(e.pos?.entry_price ?? 0).toFixed(6)} ${e.mode === 'dry' ? '[DRY]' : ''}`;
  if (k === "position.plan") return `planned mint ${e.cand?.symbol} · ticks=[${e.plan?.tickLower}, ${e.plan?.tickUpper}]`;
  if (k === "position.close") return `✕ closed ${e.pos?.symbol} · age ${e.pos?.age_min ?? 0}m`;
  if (k === "position.tp") return `💰 TP hit ${e.symbol} · ${fmt.pct(e.change)} after ${e.age_min?.toFixed(0)}m`;
  if (k === "position.sl") return `🩸 SL hit ${e.symbol} · ${fmt.pct(e.change)} after ${e.age_min?.toFixed(0)}m`;
  if (k === "position.rebalance") return `⟳ rebalance ${e.symbol} · new center $${(e.new_price ?? 0).toFixed(6)}`;
  if (k === "position.open.fail") return `failed opening ${e.symbol}: ${e.err}`;
  if (k === "error") return e.msg || "unknown error";
  return JSON.stringify(e).slice(0, 140);
}

// ---- Wire up ----
$("#btnScan").addEventListener("click", apiScan);
$("#btnStart").addEventListener("click", apiStart);
$("#btnStop").addEventListener("click", apiStop);

connect();
fetchState();
// Poll periodically for kpis freshness
setInterval(fetchState, 15000);
