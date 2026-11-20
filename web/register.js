// Team sign-up. The access code is never shown here: it goes to every member's inbox.

import { html, render, useEffect, useState } from "./vendor/preact-htm.js";
import { api, Logo, Toasts } from "./lib.js";

function Register() {
  const [rules, setRules] = useState(null);
  const [name, setName] = useState("");
  const [members, setMembers] = useState([
    { name: "", email: "" },
    { name: "", email: "" },
  ]);
  const [err, setErr] = useState("");
  const [done, setDone] = useState(null);
  const [busy, setBusy] = useState(false);
  const [hp, setHp] = useState("");
  useEffect(() => {
    api("/registration")
      .then((r) => {
        setRules(r);
      })
      .catch(() => setRules({ open: false }));
  }, []);
  const set = (i, k, v) =>
    setMembers(members.map((m, j) => (j === i ? { ...m, [k]: v } : m)));
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      setDone(
        await api("/registration", {
          method: "POST",
          body: { team_name: name, members, website: hp },
        }),
      );
    } catch (e2) {
      const d = e2.details || {};
      setErr(
        {
          BAD_EMAIL: `Use @${rules.domain} addresses${d.emails ? `: check ${d.emails.join(", ")}` : ""}.`,
          EMAIL_TAKEN: `Already registered with another team: ${(d.emails || []).join(", ")}.`,
          NAME_TAKEN: "Another team already has that name.",
          BAD_TEAM_SIZE: `Teams have ${rules.min_members} to ${rules.max_members} members.`,
          REGISTRATION_CLOSED: "Registration is closed.",
        }[e2.code] || e2.message,
      );
    }
    setBusy(false);
  };
  const side = html`<section class="login-art">
    <${Logo} />
    <div>
      <div class="kicker">Team registration</div>
      <h1 class="hero-title px" style="margin-top:18px">PERPLEXED<br />2.0</h1>
      <p class="muted" style="margin-top:22px">
        Register once per team. Every member gets the same access code by email,
        at their ${rules?.domain || "thapar.edu"} address.
      </p>
    </div>
    <div class="small muted">PLEX × SAT</div>
  </section>`;
  if (!rules)
    return html`<div class="login">
      ${side}
      <section class="login-form"><div class="empty">Loading…</div></section>
    </div>`;
  if (done)
    return html`<div class="login">
      ${side}
      <section class="login-form">
        <div style="max-width:380px" class="col">
          <div class="kicker">Registered</div>
          <h2
            class="strong"
            style="font-size:30px;letter-spacing:-.03em;line-height:1.1"
          >
            ${done.team_name} is in. You're ${done.team_code}.
          </h2>
          ${
            done.email_status === "sent"
              ? html`<p>We've emailed your access code to:</p>
                  <ul class="small">
                    ${done.emailed_to.map((e) => html`<li>${e}</li>`)}
                  </ul>
                  <p class="small muted">
                    Not there in a few minutes? Check spam, then tell the
                    organisers.
                  </p>`
              : html`<p>
                  Your team is registered, but the email didn't go out yet. The
                  organisers will send your code; no need to register again.
                </p>`
          }
          <a class="btn" href="/" style="justify-content:center"
            >Go to login →</a
          >
        </div>
      </section>
    </div>`;
  return html`<div class="login">
    ${side}
    <section class="login-form">
      ${
        !rules.open
          ? html`<div class="empty" style="max-width:360px">
              <span class="strong">Registration is closed</span>Watch for the
              organisers' announcement. Already registered?
              <a href="/">Log in</a>.
            </div>`
          : html`<form onSubmit=${submit} style="max-width:440px">
              <div class="kicker">Register your team</div>
              <label class="field"
                >Team name<input
                  class="input"
                  required
                  minlength="2"
                  maxlength="60"
                  value=${name}
                  onInput=${(e) => setName(e.target.value)}
              /></label>
              ${members.map(
                (m, i) =>
                  html`<div
                    class="row"
                    style="align-items:flex-end;gap:8px"
                    key=${i}
                  >
                    <label class="field grow"
                      >Member ${i + 1}<input
                        class="input"
                        required
                        maxlength="80"
                        placeholder="Name"
                        value=${m.name}
                        onInput=${(e) => set(i, "name", e.target.value)}
                    /></label>
                    <label class="field grow"
                      ><span class="sr">Email</span
                      ><input
                        class="input"
                        type="email"
                        required
                        placeholder=${`you@${rules.domain}`}
                        value=${m.email}
                        onInput=${(e) => set(i, "email", e.target.value)}
                    /></label>
                    ${members.length > rules.min_members && html`<button type="button" class="btn link small" onClick=${() => setMembers(members.filter((_, j) => j !== i))}>✕</button>`}
                  </div>`,
              )}
              ${members.length < rules.max_members && html`<button type="button" class="btn ghost small" style="align-self:flex-start" onClick=${() => setMembers([...members, { name: "", email: "" }])}>+ Add a member</button>`}
              <input
                class="sr"
                tabindex="-1"
                autocomplete="off"
                aria-hidden="true"
                value=${hp}
                onInput=${(e) => setHp(e.target.value)}
                name="website"
              />
              ${err && html`<div class="error">${err}</div>`}
              <button
                class="btn"
                disabled=${busy}
                style="justify-content:center;padding:12px"
              >
                ${busy ? "Registering…" : "Register →"}
              </button>
              <p class="small muted">
                ${rules.min_members}–${rules.max_members} members,
                ${rules.domain} addresses only, each person in one team. The
                code goes to every member's inbox.
              </p>
            </form>`
      }
    </section>
    <${Toasts} />
  </div>`;
}

render(html`<${Register} />`, document.getElementById("app"));
