"use strict";

const TOKEN_KEY = "photo_hunt_token";
const MAX_SIDE = 1024;
const JPEG_QUALITY = 0.75;

const $ = (id) => document.getElementById(id);
const els = {
  join: $("join"), joinForm: $("join-form"), name: $("name"),
  game: $("game"), round: $("round"), title: $("title"), item: $("item"), lyric: $("lyric"),
  timer: $("timer"), status: $("status"), shoot: $("shoot"), camera: $("camera"), error: $("error"),
};

let conn = null;
let lastState = null;
let uploading = false;

function showError(text) { els.error.textContent = text || ""; }

function showJoin() {
  if (conn) { conn.stop(); conn = null; }
  store.remove("localStorage", TOKEN_KEY);
  els.game.hidden = true;
  els.join.hidden = false;
}

function start(token) {
  els.join.hidden = true;
  els.game.hidden = false;
  els.title.textContent = "Подключаемся…";
  conn = socket("/ws/player", token, onMessage, showJoin);
}

function onMessage(msg) {
  if (msg.type === "reset") { showJoin(); showError("Ведущий начал новую игру, зайди заново"); return; }
  if (msg.type === "state") { lastState = msg; render(msg); }
}

function setStatus(text, tone) {
  els.status.textContent = text;
  els.status.className = "status" + (tone ? " " + tone : "");
}

function render(s) {
  const me = s.me;
  els.round.textContent = s.round_no ? `Раунд ${s.round_no} · в игре ${s.alive_count}` : `Игроков: ${s.alive_count}`;
  const showItem = Boolean(s.item);
  els.item.hidden = !showItem;
  els.item.textContent = s.item || "";
  els.lyric.hidden = !(showItem && s.lyric);
  els.lyric.textContent = s.lyric || "";
  els.shoot.hidden = true;
  countdown(els.timer, s.phase === "hunt" ? s.seconds_left : null);

  if (s.phase === "finished") {
    els.title.textContent = me.alive ? "Ты победил!" : `Победитель: ${s.winner}`;
    setStatus(me.alive ? "Поздравляем!" : "Спасибо за игру", me.alive ? "ok" : "");
    return;
  }
  if (!me.alive) {
    els.title.textContent = "Ты выбыл";
    setStatus("Смотри, кто победит", "");
    return;
  }
  if (s.phase === "lobby") {
    els.title.textContent = `${me.name}, ты в игре`;
    setStatus("Ждём старта", "");
  } else if (s.phase === "music") {
    els.title.textContent = "Слушай песню";
    setStatus("Когда музыка остановится, здесь появится предмет", "");
  } else if (s.phase === "hunt") {
    els.title.textContent = "Найди и сфоткайся:";
    renderHunt(s);
  } else if (s.phase === "results") {
    els.title.textContent = "Ты прошёл дальше!";
    setStatus("Готовься к следующему раунду", "ok");
  }
}

function renderHunt(s) {
  const v = s.me.verdict;
  const timeUp = s.seconds_left !== null && s.seconds_left <= 0;
  if (v === "accepted") {
    setStatus(`Засчитано! Ты ${s.me.place}-й. Проходят первые ${s.advance}.`, "ok");
  } else if (v === "checking" || uploading) {
    setStatus("Проверяем фото…", "warn");
  } else if (v === "review") {
    setStatus("Фото смотрит ведущий", "warn");
  } else {
    if (v === "rejected") setStatus(`Не засчитано: ${s.me.reason}. Попробуй ещё!`, "bad");
    else setStatus("", "");
    els.shoot.hidden = timeUp;
    if (timeUp) setStatus("Время вышло", "bad");
  }
}

async function compress(file) {
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Не удалось обработать фото"))), "image/jpeg", JPEG_QUALITY);
  });
}

els.shoot.addEventListener("click", () => { showError(""); els.camera.click(); });

els.camera.addEventListener("change", async () => {
  const file = els.camera.files && els.camera.files[0];
  els.camera.value = "";
  if (!file) return;
  uploading = true;
  if (lastState) render(lastState);
  try {
    const blob = await compress(file);
    const form = new FormData();
    form.append("photo", blob, "photo.jpg");
    await api("/api/photo", {
      method: "POST", form,
      headers: { "X-Player-Token": store.get("localStorage", TOKEN_KEY) || "" },
    });
  } catch (err) {
    showError(err.message);
    if (err.status === 401) showJoin();
  } finally {
    uploading = false;
    if (lastState) render(lastState);
  }
});

els.joinForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  showError("");
  try {
    const { token } = await api("/api/join", { method: "POST", body: { name: els.name.value } });
    store.set("localStorage", TOKEN_KEY, token);
    start(token);
  } catch (err) {
    showError(err.message);
  }
});

const saved = store.get("localStorage", TOKEN_KEY);
if (saved) start(saved); else showJoin();
