// Лёгкий тренд на canvas без внешних библиотек: значение, «коридор
// нормы» от ИИ, пороги алармов, точки аномалий и прогноз до порога.
import { fmtNum, fmtTime } from "./i18n.js";

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const LIMIT_COLOR = { HH: "--high", LL: "--high", H: "--medium", L: "--medium" };

export class TrendChart {
  constructor(canvas) {
    this.canvas = canvas;
    this.hover = null;
    canvas.addEventListener("mousemove", (e) => {
      const r = canvas.getBoundingClientRect();
      this.hover = e.clientX - r.left;
      this.draw();
    });
    canvas.addEventListener("mouseleave", () => { this.hover = null; this.draw(); });
  }

  set(data, opts) {
    this.data = data;
    this.opts = opts;
    this.draw();
  }

  draw() {
    const { canvas, data, opts } = this;
    if (!data || !opts) return;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth, H = canvas.clientHeight;
    if (!W || !H) return;
    if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const pad = { l: 48, r: 14, t: 10, b: 22 };
    const now = opts.now;
    const fc = opts.forecast;
    const future = fc ? Math.min(fc.eta_s, opts.range * 0.5) : 0;
    const x0 = now - opts.range, x1 = now + future;

    const vals = [];
    data.value.forEach((v, i) => {
      if (v !== null && data.ts[i] >= x0) vals.push(v);
      if (data.band_low?.[i] != null && data.ts[i] >= x0) vals.push(data.band_low[i], data.band_high[i]);
    });
    for (const v of Object.values(opts.limits || {})) vals.push(v);
    if (fc) vals.push(fc.limit);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
    if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
    const m = (hi - lo) * 0.08; lo -= m; hi += m;
    // Не показываем физически невозможное (например, отрицательный расход).
    if (opts.min !== null && opts.min !== undefined) lo = Math.max(lo, opts.min - m * 0.5);
    if (opts.max !== null && opts.max !== undefined) hi = Math.min(hi, opts.max + m * 0.5);

    const X = (ts) => pad.l + ((ts - x0) / (x1 - x0)) * (W - pad.l - pad.r);
    const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
    const text = css("--text"), muted = css("--muted"), line = css("--line"), ml = css("--ml");

    // сетка
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillStyle = muted;
    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i += 1) {
      const v = lo + ((hi - lo) * i) / 4;
      const y = Math.round(Y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
      ctx.textAlign = "right";
      ctx.fillText(fmtNum(v, opts.decimals ?? 1), pad.l - 6, y + 3.5);
    }
    ctx.textAlign = "center";
    for (let i = 0; i <= 3; i += 1) {
      const ts = x0 + ((now - x0) * i) / 3;
      ctx.fillText(fmtTime(ts, false), X(ts), H - 6);
    }

    // всё, что ниже, обрезаем по области графика
    ctx.save();
    ctx.beginPath();
    ctx.rect(pad.l, pad.t, W - pad.l - pad.r, H - pad.t - pad.b);
    ctx.clip();

    // коридор нормы (ИИ)
    ctx.fillStyle = "rgba(168,121,255,0.14)";
    ctx.beginPath();
    let started = false;
    const idx = data.ts.map((ts, i) => i).filter((i) => data.ts[i] >= x0 && data.band_low?.[i] != null);
    idx.forEach((i) => { const x = X(data.ts[i]), y = Y(data.band_high[i]); if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y); });
    [...idx].reverse().forEach((i) => ctx.lineTo(X(data.ts[i]), Y(data.band_low[i])));
    if (started) { ctx.closePath(); ctx.fill(); }

    // пороги
    for (const [k, v] of Object.entries(opts.limits || {})) {
      const y = Math.round(Y(v)) + 0.5;
      ctx.strokeStyle = css(LIMIT_COLOR[k] || "--muted");
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = ctx.strokeStyle;
      ctx.textAlign = "left";
      ctx.fillText(k, pad.l + 4, y - 4);
    }

    // значение
    ctx.strokeStyle = text;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    let pen = false, last = null;
    data.ts.forEach((ts, i) => {
      const v = data.value[i];
      if (ts < x0 || v === null) { pen = false; return; }
      const x = X(ts), y = Y(v);
      if (!pen) { ctx.moveTo(x, y); pen = true; } else ctx.lineTo(x, y);
      last = { ts, v };
    });
    ctx.stroke();

    // аномалии
    ctx.fillStyle = ml;
    data.ts.forEach((ts, i) => {
      if (ts >= x0 && data.score?.[i] >= 0.5 && data.value[i] !== null) {
        ctx.beginPath(); ctx.arc(X(ts), Y(data.value[i]), 3.2, 0, Math.PI * 2); ctx.fill();
      }
    });

    // прогноз
    if (fc && last) {
      const tEnd = last.ts + fc.eta_s;
      const xEnd = Math.min(X(tEnd), W - pad.r);
      const vEnd = last.v + ((fc.limit - last.v) * (xEnd - X(last.ts))) / Math.max(1, X(tEnd) - X(last.ts));
      ctx.strokeStyle = ml; ctx.lineWidth = 2; ctx.setLineDash([6, 5]);
      ctx.beginPath(); ctx.moveTo(X(last.ts), Y(last.v)); ctx.lineTo(xEnd, Y(vEnd)); ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.arc(xEnd, Y(vEnd), 4, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = line; ctx.beginPath(); ctx.moveTo(X(now) + 0.5, pad.t); ctx.lineTo(X(now) + 0.5, H - pad.b); ctx.stroke();
    }

    ctx.restore();

    // подсказка при наведении
    if (this.hover !== null && this.hover > pad.l) {
      const tsAt = x0 + ((this.hover - pad.l) / (W - pad.l - pad.r)) * (x1 - x0);
      let best = -1, bd = Infinity;
      data.ts.forEach((ts, i) => { const d = Math.abs(ts - tsAt); if (d < bd && data.value[i] !== null) { bd = d; best = i; } });
      if (best >= 0 && data.ts[best] >= x0) {
        const x = X(data.ts[best]), y = Y(data.value[best]);
        ctx.strokeStyle = muted; ctx.beginPath(); ctx.moveTo(x + 0.5, pad.t); ctx.lineTo(x + 0.5, H - pad.b); ctx.stroke();
        ctx.fillStyle = text; ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.fill();
        const label = `${fmtTime(data.ts[best])}  ${fmtNum(data.value[best], opts.decimals ?? 1)} ${opts.unit || ""}`;
        ctx.font = "12px system-ui, sans-serif";
        const tw = ctx.measureText(label).width + 12;
        const bx = Math.min(Math.max(x - tw / 2, pad.l), W - pad.r - tw);
        ctx.fillStyle = css("--panel-2"); ctx.strokeStyle = line;
        ctx.fillRect(bx, pad.t, tw, 20); ctx.strokeRect(bx + 0.5, pad.t + 0.5, tw, 20);
        ctx.fillStyle = text; ctx.textAlign = "left"; ctx.fillText(label, bx + 6, pad.t + 14);
      }
    }
  }
}
