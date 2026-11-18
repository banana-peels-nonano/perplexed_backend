// Alliance and Audit Findings Sheet pages.

import { html, useEffect, useRef, useState } from "./vendor/preact-htm.js";
import { api, useStore, fmtClock, ago, toast, Modal, useTick, evidencePool } from "./lib.js";
import { S } from "./app.js";

// ------------------------------------------------------------------ alliance
export function AlliancePage() {
  const s = useStore(S);
  const a = s.alliance;
  const [cands, setCands] = useState([]);
  const [busy, setBusy] = useState(false);
  const refresh = async () => {
    try { S.set({ alliance: await api("/alliance") }); setCands(await api("/alliance/teams")); } catch (e) { toast(e.message, "err"); }
  };
  useEffect(() => { refresh(); }, [s.status?.phase?.key]);
  const act = async (fn, ok) => {
    setBusy(true);
    try { await fn(); if (ok) toast(ok); } catch (e) { toast(e.message, "err"); }
    setBusy(false); refresh();
  };
  if (!a) return html`<div class="page"><div class="empty">Loading…</div></div>`;
  const asked = new Set(a.outgoing.flatMap((r) => r.teams.map((t) => t.code)));
  return html`<div class="page">
    <div class="kicker">Two companies, one picture</div>
    <h1 class="hero-title" style="font-size:40px;margin:12px 0 10px">Alliance</h1>
    <p class="muted" style="max-width:640px;margin:0 0 28px">Each company only sees half of what went wrong. Pair up with a team from the other company: once both sides confirm, the alliance locks for good, and each team can see and cite the other's evidence.</p>
    ${a.locked ? html`<div class="result-card">
        <div class="kicker">Locked ${ago(a.locked.created_at)}</div>
        <div class="row" style="flex-wrap:wrap;gap:14px;margin-top:14px">${a.locked.teams.map((t) => html`<div class="panel" style="padding:14px 18px">
          <div class="tiny muted">Company ${t.company_id}</div><div class="strong" style="font-size:20px">${t.code}</div><div class="small">${t.name}</div></div>`)}</div>
        <p class="small" style="margin:16px 0 0">Their evidence is on your <a href="#/board">board</a>'s list and citable in your <a href="#/sheet">findings</a>.</p>
      </div>
      <h2 class="strong" style="font-size:22px;margin:36px 0 12px">Partner evidence · ${s.partnerEvidence.length}</h2>
      ${s.partnerEvidence.length ? html`<div class="evidence-grid">${s.partnerEvidence.map((c) => html`<div class="ev" key=${c.passage_id}>
          <div class="ev-h"><div><div class="ev-title">${c.record_title}</div><div class="ev-meta">${c.record_id} · via ${c.team_code} · ${c.stakeholder_name}</div></div><span class=${"chip" + (c.mode === "quote" ? " solid" : "")}>${c.mode}</span></div>
          <div class="ev-x">${c.excerpt}</div></div>`)}</div>` : html`<div class="empty small">Nothing yet. It appears as your partners are shown evidence.</div>`}`
    : html`
      ${!a.can_request && html`<div class="closed-note" style="margin-bottom:22px">Alliances can only be formed while the organisers have the alliance window open.</div>`}
      ${a.incoming.length > 0 && html`<h2 class="strong" style="font-size:22px;margin:0 0 12px">Asking you</h2>
        <div class="col" style="margin-bottom:26px">${a.incoming.map((r) => html`<div class="alert row" style="gap:16px">
          <div class="grow"><b>${r.teams.map((t) => `${t.code} · ${t.name}`).join(", ")}</b><div class="tiny muted">${ago(r.created_at)}</div></div>
          <button class="btn small" disabled=${busy || !a.can_request} onClick=${() => confirm("Accept? Once everyone confirms, the alliance is locked for good.") && act(() => api(`/alliance/requests/${r.id}/accept`, { method: "POST" }), "Accepted")}>Accept</button>
          <button class="btn ghost small" disabled=${busy} onClick=${() => act(() => api(`/alliance/requests/${r.id}`, { method: "DELETE" }), "Declined")}>Decline</button></div>`)}</div>`}
      ${a.outgoing.length > 0 && html`<h2 class="strong" style="font-size:22px;margin:0 0 12px">You asked</h2>
        <div class="col" style="margin-bottom:26px">${a.outgoing.map((r) => html`<div class="alert row" style="gap:16px">
          <div class="grow"><b>${r.teams.map((t) => `${t.code} · ${t.name}`).join(", ")}</b><div class="tiny muted">waiting for them · ${ago(r.created_at)}</div></div>
          <button class="btn ghost small" disabled=${busy} onClick=${() => act(() => api(`/alliance/requests/${r.id}`, { method: "DELETE" }), "Withdrawn")}>Withdraw</button></div>`)}</div>`}
      <h2 class="strong" style="font-size:22px;margin:0 0 12px">Teams you could ask</h2>
      ${cands.length ? html`<div class="doc-list">${cands.map((t) => html`<div class="doc" style="min-height:0;cursor:default">
          <span class="tiny muted">Company ${t.company_id}</span><span class="title">${t.code}</span><span class="small">${t.name}</span>
          <button class="btn small" style="margin-top:8px;align-self:flex-start" disabled=${busy || !a.can_request || asked.has(t.code)}
            onClick=${() => act(() => api("/alliance/requests", { method: "POST", body: { team: t.code } }), `Asked ${t.code}`)}>${asked.has(t.code) ? "Asked" : "Ask to ally"}</button></div>`)}</div>`
        : html`<div class="empty small">No teams available right now.</div>`}`}
  </div>`;
}

// ------------------------------------------------------------------ findings sheet
const blank = () => ({ id: `f${Date.now().toString(36)}`, title: "", finding: "", impact: "", recommendation: "", sources: [], cross_company: false });

export function Sheet() {
  const s = useStore(S);
  useTick(1000);
  const [sheet, setSheet] = useState(null);
  const [at, setAt] = useState(Date.now());
  const [draft, setDraft] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pick, setPick] = useState(null); // finding index choosing sources
  const mine = useRef(0); // the version this laptop last loaded or saved
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  const load = async (force) => {
    try {
      const sh = await api("/submission");
      if (!force && sh.version === mine.current) return; // our own save echoing back
      setSheet(sh); setAt(Date.now());
      if (force || !dirtyRef.current) { setDraft({ summary: sh.summary, findings: sh.findings }); setDirty(false); mine.current = sh.version; }
      else toast("A teammate saved a newer version. Copy anything you need, then reload the page.", "err");
    } catch (e) { toast(e.message, "err"); }
  };
  useEffect(() => { load(true); }, []);
  useEffect(() => { if (sheet) load(false); }, [s.sheetTick]);
  useEffect(() => {
    const warn = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
    addEventListener("beforeunload", warn);
    return () => removeEventListener("beforeunload", warn);
  }, [dirty]);
  if (!sheet || !draft) return html`<div class="page"><div class="empty">Loading…</div></div>`;

  const left = sheet.closes_in_seconds == null ? null : Math.max(0, sheet.closes_in_seconds - (Date.now() - at) / 1000);
  const open = sheet.open && (left == null || left > 0);
  const pool = evidencePool(s);
  const byId = Object.fromEntries(pool.map((e) => [e.passage_id, e]));
  const edit = (i, patch) => { setDraft((d) => ({ ...d, findings: d.findings.map((f, j) => (j === i ? { ...f, ...patch } : f)) })); setDirty(true); };
  const save = async (final) => {
    if (final && !confirm("Submit this version? You can still change and resubmit until the deadline; judges read the last one you submit.")) return;
    setBusy(true);
    try {
      const out = await api("/submission", { method: "PUT", body: { base_version: sheet.version, summary: draft.summary, findings: draft.findings, final } });
      mine.current = out.version;
      setSheet(out); setAt(Date.now()); setDirty(false);
      toast(final ? `Submitted (version ${out.version})` : `Saved (version ${out.version})`, final ? "holo" : "");
    } catch (e) {
      if (e.code === "SUBMISSION_CONFLICT") toast("A teammate saved first. Copy anything you need, then reload.", "err");
      else if (e.code === "SHEET_INCOMPLETE") toast(`Not yet: ${(e.details.problems || [e.message]).join("; ")}`, "err");
      else toast(e.message, "err");
    }
    setBusy(false);
  };

  return html`<div class="page">
    <div class="row" style="align-items:flex-end;flex-wrap:wrap;gap:16px;margin-bottom:22px">
      <div><div class="kicker">${sheet.round_name || "No round is taking submissions"}</div>
        <h1 class="hero-title" style="font-size:40px;margin-top:12px">Audit findings</h1></div>
      <div class="right row" style="gap:14px">
        ${sheet.final ? html`<span class="chip holo">Submitted · v${sheet.version}</span>` : sheet.version ? html`<span class="chip">Draft · v${sheet.version}</span>` : null}
        ${open ? html`<span class="clock"><span class="small muted">closes in</span><span class=${"t" + (left != null && left < 120 ? " low" : "")}>${left == null ? "--:--" : fmtClock(left)}</span></span>`
          : html`<span class="chip solid">Closed</span>`}
      </div>
    </div>
    ${sheet.requires_cross_company && html`<div class="alert" style="margin-bottom:18px">Your team is in an alliance: at least one finding must be marked <b>cross-company</b> and cite your partner's evidence.</div>`}
    <label class="field" style="margin-bottom:18px">Executive summary
      <textarea class="textarea" rows="3" maxlength="4000" disabled=${!open} value=${draft.summary} onInput=${(e) => { setDraft({ ...draft, summary: e.target.value }); setDirty(true); }}></textarea></label>
    <div class="col" style="gap:16px">${draft.findings.map((f, i) => html`<div class="panel" key=${f.id}>
      <div class="panel-h"><span class="pixel" style="font-size:11px">F${String(i + 1).padStart(2, "0")}</span>
        <input class="input grow" style="font-weight:600" maxlength="200" placeholder="Finding title: the root cause, not the symptom" disabled=${!open} value=${f.title} onInput=${(e) => edit(i, { title: e.target.value })} />
        ${sheet.requires_cross_company && html`<label class="row small" style="white-space:nowrap"><input type="checkbox" disabled=${!open} checked=${f.cross_company} onChange=${(e) => edit(i, { cross_company: e.target.checked })} /> cross-company</label>`}
        <button class="btn link small" disabled=${!open} onClick=${() => { setDraft({ ...draft, findings: draft.findings.filter((_, j) => j !== i) }); setDirty(true); }}>Remove</button></div>
      <div class="panel-b col">
        <label class="field">What is wrong, and what shows it<textarea class="textarea" rows="3" maxlength="4000" disabled=${!open} value=${f.finding} onInput=${(e) => edit(i, { finding: e.target.value })}></textarea></label>
        <div class="grid2">
          <label class="field">Impact<textarea class="textarea" rows="2" maxlength="2000" disabled=${!open} value=${f.impact} onInput=${(e) => edit(i, { impact: e.target.value })}></textarea></label>
          <label class="field">Recommendation<textarea class="textarea" rows="2" maxlength="2000" disabled=${!open} value=${f.recommendation} onInput=${(e) => edit(i, { recommendation: e.target.value })}></textarea></label>
        </div>
        <div class="row" style="flex-wrap:wrap;gap:6px"><span class="tiny muted">Sources</span>
          ${f.sources.map((pid) => html`<span class=${"chip" + (byId[pid] && !byId[pid].mine ? " holo" : "")} title=${byId[pid]?.record_title || pid}>${byId[pid]?.record_id || pid}${byId[pid] && !byId[pid].mine ? ` · ${byId[pid].team_code}` : ""}
            ${open && html`<button class="btn link small" style="padding:0" onClick=${() => edit(i, { sources: f.sources.filter((x) => x !== pid) })}>✕</button>`}</span>`)}
          ${open && html`<button class="btn ghost small" onClick=${() => setPick(i)}>+ cite evidence</button>`}</div>
      </div></div>`)}</div>
    ${open && html`<button class="btn ghost" style="margin-top:16px" disabled=${draft.findings.length >= 30} onClick=${() => { setDraft({ ...draft, findings: [...draft.findings, blank()] }); setDirty(true); }}>+ Add a finding</button>`}
    <div class="row" style="margin-top:26px;gap:12px;flex-wrap:wrap">
      <button class="btn ghost" disabled=${!open || busy || !dirty} onClick=${() => save(false)}>${dirty ? "Save draft" : "Saved"}</button>
      <button class="btn" disabled=${!open || busy} onClick=${() => save(true)}>Submit</button>
      ${dirty && html`<span class="small muted">Unsaved changes</span>`}
      ${sheet.saved_at && html`<span class="small muted right">Last saved ${ago(sheet.saved_at)} · ${sheet.versions.length} version${sheet.versions.length === 1 ? "" : "s"}</span>`}
    </div>
    ${pick != null && html`<${Modal} title="Cite evidence" onClose=${() => setPick(null)}>
      ${pool.length ? html`<div class="col">${pool.map((e) => { const on = draft.findings[pick]?.sources.includes(e.passage_id); return html`<label class="alert row" style="gap:12px;cursor:pointer">
        <input type="checkbox" checked=${on} onChange=${() => edit(pick, { sources: on ? draft.findings[pick].sources.filter((x) => x !== e.passage_id) : [...draft.findings[pick].sources, e.passage_id].slice(0, 20) })} />
        <div class="grow"><b>${e.record_title}</b><div class="tiny muted">${e.mode === "record" ? `${e.record_id} · records tab${e.mine ? "" : " · partner"} · ${e.excerpt.slice(0, 70)}…` : `${e.record_id} · ${e.mode}${e.mine ? "" : ` · partner ${e.team_code}`}`}</div></div></label>`; })}</div>`
        : html`<div class="empty">No evidence yet: it comes from interviews${s.records?.mode === "full" ? " and the records tab" : ""}.</div>`}
    </${Modal}>`}
  </div>`;
}
