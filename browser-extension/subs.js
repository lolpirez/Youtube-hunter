// YouTube Hunter — Subtitles panel (v1.8).
//
// The "CC" half of the Hunter button opens this panel: the video's captions
// with timestamps, scrolling along with the speaker. The line being spoken is
// highlighted and kept in view; click any line to jump the video there.
// Copy (with or without timestamps) and export as .srt / .txt.
//
// The captions are read by the desktop app (GET /v1/transcript): it already
// handles YouTube's PO-token / proxy / cookie hurdles that a page script
// cannot. This file only draws them and follows video.currentTime.
//
// v1.8.2: the panel is the user's to place (drag the header; it docks to edges
// and double-click sends it home), it hugs its content while loading instead of
// sitting at full height, and the loading state is a real skeleton of the list.
//
// v1.8.3: if the desktop app isn't running, the CC click now wakes it — the
// same orb-and-steps animation as the download/cut panel, the app starts
// minimised, and the captions load the moment the bridge answers. No more
// dead "Try again" card on the first click.
//
// Runs in the same isolated world as content.js and borrows its helpers
// through window.__ythKit.

(() => {
  "use strict";
  if (window.__ythSubs) return;

  const kit = () => window.__ythKit || null;
  const call = (path, opt) => kit() ? kit().call(path, opt)
    : Promise.resolve({ reachable: false, status: 0, data: null });
  const $ = (r, s) => r.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const PREF_KEY = "yth.subsPrefs";
  const prefs = { stamps: true, follow: true, w: 0, h: 0, x: null, y: null };
  try {
    chrome.storage.local.get(PREF_KEY).then((x) => {
      const v = x && x[PREF_KEY];
      if (v && typeof v === "object") {
        prefs.stamps = v.stamps !== false;
        prefs.follow = v.follow !== false;
        prefs.w = Math.max(0, Math.round(Number(v.w) || 0));
        prefs.h = Math.max(0, Math.round(Number(v.h) || 0));
        prefs.x = Number.isFinite(v.x) ? Math.round(v.x) : null;
        prefs.y = Number.isFinite(v.y) ? Math.round(v.y) : null;
        if (root) { syncToggles(); applySize(); }
      }
    }).catch(() => {});
  } catch (_) {}
  const savePrefs = () => { try { chrome.storage.local.set({ [PREF_KEY]: prefs }); } catch (_) {} };

  // ── time helpers ──────────────────────────────────────────────────────
  const p2 = (n) => String(n).padStart(2, "0");
  function stamp(sec, hours) {
    const s = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return hours || h ? `${h}:${p2(m)}:${p2(x)}` : `${p2(m)}:${p2(x)}`;
  }
  function srtTime(sec) {
    let ms = Math.max(0, Math.round((sec || 0) * 1000));
    const h = Math.floor(ms / 3600000); ms -= h * 3600000;
    const m = Math.floor(ms / 60000); ms -= m * 60000;
    const s = Math.floor(ms / 1000); ms -= s * 1000;
    return `${p2(h)}:${p2(m)}:${p2(s)},${String(ms).padStart(3, "0")}`;
  }
  // balanced two-line wrap, like the app's SRT writer
  function wrap(text, max = 42) {
    const t = String(text).replace(/\s+/g, " ").trim();
    const w = t.split(" ");
    if (t.length <= max || w.length < 2) return t;
    let best = 1, bestScore = Infinity;
    for (let i = 1; i < w.length; i++) {
      const a = w.slice(0, i).join(" ").length, b = w.slice(i).join(" ").length;
      const score = Math.abs(a - b) + (Math.max(a, b) > max * 1.25 ? 100 : 0);
      if (score < bestScore) { best = i; bestScore = score; }
    }
    return w.slice(0, best).join(" ") + "\n" + w.slice(best).join(" ");
  }
  const toSRT = (cues) => cues.map((c, i) =>
    `${i + 1}\n${srtTime(c[0])} --> ${srtTime(Math.max(c[1], c[0] + 0.2))}\n${wrap(c[2])}\n`).join("\n");
  const toTimedText = (cues) => {
    const hours = cues.some((c) => c[0] >= 3600);
    return cues.map((c) => `[${stamp(c[0], hours)}] ${c[2]}`).join("\n");
  };
  const toPlain = (cues) => cues.map((c) => c[2]).join(" ");

  // ── state ─────────────────────────────────────────────────────────────
  let host = null, root = null, anchorBtn = null;
  let phase = "idle";            // loading | offline | error | ready
  let vid = null;                // {id, short}
  let data = null;               // {cues, tracks, key, lang, auto}
  let message = "";
  let token = 0;
  let active = -1;
  let tick = 0;
  let userScrollAt = 0;
  let query = "";
  let hours = false;
  let loadAt = 0, stageTimer = 0, reveal = false;
  let wakeTimer = 0, wakeStepTimer = 0, wakeT0 = 0, wakeBlur = false, wakeBack = false, wakeCount = 0;
  try {
    chrome.storage.local.get("yth.wakes").then((x) => { wakeCount = (x && x["yth.wakes"]) || 0; }).catch(() => {});
  } catch (_) {}
  // the browser's own "Open YouTube Hunter?" prompt takes focus; focus coming
  // back means it was answered, so the second step can light up
  window.addEventListener("blur", () => { if (phase === "waking") wakeBlur = true; });
  window.addEventListener("focus", () => { if (phase === "waking" && wakeBlur) wakeBack = true; });

  const player = () => document.querySelector("video.html5-main-video")
    || document.querySelector("#movie_player video") || document.querySelector("video");
  const adShowing = () => !!document.querySelector(".html5-video-player.ad-showing");
  const pageTitle = () => (document.title || "video").replace(/^\(\d+\)\s*/, "").replace(/\s*-\s*YouTube\s*$/i, "");

  const CSS = `
    :host { all: initial; }
    :host([data-theme="dark"]) { --bg:#111114; --raise:#17171b; --well:#151518; --line:#24242a; --text:#ececef; --strong:#fafafb;
      --muted:#9a9aa5; --dim:#6a6a75; --track:rgba(255,255,255,.07); --shine:rgba(255,255,255,.11); --ok:#2cc28b; --bad:#ff5468;
      --shadow:0 28px 70px -18px rgba(0,0,0,.75),0 0 0 1px rgba(0,0,0,.4); }
    :host([data-theme="light"]) { --bg:#fff; --raise:#f8f8fa; --well:#fafafb; --line:#e4e4e9; --text:#17171b; --strong:#0b0b0e;
      --muted:#5f5f6b; --dim:#9a9aa5; --track:rgba(0,0,0,.06); --shine:rgba(255,255,255,.9); --ok:#109467; --bad:#e0243f;
      --shadow:0 28px 60px -20px rgba(20,20,30,.35),0 2px 8px rgba(20,20,30,.06); }
    .p { --sans:"YTH Hanken","YTH Vazir",Roboto,Arial,sans-serif; --mono:"YTH Mono",ui-monospace,Consolas,monospace;
      --e:cubic-bezier(.2,.8,.2,1);
      position:fixed; top:68px; left:0; width:392px; height:420px; display:flex; flex-direction:column;
      transition:height .42s var(--e), box-shadow .2s ease;
      background:var(--bg); color:var(--text); border:1px solid var(--line); border-radius:20px; box-shadow:var(--shadow);
      font:400 14px/1.4 var(--sans); -webkit-font-smoothing:antialiased; overflow:hidden; }
    .p[hidden] { display:none; }
    .p *, .p *::before, .p *::after { box-sizing:border-box; }
    .p.arrive { animation:arr .32s var(--e) both; }
    @keyframes arr { from { opacity:0; transform:translateY(-10px) scale(.97); } to { opacity:1; transform:none; } }
    .p::before { content:""; position:absolute; inset:0 0 auto 0; height:110px; pointer-events:none;
      background:radial-gradient(120% 100% at 85% -40%, rgba(var(--acc-rgb),.16), transparent 70%); }

    /* v1.8.1: drag the left edge / bottom edge / corner to resize, like the main panel */
    .rz { position:absolute; z-index:6; touch-action:none; }
    .rz[data-rz="w"] { top:22px; bottom:22px; left:0; width:5px; cursor:ew-resize; }
    .rz[data-rz="s"] { left:22px; right:22px; bottom:0; height:5px; cursor:ns-resize; }
    .rz[data-rz="e"] { top:22px; bottom:22px; right:0; width:5px; cursor:ew-resize; }
    .rz[data-rz="sw"] { left:0; bottom:0; width:20px; height:20px; cursor:nesw-resize; }
    .rz[data-rz="se"] { right:0; bottom:0; width:20px; height:20px; cursor:nwse-resize; }
    .p:not([data-phase="ready"]) .rz[data-rz="s"], .p:not([data-phase="ready"]) .rz[data-rz="sw"],
    .p:not([data-phase="ready"]) .rz[data-rz="se"] { display:none; }
    .rz[data-rz="sw"]::after { content:""; position:absolute; left:6px; bottom:6px; width:8px; height:8px;
      border-left:2px solid var(--dim); border-bottom:2px solid var(--dim); border-bottom-left-radius:7px;
      opacity:.5; transition:opacity .15s ease, border-color .15s ease; }
    .p:hover .rz[data-rz="sw"]::after { opacity:.85; }
    .rz:hover::after, .p[data-rz-drag] .rz::after { border-color:var(--acc) !important; opacity:1; }

    .hd { position:relative; display:flex; align-items:center; gap:10px; padding:14px 12px 8px 16px;
      cursor:grab; touch-action:none; user-select:none; -webkit-user-select:none; }
    .grip { position:absolute; top:5px; left:50%; width:34px; height:4px; margin-left:-17px; border-radius:2px; background:var(--dim);
      opacity:0; transition:opacity .2s ease, background .2s ease; pointer-events:none; }
    .hd:hover .grip { opacity:.5; }
    .p[data-live="drag"] .hd { cursor:grabbing; }
    .p[data-live="drag"] .grip { opacity:1; background:var(--acc); }
    .p[data-live] { transition:none; }
    .p[data-live="drag"], .p[data-live="rz"] { box-shadow:0 44px 100px -20px rgba(0,0,0,.7), 0 0 0 1px rgba(var(--acc-rgb),.38); }
    .p[data-glide] { transition:left .38s var(--e), top .38s var(--e), width .38s var(--e), height .42s var(--e), box-shadow .2s ease; }
    .pg { position:absolute; inset:auto 0 0 0; height:2px; overflow:hidden; opacity:0; transition:opacity .25s ease; pointer-events:none; }
    .p[data-phase="loading"] .pg { opacity:1; }
    .pg::after { content:""; position:absolute; top:0; bottom:0; width:38%; border-radius:2px;
      background:linear-gradient(90deg, transparent, var(--acc), var(--acc2), transparent);
      animation:sweep 1.5s cubic-bezier(.45,.05,.35,1) infinite; }
    @keyframes sweep { from { transform:translateX(-110%); } to { transform:translateX(290%); } }
    .p[data-phase="loading"] .ttl svg { animation:breathe 1.8s ease-in-out infinite; }
    @keyframes breathe { 50% { opacity:.6; filter:drop-shadow(0 0 6px rgba(var(--acc-rgb),.75)); } }
    .ttl { display:flex; align-items:center; gap:9px; font-weight:700; font-size:15px; color:var(--strong); letter-spacing:-.01em; }
    .ttl svg { color:var(--acc); }
    .cnt { font:500 11.5px/1 var(--mono); color:var(--muted); background:var(--track); border-radius:10px; padding:4px 8px; }
    .sp { flex:1; }
    .x { all:unset; cursor:pointer; width:30px; height:30px; display:grid; place-items:center; border-radius:10px; color:var(--muted); }
    .x:hover { background:var(--track); color:var(--strong); }

    .bar { position:relative; display:flex; align-items:center; gap:8px; padding:4px 14px 10px; flex-wrap:wrap; }
    .srch { flex:1 1 150px; min-width:120px; display:flex; align-items:center; gap:7px; height:32px; padding:0 10px;
      background:var(--well); border:1px solid var(--line); border-radius:10px; color:var(--muted); }
    .srch:focus-within { border-color:rgba(var(--acc-rgb),.7); }
    .srch input { all:unset; flex:1; min-width:0; font:400 13px var(--sans); color:var(--text); }
    .sel { all:unset; box-sizing:border-box; cursor:pointer; height:32px; max-width:150px; padding:0 26px 0 10px; font:500 12.5px/32px var(--sans);
      color:var(--text); background:var(--well); border:1px solid var(--line); border-radius:10px; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;
      background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),linear-gradient(135deg,var(--muted) 50%,transparent 50%);
      background-position:calc(100% - 14px) 14px, calc(100% - 9px) 14px; background-size:5px 5px; background-repeat:no-repeat; }
    .sel option { background:var(--bg); color:var(--text); }
    .sw { all:unset; cursor:pointer; display:inline-flex; align-items:center; gap:7px; height:30px; padding:0 11px 0 8px; border-radius:15px;
      border:1px solid var(--line); font:600 12.5px/1 var(--sans); color:var(--muted); transition:all .18s ease; }
    .sw i { width:26px; height:15px; border-radius:8px; background:var(--track); position:relative; transition:background .18s ease; }
    .sw i::after { content:""; position:absolute; top:2px; left:2px; width:11px; height:11px; border-radius:50%; background:var(--muted); transition:all .2s var(--e); }
    .sw[aria-pressed="true"] { color:var(--strong); border-color:rgba(var(--acc-rgb),.5); background:rgba(var(--acc-rgb),.1); }
    .sw[aria-pressed="true"] i { background:rgba(var(--acc-rgb),.35); }
    .sw[aria-pressed="true"] i::after { left:13px; background:var(--acc); }
    .sw:focus-visible, .btn:focus-visible, .x:focus-visible { outline:2px solid rgba(var(--acc-rgb),.85); outline-offset:1px; }

    .list { position:relative; flex:1; min-height:0; overflow-y:auto; padding:2px 8px 10px; scrollbar-width:thin; scrollbar-color:var(--track) transparent;
      border-top:1px solid var(--line); background:var(--well); }
    .row { display:flex; gap:10px; padding:8px 10px; border-radius:11px; cursor:pointer; position:relative; transition:background .15s ease; }
    .row:hover { background:var(--track); }
    .row .t { flex:none; align-self:flex-start; margin-top:1px; font:600 11.5px/1 var(--mono); color:var(--muted); background:var(--track);
      border-radius:9px; padding:5px 7px; min-width:46px; text-align:center; transition:all .15s ease; }
    .row .w { flex:1; min-width:0; font-size:14px; line-height:1.5; color:var(--text); unicode-bidi:plaintext; text-align:start; overflow-wrap:anywhere; }
    .list.nostamp .row .t { display:none; }
    .list.nostamp .row { display:inline; padding:3px 3px; border-radius:6px; }
    .list.nostamp .row .w { display:inline; }
    .list.nostamp .flow { padding:6px 8px; line-height:1.9; }
    .row.on { background:rgba(var(--acc-rgb),.14); }
    .row.on::before { content:""; position:absolute; inset-block:8px; inset-inline-start:0; width:3px; border-radius:2px; background:var(--acc); }
    .row.on .t { background:var(--acc); color:var(--on-acc,#fff); }
    .row.on .w { color:var(--strong); font-weight:500; }
    .list.nostamp .row.on::before { display:none; }
    .list.nostamp .row.on { background:rgba(var(--acc-rgb),.22); box-decoration-break:clone; -webkit-box-decoration-break:clone; }
    mark { background:rgba(var(--acc-rgb),.35); color:inherit; border-radius:3px; padding:0 1px; }
    .empty { padding:40px 22px; text-align:center; color:var(--muted); }

    .now { position:absolute; left:50%; transform:translateX(-50%); bottom:62px; z-index:3; all:unset; cursor:pointer;
      display:inline-flex; align-items:center; gap:6px; padding:7px 13px; border-radius:16px; font:600 12px/1 var(--sans);
      background:var(--acc); color:var(--on-acc,#fff); box-shadow:0 8px 22px -8px rgba(var(--acc-rgb),.9); }
    .now[hidden] { display:none; }

    .ft { display:flex; gap:8px; padding:10px 12px; border-top:1px solid var(--line); background:var(--bg); }
    .btn { all:unset; cursor:pointer; flex:1; display:inline-flex; align-items:center; justify-content:center; gap:7px; height:36px; border-radius:11px;
      border:1px solid var(--line); font:600 13px/1 var(--sans); color:var(--text); background:var(--raise); transition:all .15s ease; }
    .btn:hover { border-color:rgba(var(--acc-rgb),.55); background:rgba(var(--acc-rgb),.1); }
    .btn.pri { background:linear-gradient(135deg,var(--acc),var(--acc2)); border-color:transparent; color:var(--on-acc,#fff); }
    .btn.pri:hover { filter:brightness(1.08); }
    .btn[disabled] { opacity:.45; pointer-events:none; }
    .btn.ok { border-color:var(--ok); color:var(--ok); }

    .msg { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:8px; padding:26px 28px 30px; text-align:center;
      border-top:1px solid var(--line); background:var(--well); color:var(--muted); font-size:13px; line-height:1.5; }
    .msg b { color:var(--strong); font-size:15px; letter-spacing:-.01em; }
    .msg span { max-width:290px; }
    .msg .btn { flex:none; padding:0 18px; margin-top:8px; }
    .msg .ico { width:46px; height:46px; border-radius:50%; display:grid; place-items:center; color:var(--acc); margin-bottom:4px;
      background:radial-gradient(circle at 50% 35%, rgba(var(--acc-rgb),.24), rgba(var(--acc-rgb),.06) 72%);
      box-shadow:inset 0 0 0 1px rgba(var(--acc-rgb),.3); animation:pop .45s var(--e) both; }
    .msg.bad .ico { color:var(--bad); background:radial-gradient(circle at 50% 35%, rgba(255,84,104,.22), rgba(255,84,104,.05) 72%);
      box-shadow:inset 0 0 0 1px rgba(255,84,104,.32); }
    @keyframes pop { from { opacity:0; transform:scale(.82); } }

    /* waking the desktop app: the same orb-and-steps moment as download/cut */
    .wake { flex:1; min-height:0; overflow-y:auto; display:flex; flex-direction:column; align-items:center; justify-content:center;
      text-align:center; padding:10px 20px 16px; border-top:1px solid var(--line); background:var(--well);
      scrollbar-width:thin; scrollbar-color:var(--track) transparent; }
    .wstage { position:relative; width:112px; height:112px; display:grid; place-items:center; margin:0 0 8px; flex:none; }
    .wstage > * { grid-area:1/1; }
    .wr { border-radius:50%; box-sizing:border-box; }
    .wr.r1 { width:112px; height:112px; border:1px dashed rgba(var(--acc-rgb),.3); animation:spin 22s linear infinite; }
    .wr.r2 { width:88px; height:88px;
      background:conic-gradient(from 0deg, transparent 0 50%, rgba(var(--acc-rgb),.25) 70%, var(--acc) 90%, transparent 91%);
      -webkit-mask:radial-gradient(farthest-side, transparent calc(100% - 2.5px), #000 calc(100% - 2px));
      mask:radial-gradient(farthest-side, transparent calc(100% - 2.5px), #000 calc(100% - 2px));
      animation:spin 1.9s linear infinite; }
    .wr.r3 { width:80px; height:80px; background:radial-gradient(circle, rgba(var(--acc-rgb),.32), rgba(var(--acc-rgb),0) 68%); animation:glowb 2.4s ease-in-out infinite; }
    @keyframes glowb { 50% { transform:scale(1.14); opacity:.6; } }
    @keyframes spin { to { transform:rotate(360deg); } }
    .wsat { position:relative; width:100px; height:100px; animation:spin 3.4s linear infinite reverse; }
    .wsat i { position:absolute; top:-3px; left:50%; width:6px; height:6px; margin-left:-3px; border-radius:50%;
      background:var(--acc); box-shadow:0 0 10px 2px rgba(var(--acc-rgb),.85); }
    .wcore { position:relative; width:58px; height:58px; border-radius:50%; display:grid; place-items:center;
      background:radial-gradient(circle at 50% 30%, var(--raise), var(--bg) 75%);
      border:1px solid rgba(var(--acc-rgb),.35);
      box-shadow:0 14px 34px -12px rgba(var(--acc-rgb),.8), inset 0 1px 0 rgba(255,255,255,.06);
      animation:float 3s ease-in-out infinite; transition:border-color .4s ease, box-shadow .4s ease; }
    @keyframes float { 50% { transform:translateY(-3px); } }
    .wtick { width:26px; height:26px; border-radius:50%; display:grid; place-items:center; color:#fff; background:var(--ok);
      box-shadow:0 0 0 3px var(--bg); transform:translate(21px,21px) scale(0); transition:transform .5s cubic-bezier(.3,1.7,.5,1) .1s; }
    .wake.linked .wtick { transform:translate(21px,21px) scale(1); }
    .wake.linked .wr.r1, .wake.linked .wr.r2, .wake.linked .wsat { animation:collapse .6s var(--e) forwards; }
    @keyframes collapse { to { transform:scale(.55); opacity:0; } }
    .wake.linked .wr.r3 { background:radial-gradient(circle, color-mix(in srgb, var(--ok) 35%, transparent), transparent 68%); animation:none; }
    .wake.linked .wcore { border-color:var(--ok); box-shadow:0 14px 34px -12px color-mix(in srgb, var(--ok) 80%, transparent); animation:none; }
    .wt { font:700 17px/1.2 var(--sans); color:var(--strong); letter-spacing:-.015em; }
    .wb { margin:4px 0 10px; font-size:13.5px; color:var(--muted); }
    .wsteps { list-style:none; margin:0 0 10px; padding:0; width:100%; display:flex; flex-direction:column; gap:2px; text-align:start; }
    .wsteps li { display:flex; align-items:center; gap:11px; padding:7px 10px; border-radius:11px;
      font:600 13.5px/1.2 var(--sans); color:var(--dim); transition:color .3s ease, background .3s ease; }
    .wsteps li i { position:relative; flex:none; width:20px; height:20px; border-radius:50%; box-sizing:border-box;
      border:1.5px solid var(--line); display:grid; place-items:center; transition:background .3s ease, border-color .3s ease; }
    .wsteps li i u { width:5px; height:9px; margin-top:-2px; box-sizing:border-box; border:solid #fff; border-width:0 2px 2px 0;
      transform:rotate(45deg) scale(.4); opacity:0; transition:transform .35s cubic-bezier(.3,1.7,.5,1), opacity .2s ease; }
    .wake[data-step="1"] li[data-n="1"], .wake[data-step="2"] li[data-n="2"] { color:var(--strong); background:var(--track); }
    .wake[data-step="1"] li[data-n="1"] i, .wake[data-step="2"] li[data-n="2"] i { border-color:rgba(var(--acc-rgb),.25); border-top-color:var(--acc); animation:spin .8s linear infinite; }
    .wake[data-step="2"] li[data-n="1"], .wake[data-step="3"] li { color:var(--text); }
    .wake[data-step="2"] li[data-n="1"] i, .wake[data-step="3"] li i { background:var(--ok); border-color:var(--ok); }
    .wake[data-step="2"] li[data-n="1"] i u, .wake[data-step="3"] li i u { opacity:1; transform:rotate(45deg); }
    .wake[data-step="3"] li[data-n="2"] i { transition-delay:.12s; }
    .wake[data-step="3"] li[data-n="3"] i { transition-delay:.24s; }
    .whint { width:100%; box-sizing:border-box; display:grid; grid-template-columns:132px 1fr; gap:12px; align-items:center;
      padding:10px; border-radius:14px; background:var(--bg); border:1px solid var(--line); text-align:start; }
    .whint p { margin:0; font-size:13px; line-height:1.45; color:var(--muted); }
    .whint1 { margin:0; font-size:13px; color:var(--muted); }
    .mock { position:relative; display:flex; flex-direction:column; gap:6px; padding:9px 9px 8px; border-radius:10px;
      background:#26262b; border:1px solid rgba(255,255,255,.09); box-shadow:0 10px 22px -10px rgba(0,0,0,.7); }
    .mk-t { width:74%; height:6px; border-radius:3px; background:rgba(255,255,255,.55); }
    .mk-u { width:90%; height:4px; border-radius:2px; background:rgba(255,255,255,.18); }
    .mk-row { display:flex; align-items:center; gap:5px; }
    .mk-box { width:12px; height:12px; border-radius:3px; box-sizing:border-box; display:grid; place-items:center;
      border:1.4px solid rgba(255,255,255,.55); color:#062e6f; animation:mkbox 4.4s infinite; }
    .mk-box svg { opacity:0; animation:mktick 4.4s infinite; }
    @keyframes mkbox { 0%,33% { background:transparent; border-color:rgba(255,255,255,.55); } 36%,96% { background:#a8c7fa; border-color:#a8c7fa; } 100% { background:transparent; } }
    @keyframes mktick { 0%,33% { opacity:0; } 36%,96% { opacity:1; } 100% { opacity:0; } }
    .mk-l { font:600 9.5px/1 var(--sans); color:rgba(255,255,255,.8); white-space:nowrap; }
    .mk-btns { display:flex; justify-content:flex-end; gap:4px; margin-top:1px; }
    .mk-btns span { font:700 9.5px/1 var(--sans); padding:5px 8px; border-radius:999px; white-space:nowrap; }
    .mk-open { color:#c2e7ff; background:#004a77; animation:mkopen 4.4s infinite; }
    .mk-no { color:#062e6f; background:#a8c7fa; }
    @keyframes mkopen { 0%,70% { transform:none; filter:none; } 73% { transform:scale(.92); filter:brightness(1.5); } 78%,100% { transform:none; filter:none; } }
    .mk-cur { position:absolute; left:88%; top:88%; width:14px; height:14px; pointer-events:none;
      animation:mkcur 4.4s cubic-bezier(.5,0,.3,1) infinite; filter:drop-shadow(0 2px 2px rgba(0,0,0,.5)); }
    @keyframes mkcur {
      0% { left:88%; top:88%; opacity:0; } 8% { opacity:1; }
      30% { left:9%; top:47%; transform:none; } 34% { left:9%; top:47%; transform:scale(.82); } 38% { left:9%; top:47%; transform:none; }
      64% { left:30%; top:76%; transform:none; } 72% { left:30%; top:76%; transform:scale(.82); } 76% { left:30%; top:76%; transform:none; }
      92% { left:30%; top:76%; opacity:1; } 100% { left:30%; top:76%; opacity:0; } }
    .wtime { display:block; width:100%; height:2px; margin-top:12px; border-radius:1px; background:var(--track); overflow:hidden; flex:none; }
    .wtime i { display:block; height:100%; background:linear-gradient(90deg,var(--acc2),var(--acc)); transform-origin:left; animation:drain 30s linear forwards; }
    @keyframes drain { to { transform:scaleX(0); } }

    /* loading: a skeleton shaped like the real list, a live status line */
    .ld { flex:1; min-height:0; display:flex; flex-direction:column; border-top:1px solid var(--line); background:var(--well); overflow:hidden; }
    .skw { flex:1; min-height:0; overflow:hidden; padding:8px 8px 0; display:flex; flex-direction:column; gap:2px;
      -webkit-mask-image:linear-gradient(#000 58%, transparent); mask-image:linear-gradient(#000 58%, transparent); }
    .skr { display:flex; gap:10px; padding:9px 10px; animation:skin .5s var(--e) both; animation-delay:calc(var(--i) * 60ms); }
    @keyframes skin { from { opacity:0; transform:translateY(5px); } }
    .sh { display:block; border-radius:8px; animation:shim 1.6s linear infinite; animation-delay:calc(var(--i) * -140ms);
      background:linear-gradient(100deg, var(--track) 35%, var(--shine) 50%, var(--track) 65%) 100% 0 / 260% 100%; }
    @keyframes shim { to { background-position:0 0; } }
    .skr .c { flex:none; width:46px; height:22px; border-radius:9px; }
    .skr .ls { flex:1; display:grid; gap:7px; padding-top:3px; align-content:start; }
    .skr .ls .sh { height:11px; }
    .stat { flex:none; display:flex; align-items:center; justify-content:center; gap:9px; padding:12px 20px 18px; color:var(--muted); font-size:12.5px; }
    .stat .dot { width:7px; height:7px; border-radius:50%; background:var(--acc); animation:ping 1.6s ease-out infinite; }
    @keyframes ping { 0% { box-shadow:0 0 0 0 rgba(var(--acc-rgb),.55); } 100% { box-shadow:0 0 0 9px rgba(var(--acc-rgb),0); } }
    .stat .tx { transition:opacity .16s ease; }
    .stat .tx.fade { opacity:0; }
    .stat .el { font:500 11px/1 var(--mono); color:var(--dim); }

    /* the list arrives in one soft cascade */
    .row.in { animation:rin .45s var(--e) both; animation-delay:calc(var(--i) * 26ms); }
    @keyframes rin { from { opacity:0; transform:translateY(7px); } }

    @media (prefers-reduced-motion: reduce) {
      .p, .p *, .p *::before, .p *::after { animation:none !important; transition:none !important; }
    }
  `;

  const ICON_CC = '<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><rect x="2.8" y="5" width="18.4" height="14" rx="3.6" fill="none" stroke="currentColor" stroke-width="2"/><path d="M10.4 10.1a2.3 2.3 0 1 0 0 3.8M16.6 10.1a2.3 2.3 0 1 0 0 3.8" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>';
  const ICON_SEARCH = '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.6" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M10.5 10.5L14 14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
  const ICON_DL = '<svg width="15" height="15" viewBox="0 0 18 18" aria-hidden="true"><path d="M9 2.5v9m0 0L5.4 7.9M9 11.5l3.6-3.6M3 15h12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_COPY = '<svg width="15" height="15" viewBox="0 0 18 18" aria-hidden="true"><rect x="6" y="6" width="9" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 6V4.5A1.5 1.5 0 0 0 10.5 3h-6A1.5 1.5 0 0 0 3 4.5v6A1.5 1.5 0 0 0 4.5 12H6" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
  const ICON_X = '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
  const ICON_DOWN = '<svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v9m0 0L4.5 8.5M8 12l3.5-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_CHECK = '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.6 8.4l2.9 2.9 5.9-6.6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  const ICON_OFF = '<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5v8M7.1 6.9a7.2 7.2 0 1 0 9.8 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
  const ICON_NOCC = '<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><rect x="2.8" y="5" width="18.4" height="14" rx="3.6" fill="none" stroke="currentColor" stroke-width="2"/><path d="M10.4 10.1a2.3 2.3 0 1 0 0 3.8M16.6 10.1a2.3 2.3 0 1 0 0 3.8" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><path d="M5 20.5L19 3.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';

  function build() {
    host = document.createElement("yth-subs-panel");
    host.style.cssText = "all: initial; position: fixed; z-index: 2147483000; top: 0; left: 0;";
    root = host.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${CSS}</style>
      <section class="p" role="dialog" aria-label="Subtitles" hidden>
        <header class="hd" title="Drag to move · double-click to send it home">
          <i class="grip" aria-hidden="true"></i>
          <span class="ttl">${ICON_CC}<span>Subtitles</span></span>
          <span class="cnt" hidden></span>
          <span class="sp"></span>
          <button class="x" type="button" aria-label="Close">${ICON_X}</button>
          <i class="pg" aria-hidden="true"></i>
        </header>
        <div class="bar">
          <label class="srch">${ICON_SEARCH}<input type="text" placeholder="Search the transcript" spellcheck="false" autocomplete="off"></label>
          <select class="sel" aria-label="Caption language" hidden></select>
          <button class="sw" type="button" data-sw="stamps" aria-pressed="true"><i></i>Timestamps</button>
          <button class="sw" type="button" data-sw="follow" aria-pressed="true"><i></i>Follow video</button>
        </div>
        <div class="body" style="display:contents"></div>
        <footer class="ft">
          <button class="btn" type="button" data-act="copy">${ICON_COPY}<span>Copy</span></button>
          <button class="btn" type="button" data-act="txt">${ICON_DL}<span>.txt</span></button>
          <button class="btn pri" type="button" data-act="srt">${ICON_DL}<span>Export .srt</span></button>
        </footer>
        <i class="rz" data-rz="w" aria-hidden="true"></i>
        <i class="rz" data-rz="s" aria-hidden="true"></i>
        <i class="rz" data-rz="e" aria-hidden="true"></i>
        <i class="rz" data-rz="sw" title="Drag to resize · double-click to reset" aria-label="Resize"></i>
        <i class="rz" data-rz="se" title="Drag to resize · double-click to reset" aria-label="Resize"></i>
      </section>`;
    document.documentElement.appendChild(host);

    $(root, ".x").addEventListener("click", close);
    root.addEventListener("click", onClick);
    $(root, ".srch input").addEventListener("input", (e) => { query = e.target.value.trim(); paintList(true); });
    $(root, ".sel").addEventListener("change", (e) => load(e.target.value));
    root.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
    // keep YouTube's single-key shortcuts (space, k, f, m…) out of our search box
    for (const ev of ["keydown", "keyup", "keypress"]) {
      $(root, ".srch input").addEventListener(ev, (e) => { if (e.key !== "Escape") e.stopPropagation(); });
    }
    const hd = $(root, ".hd");
    hd.addEventListener("pointerdown", onHeadDown);
    hd.addEventListener("dblclick", onHeadDbl);
    root.querySelectorAll(".rz").forEach((z) => {
      z.addEventListener("pointerdown", onRzDown);
      if (/^s[we]$/.test(z.getAttribute("data-rz"))) {
        z.addEventListener("dblclick", (e) => { e.preventDefault(); e.stopPropagation(); resetSize(); });
      }
    });
    window.addEventListener("resize", () => { if (isOpen()) applySize(); }, { passive: true });
    retheme();
    syncToggles();
    applySize();
  }

  function retheme() {
    if (!host || !kit()) return;
    const { appTheme, accent } = kit().theme();
    const mode = appTheme && appTheme.mode ? appTheme.mode : (kit().isDark() ? "dark" : "light");
    host.setAttribute("data-theme", mode === "light" ? "light" : "dark");
    const vars = (appTheme && appTheme.vars) || {};
    for (const k of kit().THEME_VARS_LIST) {
      if (vars[k]) host.style.setProperty("--" + k, vars[k]); else host.style.removeProperty("--" + k);
    }
    kit().applyVars(host);
  }

  function syncToggles() {
    if (!root) return;
    root.querySelectorAll(".sw").forEach((b) => b.setAttribute("aria-pressed", String(!!prefs[b.dataset.sw])));
    const l = $(root, ".list");
    if (l) l.classList.toggle("nostamp", !prefs.stamps);
  }

  // ── place + size: drag the header to move, an edge / corner to resize ─
  const MIN_W = 300, MIN_H = 260, MAX_W = 900, M = 8, DEF_W = 392, DEF_H = 640, HOME = { x: 20, y: 68 };
  const LOAD_H = 352, MSG_H = 300, WAKE_H = 470;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const viewW = () => document.documentElement.clientWidth || window.innerWidth;
  const narrow = () => viewW() <= 520;
  // while loading (or empty / offline) the panel hugs its content; only a ready
  // list — or a language switch that keeps the old list in view — gets full height
  const compactNow = () => phase !== "ready" && !(phase === "loading" && data && data.cues && data.cues.length);

  function geo() {
    const vw = viewW(), vh = window.innerHeight;
    const w = narrow() ? vw - 16 : clamp(prefs.w || DEF_W, MIN_W, Math.min(MAX_W, vw - 2 * M));
    const x = narrow() ? 8 : prefs.x == null ? vw - w - HOME.x : clamp(prefs.x, M, vw - w - M);
    const y = prefs.y == null ? HOME.y : clamp(prefs.y, M, Math.max(M, vh - 220));
    const compact = compactNow();
    let h = compact ? (phase === "loading" ? LOAD_H : (phase === "waking" || phase === "linked") ? WAKE_H : MSG_H) : (prefs.h || Math.min(DEF_H, vh - 88));
    h = Math.max(compact ? 200 : MIN_H, Math.min(vh - y - M, h));
    return { x, y, w, h };
  }
  function applySize() {
    const sec = root && $(root, ".p");
    if (!sec) return;
    const g = geo();
    sec.style.left = Math.round(g.x) + "px";
    sec.style.top = Math.round(g.y) + "px";
    sec.style.width = Math.round(g.w) + "px";
    sec.style.height = Math.round(g.h) + "px";
  }
  function glide() {
    const sec = $(root, ".p");
    sec.setAttribute("data-glide", "");
    clearTimeout(glide.t);
    glide.t = setTimeout(() => sec.removeAttribute("data-glide"), 460);
  }

  function onHeadDown(e) {
    if (e.button !== 0 || (e.target.closest && e.target.closest("button, input, select, a"))) return;
    const sec = $(root, ".p"), head = e.currentTarget;
    const r = sec.getBoundingClientRect();
    const sx = e.clientX, sy = e.clientY;
    let moved = false;
    try { head.setPointerCapture(e.pointerId); } catch (_) {}
    const move = (ev) => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      if (!moved) {
        if (Math.hypot(dx, dy) < 4) return;
        moved = true;
        sec.removeAttribute("data-glide");
        sec.setAttribute("data-live", "drag");
      }
      const vw = viewW(), vh = window.innerHeight;
      const x = narrow() ? r.left : clamp(r.left + dx, M, vw - M - r.width);
      const y = clamp(r.top + dy, M, Math.max(M, vh - 220));
      sec.style.left = Math.round(x) + "px";
      sec.style.top = Math.round(y) + "px";
      // never hang off the bottom of the window while moving; the full height returns on release
      sec.style.height = Math.round(Math.max(200, Math.min(r.height, vh - y - M))) + "px";
    };
    const up = () => {
      head.removeEventListener("pointermove", move);
      head.removeEventListener("pointerup", up);
      head.removeEventListener("pointercancel", up);
      if (!moved) return;
      sec.removeAttribute("data-live");
      const b = sec.getBoundingClientRect(), vw = viewW();
      let x = b.left, y = b.top;
      // dock to near edges, and to the home spot under YouTube's top bar
      if (x < 24) x = HOME.x;
      if (vw - b.right < 24) x = vw - b.width - HOME.x;
      if (y < 24) y = M;
      if (Math.abs(y - HOME.y) < 16) y = HOME.y;
      const home = Math.abs(x - (vw - b.width - HOME.x)) < 20 && Math.abs(y - HOME.y) < 20;
      prefs.x = home || narrow() ? null : Math.round(x);
      prefs.y = home ? null : Math.round(y);
      glide(); applySize(); savePrefs();
    };
    head.addEventListener("pointermove", move);
    head.addEventListener("pointerup", up);
    head.addEventListener("pointercancel", up);
  }
  function onHeadDbl(e) {
    if (e.target.closest && e.target.closest("button, input, select, a")) return;
    prefs.x = null; prefs.y = null; prefs.w = 0; prefs.h = 0;
    savePrefs(); glide(); applySize();
  }

  function onRzDown(e) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const z = e.currentTarget, dir = z.getAttribute("data-rz");
    const sec = $(root, ".p");
    const r = sec.getBoundingClientRect();
    const sx = e.clientX, sy = e.clientY, vw = viewW(), vh = window.innerHeight;
    sec.removeAttribute("data-glide");
    sec.setAttribute("data-live", "rz");
    sec.setAttribute("data-rz-drag", dir);
    try { z.setPointerCapture(e.pointerId); } catch (_) {}
    const move = (ev) => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      if (dir.includes("w")) {
        const w = clamp(r.width - dx, MIN_W, Math.min(MAX_W, r.right - M));
        sec.style.width = Math.round(w) + "px";
        sec.style.left = Math.round(r.right - w) + "px";
      }
      if (dir.includes("e")) {
        const w = clamp(r.width + dx, MIN_W, Math.min(MAX_W, vw - M - r.left));
        sec.style.width = Math.round(w) + "px";
        sec.style.left = Math.round(r.left) + "px";
      }
      if (dir.includes("s")) {
        sec.style.height = Math.round(clamp(r.height + dy, MIN_H, vh - r.top - M)) + "px";
      }
    };
    const up = () => {
      z.removeEventListener("pointermove", move);
      z.removeEventListener("pointerup", up);
      z.removeEventListener("pointercancel", up);
      sec.removeAttribute("data-live");
      sec.removeAttribute("data-rz-drag");
      const b = sec.getBoundingClientRect();
      if (dir.includes("w") || dir.includes("e")) {
        prefs.w = Math.round(b.width);
        // at the home spot the right edge is the anchor; once moved (or pulled by its east edge) pin it
        if (prefs.x != null || dir.includes("e")) {
          prefs.x = Math.round(b.left);
          if (prefs.y == null) prefs.y = Math.round(b.top);
        }
      }
      if (dir.includes("s") && !compactNow()) prefs.h = Math.round(b.height);
      applySize();
      savePrefs();
    };
    z.addEventListener("pointermove", move);
    z.addEventListener("pointerup", up);
    z.addEventListener("pointercancel", up);
  }
  function resetSize() {
    prefs.w = 0; prefs.h = 0;
    savePrefs();
    glide();
    applySize();
  }

  const isOpen = () => !!(host && !$(root, ".p").hidden);

  function setExpanded(v) {
    document.querySelectorAll("yth-hunter-button").forEach((h) => {
      const b = h.shadowRoot && h.shadowRoot.querySelector('button[data-mode="subs"]');
      if (b) b.setAttribute("aria-expanded", v && h === anchorBtn ? "true" : "false");
    });
  }

  // ── open / close ──────────────────────────────────────────────────────
  function open(btn) {
    if (!host) build();
    anchorBtn = btn || anchorBtn;
    const sec = $(root, ".p");
    const fresh = sec.hidden;
    sec.hidden = false;
    if (fresh) {
      sec.setAttribute("data-live", "open");   // snap to the right box, then allow tweens again
      requestAnimationFrame(() => requestAnimationFrame(() => sec.removeAttribute("data-live")));
      sec.classList.remove("arrive"); void sec.offsetWidth; sec.classList.add("arrive");
    }
    setExpanded(true);
    retheme();
    applySize();
    const ping = call("/v1/ping", { timeout: 1500 });
    ping.then((p) => {
      if (p.reachable && p.data && p.data.ok && kit()) { kit().adoptTheme(p.data); retheme(); }
    });
    const v = kit() && kit().currentVideo();
    if (!v) { vid = null; phase = "error"; message = "Open a video first."; render(); return; }
    if (!vid || vid.id !== v.id || phase === "idle" || phase === "offline" || phase === "error") {
      vid = v;
      // Decide from the ping the click already made: app up → read the captions,
      // app closed → wake it. The ping fails instantly when nothing listens.
      ping.then((p) => {
        if (!isOpen() || !vid) return;
        if (p.reachable && p.data && p.data.ok) load("");
        else wake();
      });
    } else render();
    startTick();
  }

  function close() {
    if (!host) return;
    $(root, ".p").hidden = true;
    clearInterval(tick); tick = 0;
    stopStatus();
    stopWake();
    if (phase === "waking" || phase === "linked") phase = "idle";
    setExpanded(false);
    if (anchorBtn && anchorBtn.shadowRoot) {
      const b = anchorBtn.shadowRoot.querySelector('button[data-mode="subs"]');
      b && b.focus({ preventScroll: true });
    }
  }

  function toggle(btn) {
    if (isOpen() && (!btn || btn === anchorBtn)) close(); else open(btn);
  }

  // ── data ──────────────────────────────────────────────────────────────
  async function load(key) {
    const my = ++token;
    phase = "loading"; message = ""; active = -1;
    if (!key) data = null;
    render();
    const path = "/v1/transcript?v=" + encodeURIComponent(vid.id) + (key ? "&key=" + encodeURIComponent(key) : "");
    const r = await call(path, { timeout: 90000 });
    if (my !== token) return;
    if (!r.reachable) { wake(); return; }
    const d = r.data || {};
    if (r.status === 404) { phase = "error"; message = "Update YouTube Hunter to use subtitles — your app is older than this extension."; render(); return; }
    if (!d.ok || !Array.isArray(d.cues) || !d.cues.length) {
      phase = "error"; message = d.error || "No captions could be read for this video."; data = { tracks: d.tracks || [], cues: [] }; render(); return;
    }
    data = { cues: d.cues, tracks: d.tracks || [], key: d.key || "", lang: d.lang || "", auto: !!d.auto };
    hours = d.cues.some((c) => c[0] >= 3600);
    phase = "ready";
    reveal = true;
    render();
    syncToVideo(true);
  }

  // ── waking the app: same designed moment as the download/cut panel ───
  // The installer registers youtubehunter:// → YouTube Hunter.exe, so a click
  // here starts the app minimised. We then poll the bridge and load the
  // captions the second it answers — no dead end, no second click.
  function wakeHTML(linked) {
    const k = kit();
    const logo = k && k.markSVG ? k.markSVG(32, false) : ICON_CC;
    const full = wakeCount <= 3;
    return `<div class="wake${linked ? " linked" : ""}" data-step="${linked ? 3 : 1}" role="status" aria-live="polite">
        <div class="wstage" aria-hidden="true">
          <span class="wr r1"></span><span class="wr r2"></span><span class="wr r3"></span>
          <span class="wsat"><i></i></span>
          <span class="wcore">${logo}</span>
          <span class="wtick">${ICON_CHECK}</span>
        </div>
        <b class="wt">${esc(linked ? "Connected" : "Waking YouTube Hunter")}</b>
        <p class="wb">${esc(linked ? "Connected. Loading the captions…" : "It opens minimised. You stay right here.")}</p>
        <ol class="wsteps">
          <li data-n="1"><i><u></u></i><span>Allow it in the browser prompt</span></li>
          <li data-n="2"><i><u></u></i><span>App starting up</span></li>
          <li data-n="3"><i><u></u></i><span>Connected</span></li>
        </ol>
        ${linked ? "" : full ? `<div class="whint">
            <span class="mock" aria-hidden="true">
              <span class="mk-t"></span><span class="mk-u"></span>
              <span class="mk-row"><span class="mk-box"><svg width="10" height="10" viewBox="0 0 16 16"><path d="M3.6 8.4l2.9 2.9 5.9-6.6" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span class="mk-l">Always allow</span></span>
              <span class="mk-btns"><span class="mk-open">Open</span><span class="mk-no">Cancel</span></span>
              <span class="mk-cur"><svg width="14" height="14" viewBox="0 0 16 16"><path d="M3 1.8l9.6 6.3-4.3.7 2.4 4.6-1.7.9-2.4-4.7-3.1 3z" fill="#fff" stroke="#111" stroke-width="1" stroke-linejoin="round"/></svg></span>
            </span>
            <p>Your browser asks once. Tick Always allow, then Open, and you won't see it again.</p>
          </div>` : `<p class="whint1">Press Open in the browser prompt.</p>`}
        ${linked ? "" : `<span class="wtime" aria-hidden="true"><i></i></span>`}
      </div>`;
  }

  function stopWake() {
    clearInterval(wakeTimer); wakeTimer = 0;
    clearInterval(wakeStepTimer); wakeStepTimer = 0;
  }

  function startWakeSteps() {
    clearInterval(wakeStepTimer);
    wakeStepTimer = setInterval(() => {
      if (phase !== "waking") { clearInterval(wakeStepTimer); wakeStepTimer = 0; return; }
      const el = root && $(root, ".wake");
      if (!el) { clearInterval(wakeStepTimer); wakeStepTimer = 0; return; }
      const step = wakeBack || (performance.now() - wakeT0) / 1000 > 6 ? 2 : 1;
      if (el.getAttribute("data-step") !== String(step)) el.setAttribute("data-step", String(step));
    }, 100);
  }

  function wake() {
    if (!vid) return;
    const a = document.createElement("a");
    a.href = "youtubehunter://wake";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    a.remove();
    wakeCount++;
    try { chrome.storage.local.set({ "yth.wakes": wakeCount }); } catch (_) {}
    wakeT0 = performance.now(); wakeBlur = false; wakeBack = false;
    phase = "waking"; message = "";
    render();
    const started = Date.now();
    clearInterval(wakeTimer);
    wakeTimer = setInterval(async () => {
      if (phase !== "waking" || !isOpen()) { stopWake(); return; }
      const p = await call("/v1/ping", { timeout: 900 });
      if (phase !== "waking") { stopWake(); return; }
      if (p.reachable && p.data && p.data.ok) {
        stopWake();
        phase = "linked";
        render();
        setTimeout(() => { if (phase === "linked" && isOpen()) load(""); }, 850);
      } else if (Date.now() - started > 30000) {
        stopWake();
        phase = "offline";
        render();
      }
    }, 1000);
  }

  // ── rendering ─────────────────────────────────────────────────────────
  function render() {
    if (!root) return;
    const body = $(root, ".body");
    const cnt = $(root, ".cnt"), sel = $(root, ".sel"), ft = $(root, ".ft"), bar = $(root, ".bar");
    const ready = phase === "ready";
    ft.style.display = ready ? "" : "none";
    bar.style.display = ready || (data && data.tracks && data.tracks.length > 1) ? "" : "none";
    cnt.hidden = !ready;
    if (ready) cnt.textContent = data.cues.length + " lines" + (data.lang ? " · " + data.lang.toUpperCase() : "");

    const tracks = (data && data.tracks) || [];
    sel.hidden = tracks.length < 2;
    if (tracks.length > 1) {
      sel.innerHTML = tracks.map((t) => `<option value="${esc(t.key)}"${t.key === (data && data.key) ? " selected" : ""}>${esc(t.label)}</option>`).join("");
    }

    const sec = $(root, ".p");
    sec.setAttribute("data-phase", phase);
    if (phase !== "loading") stopStatus();

    if (phase === "loading") {
      const n = compactNow() ? 5 : 9;
      const W = [[74, 0], [90, 46], [66, 0], [82, 54], [60, 0], [94, 40], [72, 0], [86, 46], [64, 0]];
      const rows = Array.from({ length: n }, (_, i) =>
        `<div class="skr" style="--i:${i}"><span class="sh c"></span><span class="ls"><span class="sh" style="width:${W[i][0]}%"></span>${
          W[i][1] ? `<span class="sh" style="width:${W[i][1]}%"></span>` : ""}</span></div>`).join("");
      body.innerHTML = `<div class="ld"><div class="skw" aria-hidden="true">${rows}</div>` +
        `<div class="stat" role="status" aria-live="polite"><span class="dot"></span><span class="tx">${STAGES[0][1]}</span><span class="el"></span></div></div>`;
      startStatus();
    } else if (phase === "waking" || phase === "linked") {
      body.innerHTML = wakeHTML(phase === "linked");
      if (phase === "waking") startWakeSteps();
    } else if (phase === "offline") {
      body.innerHTML = `<div class="msg bad"><span class="ico">${ICON_OFF}</span><b>YouTube Hunter didn't start</b><span>Nothing answered on this PC. If the app isn't installed, install it, then try again.</span><button class="btn pri" type="button" data-act="retry">Try again</button></div>`;
    } else if (phase === "error") {
      body.innerHTML = `<div class="msg"><span class="ico">${ICON_NOCC}</span><b>No subtitles</b><span>${esc(message)}</span><button class="btn" type="button" data-act="retry">Try again</button></div>`;
    } else {
      body.innerHTML = `<div class="list${prefs.stamps ? "" : " nostamp"}" tabindex="0"></div><button class="now" type="button" data-act="now" hidden>${ICON_DOWN}<span>Jump to now</span></button>`;
      const list = $(root, ".list");
      list.addEventListener("wheel", markUserScroll, { passive: true });
      list.addEventListener("touchmove", markUserScroll, { passive: true });
      list.addEventListener("pointerdown", (e) => { if (e.target === list) markUserScroll(); });
      list.addEventListener("scroll", () => { updateNowBtn(); }, { passive: true });
      paintList(false);
    }
    applySize();
  }

  // ── loading status: stages the wait so it never feels stuck ─────────
  const STAGES = [[0, "Connecting to YouTube Hunter…"], [900, "Reading the captions…"], [3500, "Aligning timestamps…"],
    [12000, "Long video — still working…"], [40000, "Taking longer than usual…"]];
  function stopStatus() { clearInterval(stageTimer); stageTimer = 0; }
  function startStatus() {
    stopStatus();
    loadAt = Date.now();
    let shown = -1;
    const paint = () => {
      const tx = root && $(root, ".stat .tx"), el = root && $(root, ".stat .el");
      if (!tx) return;
      const ms = Date.now() - loadAt;
      let k = 0;
      STAGES.forEach((st, i) => { if (ms >= st[0]) k = i; });
      if (k !== shown) {
        const first = shown < 0; shown = k;
        if (first) tx.textContent = STAGES[k][1];
        else { tx.classList.add("fade"); setTimeout(() => { tx.textContent = STAGES[k][1]; tx.classList.remove("fade"); }, 160); }
      }
      if (el) el.textContent = ms >= 4000 ? stamp(ms / 1000) : "";
    };
    paint();
    stageTimer = setInterval(paint, 400);
  }

  function hl(text) {
    const t = esc(text);
    if (!query) return t;
    const q = esc(query).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    try { return t.replace(new RegExp(q, "gi"), (m) => `<mark>${m}</mark>`); } catch (_) { return t; }
  }

  function paintList(keepScroll) {
    const list = root && $(root, ".list");
    if (!list || !data) return;
    const q = query.toLowerCase();
    const prev = list.scrollTop;
    let out = "";
    const stag = reveal && prefs.stamps && !q;
    reveal = false;
    let k = 0;
    data.cues.forEach((c, i) => {
      if (q && !c[2].toLowerCase().includes(q)) return;
      const anim = stag && k < 14 ? ` in" style="--i:${k++}` : "";
      out += `<div class="row${anim}" data-i="${i}" data-s="${c[0]}" role="button" tabindex="-1"><span class="t">${stamp(c[0], hours)}</span><span class="w" dir="auto">${hl(c[2])}</span></div>`;
    });
    if (!out) out = `<div class="empty">${q ? "Nothing matches “" + esc(query) + "”." : "No lines."}</div>`;
    list.innerHTML = prefsWrap(out);
    list.classList.toggle("nostamp", !prefs.stamps);
    if (keepScroll) list.scrollTop = prev;
    const a = active; active = -1;
    setActive(a, false);
  }
  // plain-text mode flows the lines as one paragraph
  function prefsWrap(html) { return prefs.stamps ? html : `<div class="flow">${html}</div>`; }

  function setActive(i, scroll) {
    const list = root && $(root, ".list");
    if (!list) { active = i; return; }
    if (i !== active) {
      const old = list.querySelector(".row.on");
      if (old) old.classList.remove("on");
      active = i;
    }
    if (i < 0) { updateNowBtn(); return; }
    const row = list.querySelector(`.row[data-i="${i}"]`);
    if (!row) return;
    row.classList.add("on");
    if (scroll && prefs.follow && Date.now() - userScrollAt > 3500) {
      const top = row.offsetTop - list.clientHeight / 2 + row.offsetHeight / 2;
      list.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    }
    updateNowBtn();
  }

  function updateNowBtn() {
    const list = root && $(root, ".list"), b = root && $(root, ".now");
    if (!list || !b) return;
    const row = active >= 0 ? list.querySelector(`.row[data-i="${active}"]`) : null;
    if (!row) { b.hidden = true; return; }
    const r = row.getBoundingClientRect(), l = list.getBoundingClientRect();
    b.hidden = !(r.bottom < l.top + 4 || r.top > l.bottom - 4);
  }
  const markUserScroll = () => { userScrollAt = Date.now(); };

  // ── following the video ───────────────────────────────────────────────
  function cueAt(t) {
    const cues = data.cues;
    let lo = 0, hi = cues.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (cues[mid][0] <= t + 0.05) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (ans >= 0 && t > cues[ans][1] + 2.5) return -1;   // long silence: nothing is being said
    return ans;
  }
  function syncToVideo(force) {
    if (phase !== "ready" || !data) return;
    const v = player();
    if (!v || adShowing()) return;
    const i = cueAt(v.currentTime || 0);
    if (i !== active || force) setActive(i, true);
  }
  function startTick() {
    clearInterval(tick);
    tick = setInterval(() => {
      if (!isOpen() || document.visibilityState !== "visible") return;
      // the page moved to another video (YouTube is a single-page app)
      const v = kit() && kit().currentVideo();
      if (v && vid && v.id !== vid.id) { vid = v; data = null; load(""); return; }
      syncToVideo(false);
    }, 250);
  }

  function seek(s) {
    const v = player();
    if (!v) return;
    v.currentTime = Math.max(0, s);
    userScrollAt = 0;
    if (v.paused) { try { v.play(); } catch (_) {} }
  }

  // ── actions ───────────────────────────────────────────────────────────
  function visibleCues() {
    if (!data) return [];
    if (!query) return data.cues;
    const q = query.toLowerCase();
    return data.cues.filter((c) => c[2].toLowerCase().includes(q));
  }
  function fname(ext) {
    const base = pageTitle().replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 80) || "video";
    return `${base} [${vid.id}]${data && data.lang ? "." + data.lang : ""}.${ext}`;
  }
  function save(text, ext, mime) {
    const blob = new Blob(["﻿" + text], { type: mime + ";charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = fname(ext);
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
    try {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.cssText = "position:fixed;opacity:0;top:0";
      document.body.appendChild(ta); ta.select();
      const ok = document.execCommand("copy"); ta.remove(); return ok;
    } catch (_) { return false; }
  }
  function flash(btn, label) {
    const s = btn.querySelector("span"), old = s.textContent;
    btn.classList.add("ok"); s.textContent = label;
    setTimeout(() => { btn.classList.remove("ok"); s.textContent = old; }, 1400);
  }

  async function onClick(e) {
    const sw = e.target.closest("[data-sw]");
    if (sw) {
      prefs[sw.dataset.sw] = !prefs[sw.dataset.sw];
      savePrefs(); syncToggles();
      if (sw.dataset.sw === "stamps") paintList(true);
      if (sw.dataset.sw === "follow" && prefs.follow) { userScrollAt = 0; setActive(active, true); }
      return;
    }
    const row = e.target.closest(".row");
    if (row) {
      if (window.getSelection && String(window.getSelection()).length) return;   // selecting text, not seeking
      seek(parseFloat(row.dataset.s));
      return;
    }
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "retry") { if (phase === "offline") wake(); else load(data && data.key || ""); return; }
    if (act === "now") { userScrollAt = 0; setActive(active, true); return; }
    if (!data) return;
    const cues = visibleCues();
    if (!cues.length) return;
    if (act === "copy") {
      const ok = await copy(prefs.stamps ? toTimedText(cues) : toPlain(cues));
      flash(b, ok ? "Copied" : "Couldn't copy");
    } else if (act === "txt") {
      save(prefs.stamps ? toTimedText(cues) : toPlain(cues), "txt", "text/plain");
      flash(b, "Saved");
    } else if (act === "srt") {
      save(toSRT(cues), "srt", "application/x-subrip");
      flash(b, "Saved");
    }
  }

  window.addEventListener("yt-navigate-finish", () => {
    if (!isOpen()) return;
    const v = kit() && kit().currentVideo();
    if (!v) { close(); return; }
    if (!vid || v.id !== vid.id) { vid = v; data = null; load(""); }
  });

  window.__ythSubs = { toggle, open, close, isOpen, retheme };
})();
