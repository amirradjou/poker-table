// Play in the demo: the visitor sits down with the scripted bots, and the engine runs in their
// own browser. Python (Pyodide) runs poker_table's own wheel in a worker (play-worker.js). There
// is no server and no key, nothing is sent anywhere, and none of it loads before Play is pressed.
//
// The table is drawn by the viewer a live table uses (app.js). Once a table is open,
// POKER_TABLE_PLAY.on sends the viewer's requests here instead of to a server: its GETs and
// POSTs go to the table (web/demo_play.py answers them as web/app.py would), and its event
// stream carries what the table plays.
//
// A page cannot wait inside Python for a click, so the table is driven a step at a time. Each
// step plays one thing, a deal or one bot's decision, and the table stops at your turn and at
// the end of each hand. This page waits between steps, as a live table's pace does.
(() => {
  const WHEEL = window.POKER_TABLE_PLAY_WHEEL;  // {url, bytes}: written by `serve --export-play`
  if (!WHEEL) return;

  // Pyodide 314.0.7 (Python 3.14.2), released 2026-09-14. Its files, measured in Chromium by
  // tests/test_browser.py: the bytes sent over the network, and what they unpack to in the
  // worker's fetch() (pyodide.asm.mjs is imported, not fetched, so only the first counts it).
  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
  const PYODIDE_SENT = 6_358_872;
  const PYODIDE_UNPACKED = 12_378_418;
  const DOWNLOAD = PYODIDE_SENT + WHEEL.bytes;
  const BEAT = 3000;  // ms to look at a hand's result before the next one is dealt
  // The bots to pick from, in the order they sit down (demo_play.BOTS, written by the export).
  const { names: BOTS, default: DEFAULT_BOTS } = window.POKER_TABLE_PLAY_BOTS;
  const megabytes = (bytes) => `${(bytes / 1e6).toFixed(1)} MB`;

  // ---- the Play button and the bar under the header ----
  const button = document.createElement("button");
  button.type = "button";
  button.id = "play-open";
  button.className = "play-open";
  button.textContent = `Play the bots · ${megabytes(DOWNLOAD)}`;
  button.title = `Sit down with the bots. Python and the table (${megabytes(DOWNLOAD)}) download into this browser and run here.`;
  document.getElementById("file").after(button);

  const bar = document.createElement("section");
  bar.id = "play-bar";
  bar.className = "play-bar hidden";
  bar.setAttribute("aria-label", "Play the bots");
  bar.innerHTML = `
    <div class="play-setup" id="play-setup">
      <strong>Sit down with the bots</strong>
      <span class="play-bots" role="group" aria-label="How many bots">Bots
        ${BOTS.map((_, i) => `<button type="button" data-bots="${i + 1}" aria-pressed="${i + 1 === DEFAULT_BOTS}" title="The first ${i + 1}">${i + 1}</button>`).join("")}
      </span>
      <span class="play-pick" role="group" aria-label="Which bots">
        ${BOTS.map((name, i) => `<button type="button" data-bot="${name}" aria-pressed="${i < DEFAULT_BOTS}">${name}</button>`).join("")}
      </span>
      <button type="button" class="primary" id="play-deal">Deal me in</button>
      <button type="button" id="play-later">Not now</button>
      <span class="play-status" id="play-status" aria-live="polite"></span>
      <span class="play-note">Python and the table run in this browser, against the scripted bots: no server, no key, nothing sent.</span>
    </div>
    <div class="play-seated hidden" id="play-seated">
      <span id="play-who"></span>
      <span class="play-same">Seed <b id="play-seed"></b>: <code id="play-command"></code> deals the same hands.</span>
      <button type="button" id="play-leave">Leave the table</button>
    </div>
    <p class="play-error hidden" id="play-error" role="alert"></p>
    <div class="play-progress hidden" id="play-progress"><i></i></div>`;
  document.body.insertBefore(bar, document.querySelector("main"));
  const $p = (id) => document.getElementById(id);

  // Who sits down: a number picks the first that many, and each name turns one bot on or off.
  // They sit in the order of BOTS whatever the order of the clicks, so the first N are bots=N.
  let bots = new Set(BOTS.slice(0, DEFAULT_BOTS));
  function showPicked() {
    const picked = BOTS.filter((name) => bots.has(name));
    const prefix = picked.every((name, i) => name === BOTS[i]) ? picked.length : 0;
    for (const b of bar.querySelectorAll("[data-bots]")) b.setAttribute("aria-pressed", +b.dataset.bots === prefix);
    for (const b of bar.querySelectorAll("[data-bot]")) b.setAttribute("aria-pressed", bots.has(b.dataset.bot));
  }
  for (const b of bar.querySelectorAll("[data-bots]")) {
    b.onclick = () => { bots = new Set(BOTS.slice(0, +b.dataset.bots)); showPicked(); };
  }
  for (const b of bar.querySelectorAll("[data-bot]")) {
    b.onclick = () => {
      const name = b.dataset.bot;
      if (!bots.has(name)) bots.add(name);
      else if (bots.size > 1) bots.delete(name);  // somebody has to sit across from you
      showPicked();
    };
  }
  button.onclick = () => {
    bar.classList.remove("hidden");
    button.setAttribute("aria-pressed", "true");
    load().catch(() => {});  // the download starts while the visitor picks; fail() reports it
  };
  $p("play-later").onclick = () => { bar.classList.add("hidden"); button.setAttribute("aria-pressed", "false"); };
  $p("play-deal").onclick = () => sitDown();
  $p("play-leave").onclick = () => { location.href = location.pathname + location.search; };

  function showProgress({ progress, stage }) {
    if (stage) showProgress.stage = stage;
    if (progress !== undefined) showProgress.at = progress;
    const at = showProgress.at || 0;
    $p("play-progress").classList.toggle("hidden", at >= 1);
    $p("play-progress").style.setProperty("--p", `${Math.round(at * 100)}%`);
    $p("play-status").textContent = at >= 1 ? showProgress.stage : `${showProgress.stage}, ${Math.round(at * 100)}%`;
  }
  function fail(message) {
    const el = $p("play-error");
    el.textContent = `The table stopped: ${message}. The replays still work.`;
    el.classList.remove("hidden");
    $p("play-deal").disabled = false;
    $p("play-deal").textContent = "Try again";
  }

  // ---- the worker ----
  let worker = null, loading = null, lastId = 0;
  const waiting = new Map();
  function call(message) {
    return new Promise((resolve, reject) => {
      const id = ++lastId;
      waiting.set(id, { resolve, reject });
      worker.postMessage({ ...message, id });
    });
  }
  function load() {
    if (loading) return loading;
    $p("play-error").classList.add("hidden");
    if (!worker) {
      worker = new Worker("static/play-worker.js", { type: "module" });
      worker.onmessage = ({ data }) => {
        if (data.id === undefined) { showProgress(data); return; }
        const pending = waiting.get(data.id);
        waiting.delete(data.id);
        if (data.error !== undefined) pending.reject(new Error(data.error));
        else pending.resolve(data.result);
      };
      worker.onerror = (e) => {
        for (const pending of waiting.values()) pending.reject(new Error(e.message || "the worker stopped"));
        waiting.clear();
      };
    }
    showProgress({ progress: 0, stage: "Downloading Python" });
    loading = call({
      op: "load",
      pyodide: PYODIDE,
      wheel: new URL(WHEEL.url, location.href).href,
      expect: PYODIDE_UNPACKED + WHEEL.bytes,
    });
    loading.catch((exc) => { loading = null; fail(exc.message); });
    return loading;
  }

  // ---- sitting down ----
  async function sitDown() {
    const deal = $p("play-deal");
    deal.disabled = true;
    deal.textContent = "Dealing…";
    try {
      await load();
      // A new deal each visit; ?seed=N in the address deals that seed's hands instead.
      const wanted = Number.parseInt(new URLSearchParams(location.search).get("seed"), 10);
      const seed = wanted >= 0 ? wanted : crypto.getRandomValues(new Uint32Array(1))[0] % 99999 + 1;
      const about = await call({ op: "open", table: { bots: BOTS.filter((name) => bots.has(name)), seed } });
      hook.on = true;
      $p("play-setup").classList.add("hidden");
      button.classList.add("hidden");
      $p("play-who").textContent = `You and ${about.seats.length - 1} bot${about.seats.length === 2 ? "" : "s"}: ${about.seats.slice(1).join(", ")}.`;
      $p("play-seed").textContent = about.seed;
      $p("play-command").textContent = about.command;
      $p("play-seated").classList.remove("hidden");
      // The viewer starts over on the new table: its hands, its leaderboard, its live stream.
      setTab("hands");
      $("filter").value = ""; $("winner").value = "";
      await loadSession();
      await loadList(true);
      await connectLive();
    } catch (exc) {
      fail(exc.message);
    }
  }

  // ---- what app.js talks to while the table is open ----
  class Stream {  // the part of EventSource that app.js uses
    constructor() { this.onmessage = null; this.closed = false; }
    close() { this.closed = true; }
    send(event) { if (!this.closed && this.onmessage) this.onmessage({ data: JSON.stringify(event) }); }
  }
  let stream = null, pace = 0.8, stepping = false;
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  function deliver(events) { for (const event of events || []) stream.send(event); }

  // Step until the table waits for you or is over, pausing as a live table would: the pace
  // before each bot's decision, a beat on each hand's result.
  async function go(next, wait = true) {
    if (stepping) return;
    stepping = true;
    try {
      while (stream && !stream.closed && (next === "bot" || next === "deal")) {
        if (wait) await sleep(next === "deal" ? BEAT : pace * 1000);
        wait = true;
        const out = await call({ op: "step" });
        deliver(out.events);
        next = out.next;
      }
    } catch (exc) {
      fail(exc.message);
    } finally {
      stepping = false;
    }
  }

  const hook = window.POKER_TABLE_PLAY = {
    on: false,
    load,
    call,  // for tests, and for anyone curious at the console
    async get(path) {
      const out = await call({ op: "get", path });
      if (out.status !== 200) throw new Error(`${path}: ${out.status}`);
      return out.body;
    },
    async post(path, body) {
      const out = await call({ op: "post", path, body });
      if (out.status === 200 && path === "/api/live/pace") pace = out.body.pace;
      // What your action set off is shown after app.js has closed your turn.
      if (out.events) setTimeout(() => { deliver(out.events); go(out.next); }, 0);
      return new Response(JSON.stringify(out.body), { status: out.status, headers: { "Content-Type": "application/json" } });
    },
    events() {
      stream = new Stream();
      setTimeout(async () => {  // once app.js has set onmessage, as an EventSource opens
        try {
          const hello = await call({ op: "hello" });
          pace = hello.body.pace;
          stream.send(hello.body);
          go("deal", false);
        } catch (exc) {
          fail(exc.message);
        }
      }, 0);
      return stream;
    },
  };
})();
