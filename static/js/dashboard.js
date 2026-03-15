/* Shared chart helpers */
const BLUE  = "rgba(37,99,235,0.85)";
const GREEN = "rgba(16,185,129,0.85)";
const PAL   = ["#2563eb","#10b981","#f59e0b","#ef4444","#8b5cf6","#06b6d4","#ec4899","#84cc16"];

const OPTS_CLEAN  = { plugins: { legend: { display: false } } };
const OPTS_LEGEND = { plugins: { legend: { position: "bottom" } } };
const SCALE_Y     = { scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } };
const SCALE_X     = { scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } };

function makeChart(canvasId, type, labels, datasets, options) {
  const el = document.getElementById(canvasId);
  if (!el) return null;
  return new Chart(el, { type, data: { labels, datasets }, options: options || {} });
}

/* Shared fetch helper */
function dashFetch(url) {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const sep = url.includes("?") ? "&" : "?";
  return fetch(url + sep + "token=" + encodeURIComponent(token))
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
}

/* Format date to relative */
function timeAgo(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return "ahora";
  if (diff < 3600) return Math.floor(diff / 60) + " min";
  if (diff < 86400) return Math.floor(diff / 3600) + " h";
  if (diff < 604800) return Math.floor(diff / 86400) + " d";
  return d.toLocaleDateString("es-AR", { day: "2-digit", month: "short" });
}

/* Format time from ISO */
function formatTime(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  return d.toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit" });
}
