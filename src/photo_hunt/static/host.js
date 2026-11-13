"use strict";

const HOST_KEY = "photo_hunt_host";
const VERDICT_TEXT = { checking: "проверка", accepted: "засчитано", rejected: "не засчитано", review: "решает ведущий" };

const $ = (id) => document.getElementById(id);
const els = {
  login: $("login"), loginToken: $("host-token"), loginError: $("login-error"),
  app: $("app"), stage: $("stage"), summary: $("summary"), counts: $("counts"),
  song: $("song"), item: $("item"), lyric: $("lyric"), advance: $("advance"), duration: $("duration"),
  setup: $("setup"), controls: $("controls"), btnMusic: $("btn-music"), btnHunt: $("btn-hunt"), btnFinish: $("btn-finish"),
  btnReview: $("btn-review"), btnReset: $("btn-reset"), audio: $("audio"), subs: $("subs"), error: $("error"),
};

let token = store.get("sessionStorage", HOST_KEY);
let state = null;
let songs = [];
let currentSong = null;
let advanceTouched = false;
const photoUrls = new Map();

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}

function hostApi(path, opts = {}) {
  return api(path, { ...opts, headers: { ...(opts.headers || {}), "X-Host-Token": token } });
}

async function run(action) {
  els.error.textContent = "";
  try { await action(); } catch (err) {
    els.error.textContent = err.message;
    if (err.status === 401) logout();
  }
}

function logout() {
  store.remove("sessionStorage", HOST_KEY);
  location.reload();
}

// Фото грузим с заголовком, а не по ссылке: пароль ведущего не попадает в адрес.
async function photoUrl(id) {
  if (photoUrls.has(id)) return photoUrls.get(id);
  const res = await hostApi(`/api/host/photo/${encodeURIComponent(id)}`);
  const url = URL.createObjectURL(await res.blob());
  photoUrls.set(id, url);
  return url;
}

function photoImg(id, alt) {
  const img = el("img", { alt });
  photoUrl(id).then((u) => { img.src = u; }).catch(() => { img.alt = "нет фото"; });
  return img;
}

function suggestAdvance(alive) {
  if (alive <= 1) return 1;
  if (alive <= 5) return alive - 1;
  return Math.ceil(alive * 0.6);
}

// ---------- отрисовка ----------

function render(s) {
  state = s;
  const roundText = s.round_no ? `Раунд ${s.round_no} · ` : "";
  els.summary.textContent = `${roundText}в игре ${s.alive_count} из ${s.players_total}`;
  const c = s.counts;
  els.counts.textContent = s.phase === "hunt"
    ? `Засчитано ${c.accepted} из ${s.advance} · проверка ${c.checking} · спорных ${c.review} · отказ ${c.rejected}`
    : "";

  const canStart = s.phase === "lobby" || s.phase === "results";
  els.setup.hidden = !canStart;
  els.btnHunt.hidden = s.phase !== "music";
  els.btnFinish.hidden = s.phase !== "hunt";
  els.btnReview.hidden = !(s.phase === "hunt" && c.review > 0);
  els.controls.hidden = els.btnHunt.hidden && els.btnFinish.hidden && els.audio.hidden;
  if (canStart && !advanceTouched) els.advance.value = suggestAdvance(s.alive_count);
  els.advance.max = Math.max(1, s.alive_count - 1);

  renderStage(s);
  renderSubs(s);
}

function renderStage(s) {
  const nodes = [];
  if (s.phase === "lobby") {
    nodes.push(el("div", { class: "headline", text: "Сканируй и играй" }));
    nodes.push(el("img", { class: "qr", src: "/api/qr.svg", alt: "QR-код для входа в игру" }));
    nodes.push(el("div", { class: "lyric", text: s.join_url }));
    nodes.push(el("div", { class: "headline", text: `Игроков: ${s.players_total}` }));
  } else if (s.phase === "music") {
    nodes.push(el("div", { class: "lyric", text: `Раунд ${s.round_no}` }));
    nodes.push(el("div", { class: "headline", text: "Слушаем песню…" }));
    nodes.push(el("div", { class: "lyric", text: `В игре ${s.alive_count}, дальше пройдут ${s.advance}` }));
  } else if (s.phase === "hunt") {
    if (s.lyric) nodes.push(el("div", { class: "lyric", text: s.lyric }));
    nodes.push(el("div", { class: "item", text: s.item }));
    const timer = el("div", { class: "timer" });
    countdown(timer, s.seconds_left);
    nodes.push(timer);
    nodes.push(el("div", { class: "headline", text: `${s.counts.accepted} / ${s.advance}` }));
  } else if (s.phase === "results") {
    nodes.push(el("div", { class: "headline", text: s.survivors.length ? `Дальше проходят ${s.survivors.length}` : "Никто не успел, раунд переигрываем" }));
    nodes.push(gallery(s.survivors));
  } else if (s.phase === "finished") {
    const w = s.survivors[0];
    nodes.push(el("div", { class: "headline", text: "Победитель" }));
    if (w && w.submission_id) nodes.push(el("div", { class: "winner" }, [photoImg(w.submission_id, w.name)]));
    nodes.push(el("div", { class: "item", text: w ? w.name : "" }));
  }
  els.stage.replaceChildren(...nodes);
}

function gallery(list) {
  return el("div", { class: "gallery" }, list.map((p) =>
    el("figure", {}, [
      p.submission_id ? photoImg(p.submission_id, p.name) : el("div"),
      el("figcaption", { text: p.name }),
    ])));
}

function renderSubs(s) {
  if (!s.submissions.length) {
    els.subs.replaceChildren(el("p", { class: "muted", text: s.phase === "hunt" ? "Фото пока нет" : "Раунд не идёт" }));
    return;
  }
  const rows = s.submissions.map((sub) => {
    const showPhoto = sub.verdict === "review" || sub.verdict === "accepted";
    const info = el("div", {}, [
      el("div", { text: `${sub.t} с · ${sub.name}` }),
      el("div", { class: "muted", text: `${VERDICT_TEXT[sub.verdict]}${sub.reason ? " · " + sub.reason : ""}` }),
    ]);
    const actions = el("div", { class: "actions" });
    if (s.phase === "hunt" && sub.verdict !== "checking") {
      if (sub.verdict !== "accepted") actions.append(decideBtn(sub.id, true));
      if (sub.verdict !== "rejected") actions.append(decideBtn(sub.id, false));
    }
    return el("div", { class: `sub ${sub.verdict}` }, [showPhoto ? photoImg(sub.id, sub.name) : el("div"), info, actions]);
  });
  els.subs.replaceChildren(...rows);
}

function decideBtn(id, accept) {
  const b = el("button", { class: accept ? "ok" : "bad", type: "button", text: accept ? "Засчитать" : "Отклонить" });
  b.addEventListener("click", () => run(() => hostApi(`/api/host/decide/${encodeURIComponent(id)}`, { method: "POST", body: { accept } })));
  return b;
}

// ---------- музыка ----------

function fillSongs() {
  els.song.replaceChildren(
    el("option", { value: "", text: "Без песни (ввести вручную)" }),
    ...songs.map((s) => el("option", { value: String(s.id), text: s.title + (s.has_music ? "" : " (нет файла)") })),
  );
}

els.song.addEventListener("change", () => {
  const s = songs.find((x) => String(x.id) === els.song.value);
  if (s) { els.item.value = s.item; els.lyric.value = s.lyric; }
});
els.advance.addEventListener("input", () => { advanceTouched = true; });

async function loadAudio(song) {
  if (!song || !song.has_music) { els.audio.hidden = true; return; }
  const res = await hostApi(`/api/host/music/${song.id}`);
  if (els.audio.src) URL.revokeObjectURL(els.audio.src);
  els.audio.src = URL.createObjectURL(await res.blob());
  els.audio.hidden = false;
  await els.audio.play();
}

els.audio.addEventListener("timeupdate", () => {
  if (state && state.phase === "music" && currentSong && currentSong.stop_at != null && els.audio.currentTime >= currentSong.stop_at) {
    run(stopAndHunt);
  }
});

async function stopAndHunt() {
  if (!state || state.phase !== "music") return;
  els.audio.pause();
  state = { ...state, phase: "hunt-pending" };
  await hostApi("/api/host/hunt", { method: "POST", body: { duration_s: Number(els.duration.value) || 45 } });
}

els.btnMusic.addEventListener("click", () => run(async () => {
  currentSong = songs.find((x) => String(x.id) === els.song.value) || null;
  await hostApi("/api/host/music", {
    method: "POST",
    body: { item: els.item.value, lyric: els.lyric.value, advance: Number(els.advance.value) },
  });
  advanceTouched = false;
  await loadAudio(currentSong);
}));

els.btnHunt.addEventListener("click", () => run(stopAndHunt));
els.btnFinish.addEventListener("click", () => run(() => hostApi("/api/host/finish", { method: "POST" })));
els.btnReview.addEventListener("click", () => run(() => hostApi("/api/host/accept-review", { method: "POST" })));
els.btnReset.addEventListener("click", () => {
  if (!window.confirm("Начать новую игру? Все игроки и фото будут удалены.")) return;
  run(async () => {
    await hostApi("/api/host/reset", { method: "POST" });
    for (const u of photoUrls.values()) URL.revokeObjectURL(u);
    photoUrls.clear();
  });
});

// ---------- вход ----------

async function enter() {
  songs = await hostApi("/api/host/songs");
  fillSongs();
  els.login.hidden = true;
  els.app.hidden = false;
  socket("/ws/host", token, (msg) => { if (msg.type === "state") render(msg); }, logout);
}

els.login.addEventListener("submit", async (e) => {
  e.preventDefault();
  token = els.loginToken.value;
  try {
    await enter();
    store.set("sessionStorage", HOST_KEY, token);
  } catch (err) {
    els.loginError.textContent = err.status === 401 ? "Неверный пароль" : err.message;
  }
});

if (token) enter().catch(() => { els.login.hidden = false; });
else els.login.hidden = false;
