const $ = (id) => document.getElementById(id);
const SUITS = { c: "♣", d: "♦", h: "♥", s: "♠" };
const STAT_HELP = {
  "bb/100": "big blinds won per 100 hands",
  vpip: "voluntarily put money in preflop",
  pfr: "raised preflop",
  "3bet": "re-raised when facing one raise",
  f3b: "folded the open to a 3-bet",
  af: "postflop (bets + raises) / calls",
  wtsd: "went to showdown after seeing the flop",
  "w$sd": "won money at showdown",
  bluff: "postflop bets/raises with no pair and no draw",
  illegal: "decisions the table had to replace",
  ms: "average decision time",
  "$/hand": "estimated model cost per hand",
};
const PERCENT = new Set(["vpip", "pfr", "3bet", "f3b", "wtsd", "w$sd", "bluff", "illegal"]);
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const EASE = "cubic-bezier(.2,.8,.2,1)";
// Who sits where: a glyph and a hue per kind. LLM seats share one look (a gold ring marks them).
const KINDS = {
  tag: { glyph: "♠", color: "#3b5f8a", label: "bot · tag" },
  rock: { glyph: "■", color: "#6b6a63", label: "bot · rock" },
  maniac: { glyph: "⚡", color: "#b3261e", label: "bot · maniac" },
  station: { glyph: "●", color: "#7a8f3a", label: "bot · station" },
  random: { glyph: "⚄", color: "#8a5fb5", label: "bot · random" },
  human: { glyph: "☺", color: "#d4a72c", label: "human" },
  laya: { glyph: "◈", color: "#2a6f8f", label: "laya", model: true },
};
const DENOMS = [[100, "black"], [25, "green"], [5, "red"], [1, "white"]];

let listOffset = 0, listTotal = 0;
let hand = null, step = 0, showThink = true, showCards = true;
let live = null, follow = true, autoTimer = null, turn = null;
let liveHand = null, liveActing = null, tickerTimer = null;
const bubbles = {};      // seat -> { text, until }
const lastDecision = {}; // seat -> { latency_ms, cost_usd } shown after an LLM seat decides
const modelOf = {};      // seat -> model name seen in a decision's meta
let prev = { handId: null, street: null, boardLen: 0, done: false };

function kindInfo(kind) {
  if (!kind) return { glyph: "", color: "#8f8b80", label: "" };
  if (kind.startsWith("llm:")) return { glyph: "✦", color: "#1f7a4d", label: kind.slice(4), llm: true };
  return KINDS[kind] || { glyph: "", color: "#8f8b80", label: kind };
}
function initials(name) {
  const letters = String(name).replace(/[^\p{L}\p{N}]/gu, "");
  return (letters.slice(0, 2) || "?").toUpperCase();
}
function cardEl(text, small) {
  const el = document.createElement("span");
  if (text === null) { el.className = "card back" + (small ? " small" : ""); return el; }
  const rank = text[0] === "T" ? "10" : text[0], suit = text[1];
  el.className = "card" + ((suit === "h" || suit === "d") ? " red" : "") + (small ? " small" : "");
  el.innerHTML = `<span>${rank}</span><span class="suit">${SUITS[suit]}</span>`;
  return el;
}
function avatarEl(p) {
  const info = kindInfo(p.kind);
  const el = document.createElement("span");
  el.className = "avatar" + (info.llm ? " llm" : "") + (info.model ? " model" : "") + (p.kind === "human" ? " human" : "");
  el.style.setProperty("--c", info.color);
  el.innerHTML = `<span class="initials">${esc(initials(p.name))}</span>` + (info.glyph ? `<span class="glyph">${info.glyph}</span>` : "");
  return el;
}
// A stack of chip discs for an amount: classic 1 / 5 / 25 / 100 colours, at most six discs.
function chipStackEl(amount) {
  const el = document.createElement("span"); el.className = "chipstack";
  let rest = amount; const discs = [];
  for (const [value, color] of DENOMS) {
    const n = Math.floor(rest / value); rest -= n * value;
    for (let i = 0; i < n && discs.length < 6; i++) discs.push(color);
  }
  if (!discs.length) discs.push("white");
  discs.forEach((color, i) => { const c = document.createElement("i"); c.className = "chip " + color; c.style.bottom = (i * 3) + "px"; el.appendChild(c); });
  const label = document.createElement("b"); label.textContent = amount; el.appendChild(label);
  return el;
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function loadSession() {
  const s = await api("/api/session");
  $("file").textContent = s.hands ? `${s.hands} hands from ${s.file}` : `${s.file} is empty`;
  return s;
}

async function loadList(reset) {
  if (reset) { listOffset = 0; $("hand-list").innerHTML = ""; }
  const data = await api(`/api/hands?offset=${listOffset}&limit=100`);
  listTotal = data.total;
  for (const h of data.hands) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.dataset.id = h.hand_id;
    const net = h.players.filter(p => p.net > 0).map(p => `${esc(p.name)} +${p.net}`).join(", ");
    b.innerHTML = `<span class="id">#${esc(h.hand_id)}</span><span class="who">${net || "no winner"}</span><span class="pot">${h.pot}</span>`;
    b.addEventListener("click", () => openHand(h.hand_id));
    li.appendChild(b);
    $("hand-list").appendChild(li);
  }
  listOffset += data.hands.length;
  $("more").classList.toggle("hidden", listOffset >= listTotal);
}

async function openHand(id, { autoplay = false, at = "start" } = {}) {
  stopAuto();
  hand = await api(`/api/hands/${id}`);
  hand.streaming = false;
  step = at === "end" ? hand.steps.length : 0;
  $("action-bar").classList.add("hidden");
  $("summary").classList.remove("hidden");
  for (const b of $("hand-list").querySelectorAll("button[data-id]")) b.setAttribute("aria-current", b.dataset.id === id);
  $("empty").classList.add("hidden");
  $("replay").classList.remove("hidden");
  $("hand-title").textContent = `Hand #${hand.hand_id}`;
  $("hand-sub").textContent = `blinds ${hand.small_blind}/${hand.big_blind}, seed ${hand.seed}`;
  $("scrub").max = hand.steps.length;
  buildLog();
  buildSummary();
  render();
  history.replaceState(null, "", `#${id}`);
  if (autoplay) startAuto();
}

// Table state after the first `n` steps: stacks, bets, folds, board, pot.
function stateAt(n) {
  const seats = hand.players.map(p => ({ ...p, stack: p.stack, bet: 0, folded: false, allIn: false, won: 0 }));
  let board = [], pot = 0, street = "preflop", acting = null, showdown = false;
  const paid = (s, amt) => { s.stack -= amt; s.bet += amt; pot += amt; };
  for (let i = 0; i < n; i++) {
    const e = hand.steps[i];
    switch (e.kind) {
      case "post_blind": paid(seats[e.seat], e.amount); if (e.all_in) seats[e.seat].allIn = true; break;
      case "action": {
        const s = seats[e.seat];
        if (e.action === "fold") s.folded = true;
        paid(s, e.amount);
        if (e.all_in) s.allIn = true;
        break;
      }
      case "street": board = board.concat(e.cards); street = e.street; seats.forEach(s => s.bet = 0); break;
      case "return_uncalled": seats[e.seat].stack += e.amount; pot -= e.amount; seats[e.seat].bet = Math.max(0, seats[e.seat].bet - e.amount); break;
      case "showdown": showdown = true; street = "showdown"; break;
      case "win": seats[e.seat].stack += e.amount; seats[e.seat].won += e.amount; seats[e.seat].allIn = false; pot -= e.amount; break;
    }
  }
  // Who acts next: the seat of the next action step (replay) or whoever the table says (live).
  const nxt = hand.steps.slice(n).find(e => e.kind === "action");
  if (nxt) acting = nxt.seat;
  if (hand.streaming && n >= hand.steps.length) acting = liveActing ? liveActing.seat : (turn ? turn.seat : null);
  if (hand.drill && n >= hand.steps.length) acting = hand.seat;
  const done = n >= hand.steps.length && !hand.streaming && !hand.drill;
  return { seats, board, pot, street, acting, showdown, done };
}

function rectIn(el, box) {
  const r = el.getBoundingClientRect(), t = box.getBoundingClientRect();
  return { x: r.left - t.left, y: r.top - t.top, w: r.width, h: r.height };
}

function render() {
  const st = stateAt(step);
  const table = $("table");
  const sameHand = prev.handId === hand.hand_id;
  // Remember where chips were, so they can travel when the state moves on.
  const oldBets = {};
  for (const el of table.querySelectorAll(".seat")) {
    const b = el.querySelector(".bet .chipstack");
    if (b) oldBets[el.dataset.seat] = rectIn(b, table);
  }
  const oldPotEl = $("pot").querySelector(".chipstack");
  const oldPot = oldPotEl ? rectIn(oldPotEl, table) : null;

  $("scrub").value = step;
  $("street").textContent = st.done ? "hand over" : st.street;
  // board
  const board = $("board"); board.innerHTML = "";
  st.board.forEach(c => board.appendChild(cardEl(c)));
  $("pot").innerHTML = "";
  if (st.pot) { $("pot").appendChild(chipStackEl(st.pot)); const t = document.createElement("span"); t.className = "pot-label"; t.textContent = `pot ${st.pot}`; $("pot").appendChild(t); }
  // seats around the oval (ghost chip flights remove themselves when they land)
  for (const el of table.querySelectorAll(".seat")) el.remove();
  const n = st.seats.length;
  const now = Date.now();
  st.seats.forEach((s, i) => {
    const el = document.createElement("div");
    const angle = Math.PI / 2 + (2 * Math.PI * i) / n;  // seat 0 at the bottom, clockwise
    const x = 50 + 43 * Math.cos(angle), y = 50 + 33 * Math.sin(angle);
    const acting = st.acting === i && !st.done;
    el.className = "seat" + (y < 50 ? " top" : " bottom") + (s.folded ? " folded" : "")
      + (acting ? " acting" : "") + (s.won ? " winner" : "");
    el.dataset.seat = i;
    el.style.left = x + "%"; el.style.top = y + "%";
    const info = kindInfo(s.kind);
    const who = document.createElement("div"); who.className = "who";
    who.appendChild(avatarEl(s));
    const named = (info.llm || info.model) && modelOf[i];
  const label = named ? `${info.label} · ${modelOf[i].replace(/^claude-|^laya:/, "")}` : info.label;
    who.innerHTML += `<div class="id"><div class="name">${esc(s.name)}</div><div class="pos">${s.position}${label ? " · " + esc(label) : ""}</div></div>`;
    el.appendChild(who);
    const reveal = showCards || st.showdown || (st.done && s.won);
    const cards = document.createElement("div"); cards.className = "cards";
    const hole = s.hole.length ? s.hole : [null, null];  // imported hands may not know them
    if (!s.folded || showCards) hole.forEach(c => cards.appendChild(cardEl(reveal ? c : null, true)));
    el.appendChild(cards);
    const stack = document.createElement("div"); stack.className = "stack";
    stack.textContent = s.allIn ? "all-in" : `${s.stack}`;
    el.appendChild(stack);
    if (acting && hand.streaming && !(turn && turn.seat === i)) {
      const th = document.createElement("div"); th.className = "thinking"; th.innerHTML = "<i></i><i></i><i></i>";
      el.appendChild(th);
      if (info.llm || info.model) { const tk = document.createElement("div"); tk.className = "ticker"; tk.dataset.since = liveActing ? liveActing.since : now; el.appendChild(tk); }
    } else if (lastDecision[i] && hand.streaming && (info.llm || info.model)) {
      const tk = document.createElement("div"); tk.className = "ticker settled";
      const d = lastDecision[i];
      tk.textContent = `${(d.latency_ms / 1000).toFixed(1)} s` + (d.cost_usd !== undefined ? ` · $${d.cost_usd.toFixed(4)}` : "");
      el.appendChild(tk);
    }
    if (s.bet) { const b = document.createElement("div"); b.className = "bet"; b.appendChild(chipStackEl(s.bet)); el.appendChild(b); }
    if (i === hand.button) { const d = document.createElement("div"); d.className = "button"; d.textContent = "D"; el.appendChild(d); }
    const bubble = bubbles[i];
    if (bubble && bubble.until > now) { const q = document.createElement("div"); q.className = "bubble"; q.textContent = `“${bubble.text}”`; el.appendChild(q); }
    table.appendChild(el);
  });
  // motion that answers what just happened
  if (!REDUCED) {
    const forward = sameHand && step === prev.step + 1;
    if (!sameHand && (step === 0 || hand.streaming)) dealCards(table);
    if (forward && st.street !== prev.street && Object.keys(oldBets).length) chipsToPot(table, oldBets);
    if (forward && st.board.length > prev.boardLen) flipIn(board, st.board.length - prev.boardLen);
    const justWon = st.seats.some(s => s.won) && !prev.won;
    if (forward && justWon && oldPot) potToWinners(table, oldPot, st.seats.filter(s => s.won).map(s => s.seat));
  }
  prev = { handId: hand.hand_id, step, street: st.street, boardLen: st.board.length, done: st.done, won: st.seats.some(s => s.won) };
  // log
  const items = $("log-list").children;
  for (let i = 0; i < items.length; i++) {
    items[i].classList.toggle("future", i >= step);
    items[i].classList.toggle("current", i === step - 1);
  }
  const log = $("log"), cur = items[step - 1];
  if (cur) {  // keep the current line visible inside the log without scrolling the page
    const top = cur.offsetTop - log.offsetTop, bottom = top + cur.offsetHeight;
    if (top < log.scrollTop) log.scrollTop = top;
    else if (bottom > log.scrollTop + log.clientHeight) log.scrollTop = bottom - log.clientHeight;
  } else log.scrollTop = 0;
  $("log").classList.toggle("no-think", !showThink);
}

// ---- motion ----
function dealCards(table) {
  const centre = { x: table.clientWidth / 2, y: table.clientHeight / 2 };
  let i = 0;
  for (const card of table.querySelectorAll(".seat .cards .card")) {
    const r = rectIn(card, table);
    const dx = centre.x - (r.x + r.w / 2), dy = centre.y - (r.y + r.h / 2);
    card.animate([{ transform: `translate(${dx}px, ${dy}px) scale(.5)`, opacity: 0 }, { transform: "none", opacity: 1 }],
      { duration: 380, delay: 60 * i++, easing: EASE, fill: "backwards" });
  }
}
function flipIn(board, count) {
  const cards = [...board.children].slice(-count);
  cards.forEach((c, i) => c.animate([{ transform: "rotateY(90deg) scale(.9)", opacity: .3 }, { transform: "none", opacity: 1 }],
    { duration: 280, delay: 110 * i, easing: EASE, fill: "backwards" }));
}
function ghostStack(table, rect, amount) {
  const g = document.createElement("div"); g.className = "ghost";
  g.style.left = rect.x + "px"; g.style.top = rect.y + "px";
  g.appendChild(chipStackEl(amount));
  table.appendChild(g);
  return g;
}
function chipsToPot(table, oldBets) {
  const potEl = $("pot").querySelector(".chipstack");
  if (!potEl) return;
  const target = rectIn(potEl, table);
  potEl.style.opacity = "0";
  const flights = Object.entries(oldBets).map(([seat, r]) => {
    const g = ghostStack(table, r, 0); g.querySelector("b").remove();
    return g.animate([{ transform: "none" }, { transform: `translate(${target.x - r.x}px, ${target.y - r.y}px)`, opacity: .9 }],
      { duration: 450, easing: EASE, fill: "forwards" }).finished.then(() => g.remove());
  });
  Promise.all(flights).then(() => { potEl.style.opacity = ""; });
}
function potToWinners(table, oldPot, winners) {
  for (const seat of winners) {
    const el = table.querySelector(`.seat[data-seat="${seat}"]`);
    if (!el) continue;
    const target = rectIn(el.querySelector(".stack"), table);
    const g = ghostStack(table, oldPot, 0); g.querySelector("b").remove();
    g.animate([{ transform: "none", opacity: 1 }, { transform: `translate(${target.x - oldPot.x}px, ${target.y - oldPot.y}px)`, opacity: .2 }],
      { duration: 600, easing: EASE, fill: "forwards" }).finished.then(() => {
        g.remove();
        el.animate([{ boxShadow: "0 0 0 0 rgba(212,167,44,.9)" }, { boxShadow: "0 0 0 18px rgba(212,167,44,0)" }], { duration: 900, easing: "ease-out" });
      });
  }
}
function bubble(seat, text) {
  bubbles[seat] = { text, until: Date.now() + 4000 };
  setTimeout(() => { if (bubbles[seat] && bubbles[seat].until <= Date.now() + 5) { delete bubbles[seat]; if (hand) render(); } }, 4100);
}
function startTicker() {
  if (tickerTimer) return;
  tickerTimer = setInterval(() => {
    for (const tk of document.querySelectorAll(".ticker:not(.settled)")) tk.textContent = `${((Date.now() - +tk.dataset.since) / 1000).toFixed(1)} s`;
  }, 200);
}

function buildLog() {
  const ol = $("log-list"); ol.innerHTML = "";
  for (const e of hand.steps) ol.appendChild(logItem(e));
}
function logItem(e) {
  const li = document.createElement("li");
  switch (e.kind) {
    case "post_blind":
      li.textContent = `${e.name} posts ${e.amount}${e.all_in ? " and is all-in" : ""}`; break;
    case "action": {
      li.innerHTML = `<span class="who">${esc(e.name)}</span> ${verb(e)}${e.all_in ? " and is all-in" : ""}`;
      if (e.table_talk) li.innerHTML += ` <span class="talk">“${esc(e.table_talk)}”</span>`;
      if (e.meta && e.meta.cost_usd !== undefined) li.innerHTML += `<span class="meta">${esc(e.meta.model)}, $${e.meta.cost_usd.toFixed(4)}</span>`;
      if (e.reasoning || e.illegal) {
        const t = document.createElement("span"); t.className = "think";
        t.innerHTML = (e.illegal ? `<span class="illegal">wanted “${esc(e.requested)}”, which was illegal.</span> ` : "") + esc(e.reasoning || "");
        li.appendChild(t);
      }
      break;
    }
    case "street": {
      li.className = "street";
      li.textContent = e.street;
      const c = document.createElement("span"); c.className = "cards";
      e.cards.forEach(x => c.appendChild(cardEl(x, true)));
      li.appendChild(c);
      break;
    }
    case "return_uncalled": li.textContent = `${e.amount} uncalled, back to ${e.name}`; break;
    case "showdown": {
      li.innerHTML = `<span class="who">${esc(e.name)}</span> shows `;
      const c = document.createElement("span"); c.className = "cards"; c.style.display = "inline-flex"; c.style.gap = "3px"; c.style.verticalAlign = "middle";
      e.cards.forEach(x => c.appendChild(cardEl(x, true)));
      li.appendChild(c);
      li.appendChild(document.createTextNode(` — ${e.text}`));
      break;
    }
    case "win": li.className = "win"; li.innerHTML = `<span class="who">${esc(e.name)}</span> wins ${e.amount} (${esc(e.text)})`; break;
    case "hand_end": li.className = "street"; li.textContent = "hand over"; break;
    default: li.textContent = e.kind;
  }
  return li;
}

function verb(e) {
  const [a, amt] = e.action.split(" ");
  if (a === "raise") return `raises to ${amt}`;
  if (a === "bet") return `bets ${amt}`;
  if (a === "call") return `calls${e.amount ? " " + e.amount : ""}`;
  return a + "s";
}

function buildSummary() {
  const rows = hand.players.map(p => {
    const cls = p.net > 0 ? "plus" : p.net < 0 ? "minus" : "";
    const sd = hand.showdown[p.seat] ? ` — ${hand.showdown[p.seat]}` : "";
    return `<tr><td>${esc(p.name)}</td><td>${p.position}</td><td>${p.hole.join(" ")}${sd}</td><td class="num ${cls}">${p.net > 0 ? "+" : ""}${p.net}</td></tr>`;
  }).join("");
  const pots = hand.pots.map((p, i) => `${i === 0 ? "main pot" : "side pot " + i} ${p.amount}`).join(", ");
  $("summary").innerHTML = `<table><tr><th>Seat</th><th></th><th>Cards</th><th class="num">Result</th></tr>${rows}</table><p>${pots}. Board ${hand.board.join(" ") || "never dealt"}.</p>`;
}

function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

const SVG = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs, parent) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}

// Cumulative chips per seat over the session: a multi-series line chart with a crosshair.
async function drawBankroll() {
  const data = await api("/api/bankroll");
  const root = $("bankroll");
  const n = data.hands.length;
  if (n < 2) { root.classList.add("hidden"); return; }
  root.classList.remove("hidden");
  const series = data.series.slice(0, 8);  // the palette has eight slots; more seats stay in the table
  $("bankroll-sub").textContent = `${n} hands, chips` + (data.series.length > 8 ? ` (first 8 seats)` : "");

  const svg = $("bankroll-svg"); svg.innerHTML = "";
  const W = svg.clientWidth || 800, H = 260, m = { top: 12, right: 74, bottom: 26, left: 52 };
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const all = series.flatMap(s => s.values);
  let lo = Math.min(0, ...all), hi = Math.max(0, ...all);
  if (lo === hi) hi = lo + 1;
  const x = i => m.left + (i / (n - 1)) * (W - m.left - m.right);
  const y = v => m.top + (1 - (v - lo) / (hi - lo)) * (H - m.top - m.bottom);

  // gridlines: a few round steps
  const grid = svgEl("g", { class: "grid" }, svg), axis = svgEl("g", { class: "axis" }, svg);
  const stepRaw = (hi - lo) / 4, mag = Math.pow(10, Math.floor(Math.log10(stepRaw)));
  const gstep = [1, 2, 5, 10].map(k => k * mag).find(k => k >= stepRaw) || mag;
  for (let v = Math.ceil(lo / gstep) * gstep; v <= hi; v += gstep) {
    svgEl("line", { x1: m.left, x2: W - m.right, y1: y(v), y2: y(v), class: v === 0 ? "zero" : "" }, grid);
    const t = svgEl("text", { x: m.left - 8, y: y(v) + 4, "text-anchor": "end" }, axis); t.textContent = v;
  }
  const ticks = [0, Math.round((n - 1) / 2), n - 1];
  for (const i of ticks) { const t = svgEl("text", { x: x(i), y: H - 8, "text-anchor": "middle" }, axis); t.textContent = `hand ${data.hands[i]}`; }

  // lines
  series.forEach((s, k) => {
    const d = s.values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    svgEl("path", { d, class: "line", stroke: `var(--series-${k + 1})` }, svg);
  });
  // direct end labels when they don't collide (else the legend + tooltip carry identity)
  if (series.length <= 4) {
    const ends = series.map((s, k) => ({ k, name: s.name, y: y(s.values[n - 1]) })).sort((a, b) => a.y - b.y);
    const collide = ends.some((e, i) => i && e.y - ends[i - 1].y < 14);
    if (!collide) for (const e of ends) { const t = svgEl("text", { x: W - m.right + 8, y: e.y + 4, class: "end-label" }, svg); t.textContent = e.name; }
  }

  // legend (always, for >= 2 series) - identity by swatch, text in ink
  const legend = $("bankroll-legend"); legend.innerHTML = "";
  series.forEach((s, k) => { const el = document.createElement("span"); el.style.setProperty("--c", `var(--series-${k + 1})`); el.textContent = s.name; legend.appendChild(el); });

  // hover: crosshair + one dot per series + tooltip
  const cross = svgEl("line", { class: "crosshair hidden", y1: m.top, y2: H - m.bottom }, svg);
  const dots = series.map((s, k) => svgEl("circle", { r: 4, class: "dot hidden", fill: `var(--series-${k + 1})` }, svg));
  const tip = $("bankroll-tip");
  const show = (evt) => {
    const rect = svg.getBoundingClientRect();
    const px = (evt.clientX - rect.left) * (W / rect.width);
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - m.left) / (W - m.left - m.right)) * (n - 1))));
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.classList.remove("hidden");
    dots.forEach((d, k) => { d.setAttribute("cx", x(i)); d.setAttribute("cy", y(series[k].values[i])); d.classList.remove("hidden"); });
    tip.innerHTML = `<b>hand ${data.hands[i]}</b>` + series.map((s, k) => `<div><i style="background:var(--series-${k + 1})"></i>${s.name} ${s.values[i] > 0 ? "+" : ""}${s.values[i]}</div>`).join("");
    tip.classList.remove("hidden");
    const left = (x(i) / W) * rect.width;
    tip.style.left = (left > rect.width / 2 ? left - tip.offsetWidth - 12 : left + 12) + "px";
  };
  svg.onpointermove = show;
  svg.onpointerleave = () => { cross.classList.add("hidden"); dots.forEach(d => d.classList.add("hidden")); tip.classList.add("hidden"); };

  // table view (the relief for low-contrast hues, and for screen readers)
  const head = "<tr><th>hand</th>" + data.series.map(s => `<th>${s.name}</th>`).join("") + "</tr>";
  const rows = data.hands.map((h, i) => `<tr><td>${h}</td>` + data.series.map(s => `<td>${s.values[i]}</td>`).join("") + "</tr>").join("");
  $("bankroll-table").innerHTML = `<table><thead>${head}</thead><tbody>${rows}</tbody></table>`;
}
$("bankroll-table-toggle").onclick = (e) => {
  const on = $("bankroll-table").classList.toggle("hidden") === false;
  e.target.setAttribute("aria-pressed", on);
  e.target.textContent = on ? "Show as chart" : "Show as table";
  $("bankroll-figure").classList.toggle("hidden", on);
  $("bankroll-legend").classList.toggle("hidden", on);
};

async function showBoard() {
  drawBankroll().catch(() => $("bankroll").classList.add("hidden"));
  const data = await api("/api/stats");
  $("board-title").textContent = `${data.hands} hands, sorted by chips won`;
  const cols = data.rows.length ? Object.keys(data.rows[0]) : [];
  const head = cols.map(c => `<th${STAT_HELP[c] ? ` title="${STAT_HELP[c]}"` : ""}>${c}</th>`).join("");
  const body = data.rows.map(r => "<tr>" + cols.map(c => {
    const v = r[c];
    let text = v === null || v === undefined ? "–" : PERCENT.has(c) ? `${Math.round(v * 100)}%`
      : c === "$/hand" ? `$${v.toFixed(4)}` : typeof v === "number" && !Number.isInteger(v) ? v.toFixed(1) : v;
    const cls = c === "net" ? (v > 0 ? "plus" : v < 0 ? "minus" : "") : "";
    return `<td class="${cls}">${text}</td>`;
  }).join("") + "</tr>").join("");
  $("board-table").innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
}

function startAuto() {
  if (!hand || autoTimer || hand.streaming) return;
  if (step >= hand.steps.length) step = 0;
  $("auto").setAttribute("aria-pressed", "true");
  autoTimer = setInterval(() => {
    if (step >= hand.steps.length) { stopAuto(); return; }
    go(step + 1);
  }, 650);
}
function stopAuto() {
  if (autoTimer) clearInterval(autoTimer);
  autoTimer = null;
  $("auto").setAttribute("aria-pressed", "false");
}

// ---- live sessions ----
function liveText(st) {
  if (st.error) return `live session failed: ${st.error}`;
  if (st.finished) return `session over, ${st.hands_played} hands`;
  if (st.turn) return "waiting for you";
  return `live: hand ${st.hands_played + 1} of ${st.hands_total}`;
}
function setLive(st) {
  live = st;
  const el = $("live");
  el.classList.remove("hidden");
  el.textContent = liveText(st);
  el.classList.toggle("over", !!st.finished);
  el.classList.toggle("turn", !!st.turn);
  $("follow").classList.remove("hidden");
  $("pace-wrap").classList.toggle("hidden", !!st.finished);
  if (st.pace !== undefined && document.activeElement !== $("pace")) { $("pace").value = st.pace; $("pace-value").textContent = `${(+st.pace).toFixed(1)} s`; }
  // with a human seated nobody else's cards are available, so the reveal toggle is moot
  $("toggle-cards").classList.toggle("hidden", st.spectator === false);
}
function watchingLive() { return hand && hand.streaming; }
function showLive() {
  if (!liveHand) return;
  stopAuto();
  hand = liveHand;
  step = liveHand.steps.length;
  $("empty").classList.add("hidden");
  $("replay").classList.remove("hidden");
  $("action-bar").classList.toggle("hidden", !turn);
  $("summary").classList.add("hidden");
  $("hand-title").textContent = `Hand #${liveHand.hand_id}`;
  $("hand-sub").textContent = turn ? `your turn on the ${turn.street}` : `live, blinds ${liveHand.small_blind}/${liveHand.big_blind}`;
  $("scrub").max = liveHand.steps.length;
  for (const b of $("hand-list").querySelectorAll("button[data-id]")) b.setAttribute("aria-current", "false");
  buildLog();
  render();
  startTicker();
}
function beginLiveHand(h) {
  liveHand = { ...h, streaming: true };
  liveActing = null;
  for (const k of Object.keys(lastDecision)) delete lastDecision[k];
  if (turn && turn.hand_id !== h.hand_id) turn = null;
  if (follow) showLive();
}
function takeLiveStep(ev) {
  if (!liveHand || ev.hand_id !== liveHand.hand_id) return;
  const s = ev.step;
  liveHand.steps.push(s);
  if (s.kind === "street") liveHand.board = liveHand.board.concat(s.cards);
  if (s.kind === "action") {
    if (liveActing && liveActing.seat === s.seat) liveActing = null;
    if (s.latency_ms !== undefined) lastDecision[s.seat] = { latency_ms: s.latency_ms, cost_usd: s.meta ? s.meta.cost_usd : undefined };
    if (s.meta && s.meta.model) modelOf[s.seat] = s.meta.model;
    if (s.table_talk) bubble(s.seat, s.table_talk);
  }
  if (watchingLive() && hand.hand_id === liveHand.hand_id) {
    step = liveHand.steps.length;
    $("scrub").max = step;
    $("log-list").appendChild(logItem(s));
    render();
  }
}
async function connectLive() {
  const st = await api("/api/live");
  if (!st.live) return;
  setLive(st);
  if (st.current) beginLiveHand(st.current);
  if (st.turn) showTurn(st.turn);
  const es = new EventSource("/api/events");
  es.onmessage = async (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type === "hand_start") beginLiveHand(ev.hand);
    else if (ev.type === "acting") {
      liveActing = { seat: ev.seat, kind: ev.kind, since: Date.now() };
      if (watchingLive()) render();
    } else if (ev.type === "step") takeLiveStep(ev);
    else if (ev.type === "hand") {
      if (liveHand && liveHand.hand_id === ev.hand_id) { liveHand.streaming = false; liveHand.finished = true; }
      await loadSession();
      await loadList(true);
      const now = await api("/api/live");  // the next hand may already be waiting for us
      setLive(now);
      if (now.turn) showTurn(now.turn);
      else if (follow && (!hand || hand.hand_id === ev.hand_id)) openHand(ev.hand_id, { at: "end" });
    } else if (ev.type === "turn") {
      const now = await api("/api/live");
      setLive(now);
      if (now.turn) showTurn(now.turn);
    } else if (ev.type === "done") {
      setLive({ ...live, finished: true, running: false, error: ev.error, hands_played: ev.hands, turn: null });
      es.close();
    }
  };
}

// Show the pending decision: the table as the human sees it, plus the action bar.
function showTurn(t) {
  turn = t;
  drillSpot = null;
  if (!liveHand || liveHand.hand_id !== t.hand_id) liveHand = { ...t, streaming: true };
  liveHand.players[t.seat].hole = t.players[t.seat].hole;  // our own cards, for the rest of the hand
  liveActing = null;
  showLive();
  fillActionBar(t);
}

// The action bar for a decision payload (a live turn or a drill spot): legal buttons and sizes.
function fillActionBar(t) {
  const L = t.legal;
  $("action-legal").textContent = `— ${L.describe}`;
  $("act-check").textContent = L.can_check ? "Check" : `Call ${L.call_amount}`;
  const canRaise = L.max_raise_to > 0;
  $("act-size").classList.toggle("hidden", !canRaise);
  if (canRaise) {
    $("act-raise").textContent = L.is_bet ? "Bet" : "Raise to";
    const amt = $("act-amount");
    amt.min = L.min_raise_to; amt.max = L.max_raise_to; amt.value = L.min_raise_to;
    // "raise to" sizes: what the pot will hold once I call, added on top of the current bet
    const potAfterCall = t.pot + L.call_amount;
    const sizes = [
      ["min", L.min_raise_to],
      ["½ pot", t.current_bet + Math.round(potAfterCall / 2)],
      ["pot", t.current_bet + potAfterCall],
      ["all-in", L.max_raise_to],
    ];
    $("act-quick").innerHTML = "";
    for (const [label, value] of sizes) {
      const v = Math.max(L.min_raise_to, Math.min(L.max_raise_to, value));
      const b = document.createElement("button"); b.type = "button"; b.textContent = label; b.title = `${v}`;
      b.onclick = () => { amt.value = v; };
      $("act-quick").appendChild(b);
    }
  }
  $("act-talk").value = "";
  const talks = $("act-talk-list");
  talks.classList.toggle("hidden", !t.talk.length);
  talks.textContent = t.talk.map(([who, text]) => `${who}: “${text}”`).join("   ");
  $("action-bar").classList.remove("hidden");
  $("action-bar").scrollIntoView({ block: "nearest" });
}

async function sendAction(text) {
  if (drillSpot) return answerDrill(text);
  if (!turn) return;
  const body = { action: text, table_talk: $("act-talk").value.trim() };
  const r = await fetch("/api/act", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) { $("action-legal").textContent = `— ${(await r.json()).detail}`; return; }
  turn = null;
  $("action-bar").classList.add("hidden");
  $("hand-sub").textContent = "waiting for the other seats";
  setLive({ ...live, turn: null });
}
const decision = () => drillSpot || turn;  // whichever decision the action bar is showing

$("act-fold").onclick = () => sendAction("fold");
$("act-check").onclick = () => sendAction(decision() && decision().legal.can_check ? "check" : "call");
$("act-raise").onclick = () => sendAction(`${decision().legal.is_bet ? "bet" : "raise"} ${$("act-amount").value}`);
$("action-bar").onsubmit = (e) => { e.preventDefault(); if (decision() && decision().legal.max_raise_to > 0) $("act-raise").click(); else $("act-check").click(); };

// ---- drill tab: the coach's flagged spots, asked on the real table ----
let drillPlayer = null, drillSpot = null;
async function showDrill({ restart = false } = {}) {
  const s = await api("/api/session");
  const picker = $("drill-picker"); picker.innerHTML = "";
  if (!drillPlayer && s.players.length) drillPlayer = s.hero || s.players[0];
  for (const name of s.players) {
    const b = document.createElement("button");
    b.textContent = name; b.setAttribute("aria-pressed", name === drillPlayer);
    b.onclick = () => { drillPlayer = name; showDrill(); };
    picker.appendChild(b);
  }
  $("drill-verdict").classList.add("hidden");
  if (!drillPlayer) { $("drill-progress").innerHTML = '<span class="empty-note">No hands yet.</span>'; return; }
  $("drill-progress").innerHTML = "Replaying the flagged spots…";
  const data = await api(`/api/drill/next?player=${encodeURIComponent(drillPlayer)}${restart ? "&restart=1" : ""}`);
  renderDrillProgress(data.progress);
  if (data.spot) showDrillSpot(data.spot);
  else {
    drillSpot = null;
    $("action-bar").classList.add("hidden");
    $("empty").classList.remove("hidden");
    $("replay").classList.add("hidden");
    const p = data.progress;
    $("empty").innerHTML = p.spots === 0
      ? `The coach found nothing to drill for ${esc(drillPlayer)}. Play more hands.`
      : p.asked ? `Done for now: ${p.correct} of ${p.asked} right. ${p.due - p.remaining > 0 ? "Wrong ones come back tomorrow; " : ""}use Start again to go through them once more.`
      : "Nothing is due today. Come back tomorrow, or play more hands.";
  }
}
function renderDrillProgress(p) {
  const total = Math.max(1, p.spots);
  const boxes = p.boxes.map((n, i) => `<span title="box ${i}: ${n}"><i style="width:${Math.round(100 * n / total)}%"></i></span>`).join("");
  $("drill-progress").innerHTML =
    `<strong>${p.remaining}</strong> to go today · ${p.spots} spots, ${p.due} due` +
    (p.asked ? ` · this sitting <strong>${p.correct}/${p.asked}</strong>` : "") +
    `<div class="boxes">${boxes}</div><div class="boxes-legend"><span>new / missed</span><span>30 days</span></div>`;
}
function showDrillSpot(spot) {
  stopAuto();
  turn = null;
  drillSpot = spot;
  hand = { ...spot, drill: true };
  step = spot.steps.length;
  $("empty").classList.add("hidden");
  $("replay").classList.remove("hidden");
  $("summary").classList.add("hidden");
  $("hand-title").textContent = `Drill · hand #${spot.hand_id}`;
  $("hand-sub").textContent = `your move on the ${spot.street}`;
  $("scrub").max = spot.steps.length;
  buildLog();
  render();
  fillActionBar(spot);
  $("act-talk").classList.add("hidden");
  $("drill-verdict").classList.add("hidden");
}
async function answerDrill(text) {
  const spot = drillSpot;
  const r = await fetch("/api/drill/answer", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ player: drillPlayer, key: spot.key, action: text }) });
  if (!r.ok) { $("action-legal").textContent = `— ${(await r.json()).detail}`; return; }
  const v = await r.json();
  drillSpot = null;
  $("action-bar").classList.add("hidden");
  $("act-talk").classList.remove("hidden");
  const box = $("drill-verdict");
  box.className = "verdict " + (v.correct ? "right" : "wrong");
  box.innerHTML = `<b>${v.correct ? "Right." : `Not this time — the answer is ${esc(v.answer_text)}.`}</b>` +
    `<div class="table-said">At the table you chose ${esc(v.at_the_table)}: ${esc(v.detail)}</div>` +
    `<div class="table-said">Box ${v.box}, next due ${esc(v.due)}.</div>`;
  box.classList.remove("hidden");
  renderDrillProgress(v.progress);
  $("hand-sub").textContent = v.correct ? "right — next spot when you are ready" : "not this time — next spot when you are ready";
  $("drill-next").focus();
}
$("drill-next").onclick = () => showDrill();
$("drill-restart").onclick = () => showDrill({ restart: true });
$("follow").onclick = (e) => { follow = !follow; e.target.setAttribute("aria-pressed", follow); if (follow && liveHand && liveHand.streaming) showLive(); };
$("auto").onclick = () => (autoTimer ? stopAuto() : startAuto());
$("pace").oninput = (e) => { $("pace-value").textContent = `${(+e.target.value).toFixed(1)} s`; };
$("pace").onchange = (e) => fetch("/api/live/pace", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pace: +e.target.value }) });

function setTab(which) {
  for (const name of ["hands", "board", "coach", "drill"]) $(`tab-${name}`).setAttribute("aria-pressed", name === which);
  const hands = which === "hands", drill = which === "drill";
  $("list").classList.toggle("hidden", !hands);
  $("drill-side").classList.toggle("hidden", !drill);
  $("hand-view").classList.toggle("hidden", !hands && !drill);
  $("board-view").classList.toggle("hidden", which !== "board");
  $("coach-view").classList.toggle("hidden", which !== "coach");
  if (!drill && drillSpot) {  // leaving a drill mid-spot: the table goes back to normal use
    drillSpot = null; hand = null;
    $("action-bar").classList.add("hidden"); $("act-talk").classList.remove("hidden");
    $("replay").classList.add("hidden"); $("empty").classList.remove("hidden");
  }
  if (which === "board") showBoard();
  if (which === "coach") showCoach();
  if (drill) showDrill();
}

// ---- coach tab ----
let coachPlayer = null, coachNotes = null, coachWeek = null;  // coachWeek: {label, since, until} or null = all
async function showCoach() {
  const s = await api("/api/session");
  const picker = $("seat-picker"); picker.innerHTML = "";
  for (const name of s.players) {
    const b = document.createElement("button");
    b.textContent = name; b.setAttribute("aria-pressed", name === coachPlayer);
    b.onclick = () => { coachPlayer = name; coachNotes = null; showCoach(); };
    picker.appendChild(b);
  }
  if (!coachPlayer && s.players.length) coachPlayer = s.hero || s.players[0];
  if (!coachPlayer) { $("coach").innerHTML = '<span class="empty-note">No hands yet.</span>'; return; }
  for (const b of picker.children) b.setAttribute("aria-pressed", b.textContent === coachPlayer);
  // a week picker, only when the hands span more than one week
  const weeks = $("week-picker"); weeks.innerHTML = "";
  weeks.classList.toggle("hidden", s.weeks.length < 2);
  if (s.weeks.length >= 2) {
    const all = document.createElement("button"); all.textContent = "All weeks";
    all.setAttribute("aria-pressed", coachWeek === null);
    all.onclick = () => { coachWeek = null; coachNotes = null; showCoach(); };
    weeks.appendChild(all);
    for (const w of s.weeks) {
      const b = document.createElement("button");
      b.textContent = `${w.label} · ${w.hands}`; b.title = `${w.since} to ${w.until}`;
      b.setAttribute("aria-pressed", !!coachWeek && coachWeek.label === w.label);
      b.onclick = () => { coachWeek = w; coachNotes = null; showCoach(); };
      weeks.appendChild(b);
    }
  }
  $("coach").innerHTML = '<span class="empty-note">Replaying every hand and running the numbers…</span>';
  const period = coachWeek ? `&since=${coachWeek.since}&until=${coachWeek.until}` : "";
  const r = await api(`/api/coach?player=${encodeURIComponent(coachPlayer)}${period}`);
  renderCoach(r);
}

// The Hands list narrowed to a few hands (a leak's examples); "All hands" brings the list back.
async function showHandSubset(ids, label) {
  const data = await api(`/api/hands?ids=${encodeURIComponent(ids.join(","))}`);
  const list = $("hand-list"); list.innerHTML = "";
  const head = document.createElement("li"); head.className = "subset";
  head.innerHTML = `<span>${esc(label)}</span>`;
  const back = document.createElement("button"); back.textContent = "All hands";
  back.onclick = () => loadList(true);
  head.appendChild(back);
  list.appendChild(head);
  for (const h of data.hands) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.dataset.id = h.hand_id;
    const net = h.players.filter(p => p.net > 0).map(p => `${esc(p.name)} +${p.net}`).join(", ");
    b.innerHTML = `<span class="id">#${esc(h.hand_id)}</span><span class="who">${net || "no winner"}</span><span class="pot">${h.pot}</span>`;
    b.addEventListener("click", () => openHand(h.hand_id));
    li.appendChild(b);
    list.appendChild(li);
  }
  $("more").classList.add("hidden");
  setTab("hands");
}

function renderCoach(r) {
  const root = $("coach");
  const netCls = r.net > 0 ? "plus" : r.net < 0 ? "minus" : "";
  const period = r.since || r.until ? ` (${coachWeek ? coachWeek.label : `${r.since} to ${r.until}`})` : "";
  let html = `<div class="lead"><strong>${esc(r.player)}</strong><span>${r.hands} hands${esc(period)}, ${r.decisions} decisions</span>` +
    `<span class="${netCls}">${r.net > 0 ? "+" : ""}${r.net} chips (${r.bb_per_100 > 0 ? "+" : ""}${r.bb_per_100} bb/100)</span>` +
    `<button id="ask-claude" type="button">Ask Claude to explain</button></div>`;
  if (!r.leaks.length) html += '<p class="empty-note">No leaks found by the charts and the math. Play more hands.</p>';
  html += '<ol class="leaks">';
  for (const leak of r.leaks) {
    const chips = Object.entries(leak.by_position).filter(([, [n]]) => n).map(([pos, [n, d]]) => `<span>${pos} ${n}/${d}</span>`).join("");
    const streets = Object.entries(leak.by_street).filter(([, [n]]) => n).map(([st, [n, d]]) => `<span class="street-chip">${st} ${n}/${d}</span>`).join("");
    const examples = leak.examples.slice(0, 6).map(e =>
      `<li><a href="#${e.hand_id}" data-hand="${e.hand_id}" data-step="${e.step}">#${e.hand_id}</a><span class="where">${e.street} ${e.position}</span>${esc(e.detail)}</li>`).join("");
    const note = coachNotes && coachNotes.notes.find(n => n.tag === leak.tag);
    const noteHtml = note ? `<div class="note">${esc(note.note)}<div class="do">Do this: ${esc(note.one_thing)}</div></div>` : "";
    const ids = leak.examples.map(e => e.hand_id).join(",");
    html += `<li><h3>${esc(leak.title)} <span class="rate">— ${leak.leaks} of ${leak.opportunities} (${Math.round(leak.rate * 100)}%)</span>` +
      ` <button type="button" class="leak-link" data-ids="${esc(ids)}" data-label="${esc(leak.title)}">show these hands</button></h3>` +
      `<div class="chips">${chips}${streets}</div><ul class="examples">${examples}</ul>${noteHtml}</li>`;
  }
  html += "</ol>";
  if (r.observations.length) html += "<h4>Observations</h4><ul class=\"plain\">" + r.observations.map(o => `<li>${esc(o)}</li>`).join("") + "</ul>";
  if (r.trend.length) html += "<h4>Trend</h4><ul class=\"plain\">" + r.trend.map(o => `<li>${esc(o)}</li>`).join("") + "</ul>";
  if (r.opponents && r.opponents.length) html += "<h4>Against whom</h4><ul class=\"plain\">" + r.opponents.map(o => `<li>${esc(o.text)}</li>`).join("") + "</ul>";
  if (coachNotes && coachNotes.focus) html += `<div class="focus">The one thing to work on: ${esc(coachNotes.focus)}</div>`;
  if (coachNotes) html += `<p class="empty-note">${esc(coachNotes.model)}, $${coachNotes.cost_usd}${coachNotes.dropped ? `, ${coachNotes.dropped} unsupported note(s) dropped` : ""}</p>`;
  root.innerHTML = html;
  for (const b of root.querySelectorAll("button.leak-link")) {
    b.onclick = () => showHandSubset(b.dataset.ids.split(","), b.dataset.label);
  }
  for (const a of root.querySelectorAll("a[data-hand]")) {
    a.onclick = async (e) => {
      e.preventDefault();
      setTab("hands");
      await openHand(a.dataset.hand);
      go(+a.dataset.step);
    };
  }
  const ask = $("ask-claude");
  ask.onclick = async () => {
    ask.disabled = true; ask.textContent = "Asking…";
    const resp = await fetch("/api/coach/narrate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ player: r.player }) });
    if (!resp.ok) { ask.disabled = false; ask.textContent = "Ask Claude to explain"; alert(`Could not ask Claude: ${(await resp.json()).detail}`); return; }
    coachNotes = await resp.json();
    renderCoach(r);
  };
}

function go(n) {
  if (!hand) return;
  n = Math.max(0, Math.min(hand.steps.length, n));
  const s = n === step + 1 ? hand.steps[n - 1] : null;  // stepping onto a line: let it speak
  if (s && s.kind === "action" && s.table_talk) bubble(s.seat, s.table_talk);
  step = n;
  render();
}
for (const id of ["first", "prev", "next", "last", "scrub"]) $(id).addEventListener("pointerdown", stopAuto);

$("first").onclick = () => go(0);
$("prev").onclick = () => go(step - 1);
$("next").onclick = () => go(step + 1);
$("last").onclick = () => go(Infinity);
$("scrub").oninput = (e) => go(+e.target.value);
$("toggle-think").onclick = (e) => { showThink = !showThink; e.target.setAttribute("aria-pressed", showThink); render(); };
$("toggle-cards").onclick = (e) => { showCards = !showCards; e.target.setAttribute("aria-pressed", showCards); render(); };
$("tab-hands").onclick = () => setTab("hands");
$("tab-board").onclick = () => setTab("board");
$("tab-coach").onclick = () => setTab("coach");
$("tab-drill").onclick = () => setTab("drill");
$("more").onclick = () => loadList(false);
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === " ") { e.preventDefault(); $("auto").click(); }
  else if (e.key === "ArrowRight") go(step + 1);
  else if (e.key === "ArrowLeft") go(step - 1);
  else if (e.key === "Home") go(0);
  else if (e.key === "End") go(Infinity);
});

(async () => {
  await loadSession();
  await loadList(true);
  const wanted = location.hash.slice(1);
  if (wanted) openHand(wanted).catch(() => {});
  connectLive().catch(() => {});
})();
