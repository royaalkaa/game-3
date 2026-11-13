"use strict";

const HOST_KEY = "photo_hunt_host";
const TEST_LEAD_S = 6;

const $ = (id) => document.getElementById(id);
const els = {
  login: $("login"), loginToken: $("host-token"), loginError: $("login-error"), app: $("app"),
  list: $("list"), btnNew: $("btn-new"), form: $("form"), formTitle: $("form-title"),
  title: $("title"), item: $("item"), lyric: $("lyric"), stopAt: $("stop-at"), music: $("music"),
  player: $("player"), audio: $("audio"), btnMark: $("btn-mark"), btnTest: $("btn-test"),
  btnDelete: $("btn-delete"), saved: $("saved"), error: $("error"),
};

let token = store.get("sessionStorage", HOST_KEY);
let songs = [];
let current = null;
let testStop = null;
const hostApi = (path, opts) => withHostToken(token, path, opts);

async function run(action) {
  els.error.textContent = "";
  els.saved.textContent = "";
  try { await action(); } catch (err) {
    els.error.textContent = err.message;
    if (err.status === 401) { store.remove("sessionStorage", HOST_KEY); location.reload(); }
  }
}

function formatStop(s) {
  return s.stop_at == null ? "стоп вручную" : `стоп на ${s.stop_at} с`;
}

function renderList() {
  if (!songs.length) {
    els.list.replaceChildren(el("p", { class: "muted", text: "Песен пока нет" }));
    return;
  }
  els.list.replaceChildren(...songs.map((s) => {
    const btn = el("button", { type: "button", class: s.id === (current && current.id) ? "song active" : "song" }, [
      el("strong", { text: s.title }),
      el("span", { class: "muted", text: `${s.item} · ${formatStop(s)}${s.has_music ? "" : " · нет файла"}` }),
    ]);
    btn.addEventListener("click", () => run(() => select(s)));
    return btn;
  }));
}

function setAudio(blobUrl) {
  if (els.audio.src) URL.revokeObjectURL(els.audio.src);
  if (blobUrl) els.audio.src = blobUrl; else els.audio.removeAttribute("src");
  els.player.hidden = !blobUrl;
}

async function select(song) {
  current = song;
  els.formTitle.textContent = song ? song.title : "Новая песня";
  els.title.value = song ? song.title : "";
  els.item.value = song ? song.item : "";
  els.lyric.value = song ? song.lyric : "";
  els.stopAt.value = song && song.stop_at != null ? song.stop_at : "";
  els.music.value = "";
  els.btnDelete.hidden = !song;
  renderList();
  setAudio(null);
  if (song && song.has_music) {
    const res = await hostApi(`/api/host/music/${encodeURIComponent(song.id)}`);
    setAudio(URL.createObjectURL(await res.blob()));
  }
}

// Выбранный файл можно прослушать и отметить остановку ещё до сохранения.
els.music.addEventListener("change", () => {
  const file = els.music.files[0];
  setAudio(file ? URL.createObjectURL(file) : null);
});

els.btnMark.addEventListener("click", () => {
  els.stopAt.value = (Math.round(els.audio.currentTime * 10) / 10).toString();
  els.audio.pause();
});

els.btnTest.addEventListener("click", () => {
  const stop = Number(els.stopAt.value);
  if (!els.stopAt.value || Number.isNaN(stop)) { els.error.textContent = "Сначала отметь секунду остановки"; return; }
  testStop = stop;
  els.audio.currentTime = Math.max(0, stop - TEST_LEAD_S);
  els.audio.play();
});

els.audio.addEventListener("timeupdate", () => {
  if (testStop != null && els.audio.currentTime >= testStop) { els.audio.pause(); testStop = null; }
});

els.btnNew.addEventListener("click", () => run(() => select(null)));

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  run(async () => {
    const body = {
      title: els.title.value.trim(),
      item: els.item.value.trim(),
      lyric: els.lyric.value.trim(),
      stop_at: els.stopAt.value === "" ? null : Number(els.stopAt.value),
    };
    let song = current
      ? await hostApi(`/api/host/songs/${encodeURIComponent(current.id)}`, { method: "PUT", body })
      : await hostApi("/api/host/songs", { method: "POST", body });
    current = song; // если файл не загрузится, повторное сохранение не создаст дубль
    const file = els.music.files[0];
    if (file) {
      const form = new FormData();
      form.append("music", file);
      els.saved.textContent = "Загружаю файл…";
      song = await hostApi(`/api/host/songs/${encodeURIComponent(song.id)}/music`, { method: "PUT", form });
    }
    songs = await hostApi("/api/host/songs");
    await select(songs.find((s) => s.id === song.id) || null);
    els.saved.textContent = "Сохранено";
  });
});

els.btnDelete.addEventListener("click", () => {
  if (!current || !window.confirm(`Удалить песню «${current.title}» вместе с файлом?`)) return;
  run(async () => {
    await hostApi(`/api/host/songs/${encodeURIComponent(current.id)}`, { method: "DELETE" });
    songs = await hostApi("/api/host/songs");
    await select(null);
  });
});

async function enter() {
  songs = await hostApi("/api/host/songs");
  els.login.hidden = true;
  els.app.hidden = false;
  await select(null);
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
