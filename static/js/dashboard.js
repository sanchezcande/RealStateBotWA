/* Shared chart helpers — Editorial palette */
const BLUE  = "rgba(154,107,47,0.85)";
const GREEN = "rgba(45,106,79,0.85)";
const PAL   = ["#9A6B2F","#2D6A4F","#D4A957","#8B3A1F","#6B5B95","#2A6B6B","#B85C5C","#5B8C2A"];
const CHANNEL_PAL = ["#2D6A4F", "#8B3A1F", "#2A4A6B", "#6C6257"];

const OPTS_CLEAN  = { plugins: { legend: { display: false } } };
const OPTS_LEGEND = { plugins: { legend: { position: "bottom" } } };
const OPTS_DONUT  = { plugins: { legend: { display: false } } };
const DONUT_OPTS  = {
  cutout: "62%",
  plugins: { legend: { display: false }, tooltip: { bodyFont: { size: 13 } } },
  layout: { padding: 4 },
  responsive: true,
  maintainAspectRatio: true,
};
const SCALE_Y     = { scales: { y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "rgba(217,207,191,.25)" } }, x: { grid: { color: "rgba(217,207,191,.15)" } } } };
const SCALE_X     = { scales: { x: { beginAtZero: true, ticks: { precision: 0 } } } };

Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
Chart.defaults.color = "#6C6257";
Chart.defaults.borderColor = "rgba(217,207,191,.3)";

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
  if (diff < 60) return t("time_now");
  if (diff < 3600) return Math.floor(diff / 60) + " " + t("time_min");
  if (diff < 86400) return Math.floor(diff / 3600) + " " + t("time_h");
  if (diff < 604800) return Math.floor(diff / 86400) + " " + t("time_d");
  const locale = (typeof getLang === "function" && getLang() === "en") ? "en-US" : "es-AR";
  return d.toLocaleDateString(locale, { day: "2-digit", month: "short" });
}

/* Format time from ISO */
function formatTime(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  const locale = (typeof getLang === "function" && getLang() === "en") ? "en-US" : "es-AR";
  return d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
}

/* Build HTML legend beside a doughnut chart */
function buildLegend(containerId, labels, values, colors, total) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = labels.map((label, i) => {
    const pct = total > 0 ? ((values[i] / total) * 100).toFixed(1) : 0;
    return `<div class="legend-item">
      <span class="legend-dot" style="background:${colors[i]}"></span>
      <span class="legend-label">${label}</span>
      <span class="legend-val">${values[i]} <small>(${pct}%)</small></span>
    </div>`;
  }).join("");
}
