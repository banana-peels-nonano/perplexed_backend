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

// ------------------------------------------------------------------ portraits
// Flat colour-block busts: a split-lit face, simple shapes, one bold background per
// person. What each person looks like comes from the case file (`look`, see
// app/content/schema.py); anything left out is picked from the id, so every laptop
// draws the same face.
function hash(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}
function rng(seed) {
  return () => ((seed = Math.imul(seed ^ (seed >>> 15), 2246822507) ^ Math.imul(seed ^ (seed >>> 13), 3266489909)), (seed >>>= 0) / 4294967296);
}

const SKIN = { 1: ["#F4D9C6", "#E6BFA6"], 2: ["#EDC4A2", "#DBA983"], 3: ["#D8A37B", "#C48A63"], 4: ["#BF865B", "#A76F47"], 5: ["#986444", "#7F5133"], 6: ["#6C4630", "#583724"] };
const HAIR = { black: "#1F1B1A", dark_brown: "#3A2A22", brown: "#6A4631", auburn: "#8C3D25", blonde: "#D6AE66", grey: "#9C9A97", white: "#E9E6E0" };
export const PORTRAIT_BG = ["#B23A5C", "#3C6CA3", "#2D6B51", "#C8BFBA", "#C9661F", "#D8473C", "#6A4C9C", "#2B8686", "#A88A2E", "#4E5D6C"];
const INK = "#1F1B1A";

function shade(hex, k) { // k < 0 darker, k > 0 lighter
  const n = parseInt(hex.slice(1), 16), f = (c) => Math.round(k < 0 ? c * (1 + k) : c + (255 - c) * k);
  return "#" + [n >> 16, (n >> 8) & 255, n & 255].map((c) => f(c).toString(16).padStart(2, "0")).join("");
}
function luma(hex) { const n = parseInt(hex.slice(1), 16); return (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255; }
// a readable ink colour for text in this person's colour, on the paper badge
export const nameColor = (look, id) => { const bg = resolveLook(look, id).background; return luma(bg) > 0.55 ? shade(bg, -0.55) : luma(bg) > 0.42 ? shade(bg, -0.25) : bg; };

export function resolveLook(look = {}, id = "x") {
  const r = rng(hash(id));
  const pick = (a) => a[Math.floor(r() * a.length)];
  const d = {
    skin: 1 + Math.floor(r() * 6),
    hair: pick(["short", "side_part", "quiff", "curly", "bob", "long", "bun", "ponytail", "buzz", "receding"]),
    hair_color: pick(["black", "black", "dark_brown", "dark_brown", "brown", "grey"]),
    facial_hair: r() < 0.7 ? "none" : pick(["stubble", "mustache", "beard", "goatee"]),
    glasses: r() < 0.65 ? "none" : pick(["round", "square", "half"]),
    outfit: pick(["blazer", "suit_tie", "shirt", "sweater", "turtleneck", "hoodie", "kurta"]),
    outfit_color: pick(["#22324A", "#3B3B3B", "#5B2C3A", "#2F4A3A", "#6B4A2B", "#2A3F6B", "#7A2E2A"]),
    background: pick(PORTRAIT_BG),
    accessory: r() < 0.75 ? "none" : pick(["earrings", "lanyard", "pen"]),
    age: pick(["young", "mid", "mid", "older"]),
    expression: pick(["calm", "smile", "smirk", "tired", "stern", "worried"]),
  };
  if (["long", "bob", "bun", "ponytail", "braid"].includes(look?.hair || d.hair) && !look?.facial_hair) d.facial_hair = "none";
  // a described person wears only what the case file says: no random extras
  if (look && Object.keys(look).length) for (const k of ["glasses", "facial_hair", "accessory"]) d[k] = "none";
  const out = { ...d };
  for (const k in look || {}) if (look[k] != null) out[k] = look[k];
  return out;
}

const f1 = (n) => Math.round(n * 10) / 10;
export function portraitSVG(look, id) {
  const L0 = resolveLook(look, id), r = rng(hash(id + "#"));
  const [sk, skS] = SKIN[L0.skin] || SKIN[3];
  const hc = HAIR[L0.hair_color] || HAIR.black, hcS = shade(hc, hc === HAIR.white || hc === HAIR.blonde ? -0.18 : -0.35);
  const oc = L0.outfit_color, ocS = shade(oc, -0.28), ocL = shade(oc, 0.18);
  const cx = 50, w = 38 + r() * 6, h = 50 + r() * 4, T = 26, B = T + h, L = cx - w / 2, R = cx + w / 2;
  const eyeY = T + h * 0.47, my = eyeY + 17, ex = 8.5 + r() * 1.5;
  const P = [];
  const add = (x) => P.push(x);
  const path = (d, fill, extra = "") => add(`<path d="${d}" fill="${fill}" ${extra}/>`);
  const head = `M${f1(L)} ${f1(T + 14)} Q${f1(L)} ${T} ${f1(L + 14)} ${T} H${f1(R - 14)} Q${f1(R)} ${T} ${f1(R)} ${f1(T + 14)} V${f1(B - 18)} Q${f1(R)} ${f1(B)} ${cx} ${f1(B)} Q${f1(L)} ${f1(B)} ${f1(L)} ${f1(B - 18)} Z`;
  const headR = `M${cx} ${T} H${f1(R - 14)} Q${f1(R)} ${T} ${f1(R)} ${f1(T + 14)} V${f1(B - 18)} Q${f1(R)} ${f1(B)} ${cx} ${f1(B)} Z`;
  const cap = (lift, low) => `M${f1(L - 2)} ${f1(T + low)} Q${f1(L - 3)} ${f1(T - lift)} ${cx} ${f1(T - lift)} Q${f1(R + 3)} ${f1(T - lift)} ${f1(R + 2)} ${f1(T + low)} L${f1(R - 1)} ${f1(T + 11)} Q${cx} ${f1(T + 3)} ${f1(L + 1)} ${f1(T + 11)} Z`;

  add(`<rect width="100" height="120" fill="${L0.background}"/>`);
  add(`<rect x="50" width="50" height="120" fill="#000" opacity=".06"/>`);

  // hair that falls behind the head and shoulders
  const back = { long: B + 22, bob: B - 6, braid: 0, ponytail: 0, covered: B + 26 }[L0.hair];
  if (L0.hair === "long" || L0.hair === "bob") {
    path(`M${f1(L - 6)} ${f1(T + 12)} Q${f1(L - 7)} ${f1(T - 7)} ${cx} ${f1(T - 7)} Q${f1(R + 7)} ${f1(T - 7)} ${f1(R + 6)} ${f1(T + 12)} V${f1(back)} H${f1(L - 6)} Z`, hcS);
  }
  if (L0.hair === "ponytail") path(`M${f1(R - 4)} ${f1(T + 6)} Q${f1(R + 16)} ${f1(T + 10)} ${f1(R + 10)} ${f1(B + 4)} Q${f1(R + 4)} ${f1(T + 26)} ${f1(R - 6)} ${f1(T + 16)} Z`, hcS);
  if (L0.hair === "covered") path(`M${f1(L - 7)} ${f1(T + 10)} Q${f1(L - 8)} ${f1(T - 8)} ${cx} ${f1(T - 8)} Q${f1(R + 8)} ${f1(T - 8)} ${f1(R + 7)} ${f1(T + 10)} L${f1(R + 14)} ${f1(B + 30)} H${f1(L - 14)} Z`, ocS);

  // neck, then the body over it
  add(`<rect x="${cx - 8}" y="${f1(B - 8)}" width="16" height="${f1(120 - B)}" fill="${skS}"/>`);
  const sh = B + 6;
  path(`M6 120 V${f1(sh + 14)} Q6 ${f1(sh)} 22 ${f1(sh - 2)} H78 Q94 ${f1(sh)} 94 ${f1(sh + 14)} V120 Z`, oc);
  path(`M50 ${f1(sh - 2)} H78 Q94 ${f1(sh)} 94 ${f1(sh + 14)} V120 H50 Z`, ocS, `opacity=".55"`);
  const o = L0.outfit;
  if (o === "blazer" || o === "suit_tie") {
    path(`M${cx - 10} ${f1(sh - 2)} H${cx + 10} L${cx} ${f1(sh + (o === "suit_tie" ? 26 : 20))} Z`, "#F2EFEA");
    if (o === "suit_tie") {
      path(`M${cx - 2.4} ${f1(sh)} H${cx + 2.4} L${cx + 3.6} ${f1(sh + 19)} L${cx} ${f1(sh + 24)} L${cx - 3.6} ${f1(sh + 19)} Z`, L0.background === "#D8473C" ? "#2A3F6B" : "#C0392B");
      path(`M${cx - 3} ${f1(sh - 2)} H${cx + 3} L${cx + 2} ${f1(sh + 3)} H${cx - 2} Z`, shade(L0.background === "#D8473C" ? "#2A3F6B" : "#C0392B", -0.25));
    }
    path(`M${cx - 10} ${f1(sh - 2)} L${cx - 17} ${f1(sh)} L${cx - 4} ${f1(sh + 30)} L${cx} ${f1(sh + 22)} Z`, ocS);
    path(`M${cx + 10} ${f1(sh - 2)} L${cx + 17} ${f1(sh)} L${cx + 4} ${f1(sh + 30)} L${cx} ${f1(sh + 22)} Z`, shade(ocS, -0.15));
  } else if (o === "shirt") {
    path(`M${cx - 9} ${f1(sh - 3)} L${cx} ${f1(sh + 6)} L${cx - 3} ${f1(sh + 10)} L${cx - 12} ${f1(sh + 2)} Z`, ocL);
    path(`M${cx + 9} ${f1(sh - 3)} L${cx} ${f1(sh + 6)} L${cx + 3} ${f1(sh + 10)} L${cx + 12} ${f1(sh + 2)} Z`, ocL);
    add(`<rect x="${cx - 0.6}" y="${f1(sh + 7)}" width="1.2" height="${f1(120 - sh)}" fill="${ocS}"/>`);
    for (let i = 0; i < 3; i++) add(`<circle cx="${cx}" cy="${f1(sh + 13 + i * 7)}" r=".9" fill="${ocL}"/>`);
  } else if (o === "sweater") {
    path(`M${cx - 11} ${f1(sh - 2)} Q${cx} ${f1(sh + 10)} ${cx + 11} ${f1(sh - 2)} Q${cx} ${f1(sh + 4)} ${cx - 11} ${f1(sh - 2)} Z`, ocS);
    path(`M${cx - 7} ${f1(sh - 1)} L${cx - 3} ${f1(sh + 4)} L${cx - 9} ${f1(sh + 1)} Z`, "#F2EFEA");
    path(`M${cx + 7} ${f1(sh - 1)} L${cx + 3} ${f1(sh + 4)} L${cx + 9} ${f1(sh + 1)} Z`, "#F2EFEA");
  } else if (o === "turtleneck") {
    add(`<rect x="${cx - 10}" y="${f1(B - 4)}" width="20" height="${f1(sh - B + 8)}" rx="3" fill="${ocS}"/>`);
    for (let i = 0; i < 2; i++) add(`<rect x="${cx - 10}" y="${f1(B + i * 4)}" width="20" height="1" fill="${shade(ocS, -0.2)}"/>`);
  } else if (o === "hoodie") {
    path(`M${cx - 18} ${f1(sh)} Q${cx} ${f1(sh + 14)} ${cx + 18} ${f1(sh)} Q${cx} ${f1(sh - 8)} ${cx - 18} ${f1(sh)} Z`, ocS);
    add(`<rect x="${cx - 6}" y="${f1(sh + 6)}" width="1.2" height="14" fill="#F2EFEA"/><rect x="${cx + 5}" y="${f1(sh + 6)}" width="1.2" height="14" fill="#F2EFEA"/>`);
  } else if (o === "kurta") {
    add(`<rect x="${cx - 9}" y="${f1(sh - 4)}" width="18" height="5" rx="1.5" fill="${ocS}"/>`);
    add(`<rect x="${cx - 1.5}" y="${f1(sh + 1)}" width="3" height="20" fill="${ocS}"/>`);
    for (let i = 0; i < 3; i++) add(`<circle cx="${cx}" cy="${f1(sh + 5 + i * 6)}" r="1" fill="#E7C66B"/>`);
  }
  if (L0.accessory === "lanyard") {
    path(`M${cx - 11} ${f1(sh - 1)} L${cx - 3} ${f1(sh + 26)} H${cx - 1} L${cx - 9} ${f1(sh - 1)} Z`, "#E9E6E0");
    path(`M${cx + 11} ${f1(sh - 1)} L${cx + 3} ${f1(sh + 26)} H${cx + 1} L${cx + 9} ${f1(sh - 1)} Z`, "#E9E6E0");
    add(`<rect x="${cx - 6}" y="${f1(sh + 25)}" width="12" height="9" rx="1" fill="#F7F5F0"/><rect x="${cx - 4}" y="${f1(sh + 27)}" width="5" height="1.4" fill="${L0.background}"/>`);
  }
  if (L0.accessory === "pen") add(`<rect x="${cx + 17}" y="${f1(sh + 8)}" width="2" height="9" rx=".8" fill="#E7C66B"/><rect x="${cx + 13}" y="${f1(sh + 14)}" width="10" height="1.2" fill="${shade(oc, -0.4)}"/>`);
  if (L0.accessory === "pearls") for (let i = 0; i < 9; i++) { const a = Math.PI * (0.12 + i * 0.095); add(`<circle cx="${f1(cx - 13 * Math.cos(a))}" cy="${f1(sh - 2 + 8 * Math.sin(a))}" r="1.5" fill="#F7F3EA"/>`); }

  // ears, head, the lit and shaded halves
  add(`<ellipse cx="${f1(L)}" cy="${f1(eyeY + 4)}" rx="3.6" ry="6" fill="${sk}"/><ellipse cx="${f1(R)}" cy="${f1(eyeY + 4)}" rx="3.6" ry="6" fill="${skS}"/>`);
  path(head, sk);
  path(headR, skS);
  if (L0.accessory === "earrings") add(`<circle cx="${f1(L - 0.5)}" cy="${f1(eyeY + 11)}" r="1.8" fill="#E2B34C"/><circle cx="${f1(R + 0.5)}" cy="${f1(eyeY + 11)}" r="1.8" fill="#C99A35"/>`);
  if (L0.age === "older") {
    add(`<rect x="${cx - 7}" y="${f1(T + 9)}" width="10" height=".9" fill="${shade(skS, -0.12)}"/><rect x="${cx - 4}" y="${f1(T + 12)}" width="9" height=".9" fill="${shade(skS, -0.12)}"/>`);
    path(`M${cx - 9} ${f1(my - 7)} Q${cx - 11} ${f1(my - 1)} ${cx - 8} ${f1(my + 3)}`, "none", `stroke="${shade(skS, -0.1)}" stroke-width=".9"`);
    path(`M${cx + 9} ${f1(my - 7)} Q${cx + 11} ${f1(my - 1)} ${cx + 8} ${f1(my + 3)}`, "none", `stroke="${shade(skS, -0.18)}" stroke-width=".9"`);
  }
  if (L0.facial_hair === "stubble" || L0.facial_hair === "beard") {
    path(`M${f1(L + 1)} ${f1(eyeY + 7)} V${f1(B - 18)} Q${f1(L + 1)} ${f1(B + (L0.facial_hair === "beard" ? 4 : 0))} ${cx} ${f1(B + (L0.facial_hair === "beard" ? 4 : 0))} Q${f1(R - 1)} ${f1(B + (L0.facial_hair === "beard" ? 4 : 0))} ${f1(R - 1)} ${f1(B - 18)} V${f1(eyeY + 7)} Q${f1(R - 5)} ${f1(my + 1)} ${cx} ${f1(my)} Q${f1(L + 5)} ${f1(my + 1)} ${f1(L + 1)} ${f1(eyeY + 7)} Z`, hc, L0.facial_hair === "stubble" ? `opacity=".22"` : "");
  }
  // nose: a lit wedge pushing into the shaded half
  path(`M${cx} ${f1(eyeY - 1)} L${f1(cx + 4.5)} ${f1(eyeY + 10)} Q${f1(cx + 2)} ${f1(eyeY + 13)} ${f1(cx - 1.5)} ${f1(eyeY + 12)} Z`, sk);
  path(`M${f1(cx - 1.5)} ${f1(eyeY + 12)} Q${f1(cx + 2)} ${f1(eyeY + 13)} ${f1(cx + 4.5)} ${f1(eyeY + 10)}`, "none", `stroke="${shade(skS, -0.15)}" stroke-width=".8"`);

  // eyes and brows
  const xp = (i) => cx + (i ? ex : -ex);
  const e = L0.expression;
  add(`<g class="eyes">`);
  for (const i of [0, 1]) {
    if (e === "tired") { add(`<ellipse cx="${f1(xp(i))}" cy="${f1(eyeY + 0.6)}" rx="2.4" ry="1.6" fill="${INK}"/><rect x="${f1(xp(i) - 3)}" y="${f1(eyeY - 1.4)}" width="6" height="1.6" fill="${i ? skS : sk}"/><path d="M${f1(xp(i) - 2.6)} ${f1(eyeY + 3.6)} Q${f1(xp(i))} ${f1(eyeY + 5)} ${f1(xp(i) + 2.6)} ${f1(eyeY + 3.6)}" fill="none" stroke="${shade(skS, -0.15)}" stroke-width=".7"/>`); }
    else if (e === "smile") add(`<path d="M${f1(xp(i) - 2.6)} ${f1(eyeY + 0.8)} Q${f1(xp(i))} ${f1(eyeY - 2.2)} ${f1(xp(i) + 2.6)} ${f1(eyeY + 0.8)}" fill="none" stroke="${INK}" stroke-width="1.5" stroke-linecap="round"/>`);
    else add(`<ellipse cx="${f1(xp(i))}" cy="${f1(eyeY)}" rx="2.1" ry="2.5" fill="${INK}"/><circle cx="${f1(xp(i) + 0.7)}" cy="${f1(eyeY - 0.8)}" r=".6" fill="#fff"/>`);
  }
  add(`</g>`);
  const browY = eyeY - 6;
  const brow = (i, tilt) => { const x = xp(i), dx = 4; const y1 = browY + (i ? -tilt : tilt), y2 = browY - (i ? -tilt : tilt); add(`<path d="M${f1(x - dx)} ${f1(i ? y2 : y1)} L${f1(x + dx)} ${f1(i ? y1 : y2)}" stroke="${L0.hair === "bald" || L0.hair === "covered" ? shade(hc, 0) : hc}" stroke-width="2.1" stroke-linecap="round"/>`); };
  const tilt = { calm: 0, smile: -0.4, smirk: 0, tired: 0.6, stern: -1.5, worried: 1.4 }[e] ?? 0;
  brow(0, tilt); brow(1, e === "smirk" ? -1.6 : tilt);

  // glasses
  if (L0.glasses === "round") add(`<g fill="#fff" fill-opacity=".18" stroke="${INK}" stroke-width="1.3"><circle cx="${f1(xp(0))}" cy="${f1(eyeY)}" r="5.4"/><circle cx="${f1(xp(1))}" cy="${f1(eyeY)}" r="5.4"/></g><path d="M${f1(xp(0) + 5.4)} ${f1(eyeY - 1)} Q${cx} ${f1(eyeY - 3)} ${f1(xp(1) - 5.4)} ${f1(eyeY - 1)}" fill="none" stroke="${INK}" stroke-width="1.2"/>`);
  if (L0.glasses === "square") add(`<g fill="#fff" fill-opacity=".18" stroke="${INK}" stroke-width="1.3"><rect x="${f1(xp(0) - 6)}" y="${f1(eyeY - 4.2)}" width="12" height="8.6" rx="1.6"/><rect x="${f1(xp(1) - 6)}" y="${f1(eyeY - 4.2)}" width="12" height="8.6" rx="1.6"/></g><path d="M${f1(xp(0) + 6)} ${f1(eyeY - 1.5)} H${f1(xp(1) - 6)}" stroke="${INK}" stroke-width="1.2"/>`);
  if (L0.glasses === "half") add(`<g fill="none" stroke="${INK}" stroke-width="1.3"><path d="M${f1(xp(0) - 6)} ${f1(eyeY)} H${f1(xp(0) + 6)} V${f1(eyeY + 2)} Q${f1(xp(0))} ${f1(eyeY + 6)} ${f1(xp(0) - 6)} ${f1(eyeY + 2)} Z"/><path d="M${f1(xp(1) - 6)} ${f1(eyeY)} H${f1(xp(1) + 6)} V${f1(eyeY + 2)} Q${f1(xp(1))} ${f1(eyeY + 6)} ${f1(xp(1) - 6)} ${f1(eyeY + 2)} Z"/><path d="M${f1(xp(0) + 6)} ${f1(eyeY)} H${f1(xp(1) - 6)}"/></g>`);

  // facial hair over the lip, then the mouth
  if (L0.facial_hair === "mustache" || L0.facial_hair === "beard" || L0.facial_hair === "goatee") path(`M${cx - 9} ${f1(my - 2)} Q${cx - 6} ${f1(my - 7)} ${cx} ${f1(my - 5)} Q${cx + 6} ${f1(my - 7)} ${cx + 9} ${f1(my - 2)} Q${cx + 5} ${f1(my - 3)} ${cx} ${f1(my - 2.6)} Q${cx - 5} ${f1(my - 3)} ${cx - 9} ${f1(my - 2)} Z`, hc);
  if (L0.facial_hair === "goatee") path(`M${cx - 5} ${f1(my + 3)} Q${cx} ${f1(my + 2)} ${cx + 5} ${f1(my + 3)} Q${cx + 4} ${f1(B - 1)} ${cx} ${f1(B)} Q${cx - 4} ${f1(B - 1)} ${cx - 5} ${f1(my + 3)} Z`, hc);
  const lip = shade(skS, -0.38);
  const mouth = {
    calm: `M${cx - 5} ${f1(my)} Q${cx} ${f1(my + 1.6)} ${cx + 5} ${f1(my)}`,
    smile: `M${cx - 7} ${f1(my - 1)} Q${cx} ${f1(my + 6)} ${cx + 7} ${f1(my - 1)}`,
    smirk: `M${cx - 5} ${f1(my + 0.6)} Q${cx + 1} ${f1(my + 1.6)} ${cx + 6} ${f1(my - 2)}`,
    tired: `M${cx - 5} ${f1(my + 0.8)} H${cx + 5}`,
    stern: `M${cx - 6} ${f1(my)} H${cx + 6}`,
    worried: `M${cx - 5} ${f1(my + 1.4)} Q${cx} ${f1(my - 1.6)} ${cx + 5} ${f1(my + 1.4)}`,
  }[e] || `M${cx - 5} ${f1(my)} H${cx + 5}`;
  if (e === "smile") path(`M${cx - 7} ${f1(my - 1)} Q${cx} ${f1(my + 6)} ${cx + 7} ${f1(my - 1)} Z`, "#fff", `stroke="${lip}" stroke-width="1.4" stroke-linejoin="round"`);
  else path(mouth, "none", `stroke="${lip}" stroke-width="${e === "stern" ? 1.8 : 1.5}" stroke-linecap="round"`);
  if (e === "smile") add(`<circle cx="${f1(xp(0) - 2)}" cy="${f1(eyeY + 8)}" r="3" fill="#E06C6C" opacity=".28"/><circle cx="${f1(xp(1) + 2)}" cy="${f1(eyeY + 8)}" r="3" fill="#E06C6C" opacity=".22"/>`);
  if (L0.accessory === "bindi") add(`<circle cx="${cx}" cy="${f1(browY - 2)}" r="1.4" fill="#C0283A"/>`);

  // hair on top
  const H = L0.hair;
  if (H === "buzz") path(`M${f1(L)} ${f1(T + 14)} Q${f1(L)} ${T} ${f1(L + 14)} ${T} H${f1(R - 14)} Q${f1(R)} ${T} ${f1(R)} ${f1(T + 14)} V${f1(T + 16)} Q${cx} ${f1(T + 9)} ${f1(L)} ${f1(T + 16)} Z`, hc, `opacity=".9"`);
  if (H === "short" || H === "quiff" || H === "side_part" || H === "curly" || H === "bun" || H === "ponytail" || H === "braid") {
    path(cap(H === "curly" ? 7 : 5, 18), hc);
    path(`M${f1(L - 2)} ${f1(T + 18)} V${f1(eyeY + 1)} H${f1(L + 2)} V${f1(T + 12)} Z`, hc);
    path(`M${f1(R + 2)} ${f1(T + 18)} V${f1(eyeY + 1)} H${f1(R - 2)} V${f1(T + 12)} Z`, hcS);
  }
  if (H === "side_part") { path(`M${f1(L + 6)} ${f1(T + 8)} Q${f1(cx + 4)} ${f1(T - 2)} ${f1(R + 2)} ${f1(T + 14)} Q${f1(cx + 2)} ${f1(T + 4)} ${f1(L + 6)} ${f1(T + 8)} Z`, hcS); add(`<rect x="${f1(cx - 9)}" y="${f1(T - 4)}" width="1.1" height="9" fill="${shade(hc, 0.25)}" transform="rotate(-12 ${f1(cx - 9)} ${T})"/>`); }
  if (H === "quiff") path(`M${f1(L + 6)} ${f1(T + 2)} Q${f1(L + 6)} ${f1(T - 14)} ${cx + 4} ${f1(T - 13)} Q${f1(R + 2)} ${f1(T - 12)} ${f1(R - 3)} ${f1(T + 4)} Q${cx} ${f1(T - 4)} ${f1(L + 6)} ${f1(T + 2)} Z`, hc);
  if (H === "curly") for (let i = 0; i < 9; i++) add(`<circle cx="${f1(L - 1 + i * (w + 2) / 8)}" cy="${f1(T - 3 + Math.sin(i * 1.7) * 2.5)}" r="5.2" fill="${i % 2 ? hcS : hc}"/>`);
  if (H === "bun") add(`<circle cx="${cx}" cy="${f1(T - 9)}" r="8" fill="${hc}"/><rect x="${cx - 6}" y="${f1(T - 4)}" width="12" height="2" fill="${hcS}"/>`);
  if (H === "braid") for (let i = 0; i < 6; i++) add(`<ellipse cx="${f1(L - 3 + (i % 2))}" cy="${f1(eyeY + 6 + i * 6)}" rx="3.6" ry="3.4" fill="${i % 2 ? hcS : hc}"/>`);
  if (H === "receding") {
    path(`M${f1(L - 2)} ${f1(T + 10)} Q${f1(L - 1)} ${f1(T + 2)} ${f1(L + 6)} ${f1(T + 3)} V${f1(eyeY + 1)} H${f1(L - 2)} Z`, hc);
    path(`M${f1(R + 2)} ${f1(T + 10)} Q${f1(R + 1)} ${f1(T + 2)} ${f1(R - 6)} ${f1(T + 3)} V${f1(eyeY + 1)} H${f1(R + 2)} Z`, hcS);
  }
  if (H === "bald" || H === "receding") add(`<ellipse cx="${f1(cx - 7)}" cy="${f1(T + 6)}" rx="6" ry="2.6" fill="#fff" opacity=".22"/>`);
  if (H === "long" || H === "bob") {
    path(`M${f1(L - 3)} ${f1(T + 16)} Q${f1(L - 3)} ${f1(T - 6)} ${cx} ${f1(T - 6)} Q${f1(R + 3)} ${f1(T - 6)} ${f1(R + 3)} ${f1(T + 16)} L${f1(R - 2)} ${f1(T + 10)} Q${f1(cx + 6)} ${f1(T + 1)} ${cx} ${f1(T + 6)} Q${f1(cx - 8)} ${f1(T + 12)} ${f1(L + 2)} ${f1(T + 14)} Z`, hc);
    path(`M${f1(L - 3)} ${f1(T + 14)} V${f1(back - (H === "bob" ? 0 : 4))} H${f1(L + 2)} V${f1(T + 14)} Z`, hc);
    path(`M${f1(R + 3)} ${f1(T + 14)} V${f1(back - (H === "bob" ? 0 : 4))} H${f1(R - 2)} V${f1(T + 14)} Z`, hcS);
  }
  if (H === "covered") { path(`M${f1(L - 4)} ${f1(T + 18)} Q${f1(L - 4)} ${f1(T - 5)} ${cx} ${f1(T - 5)} Q${f1(R + 4)} ${f1(T - 5)} ${f1(R + 4)} ${f1(T + 18)} V${f1(B - 4)} H${f1(R - 1)} V${f1(T + 12)} Q${cx} ${f1(T + 4)} ${f1(L + 1)} ${f1(T + 12)} V${f1(B - 4)} H${f1(L - 4)} Z`, oc); path(`M${cx} ${f1(T - 5)} Q${f1(R + 4)} ${f1(T - 5)} ${f1(R + 4)} ${f1(T + 18)} V${f1(B - 4)} H${f1(R - 1)} V${f1(T + 12)} Q${cx} ${f1(T + 4)} ${cx} ${f1(T + 4)} Z`, ocS, `opacity=".6"`); }
  if (L0.accessory === "headset") add(`<path d="M${f1(L - 3)} ${f1(eyeY + 2)} Q${f1(L - 4)} ${f1(T - 9)} ${cx} ${f1(T - 9)} Q${f1(R + 4)} ${f1(T - 9)} ${f1(R + 3)} ${f1(eyeY + 2)}" fill="none" stroke="${INK}" stroke-width="2.2"/><rect x="${f1(L - 6)}" y="${f1(eyeY - 2)}" width="6" height="10" rx="2" fill="${INK}"/><path d="M${f1(L - 3)} ${f1(eyeY + 8)} Q${f1(L + 2)} ${f1(my + 2)} ${cx - 6} ${f1(my + 1)}" fill="none" stroke="${INK}" stroke-width="1.3"/>`);

  return `<svg viewBox="0 0 100 120" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid slice" aria-hidden="true">${P.join("")}</svg>`;
}

export function Portrait({ id, name, look }) {
  const svg = portraitSVG(look || {}, id || name || "x");
  return html`<div class="portrait" dangerouslySetInnerHTML=${{ __html: svg }}></div>`;
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
