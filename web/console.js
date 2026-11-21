// PERPLEXED 2.0 — the organiser console. Talks only to /api/v1/admin/* with the organiser
// token, kept in this tab's sessionStorage (closing the tab forgets it).

import {
  html,
  render,
  useEffect,
  useState,
  useCallback,
} from "./vendor/preact-htm.js";
import {
  api,
  createStore,
  useStore,
  fmtClock,
  ago,
  useTick,
  bytes,
  Toasts,
  toast,
  Modal,
  Logo,
} from "./lib.js";

const C = createStore({
  token: sessionStorage.getItem("px_admin") || "",
  tab: location.hash.slice(2) || "live",
  companies: [],
  phases: [],
});
addEventListener("hashchange", () =>
  C.set({ tab: location.hash.slice(2) || "live" }),
);
const A = (path, opts = {}) =>
  api("/admin" + path, { ...opts, token: C.get().token });
const run = async (fn, ok) => {
  try {
    const r = await fn();
    if (ok) toast(ok);
    return r;
  } catch (e) {
    if (e.status === 401) {
      sessionStorage.removeItem("px_admin");
      C.set({ token: "" });
    }
    toast(`${e.code}: ${e.message}`, "err");
  }
};

function usePoll(fn, ms, deps = []) {
  const [data, setData] = useState(null);
  const load = useCallback(async () => {
    try {
      setData(await fn());
    } catch (e) {
      if (e.status === 401) C.set({ token: "" });
    }
  }, deps);
  useEffect(() => {
    load();
    if (!ms) return;
    const t = setInterval(load, ms);
    return () => clearInterval(t);
  }, [load]);
  return [data, load];
}
const refreshShared = async () => {
  try {
    C.set({ phases: await A("/phases"), companies: await A("/companies") });
  } catch {}
};

// ------------------------------------------------------------------ login
function TokenGate() {
  const [t, setT] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try {
      await api("/admin/overview", { token: t.trim() });
      sessionStorage.setItem("px_admin", t.trim());
      C.set({ token: t.trim() });
      refreshShared();
    } catch (e2) {
      setErr(
        e2.status === 404
          ? "The organiser API is off on this server (ADMIN_TOKEN not set)."
          : "That token isn't right.",
      );
    }
  };
  return html`<div class="login">
    <section class="login-art">
      <${Logo} />
      <div>
        <div class="kicker">Organisers only</div>
        <h1 class="hero-title px" style="margin-top:18px">CONTROL<br />ROOM</h1>
      </div>
      <div></div>
    </section>
    <section class="login-form">
      <form onSubmit=${submit}>
        <div class="kicker">Organiser token</div>
        <input
          class="input"
          type="password"
          value=${t}
          onInput=${(e) => setT(e.target.value)}
          placeholder="ADMIN_TOKEN"
          autofocus
        />
        ${err && html`<div class="error">${err}</div>`}
        <button class="btn" disabled=${t.trim().length < 32}>
          Open console
        </button>
        <p class="small muted">
          The token stays in this tab only. Never paste it anywhere participants
          can see.
        </p>
      </form>
    </section>
  </div>`;
}

// ------------------------------------------------------------------ live
function Live() {
  useTick(1000);
  const [ov, reload] = usePoll(() => A("/overview"), 5000);
  const [live] = usePoll(() => A("/live"), 10000);
  const [at, setAt] = useState(Date.now());
  useEffect(() => setAt(Date.now()), [ov]);
  const p = ov?.phase;
  const left =
    p && p.remaining_seconds != null
      ? Math.max(
          0,
          p.remaining_seconds -
            (p.status === "running" ? (Date.now() - at) / 1000 : 0),
        )
      : null;
  const act = async (path, body, ok) => {
    await run(() => A(path, { method: "POST", body }), ok);
    reload();
    refreshShared();
  };
  const all = C.get().phases;
  const cur = all.find((x) => ["running", "paused"].includes(x.status));
  const next = all.find(
    (x) => x.status === "scheduled" && (!cur || x.seq > cur.seq),
  );
  return html`<div class="col" style="gap:22px">
    <div class="row">
      <h1 class="hero-title" style="font-size:34px">Live</h1>
      <span class="right small muted">${ov?.event} · case ${ov?.case_id}</span>
    </div>
    <div class="panel">
      <div class="panel-b row" style="gap:28px;flex-wrap:wrap">
        <div>
          <div class="tiny muted">Current round</div>
          <div class="strong" style="font-size:22px">
            ${p ? p.name : "None running"}
          </div>
          <div class="small">${p ? p.status : ""}</div>
        </div>
        <div>
          <div class="tiny muted">Time left</div>
          <div class="pixel" style="font-size:30px;margin-top:6px">
            ${p ? fmtClock(left) : "--:--"}
          </div>
        </div>
        <div class="row right" style="flex-wrap:wrap">
          ${p?.status === "running" && html`<button class="btn ghost" onClick=${() => act("/phases/current/pause", undefined, "Paused")}>Pause</button>`}
          ${p?.status === "paused" && html`<button class="btn" onClick=${() => act("/phases/current/resume", undefined, "Resumed")}>Resume</button>`}
          ${
            p &&
            p.planned_seconds > 0 &&
            html`<button
                class="btn ghost"
                onClick=${() => act("/phases/current/extend", { seconds: 300 }, "+5 min")}
              >
                +5 min
              </button>
              <button
                class="btn ghost"
                onClick=${() => act("/phases/current/extend", { seconds: -300 }, "−5 min")}
              >
                −5 min
              </button>`
          }
          ${p && html`<button class="btn danger" onClick=${() => confirm(`End ${p.name}? Interviews close for everyone.`) && act("/phases/current/end", undefined, "Ended")}>End round</button>`}
          ${next && html`<button class="btn" onClick=${() => confirm(`Start ${next.name}?${p ? ` This ends ${p.name}.` : ""}`) && act(`/phases/${next.key}/start`, undefined, `${next.name} started`)}>Start next: ${next.name} →</button>`}
        </div>
      </div>
    </div>
    <div class="stats">
      ${[
        ["Teams", ov?.teams],
        ["Questions", ov?.questions_asked],
        ["Deflections", ov?.deflections],
        ["HR lockouts now", ov?.active_lockouts],
        ["Spent", ov ? `$${Number(ov.spent_usd).toFixed(2)}` : ""],
        ["Provider errors (10 min)", ov?.provider_errors_last_10_min],
        [
          "Open tabs",
          live?.connections ??
            live?.tabs ??
            (live ? JSON.stringify(live).slice(0, 18) : ""),
        ],
      ].map(
        ([l, v]) =>
          html`<div class="stat">
            <div class="v">${v ?? "…"}</div>
            <div class="l">${l}</div>
          </div>`,
      )}
    </div>
    <${Alerts} compact=${true} />
  </div>`;
}

// ------------------------------------------------------------------ rounds
function Rounds() {
  const { phases } = useStore(C);
  const [form, setForm] = useState({
    key: "",
    name: "",
    minutes: 30,
    interview: true,
    after: "",
  });
  useEffect(() => {
    refreshShared();
  }, []);
  const save = async (key, body, ok) => {
    await run(() => A(`/phases/${key}`, { method: "PATCH", body }), ok);
    refreshShared();
  };
  const move = async (i, d) => {
    const keys = phases.map((p) => p.key);
    const j = i + d;
    if (j < 0 || j >= keys.length) return;
    [keys[i], keys[j]] = [keys[j], keys[i]];
    await run(() => A("/phases/order", { method: "PUT", body: { keys } }));
    refreshShared();
  };
  const add = async (e) => {
    e.preventDefault();
    await run(
      () =>
        A("/phases", {
          method: "POST",
          body: {
            key: form.key,
            name: form.name,
            planned_seconds: Math.round(form.minutes * 60),
            capabilities: { interview: form.interview },
            after: form.after || null,
          },
        }),
      "Round added",
    );
    setForm({ ...form, key: "", name: "" });
    refreshShared();
  };
  return html`<div class="col" style="gap:22px">
    <h1 class="hero-title" style="font-size:34px">Rounds</h1>
    <table class="t">
      <thead>
        <tr>
          <th></th>
          <th>Key</th>
          <th>Name</th>
          <th>Minutes</th>
          <th>Interviews</th>
          <th>Alliances</th>
          <th>Submissions</th>
          <th>Status</th>
          <th>Left</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${phases.map(
          (p, i) =>
            html`<tr key=${p.key}>
              <td style="white-space:nowrap">
                <button class="btn link small" onClick=${() => move(i, -1)}>
                  ↑</button
                ><button class="btn link small" onClick=${() => move(i, 1)}>
                  ↓
                </button>
              </td>
              <td><code>${p.key}</code></td>
              <td>
                <input
                  class="input"
                  style="padding:4px 8px"
                  value=${p.name}
                  onChange=${(e) => save(p.key, { name: e.target.value }, "Renamed")}
                />
              </td>
              <td>
                <input
                  class="input"
                  style="padding:4px 8px;width:80px"
                  type="number"
                  min="0"
                  value=${Math.round(p.planned_seconds / 60)}
                  onChange=${(e) => save(p.key, { planned_seconds: Math.round(+e.target.value * 60) }, "Retimed")}
                />
              </td>
              ${["interview", "alliances", "submissions"].map((cap) => html`<td><input type="checkbox" checked=${!!p.capabilities?.[cap]} onChange=${(e) => save(p.key, { capabilities: { [cap]: e.target.checked } }, "Updated")} /></td>`)}
              <td>
                <span
                  class=${"chip" + (["running", "paused"].includes(p.status) ? " solid" : "")}
                  >${p.status}</span
                >
              </td>
              <td>
                ${p.remaining_seconds == null ? "—" : fmtClock(p.remaining_seconds)}
              </td>
              <td style="white-space:nowrap">
                ${
                  p.status !== "running" &&
                  p.status !== "paused" &&
                  html`<button
                    class="btn small ghost"
                    onClick=${async () => {
                      if (confirm(`Start ${p.name}?`)) {
                        await run(
                          () => A(`/phases/${p.key}/start`, { method: "POST" }),
                          "Started",
                        );
                        refreshShared();
                      }
                    }}
                  >
                    Start
                  </button>`
                }
                ${
                  p.status === "scheduled" &&
                  html` <button
                    class="btn small danger"
                    onClick=${async () => {
                      if (confirm(`Delete ${p.name}?`)) {
                        await run(
                          () => A(`/phases/${p.key}`, { method: "DELETE" }),
                          "Deleted",
                        );
                        refreshShared();
                      }
                    }}
                  >
                    Delete
                  </button>`
                }
              </td>
            </tr>`,
        )}
      </tbody>
    </table>
    <form class="panel" onSubmit=${add}>
      <div class="panel-h"><span class="strong">Add a round</span></div>
      <div class="panel-b row" style="flex-wrap:wrap;align-items:flex-end">
        <label class="field"
          >Key<input
            class="input"
            required
            pattern="[a-z0-9][a-z0-9_]{0,39}"
            placeholder="round3"
            value=${form.key}
            onInput=${(e) => setForm({ ...form, key: e.target.value })}
        /></label>
        <label class="field"
          >Name<input
            class="input"
            required
            placeholder="Round 3"
            value=${form.name}
            onInput=${(e) => setForm({ ...form, name: e.target.value })}
        /></label>
        <label class="field" style="width:110px"
          >Minutes (0 = no timer)<input
            class="input"
            type="number"
            min="0"
            value=${form.minutes}
            onInput=${(e) => setForm({ ...form, minutes: +e.target.value })}
        /></label>
        <label class="field"
          >After<select
            class="select"
            value=${form.after}
            onChange=${(e) => setForm({ ...form, after: e.target.value })}
          >
            <option value="">at the end</option>
            ${phases.map((p) => html`<option value=${p.key}>${p.name}</option>`)}
          </select></label
        >
        <label class="row small"
          ><input
            type="checkbox"
            checked=${form.interview}
            onChange=${(e) => setForm({ ...form, interview: e.target.checked })}
          />
          interviews open</label
        >
        <button class="btn">Add round</button>
      </div>
    </form>
  </div>`;
}

// ------------------------------------------------------------------ teams
function Teams() {
  const { companies } = useStore(C);
  const [teams, reload] = usePoll(() => A("/teams"), 10000);
  const [names, setNames] = useState("");
  const [created, setCreated] = useState(null);
  const [open, setOpen] = useState(null);
  const [q, setQ] = useState("");
  useEffect(() => {
    refreshShared();
  }, []);
  const register = async () => {
    const list = names
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean);
    if (!list.length) return;
    const out = await run(
      () => A("/teams", { method: "POST", body: { names: list } }),
      `${list.length} teams registered`,
    );
    if (out) {
      setCreated(out);
      setNames("");
      reload();
    }
  };
  const csv = (rows) => {
    const body =
      "code,name,access_code\n" +
      rows
        .map(
          (r) => `${r.code},"${r.name.replace(/"/g, '""')}",${r.access_code}`,
        )
        .join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([body], { type: "text/csv" }));
    a.download = "team-codes.csv";
    a.click();
  };
  const shown = (teams || []).filter(
    (t) => !q || (t.code + t.name).toLowerCase().includes(q.toLowerCase()),
  );
  return html`<div class="col" style="gap:22px">
    <div class="row">
      <h1 class="hero-title" style="font-size:34px">Teams</h1>
      <span class="muted right">${teams?.length ?? "…"} teams</span>
      <button class="btn ghost small" onClick=${() => csv(teams || [])}>Download codes CSV</button>
    </div>
    <div class="grid2">
      <div class="panel">
        <div class="panel-h"><span class="strong">Register teams</span></div>
        <div class="panel-b col">
          <textarea
            class="textarea"
            rows="5"
            placeholder="One team name per line"
            value=${names}
            onInput=${(e) => setNames(e.target.value)}
          ></textarea>
          <button class="btn" onClick=${register} disabled=${!names.trim()}>
            Register
          </button>
          <p class="small muted">
            Read each team its access code; it stays in the table below.
          </p>
        </div>
      </div>
      <div class="panel">
        <div class="panel-h"><span class="strong">Companies</span></div>
        <div class="panel-b col">
          <p class="small" style="margin:0">
            Deal every team without a company into
            ${companies.map((c) => c.name).join(" / ") || "the companies"},
            evenly.
          </p>
          <div class="row">
            <button
              class="btn"
              onClick=${async () => {
                const r = await run(() =>
                  A("/teams/assign-companies", { method: "POST", body: {} }),
                );
                if (r) {
                  toast(`Assigned ${Object.keys(r.assigned).length} teams`);
                  reload();
                }
              }}
            >
              Assign unassigned
            </button>
            <button
              class="btn ghost"
              onClick=${async () => {
                if (
                  !confirm("Re-deal every team that hasn't asked anything yet?")
                )
                  return;
                const r = await run(() =>
                  A("/teams/assign-companies", {
                    method: "POST",
                    body: { only_unassigned: false },
                  }),
                );
                if (r) {
                  toast(`Re-dealt ${Object.keys(r.assigned).length} teams`);
                  reload();
                }
              }}
            >
              Re-deal idle teams
            </button>
          </div>
        </div>
      </div>
    </div>
    <input
      class="input"
      style="max-width:300px"
      placeholder="Filter teams…"
      value=${q}
      onInput=${(e) => setQ(e.target.value)}
    />
    <div style="overflow:auto">
      <table class="t">
        <thead>
          <tr>
            <th>Code</th>
            <th>Name</th>
            <th>Access code</th>
            <th>Company</th>
            <th>Laptops</th>
            <th>Qs</th>
            <th>Evidence</th>
            <th>Clues</th>
            <th>HR</th>
            <th>Lockouts</th>
            <th>Time back</th>
            <th>Cost</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${shown.map(
            (t) =>
              html`<tr key=${t.code}>
                <td><b class="codes">${t.code}</b></td>
                <td>${t.name}</td>
                <td class="codes">${t.access_code || "—"}</td>
                <td>
                  <select
                    class="select"
                    style="padding:3px 6px"
                    value=${t.company_id || ""}
                    onChange=${async (e) => {
                      await run(
                        () =>
                          A(`/teams/${t.code}/company`, {
                            method: "PUT",
                            body: { company: e.target.value || null },
                          }),
                        "Moved",
                      );
                      reload();
                    }}
                  >
                    <option value="">—</option>
                    ${companies.map((c) => html`<option value=${c.id}>${c.id} · ${c.name}</option>`)}
                  </select>
                </td>
                <td>${t.active_sessions}</td>
                <td>${t.questions_asked}</td>
                <td>${t.evidence_count}</td>
                <td>${t.clues_found}/${t.clues_total}</td>
                <td>${t.hr_flags}</td>
                <td>
                  ${
                    t.departments
                      .filter((d) => d.locked_for_seconds > 0)
                      .map(
                        (d) =>
                          `${d.department} ${fmtClock(d.locked_for_seconds)}`,
                      )
                      .join(", ") || "—"
                  }
                </td>
                <td>
                  ${t.time_bonus_seconds ? fmtClock(t.time_bonus_seconds) : "—"}
                </td>
                <td>$${t.cost_usd.toFixed(3)}</td>
                <td>
                  <button class="btn ghost small" onClick=${() => setOpen(t)}>
                    Open
                  </button>
                </td>
              </tr>`,
          )}
        </tbody>
      </table>
    </div>
    ${
      created &&
      html`<${Modal} title="New access codes" onClose=${() => setCreated(null)}>
      <table class="t"><thead><tr><th>Code</th><th>Name</th><th>Access code</th></tr></thead><tbody>${created.map(
        (r) =>
          html`<tr>
            <td>${r.code}</td>
            <td>${r.name}</td>
            <td class="codes">${r.access_code}</td>
          </tr>`,
      )}</tbody></table>
      <div class="row" style="margin-top:14px"><button class="btn" onClick=${() => csv(created)}>Download CSV</button><button class="btn ghost" onClick=${() => print()}>Print</button></div></${Modal}>`
    }
    ${
      open &&
      html`<${TeamDetail}
        t=${open}
        onClose=${() => {
          setOpen(null);
          reload();
        }}
      />`
    }
  </div>`;
}

function TeamDetail({ t, onClose }) {
  const [thread, setThread] = useState(null);
  const [clues, setClues] = useState(null);
  const [code, setCode] = useState(null);
  const [board, setBoard] = useState(false);
  useEffect(() => {
    run(() => A(`/teams/${t.code}/clues`)).then(setClues);
  }, [t.code]);
  const showThread = async (sid) =>
    setThread({
      sid,
      msgs: await run(() => A(`/teams/${t.code}/threads/${sid}`)),
    });
  return html`<${Modal} title=${`${t.code} · ${t.name}`} onClose=${onClose} wide=${true}>
    <div class="row" style="flex-wrap:wrap;margin-bottom:16px">
      <button class="btn ghost small" onClick=${() => run(() => A(`/teams/${t.code}/time`, { method: "POST", body: { seconds: 180 } }), "+3 min for this team")}>Give 3 min back</button>
      <button class="btn ghost small" onClick=${() => run(() => A(`/teams/${t.code}/time`, { method: "POST", body: { seconds: 300 } }), "+5 min for this team")}>Give 5 min back</button>
      <button class="btn danger small" onClick=${async () => {
        if (confirm("New access code? Their laptops get logged out."))
          setCode(
            await run(() =>
              A(`/teams/${t.code}/access-code`, { method: "POST" }),
            ),
          );
      }}>New access code</button>
      ${code && html`<span class="codes">${code.access_code}</span>`}
      <button class="btn danger small" onClick=${async () => {
        if (confirm(`Remove ${t.code} ${t.name}? Only possible before they've played.`) &&
            (await run(() => A(`/teams/${t.code}`, { method: "DELETE" }), "Removed")))
          onClose();
      }}>Remove team</button>
      <button class="btn ghost small" onClick=${() => setBoard(!board)}>${board ? "Hide" : "View"} evidence board</button>
    </div>
    ${board && html`<${BoardView} code=${t.code} />`}
    <table class="t"><thead><tr><th>Stakeholder</th><th>Dept</th><th>Trust</th><th>Tier</th><th>Qs used</th><th>Strikes</th><th>Lockout</th><th></th></tr></thead>
      <tbody>${t.stakeholders.map(
        (s) =>
          html`<tr>
            <td>${s.stakeholder_id}</td>
            <td>${s.department}</td>
            <td>${s.trust}</td>
            <td>${s.tier}</td>
            <td>${s.questions_used}/${s.quota}</td>
            <td>${s.strikes}</td>
            <td>
              ${s.locked_for_seconds ? fmtClock(s.locked_for_seconds) : "—"}
            </td>
            <td style="white-space:nowrap">
              <button
                class="btn link small"
                onClick=${() => showThread(s.stakeholder_id)}
              >
                Thread
              </button>
              ${s.locked_for_seconds > 0 && html`<button class="btn link small" onClick=${() => run(() => A(`/teams/${t.code}/stakeholders/${s.stakeholder_id}`, { method: "PATCH", body: { clear_lockout: true } }), "Lockout lifted")}>Lift lockout</button>`}
              <button
                class="btn link small"
                onClick=${() => run(() => A(`/teams/${t.code}/stakeholders/${s.stakeholder_id}`, { method: "PATCH", body: { quota: s.quota + 2 } }), "+2 questions")}
              >
                +2 Qs
              </button>
            </td>
          </tr>`,
      )}</tbody></table>
    ${clues && html`<p class="small" style="margin-top:14px"><b>Clues found:</b> ${clues.found.map((c) => c.clue).join(", ") || "none"} · <span class="muted">missing: ${clues.missing.map((c) => c.clue).join(", ") || "none"}</span></p>`}
    ${
      thread &&
      html`<div class="col" style="margin-top:16px">
        <div class="kicker">Thread with ${thread.sid}</div>
        ${(thread.msgs || []).map(
          (m) =>
            html`<div
              class="alert"
              style=${m.role === "question" ? "" : "border-color:var(--muted)"}
            >
              <div class="tiny muted">
                ${m.role} ·
                ${m.status}${m.meta ? ` · ${m.meta.label || ""} · trust ${m.meta.trust_before ?? ""}${m.meta.trust_delta != null ? (m.meta.trust_delta >= 0 ? "+" : "") + m.meta.trust_delta : ""}${m.meta.deflected ? " · DEFLECTED" : ""}${m.meta.failed_checks?.length ? " · blocked: " + m.meta.failed_checks.join(",") : ""}` : ""}
              </div>
              <div class="q">${m.text}</div>
              ${m.cited?.length ? html`<div class="tiny muted">cited ${m.cited.join(", ")}</div>` : null}
            </div>`,
        )}
      </div>`
    }
  </${Modal}>`;
}

// ------------------------------------------------------------------ leaderboard
const CRITERIA = [["accuracy", "Accuracy", 4], ["reasoning", "Reasoning", 3], ["recommendations", "Recs", 2], ["clarity", "Clarity", 1]];
function Leaderboard() {
  const { phases } = useStore(C);
  const [round, setRound] = useState("");
  const [edits, setEdits] = useState({});
  const [lb, reload] = usePoll(() => A("/leaderboard"), 5000, []);
  useEffect(() => {
    refreshShared();
  }, []);
  useEffect(() => {
    if (!round && phases.length) setRound(phases[0].key);
  }, [phases]);
  const cur = (t) => ({
    points: t.rounds?.[round] ?? "",
    note: t.notes?.[round] ?? "",
    breakdown: t.breakdowns?.[round] || {},
    ...edits[t.team],
  });
  const set = (t, patch) =>
    setEdits((e) => {
      const next = { ...cur(t), ...patch };
      if (patch.breakdown) {
        const vals = Object.values(next.breakdown).filter((v) => v !== "" && v != null);
        if (vals.length) next.points = vals.reduce((x, y) => x + Number(y), 0);
      }
      return { ...e, [t.team]: next };
    });
  const save = async () => {
    const entries = Object.entries(edits).map(([team, e]) => ({
      team,
      points: e.points === "" || e.points == null ? null : Number(e.points),
      note: e.note || "",
      breakdown: Object.fromEntries(Object.entries(e.breakdown || {}).filter(([, v]) => v !== "" && v != null).map(([k, v]) => [k, Number(v)])),
    }));
    if (!entries.length) return toast("Nothing changed.");
    if (await run(() => A(`/phases/${round}/scores`, { method: "PUT", body: { entries } }), "Scores saved — leaderboard updated")) {
      setEdits({});
      reload();
    }
  };
  const teams = lb?.teams || [];
  const scored = teams.filter((t) => t.rounds?.[round] != null);
  const stat = (vals) => vals.length ? `avg ${(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1)} · min ${Math.min(...vals)} · max ${Math.max(...vals)}` : "—";
  const buckets = Array.from({ length: 11 }, (_, i) => scored.filter((t) => Math.floor(t.rounds[round]) === i || (i === 10 && t.rounds[round] > 10)).length);
  const peak = Math.max(1, ...buckets);
  return html`<div class="col" style="gap:20px">
    <h1 class="hero-title" style="font-size:34px">Leaderboard</h1>
    <p class="small muted" style="margin:0;max-width:680px">
      Pick a round, edit scores and save. Teams see nothing until you press
      "Show to teams" for that round; after that, every save updates their Rounds page live.
      Filling the criteria adds them up into the points (you can still overwrite the points).
      Criteria and feedback are for organisers only. Clear the points to remove a score.
    </p>
    <div class="row" style="flex-wrap:wrap">
      <select class="select" style="max-width:240px" value=${round} onChange=${(e) => { setRound(e.target.value); setEdits({}); }}>
        ${phases.map((p) => html`<option value=${p.key}>Score: ${p.name || p.key}</option>`)}
      </select>
      <button class="btn small" onClick=${save}>Save scores (${Object.keys(edits).length})</button>
      ${(() => {
        const r = (lb?.rounds || []).find((x) => x.key === round);
        if (!r) return html`<span class="small muted">No scores saved for this round yet.</span>`;
        return html`<span class=${"chip" + (r.visible ? " solid" : "")}>${r.visible ? "Visible to teams" : "Hidden from teams"}</span>
          <button class=${"btn small " + (r.visible ? "ghost" : "danger")} onClick=${async () => {
            if (!r.visible && !confirm(`Show ${r.name} scores to every team now?`)) return;
            await run(() => A(`/phases/${round}/scores/visible`, { method: "PUT", body: { visible: !r.visible } }), r.visible ? "Hidden from teams" : "Now visible to teams");
            reload();
          }}>${r.visible ? "Hide from teams" : "Show to teams"}</button>`;
      })()}
    </div>
    <div class="alert">
      <div class="kicker">Distribution · ${round} · ${scored.length} scored</div>
      <div class="small">Total: ${stat(scored.map((t) => t.rounds[round]))}</div>
      ${CRITERIA.map(([k, l, max]) => html`<div class="small">${l} (/${max}): ${stat(scored.map((t) => t.breakdowns?.[round]?.[k]).filter((v) => v != null))}</div>`)}
      <div style="display:flex;gap:4px;align-items:flex-end;height:70px;margin-top:10px">
        ${buckets.map((n, i) => html`<div style="flex:1;text-align:center" title=${`${n} team(s) scored ${i}${i === 10 ? "+" : "–" + (i + 0.5)}`}>
          <div style=${`background:var(--fg,#444);opacity:.7;height:${(n / peak) * 50}px`}></div>
          <div class="tiny muted">${i}</div>
        </div>`)}
      </div>
    </div>
    <table class="t"><thead><tr><th>#</th><th>Team</th><th>Co.</th>
      ${(lb?.rounds || []).map((r) => html`<th>${r.name}${r.visible ? "" : " (hidden)"}</th>`)}
      <th>Total</th>
      ${CRITERIA.map(([, l, max]) => html`<th>${l} /${max}</th>`)}
      <th>${round} pts</th></tr></thead>
      <tbody>${teams.map((t) => {
        const c = cur(t);
        return html`<tr>
          <td>${t.rank}</td><td>${t.team} · ${t.name}</td><td>${t.company || "—"}</td>
          ${(lb?.rounds || []).map((r) => html`<td>${t.rounds[r.key] ?? "—"}</td>`)}
          <td><b>${t.total}</b></td>
          ${CRITERIA.map(([k, , max]) => html`<td><input class="input" style="width:60px" type="number" step="0.5" min="0" max=${max}
            value=${c.breakdown[k] ?? ""}
            onInput=${(e) => set(t, { breakdown: { ...c.breakdown, [k]: e.target.value } })} /></td>`)}
          <td><input class="input" style="width:70px" type="number" step="0.5" value=${c.points}
            onInput=${(e) => set(t, { points: e.target.value })} /></td>
        </tr>
        <tr><td></td><td colspan="99">
          <textarea class="input" rows="2" style="width:100%" placeholder="Feedback (organisers only)"
            value=${c.note} onInput=${(e) => set(t, { note: e.target.value })}></textarea>
        </td></tr>`;
      })}</tbody></table>
  </div>`;
}

function BoardView({ code }) {
  const [b, setB] = useState(null);
  useEffect(() => {
    run(() => A(`/teams/${code}/board`)).then(setB);
  }, [code]);
  if (!b) return html`<p class="small muted">Loading board…</p>`;
  const byId = Object.fromEntries(b.cards.map((c) => [c.id, c]));
  const label = (c) =>
    !c ? "?" : c.kind === "evidence" ? `${c.record_id || c.passage_id}` : c.kind === "quote" ? `quote (${c.stakeholder_id})` : "note";
  return html`<div class="col" style="margin-top:16px;gap:8px">
    <div class="kicker">Evidence board · v${b.version} · ${b.cards.length} cards · ${b.links.length} links${b.updated_at ? " · saved " + ago(b.updated_at) : ""}</div>
    ${b.cards.map((c) => html`<div class="alert">
      <div class="tiny muted">${c.kind}${c.kind === "evidence" ? ` · ${c.passage_id}${c.record_title ? " · " + c.record_title : ""}` : ""}${c.kind === "quote" ? ` · ${c.stakeholder_id}` : ""}</div>
      <div class="q">${c.text || ""}</div>
    </div>`)}
    ${b.links.length ? html`<div class="kicker" style="margin-top:8px">Links</div>
      ${b.links.map((l) => html`<div class="small">${label(byId[l.from])} —${l.label || "linked"}→ ${label(byId[l.to])}</div>`)}` : null}
  </div>`;
}

// ------------------------------------------------------------------ results
const STATUSES = ["advancing", "eliminated", "waitlisted", "pending"];
function Results() {
  const { phases } = useStore(C);
  const [round, setRound] = useState("");
  const [rows, setRows] = useState(null);
  const [edits, setEdits] = useState({});
  useEffect(() => {
    refreshShared();
  }, []);
  useEffect(() => {
    if (!round && phases.length) setRound(phases[0].key);
  }, [phases]);
  const load = async () => {
    if (round) {
      setRows(await run(() => A(`/phases/${round}/statuses`)));
      setEdits({});
    }
  };
  useEffect(() => {
    load();
  }, [round]);
  const set = (team, field, v) =>
    setEdits((e) => ({
      ...e,
      [team]: { ...rows.find((r) => r.team === team), ...e[team], [field]: v },
    }));
  const save = async (publish) => {
    const entries = Object.values(edits)
      .filter((e) => e.status)
      .map((e) => ({
        team: e.team,
        status: e.status,
        message: e.message || "",
      }));
    if (entries.length)
      await run(
        () =>
          A(`/phases/${round}/statuses`, {
            method: "PUT",
            body: { entries, publish },
          }),
        publish ? "Saved and published" : "Drafts saved",
      );
    else if (!publish) return toast("Nothing changed.");
    if (publish)
      await run(
        () => A(`/phases/${round}/statuses/publish`, { method: "POST" }),
        "Published",
      );
    load();
  };
  const all = (status) =>
    setEdits(
      Object.fromEntries(
        (rows || []).map((r) => [r.team, { ...r, ...edits[r.team], status }]),
      ),
    );
  const drafts =
    (rows || []).filter((r) => r.status && !r.published).length +
    Object.keys(edits).length;
  return html`<div class="col" style="gap:20px">
    <h1 class="hero-title" style="font-size:34px">Round results</h1>
    <p class="small muted" style="margin:0;max-width:640px">
      Set each team's status for a round and a message they'll see. Drafts are
      invisible to teams until you publish; publishing shows every team its
      result at once. A published "eliminated" closes interviews for that team
      in that round.
    </p>
    <div class="row" style="flex-wrap:wrap">
      <select
        class="select"
        style="max-width:240px"
        value=${round}
        onChange=${(e) => setRound(e.target.value)}
      >
        ${phases.map((p) => html`<option value=${p.key}>${p.name}</option>`)}
      </select>
      <span class="small muted">Set all to</span
      >${STATUSES.map((s) => html`<button class="btn ghost small" onClick=${() => all(s)}>${s}</button>`)}
      <span class="right row"
        ><button class="btn ghost" onClick=${() => save(false)}>
          Save drafts</button
        ><button
          class="btn"
          onClick=${() => confirm("Publish every draft for this round? Teams see it immediately.") && save(true)}
        >
          Publish (${drafts})
        </button></span
      >
    </div>
    ${
      rows &&
      html`<table class="t">
        <thead>
          <tr>
            <th>Team</th>
            <th>Company</th>
            <th>Status</th>
            <th>Message to the team</th>
            <th>Visible</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((r) => {
            const e = { ...r, ...edits[r.team] };
            return html`<tr key=${r.team}>
              <td><b>${r.team}</b> ${r.name}</td>
              <td>${r.company || "—"}</td>
              <td>
                <select
                  class="select"
                  style="padding:3px 6px"
                  value=${e.status || ""}
                  onChange=${(ev) => set(r.team, "status", ev.target.value)}
                >
                  <option value="">—</option>
                  ${STATUSES.map((s) => html`<option value=${s}>${s}</option>`)}
                </select>
              </td>
              <td>
                <input
                  class="input"
                  style="padding:4px 8px"
                  value=${e.message || ""}
                  placeholder="Optional"
                  onInput=${(ev) => set(r.team, "message", ev.target.value)}
                />
              </td>
              <td>
                ${r.published ? html`<span class="chip solid">published</span>` : r.status ? html`<span class="chip">draft</span>` : "—"}${edits[r.team] ? html` <span class="chip">edited</span>` : ""}
              </td>
            </tr>`;
          })}
        </tbody>
      </table>`
    }
  </div>`;
}

// ------------------------------------------------------------------ documents
function RecordsTabSwitch() {
  const [cfg, reload] = usePoll(() => A("/config"), 0);
  const mode = cfg?.records_tab?.mode || "off";
  const opts = [
    ["off", "Off", "records come out only in interviews"],
    ["titles", "Titles", "teams see what exists, not what it says"],
    ["full", "Full", "teams read their company's documents and can cite them"],
  ];
  const set = async (m) => {
    if (m === mode) return;
    if (
      m === "full" &&
      !confirm(
        "Show every team its company's documents in full? Interviews stop being the only way in, and every passage on the tab becomes citable.",
      )
    )
      return;
    await run(
      () =>
        A("/config", { method: "PATCH", body: { records_tab: { mode: m } } }),
      `Records tab: ${m}`,
    );
    reload();
  };
  return html`<div class="panel">
    <div class="panel-h">
      <span class="strong">Records tab</span
      ><span class="small muted">${opts.find((o) => o[0] === mode)?.[2]}</span>
    </div>
    <div class="panel-b row" style="gap:8px">
      ${opts.map(([m, l]) => html`<button class=${"btn" + (m === mode ? "" : " ghost")} onClick=${() => set(m)}>${l}</button>`)}
      <span class="small muted right"
        >Never shown: "in confidence" records and deleted text. Partners'
        documents appear after an alliance locks.</span
      >
    </div>
  </div>`;
}

function Documents() {
  const { phases, companies } = useStore(C);
  const [docs, reload] = usePoll(() => A("/documents"), 0);
  const [f, setF] = useState({
    title: "",
    body: "",
    file: null,
    round: "",
    company: "",
    teams: "",
    release_now: true,
  });
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    refreshShared();
  }, []);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    const body = { title: f.title, release_now: f.release_now };
    if (f.round) body.round = f.round;
    if (f.company) body.company = f.company;
    if (f.teams.trim())
      body.teams = f.teams
        .split(/[\s,]+/)
        .filter(Boolean)
        .map((x) => x.toUpperCase());
    if (f.file) {
      if (f.file.size > 15 * 1024 * 1024) {
        setBusy(false);
        return toast("Files are limited to 15 MB", "err");
      }
      const b64 = await new Promise((ok) => {
        const r = new FileReader();
        r.onload = () => ok(String(r.result).split(",")[1]);
        r.readAsDataURL(f.file);
      });
      body.file = {
        filename: f.file.name,
        content_type: f.file.type || "application/octet-stream",
        data_base64: b64,
      };
    } else body.body = f.body;
    const out = await run(
      () => A("/documents", { method: "POST", body }),
      "Document added",
    );
    setBusy(false);
    if (out) {
      setF({ ...f, title: "", body: "", file: null });
      reload();
    }
  };
  return html`<div class="col" style="gap:22px">
    <h1 class="hero-title" style="font-size:34px">Documents</h1>
    <${RecordsTabSwitch} />
    <form class="panel" onSubmit=${submit}>
      <div class="panel-h"><span class="strong">Hand out a document</span></div>
      <div class="panel-b col">
        <label class="field"
          >Title<input
            class="input"
            required
            value=${f.title}
            onInput=${(e) => setF({ ...f, title: e.target.value })}
        /></label>
        <div class="grid2">
          <label class="field"
            >A file (PDF, sheet, image — up to 15 MB)<input
              type="file"
              onChange=${(e) => setF({ ...f, file: e.target.files[0] || null })}
          /></label>
          <label class="field"
            >…or a note (markdown)<textarea
              class="textarea"
              rows="4"
              disabled=${!!f.file}
              value=${f.body}
              onInput=${(e) => setF({ ...f, body: e.target.value })}
            ></textarea>
          </label>
        </div>
        <div class="row" style="flex-wrap:wrap;align-items:flex-end">
          <label class="field"
            >Company<select
              class="select"
              value=${f.company}
              onChange=${(e) => setF({ ...f, company: e.target.value })}
            >
              <option value="">everyone</option>
              ${companies.map((c) => html`<option value=${c.id}>${c.name}</option>`)}
            </select></label
          >
          <label class="field"
            >Only these teams (codes)<input
              class="input"
              placeholder="T001, T004"
              value=${f.teams}
              onInput=${(e) => setF({ ...f, teams: e.target.value })}
          /></label>
          <label class="field"
            >Release with round<select
              class="select"
              value=${f.round}
              onChange=${(e) => setF({ ...f, round: e.target.value })}
            >
              <option value="">—</option>
              ${phases.map((p) => html`<option value=${p.key}>${p.name}</option>`)}
            </select></label
          >
          <label class="row small"
            ><input
              type="checkbox"
              checked=${f.release_now}
              onChange=${(e) => setF({ ...f, release_now: e.target.checked })}
            />
            release now</label
          >
          <button class="btn" disabled=${busy || (!f.file && !f.body.trim())}>
            ${busy ? "Uploading…" : "Add"}
          </button>
        </div>
      </div>
    </form>
    <table class="t">
      <thead>
        <tr>
          <th>Title</th>
          <th>Kind</th>
          <th>Audience</th>
          <th>Round</th>
          <th>Visible</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${(docs || []).map(
          (d) =>
            html`<tr key=${d.id}>
              <td>${d.title}</td>
              <td>
                ${d.kind === "file" ? `${d.filename} · ${bytes(d.size_bytes)}` : "note"}
              </td>
              <td>
                ${d.teams ? d.teams.join(", ") : d.company ? `company ${d.company}` : "everyone"}
              </td>
              <td>${d.round || "—"}</td>
              <td>
                ${d.visible ? html`<span class="chip solid">visible</span>` : html`<span class="chip">hidden</span>`}
              </td>
              <td style="white-space:nowrap">
                ${
                  d.released_at
                    ? html`<button
                        class="btn link small"
                        onClick=${async () => {
                          await run(() =>
                            A(`/documents/${d.id}`, {
                              method: "PATCH",
                              body: { released: false },
                            }),
                          );
                          reload();
                        }}
                      >
                        Withdraw
                      </button>`
                    : html`<button
                        class="btn link small"
                        onClick=${async () => {
                          await run(
                            () =>
                              A(`/documents/${d.id}`, {
                                method: "PATCH",
                                body: { released: true },
                              }),
                            "Released",
                          );
                          reload();
                        }}
                      >
                        Release now
                      </button>`
                }
                <button
                  class="btn link small"
                  onClick=${async () => {
                    if (confirm("Delete this document?")) {
                      await run(() =>
                        A(`/documents/${d.id}`, { method: "DELETE" }),
                      );
                      reload();
                    }
                  }}
                >
                  Delete
                </button>
              </td>
            </tr>`,
        )}
      </tbody>
    </table>
  </div>`;
}

// ------------------------------------------------------------------ announcements
function Announcements() {
  const { companies } = useStore(C);
  const [list, reload] = usePoll(() => A("/announcements"), 0);
  const [text, setText] = useState("");
  const [company, setCompany] = useState("");
  const send = async (e) => {
    e.preventDefault();
    await run(
      () =>
        A("/announcements", {
          method: "POST",
          body: { text, company: company || null },
        }),
      "Sent",
    );
    setText("");
    reload();
  };
  return html`<div class="col" style="gap:22px">
    <h1 class="hero-title" style="font-size:34px">Announcements</h1>
    <form class="panel" onSubmit=${send}>
      <div class="panel-b col">
        <textarea
          class="textarea"
          maxlength="1000"
          placeholder="What everyone should know right now"
          value=${text}
          onInput=${(e) => setText(e.target.value)}
        ></textarea>
        <div class="row">
          <select
            class="select"
            style="max-width:240px"
            value=${company}
            onChange=${(e) => setCompany(e.target.value)}
          >
            <option value="">Everyone (and the projector)</option>
            ${companies.map((c) => html`<option value=${c.id}>${c.name} only</option>`)}
          </select>
          <button class="btn right" disabled=${!text.trim()}>Broadcast</button>
        </div>
      </div>
    </form>
    <div class="col">
      ${(list || []).map(
        (a) =>
          html`<div class="alert" style=${a.retracted_at ? "opacity:.45" : ""}>
            <div class="row tiny muted">
              <span
                >${ago(a.created_at)} ·
                ${a.company ? `company ${a.company}` : "everyone"}${a.retracted_at ? " · retracted" : ""}</span
              >
              ${
                !a.retracted_at &&
                html`<button
                  class="btn link small right"
                  onClick=${async () => {
                    await run(
                      () => A(`/announcements/${a.id}`, { method: "DELETE" }),
                      "Retracted",
                    );
                    reload();
                  }}
                >
                  Retract
                </button>`
              }
            </div>
            <div class="q">${a.text}</div>
          </div>`,
      )}
    </div>
  </div>`;
}

// ------------------------------------------------------------------ alerts
function Alerts({ compact }) {
  const [alerts] = usePoll(() => A("/alerts?limit=50"), 8000);
  const [flags] = usePoll(
    () => (compact ? Promise.resolve([]) : A("/hr-flags?limit=100")),
    compact ? 0 : 15000,
  );
  return html`<div class="col" style="gap:16px">
    ${!compact && html`<h1 class="hero-title" style="font-size:34px">Alerts</h1>`}
    <div class="kicker">
      Prompt attacks, hostility and blocked drafts${compact ? " (latest)" : ""}
    </div>
    ${(alerts || []).slice(0, compact ? 6 : 50).map(
      (a) =>
        html`<div class="alert">
          <div class="row tiny">
            <b>${a.team}</b
            ><span>→ ${a.stakeholder_id}</span
            >${a.reasons.map((r) => html`<span class="chip">${r}</span>`)}${a.walked_out ? html`<span class="chip solid">walk-out</span>` : ""}<span
              class="right muted"
              >${ago(a.at)}</span
            >
          </div>
          <div class="q">${a.question}</div>
        </div>`,
    )}
    ${alerts && !alerts.length && html`<div class="empty small">Nothing suspicious yet.</div>`}
    ${
      !compact &&
      html`<div class="kicker" style="margin-top:12px">HR log</div>
        <table class="t">
          <thead>
            <tr>
              <th>When</th>
              <th>Team</th>
              <th>Who</th>
              <th>Dept</th>
              <th>Label</th>
              <th>Outcome</th>
              <th>Question</th>
            </tr>
          </thead>
          <tbody>
            ${(flags || []).map(
              (f) =>
                html`<tr>
                  <td>${ago(f.at)}</td>
                  <td>${f.team}</td>
                  <td>${f.stakeholder_id}</td>
                  <td>${f.department}</td>
                  <td>${f.label}</td>
                  <td>${f.outcome}</td>
                  <td>${f.question}</td>
                </tr>`,
            )}
          </tbody>
        </table>`
    }
  </div>`;
}

// ------------------------------------------------------------------ alliances
function Alliances() {
  const [list, reload] = usePoll(() => A("/alliances"), 10000);
  const [codes, setCodes] = useState("");
  const pair = async () => {
    const teams = codes
      .split(/[\s,]+/)
      .filter(Boolean)
      .map((x) => x.toUpperCase());
    if (
      await run(
        () => A("/alliances", { method: "POST", body: { teams } }),
        "Alliance locked",
      )
    ) {
      setCodes("");
      reload();
    }
  };
  return html`<div class="col" style="gap:22px">
    <h1 class="hero-title" style="font-size:34px">Alliances</h1>
    <div class="panel">
      <div class="panel-h">
        <span class="strong">Pair teams yourself</span
        ><span class="small muted"
          >2 or 3 teams, from both companies; locked at once</span
        >
      </div>
      <div class="panel-b row">
        <input
          class="input"
          placeholder="T001, T014 (, T020)"
          value=${codes}
          onInput=${(e) => setCodes(e.target.value)}
        /><button class="btn" onClick=${pair} disabled=${!codes.trim()}>
          Lock alliance
        </button>
      </div>
    </div>
    <table class="t">
      <thead>
        <tr>
          <th>Status</th>
          <th>Teams</th>
          <th>Made by</th>
          <th>Since</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${(list || []).map(
          (a) =>
            html`<tr key=${a.id}>
              <td>
                <span class=${"chip" + (a.status === "locked" ? " solid" : "")}
                  >${a.status}</span
                >
              </td>
              <td>
                ${a.teams.map((t) => html`<span style="margin-right:12px"><b>${t.code}</b> <span class="muted">${t.company}</span>${a.status === "pending" && !t.confirmed ? " (waiting)" : ""}</span>`)}
              </td>
              <td>${a.by_organisers ? "organisers" : "teams"}</td>
              <td>${ago(a.locked_at || a.created_at)}</td>
              <td>
                <button
                  class="btn danger small"
                  onClick=${async () => {
                    if (
                      confirm(
                        "Dissolve this alliance? Partners stop seeing each other's evidence.",
                      )
                    ) {
                      await run(
                        () => A(`/alliances/${a.id}`, { method: "DELETE" }),
                        "Dissolved",
                      );
                      reload();
                    }
                  }}
                >
                  Dissolve
                </button>
              </td>
            </tr>`,
        )}
      </tbody>
    </table>
  </div>`;
}

// ------------------------------------------------------------------ submissions
function Submissions() {
  const { phases } = useStore(C);
  const [round, setRound] = useState("");
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(null);
  useEffect(() => {
    refreshShared();
  }, []);
  const load = async () =>
    setData(
      await run(() => A(`/submissions${round ? `?round=${round}` : ""}`)),
    );
  useEffect(() => {
    load();
  }, [round]);
  const view = async (code) =>
    setOpen(await run(() => A(`/submissions/${code}?round=${data.round}`)));
  const done = (data?.teams || []).filter((t) => t.submitted_at).length;
  return html`<div class="col" style="gap:20px">
    <div class="row">
      <h1 class="hero-title" style="font-size:34px">Submissions</h1>
      <select
        class="select right"
        style="max-width:240px"
        value=${round || data?.round || ""}
        onChange=${(e) => setRound(e.target.value)}
      >
        ${phases.map((p) => html`<option value=${p.key}>${p.name}</option>`)}
      </select>
      <button class="btn ghost small" onClick=${load}>Refresh</button>
    </div>
    ${
      data &&
      html`<p class="small muted" style="margin:0">
          ${done} of ${data.teams.length} teams have submitted for
          ${data.round}.
        </p>
        <table class="t">
          <thead>
            <tr>
              <th>Team</th>
              <th>Company</th>
              <th>Allied</th>
              <th>Versions</th>
              <th>Last saved</th>
              <th>Submitted</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${data.teams.map(
              (t) =>
                html`<tr key=${t.team}>
                  <td><b>${t.team}</b> ${t.name}</td>
                  <td>${t.company || "—"}</td>
                  <td>${t.allied ? "yes" : "—"}</td>
                  <td>${t.versions}</td>
                  <td>${t.last_saved_at ? ago(t.last_saved_at) : "—"}</td>
                  <td>
                    ${t.submitted_at ? new Date(t.submitted_at).toLocaleTimeString() : html`<span class="muted">not yet</span>`}
                  </td>
                  <td>
                    ${t.versions ? html`<button class="btn ghost small" onClick=${() => view(t.team)}>Read</button>` : null}
                  </td>
                </tr>`,
            )}
          </tbody>
        </table>`
    }
    ${
      open &&
      html`<${Modal} title=${`${open.team} · ${open.name || ""} · ${open.round}`} wide=${true} onClose=${() => setOpen(null)}>
      ${
        !open.sheet
          ? html`<div class="empty">Nothing saved.</div>`
          : html`<div class="col">
              <div class="row small">
                ${open.never_submitted ? html`<span class="chip">never submitted: last draft shown</span>` : html`<span class="chip solid">submitted v${open.sheet.version}</span>`}<span
                  class="muted"
                  >saved ${new Date(open.sheet.saved_at).toLocaleString()}</span
                >
              </div>
              ${
                open.sheet.summary &&
                html`<div class="alert">
                  <div class="tiny muted">Summary</div>
                  <div class="q">${open.sheet.summary}</div>
                </div>`
              }
              ${open.sheet.findings.map(
                (f, i) =>
                  html`<div class="panel">
                    <div class="panel-h">
                      <span class="pixel" style="font-size:10px">F${i + 1}</span
                      ><span class="strong">${f.title}</span
                      >${f.cross_company ? html`<span class="chip holo right">cross-company</span>` : ""}
                    </div>
                    <div class="panel-b col small">
                      <div><b>Finding.</b> ${f.finding}</div>
                      ${f.impact && html`<div><b>Impact.</b> ${f.impact}</div>`}${f.recommendation && html`<div><b>Recommendation.</b> ${f.recommendation}</div>`}
                      <div class="tiny muted">
                        Sources: ${f.sources.join(", ") || "none"}
                      </div>
                    </div>
                  </div>`,
              )}
            </div>`
      }
    </${Modal}>`
    }
  </div>`;
}

// ------------------------------------------------------------------ settings
function Settings() {
  const [cfg, reload] = usePoll(() => A("/config"), 0);
  const [costs] = usePoll(() => A("/costs"), 0);
  const [text, setText] = useState("");
  const [quota, setQuota] = useState(10);
  const [path, setPath] = useState("content/case.toml");
  useEffect(() => {
    if (cfg) setText(JSON.stringify(cfg, null, 2));
  }, [cfg]);
  const patch = async () => {
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      return toast("That isn't valid JSON", "err");
    }
    const allowed = [
      "tier_thresholds",
      "trust_rules",
      "hr",
      "evidence_token_budget",
      "max_passages",
      "models",
      "trust_start",
      "default_quota",
    ];
    const send = Object.fromEntries(
      Object.entries(body).filter(([k]) => allowed.includes(k)),
    );
    await run(
      () => A("/config", { method: "PATCH", body: send }),
      "Settings saved",
    );
    reload();
  };
  return html`<div class="col" style="gap:22px">
    <h1 class="hero-title" style="font-size:34px">Settings</h1>
    <div class="grid2">
      <div class="panel">
        <div class="panel-h">
          <span class="strong">Question quota (every team)</span>
        </div>
        <div class="panel-b row">
          <input
            class="input"
            type="number"
            min="1"
            style="width:100px"
            value=${quota}
            onInput=${(e) => setQuota(+e.target.value)}
          />
          <button
            class="btn"
            onClick=${() => confirm(`Set every team's quota per stakeholder to ${quota}?`) && run(() => A("/quota", { method: "POST", body: { quota } }), "Quota updated")}
          >
            Apply
          </button>
        </div>
      </div>
      <div class="panel">
        <div class="panel-h"><span class="strong">Reload the case</span></div>
        <div class="panel-b row">
          <input
            class="input"
            value=${path}
            onInput=${(e) => setPath(e.target.value)}
          />
          <button
            class="btn"
            onClick=${() => confirm("Reload the case from this file? It's validated first.") && run(() => A("/content/reload", { method: "POST", body: { path } }), "Case reloaded")}
          >
            Reload
          </button>
        </div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-h">
        <span class="strong">Live settings</span
        ><span class="small muted"
          >only tier_thresholds, trust_rules, hr, evidence_token_budget,
          max_passages, models, trust_start, default_quota are changeable</span
        >
      </div>
      <div class="panel-b col">
        <textarea
          class="textarea"
          rows="18"
          style="font-size:12px"
          value=${text}
          onInput=${(e) => setText(e.target.value)}
        ></textarea>
        <button class="btn" onClick=${patch}>Save</button>
      </div>
    </div>
    ${
      costs &&
      html`<div class="panel">
        <div class="panel-h"><span class="strong">Costs</span></div>
        <div class="panel-b">
          <pre class="small" style="margin:0;white-space:pre-wrap">
${JSON.stringify(costs, null, 2)}</pre>
        </div>
      </div>`
    }
  </div>`;
}

// ------------------------------------------------------------------ app
const TABS = [
  ["live", "Live", Live],
  ["rounds", "Rounds", Rounds],
  ["teams", "Teams", Teams],
  ["results", "Results", Results],
  ["leaderboard", "Leaderboard", Leaderboard],
  ["alliances", "Alliances", Alliances],
  ["submissions", "Submissions", Submissions],
  ["documents", "Documents", Documents],
  ["announcements", "Announcements", Announcements],
  ["alerts", "Alerts", Alerts],
  ["settings", "Settings", Settings],
];
function Console() {
  const { token, tab } = useStore(C);
  useEffect(() => {
    if (token) refreshShared();
  }, [token]);
  if (!token) return html`<${TokenGate} /><${Toasts} />`;
  const [, , Page] = TABS.find((t) => t[0] === tab) || TABS[0];
  return html`<div class="console">
    <nav class="console-nav">
      <div style="margin-bottom:18px">
        <${Logo} />
        <div class="tiny muted" style="margin-top:6px">Organiser console</div>
      </div>
      ${TABS.map(([k, l]) => html`<a href=${"#/" + k} class=${tab === k ? "on" : ""}>${l}</a>`)}
      <div style="margin-top:auto" class="col">
        <a href="/display" target="_blank" class="small">Projector view ↗</a>
        <button
          class="btn ghost small"
          onClick=${() => {
            sessionStorage.removeItem("px_admin");
            C.set({ token: "" });
          }}
        >
          Lock console
        </button>
      </div>
    </nav>
    <main class="console-main"><${Page} /></main>
    <${Toasts} />
  </div>`;
}

render(html`<${Console} />`, document.getElementById("app"));
