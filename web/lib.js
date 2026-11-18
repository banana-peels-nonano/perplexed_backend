// Shared helpers for the team portal and the organiser console.
// No build step: plain ES modules + Preact/htm (vendored, ~13 KB).

import { html, useEffect, useState } from "./vendor/preact-htm.js";

export const API = "/api/v1";

// ------------------------------------------------------------------ store
export function createStore(initial) {
  let state = initial;
  const subs = new Set();
  return {
    get: () => state,
    set(patch) {
      state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
      subs.forEach((f) => f(state));
    },
    subscribe(f) {
      subs.add(f);
      return () => subs.delete(f);
    },
  };
}

export function useStore(store) {
  const [s, set] = useState(store.get());
  useEffect(() => store.subscribe(set), [store]);
  return s;
}

// ------------------------------------------------------------------ http
export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details || {};
  }
}

export async function api(path, { method = "GET", body, token, raw } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(API + path, {
      method,
      headers,
      credentials: "include",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError(0, "NETWORK", "can't reach the server — check the wifi");
  }
  if (raw) return res;
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const e = (data && data.error) || {};
    throw new ApiError(res.status, e.code || `HTTP_${res.status}`, e.message || res.statusText, e.details);
  }
  return data;
}

// ------------------------------------------------------------------ live updates
// One socket per tab. Events say what changed; handlers refetch (notify-then-refetch).
export function connectLive(onEvent, onState) {
  let ws, timer, closed = false, delay = 1000;
  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}${API}/ws`);
    ws.onopen = () => {
      delay = 1000;
      onState && onState("live");
    };
    ws.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data));
      } catch {}
    };
    ws.onclose = (e) => {
      onState && onState("offline");
      if (closed) return;
      if (e.code === 4401) return onEvent({ type: "logged_out" });
      if (e.code === 4429) onState && onState("too_many_tabs");
      timer = setTimeout(() => {
        delay = Math.min(delay * 2, 15000);
        open();
        onEvent({ type: "reconnected" });
      }, delay);
    };
  };
  open();
  return () => {
    closed = true;
    clearTimeout(timer);
    ws && ws.close();
  };
}

// ------------------------------------------------------------------ time
export function fmtClock(s) {
  if (s == null) return "--:--";
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  const mm = String(m).padStart(2, "0"), ss = String(x).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function ago(iso) {
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return new Date(iso).toLocaleString();
}

export function useTick(ms = 1000) {
  const [, set] = useState(0);
  useEffect(() => {
    const t = setInterval(() => set((x) => x + 1), ms);
    return () => clearInterval(t);
  }, [ms]);
}

export const uuid = () =>
  crypto.randomUUID
    ? crypto.randomUUID()
    : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
      });

export const bytes = (n) =>
  n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`;

// ------------------------------------------------------------------ markdown (safe subset)
// Escapes everything first, then adds a few tags back. No raw HTML ever reaches the page.
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

export function markdown(src) {
  const lines = (src || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let list = null, para = [], code = null, quote = [], table = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; } };
  const flushQuote = () => { if (quote.length) { out.push(`<blockquote>${inline(quote.join(" "))}</blockquote>`); quote = []; } };
  const cells = (row) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r)); // drop the |---| row
    const [head, ...body] = rows;
    out.push(`<table><thead><tr>${cells(head).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${body
      .map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
    table = [];
  };
  const flushAll = () => { flushPara(); flushList(); flushQuote(); flushTable(); };
  for (const line of lines) {
    if (code !== null) {
      if (/^```/.test(line)) { out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`); code = null; }
      else code.push(line);
      continue;
    }
    if (/^```/.test(line)) { flushAll(); code = []; continue; }
    if (/^\s*\|/.test(line)) { flushPara(); flushList(); flushQuote(); table.push(line); continue; }
    if (table.length) flushTable();
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) { flushAll(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); continue; }
    if (/^(-{3,}|\*{3,})\s*$/.test(line)) { flushAll(); out.push("<hr>"); continue; }
    if ((m = line.match(/^>\s?(.*)$/))) { flushPara(); flushList(); quote.push(m[1]); continue; }
    if ((m = line.match(/^\s*[-*]\s+(.*)$/))) { flushPara(); flushQuote(); if (!list || list.tag !== "ul") { flushList(); list = { tag: "ul", items: [] }; } list.items.push(m[1]); continue; }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { flushPara(); flushQuote(); if (!list || list.tag !== "ol") { flushList(); list = { tag: "ol", items: [] }; } list.items.push(m[1]); continue; }
    if (!line.trim()) { flushAll(); continue; }
    flushList(); flushQuote();
    para.push(line.trim());
  }
  if (code !== null) out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
  flushAll();
  return out.join("\n");
}

export const Markdown = ({ src, class: cls = "md" }) =>
  html`<div class=${cls} dangerouslySetInnerHTML=${{ __html: markdown(src) }}></div>`;

// ------------------------------------------------------------------ toasts
// Everything a team may pin or cite: its interview evidence, a locked partner's, and the
// records tab when organisers show documents in full. Same shape for all three.
export function evidencePool(s) {
  const seen = new Set();
  const out = [];
  const add = (e) => { if (!seen.has(e.passage_id)) { seen.add(e.passage_id); out.push(e); } };
  s.evidence.forEach((e) => add({ ...e, mine: true }));
  s.partnerEvidence.forEach(add);
  (s.records?.records || []).forEach((r) => r.passages.forEach((p) => add({
    passage_id: p.passage_id, record_id: r.id, record_title: r.title, record_date: r.date,
    excerpt: p.text, mode: "record", stakeholder_name: "", mine: !r.partner, team_code: "records",
  })));
  return out;
}

export const toasts = createStore({ items: [] });
export function toast(text, kind = "") {
  const id = Math.random();
  toasts.set((s) => ({ items: [...s.items, { id, text, kind }] }));
  setTimeout(() => toasts.set((s) => ({ items: s.items.filter((t) => t.id !== id) })), kind === "err" ? 6000 : 4000);
}
export function Toasts() {
  const { items } = useStore(toasts);
  return html`<div class="toasts" role="status" aria-live="polite">
    ${items.map((t) => html`<div class=${"toast " + t.kind} key=${t.id}>${t.text}</div>`)}
  </div>`;
}

export function Modal({ title, onClose, children, wide }) {
  useEffect(() => {
    const k = (e) => e.key === "Escape" && onClose();
    addEventListener("keydown", k);
    return () => removeEventListener("keydown", k);
  }, [onClose]);
  return html`<div class="modal-bg" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class="modal" style=${wide ? "width:min(1100px,100%)" : ""} role="dialog" aria-modal="true">
      <div class="modal-h"><span class="strong">${title}</span><button class="btn ghost small right" onClick=${onClose}>Close</button></div>
      <div class="modal-b">${children}</div>
    </div>
  </div>`;
}

export const Logo = () => html`<span class="logo"><span class="logo-mark">${[...Array(9)].map(() => html`<i></i>`)}</span>PERPLEXED</span>`;

// ------------------------------------------------------------------ scanline portraits
// Each stakeholder gets a generated bust, drawn as horizontal scanlines (pin 1's look).
// Deterministic from the id, so every laptop shows the same face.
function hash(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}
function rng(seed) {
  return () => ((seed = Math.imul(seed ^ (seed >>> 15), 2246822507) ^ Math.imul(seed ^ (seed >>> 13), 3266489909)), (seed >>>= 0) / 4294967296);
}

export function drawPortrait(canvas, id, name, { t = 0, glitch = 0 } = {}) {
  const W = 160, H = 160;
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  const r = rng(hash(id || name || "x"));
  // 1. the shape, on a mask canvas
  const m = document.createElement("canvas");
  m.width = W; m.height = H;
  const g = m.getContext("2d", { willReadFrequently: true });
  g.fillStyle = "#000";
  const headW = 34 + r() * 10, headH = 42 + r() * 8, cx = 80, cy = 70;
  g.beginPath(); g.ellipse(cx, cy, headW, headH, 0, 0, Math.PI * 2); g.fill();
  g.beginPath(); g.moveTo(14, H); g.quadraticCurveTo(28, 116, 80, 112); g.quadraticCurveTo(132, 116, 146, H); g.fill();
  g.fillRect(cx - 14, cy + headH - 12, 28, 24); // neck
  const hair = Math.floor(r() * 4);
  if (hair === 0) { g.beginPath(); g.ellipse(cx, cy - headH * .55, headW * 1.12, headH * .6, 0, Math.PI, 0); g.fill(); }
  if (hair === 1) { g.beginPath(); g.ellipse(cx, cy - 6, headW * 1.25, headH * 1.12, 0, Math.PI * 1.02, Math.PI * 1.98); g.fill(); g.fillRect(cx - headW * 1.22, cy - 6, 12, 60); g.fillRect(cx + headW * 1.22 - 12, cy - 6, 12, 60); }
  if (hair === 2) { for (let i = 0; i < 9; i++) { g.beginPath(); g.arc(cx - headW + i * headW / 4, cy - headH * .7 + Math.sin(i) * 4, 11, 0, Math.PI * 2); g.fill(); } }
  if (hair === 3) { g.beginPath(); g.ellipse(cx, cy - headH * .5, headW * 1.05, headH * .5, -.2, Math.PI, 0); g.fill(); g.fillRect(cx + headW * .3, cy - headH * 1.15, 16, 20); }
  // face: eyes and mouth cut out
  g.globalCompositeOperation = "destination-out";
  const ey = cy - 2 + r() * 6, ex = 13 + r() * 4;
  g.fillRect(cx - ex - 5, ey, 8, 6); g.fillRect(cx + ex - 3, ey, 8, 6);
  if (r() > .5) { g.fillRect(cx - 9, ey + 22, 18, 3); } else { g.fillRect(cx - 6, ey + 22, 12, 4); }
  if (r() > .6) { g.fillRect(cx - ex - 9, ey - 3, 16, 2); g.fillRect(cx + ex - 7, ey - 3, 16, 2); } // glasses-ish
  g.globalCompositeOperation = "source-over";
  // 2. scan it into lines
  const data = g.getImageData(0, 0, W, H).data;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#0e0e0e";
  for (let y = 0; y < H; y += 3) {
    const shift = glitch && Math.sin((y + t) * 0.31) > 0.93 ? Math.round((r() - .5) * 14 * glitch) : 0;
    let run = -1;
    for (let x = 0; x <= W; x++) {
      const on = x < W && data[(y * W + x) * 4 + 3] > 100;
      if (on && run < 0) run = x;
      if (!on && run >= 0) { ctx.fillRect(run + shift, y, x - run, 2); run = -1; }
    }
  }
  // a little ghost trail to the right, like the reference
  ctx.globalAlpha = .18;
  for (let y = 6; y < H * .55; y += 6) {
    const w = 10 + ((y * 7 + hash(name || "")) % 40);
    ctx.fillRect(W - w, y, w, 1);
  }
  ctx.globalAlpha = 1;
}

export function Portrait({ id, name, animate }) {
  let ref;
  useEffect(() => {
    if (!ref) return;
    drawPortrait(ref, id, name);
    if (!animate) return;
    let t = 0, raf, on = false;
    const el = ref;
    const loop = () => { t += 2; drawPortrait(el, id, name, { t, glitch: 1 }); if (on) raf = requestAnimationFrame(loop); };
    const enter = () => { on = true; loop(); };
    const leave = () => { on = false; cancelAnimationFrame(raf); drawPortrait(el, id, name); };
    const host = el.closest(".badge-hang") || el;
    host.addEventListener("mouseenter", enter);
    host.addEventListener("mouseleave", leave);
    return () => { cancelAnimationFrame(raf); host.removeEventListener("mouseenter", enter); host.removeEventListener("mouseleave", leave); };
  }, [id, name, animate]);
  return html`<div class="portrait"><canvas ref=${(e) => (ref = e)} aria-hidden="true"></canvas></div>`;
}

// a fake QR-ish block for the badge back (decoration only)
export function Glyph({ seed, size = 64 }) {
  const r = rng(hash(seed));
  const n = 9, cells = [];
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    const corner = (x < 3 && y < 3) || (x > 5 && y < 3) || (x < 3 && y > 5);
    if (corner || r() > .5) cells.push(html`<rect x=${x} y=${y} width="1" height="1" />`);
  }
  return html`<svg class="qr" viewBox="0 0 9 9" width=${size} height=${size} shape-rendering="crispEdges" fill="#0e0e0e">${cells}</svg>`;
}
