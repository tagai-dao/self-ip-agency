/**
 * Self-IP Agency Dashboard v2 — Frontend
 * Fetches /api/status, /api/strategy every 30s and renders all sections.
 */

const REFRESH_MS = 30_000;
let refreshTimer = null;

// ── Helpers ──

function fmt(v, fallback) { return v != null ? v : (fallback || '—'); }
function fmtPct(v) { return v != null ? (v * 100).toFixed(0) + '%' : '—'; }
function fmtUsd(v) { return v != null ? '$' + Number(v).toFixed(2) : '—'; }

function ageStr(isoStr) {
  if (!isoStr) return '—';
  try {
    const dt = new Date(isoStr);
    const diff = (Date.now() - dt.getTime()) / 1000;
    if (diff < 60) return Math.round(diff) + 's ago';
    if (diff < 3600) return Math.round(diff / 60) + 'm ago';
    if (diff < 86400) return Math.round(diff / 3600) + 'h ago';
    return Math.round(diff / 86400) + 'd ago';
  } catch { return '—'; }
}

function statusIcon(s) {
  const map = { ok: '✅', active: '✅', running: '🔄', idle: '💤', stale: '⏰', missing: '❓', degraded: '⚠️', unknown: '❓' };
  return (map[s] || '❓') + ' ' + (s || 'unknown');
}

function setPill(id, status) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = 'pill pill-' + (status || 'unknown');
}

// ── Data fetching ──

async function fetchStatus() {
  try {
    const resp = await fetch('/api/status');
    if (!resp.ok) return;
    const d = await resp.json();
    renderStatus(d);
  } catch (e) {
    console.error('fetchStatus failed:', e);
  }
}

async function fetchStrategy() {
  try {
    const resp = await fetch('/api/strategy');
    if (!resp.ok) return;
    const d = await resp.json();
    renderStrategy(d);
  } catch (e) {
    console.error('fetchStrategy failed:', e);
  }
}

function fetchAll() {
  fetchStatus();
  fetchStrategy();
  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
}

// ── Renderers ──

function renderStatus(d) {
  // TAS
  document.getElementById('tas-total').textContent = fmt(d.tas?.total);
  document.getElementById('tas-social').textContent = fmt(d.tas?.social);
  document.getElementById('tas-trade').textContent = fmt(d.tas?.trade);
  const modeEl = document.getElementById('tas-mode');
  modeEl.textContent = fmt(d.tas?.mode, 'idle');
  modeEl.className = 'mode-badge mode-' + (d.tas?.mode || 'idle');

  // Live OP/VP
  document.getElementById('live-op').textContent = fmt(d.live_op_vp?.op);
  document.getElementById('live-vp').textContent = fmt(d.live_op_vp?.vp);

  // Agent pills
  const agents = d.agents || {};
  for (const name of ['main', 'bookmarker', 'trader']) {
    const a = agents[name] || {};
    const ageStatus = a.age_status || a.status || 'unknown';
    setPill('pill-' + name, ageStatus === 'ok' ? 'ok' : (ageStatus === 'stale' ? 'stale' : a.status));
  }

  // Main agent
  const main = agents.main || {};
  document.getElementById('main-status').textContent = statusIcon(main.status);
  document.getElementById('main-heartbeat').textContent = ageStr(main.last_heartbeat);
  document.getElementById('main-freshness').textContent = statusIcon(main.age_status);

  // Bookmarker
  const bk = agents.bookmarker || {};
  document.getElementById('bk-status').textContent = statusIcon(bk.status);
  document.getElementById('bk-tas').textContent = fmt(bk.tas_social);
  const topics = bk.topic_brief || [];
  document.getElementById('bk-topics').textContent = topics.length ? topics.map(t => typeof t === 'string' ? t : (t.name || t.topic || '')).join(', ') : '—';

  // Trader
  const tr = agents.trader || {};
  document.getElementById('tr-status').textContent = statusIcon(tr.status);
  document.getElementById('tr-tas').textContent = fmt(tr.tas_trade);
  document.getElementById('tr-wallet').textContent = fmtUsd(tr.wallet?.total_usd);

  // Wiki
  const wiki = d.wiki || {};
  document.getElementById('wiki-health').textContent = wiki.health_score != null ? wiki.health_score.toFixed(1) : '—';
  document.getElementById('wiki-contract').textContent = statusIcon(wiki.contract_status) + ` (${fmt(wiki.contract_pass, 0)}/${fmt(wiki.contract_pass + wiki.contract_fail, 0)})`;
  document.getElementById('wiki-attention').textContent = wiki.needs_attention ? '⚠️ Yes' : '✅ No';

  // Strategy summary
  const strat = d.strategy || {};
  document.getElementById('strat-bk-mode').textContent = fmt(strat.bk_mode);
  document.getElementById('strat-bk-wr').textContent = fmtPct(strat.bk_win_rate);
  document.getElementById('strat-tr-mode').textContent = fmt(strat.tr_mode);
  document.getElementById('strat-tr-wr').textContent = fmtPct(strat.tr_win_rate);
  document.getElementById('strat-cycles').textContent = fmt(strat.experiment_cycle);

  // Community heat
  const heat = d.community_heat || {};
  const ticks = heat.top_ticks || [];
  document.getElementById('community-ticks').textContent = ticks.length ? ticks.join(' · ') : '—';
}

function renderStrategy(d) {
  const container = document.getElementById('recent-cycles');
  if (!container) return;
  const cycles = d.recent_cycles || [];
  if (!cycles.length) {
    container.textContent = 'No strategy cycles yet';
    return;
  }
  let html = '<table class="cycle-table"><tr><th>Cycle</th><th>Outcome</th><th>Delta</th><th>Mode</th></tr>';
  for (const c of cycles.slice(-5).reverse()) {
    const delta = c.delta?.total != null ? (c.delta.total > 0 ? '+' : '') + c.delta.total.toFixed(4) : '—';
    const icon = c.kept ? '✅' : '❌';
    html += `<tr><td>${(c.cycle_id || '').slice(0, 19)}</td><td>${icon} ${c.outcome || '—'}</td><td>${delta}</td><td>${c.experiment_mode || '—'}</td></tr>`;
  }
  html += '</table>';
  container.innerHTML = html;
}

// ── Clock + auto-refresh ──

function updateClock() {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
}

setInterval(updateClock, 1000);
updateClock();

// Initial fetch
fetchAll();
refreshTimer = setInterval(fetchAll, REFRESH_MS);
