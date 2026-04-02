/**
 * Self-IP Agency Dashboard — Frontend
 * Fetches /api/data every 30s and renders TAS scores + agent status cards.
 */

const REFRESH_MS = 30_000;
let refreshTimer = null;

// ── Data fetching ──────────────────────────────────────────────────────────

async function fetchData() {
  try {
    const res = await fetch("/api/data");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error("Failed to fetch dashboard data:", err);
    return null;
  }
}

// ── Rendering helpers ──────────────────────────────────────────────────────

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value ?? "—";
}

function setClass(el, ...classes) {
  if (!el) return;
  el.className = el.className.replace(/\b(idle|active|super)\b/g, "");
  el.classList.add(...classes.filter(Boolean));
}

function formatNum(n, decimals = 0) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toFixed(decimals);
}

function formatUSD(n) {
  if (n == null || isNaN(n)) return "—";
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : n > 0 ? "+" : "";
  return `${sign}$${abs.toFixed(2)}`;
}

function formatRelativeTime(isoStr) {
  if (!isoStr) return "—";
  try {
    const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    return `${Math.floor(diff / 3600)}h ago`;
  } catch {
    return "—";
  }
}

// ── Main render ────────────────────────────────────────────────────────────

function render(data) {
  if (!data) return;

  const { tas, agents, vp, alerts } = data;

  // TAS hero
  setText("tas-total", formatNum(tas?.total, 1));
  setText("tas-social", formatNum(tas?.social, 1));
  setText("tas-trade", formatNum(tas?.trade, 1));

  // Mode badge
  const modeBadge = document.getElementById("mode-badge");
  const mode = tas?.mode || "idle";
  if (modeBadge) {
    modeBadge.textContent = mode.toUpperCase();
    modeBadge.className = `mode-badge ${mode}`;
  }

  // Main agent
  const mainA = agents?.main || {};
  const mainDot = document.getElementById("dot-main");
  setClass(mainDot, mainA.status === "active" ? "active" : "idle");
  setText("main-mode", mainA.mode || "—");
  setText("main-heartbeat", formatRelativeTime(mainA.last_heartbeat));
  setText("main-uptime", mainA.uptime_hours != null ? `${mainA.uptime_hours}h` : "—");

  // Bookmarker agent
  const bmA = agents?.bookmarker || {};
  const bmDot = document.getElementById("dot-bookmarker");
  setClass(bmDot, bmA.status === "active" ? "active" : "idle");
  setText("bm-tas", formatNum(bmA.tas_social, 1));
  setText("bm-curated", bmA.posts_curated ?? "—");
  setText("bm-vp", bmA.vp_spent != null ? `${bmA.vp_spent} VP` : "—");
  setText("bm-posts", bmA.posts_created ?? "—");

  const bmCard = document.getElementById("card-bookmarker");
  if (bmCard) setClass(bmCard, "agent-card", bmA.status === "active" ? "active" : "");

  // Trader agent
  const trA = agents?.trader || {};
  const trDot = document.getElementById("dot-trader");
  setClass(trDot, trA.status === "active" ? "active" : "idle");
  setText("tr-tas", formatNum(trA.tas_trade, 1));
  setText("tr-trades", trA.trades_executed ?? "—");
  setText("tr-volume", trA.total_volume_usd != null ? `$${trA.total_volume_usd.toFixed(0)}` : "—");
  const pnlEl = document.getElementById("tr-pnl");
  if (pnlEl) {
    pnlEl.textContent = formatUSD(trA.net_pnl_usd);
    pnlEl.style.color = (trA.net_pnl_usd ?? 0) >= 0
      ? "var(--accent-green)"
      : "var(--accent-red)";
  }

  const trCard = document.getElementById("card-trader");
  if (trCard) setClass(trCard, "agent-card", trA.status === "active" ? "active" : "");

  // VP bar
  if (vp) {
    const budget = vp.daily_budget || 1000;
    const used = vp.used_today || 0;
    const pct = Math.min((used / budget) * 100, 100);
    const reservePct = Math.min(((budget - vp.reserve_floor) / budget) * 100, 100);

    const fill = document.getElementById("vp-fill");
    const reserveLine = document.getElementById("vp-reserve-line");
    if (fill) fill.style.width = `${pct}%`;
    if (reserveLine) reserveLine.style.left = `${reservePct}%`;

    setText("vp-used-label", `${used} used / ${budget} daily`);
    setText("vp-remaining-label", `${vp.remaining ?? "—"} remaining`);
  }

  // Alerts
  const alertsList = document.getElementById("alerts-list");
  const alertsSection = document.getElementById("alerts-section");
  if (alertsList && alertsSection) {
    const alertArr = Array.isArray(alerts) ? alerts : [];
    alertsSection.style.display = alertArr.length > 0 ? "block" : "none";
    alertsList.innerHTML = alertArr
      .slice(0, 10)
      .map(a => `<div class="alert-item">${String(a).replace(/</g, "&lt;")}</div>`)
      .join("");
  }

  // Timestamp
  setText("last-updated", data.generated_at
    ? new Date(data.generated_at).toLocaleTimeString()
    : "—"
  );
}

// ── Init ───────────────────────────────────────────────────────────────────

async function refresh() {
  const data = await fetchData();
  render(data);
}

async function init() {
  await refresh();
  refreshTimer = setInterval(refresh, REFRESH_MS);
}

document.addEventListener("DOMContentLoaded", init);
