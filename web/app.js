// Combat Sports Training Intelligence — dashboard rendering.
// Hand-rolled SVG charts (no charting library) reading the arrays in data.js.

const SVGNS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function fmtWeek(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// ---------------- Theme toggle ----------------
(function initTheme() {
  const btn = document.getElementById("themeToggle");
  const order = ["auto", "light", "dark"];
  let saved = "auto";
  try { saved = localStorage.getItem("csti-theme") || "auto"; } catch (e) {}
  function apply(mode) {
    if (mode === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", mode);
    btn.textContent = "Theme: " + mode[0].toUpperCase() + mode.slice(1);
  }
  apply(saved);
  btn.addEventListener("click", () => {
    saved = order[(order.indexOf(saved) + 1) % order.length];
    apply(saved);
    try { localStorage.setItem("csti-theme", saved); } catch (e) {}
    // theme swap changes CSS var values — redraw the SVG charts
    renderTrendChart();
    renderBeltChart();
    renderVolumeChart();
  });
})();

// ---------------- Stat tiles ----------------
function renderStats() {
  const row = document.getElementById("statRow");
  const tiles = [
    { num: STATS.sessions, label: "Sessions logged" },
    { num: STATS.exchanges, label: "Exchanges recorded" },
    { num: STATS.weeks, label: "Weeks covered" },
    { num: STATS.avgPerWeek.toFixed(1), label: "Avg sessions / week" },
  ];
  row.innerHTML = tiles.map(t =>
    `<div class="stat-tile"><span class="num">${t.num}</span><span class="label">${t.label}</span></div>`
  ).join("");
}

// ---------------- Trend chart (line + area + weighted regression) ----------------
function weightedLinReg(xs, ys, ws) {
  let sw = 0, swx = 0, swy = 0, swxy = 0, swxx = 0;
  for (let i = 0; i < xs.length; i++) {
    const w = ws[i];
    sw += w; swx += w * xs[i]; swy += w * ys[i];
    swxy += w * xs[i] * ys[i]; swxx += w * xs[i] * xs[i];
  }
  const denom = sw * swxx - swx * swx;
  const slope = denom !== 0 ? (sw * swxy - swx * swy) / denom : 0;
  const intercept = (swy - slope * swx) / sw;
  return { slope, intercept };
}

function renderTrendChart() {
  const svg = document.getElementById("trendChart");
  svg.innerHTML = "";
  const W = 1080, H = 340, M = { top: 16, right: 18, bottom: 34, left: 40 };
  const plotW = W - M.left - M.right, plotH = H - M.top - M.bottom;
  const data = TAKEDOWN_TREND;
  const n = data.length;

  const grid = cssVar("--grid"), axis = cssVar("--axis"), muted = cssVar("--muted");
  const accent = cssVar("--accent"), ink2 = cssVar("--ink-2");

  const x = i => M.left + (plotW * i) / (n - 1);
  const y = v => M.top + plotH * (1 - v / 100);

  // gridlines + y labels
  [0, 25, 50, 75, 100].forEach(v => {
    el("line", { x1: M.left, x2: W - M.right, y1: y(v), y2: y(v), class: "grid-line", stroke: grid }, svg);
    el("text", { x: M.left - 10, y: y(v) + 4, "text-anchor": "end", class: "axis-label", fill: muted }, svg).textContent = v + "%";
  });
  el("line", { x1: M.left, x2: W - M.right, y1: y(0), y2: y(0), stroke: axis, class: "baseline" }, svg);

  // x labels — every 3rd week to avoid crowding
  data.forEach((d, i) => {
    if (i % 3 === 0 || i === n - 1) {
      const t = el("text", { x: x(i), y: H - 10, "text-anchor": "middle", class: "axis-label", fill: muted }, svg);
      t.textContent = fmtWeek(d.start);
    }
  });

  // weighted regression trend line
  const xs = data.map((_, i) => i), ys = data.map(d => d.rate), ws = data.map(d => d.attempts);
  const { slope, intercept } = weightedLinReg(xs, ys, ws);
  const trendY0 = Math.max(0, Math.min(100, intercept));
  const trendY1 = Math.max(0, Math.min(100, slope * (n - 1) + intercept));
  el("line", {
    x1: x(0), y1: y(trendY0), x2: x(n - 1), y2: y(trendY1),
    stroke: ink2, "stroke-width": 2, "stroke-dasharray": "5 5", opacity: 0.55,
  }, svg);

  // area fill
  let areaPath = `M ${x(0)} ${y(0)} `;
  data.forEach((d, i) => { areaPath += `L ${x(i)} ${y(d.rate)} `; });
  areaPath += `L ${x(n - 1)} ${y(0)} Z`;
  el("path", { d: areaPath, fill: accent, opacity: 0.10, stroke: "none" }, svg);

  // line
  let linePath = "";
  data.forEach((d, i) => { linePath += (i === 0 ? "M" : "L") + ` ${x(i)} ${y(d.rate)} `; });
  el("path", { d: linePath, fill: "none", stroke: accent, "stroke-width": 2.5 }, svg);

  // points, radius scaled by attempts (sample size visible in the mark itself)
  const maxAttempts = Math.max(...data.map(d => d.attempts));
  data.forEach((d, i) => {
    const r = 3 + 6 * Math.sqrt(d.attempts / maxAttempts);
    el("circle", { cx: x(i), cy: y(d.rate), r, fill: accent, stroke: cssVar("--surface"), "stroke-width": 1.5 }, svg);
  });

  // hover interaction
  const tooltip = document.getElementById("trendTooltip");
  const hoverLine = el("line", { x1: 0, x2: 0, y1: M.top, y2: H - M.bottom, stroke: ink2, "stroke-width": 1, opacity: 0 }, svg);
  const hoverDot = el("circle", { r: 6, fill: "none", stroke: accent, "stroke-width": 2, opacity: 0 }, svg);
  const overlay = el("rect", { x: M.left, y: M.top, width: plotW, height: plotH, fill: "transparent" }, svg);

  overlay.addEventListener("mousemove", (evt) => {
    const rect = svg.getBoundingClientRect();
    const scaleX = W / rect.width;
    const mx = (evt.clientX - rect.left) * scaleX;
    let i = Math.round(((mx - M.left) / plotW) * (n - 1));
    i = Math.max(0, Math.min(n - 1, i));
    const d = data[i];
    hoverLine.setAttribute("x1", x(i)); hoverLine.setAttribute("x2", x(i)); hoverLine.setAttribute("opacity", 0.5);
    hoverDot.setAttribute("cx", x(i)); hoverDot.setAttribute("cy", y(d.rate)); hoverDot.setAttribute("opacity", 1);

    const svgRect = svg.getBoundingClientRect();
    const px = svgRect.left + (x(i) / W) * svgRect.width;
    const py = svgRect.top + (y(d.rate) / H) * svgRect.height + window.scrollY;
    tooltip.style.left = (px - svgRect.left) + "px";
    tooltip.style.top = (py - svgRect.top - window.scrollY) + "px";
    tooltip.innerHTML = `week of ${fmtWeek(d.start)}<br>${d.rate.toFixed(1)}% success<br>n = ${d.attempts} attempts`;
    tooltip.classList.add("show");
  });
  overlay.addEventListener("mouseleave", () => {
    hoverLine.setAttribute("opacity", 0);
    hoverDot.setAttribute("opacity", 0);
    tooltip.classList.remove("show");
  });
}

// ---------------- Position list (HTML bars, not SVG — simpler for a ranked list) ----------------
function renderPositionList() {
  const container = document.getElementById("positionList");
  const maxFinished = Math.max(...POSITIONS.map(p => p.finished));
  const critical = cssVar("--critical");
  const notablePositions = new Set(["mount"]); // flagged by analysis/trend_analysis.py flag_notable_positions()

  container.innerHTML = POSITIONS.map(p => {
    const pct = (p.finished / maxFinished) * 100;
    const isNotable = notablePositions.has(p.position);
    return `
      <div class="position-row${isNotable ? " notable" : ""}" title="${p.against} submission attempts against ${p.position.replace(/_/g, " ")}, ${p.finished} finished (${p.finishRate.toFixed(1)}%)">
        <span class="pname">${p.position.replace(/_/g, " ")}${isNotable ? ' <span class="badge-notable"><svg viewBox="0 0 16 16"><path d="M8 1 L15 14 L1 14 Z" fill="none" stroke="currentColor" stroke-width="1.4"/><line x1="8" y1="6" x2="8" y2="9.5" stroke="currentColor" stroke-width="1.4"/><circle cx="8" cy="11.5" r="0.8" fill="currentColor"/></svg>notable</span>' : ""}</span>
        <span class="bartrack"><span class="barfill" style="width:${pct}%; background:${critical};"></span></span>
        <span class="count">${p.finished}<span class="share">${p.share.toFixed(1)}% share</span></span>
      </div>`;
  }).join("");
}

// ---------------- Belt grouped bar chart ----------------
function renderBeltChart() {
  const svg = document.getElementById("beltChart");
  svg.innerHTML = "";
  const W = 460, H = 300, M = { top: 16, right: 12, bottom: 54, left: 34 };
  const plotW = W - M.left - M.right, plotH = H - M.top - M.bottom;
  const n = BELTS.length;
  const groupW = plotW / n;
  const barW = groupW * 0.30;

  const grid = cssVar("--grid"), axis = cssVar("--axis"), muted = cssVar("--muted"), ink = cssVar("--ink");
  const accent = cssVar("--accent"), measure2 = cssVar("--measure-2");
  const beltColor = {
    white: cssVar("--belt-white"), blue: cssVar("--belt-blue"), purple: cssVar("--belt-purple"),
    brown: cssVar("--belt-brown"), black: cssVar("--belt-black"),
  };
  const beltLine = { white: cssVar("--belt-white-line") };

  const y = v => M.top + plotH * (1 - v / 100);
  [0, 25, 50, 75, 100].forEach(v => {
    el("line", { x1: M.left, x2: W - M.right, y1: y(v), y2: y(v), stroke: grid }, svg);
    el("text", { x: M.left - 8, y: y(v) + 4, "text-anchor": "end", class: "axis-label", fill: muted }, svg).textContent = v;
  });
  el("line", { x1: M.left, x2: W - M.right, y1: y(0), y2: y(0), stroke: axis }, svg);

  const tooltip = document.getElementById("beltTooltip");
  const svgRectFor = () => svg.getBoundingClientRect();

  function addBar(cx, value, color, label) {
    const bx = cx - barW / 2;
    const by = y(value), bh = y(0) - y(value);
    const rect = el("rect", { x: bx, y: by, width: barW, height: Math.max(bh, 1), rx: 3, fill: color }, svg);
    rect.style.cursor = "pointer";
    rect.addEventListener("mousemove", (evt) => {
      const r = svgRectFor();
      const px = ((cx) / W) * r.width;
      const py = ((by) / H) * r.height;
      tooltip.style.left = px + "px";
      tooltip.style.top = py + "px";
      tooltip.innerHTML = label;
      tooltip.classList.add("show");
    });
    rect.addEventListener("mouseleave", () => tooltip.classList.remove("show"));
  }

  BELTS.forEach((b, i) => {
    const cx = M.left + groupW * i + groupW / 2;
    addBar(cx - barW * 0.55, b.subRate, accent,
      `${b.belt} belt — submission<br>${b.subRate.toFixed(1)}% (n = ${b.subAttempts})`);
    addBar(cx + barW * 0.55, b.tdRate, measure2,
      `${b.belt} belt — takedown<br>${b.tdRate.toFixed(1)}% (n = ${b.tdAttempts})`);

    // belt-literal chip + label under group
    const chipY = H - M.bottom + 18;
    const chip = el("rect", {
      x: cx - 5, y: chipY - 9, width: 10, height: 10, rx: 2,
      fill: beltColor[b.belt], stroke: beltLine[b.belt] || "none", "stroke-width": 1,
    }, svg);
    const label = el("text", {
      x: cx, y: chipY + 16, "text-anchor": "middle", class: "axis-label", fill: ink,
    }, svg);
    label.style.fontSize = "11px";
    label.textContent = b.belt;
  });
}

// ---------------- Volume chart ----------------
function renderVolumeChart() {
  const svg = document.getElementById("volumeChart");
  svg.innerHTML = "";
  const W = 1080, H = 260, M = { top: 16, right: 18, bottom: 34, left: 34 };
  const plotW = W - M.left - M.right, plotH = H - M.top - M.bottom;
  const data = VOLUME;
  const n = data.length;
  const maxSessions = Math.max(...data.map(d => d.sessions));
  const yMax = Math.ceil(maxSessions * 1.15);

  const grid = cssVar("--grid"), axis = cssVar("--axis"), muted = cssVar("--muted");
  const accent = cssVar("--accent"), ink2 = cssVar("--ink-2");

  const barSlot = plotW / n;
  const barW = barSlot * 0.62;
  const y = v => M.top + plotH * (1 - v / yMax);
  const x = i => M.left + barSlot * i + barSlot / 2;

  for (let v = 0; v <= yMax; v += Math.max(1, Math.round(yMax / 4))) {
    el("line", { x1: M.left, x2: W - M.right, y1: y(v), y2: y(v), stroke: grid }, svg);
    el("text", { x: M.left - 8, y: y(v) + 4, "text-anchor": "end", class: "axis-label", fill: muted }, svg).textContent = v;
  }
  el("line", { x1: M.left, x2: W - M.right, y1: y(0), y2: y(0), stroke: axis }, svg);

  // average reference line
  el("line", {
    x1: M.left, x2: W - M.right, y1: y(STATS.avgPerWeek), y2: y(STATS.avgPerWeek),
    stroke: ink2, "stroke-width": 1.5, "stroke-dasharray": "5 5", opacity: 0.6,
  }, svg);

  const tooltip = document.getElementById("volumeTooltip");
  data.forEach((d, i) => {
    const bx = x(i) - barW / 2;
    const by = y(d.sessions);
    const rect = el("rect", { x: bx, y: by, width: barW, height: Math.max(y(0) - by, 1), rx: 3, fill: accent, opacity: 0.85 }, svg);
    rect.style.cursor = "pointer";
    rect.addEventListener("mousemove", () => {
      const r = svg.getBoundingClientRect();
      const px = (x(i) / W) * r.width;
      const py = (by / H) * r.height;
      tooltip.style.left = px + "px";
      tooltip.style.top = py + "px";
      tooltip.innerHTML = `week of ${fmtWeek(d.start)}<br>${d.sessions} session${d.sessions === 1 ? "" : "s"} · ${d.minutes} min<br>${d.rounds} rounds total`;
      tooltip.classList.add("show");
    });
    rect.addEventListener("mouseleave", () => tooltip.classList.remove("show"));

    if (i % 3 === 0 || i === n - 1) {
      const t = el("text", { x: x(i), y: H - 10, "text-anchor": "middle", class: "axis-label", fill: muted }, svg);
      t.textContent = fmtWeek(d.start);
    }
  });
}

renderStats();
renderTrendChart();
renderPositionList();
renderBeltChart();
renderVolumeChart();
window.addEventListener("resize", () => { renderTrendChart(); renderBeltChart(); renderVolumeChart(); });
