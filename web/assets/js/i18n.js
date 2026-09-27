// Локализация: RU (основной), EN, ZH. Словари — web/i18n/*.json.
export const LANGS = ["ru", "en", "zh"];
const LOCALES = { ru: "ru-RU", en: "en-GB", zh: "zh-CN" };
const dicts = {};
let lang = "ru";

function store(key, value) {
  try { localStorage.setItem(key, value); } catch { /* приватный режим — не критично */ }
}
function load(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

export async function initI18n(defaultLang) {
  const saved = load("scada.lang");
  const fromBrowser = (navigator.language || "").slice(0, 2);
  const initial = LANGS.includes(saved) ? saved : LANGS.includes(defaultLang) ? defaultLang : fromBrowser;
  await setLang(LANGS.includes(initial) ? initial : "ru");
}

export async function setLang(next) {
  if (!dicts[next]) {
    const res = await fetch(`/i18n/${next}.json`);
    dicts[next] = await res.json();
  }
  if (!dicts.ru && next !== "ru") {
    dicts.ru = await (await fetch("/i18n/ru.json")).json();
  }
  lang = next;
  store("scada.lang", next);
  document.documentElement.lang = next === "zh" ? "zh-CN" : next;
  applyStatic();
}

export const getLang = () => lang;
export const locale = () => LOCALES[lang];

/** Перевод по ключу с подстановкой {параметров}. */
export function t(key, params = {}) {
  const s = dicts[lang]?.[key] ?? dicts.ru?.[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (_, k) => (params[k] ?? params[k] === 0 ? String(params[k]) : ""));
}

export function has(key) {
  return Boolean(dicts[lang]?.[key] ?? dicts.ru?.[key]);
}

export function applyStatic(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  root.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.dataset.i18nTitle);
    el.setAttribute("aria-label", el.title);
  });
}

/** Текст из словаря конфигурации {ru, en, zh}. */
export function label(obj, fallback = "") {
  if (!obj) return fallback;
  if (typeof obj === "string") return obj;
  return obj[lang] || obj.ru || obj.en || fallback;
}

export function fmtNum(v, decimals = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return new Intl.NumberFormat(locale(), {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(v);
}

export function fmtTime(ts, withSeconds = true) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString(locale(), {
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
  });
}

/** 252 -> «4 мин 12 с» / «4 min 12 s» / «4分12秒». */
export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const sep = lang === "zh" ? "" : " ";
  const parts = [];
  if (h) parts.push(`${h}${sep}${t("common.h")}`);
  if (m || h) parts.push(`${m}${sep}${t("common.min")}`);
  if (!h) parts.push(`${sec}${sep}${t("common.s")}`);
  return parts.join(lang === "zh" ? "" : " ");
}
