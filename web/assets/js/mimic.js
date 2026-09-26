// Рендер сгенерированной мнемосхемы в SVG и её «оживление» данными.
import { fmtDuration, fmtNum, label, t } from "./i18n.js";

const NS = "http://www.w3.org/2000/svg";

function el(name, attrs = {}, parent = null, text = null) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  if (text !== null) node.textContent = text;
  if (parent) parent.appendChild(node);
  return node;
}

const span = (w) => {
  const lo = w.min ?? 0;
  const hi = w.max ?? (w.limits?.HH ?? w.limits?.H ?? 100);
  return [lo, hi > lo ? hi : lo + 1];
};
const frac = (w, v) => {
  const [lo, hi] = span(w);
  return Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
};

function drawTank(g, w) {
  const vx = w.x + 18, vy = w.y + 20, vw = 96, vh = 164;
  el("rect", { x: vx, y: vy, width: vw, height: vh, rx: 14, class: "vessel" }, g);
  const clipId = `clip-${w.key.replace(/\W/g, "_")}-${Math.random().toString(36).slice(2, 7)}`;
  const clip = el("clipPath", { id: clipId }, g);
  el("rect", { x: vx + 2, y: vy + 2, width: vw - 4, height: vh - 4, rx: 12 }, clip);
  const liquid = el("rect", { x: vx, y: vy + vh, width: vw, height: 0, class: "liquid", "clip-path": `url(#${clipId})` }, g);
  for (const [k, v] of Object.entries(w.limits || {})) {
    const yy = vy + vh - frac(w, v) * vh;
    el("line", { x1: vx + vw, y1: yy, x2: vx + vw + 8, y2: yy, class: "tick" }, g);
    el("text", { x: vx + vw + 11, y: yy + 3.5, class: "tick-label" }, g, k);
  }
  return { liquid, vy, vh };
}

function drawPump(g, w) {
  const cx = w.x + 70, cy = w.y + 104, r = 52;
  el("rect", { x: cx - 8, y: cy - r - 26, width: 16, height: 30, class: "pump-body" }, g);
  el("circle", { cx, cy, r, class: "pump-body" }, g);
  const imp = el("g", { class: "impeller" }, g);
  for (let i = 0; i < 3; i += 1) {
    const a = (i * 2 * Math.PI) / 3;
    const x1 = cx + Math.cos(a) * 36, y1 = cy + Math.sin(a) * 36;
    const x2 = cx + Math.cos(a + 0.9) * 14, y2 = cy + Math.sin(a + 0.9) * 14;
    el("path", { d: `M${cx} ${cy} L${x1} ${y1} Q${x2 + (x1 - cx) * 0.3} ${y2 + (y1 - cy) * 0.3} ${cx} ${cy}Z` }, imp);
  }
  el("circle", { cx, cy, r: 7 }, imp);
  el("rect", { x: w.x + 18, y: cy + r + 4, width: 104, height: 8, rx: 2, class: "pump-body" }, g);
  return { impeller: imp };
}

function drawValve(g, w) {
  const cx = w.x + 70, cy = w.y + 110;
  el("line", { x1: cx, y1: cy - 48, x2: cx, y2: cy, class: "tick", "stroke-width": 3 }, g);
  el("rect", { x: cx - 26, y: cy - 66, width: 52, height: 20, rx: 4, class: "pump-body" }, g);
  const body = el("path", { d: `M${cx - 46} ${cy - 30} L${cx + 46} ${cy + 30} L${cx + 46} ${cy - 30} L${cx - 46} ${cy + 30}Z`, class: "valve-body" }, g);
  return { body };
}

function drawGauge(g, w) {
  const cx = w.x + w.w / 2, cy = w.y + 128, r = 74;
  const arc = `M${cx - r} ${cy} A${r} ${r} 0 0 1 ${cx + r} ${cy}`;
  el("path", { d: arc, class: "gauge-track" }, g);
  const val = el("path", { d: arc, class: "gauge-val", "stroke-dasharray": `0 999` }, g);
  const len = Math.PI * r;
  for (const [k, v] of Object.entries(w.limits || {})) {
    const a = Math.PI * (1 - frac(w, v));
    const x1 = cx + Math.cos(a) * (r - 12), y1 = cy - Math.sin(a) * (r - 12);
    const x2 = cx + Math.cos(a) * (r + 12), y2 = cy - Math.sin(a) * (r + 12);
    el("line", { x1, y1, x2, y2, class: "gauge-lim" }, g);
    el("text", { x: cx + Math.cos(a) * (r + 22), y: cy - Math.sin(a) * (r + 22) + 4, class: "tick-label", "text-anchor": "middle" }, g, k);
  }
  const [lo, hi] = span(w);
  el("text", { x: cx - r, y: cy + 18, class: "tick-label", "text-anchor": "middle" }, g, fmtNum(lo, 0));
  el("text", { x: cx + r, y: cy + 18, class: "tick-label", "text-anchor": "middle" }, g, fmtNum(hi, 0));
  return { gauge: val, len };
}

function drawThermo(g, w) {
  const x = w.x + 50, y = w.y + 22, h = 150;
  el("rect", { x: x - 7, y, width: 14, height: h, rx: 7, class: "thermo-track" }, g);
  el("circle", { cx: x, cy: y + h + 12, r: 14, class: "thermo-fill" }, g);
  const fill = el("rect", { x: x - 4, y: y + h, width: 8, height: 0, rx: 4, class: "thermo-fill" }, g);
  for (const [k, v] of Object.entries(w.limits || {})) {
    const yy = y + h - frac(w, v) * h;
    el("line", { x1: x + 9, y1: yy, x2: x + 17, y2: yy, class: "tick" }, g);
    el("text", { x: x + 20, y: yy + 3.5, class: "tick-label" }, g, k);
  }
  return { thermo: fill, ty: y, th: h };
}

function drawFlow(g, w) {
  const y = w.y + 104;
  el("path", { d: `M${w.x + 16} ${y - 14} h78 v-14 l34 28 l-34 28 v-14 h-78z`, class: "pump-body" }, g);
  return {};
}

export class Mimic {
  constructor(svg, { onSelect = null, interactive = true, preview = false } = {}) {
    this.svg = svg;
    this.preview = preview;
    this.onSelect = onSelect;
    this.interactive = interactive;
    this.items = new Map();
    this.pipes = [];
  }

  render(layout) {
    this.layout = layout;
    const svg = this.svg;
    svg.innerHTML = "";
    this.items.clear();
    this.pipes = [];
    svg.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
    const defs = el("defs", {}, svg);
    const f = el("filter", { id: "glow", x: "-30%", y: "-30%", width: "160%", height: "160%" }, defs);
    el("feGaussianBlur", { stdDeviation: 9 }, f);

    for (const link of layout.links) {
      const [[x1, y1], [x2, y2]] = link.points;
      el("path", { d: `M${x1 - 12} ${y1} H${x2 + 12}`, class: "pipe" }, svg);
      this.pipes.push(el("path", { d: `M${x1 - 12} ${y1} H${x2 + 12}`, class: "pipe-flow" }, svg));
    }
    for (const area of layout.areas) {
      el("rect", { x: area.x, y: area.y, width: area.w, height: area.h, rx: 12, class: "area" }, svg);
      el("text", { x: area.x + 16, y: area.y + 26, class: "area-title" }, svg, label(area.label, area.id));
      if (area.main) this._widget(area.main, true);
      for (const tile of area.tiles) this._widget(tile, false);
    }
  }

  _widget(w, main) {
    const g = el("g", { class: "w", "data-key": w.key }, this.svg);
    el("rect", { x: w.x - 4, y: w.y - 4, width: w.w + 8, height: w.h + 8, rx: 10, class: "halo", filter: "url(#glow)" }, g);
    el("rect", { x: w.x - 4, y: w.y - 4, width: w.w + 8, height: w.h + 8, rx: 10, class: "frame" }, g);
    const refs = { g, w, main };
    if (main) {
      const extra = { tank: drawTank, pump: drawPump, valve: drawValve, gauge: drawGauge, thermometer: drawThermo, flow: drawFlow }[w.type];
      Object.assign(refs, extra ? extra(g, w) : {});
      const centered = w.type === "gauge";
      const tx = centered ? w.x + w.w / 2 : w.x + 150;
      const anchor = centered ? "middle" : "start";
      refs.label = el("text", { x: w.x + 2, y: w.y + 8, class: "lbl" }, g, label(w.label, w.tag));
      refs.value = el("text", { x: tx, y: centered ? w.y + 118 : w.y + 104, class: "big", "text-anchor": anchor }, g, "—");
      refs.unit = el("text", { x: tx, y: centered ? w.y + 136 : w.y + 124, class: "unit", "text-anchor": anchor }, g, w.unit || "");
    } else {
      el("rect", { x: w.x, y: w.y, width: w.w, height: w.h, rx: 7, class: "tile-bg" }, g);
      refs.label = el("text", { x: w.x + 10, y: w.y + 18, class: "lbl" }, g, label(w.label, w.tag));
      if (w.bit) {
        refs.pill = el("rect", { x: w.x + w.w - 56, y: w.y + 20, width: 46, height: 20, rx: 10, class: "pill" }, g);
        refs.value = el("text", { x: w.x + w.w - 33, y: w.y + 34, class: "pill-txt", "text-anchor": "middle" }, g, "—");
      } else {
        refs.value = el("text", { x: w.x + w.w - 10, y: w.y + 38, class: "tile-val", "text-anchor": "end" }, g, "—");
        refs.tileUnit = w.unit || "";
        if (w.min !== null && w.max !== null) {
          el("rect", { x: w.x + 10, y: w.y + w.h - 12, width: w.w - 110, height: 3, rx: 1.5, class: "mini-track" }, g);
          refs.mini = el("rect", { x: w.x + 10, y: w.y + w.h - 12, width: 0, height: 3, rx: 1.5, class: "mini-fill" }, g);
          refs.miniW = w.w - 110;
        }
      }
    }
    refs.badges = el("g", {}, g);
    if (this.interactive && this.onSelect) {
      g.addEventListener("click", () => this.onSelect(w));
      g.setAttribute("tabindex", "0");
      g.setAttribute("role", "button");
      g.addEventListener("keydown", (e) => { if (e.key === "Enter") this.onSelect(w); });
    }
    this.items.set(w.key, refs);
  }

  /** Применяет снимок состояния (snapshot.tags) к схеме. */
  update(tags) {
    let flowing = false;
    for (const [key, r] of this.items) {
      const s = tags?.[key];
      const w = r.w;
      const v = s?.value;
      const good = s && s.quality === "GOOD" && v !== null && v !== undefined;
      r.g.classList.toggle("comm", !good && !this.preview);
      if (this.preview) {
        r.value.textContent = "—";
        continue;
      }

      if (w.bit) {
        const on = good && v >= 0.5;
        if (r.pill) r.pill.classList.toggle("on", on);
        const [onTxt, offTxt] = w.type === "valve" ? ["tag.open", "tag.closed"] : ["tag.on", "tag.off"];
        r.value.textContent = good ? t(on ? onTxt : offTxt) : "?";
        if (r.body) r.body.classList.toggle("closed", !on);
      } else if (r.tileUnit !== undefined) {
        r.value.textContent = good ? fmtNum(v, w.decimals) : "?";
        if (r.tileUnit) el("tspan", { class: "unit", dx: 4 }, r.value, r.tileUnit);
      } else {
        r.value.textContent = good ? fmtNum(v, w.decimals) : "?";
      }
      if (good && !w.bit) {
        const fr = frac(w, v);
        if (r.liquid) {
          const h = fr * (r.vh - 4);
          r.liquid.setAttribute("y", r.vy + r.vh - 2 - h);
          r.liquid.setAttribute("height", h);
        }
        if (r.mini) r.mini.setAttribute("width", Math.max(1, fr * r.miniW));
        if (r.gauge) r.gauge.setAttribute("stroke-dasharray", `${fr * r.len} 999`);
        if (r.thermo) {
          const h = fr * r.th;
          r.thermo.setAttribute("y", r.ty + r.th - h);
          r.thermo.setAttribute("height", h);
        }
        if (r.impeller) {
          const running = v > 1;
          r.impeller.classList.toggle("spin", running);
          if (running) r.impeller.style.setProperty("--spin", `${Math.max(0.25, 900 / v)}s`);
          flowing = flowing || running;
        }
        if (w.type === "flow" && v > 0.5) flowing = true;
      }

      // алармы: цвет и форма иконки по приоритету (ISA-101)
      const alarm = s?.alarm;
      r.g.classList.remove("alarm-high", "alarm-medium", "alarm-low", "unack");
      if (alarm) {
        r.g.classList.add(`alarm-${alarm.priority}`);
        if (alarm.state === "ACTIVE_UNACK" || alarm.state === "RTN_UNACK") r.g.classList.add("unack");
      }
      const ml = s?.ml;
      r.g.classList.toggle("anomaly", Boolean(ml && ml.ready && ml.score >= 0.5));
      this._badges(r, alarm, ml);
    }
    for (const p of this.pipes) p.classList.toggle("on", flowing);
  }

  _badges(r, alarm, ml) {
    const w = r.w;
    const sig = `${alarm ? alarm.priority + alarm.kind : ""}|${ml?.eta_s ? Math.round(ml.eta_s / 5) : ""}`;
    if (r.badgeSig === sig) return;
    r.badgeSig = sig;
    r.badges.innerHTML = "";
    const right = w.x + w.w + 4;
    if (alarm) {
      const cx = right - 2, cy = w.y - 2;
      const shape = alarm.priority === "high"
        ? el("path", { d: `M${cx} ${cy - 10} L${cx + 10} ${cy + 8} L${cx - 10} ${cy + 8}Z` }, r.badges)
        : alarm.priority === "medium"
          ? el("rect", { x: cx - 8, y: cy - 8, width: 16, height: 16, transform: `rotate(45 ${cx} ${cy})` }, r.badges)
          : el("circle", { cx, cy, r: 9 }, r.badges);
      shape.setAttribute("class", `alarm-icon ${alarm.priority}`);
      const num = { high: "1", medium: "2", low: "3" }[alarm.priority] || "";
      el("text", { x: cx, y: cy + (alarm.priority === "high" ? 6 : 3.5), class: "alarm-icon-txt", "text-anchor": "middle" }, r.badges, num);
    }
    if (ml && ml.eta_s) {
      const txt = `⏱ ${fmtDuration(ml.eta_s)} → ${ml.eta_kind}`;
      const bw = txt.length * 6.4 + 14;
      // У главного элемента — над рамкой, у плитки — внутри, справа сверху.
      const bx = r.main ? right - bw - (alarm ? 16 : 0) : w.x + w.w - bw - 6;
      const by = r.main ? w.y - 13 : w.y + 4;
      el("rect", { x: bx, y: by, width: bw, height: 17, rx: 8.5, class: "badge-bg" }, r.badges);
      el("text", { x: bx + bw / 2, y: by + 12.5, class: "badge-txt", "text-anchor": "middle" }, r.badges, txt);
    }
  }

  /** Сериализация текущей схемы в самостоятельный SVG-файл. */
  toSVGString() {
    const clone = this.svg.cloneNode(true);
    clone.setAttribute("xmlns", NS);
    const css = getComputedStyle(document.documentElement);
    const vars = ["--panel", "--panel-2", "--line", "--line-2", "--text", "--muted", "--faint", "--proc", "--proc-fill", "--liquid", "--ml", "--high", "--medium", "--low"]
      .map((v) => `${v}:${css.getPropertyValue(v)}`).join(";");
    const rules = [...document.styleSheets]
      .flatMap((sh) => { try { return [...sh.cssRules]; } catch { return []; } })
      .map((r) => r.cssText).filter((c) => c.startsWith(".mimic")).join("\n");
    const style = document.createElementNS(NS, "style");
    style.textContent = `svg{${vars};font-family:sans-serif;background:${css.getPropertyValue("--panel")}}\n${rules}`;
    clone.insertBefore(style, clone.firstChild);
    clone.classList.add("mimic");
    return new XMLSerializer().serializeToString(clone);
  }
}
