// The evidence board: cards the team places and links the team draws. The server never
// draws a link (that would do the deduction the competition judges).

import { html, useEffect, useRef, useState } from "./vendor/preact-htm.js";
import { api, useStore, toast } from "./lib.js";

const W = 240;
const newId = (p) => `${p}${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`;

export function Board({ S, hasTicker }) {
  const s = useStore(S);
  const [b, setB] = useState(null); // {version, cards, links, extra}
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [mode, setMode] = useState("move"); // move | link
  const [linkFrom, setLinkFrom] = useState(null);
  const [pending, setPending] = useState(null); // {from, to} awaiting a label
  const [label, setLabel] = useState("");
  const [selLink, setSelLink] = useState(null);
  const [q, setQ] = useState("");
  const area = useRef();
  const heights = useRef({});
  const bRef = useRef(b);
  bRef.current = b;

  const reload = async () => {
    try { setB(await api("/board")); setDirty(false); } catch (e) { toast(e.message, "err"); }
  };
  useEffect(() => { reload(); }, []);
  // a teammate saved: take their board unless we have unsaved changes (then our save will 409)
  useEffect(() => { if (b && s.boardVersionSeen > b.version && !dirty) reload(); }, [s.boardVersionSeen]);

  // autosave
  useEffect(() => {
    if (!dirty || !b) return;
    const t = setTimeout(async () => {
      setSaving(true);
      try {
        const out = await api("/board", { method: "PUT", body: { version: b.version, cards: b.cards.map(strip), links: b.links, extra: b.extra || {} } });
        setB((cur) => ({ ...cur, version: out.version, cards: mergeServer(cur.cards, out.cards) }));
        setDirty(false);
      } catch (e) {
        if (e.code === "BOARD_CONFLICT") { toast("A teammate changed the board — reloaded their version. Redo your last move.", "err"); await reload(); }
        else toast(e.message, "err");
      } finally { setSaving(false); }
    }, 700);
    return () => clearTimeout(t);
  }, [b, dirty]);

  const change = (fn) => { setB((cur) => fn(cur)); setDirty(true); };
  // the team's own evidence, plus a locked alliance partner's (shared after the lock)
  const pool = [...s.evidence, ...s.partnerEvidence.filter((p) => !s.evidence.some((e) => e.passage_id === p.passage_id))];
  const evidenceById = Object.fromEntries(pool.map((e) => [e.passage_id, e]));
  const names = Object.fromEntries((s.status?.stakeholders || []).map((x) => [x.id, x.name]));

  const spot = () => {
    const el = area.current;
    const n = (b?.cards.length || 0) % 5;
    return { x: (el?.scrollLeft || 0) + 40 + n * 30, y: (el?.scrollTop || 0) + 40 + n * 30 };
  };
  const addEvidence = (pid) => {
    if (b.cards.some((c) => c.kind === "evidence" && c.passage_id === pid)) return toast("Already on the board.");
    change((cur) => ({ ...cur, cards: [...cur.cards, { id: newId("e"), kind: "evidence", passage_id: pid, ...spot() }] }));
  };
  const addNote = () => change((cur) => ({ ...cur, cards: [...cur.cards, { id: newId("n"), kind: "note", text: "New note", ...spot() }] }));
  const removeCard = (id) => change((cur) => ({ ...cur, cards: cur.cards.filter((c) => c.id !== id), links: cur.links.filter((l) => l.from !== id && l.to !== id) }));

  // dragging
  const startDrag = (e, card) => {
    if (mode === "link") {
      e.stopPropagation();
      if (!linkFrom) return setLinkFrom(card.id);
      if (linkFrom === card.id) return setLinkFrom(null);
      if (b.links.some((l) => (l.from === linkFrom && l.to === card.id) || (l.from === card.id && l.to === linkFrom))) { setLinkFrom(null); return toast("Those two are already linked."); }
      setPending({ from: linkFrom, to: card.id }); setLabel(""); setLinkFrom(null);
      return;
    }
    if (e.target.closest("button, textarea")) return;
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY, ox = card.x || 0, oy = card.y || 0;
    const el = e.currentTarget;
    el.classList.add("dragging");
    let moved = false;
    const move = (ev) => {
      moved = true;
      const x = Math.max(0, ox + ev.clientX - sx), y = Math.max(0, oy + ev.clientY - sy);
      setB((cur) => ({ ...cur, cards: cur.cards.map((c) => (c.id === card.id ? { ...c, x, y } : c)) }));
    };
    const up = () => {
      el.classList.remove("dragging");
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      if (moved) setDirty(true);
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  };

  const commitLink = () => {
    change((cur) => ({ ...cur, links: [...cur.links, { from: pending.from, to: pending.to, label: label.trim().slice(0, 40) }] }));
    setPending(null);
  };

  if (!b) return html`<div class="page"><div class="empty">Loading the board…</div></div>`;
  const pos = Object.fromEntries(b.cards.map((c) => [c.id, c]));
  const centre = (c) => ({ x: (c.x || 0) + W / 2, y: (c.y || 0) + (heights.current[c.id] || 90) / 2 });
  const onBoard = new Set(b.cards.filter((c) => c.kind === "evidence").map((c) => c.passage_id));
  const side = pool.filter((e) => !q || (e.record_title + e.excerpt).toLowerCase().includes(q.toLowerCase()));

  return html`<div class=${"board-wrap" + (hasTicker ? " has-ticker" : "")}>
    <aside class="board-side">
      <div class="kicker">Your evidence · ${s.evidence.length}${s.partnerEvidence.length ? ` + ${s.partnerEvidence.length} from partners` : ""}</div>
      <input class="input" placeholder="Filter…" value=${q} onInput=${(e) => setQ(e.target.value)} />
      ${side.length ? side.map((e) => html`<div class="ev" key=${e.passage_id} style="padding:10px">
          <div class="ev-title">${e.record_title}</div>
          <div class="ev-meta">${e.record_id} · ${e.mode} · ${e.stakeholder_name}${e.team_code ? ` · via ${e.team_code}` : ""}</div>
          <div class="ev-actions"><button class="btn ghost small" disabled=${onBoard.has(e.passage_id)} onClick=${() => addEvidence(e.passage_id)}>${onBoard.has(e.passage_id) ? "On the board" : "Add →"}</button></div>
        </div>`) : html`<div class="empty small">Evidence you're shown in interviews lands here.</div>`}
    </aside>
    <section style="display:flex;flex-direction:column;min-width:0;min-height:0">
      <div class="board-tools">
        <button class=${"btn small" + (mode === "move" ? "" : " ghost")} onClick=${() => { setMode("move"); setLinkFrom(null); }}>Move</button>
        <button class=${"btn small" + (mode === "link" ? "" : " ghost")} onClick=${() => { setMode("link"); setSelLink(null); }}>Link</button>
        <button class="btn ghost small" onClick=${addNote}>+ Note</button>
        ${mode === "link" && html`<span class="small muted">${linkFrom ? "Now click the card to link it to." : "Click a card, then another."}</span>`}
        ${pending && html`<span class="row small">Label: <input class="input" style="width:180px;padding:5px 8px" maxlength="40" autofocus value=${label}
            placeholder="e.g. contradicts" onInput=${(e) => setLabel(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && commitLink()} />
            <button class="btn small" onClick=${commitLink}>Link</button><button class="btn link small" onClick=${() => setPending(null)}>Cancel</button></span>`}
        ${selLink != null && b.links[selLink] && html`<span class="row small">Link “${b.links[selLink].label || "unlabelled"}”
            <button class="btn danger small" onClick=${() => { change((cur) => ({ ...cur, links: cur.links.filter((_, i) => i !== selLink) })); setSelLink(null); }}>Delete link</button></span>`}
        <span class="savestate right">${saving ? "Saving…" : dirty ? "Unsaved" : `Saved · v${b.version}`}</span>
      </div>
      <div class="board-area" ref=${area} style="flex:1" onPointerDown=${(e) => { if (e.target === e.currentTarget || e.target.tagName === "svg") { setSelLink(null); setLinkFrom(null); } }}>
        <div class="board-surface">
          <svg>
            ${b.links.map((l, i) => {
              const a = pos[l.from], z = pos[l.to];
              if (!a || !z) return null;
              const p = centre(a), r = centre(z);
              return html`<g key=${i}>
                <line x1=${p.x} y1=${p.y} x2=${r.x} y2=${r.y} class=${selLink === i ? "sel" : ""} />
                <line class="hit" x1=${p.x} y1=${p.y} x2=${r.x} y2=${r.y} onClick=${() => setSelLink(i)} />
                ${l.label && html`<text x=${(p.x + r.x) / 2} y=${(p.y + r.y) / 2 - 4} text-anchor="middle">${l.label}</text>`}
              </g>`;
            })}
          </svg>
          ${b.cards.map((c) => {
            const ev = c.kind === "evidence" ? evidenceById[c.passage_id] : null;
            return html`<div key=${c.id} class=${"bcard " + c.kind + (linkFrom === c.id ? " linking" : "")}
                style=${`left:${c.x || 0}px;top:${c.y || 0}px`} onPointerDown=${(e) => startDrag(e, c)}
                ref=${(el) => { if (el) heights.current[c.id] = el.offsetHeight; }}>
              <div class="k"><span>${c.kind === "evidence" ? `${ev?.record_id || c.passage_id} · ${ev?.mode || ""}` : c.kind === "quote" ? `quote · ${names[c.stakeholder_id] || c.stakeholder_id || ""}` : "note"}</span>
                <button title="Remove from board" onClick=${() => removeCard(c.id)}>✕</button></div>
              ${c.kind === "evidence" && html`<div class="body"><div class="ev-title" style="margin-bottom:4px">${ev?.record_title || "Evidence"}</div><div class="ev-x">${ev?.excerpt || ""}</div></div>`}
              ${c.kind === "quote" && html`<div class="body">“${c.text || "…"}”</div>`}
              ${c.kind === "note" && html`<textarea maxlength="1000" value=${c.text} onPointerDown=${(e) => e.stopPropagation()}
                  onChange=${(e) => change((cur) => ({ ...cur, cards: cur.cards.map((x) => (x.id === c.id ? { ...x, text: e.target.value || " " } : x)) }))}></textarea>`}
            </div>`;
          })}
          ${!b.cards.length && html`<div class="empty" style="position:absolute;left:60px;top:60px;text-align:left;max-width:420px">
            <span class="strong">An empty board</span>Add evidence from the left, pin quotes from interviews, write notes, then link what connects. Only your team draws links.</div>`}
        </div>
      </div>
    </section>
  </div>`;
}

// quote text and speaker are filled by the server: never send our copy back
function strip(c) {
  const out = { id: c.id, kind: c.kind, x: Math.round(c.x || 0), y: Math.round(c.y || 0) };
  if (c.kind === "evidence") out.passage_id = c.passage_id;
  if (c.kind === "quote") Object.assign(out, { message_id: c.message_id, start: c.start, end: c.end });
  if (c.kind === "note") out.text = c.text;
  return out;
}
const mergeServer = (local, server) => {
  const byId = Object.fromEntries(server.map((c) => [c.id, c]));
  return local.map((c) => (byId[c.id] ? { ...c, text: byId[c.id].text, stakeholder_id: byId[c.id].stakeholder_id } : c));
};
