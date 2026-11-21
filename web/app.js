// PERPLEXED 2.0 — the team portal.
// Everything here reads the participant API; nothing hidden (trust, deflections) exists in it.

import {
  html,
  render,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "./vendor/preact-htm.js";
import {
  api,
  ApiError,
  connectLive,
  createStore,
  useStore,
  fmtClock,
  ago,
  useTick,
  uuid,
  bytes,
  Markdown,
  Toasts,
  toast,
  Modal,
  Logo,
  Portrait,
  Glyph,
  nameColor,
} from "./lib.js";
import { Board } from "./board.js";
import { AlliancePage, Sheet } from "./work.js";

// ------------------------------------------------------------------ state
export const S = createStore({
  me: undefined, // undefined = checking, null = logged out
  status: null,
  statusAt: 0,
  rounds: [],
  evidence: [],
  announcements: [],
  documents: [],
  brief: null,
  alliance: null,
  partnerEvidence: [],
  sheetTick: 0,
  records: { mode: "off", records: [] },
  threads: {},
  thinking: {},
  fresh: new Set(),
  live: "offline",
  seenAnn: Number(localStorage.getItem("px_seen_ann") || 0),
  boardVersionSeen: 0,
});

const route = createStore({ path: location.hash.slice(1) || "/people" });
addEventListener("hashchange", () =>
  route.set({ path: location.hash.slice(1) || "/people" }),
);
export const go = (p) => (location.hash = p);

// ------------------------------------------------------------------ loaders
async function load(name, fn) {
  try {
    await fn();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return S.set({ me: null });
    if (!(e instanceof ApiError && e.code === "COMPANY_NOT_ASSIGNED"))
      console.warn(name, e);
  }
}
const loaders = {
  status: () =>
    load("status", async () =>
      S.set({ status: await api("/status"), statusAt: Date.now() }),
    ),
  rounds: () =>
    load("rounds", async () => {
      const rounds = await api("/rounds");
      S.set({ rounds });
      announceResults(rounds);
    }),
  evidence: () =>
    load("evidence", async () => S.set({ evidence: await api("/evidence") })),
  announcements: () =>
    load("ann", async () => {
      const list = await api("/announcements");
      const newest = list[0] ? new Date(list[0].created_at).getTime() : 0;
      if (S.get().announcements.length && newest > S.get().seenAnn && list[0])
        toast(`📣 ${list[0].text}`, "holo");
      S.set({ announcements: list });
    }),
  documents: () =>
    load("docs", async () => S.set({ documents: await api("/documents") })),
  brief: () => load("brief", async () => S.set({ brief: await api("/brief") })),
  alliance: () =>
    load("alliance", async () => S.set({ alliance: await api("/alliance") })),
  partnerEvidence: () =>
    load("pev", async () =>
      S.set({ partnerEvidence: await api("/alliance/evidence") }),
    ),
  records: () =>
    load("records", async () => {
      const was = S.get().records.mode;
      const records = await api("/records");
      S.set({ records });
      if (was !== records.mode && records.mode !== "off" && S.get().me)
        toast(
          records.mode === "full"
            ? "Your company's records are open: see the Records tab."
            : "A list of your company's records is up: see the Records tab.",
          "holo",
        );
    }),
  thread: (sid) =>
    load("thread", async () => {
      const msgs = await api(`/stakeholders/${sid}/messages`);
      S.set((s) => ({ threads: { ...s.threads, [sid]: msgs } }));
    }),
};
const loadAll = () =>
  Promise.all([
    loaders.status(),
    loaders.rounds(),
    loaders.evidence(),
    loaders.announcements(),
    loaders.documents(),
    loaders.brief(),
    loaders.alliance(),
    loaders.partnerEvidence(),
    loaders.records(),
  ]);

// a published result the team hasn't acknowledged yet → a modal, once per laptop
const resultModal = createStore({ round: null });
function announceResults(rounds) {
  const seen = JSON.parse(localStorage.getItem("px_seen_results") || "{}");
  const fresh = rounds.find(
    (r) =>
      r.my_status &&
      seen[r.key] !== r.my_status.published_at + r.my_status.status,
  );
  if (fresh) resultModal.set({ round: fresh });
}
function ackResult(r) {
  const seen = JSON.parse(localStorage.getItem("px_seen_results") || "{}");
  seen[r.key] = r.my_status.published_at + r.my_status.status;
  localStorage.setItem("px_seen_results", JSON.stringify(seen));
  resultModal.set({ round: null });
}

function onLive(ev) {
  switch (ev.type) {
    case "phase":
    case "stakeholders":
      loaders.status();
      if (ev.type === "phase") {
        loaders.rounds();
        loaders.alliance();
        S.set((st) => ({ sheetTick: st.sheetTick + 1 }));
      }
      break;
    case "team":
      loadAll();
      break;
    case "thread":
      loaders.thread(ev.stakeholder_id);
      break;
    case "evidence":
      loaders.evidence();
      loaders.partnerEvidence();
      break;
    case "alliance":
      loaders.alliance();
      loaders.partnerEvidence();
      loaders.records();
      break;
    case "records":
      loaders.records();
      break;
    case "submission":
      S.set((st) => ({ sheetTick: st.sheetTick + 1 }));
      break;
    case "board":
      S.set({ boardVersionSeen: ev.version });
      break;
    case "announcement":
      loaders.announcements();
      break;
    case "documents":
      loaders.documents();
      break;
    case "rounds":
      loaders.rounds();
      break;
    case "content":
      loadAll();
      toast("The case was updated by the organisers.");
      break;
    case "reconnected":
      loadAll();
      break;
    case "logged_out":
      S.set({ me: null });
      break;
  }
}

// ------------------------------------------------------------------ derived clocks
function useClock() {
  const s = useStore(S);
  useTick(1000);
  const p = s.status?.phase;
  if (!p || p.remaining_seconds == null) return { phase: p, left: null };
  const elapsed = p.status === "running" ? (Date.now() - s.statusAt) / 1000 : 0;
  return { phase: p, left: Math.max(0, p.remaining_seconds - elapsed) };
}
const lockLeft = (sh, statusAt) =>
  Math.max(0, (sh.available_in_seconds || 0) - (Date.now() - statusAt) / 1000);

// "Dr. Kavya Nair" -> "Kavya": skip titles when addressing someone by first name
const firstName = (n) =>
  n
    .split(/\s+/)
    .find((w) => !/^(dr|mr|mrs|ms|prof|sri|shri|smt)\.?$/i.test(w)) || n;

// ------------------------------------------------------------------ login art
// Isometric pixel art: a small New York. Built from unit cubes, hidden faces skipped,
// snapped to a fixed palette, outlined, then scaled up by whole pixels in CSS
// (image-rendering: pixelated). It rises from the bottom edge of the panel like a horizon.
const ISO_N = 4;
const ISO_PAL = {
  ink: "#0e0e0e",
  g1: "#262626",
  g2: "#4a4a4a",
  g3: "#6e6e6e",
  g4: "#8e8c88",
  g5: "#bdbab3",
  g6: "#d6d3cc",
  g7: "#e9e6df",
  lav: "#8d84c6",
  lavL: "#bdb6ea",
  lavD: "#5e5690",
  win: "#1c1c1c",
  rust: "#7a5a46",
  rustD: "#56402f",
};
const ISO_MAT = {
  // top, left (+y face), right (+x face)
  ground: ["g7", "g5", "g4"],
  edge: ["g6", "g3", "g2"],
  water: ["lavD", "g2", "g1"],
  wave: ["lav", "g2", "g1"],
  pier: ["g4", "g2", "g1"],
  a: ["g5", "g3", "g2"],
  b: ["g6", "g4", "g3"],
  c: ["g4", "g2", "g1"],
  stone: ["g7", "g5", "g4"],
  brown: ["g3", "rust", "rustD"],
  tank: ["rust", "rust", "rustD"],
  glass: ["g6", "lav", "lavD"],
  mull: ["g5", "g3", "g2"],
  roof: ["g4", "g2", "g1"],
  lit: ["g5", "lavL", "lav"],
  dark: ["g5", "win", "win"],
  q: ["lavL", "lav", "lavD"],
  tree: ["g2", "g1", "ink"],
};
const ISO_Q = [".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."];
const ihash = (x, y, z) => {
  let h = (x * 374761393 + y * 668265263 + z * 1442695041) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
};

function cityKit(t) {
  const v = [];
  const put = (x, y, z, m) => v.push([x, y, z, m]);
  const lit = (x, y, z) =>
    ihash(x, y, z + 7 * Math.floor((t + ihash(x, z, y) * 60) / 60)) > 0.45;
  // a building: per-voxel material from a facade rule; keep() carves the footprint
  const block = (x0, y0, w, d, z0, z1, m, win = "grid", keep = () => true) => {
    for (let z = z0; z < z1; z++)
      for (let x = x0; x < x0 + w; x++)
        for (let y = y0; y < y0 + d; y++) {
          if (!keep(x - x0, y - y0, z)) continue;
          const fx = x === x0 + w - 1 || !keep(x - x0 + 1, y - y0, z),
            fy = y === y0 + d - 1 || !keep(x - x0, y - y0 + 1, z);
          let mat = m;
          if ((fx || fy) && z < z1 - 1) {
            const k = fy ? x - x0 : y - y0;
            if (win === "glass") mat = z % 3 === 0 ? "mull" : "glass";
            else if (
              win === "grid"
                ? z % 2 === 1 && k % 2 === 1
                : win === "cols"
                  ? k % 2 === 1 && z % 4 !== 0
                  : win === "bands"
                    ? z % 2 === 1
                    : false
            )
              mat = lit(x, y, z) ? "lit" : "dark";
          }
          put(x, y, z, mat);
        }
  };
  const land = (x, y) => {
    put(x, y, 0, "edge");
    put(x, y, 1, "ground");
  };
  const water = (x, y) =>
    put(x, y, 0, ihash(x, y, Math.floor(t / 4)) > 0.9 ? "wave" : "water");
  const pole = (x, y, z0, z1) => {
    for (let z = z0; z < z1; z++) put(x, y, z, z === z1 - 1 ? "q" : "roof");
  };
  // the landmarks, each from its lot's back corner (x0, y0)
  const taper = (x0, y0) => {
    [6, 6, 5, 5, 4, 4, 3, 2].forEach((sz, i) => {
      const o = Math.floor((6 - sz) / 2);
      block(x0 + o, y0 + o, sz, sz, 2 + i * 3, 5 + i * 3, "glass", "glass");
    });
    pole(x0 + 2, y0 + 2, 26, 30);
  };
  const setback = (x0, y0) => {
    block(x0, y0, 5, 5, 2, 13, "a", "cols");
    block(x0 + 1, y0 + 1, 3, 3, 13, 20, "a", "cols");
    pole(x0 + 2, y0 + 2, 20, 25);
  };
  const crown = (x0, y0) => {
    block(x0, y0, 5, 5, 2, 16, "b", "cols");
    block(
      x0 + 1,
      y0 + 1,
      3,
      3,
      16,
      19,
      "stone",
      "none",
      (dx, dy, z) => z < 18 || dx === 1 || dy === 1,
    );
    const blink = Math.floor(t / 6) % 2;
    for (let z = 16; z < 19; z++) {
      put(x0 + 3, y0 + 2, z, (z + blink) % 2 ? "lit" : "stone");
      put(x0 + 2, y0 + 3, z, (z + blink) % 2 ? "stone" : "lit");
    }
    for (let z = 19; z < 24; z++) put(x0 + 2, y0 + 2, z, "stone");
  };
  const wedge = (x0, y0) =>
    block(
      x0,
      y0,
      4,
      4,
      2,
      11,
      "stone",
      "grid",
      (dx, dy) => dx >= dy - 1 && dx < 4 && dy < 4,
    );
  const tank = (x0, y0, w = 3, d = 3, h = 8) => {
    block(x0, y0, w, d, 2, h, "b", "grid");
    for (const [x, y] of [
      [x0, y0],
      [x0 + 1, y0],
      [x0, y0 + 1],
      [x0 + 1, y0 + 1],
    ]) {
      put(x, y, h, "roof");
      put(x, y, h + 1, "tank");
      put(x, y, h + 2, "tank");
    }
  };
  const slab = (x0, y0, w, d, h) => block(x0, y0, w, d, 2, h, "c", "bands");
  const brownstones = (x0, y0, n, alongY) => {
    for (let i = 0; i < n; i++)
      alongY
        ? block(x0, y0 + i * 2, 3, 2, 2, 5 + (i % 2), "brown", "grid")
        : block(x0 + i * 2, y0, 2, 3, 2, 5 + (i % 2), "brown", "grid");
  };
  const question = (x0, y0, ztop) => {
    const bob = Math.round(Math.sin(t / 5) * 2) / 4;
    ISO_Q.forEach((row, i) =>
      [...row].forEach((ch, j) => {
        if (ch === "#") put(x0 + j, y0, ztop - i + bob, "q");
      }),
    );
  };
  return {
    v,
    put,
    land,
    water,
    taper,
    setback,
    crown,
    wedge,
    tank,
    slab,
    brownstones,
    question,
  };
}

// two waterfronts meeting at the tallest tower; the skyline steps down both ways
function city(t) {
  const k = cityKit(t);
  for (let x = 0; x < 30; x++)
    for (let y = 0; y < 30; y++) {
      if (y < 7 || x < 7) k.land(x, y);
      else if (y < 10 || x < 10) k.water(x, y);
    }
  k.taper(0, 0);
  k.setback(8, 1);
  k.crown(14, 1);
  k.wedge(20, 2);
  k.brownstones(24, 2, 3, false);
  k.slab(1, 8, 5, 4, 17);
  k.tank(2, 14, 3, 3, 12);
  k.slab(2, 19, 4, 3, 9);
  k.brownstones(2, 23, 3, true);
  for (let i = 8; i < 30; i++) {
    if (ihash(i, 6, 3) > 0.55) k.put(i, 6, 2, "tree");
    if (ihash(6, i, 3) > 0.55) k.put(6, i, 2, "tree");
  }
  k.question(22, 1, 22);
  return k.v;
}

function isoBounds(vox) {
  // the drawing's extent in pixels, so any layout fits its canvas
  let x0 = 1e9,
    x1 = -1e9,
    y0 = 1e9,
    y1 = -1e9;
  for (const [x, y, z] of vox) {
    x0 = Math.min(x0, (x - y - 1) * ISO_N);
    x1 = Math.max(x1, (x - y + 1) * ISO_N);
    y0 = Math.min(y0, ((x + y) * ISO_N) / 2 - (z + 2) * ISO_N);
    y1 = Math.max(y1, ((x + y + 2) * ISO_N) / 2 - z * ISO_N);
  }
  return {
    W: Math.ceil(x1 - x0) + 4,
    H: Math.ceil(y1 - y0) + 4,
    ox: -x0 + 2,
    oy: -y0 + 2,
  };
}
function drawIso(canvas, t) {
  const vox = city(t);
  canvas._b = canvas._b || isoBounds(city(0));
  const { W, H, ox, oy } = canvas._b,
    n = ISO_N;
  canvas.width = W;
  canvas.height = H;
  const g = canvas.getContext("2d", { willReadFrequently: true });
  const P = (x, y, z) => [ox + (x - y) * n, oy + ((x + y) * n) / 2 - z * n];
  const filled = new Set(
    vox
      .filter((v) => Number.isInteger(v[2]))
      .map(([x, y, z]) => `${x},${y},${z}`),
  );
  vox.sort((a, b) => a[0] + a[1] - (b[0] + b[1]) || a[2] - b[2]);
  const poly = (pts, col) => {
    g.fillStyle = ISO_PAL[col];
    g.beginPath();
    pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    g.closePath();
    g.fill();
  };
  for (const [x, y, z, m] of vox) {
    const [top, left, right] = ISO_MAT[m];
    const has = (a, b, c) => filled.has(`${a},${b},${c}`);
    if (!has(x, y + 1, z))
      poly(
        [
          P(x, y + 1, z),
          P(x + 1, y + 1, z),
          P(x + 1, y + 1, z + 1),
          P(x, y + 1, z + 1),
        ],
        left,
      );
    if (!has(x + 1, y, z))
      poly(
        [
          P(x + 1, y, z),
          P(x + 1, y + 1, z),
          P(x + 1, y + 1, z + 1),
          P(x + 1, y, z + 1),
        ],
        right,
      );
    if (!has(x, y, z + 1))
      poly(
        [
          P(x, y, z + 1),
          P(x + 1, y, z + 1),
          P(x + 1, y + 1, z + 1),
          P(x, y + 1, z + 1),
        ],
        top,
      );
  }
  // snap every pixel to the palette, then outline the silhouette
  const img = g.getImageData(0, 0, W, H),
    d = img.data;
  const pal = Object.values(ISO_PAL).map((h) => [
    parseInt(h.slice(1, 3), 16),
    parseInt(h.slice(3, 5), 16),
    parseInt(h.slice(5, 7), 16),
  ]);
  const solid = new Uint8Array(W * H);
  for (let i = 0; i < W * H; i++) {
    const a = d[i * 4 + 3];
    if (a < 110) {
      d[i * 4 + 3] = 0;
      continue;
    }
    const r = (d[i * 4] * 255) / a,
      gg = (d[i * 4 + 1] * 255) / a,
      b = (d[i * 4 + 2] * 255) / a;
    let best = 0,
      bd = 1e9;
    pal.forEach(([pr, pg, pb], k) => {
      const e = (pr - r) ** 2 + (pg - gg) ** 2 + (pb - b) ** 2;
      if (e < bd) {
        bd = e;
        best = k;
      }
    });
    [d[i * 4], d[i * 4 + 1], d[i * 4 + 2], d[i * 4 + 3]] = [...pal[best], 255];
    solid[i] = 1;
  }
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      const i = y * W + x;
      if (solid[i]) continue;
      if (
        (x > 0 && solid[i - 1]) ||
        (x < W - 1 && solid[i + 1]) ||
        (y > 0 && solid[i - W]) ||
        (y < H - 1 && solid[i + W])
      ) {
        d[i * 4] = 14;
        d[i * 4 + 1] = 14;
        d[i * 4 + 2] = 14;
        d[i * 4 + 3] = 255;
      }
    }
  g.putImageData(img, 0, 0);
}

// ------------------------------------------------------------------ login
function Login() {
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const art = useRef();
  useEffect(() => {
    const c = art.current;
    const reduce = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    drawIso(c, 0);
    const { W, H } = c._b;
    const fit = () => {
      // whole pixels keep the pixels square; never grow into the text
      const panel = c.parentElement.getBoundingClientRect();
      const text = c.parentElement
        .querySelector(".login-copy")
        .getBoundingClientRect();
      const narrow = matchMedia("(max-width: 860px)").matches; // stacked: the art sits in the flow
      // like a horizon, the island may sink below the bottom edge, up to about a third of its height,
      // so a short screen still gets a big skyline without it reaching the text
      const room = panel.bottom - text.bottom - 28;
      let k = Math.max(
          1,
          Math.floor(
            narrow ? (panel.width * 0.9) / W : (panel.width * 1.1) / W,
          ),
        ),
        sink = 0;
      if (!narrow)
        for (; k > 1; k--) {
          sink = Math.max(0, H * k - room);
          if (sink <= H * k * 0.36) break;
        }
      if (!narrow)
        sink = Math.max(k === 1 ? Math.max(0, H - room) : sink, H * k * 0.04);
      c.style.bottom = narrow ? "" : `${-Math.round(sink)}px`;
      c.style.width = `${W * k}px`;
      c.style.height = `${H * k}px`;
    };
    fit();
    addEventListener("resize", fit);
    let t = 0,
      timer;
    const loop = () => {
      drawIso(c, t++);
      if (!reduce) timer = setTimeout(loop, 125);
    };
    loop();
    return () => {
      clearTimeout(timer);
      removeEventListener("resize", fit);
    };
  }, []);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const me = await api("/auth/login", {
        method: "POST",
        body: {
          access_code: code.trim(),
          device_label: navigator.platform || null,
        },
      });
      S.set({ me });
    } catch (e) {
      setErr(
        e.code === "INVALID_CODE"
          ? "That code doesn't open anything. Check the card."
          : e.code === "SESSION_LIMIT"
            ? "Two laptops from your team are already in. Log one out first."
            : e.message,
      );
    } finally {
      setBusy(false);
    }
  };
  return html`<div class="login">
    <section class="login-art">
      <${Logo} />
      <div class="login-copy">
        <div class="kicker">AI consulting simulation</div>
        <h1 class="hero-title px" style="margin-top:18px">
          PERPLEXED<br />2.0
        </h1>
        <p class="muted" style="margin-top:22px">
          Two companies. A handful of people who know more than they say. Ask
          well, earn their trust, and build the case from what they let slip.
        </p>
      </div>
      <canvas ref=${art} aria-hidden="true"></canvas>
      <div class="small muted">PLEX × SAT</div>
    </section>
    <section class="login-form">
      <form onSubmit=${submit}>
        <div class="kicker">Team login</div>
        <h2
          class="strong"
          style="font-size:30px;letter-spacing:-.03em;line-height:1.1"
        >
          Enter your access code
        </h2>
        <input
          class="input code-input"
          value=${code}
          onInput=${(e) => setCode(e.target.value.toUpperCase())}
          placeholder="XXXX-XXXX-XXXX"
          autocomplete="off"
          spellcheck="false"
          autofocus
          aria-label="Access code"
        />
        ${err && html`<div class="error">${err}</div>`}
        <button
          class="btn"
          disabled=${busy || code.trim().length < 4}
          style="justify-content:center;padding:12px"
        >
          ${busy ? "Checking…" : "Enter →"}
        </button>
        <p class="small muted">
          Up to two laptops per team. Your code is given to you by the organisers;
          keep it to yourselves.
        </p>
      </form>
    </section>
  </div>`;
}

// ------------------------------------------------------------------ shell
function Topbar({ path }) {
  const s = useStore(S);
  const { phase, left } = useClock();
  const t = s.status?.team || s.me;
  const unseenDocs = false;
  const links = [
    ["/brief", "Brief"],
    ["/people", "People"],
    ["/evidence", "Evidence"],
    ...(s.records.mode !== "off" ? [["/records", "Records"]] : []),
    ["/board", "Board"],
    ["/alliance", "Alliance"],
    ["/sheet", "Findings"],
    ["/docs", "Documents"],
    ["/rounds", "Rounds"],
  ];
  const logout = async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch {}
    S.set({ me: null });
  };
  return html`<header class="topbar">
    <a href="#/people" style="text-decoration:none"><${Logo} /></a>
    <nav class="nav">
      ${links.map(([p, l]) => html`<a href=${"#" + p} class=${path.startsWith(p) ? "on" : ""}>${l}${p === "/docs" && unseenDocs ? html`<span class="badge"></span>` : ""}</a>`)}
    </nav>
    <div
      class="clock right"
      title=${phase ? `${phase.name} · ${phase.status}` : "between rounds"}
    >
      <span class="small muted"
        >${phase ? phase.name : "Between rounds"}${phase?.status === "paused" ? " · paused" : ""}</span
      >
      <span
        class=${"t" + (left != null && left < 60 && phase?.status === "running" ? " low" : "")}
        >${phase ? fmtClock(left) : "--:--"}</span
      >
    </div>
    <div class="teamtag">
      <b>${t?.code || ""}</b><span class="small muted">${t?.name || ""}</span>
    </div>
    <span class="chip" title="live updates"
      >${s.live === "live" ? html`<span class="dot"></span> live` : "offline"}</span
    >
    <button class="btn ghost small" onClick=${logout}>Log out</button>
  </header>`;
}

function Ticker() {
  const s = useStore(S);
  const a = s.announcements[0];
  if (!a) return null;
  return html`<div class="ticker">
    <span class="lbl">NOTICE</span><span class="msg">${a.text}</span
    ><span class="right small" style="opacity:.6">${ago(a.created_at)}</span>
  </div>`;
}

// ------------------------------------------------------------------ people
const pretty = (s) => (s || "").replace(/_/g, " ");
const empNo = (id) =>
  String(
    ([...id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % 9000) +
      1000,
  );

function Badge({ sh, i = 0 }) {
  const s = useStore(S);
  useTick(1000);
  const [flipped, setFlipped] = useState(false);
  const locked = lockLeft(sh, s.statusAt);
  const total = Math.max(sh.questions_remaining, 10);
  return html`<div
    class="badge-hang"
    style=${`animation-delay:${i * 140}ms,${450 + i * 140}ms`}
  >
    <div
      class=${"badge" + (flipped ? " flipped" : "") + (locked ? " locked" : "")}
      onClick=${() => setFlipped(!flipped)}
      role="button"
      tabindex="0"
      aria-label=${`${sh.name}, ${sh.role}`}
      onKeyDown=${(e) => e.key === "Enter" && setFlipped(!flipped)}
    >
      <div class="badge-inner">
        <div class="badge-face">
          <div class="badge-top">
            <span>${pretty(sh.department)}</span
            ><span>EMP-${empNo(sh.id)}</span>
          </div>
          <div style="position:relative">
            <${Portrait} id=${sh.id} name=${sh.name} look=${sh.look} />
            ${locked > 0 && html`<div class="stamp">AT HR<br />${fmtClock(locked)}</div>`}
          </div>
          <div class="badge-name" style=${`color:${nameColor(sh.look, sh.id)}`}>
            ${sh.name}
          </div>
          <div class="badge-role">${sh.role}</div>
          <div class="badge-foot">
            <span>${sh.questions_remaining} left</span>
            <span class="meter"
              >${[...Array(10)].map((_, i) => html`<i class=${i < Math.round((sh.questions_remaining / total) * 10) ? "" : "off"}></i>`)}</span
            >
          </div>
        </div>
        <div class="badge-face badge-back">
          <div
            class="badge-top"
            style="position:relative;z-index:1;color:var(--ink-2)"
          >
            <span>Staff pass</span><span>${pretty(sh.department)}</span>
          </div>
          <div style="position:relative;z-index:1">
            <div class="badge-name" style="margin-top:4px">${sh.name}</div>
            <div class="badge-role">${sh.role}</div>
            <dl>
              <dt>Dept</dt>
              <dd>${pretty(sh.department)}</dd>
              <dt>Questions</dt>
              <dd>${sh.questions_remaining} left</dd>
              <dt>Status</dt>
              <dd>${locked ? `with HR · ${fmtClock(locked)}` : "available"}</dd>
            </dl>
          </div>
          <div class="actions">
            <${Glyph} seed=${sh.id} />
            <button
              class="btn small"
              onClick=${(e) => {
                e.stopPropagation();
                go(`/people/${sh.id}`);
              }}
            >
              Interview →
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>`;
}

function People() {
  const s = useStore(S);
  const list = s.status?.stakeholders;
  if (s.status && !s.status.team.company_id)
    return html`<div class="page">
      <div class="empty">
        <span class="strong">Waiting for a company</span>The organisers haven't
        assigned your team a company yet. This page fills in the moment they do.
      </div>
    </div>`;
  return html`<div class="page">
    <div class="people-head">
      <div>
        <div class="kicker">
          ${s.brief?.company?.name || "Your company"} ·
          ${list ? list.length : "…"} people
        </div>
        <h1
          class="hero-title"
          style="font-size:clamp(28px,3.6vw,46px);margin-top:12px"
        >
          Meet the people<span class="sub">behind the work.</span>
        </h1>
      </div>
      <p>
        Click a pass to turn it over. Every question costs one from that
        person's allowance, and how you ask changes what they'll tell you.
      </p>
    </div>
    <div class="wire-wrap">
      <div class="wire"></div>
      <div class="badges">
        ${list ? list.map((sh, i) => html`<${Badge} key=${sh.id} sh=${sh} i=${i} />`) : html`<div class="empty">Loading…</div>`}
      </div>
    </div>
  </div>`;
}

// ------------------------------------------------------------------ interview room
function EvidenceCard({ c, who, pin, isNew }) {
  return html`<div class=${"ev" + (isNew ? " new" : "")}>
    <div class="ev-h">
      <div>
        <div class="ev-title">${c.record_title}</div>
        <div class="ev-meta">
          ${c.record_id}${who ? ` · from ${who}` : ""}${c.record_date ? ` · ${c.record_date}` : ""}
        </div>
      </div>
      <span class=${"chip" + (c.mode === "quote" ? " solid" : "")}
        >${c.mode}</span
      >
    </div>
    <div
      class="ev-x"
      dangerouslySetInnerHTML=${{
        __html: (c.excerpt || "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/\[withheld\]/g, "<mark>withheld</mark>"),
      }}
    ></div>
    ${pin && html`<div class="ev-actions"><button class="btn ghost small" onClick=${pin}>Pin to board</button></div>`}
  </div>`;
}

async function pinToBoard(card) {
  // read-modify-write with the version check; one retry on a race
  for (let i = 0; i < 2; i++) {
    const b = await api("/board");
    if (
      card.kind === "evidence" &&
      b.cards.some(
        (c) => c.kind === "evidence" && c.passage_id === card.passage_id,
      )
    )
      return toast("Already on the board.");
    const n = b.cards.length;
    const cards = [
      ...b.cards,
      {
        ...card,
        id: `c${Date.now().toString(36)}${Math.floor(Math.random() * 99)}`,
        x: 60 + (n % 6) * 270,
        y: 60 + Math.floor(n / 6) * 220,
      },
    ];
    try {
      await api("/board", {
        method: "PUT",
        body: { version: b.version, cards, links: b.links, extra: b.extra },
      });
      return toast("Pinned to the board.");
    } catch (e) {
      if (e.code !== "BOARD_CONFLICT") return toast(e.message, "err");
    }
  }
}

function Room({ sid }) {
  const s = useStore(S);
  useTick(1000);
  const sh = s.status?.stakeholders?.find((x) => x.id === sid);
  const msgs = s.threads[sid];
  const [text, setText] = useState("");
  const [sel, setSel] = useState(null);
  const threadRef = useRef();
  const thinking = s.thinking[sid];
  useEffect(() => {
    loaders.thread(sid);
  }, [sid]);
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs?.length, thinking]);
  useEffect(() => {
    const up = () => {
      const r = getSelection();
      if (!r || r.isCollapsed) return setSel(null);
      const node = r.anchorNode?.parentElement?.closest("[data-answer]");
      if (
        !node ||
        node !== r.focusNode?.parentElement?.closest("[data-answer]")
      )
        return setSel(null);
      const full = node.textContent,
        picked = r.toString();
      const start = full.indexOf(picked);
      if (start < 0 || !picked.trim()) return setSel(null);
      const rect = r.getRangeAt(0).getBoundingClientRect();
      setSel({
        id: node.dataset.answer,
        start,
        end: start + picked.length,
        x: rect.left + rect.width / 2,
        y: rect.top - 40,
      });
    };
    document.addEventListener("mouseup", up);
    return () => document.removeEventListener("mouseup", up);
  }, []);
  if (!s.status)
    return html`<div class="page"><div class="empty">Loading…</div></div>`;
  if (!sh)
    return html`<div class="page">
      <div class="empty">
        <span class="strong">Nobody by that name</span
        ><a href="#/people">Back to people</a>
      </div>
    </div>`;
  const locked = lockLeft(sh, s.statusAt);
  const open = s.status.phase?.interviews_open;
  const canAsk = open && !locked && sh.questions_remaining > 0 && !thinking;
  const myCards = [];
  const seen = new Set();
  (msgs || []).forEach((m) =>
    (m.evidence || []).forEach((c) => {
      if (!seen.has(c.passage_id)) {
        seen.add(c.passage_id);
        myCards.unshift(c);
      }
    }),
  );

  const ask = async () => {
    const q = text.trim();
    if (!q || !canAsk) return;
    const id = uuid();
    const optimistic = {
      id: "tmp-" + id,
      role: "question",
      text: q,
      evidence: [],
    };
    S.set((st) => ({
      thinking: { ...st.thinking, [sid]: true },
      threads: {
        ...st.threads,
        [sid]: [...(st.threads[sid] || []), optimistic],
      },
    }));
    setText("");
    let tries = 0;
    while (true) {
      try {
        const r = await api(`/stakeholders/${sid}/questions`, {
          method: "POST",
          body: { client_request_id: id, text: q },
        });
        const fresh = new Set(S.get().fresh);
        r.evidence.filter((c) => c.new).forEach((c) => fresh.add(c.passage_id));
        if (r.evidence.some((c) => c.new))
          toast(
            `New evidence: ${r.evidence
              .filter((c) => c.new)
              .map((c) => c.record_title)
              .join(", ")}`,
            "holo",
          );
        if (r.kind === "hr_notice") toast(`${sh.name} has gone to HR.`, "err");
        S.set({ fresh });
        break;
      } catch (e) {
        if (e.code === "PROVIDER_BUSY" && tries++ < 2) {
          await new Promise((ok) => setTimeout(ok, 2500));
          continue;
        } // same id: never charged twice
        if (e.code === "NETWORK" && tries++ < 2) {
          await new Promise((ok) => setTimeout(ok, 2000));
          continue;
        }
        const msg =
          {
            PHASE_CLOSED: "Interviews are closed right now.",
            QUOTA_EXHAUSTED: `No questions left for ${sh.name}.`,
            QUESTION_IN_FLIGHT: `A teammate's question to ${sh.name} is still being answered.`,
            HR_LOCKOUT: `The ${e.details.department || sh.department} department isn't talking to your team for now.`,
            NOT_IN_ROUND: "Your team isn't taking part in this round.",
            PROVIDER_BUSY:
              "Busy right now — try again in a moment (you weren't charged).",
          }[e.code] || e.message;
        toast(msg, "err");
        if (!["QUESTION_IN_FLIGHT"].includes(e.code)) setText(q);
        break;
      }
    }
    S.set((st) => ({ thinking: { ...st.thinking, [sid]: false } }));
    loaders.thread(sid);
    loaders.status();
    loaders.evidence();
  };

  return html`<div class=${"room" + (s.announcements[0] ? " has-ticker" : "")}>
    <aside class="room-side">
      <a href="#/people" class="small">← All people</a>
      <${Portrait} id=${sh.id} name=${sh.name} look=${sh.look} />
      <div>
        <div class="badge-name" style="margin:0">${sh.name}</div>
        <div class="badge-role">${sh.role}</div>
      </div>
      <dl
        class="small"
        style="display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:0"
      >
        <dt class="muted">Dept</dt>
        <dd style="margin:0">${pretty(sh.department)}</dd>
        <dt class="muted">Questions</dt>
        <dd style="margin:0">${sh.questions_remaining} left</dd>
      </dl>
      ${locked > 0 && html`<div class="closed-note"><b class="strong">At HR</b><br />${pretty(sh.department)} is back in ${fmtClock(locked)}</div>`}
      <p class="small muted" style="margin-top:auto">
        Tip: select words in an answer to pin them to your board as a quote.
      </p>
    </aside>
    <section class="room-main">
      <div class="thread" ref=${threadRef} aria-live="polite">
        ${!msgs ? html`<div class="empty">Loading the conversation…</div>` : !msgs.length ? html`<div class="empty"><span class="strong">A blank page</span>${sh.name} is waiting. Introduce yourselves, or get straight to it.</div>` : null}
        ${(msgs || []).map((m) =>
          m.role === "question"
            ? html`<div class="msg q" key=${m.id}>
                <div class="who">Your team</div>
                <div class="bubble">${m.text}</div>
              </div>`
            : m.role === "notice"
              ? html`<div class="msg n" key=${m.id}>
                  <div class="who">HR notice</div>
                  <div class="bubble">${m.text}</div>
                </div>`
              : html`<div class="msg a" key=${m.id}>
                  <div class="who">${sh.name}</div>
                  <div class="bubble" data-answer=${m.id}>${m.text}</div>
                  ${m.evidence?.length ? html`<div class="cites">${m.evidence.map((c) => html`<span class=${"chip" + (s.fresh.has(c.passage_id) ? " holo" : "")}>${c.record_id}</span>`)}</div>` : null}
                </div>`,
        )}
        ${
          thinking &&
          html`<div class="thinking">
            <span class="bars"><i></i><i></i><i></i></span>${firstName(sh.name)}
            is thinking…
          </div>`
        }
      </div>
      <div class="composer">
        ${
          !open
            ? html`<div class="closed-note">
                Interviews are closed in this round.
              </div>`
            : locked
              ? html`<div class="closed-note">
                  ${pretty(sh.department)} has filed an HR complaint. Back in
                  ${fmtClock(locked)}.
                </div>`
              : sh.questions_remaining <= 0
                ? html`<div class="closed-note">
                    You've used every question for ${sh.name}.
                  </div>`
                : html`<textarea
                      class="textarea"
                      maxlength="1000"
                      rows="2"
                      placeholder=${`Ask ${firstName(sh.name)} something…`}
                      value=${text}
                      onInput=${(e) => setText(e.target.value)}
                      disabled=${thinking}
                      onKeyDown=${(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          ask();
                        }
                      }}
                    ></textarea>
                    <div class="meta">
                      <span
                        >${sh.questions_remaining}
                        question${sh.questions_remaining === 1 ? "" : "s"} left
                        · Enter to send, Shift+Enter for a new line</span
                      >
                      <span class="row"
                        ><span>${text.length}/1000</span
                        ><button
                          class="btn small"
                          disabled=${!canAsk || !text.trim()}
                          onClick=${ask}
                        >
                          Ask
                        </button></span
                      >
                    </div>`
        }
      </div>
    </section>
    <aside class="room-evidence">
      <div class="kicker">Evidence from ${firstName(sh.name)}</div>
      ${
        myCards.length
          ? myCards.map(
              (c) =>
                html`<${EvidenceCard}
                  key=${c.passage_id}
                  c=${c}
                  isNew=${s.fresh.has(c.passage_id)}
                  pin=${() => pinToBoard({ kind: "evidence", passage_id: c.passage_id })}
                />`,
            )
          : html`<div class="empty small">
              Nothing yet. Records only appear when ${firstName(sh.name)}
              decides to share them.
            </div>`
      }
    </aside>
    ${
      sel &&
      html`<button
        class="btn small pinbtn"
        style=${`left:${sel.x - 50}px;top:${Math.max(70, sel.y)}px`}
        onMouseDown=${(e) => e.preventDefault()}
        onClick=${() => {
          pinToBoard({
            kind: "quote",
            message_id: sel.id,
            start: sel.start,
            end: sel.end,
          });
          setSel(null);
          getSelection().removeAllRanges();
        }}
      >
        Pin quote
      </button>`
    }
  </div>`;
}

// ------------------------------------------------------------------ other pages
function EvidencePage() {
  const s = useStore(S);
  const [q, setQ] = useState("");
  const list = s.evidence.filter(
    (c) =>
      !q ||
      (c.record_title + c.excerpt + c.stakeholder_name)
        .toLowerCase()
        .includes(q.toLowerCase()),
  );
  return html`<div class="page">
    <div class="row" style="margin-bottom:22px;align-items:flex-end">
      <div>
        <div class="kicker">
          ${s.evidence.length} records shown to your team
        </div>
        <h1 class="hero-title" style="font-size:36px;margin-top:10px">
          Evidence
        </h1>
      </div>
      <input
        class="input right"
        style="max-width:280px"
        placeholder="Search evidence…"
        value=${q}
        onInput=${(e) => setQ(e.target.value)}
      />
    </div>
    ${
      list.length
        ? html`<div class="evidence-grid">
            ${list.map(
              (c) =>
                html`<${EvidenceCard}
                  key=${c.passage_id}
                  c=${c}
                  who=${c.stakeholder_name}
                  isNew=${s.fresh.has(c.passage_id)}
                  pin=${() => pinToBoard({ kind: "evidence", passage_id: c.passage_id })}
                />`,
            )}
          </div>`
        : html`<div class="empty">
            <span class="strong">No evidence yet</span>It turns up as people
            share it in interviews.
          </div>`
    }
  </div>`;
}

const prettyKind = (k) => k.replace(/_/g, " ");

function RecordsPage() {
  const s = useStore(S);
  const [q, setQ] = useState("");
  const { mode, records } = s.records;
  if (mode === "off")
    return html`<div class="page">
      <div class="empty">
        <span class="strong">Records are closed</span>For now, records come out
        only in interviews.
      </div>
    </div>`;
  const hit = (r) =>
    !q ||
    (r.title + r.kind + r.passages.map((p) => p.text).join(" "))
      .toLowerCase()
      .includes(q.toLowerCase());
  const own = records.filter((r) => !r.partner && hit(r));
  const partner = records.filter((r) => r.partner && hit(r));
  const Doc = ({ r }) =>
    html`<article class="ev" key=${r.id}>
      <div class="ev-h">
        <div>
          <div class="ev-title">${r.title}</div>
          <div class="ev-meta">
            ${r.id} · ${prettyKind(r.kind)}${r.date ? ` · ${r.date}` : ""}
          </div>
        </div>
        ${r.partner && html`<span class="chip">partner</span>`}
      </div>
      ${r.passages.map(
        (p) =>
          html`<div class="rec-p" key=${p.passage_id}>
            <div class="ev-x">${p.text}</div>
            <button
              class="btn link small"
              onClick=${() => pinToBoard({ kind: "evidence", passage_id: p.passage_id })}
            >
              Pin to board
            </button>
          </div>`,
      )}
    </article>`;
  return html`<div class="page">
    <div class="row" style="margin-bottom:22px;align-items:flex-end">
      <div>
        <div class="kicker">
          ${mode === "full" ? "Internal documents · citable" : "What exists · ask the people who'd know"}
        </div>
        <h1 class="hero-title" style="font-size:36px;margin-top:10px">
          Records
        </h1>
      </div>
      <input
        class="input right"
        style="max-width:280px"
        placeholder="Search records…"
        value=${q}
        onInput=${(e) => setQ(e.target.value)}
      />
    </div>
    ${
      mode === "titles"
        ? html`<p class="muted" style="max-width:640px;margin:0 0 20px">
              The organisers have released the list, not the contents. Records
              never explain themselves; the people who wrote them might.
            </p>
            <table class="t">
              <thead>
                <tr>
                  <th>Record</th>
                  <th>Kind</th>
                  <th>Date</th>
                  <th>Ref</th>
                </tr>
              </thead>
              <tbody>
                ${own.map(
                  (r) =>
                    html`<tr key=${r.id}>
                      <td><b>${r.title}</b></td>
                      <td>${prettyKind(r.kind)}</td>
                      <td class="tiny muted">${r.date}</td>
                      <td class="tiny muted">${r.id}</td>
                    </tr>`,
                )}
              </tbody>
            </table>`
        : html`<p class="muted" style="max-width:640px;margin:0 0 20px">
              Your company's documents, as filed. Records never explain
              themselves, and some don't matter. Pin a passage to your board or
              cite it in your findings like interview evidence.
            </p>
            ${own.length ? html`<div class="evidence-grid">${own.map((r) => html`<${Doc} r=${r} />`)}</div>` : html`<div class="empty">Nothing matches.</div>`}
            ${
              partner.length
                ? html`<h2
                      class="strong"
                      style="font-size:22px;margin:36px 0 12px"
                    >
                      Partner company · ${partner.length}
                    </h2>
                    <div class="evidence-grid">
                      ${partner.map((r) => html`<${Doc} r=${r} />`)}
                    </div>`
                : null
            }`
    }
  </div>`;
}

function Brief() {
  const s = useStore(S);
  const b = s.brief;
  if (!b)
    return html`<div class="page"><div class="empty">Loading…</div></div>`;
  return html`<div class="page">
    <div class="kicker">Read this first</div>
    <h1 class="hero-title" style="font-size:40px;margin:12px 0 28px">
      The brief
    </h1>
    <div class="brief-grid">
      <div class="panel">
        <div class="panel-h"><span class="strong">The case</span></div>
        <div class="panel-b">
          <${Markdown} src=${b.case || "_No brief yet._"} />
        </div>
      </div>
      <div class="panel">
        <div class="panel-h">
          <span class="strong"
            >${b.company ? b.company.name : "Your company"}</span
          >${b.company && html`<span class="chip right">Company ${b.company.id}</span>`}
        </div>
        <div class="panel-b">
          ${b.company ? html`<${Markdown} src=${b.company.brief || "_No company brief._"} />` : html`<div class="empty">Your company hasn't been assigned yet.</div>`}
        </div>
      </div>
    </div>
  </div>`;
}

function Docs() {
  const s = useStore(S);
  const [open, setOpen] = useState(null);
  const view = async (d) => {
    if (d.kind === "file")
      return window.open(`/api/v1/documents/${d.id}`, "_blank", "noopener");
    try {
      setOpen(await api(`/documents/${d.id}`));
    } catch (e) {
      toast(e.message, "err");
    }
  };
  return html`<div class="page">
    <div class="kicker">${s.documents.length} handed to your team</div>
    <h1 class="hero-title" style="font-size:40px;margin:12px 0 28px">
      Documents
    </h1>
    ${
      s.documents.length
        ? html`<div class="doc-list">
            ${s.documents.map(
              (d) =>
                html`<a
                  class="doc"
                  key=${d.id}
                  onClick=${() => view(d)}
                  tabindex="0"
                >
                  <span class="fold"></span>
                  <span class="tiny muted"
                    >${d.kind === "file" ? (d.filename || "file").split(".").pop().toUpperCase() : "NOTE"}${d.round ? ` · ${d.round}` : ""}</span
                  >
                  <span class="title">${d.title}</span>
                  <span class="small muted" style="margin-top:auto"
                    >${d.kind === "file" ? bytes(d.size_bytes) + " · opens in a new tab" : "Read"}
                    · ${ago(d.created_at)}</span
                  >
                </a>`,
            )}
          </div>`
        : html`<div class="empty">
            <span class="strong">Nothing yet</span>The organisers hand out
            documents as rounds begin.
          </div>`
    }
    ${open && html`<${Modal} title=${open.title} onClose=${() => setOpen(null)}><${Markdown} src=${open.body} /></${Modal}>`}
  </div>`;
}

const STATUS_TEXT = {
  advancing: "Through to this round",
  eliminated: "Not in this round",
  waitlisted: "Waitlisted",
  pending: "Pending",
};
function Rounds() {
  const s = useStore(S);
  const anns = s.announcements;
  return html`<div class="page">
    <div class="kicker">Where you stand</div>
    <h1 class="hero-title" style="font-size:40px;margin:12px 0 28px">Rounds</h1>
    <div class="rounds">
      ${s.rounds.map(
        (r, i) =>
          html`<div
            key=${r.key}
            class=${"round" + (["running", "paused"].includes(r.status) ? " now" : r.status === "ended" ? " done" : "")}
          >
            <div class="n">${String(i + 1).padStart(2, "0")}</div>
            <div class="name">${r.name}</div>
            <div class="small" style="opacity:.75">
              ${r.status === "running" ? "happening now" : r.status === "paused" ? "paused" : r.status === "ended" ? "finished" : "coming up"}${r.planned_seconds ? ` · ${Math.round(r.planned_seconds / 60)} min` : ""}
            </div>
            ${r.my_status && html`<div class="st"><span class=${"chip" + (r.my_status.status === "advancing" ? " holo" : r.my_status.status === "eliminated" ? " solid" : "")}>${STATUS_TEXT[r.my_status.status]}</span></div>`}
          </div>`,
      )}
    </div>
    ${s.rounds
      .filter((r) => r.my_status?.message)
      .reverse()
      .map(
        (r) =>
          html`<div class="result-card" style="margin-top:22px">
            <div class="kicker">
              ${r.name} · ${STATUS_TEXT[r.my_status.status]}
            </div>
            <p style="margin:10px 0 0;white-space:pre-wrap">
              ${r.my_status.message}
            </p>
          </div>`,
      )}
    <h2 class="strong" style="font-size:22px;margin:40px 0 12px">
      Announcements
    </h2>
    ${
      anns.length
        ? html`<div class="col">
            ${anns.map(
              (a) =>
                html`<div class="alert">
                  <div class="tiny muted">${ago(a.created_at)}</div>
                  <div class="q">${a.text}</div>
                </div>`,
            )}
          </div>`
        : html`<div class="empty small">No announcements yet.</div>`
    }
  </div>`;
}

function ResultModal() {
  const { round } = useStore(resultModal);
  if (!round) return null;
  const st = round.my_status;
  return html`<${Modal} title=${round.name} onClose=${() => ackResult(round)}>
    <div style="text-align:center;padding:10px 0 4px">
      <div class="kicker" style="justify-content:center">Result</div>
      <div class="hero-title px" style="font-size:24px;margin:22px 0;line-height:1.5">${STATUS_TEXT[st.status]}</div>
      ${st.message && html`<p style="white-space:pre-wrap;max-width:520px;margin:0 auto 18px">${st.message}</p>`}
      <button class="btn" onClick=${() => ackResult(round)}>Got it</button>
    </div>
  </${Modal}>`;
}

// ------------------------------------------------------------------ app
function App() {
  const s = useStore(S);
  const { path } = useStore(route);
  useEffect(() => {
    api("/me")
      .then((me) => S.set({ me }))
      .catch(() => S.set({ me: null }));
  }, []);
  useEffect(() => {
    if (!s.me) return;
    loadAll();
    const stop = connectLive(onLive, (live) => {
      S.set({ live });
      if (live === "too_many_tabs")
        toast("Too many tabs open for your team — close some.", "err");
    });
    const poll = setInterval(loaders.status, 30000); // a safety net; the socket does the real work
    return () => {
      stop();
      clearInterval(poll);
    };
  }, [s.me?.code]);
  useEffect(() => {
    if (s.announcements[0]) {
      const t = new Date(s.announcements[0].created_at).getTime();
      localStorage.setItem("px_seen_ann", String(t));
      S.set({ seenAnn: t });
    }
  }, [s.announcements[0]?.id]);
  if (s.me === undefined)
    return html`<div class="empty" style="padding-top:30vh">
      <span class="pixel blink">…</span>
    </div>`;
  if (s.me === null) return html`<${Login} /><${Toasts} />`;
  let page;
  if (path.startsWith("/people/"))
    page = html`<${Room} sid=${decodeURIComponent(path.slice(8))} />`;
  else if (path.startsWith("/board"))
    page = html`<${Board} S=${S} hasTicker=${!!s.announcements[0]} />`;
  else if (path.startsWith("/evidence")) page = html`<${EvidencePage} />`;
  else if (path.startsWith("/records")) page = html`<${RecordsPage} />`;
  else if (path.startsWith("/brief")) page = html`<${Brief} />`;
  else if (path.startsWith("/docs")) page = html`<${Docs} />`;
  else if (path.startsWith("/rounds")) page = html`<${Rounds} />`;
  else if (path.startsWith("/alliance")) page = html`<${AlliancePage} />`;
  else if (path.startsWith("/sheet")) page = html`<${Sheet} />`;
  else page = html`<${People} />`;
  return html`<div class="shell">
    <${Topbar}
      path=${path}
    /><${Ticker} />${page}<${ResultModal} /><${Toasts} />
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
