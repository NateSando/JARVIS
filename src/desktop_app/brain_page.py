"""🧠 Brain view: the Obsidian vault as a living force-directed graph.

Served by the memory viewer at ``/cerebro``. Self-contained (no external
scripts) so it works fully offline. See ``jarvis/brain/brain.spec.md``.
"""

from __future__ import annotations

from desktop_app.themes import COLORS

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🧠 Cérebro do J.A.R.V.I.S.</title>
<link rel="icon" href="data:,">
<style>
  :root {
    --bg: __bg_primary__;
    --panel: rgba(18, 20, 26, 0.86);
    --border: __border__;
    --text: __text_primary__;
    --text-2: __text_secondary__;
    --muted: __text_muted__;
    --accent: __accent_primary__;
    --accent-2: __accent_secondary__;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; overflow: hidden; background: var(--bg); color: var(--text);
    font-family: "Outfit", "Segoe UI", system-ui, -apple-system, sans-serif; }
  canvas { position: fixed; inset: 0; width: 100%; height: 100%; display: block; cursor: grab;
    background: radial-gradient(ellipse at center, #11131b 0%, var(--bg) 75%); }
  canvas.dragging { cursor: grabbing; }
  canvas.on-node { cursor: pointer; }
  .panel { position: fixed; background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
    backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); box-shadow: 0 10px 40px rgba(0,0,0,.45); }
  #top { top: 16px; left: 16px; padding: 14px 16px; width: min(360px, calc(100vw - 32px)); }
  #top h1 { margin: 0 0 4px; font-size: 18px; font-weight: 600; letter-spacing: .3px; }
  #top h1 span { color: var(--accent-2); }
  #stats { color: var(--muted); font-size: 12px; margin-bottom: 10px; }
  #search { width: 100%; padding: 9px 12px; border-radius: 10px; border: 1px solid var(--border);
    background: rgba(10,11,15,.7); color: var(--text); font: inherit; font-size: 14px; outline: none; }
  #search:focus { border-color: var(--accent); }
  .row { display: flex; gap: 8px; margin-top: 10px; }
  .btn { flex: 1; padding: 7px 10px; border-radius: 9px; border: 1px solid var(--border); background: rgba(26,29,38,.8);
    color: var(--text-2); font: inherit; font-size: 12px; cursor: pointer; text-align: center; text-decoration: none; }
  .btn:hover { color: var(--text); border-color: var(--accent); }
  #legend { left: 16px; bottom: 16px; padding: 10px 12px; max-height: 42vh; overflow: auto; width: min(300px, calc(100vw - 32px)); }
  #legend h2 { cursor: pointer; user-select: none; }
  #legend.collapsed #areas { display: none; }
  #legend h2 { margin: 0 0 6px; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); font-weight: 500; }
  .area { display: flex; align-items: center; gap: 8px; padding: 4px 6px; border-radius: 8px; cursor: pointer; font-size: 13px; color: var(--text-2); user-select: none; }
  .area:hover { background: rgba(255,255,255,.05); color: var(--text); }
  .area.off { opacity: .35; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; box-shadow: 0 0 8px currentColor; }
  .area .n { margin-left: auto; color: var(--muted); font-size: 11px; }
  #card { right: 16px; top: 16px; width: min(380px, calc(100vw - 32px)); max-height: calc(100vh - 32px); overflow: auto;
    padding: 16px 18px; display: none; }
  #card.show { display: block; }
  #card h3 { margin: 0 0 6px; font-size: 19px; font-weight: 600; word-break: break-word; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
  .chip { font-size: 11px; padding: 3px 9px; border-radius: 999px; border: 1px solid var(--border); color: var(--text-2); }
  #card h4 { margin: 14px 0 6px; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); font-weight: 500; }
  #card ul { margin: 0; padding-left: 18px; }
  #card li { margin: 4px 0; font-size: 14px; line-height: 1.45; color: var(--text); }
  .links { display: flex; flex-wrap: wrap; gap: 6px; }
  .link { font-size: 12px; padding: 3px 9px; border-radius: 8px; background: rgba(255,255,255,.05); cursor: pointer; color: var(--text-2); }
  .link:hover { color: var(--text); background: rgba(245,158,11,.15); }
  .hint { color: var(--muted); font-size: 12px; margin-top: 12px; }
  #empty { position: fixed; inset: 0; display: none; align-items: center; justify-content: center; text-align: center; color: var(--text-2); padding: 24px; }
  #empty.show { display: flex; }
  #empty div { max-width: 460px; line-height: 1.6; }
  #empty b { color: var(--accent-2); font-size: 42px; display: block; margin-bottom: 8px; }
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="empty"><div><b>🧠</b><span id="empty-msg">Carregando o cérebro…</span></div></div>
<div id="top" class="panel">
  <h1>🧠 Cérebro do <span>J.A.R.V.I.S.</span></h1>
  <div id="stats">Carregando…</div>
  <input id="search" type="search" placeholder="🔎 Buscar no cérebro…" autocomplete="off">
  <div class="row">
    <button class="btn" id="refresh" title="Sincroniza com o Obsidian">⟳ Atualizar</button>
    <button class="btn" id="fit" title="Enquadrar tudo">⤢ Enquadrar</button>
    <a class="btn" href="/" title="Voltar à memória">← Memória</a>
  </div>
</div>
<div id="legend" class="panel"><h2 id="legend-toggle" title="Mostrar/ocultar">🎨 Áreas ▾</h2><div id="areas"></div></div>
<div id="card" class="panel"></div>

<script>
(() => {
  "use strict";
  const KIND = { hub: "🧠 Núcleo", area: "🎯 Habilidade", memory: "💾 Memória aprendida", note: "📝 Nota do Obsidian", concept: "✨ Conceito" };
  const BASE_R = { hub: 15, area: 10, memory: 5.5, note: 5, concept: 3.2 };
  const HUB_COLOUR = "__accent_primary__";

  const canvas = document.getElementById("c");
  const ctx = canvas.getContext("2d");
  const card = document.getElementById("card");
  const search = document.getElementById("search");

  let W = 0, H = 0, DPR = 1;
  let nodes = [], links = [], areas = [], byId = new Map(), byLabel = new Map(), neighbours = new Map();
  let areaColour = new Map(), hiddenAreas = new Set();
  let view = { x: 0, y: 0, k: 1 }, target = null;
  let hover = null, pinned = null, dragNode = null, panning = null, moved = false;
  let query = "", matches = null;
  let alpha = 1, autoFit = false, breathe = true, dirty = true;
  const MAX_LABELS = 220;
  let labelOrder = [];
  const ALPHA_MIN = 0.015, ALPHA_DECAY = 0.985;
  const CELL = 70, CUT2 = CELL * CELL * 2.25;
  let grid = new Map();

  // ── Helpers ────────────────────────────────────────────────────────────
  const fold = s => (s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  // Curated, mutually distinct hues (amber is reserved for the hub); golden-angle beyond that.
  const PALETTE = ["#60a5fa", "#f472b6", "#34d399", "#a78bfa", "#f87171", "#22d3ee", "#a3e635",
                   "#e879f9", "#2dd4bf", "#818cf8", "#fb7185", "#facc15", "#38bdf8", "#c084fc", "#4ade80"];
  function colourFor(i) {
    if (i < PALETTE.length) return PALETTE[i];
    return `hsl(${((i * 137.508) % 300 + 60).toFixed(0)}, 70%, 66%)`;
  }
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function visible(n) { return !hiddenAreas.has(n.area); }
  function radius(n) { return BASE_R[n.kind] + Math.min(9, Math.sqrt(n.degree) * 1.15); }

  // ── Data ───────────────────────────────────────────────────────────────
  async function load() {
    let data;
    try {
      const resp = await fetch("/api/cerebro");
      data = await resp.json();
    } catch (e) {
      showEmpty("Não foi possível carregar o cérebro: " + e.message);
      return;
    }
    if (!data.enabled) { showEmpty("O cérebro Obsidian está desativado. Ative “Enable Obsidian Brain” nas configurações."); return; }
    if (data.error) { showEmpty("Erro: " + data.error); return; }
    if (!data.nodes.length) { showEmpty("Ainda não há conhecimento no cofre " + data.vault + "."); return; }
    document.getElementById("empty").classList.remove("show");
    ingest(data);
  }

  function showEmpty(msg) {
    document.getElementById("empty-msg").textContent = msg;
    document.getElementById("empty").classList.add("show");
    document.getElementById("stats").textContent = "";
  }

  function ingest(data) {
    const old = byId;
    areas = data.areas;
    const hubArea = (data.nodes.find(n => n.kind === "hub") || {}).area;
    const others = areas.filter(a => a.name !== hubArea);
    areaColour = new Map(others.map((a, i) => [a.name, colourFor(i)]));
    if (hubArea) areaColour.set(hubArea, HUB_COLOUR);
    const anchors = new Map();
    const R = 120 + Math.sqrt(data.nodes.length) * 22;
    areas.forEach((a, i) => {
      const t = (i / Math.max(1, areas.length)) * Math.PI * 2;
      const spread = 30 + Math.sqrt(a.count) * 22;
      anchors.set(a.name, { x: Math.cos(t) * (R + spread * .5), y: Math.sin(t) * (R + spread * .5), spread });
    });
    byId = new Map();
    nodes = data.nodes.map(d => {
      const prev = old.get(d.id);
      const a = anchors.get(d.area) || { x: 0, y: 0, spread: 80 };
      // Spawn inside a disc sized to the area so dense areas don't start in a pile.
      const ang = Math.random() * Math.PI * 2, rad = Math.sqrt(Math.random()) * a.spread;
      const n = Object.assign({}, d, {
        x: prev ? prev.x : a.x + Math.cos(ang) * rad,
        y: prev ? prev.y : a.y + Math.sin(ang) * rad,
        vx: 0, vy: 0, fx: null, fy: null,
        phase: Math.random() * Math.PI * 2,
      });
      if (n.kind === "hub" && !prev) { n.x = 0; n.y = 0; }
      n.r = radius(n);
      n.colour = n.kind === "hub" ? HUB_COLOUR : (areaColour.get(n.area) || "#a1a1aa");
      n.fold = fold(n.label + " " + n.points.join(" "));
      byId.set(n.id, n);
      return n;
    });
    byLabel = new Map(nodes.map(n => [n.label, n]));
    const rank = { hub: 0, area: 1, memory: 2, note: 3, concept: 3 };
    labelOrder = [...nodes].sort((a, b) => rank[a.kind] - rank[b.kind] || b.degree - a.degree);
    breathe = nodes.length <= 1500;
    links = data.links.map(l => ({ s: byId.get(l.source), t: byId.get(l.target) })).filter(l => l.s && l.t);
    neighbours = new Map(nodes.map(n => [n.id, new Set()]));
    for (const l of links) { neighbours.get(l.s.id).add(l.t); neighbours.get(l.t.id).add(l.s); }
    for (const l of links) {
      l.ws = 2 / (1 + neighbours.get(l.s.id).size * .15);
      l.wt = 2 / (1 + neighbours.get(l.t.id).size * .15);
      l.rest = 26 + l.s.r + l.t.r + (l.s.kind === "hub" || l.t.kind === "hub" ? 90 : 0);
    }
    alpha = old.size ? Math.max(alpha, 0.35) : 1;
    if (pinned) pinned = byId.get(pinned.id) || null;
    if (hover) hover = byId.get(hover.id) || null;
    renderLegend();
    applySearch();
    const points = nodes.reduce((s, n) => s + n.points.length, 0);
    document.getElementById("stats").textContent =
      `${nodes.length} nós · ${links.length} conexões · ${points} pontos que eu sei`;
    if (!old.size) autoFit = true;
    if (pinned) showCard(pinned);
  }

  function renderLegend() {
    const box = document.getElementById("areas");
    box.textContent = "";
    for (const a of areas) {
      const row = el("div", "area" + (hiddenAreas.has(a.name) ? " off" : ""));
      const dot = el("span", "dot"); dot.style.background = areaColour.get(a.name); dot.style.color = areaColour.get(a.name);
      row.append(dot, el("span", null, a.name), el("span", "n", String(a.count)));
      row.onclick = () => { hiddenAreas.has(a.name) ? hiddenAreas.delete(a.name) : hiddenAreas.add(a.name); renderLegend(); alpha = Math.max(alpha, .2); };
      box.append(row);
    }
  }

  // ── Physics (spatial grid keeps each tick ~O(n)) ──────────────────────
  const cellKey = (gx, gy) => (gx + 32768) * 65536 + (gy + 32768);
  function buildGrid() {
    grid = new Map();
    for (const n of nodes) {
      if (!visible(n)) continue;
      const key = cellKey(Math.floor(n.x / CELL), Math.floor(n.y / CELL));
      let cell = grid.get(key);
      if (!cell) grid.set(key, cell = []);
      cell.push(n);
    }
  }

  function tick() {
    if (alpha <= ALPHA_MIN && !dragNode) return;
    buildGrid();
    const a = Math.max(alpha, ALPHA_MIN);
    // Repulsion with nearby nodes
    for (const n of nodes) {
      if (!visible(n)) continue;
      const cx = Math.floor(n.x / CELL), cy = Math.floor(n.y / CELL);
      for (let gx = cx - 1; gx <= cx + 1; gx++) for (let gy = cy - 1; gy <= cy + 1; gy++) {
        const cell = grid.get(cellKey(gx, gy));
        if (!cell) continue;
        for (const m of cell) {
          if (m === n) continue;
          let dx = n.x - m.x, dy = n.y - m.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = .25; }
          if (d2 > CUT2) continue;
          const min = n.r + m.r + 6;
          // Smooth falloff to zero at the cutoff avoids grid-shaped artefacts.
          const f = (900 + (d2 < min * min ? 2600 : 0)) / d2 * (1 - d2 / CUT2);
          n.vx += dx * f * a * 0.06; n.vy += dy * f * a * 0.06;
        }
      }
    }
    // Springs along links
    for (const l of links) {
      if (!visible(l.s) || !visible(l.t)) continue;
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - l.rest) / d * 0.045 * a;
      l.s.vx += dx * f * l.ws; l.s.vy += dy * f * l.ws;
      l.t.vx -= dx * f * l.wt; l.t.vy -= dy * f * l.wt;
    }
    // Gentle pull to centre keeps the brain round
    for (const n of nodes) {
      if (!visible(n)) continue;
      n.vx -= n.x * 0.0018 * a; n.vy -= n.y * 0.0018 * a;
      if (n.fx != null) { n.x = n.fx; n.y = n.fy; n.vx = n.vy = 0; continue; }
      n.vx *= 0.82; n.vy *= 0.82;
      const sp = Math.hypot(n.vx, n.vy);
      if (sp > 30) { n.vx *= 30 / sp; n.vy *= 30 / sp; }
      n.x += n.vx; n.y += n.vy;
    }
    alpha *= ALPHA_DECAY;
    // Keep the whole brain in frame while it unfolds, until the user takes over.
    if (autoFit && (alpha < 0.5 && alpha > 0.49 || alpha < 0.2 && alpha > 0.195 || alpha < 0.05 && alpha > 0.0495)) fitAll();
    if (autoFit && alpha < 0.03) autoFit = false;
  }

  // ── Rendering ──────────────────────────────────────────────────────────
  function resize() {
    DPR = window.devicePixelRatio || 1;
    W = window.innerWidth; H = window.innerHeight;
    canvas.width = W * DPR; canvas.height = H * DPR;
  }
  const toScreen = (x, y) => [ (x - view.x) * view.k + W / 2, (y - view.y) * view.k + H / 2 ];
  const toWorld = (sx, sy) => [ (sx - W / 2) / view.k + view.x, (sy - H / 2) / view.k + view.y ];

  function focusSet() {
    const f = hover || pinned;
    if (!f) return null;
    const s = new Set(neighbours.get(f.id)); s.add(f);
    return s;
  }

  function draw(t) {
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);

    ctx.setTransform(DPR * view.k, 0, 0, DPR * view.k, DPR * (W / 2 - view.x * view.k), DPR * (H / 2 - view.y * view.k));
    const focus = focusSet();
    const time = t / 1000;
    // Subtle breathing offset: the brain never looks frozen, physics stays stable.
    for (const n of nodes) {
      const amp = n.kind === "hub" || !breathe ? 0 : 1.6;
      n.dx = n.x + Math.sin(time * .7 + n.phase) * amp;
      n.dy = n.y + Math.cos(time * .6 + n.phase * 1.3) * amp;
    }

    // Links
    ctx.lineWidth = 0.7 / Math.sqrt(view.k);
    ctx.strokeStyle = focus || matches ? "rgba(161,161,170,0.05)" : "rgba(161,161,170,0.16)";
    const [vx0, vy0] = toWorld(0, 0), [vx1, vy1] = toWorld(W, H);
    const off = (n) => n.dx < vx0 || n.dx > vx1 || n.dy < vy0 || n.dy > vy1;
    ctx.beginPath();
    for (const l of links) {
      if (!visible(l.s) || !visible(l.t)) continue;
      if (off(l.s) && off(l.t)) continue;
      ctx.moveTo(l.s.dx, l.s.dy); ctx.lineTo(l.t.dx, l.t.dy);
    }
    ctx.stroke();
    if (focus) {
      const f = hover || pinned;
      ctx.lineWidth = 1.4 / Math.sqrt(view.k);
      ctx.strokeStyle = f.colour; ctx.globalAlpha = .75;
      ctx.beginPath();
      for (const l of links) {
        if (l.s !== f && l.t !== f) continue;
        ctx.moveTo(l.s.dx, l.s.dy); ctx.lineTo(l.t.dx, l.t.dy);
      }
      ctx.stroke(); ctx.globalAlpha = 1;
    }

    // Nodes: one path per (colour, lit) batch keeps fill calls ~O(areas)
    const batches = new Map();
    for (const n of nodes) {
      if (!visible(n)) continue;
      const lit = focus ? focus.has(n) : matches ? matches.has(n) : true;
      if ((n.kind === "hub" || n.kind === "area") && lit) continue;  // drawn with glow below
      const key = n.colour + (lit ? "|1" : "|0");
      let b = batches.get(key);
      if (!b) batches.set(key, b = { colour: n.colour, lit, list: [] });
      b.list.push(n);
    }
    for (const b of batches.values()) {
      ctx.globalAlpha = b.lit ? 1 : 0.12;
      ctx.fillStyle = b.colour;
      ctx.beginPath();
      for (const n of b.list) { ctx.moveTo(n.dx + n.r, n.dy); ctx.arc(n.dx, n.dy, n.r, 0, Math.PI * 2); }
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    for (const n of nodes) {
      if (!visible(n) || (n.kind !== "hub" && n.kind !== "area")) continue;
      if (focus ? !focus.has(n) : matches ? !matches.has(n) : false) continue;
      ctx.shadowColor = n.colour; ctx.shadowBlur = n.kind === "hub" ? 28 : 16;
      ctx.fillStyle = n.colour;
      ctx.beginPath(); ctx.arc(n.dx, n.dy, n.r, 0, Math.PI * 2); ctx.fill();
    }
    ctx.shadowBlur = 0;
    for (const n of [hover, pinned]) {
      if (!n || !visible(n)) continue;
      ctx.lineWidth = 2 / view.k; ctx.strokeStyle = "#fff";
      ctx.beginPath(); ctx.arc(n.dx, n.dy, n.r + 3 / view.k, 0, Math.PI * 2); ctx.stroke();
    }

    // Labels: big nodes always, small ones when zoomed in or in focus
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    let drawn = 0;
    for (const n of labelOrder) {
      if (drawn >= MAX_LABELS) break;
      if (!visible(n) || off(n)) continue;
      const inFocus = focus ? focus.has(n) : matches ? matches.has(n) : false;
      const screenR = n.r * view.k;
      const show = n.kind === "hub" || n.kind === "area" || inFocus || screenR > 10 || (view.k > 1.8 && n.degree > 2) || view.k > 2.8;
      if (!show) continue;
      if ((focus || matches) && !inFocus && n.kind !== "hub") continue;
      const size = (n.kind === "hub" ? 15 : n.kind === "area" ? 12.5 : 10.5) / Math.min(view.k, 1.8) * Math.max(1, view.k * .55);
      ctx.font = `${n.kind === "area" || n.kind === "hub" ? 600 : 400} ${size}px Outfit, Segoe UI, system-ui, sans-serif`;
      ctx.globalAlpha = inFocus || !focus ? .95 : .35;
      ctx.fillStyle = n.kind === "area" || n.kind === "hub" ? "#f4f4f5" : "#a1a1aa";
      ctx.fillText(n.label.length > 42 ? n.label.slice(0, 40) + "…" : n.label, n.dx, n.dy + n.r + 3 / view.k);
      drawn++;
    }
    ctx.globalAlpha = 1;
  }

  function animateView() {
    if (!target) return;
    view.x += (target.x - view.x) * .12; view.y += (target.y - view.y) * .12; view.k += (target.k - view.k) * .12;
    if (Math.abs(target.k - view.k) < .002 && Math.hypot(target.x - view.x, target.y - view.y) < .5) target = null;
  }

  // Large settled graphs stop redrawing until something changes (no idle CPU);
  // smaller ones keep a gentle breathing motion.
  function frame(t) {
    const active = alpha > ALPHA_MIN || dragNode || target || breathe || dirty;
    tick(); animateView();
    if (active) { draw(t); dirty = false; }
    requestAnimationFrame(frame);
  }

  // ── Interaction ────────────────────────────────────────────────────────
  function nodeAt(sx, sy) {
    const [x, y] = toWorld(sx, sy);
    let best = null, bestD = Infinity;
    for (const n of nodes) {
      if (!visible(n)) continue;
      const d = Math.hypot(n.x - x, n.y - y);
      const hit = n.r + 5 / view.k;
      if (d < hit && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }

  function showCard(n) {
    card.textContent = "";
    card.append(el("h3", null, n.label));
    const chips = el("div", "chips");
    const areaChip = el("span", "chip", "● " + n.area); areaChip.style.color = areaColour.get(n.area) || n.colour;
    chips.append(areaChip, el("span", "chip", KIND[n.kind] || n.kind));
    if (n.path) chips.append(el("span", "chip", "📄 " + n.path));
    card.append(chips);

    card.append(el("h4", null, `🧩 O que eu sei (${n.points.length})`));
    if (n.points.length) {
      const ul = el("ul");
      for (const p of n.points) ul.append(el("li", null, p));
      card.append(ul);
    } else {
      card.append(el("div", "hint", n.kind === "concept"
        ? "Conceito citado nas notas abaixo. Crie uma nota com este nome no Obsidian para detalhar o que você sabe."
        : "Nenhum ponto registrado ainda."));
    }
    if (n.backlinks.length) {
      card.append(el("h4", null, `↩️ Mencionado em (${n.backlinks.length})`));
      const box = el("div", "links");
      for (const b of n.backlinks) { const s = el("span", "link", b); s.onclick = () => focusNode(byLabel.get(b)); box.append(s); }
      card.append(box);
    }
    const nb = [...(neighbours.get(n.id) || [])].sort((a, b) => b.degree - a.degree);
    if (nb.length) {
      card.append(el("h4", null, `🔗 Conexões (${nb.length})`));
      const box = el("div", "links");
      for (const m of nb.slice(0, 80)) { const s = el("span", "link", m.label); s.style.borderLeft = "3px solid " + m.colour; s.onclick = () => focusNode(m); box.append(s); }
      card.append(box);
    }
    card.append(el("div", "hint", pinned === n ? "📌 Fixado. Clique no fundo para soltar." : "🖱️ Clique para fixar · duplo clique para aproximar"));
    card.classList.add("show");
  }

  function hideCard() { if (!pinned) card.classList.remove("show"); }

  function focusNode(n) {
    if (!n) return;
    pinned = n; showCard(n);
    target = { x: n.x, y: n.y, k: Math.max(view.k, 1.8) };
  }

  function fitAll() {
    const vis = nodes.filter(visible);
    if (!vis.length) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of vis) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    // Keep clear of the floating panels on the left when there is room.
    const padL = W > 900 ? 340 : 0;
    const k = Math.min(2.2, 0.88 * Math.min((W - padL - 40) / Math.max(80, x1 - x0), (H - 60) / Math.max(80, y1 - y0)));
    const sx = padL + (W - padL) / 2;
    target = { x: (x0 + x1) / 2 - (sx - W / 2) / k, y: (y0 + y1) / 2, k };
  }

  function applySearch() {
    query = fold(search.value.trim());
    if (!query) { matches = null; return; }
    const terms = query.split(/\s+/);
    matches = new Set(nodes.filter(n => terms.every(t => n.fold.includes(t))));
  }

  canvas.addEventListener("mousemove", e => {
    if (dragNode) {
      const [x, y] = toWorld(e.clientX, e.clientY);
      dragNode.fx = x; dragNode.fy = y; moved = true; alpha = Math.max(alpha, .25);
      return;
    }
    if (panning) {
      view.x = panning.vx - (e.clientX - panning.x) / view.k;
      view.y = panning.vy - (e.clientY - panning.y) / view.k;
      moved = true; target = null;
      return;
    }
    const n = nodeAt(e.clientX, e.clientY);
    canvas.classList.toggle("on-node", !!n);
    if (n !== hover) {
      hover = n;
      if (n) showCard(n); else if (pinned) showCard(pinned); else hideCard();
    }
  });
  canvas.addEventListener("mouseleave", () => { hover = null; if (pinned) showCard(pinned); else hideCard(); });
  canvas.addEventListener("mousedown", e => {
    moved = false; autoFit = false;
    const n = nodeAt(e.clientX, e.clientY);
    if (n) { dragNode = n; n.fx = n.x; n.fy = n.y; }
    else { panning = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y }; canvas.classList.add("dragging"); }
  });
  window.addEventListener("mouseup", () => {
    if (dragNode) {
      if (!moved) { pinned = dragNode; showCard(dragNode); }
      dragNode.fx = dragNode.fy = null; dragNode = null;
    } else if (panning && !moved) {
      pinned = null; if (hover) showCard(hover); else hideCard();
    }
    panning = null; canvas.classList.remove("dragging");
  });
  canvas.addEventListener("dblclick", e => { const n = nodeAt(e.clientX, e.clientY); if (n) focusNode(n); });
  canvas.addEventListener("wheel", e => {
    e.preventDefault();
    target = null; autoFit = false;
    const [wx, wy] = toWorld(e.clientX, e.clientY);
    const k = Math.min(8, Math.max(0.08, view.k * Math.exp(-e.deltaY * 0.0015)));
    view.k = k;
    view.x = wx - (e.clientX - W / 2) / k;
    view.y = wy - (e.clientY - H / 2) / k;
  }, { passive: false });

  search.addEventListener("input", applySearch);
  search.addEventListener("keydown", e => {
    if (e.key === "Enter" && matches && matches.size) {
      const best = [...matches].sort((a, b) => b.degree - a.degree)[0];
      focusNode(best);
    }
    if (e.key === "Escape") { search.value = ""; applySearch(); }
  });
  document.getElementById("refresh").onclick = load;
  document.getElementById("fit").onclick = fitAll;
  document.getElementById("legend-toggle").onclick = () => document.getElementById("legend").classList.toggle("collapsed");
  window.addEventListener("resize", resize);
  for (const ev of ["mousemove", "mousedown", "mouseup", "wheel", "keydown", "input", "click", "resize"])
    window.addEventListener(ev, () => { dirty = true; }, { capture: true, passive: true });

  resize();
  requestAnimationFrame(frame);
  load();
  setInterval(() => { if (!document.hidden && !dragNode) load(); }, 60000);
})();
</script>
</body>
</html>
"""


def render_brain_page() -> str:
    """Return the brain page HTML with theme colours filled in."""
    html = _TEMPLATE
    for key, value in COLORS.items():
        html = html.replace(f"__{key}__", value)
    return html
