// SCADA Generator — интерфейс оператора.
import { api, connectWS, setToken } from "./api.js";
import { TrendChart } from "./chart.js";
import { BOILER_YAML } from "./examples.js";
import { applyStatic, fmtDuration, fmtNum, fmtTime, getLang, has, initI18n, label, setLang, t } from "./i18n.js";
import { Mimic } from "./mimic.js";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const state = {
  meta: null,
  hmi: null,
  tags: new Map(), // key -> метаданные виджета
  snap: null,
  view: "overview",
  trendSel: [],
  trendRange: 900,
  drawer: null,
  history: [],
  kpi: null,
  ml: null,
  faults: { available: [], active: [] },
  genLayout: null,
};

let mimic;
let genMimic;
let drawerChart;
const trendCharts = new Map();

// ---------- утилиты ----------
function tagMeta(key) { return state.tags.get(key); }
function tagName(tag, device) {
  const key = tag?.includes("/") ? tag : `${device || firstDevice()}/${tag}`;
  const m = tagMeta(key);
  return m ? label(m.label, m.tag) : tag;
}
function firstDevice() { return state.hmi?.areas?.[0]?.main?.device || state.hmi?.areas?.[0]?.tiles?.[0]?.device || ""; }

function toast(msg, err = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.toggle("err", err);
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 3200);
}

async function control(path, opts) {
  try {
    return await api(path, opts);
  } catch (e) {
    if (e.status === 401) {
      const tok = window.prompt(t("common.token"));
      if (tok) { setToken(tok); return api(path, opts); }
    }
    toast(`${t("common.error")}: ${e.message}`, true);
    throw e;
  }
}

function paramsText(p, device) {
  const out = { ...p };
  if (p.tag) out.tag = tagName(p.tag, device);
  const dec = tagMeta(`${device}/${p.tag}`)?.decimals ?? 1;
  // Для дрейфа — на знак точнее: отклонение может быть меньше шага округления.
  const precise = typeof p.sigma === "number" ? dec + 1 : dec;
  for (const k of ["value", "expected", "baseline", "limit"]) {
    if (typeof p[k] === "number") out[k] = fmtNum(p[k], k === "limit" ? dec : precise);
  }
  for (const k of ["sigma", "z"]) {
    if (typeof p[k] === "number") out[k] = fmtNum(p[k], 1);
  }
  if (typeof p.trend_per_min === "number") out.trend_per_min = (p.trend_per_min > 0 ? "+" : "") + fmtNum(p.trend_per_min, Math.max(dec, 2));
  if (p.kind) out.kind = t(`kind.${p.kind}`);
  if (typeof p.eta_s === "number") out.eta = fmtDuration(p.eta_at ? p.eta_at - Date.now() / 1000 : p.eta_s);
  if (Array.isArray(p.top)) out.top = p.top.map((x) => `${tagName(x.tag, device)} ${Math.round(x.share * 100)}%`).join(", ");
  if (Array.isArray(p.tags)) out.tags = p.tags.map((x) => tagName(x, device)).join(", ");
  return out;
}
const insightText = (ins) => t(ins.code, paramsText(ins.params, ins.device_id));
const alarmText = (a) => t(a.message?.code || "", paramsText(a.message?.params || {}, a.device_id));

// ---------- тема: светлая / тёмная / как в системе ----------
function themeChoice() {
  try {
    const v = localStorage.getItem("scada.theme");
    return ["light", "dark", "auto"].includes(v) ? v : "auto";
  } catch { return "auto"; }
}

function setTheme(choice) {
  const light = choice === "light" || (choice === "auto" && window.matchMedia("(prefers-color-scheme: light)").matches);
  document.documentElement.dataset.theme = light ? "light" : "dark";
  try { localStorage.setItem("scada.theme", choice); } catch { /* нет хранилища */ }
  $$("#theme-switch button").forEach((b) => {
    const on = b.dataset.themeSet === choice;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", String(on));
  });
}

// ---------- навигация ----------
function showView(view) {
  if (!document.getElementById(`view-${view}`)) view = "overview";
  if (view === "demo" && !state.meta?.demo) view = "overview";
  state.view = view;
  $$(".view").forEach((v) => { v.hidden = v.id !== `view-${view}`; });
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  $(".sidebar").classList.remove("open");
  refreshView();
}

function refreshView() {
  if (state.view === "trends") renderTrends(true);
  if (state.view === "alarms") loadAlarms();
  if (state.view === "insights") loadInsights();
  if (state.view === "demo") loadFaults();
  if (state.view === "generator" && !$("#gen-yaml").value) loadCurrentYaml();
}

// ---------- шапка и KPI ----------
function renderHeader() {
  const m = state.meta;
  $("#plant-name").textContent = label(m.name, "SCADA");
  document.title = `${label(m.name, "SCADA")} — SCADA Generator`;
  $("#demo-flag").hidden = !m.demo;
  $("#nav-demo").hidden = !m.demo;
  const core = $("#core-chip");
  core.textContent = t(m.ml_backend === "cpp" ? "top.core_cpp" : "top.core_python");
  core.classList.toggle("ml", m.ml_backend === "cpp");
  $("#db-chip").textContent = t(m.database ? "top.db_on" : "top.db_off");
  $("#version").textContent = `v${m.version}`;
  $$(".lang button").forEach((b) => b.classList.toggle("active", b.dataset.lang === getLang()));
  const st = state.hmi?.stats;
  if (st) $("#mimic-sub").textContent = t("overview.generated", { areas: st.areas, tags: st.tags });
}

function renderHealth() {
  const h = state.snap?.health?.plant;
  const ring = $("#health-ring");
  $("#health-value").textContent = h === undefined ? "—" : `${h}%`;
  $("#kpi-health").textContent = h === undefined ? "—" : `${h}%`;
  if (h !== undefined) {
    ring.style.strokeDashoffset = String(97.4 * (1 - h / 100));
    ring.style.stroke = h < 50 ? "var(--high)" : h < 80 ? "var(--medium)" : "var(--proc)";
  }
}

function renderKPIs() {
  const s = state.snap;
  if (!s) return;
  const real = s.alarms.filter((a) => !a.kind.startsWith("PRED_"));
  const active = real.filter((a) => a.active).length;
  const unacked = real.filter((a) => a.state === "ACTIVE_UNACK" || a.state === "RTN_UNACK").length;
  const predicted = Object.values(s.tags).filter((x) => x.ml?.eta_s).length;
  $("#kpi-alarms").textContent = active;
  $("#kpi-unacked").textContent = unacked;
  $("#kpi-predicted").textContent = predicted;
  $("#kpi-insights").textContent = s.insights.length;
  $("#kpi-alarms").parentElement.classList.toggle("alert", active > 0);
  $("#kpi-unacked").parentElement.classList.toggle("alert", unacked > 0);
  const nb = $("#nav-alarms");
  nb.hidden = unacked === 0;
  nb.textContent = unacked;
  const ni = $("#nav-insights");
  ni.hidden = s.insights.length === 0;
  ni.textContent = s.insights.length;
  if (state.kpi) {
    const lvl = $("#kpi-eemua");
    lvl.textContent = t(`eemua.${state.kpi.performance_level}`);
    lvl.classList.add("lvl");
  }
}

// ---------- инсайты ----------
function insightItem(ins, { withHint = true, now = Date.now() / 1000 } = {}) {
  const hintKey = `hint.${ins.kind}`;
  const eta = ins.kind === "forecast" && ins.active && ins.params.eta_at
    ? `<span class="eta">⏱ ${esc(fmtDuration(ins.params.eta_at - now))}</span>` : "";
  const when = ins.active ? t("insight.since", { time: fmtTime(ins.started_at) }) : `${fmtTime(ins.started_at)} · ${t("insight.ended")}`;
  const canRebase = ins.active && ((ins.tag && ["drift", "stuck"].includes(ins.kind)) || ins.kind === "event");
  return `<li class="insight ${ins.severity} ${ins.active ? "" : "ended"}" data-id="${ins.id}">
    <div>
      <div class="kind">${esc(t(`insight.kind.${ins.kind}`))}</div>
      <div class="text">${esc(insightText(ins))}</div>
      ${withHint && has(hintKey) ? `<div class="hint"><b>${esc(t("insight.what_to_do"))}:</b> ${esc(t(hintKey))}</div>` : ""}
    </div>
    <div class="meta">${eta}<span>${esc(when)}</span>
      ${canRebase ? `<button class="btn small ghost" data-rebase="${esc(ins.device_id)}|${esc(ins.tag || "")}">${esc(t("tag.accept_normal"))}</button>` : ""}
    </div>
  </li>`;
}

function renderOverviewLists() {
  const s = state.snap;
  if (!s) return;
  const ins = [...s.insights].sort((a, b) => b.started_at - a.started_at).slice(0, 5);
  $("#ov-insights").innerHTML = ins.length ? ins.map((i) => insightItem(i, { withHint: false })).join("") : `<li class="empty">${esc(t("empty.insights"))}</li>`;
  const al = s.alarms.slice(0, 6);
  $("#ov-alarms").innerHTML = al.length ? al.map(alarmRow).join("") : `<li class="empty">${esc(t("empty.alarms"))}</li>`;
}

function prioIcon(p) {
  const n = { high: "1", medium: "2", low: "3" }[p] || "";
  return `<span class="prio ${p}"><span>${n}</span></span>`;
}

function alarmRow(a) {
  const unack = a.state === "ACTIVE_UNACK" || a.state === "RTN_UNACK";
  return `<li class="alarm-row ${unack ? "unack" : ""}">${prioIcon(a.priority)}
    <div>${esc(alarmText(a))}</div><span class="t">${esc(fmtTime(a.raised_at))}</span></li>`;
}

// ---------- алармы ----------
async function loadAlarms() {
  const [data, kpi] = await Promise.all([api("/api/alarms"), api("/api/alarms/kpi")]);
  state.kpi = kpi;
  const body = $("#alarm-table tbody");
  body.innerHTML = data.active.length ? data.active.map((a) => {
    const unack = a.state === "ACTIVE_UNACK" || a.state === "RTN_UNACK";
    return `<tr class="${unack ? "unack" : ""}">
      <td>${prioIcon(a.priority)} ${esc(t(`priority.${a.priority}`))}</td>
      <td class="mono">${esc(fmtTime(a.raised_at))}</td>
      <td>${esc(alarmText(a))}</td>
      <td>${esc(t(`state.${a.state}`))}</td>
      <td class="actions">
        ${unack ? `<button class="btn small" data-ack="${a.id}">${esc(t("alarms.ack"))}</button>` : ""}
        ${a.active && !a.shelved_until ? `<button class="btn small ghost" data-shelve="${a.id}">${esc(t("alarms.shelve"))}</button>` : ""}
      </td></tr>`;
  }).join("") : `<tr><td colspan="5" class="empty">${esc(t("empty.alarms"))}</td></tr>`;

  const journal = [...data.journal].reverse().slice(0, 60);
  $("#journal").innerHTML = journal.length ? journal.map((j) => {
    if (j.event === "write") {
      const [dev, tag] = j.tag.split("/");
      return `<li><span>${esc(fmtTime(j.ts))}</span><span>${esc(t("event.write"))}</span><b>${esc(tagName(tag, dev))} → ${esc(fmtNum(j.value, 1))}</b></li>`;
    }
    return `<li><span>${esc(fmtTime(j.ts))}</span><span>${esc(t(`event.${j.event}`))}</span><b>${esc(alarmText(j.alarm))}</b></li>`;
  }).join("") : `<li class="empty">${esc(t("empty.journal"))}</li>`;

  const levels = [["overloaded", "> 10"], ["reactive", "5–10"], ["stable", "2–5"], ["robust", "1–2"], ["predictive", "≤ 1"]];
  $("#eemua").innerHTML = levels.map(([l, range]) => `<div class="${l === kpi.performance_level ? "on" : ""}"><span>${esc(t(`eemua.${l}`))}</span><span class="mono">${range}</span></div>`).join("");
  const rows = [
    ["alarms.kpi.rate", kpi.rate_last_10min],
    ["alarms.kpi.avg", `${fmtNum(kpi.avg_per_10min, 1)}`],
    ["alarms.kpi.peak", kpi.peak_per_10min],
    ["alarms.kpi.flood", `${fmtNum(kpi.flood_percent, 1)}%`],
    ["alarms.kpi.chattering", kpi.chattering.length],
    ["alarms.kpi.standing", kpi.standing.length],
    ["alarms.kpi.shelved", kpi.shelved],
  ];
  $("#kpi-list").innerHTML = rows.map(([k, v]) => `<dt>${esc(t(k))}</dt><dd>${esc(v)}</dd>`).join("")
    + `<dt class="small">${esc(t("alarms.kpi.target"))}</dt><dd></dd>`
    + (kpi.bad_actors.length ? `<dt>${esc(t("alarms.kpi.bad_actors"))}</dt><dd></dd>` + kpi.bad_actors.map((b) => {
      const [dev, tag, kind] = b.key.split("/");
      return `<dt class="small">${esc(tag === "*" ? dev : tagName(tag, dev))} · ${esc(t(`kind.${kind}`))}</dt><dd>${b.count}</dd>`;
    }).join("") : "");
  renderKPIs();
}

// ---------- ИИ-аналитика ----------
async function loadInsights() {
  const [hist, ml] = await Promise.all([api("/api/ml/insights"), api("/api/ml/status")]);
  state.history = hist;
  state.ml = ml;
  renderInsights();
}

function renderInsights() {
  const now = Date.now() / 1000;
  const active = state.snap?.insights || [];
  $("#insights-active").innerHTML = active.length
    ? [...active].sort((a, b) => b.started_at - a.started_at).map((i) => insightItem(i, { now })).join("")
    : `<li class="empty">${esc(t("empty.insights"))}</li>`;
  const ended = state.history.filter((i) => !i.active).slice(0, 25);
  $("#insights-history").innerHTML = ended.map((i) => insightItem(i, { withHint: false })).join("") || `<li class="empty">${esc(t("empty.journal"))}</li>`;

  const ml = state.ml;
  if (!ml) return;
  const devId = Object.keys(ml.devices)[0];
  const dev = ml.devices[devId];
  if (!dev) return;
  const pca = dev.pca;
  $("#mspc-status").innerHTML = pca.trained
    ? `<div class="small"><b>${esc(t("ml.trained"))}</b> · ${esc(t("ml.components"))}: ${pca.components} · ${esc(t("ml.explained"))}: ${Math.round(pca.explained_variance.reduce((a, b) => a + b, 0) * 100)}%</div>`
    : `<div class="small">${esc(t("ml.training", { pct: Math.round(pca.progress * 100) }))}</div><div class="progress"><i style="width:${pca.progress * 100}%"></i></div>`;
  const mspc = state.snap?.mspc?.[devId] || dev.mspc;
  const setBar = (id, valId, ratio) => {
    const bar = $(id);
    const r = ratio ?? 0;
    bar.style.width = `${Math.min(100, r * 50)}%`;
    bar.classList.toggle("over", r > 1);
    $(valId).textContent = ratio === undefined || ratio === null ? "—" : `${Math.round(r * 100)}%`;
  };
  setBar("#spe-bar", "#spe-val", mspc?.spe_ratio);
  setBar("#t2-bar", "#t2-val", mspc?.t2_ratio);
  const contrib = Object.entries(mspc?.contributions || {}).sort((a, b) => b[1] - a[1]);
  $("#contrib").innerHTML = contrib.map(([tag, v]) => `<div class="contrib-row"><span>${esc(tagName(tag, devId))}</span>
    <div class="bar"><i style="width:${Math.round(v * 100)}%"></i></div><span>${Math.round(v * 100)}%</span></div>`).join("");
  $("#ml-kv").innerHTML = `<dt>${esc(t("ml.core"))}</dt><dd>${esc(t(ml.backend === "cpp" ? "top.core_cpp" : "top.core_python"))}</dd>
    <dt>${esc(t("ml.samples"))}</dt><dd>${esc(fmtNum(ml.samples, 0))}</dd>`;
}

// ---------- тренды ----------
function renderTrendPicker() {
  const keys = [...state.tags.values()].filter((m) => !m.bit);
  if (!state.trendSel.length) {
    // По умолчанию — то, что сейчас интересно: прогнозы, затем аномалии, затем теги под ИИ.
    const tags = state.snap?.tags || {};
    const byEta = Object.keys(tags).filter((k) => tags[k].ml?.eta_s);
    const byScore = Object.keys(tags).filter((k) => tags[k].ml?.score >= 0.5).sort((a, b) => tags[b].ml.score - tags[a].ml.score);
    const mlKeys = keys.filter((k) => k.ml).map((k) => k.key);
    state.trendSel = [...new Set([...byEta, ...byScore, ...mlKeys])].filter((k) => state.tags.has(k)).slice(0, 4);
  }
  $("#trend-picker").innerHTML = keys.map((m) => `<button data-key="${esc(m.key)}" class="${state.trendSel.includes(m.key) ? "active" : ""}">${esc(label(m.label, m.tag))}</button>`).join("");
  $$("#trend-range button").forEach((b) => b.classList.toggle("active", Number(b.dataset.range) === state.trendRange));
}

async function renderTrends(rebuild = false) {
  const grid = $("#trend-grid");
  if (rebuild) {
    renderTrendPicker();
    grid.innerHTML = state.trendSel.map((key) => {
      const m = tagMeta(key);
      return `<div class="card trend-card" data-key="${esc(key)}"><div class="head"><h3>${esc(label(m.label, m.tag))}</h3>
        <span class="muted small">${esc(m.unit || "")}</span><span class="eta"></span><span class="val">—</span></div><canvas></canvas></div>`;
    }).join("");
    trendCharts.clear();
    $$(".trend-card", grid).forEach((c) => trendCharts.set(c.dataset.key, new TrendChart($("canvas", c))));
  }
  const now = Date.now() / 1000;
  await Promise.all(state.trendSel.map(async (key) => {
    const [dev, tag] = key.split("/");
    const m = tagMeta(key);
    const data = await api(`/api/history/${encodeURIComponent(dev)}/${encodeURIComponent(tag)}?seconds=${state.trendRange}`);
    const card = grid.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!card) return;
    const last = data.value.at(-1);
    $(".val", card).textContent = last === undefined || last === null ? "—" : `${fmtNum(last, m.decimals)} ${m.unit || ""}`;
    $(".eta", card).textContent = data.forecast ? `⏱ ${fmtDuration(data.forecast.eta_s)} → ${t(`kind.${data.forecast.kind}`)}` : "";
    trendCharts.get(key)?.set(data, { now, range: state.trendRange, limits: data.limits, forecast: data.forecast, decimals: m.decimals, unit: m.unit, min: m.min, max: m.max });
  }));
}

// ---------- карточка тега ----------
function openDrawer(w) {
  state.drawer = w.key;
  $("#drawer").classList.add("open");
  $("#scrim").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  renderDrawer(true);
}
function closeDrawer() {
  state.drawer = null;
  $("#drawer").classList.remove("open");
  $("#scrim").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
}

async function renderDrawer(full = false) {
  const key = state.drawer;
  if (!key) return;
  const m = tagMeta(key);
  const s = state.snap?.tags?.[key];
  $("#dr-title").textContent = label(m.label, m.tag);
  $("#dr-key").textContent = key;
  const good = s && s.quality === "GOOD" && s.value !== null;
  $("#dr-value").textContent = !good ? "?" : m.bit ? (s.value >= 0.5 ? t("tag.on") : t("tag.off")) : fmtNum(s.value, m.decimals);
  $("#dr-unit").textContent = m.bit ? "" : m.unit || "";
  const pills = [];
  if (!good) pills.push(`<span class="status-pill">${esc(t("tag.no_data"))}</span>`);
  if (s?.alarm) pills.push(`<span class="status-pill ${s.alarm.priority}">${esc(t(`kind.${s.alarm.kind}`))} · ${esc(t(`state.${s.alarm.state}`))}</span>`);
  if (s?.ml?.ready && s.ml.score >= 0.5) pills.push(`<span class="status-pill ml">${esc(t("legend.ml"))}</span>`);
  if (s?.ml?.eta_s) pills.push(`<span class="status-pill ml">⏱ ${esc(fmtDuration(s.ml.eta_s))} → ${esc(t(`kind.${s.ml.eta_kind}`))}</span>`);
  if (s?.ml && !s.ml.ready && !m.bit) pills.push(`<span class="status-pill">${esc(t("tag.learning"))}</span>`);
  $("#dr-status").innerHTML = pills.join("");

  const ml = s?.ml;
  const rows = [];
  if (ml?.ready) {
    rows.push(["tag.expected", `${fmtNum(ml.expected, m.decimals)} ${m.unit || ""}`]);
    rows.push(["tag.band", `${fmtNum(ml.band_low, m.decimals)} … ${fmtNum(ml.band_high, m.decimals)}`]);
    rows.push(["tag.trend", `${ml.trend_per_min > 0 ? "+" : ""}${fmtNum(ml.trend_per_min, Math.max(2, m.decimals))} ${m.unit || ""}${t("tag.per_min")}`]);
    rows.push(["tag.anomaly_score", `${Math.round(ml.score * 100)}%`]);
  }
  const lims = Object.entries(m.limits || {});
  if (lims.length) rows.push(["tag.limits", lims.map(([k, v]) => `${k} ${fmtNum(v, m.decimals)}`).join(" · ")]);
  $("#dr-kv").innerHTML = rows.map(([k, v]) => `<dt>${esc(t(k))}</dt><dd>${esc(v)}</dd>`).join("");

  if (full) {
    const ctl = $("#dr-control");
    ctl.innerHTML = "";
    if (m.writable) {
      if (m.bit) {
        ctl.innerHTML = `<div class="control"><b>${esc(t("tag.control"))}</b><div class="row">
          <button class="btn" data-write="1">${esc(t("tag.turn_on"))}</button>
          <button class="btn danger" data-write="0">${esc(t("tag.turn_off"))}</button></div></div>`;
      } else {
        ctl.innerHTML = `<div class="control"><b>${esc(t("tag.control"))}</b><div class="row">
          <input type="number" step="any" id="dr-input" min="${m.min ?? ""}" max="${m.max ?? ""}" value="${good ? s.value.toFixed(m.decimals) : ""}">
          <button class="btn primary" data-write="input">${esc(t("tag.set"))}</button></div>
          <span class="muted small">${m.min ?? "−∞"} … ${m.max ?? "∞"} ${esc(m.unit || "")}</span></div>`;
      }
    }
    if (!drawerChart) drawerChart = new TrendChart($("#dr-chart"));
  }
  $("#dr-chart").hidden = m.bit;
  if (!m.bit) {
    const [dev, tag] = key.split("/");
    const data = await api(`/api/history/${encodeURIComponent(dev)}/${encodeURIComponent(tag)}?seconds=600`);
    if (state.drawer === key) drawerChart.set(data, { now: Date.now() / 1000, range: 600, limits: data.limits, forecast: data.forecast, decimals: m.decimals, unit: m.unit, min: m.min, max: m.max });
  }
}

async function writeTag(value) {
  const key = state.drawer;
  const m = tagMeta(key);
  const shown = m.bit ? (value ? t("tag.on") : t("tag.off")) : `${fmtNum(value, m.decimals)} ${m.unit || ""}`;
  if (!window.confirm(t("tag.confirm", { tag: label(m.label, m.tag), value: shown }))) return;
  await control("/api/write", { method: "POST", body: { device: m.device, tag: m.tag, value } });
  toast(t("tag.written"));
}

// ---------- генератор ----------
async function loadCurrentYaml() {
  const { yaml } = await api("/api/config/yaml");
  $("#gen-yaml").value = yaml;
  runGenerator();
}

async function runGenerator() {
  const out = $("#gen-result");
  try {
    const res = await fetch("/api/hmi/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ yaml: $("#gen-yaml").value }) });
    const data = await res.json();
    if (!res.ok) {
      const errors = data.errors || [{ path: "", message: data.detail || res.statusText }];
      out.innerHTML = `<b>${esc(t("gen.errors"))}</b><ul>${errors.map((e) => `<li>${esc(e.path)}: ${esc(e.message)}</li>`).join("")}</ul>`;
      return;
    }
    state.genLayout = data.hmi;
    const st = data.hmi.stats;
    out.innerHTML = `<span class="ok">✓ ${esc(t("gen.ok", { areas: st.areas, tags: st.tags, alarms: st.alarm_limits, ml: st.ml_tags }))}</span>`;
    genMimic.render(data.hmi);
    genMimic.update({});
    $("#gen-svg").disabled = false;
    $("#gen-json").disabled = false;
  } catch (e) {
    out.textContent = `${t("common.error")}: ${e.message}`;
  }
}

function download(name, text, type) {
  const blob = new Blob([text], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- демо ----------
async function loadFaults() {
  state.faults = await api("/api/sim/faults");
  renderDemo();
}
function renderDemo() {
  const { available, active } = state.faults;
  $("#scenarios").innerHTML = available.map((f) => {
    const on = active.includes(f);
    return `<div class="scenario ${on ? "running" : ""}">
      <h3>${esc(t(`fault.${f}`))}${on ? `<span class="tagline">● ${esc(t("demo.running"))}</span>` : ""}</h3>
      <p>${esc(t(`fault.${f}.desc`))}</p>
      <div class="watch"><b>${esc(t("demo.watch"))}:</b> ${esc(t(`fault.${f}.watch`))}</div>
      <button class="btn ${on ? "" : "primary"}" data-fault="${esc(f)}" data-on="${on ? 1 : 0}">${esc(t(on ? "demo.stop" : "demo.start"))}</button>
    </div>`;
  }).join("");
}

// ---------- поток данных ----------
function onMessage(msg) {
  if (msg.type === "snapshot") {
    state.snap = state.snap ? { ...state.snap, ...msg.data, tags: { ...state.snap.tags, ...msg.data.tags } } : msg.data;
    mimic.update(state.snap.tags);
    renderHealth();
    renderKPIs();
    if (state.view === "overview") renderOverviewLists();
    if (state.view === "insights") renderInsights();
    if (state.drawer) renderDrawer();
  } else if (msg.type === "alarm" && state.view === "alarms") {
    loadAlarms();
  } else if (msg.type === "insight" && msg.event === "open" && state.view === "insights") {
    loadInsights();
  }
}

function onWSState(s) {
  const c = $("#conn");
  c.classList.toggle("ok", s === "open");
  c.classList.toggle("bad", s === "closed");
  $("span", c).textContent = t(s === "open" ? "top.online" : s === "closed" ? "top.offline" : "top.connecting");
  c.dataset.state = s;
}

// ---------- события ----------
function bindEvents() {
  window.addEventListener("hashchange", () => showView(location.hash.slice(1)));
  $("#menu-btn").addEventListener("click", () => $(".sidebar").classList.toggle("open"));
  $$(".lang button").forEach((b) => b.addEventListener("click", async () => {
    await setLang(b.dataset.lang);
    renderAll();
  }));
  $$("#theme-switch button").forEach((b) => b.addEventListener("click", () => {
    setTheme(b.dataset.themeSet);
    renderAll();
  }));
  window.matchMedia("(prefers-color-scheme: light)").addEventListener?.("change", () => {
    if (themeChoice() === "auto") { setTheme("auto"); renderAll(); }
  });
  $("#dr-close").addEventListener("click", closeDrawer);
  $("#scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  $("#dr-control").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-write]");
    if (!btn) return;
    const v = btn.dataset.write === "input" ? Number($("#dr-input").value) : Number(btn.dataset.write);
    if (Number.isFinite(v)) writeTag(v).catch(() => {});
  });

  document.addEventListener("click", async (e) => {
    const ack = e.target.closest("[data-ack]");
    const shelve = e.target.closest("[data-shelve]");
    const rebase = e.target.closest("[data-rebase]");
    const fault = e.target.closest("[data-fault]");
    try {
      if (ack) { await control(`/api/alarms/${ack.dataset.ack}/ack`, { method: "POST" }); loadAlarms(); }
      if (shelve) { await control(`/api/alarms/${shelve.dataset.shelve}/shelve`, { method: "POST", body: { seconds: 900 } }); loadAlarms(); }
      if (rebase) {
        const [dev, tag] = rebase.dataset.rebase.split("|");
        const q = tag ? `?tag=${encodeURIComponent(tag)}` : "";
        await control(`/api/ml/rebase/${encodeURIComponent(dev)}${q}`, { method: "POST" });
        toast(t("tag.written"));
      }
      if (fault) {
        const f = fault.dataset.fault;
        state.faults = fault.dataset.on === "1"
          ? await control(`/api/sim/fault?fault=${encodeURIComponent(f)}`, { method: "DELETE" }).then((r) => ({ ...state.faults, active: r.active }))
          : await control("/api/sim/fault", { method: "POST", body: { fault: f } }).then((r) => ({ ...state.faults, active: r.active }));
        renderDemo();
      }
    } catch { /* уже показали тост */ }
  });
  $("#ack-all").addEventListener("click", async () => { await control("/api/alarms/ack_all", { method: "POST" }).catch(() => {}); loadAlarms(); });
  $("#retrain").addEventListener("click", async () => { await control("/api/ml/retrain", { method: "POST" }).catch(() => {}); loadInsights(); });
  $("#demo-clear").addEventListener("click", async () => {
    const r = await control("/api/sim/fault", { method: "DELETE" }).catch(() => null);
    if (r) { state.faults.active = r.active; renderDemo(); }
  });
  $("#trend-picker").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    const k = b.dataset.key;
    state.trendSel = state.trendSel.includes(k) ? state.trendSel.filter((x) => x !== k) : [...state.trendSel, k].slice(-4);
    renderTrends(true);
  });
  $("#trend-range").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    state.trendRange = Number(b.dataset.range);
    renderTrends(true);
  });
  $("#gen-run").addEventListener("click", runGenerator);
  $("#gen-current").addEventListener("click", loadCurrentYaml);
  $("#gen-example").addEventListener("click", () => { $("#gen-yaml").value = BOILER_YAML; runGenerator(); });
  $("#gen-yaml").addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const ta = e.target;
      const s = ta.selectionStart;
      ta.value = `${ta.value.slice(0, s)}  ${ta.value.slice(ta.selectionEnd)}`;
      ta.selectionStart = ta.selectionEnd = s + 2;
    }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) runGenerator();
  });
  $("#gen-svg").addEventListener("click", () => download("hmi.svg", genMimic.toSVGString(), "image/svg+xml"));
  $("#gen-json").addEventListener("click", () => download("hmi.json", JSON.stringify(state.genLayout, null, 2), "application/json"));
  window.addEventListener("resize", () => { trendCharts.forEach((c) => c.draw()); drawerChart?.draw(); });
}

function renderAll() {
  applyStatic();
  renderHeader();
  mimic.render(state.hmi);
  if (state.snap) mimic.update(state.snap.tags);
  if (state.genLayout) { genMimic.render(state.genLayout); genMimic.update({}); }
  renderHealth();
  renderKPIs();
  renderOverviewLists();
  onWSState($("#conn").dataset.state || "connecting");
  refreshView();
  if (state.drawer) renderDrawer(true);
}

async function main() {
  setTheme(themeChoice());

  const [meta, hmi] = await Promise.all([api("/api/meta"), api("/api/hmi")]);
  state.meta = meta;
  state.hmi = hmi;
  for (const area of hmi.areas) {
    for (const w of [area.main, ...area.tiles].filter(Boolean)) state.tags.set(w.key, w);
  }
  await initI18n(meta.default_language);
  mimic = new Mimic($("#mimic"), { onSelect: openDrawer });
  genMimic = new Mimic($("#gen-mimic"), { interactive: false, preview: true });
  bindEvents();
  renderAll();
  showView(location.hash.slice(1) || "overview");
  connectWS(onMessage, onWSState);

  const tick = () => { $("#clock").textContent = fmtTime(Date.now() / 1000); };
  tick();
  setInterval(tick, 1000);
  setInterval(() => {
    if (document.hidden) return;
    if (state.view === "trends") renderTrends().catch(() => {});
    if (state.view === "alarms") loadAlarms().catch(() => {});
    if (state.view === "insights") loadInsights().catch(() => {});
    if (state.view === "demo") loadFaults().catch(() => {});
  }, 2000);
  setInterval(() => api("/api/alarms/kpi").then((k) => { state.kpi = k; renderKPIs(); }).catch(() => {}), 10000);
  api("/api/alarms/kpi").then((k) => { state.kpi = k; renderKPIs(); }).catch(() => {});
}

main().catch((e) => {
  document.body.insertAdjacentHTML("afterbegin", `<div style="padding:16px;background:#e5484d;color:#fff">Startup error: ${esc(e.message)}</div>`);
});
