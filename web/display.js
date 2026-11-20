// The projector page: round, clock, broadcasts meant for everyone. No login, polls /display.

import { html, render, useEffect, useState } from "./vendor/preact-htm.js";
import { api, fmtClock, ago, useTick, Logo } from "./lib.js";

function Display() {
  const [d, setD] = useState(null);
  const [at, setAt] = useState(Date.now());
  useTick(250);
  useEffect(() => {
    const load = () =>
      api("/display")
        .then((x) => {
          setD(x);
          setAt(Date.now());
        })
        .catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);
  const p = d?.phase;
  const left =
    p && p.remaining_seconds != null
      ? Math.max(
          0,
          p.remaining_seconds -
            (p.status === "running" ? (Date.now() - at) / 1000 : 0),
        )
      : null;
  return html`<div class="display">
    <${Logo} />
    <div class="kicker" style="font-size:16px">
      ${p ? `${p.name}${p.status === "paused" ? " · paused" : ""}` : d ? "Between rounds" : "…"}
    </div>
    <div
      class=${"big" + (left != null && left < 60 && p?.status === "running" ? " blink" : "")}
    >
      ${p && left != null ? fmtClock(left) : p ? "∞" : "--:--"}
    </div>
    ${
      d?.announcements?.length
        ? html`<div class="col" style="gap:14px;align-items:center">
            ${d.announcements
              .slice(0, 3)
              .map(
                (a, i) =>
                  html`<div
                    class="ann"
                    style=${i ? "opacity:.5;font-size:.7em" : ""}
                  >
                    ${a.text}
                    <span class="small muted">· ${ago(a.created_at)}</span>
                  </div>`,
              )}
          </div>`
        : null
    }
    <div class="small muted">${d?.event || ""}</div>
  </div>`;
}

render(html`<${Display} />`, document.getElementById("app"));
