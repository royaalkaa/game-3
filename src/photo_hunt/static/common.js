"use strict";

const store = {
  get(area, key) {
    try { return window[area].getItem(key); } catch { return null; }
  },
  set(area, key, value) {
    try { window[area].setItem(key, value); } catch { /* приватный режим: работаем без сохранения */ }
  },
  remove(area, key) {
    try { window[area].removeItem(key); } catch { /* см. выше */ }
  },
};

async function api(path, { method = "GET", body, headers = {}, form } = {}) {
  const init = { method, headers: { ...headers } };
  if (form) {
    init.body = form;
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = "Что-то пошло не так, попробуй ещё раз";
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
    } catch { /* ответ без JSON */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("application/json") ? res.json() : res;
}

function withHostToken(token, path, opts = {}) {
  return api(path, { ...opts, headers: { ...(opts.headers || {}), "X-Host-Token": token } });
}

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

function socket(path, token, onMessage, onAuthFailed) {
  let delay = 500;
  let stopped = false;
  let ws;
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}${path}`);
    ws.onopen = () => { delay = 500; ws.send(JSON.stringify({ token })); };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "auth_failed") { stopped = true; onAuthFailed(); return; }
      onMessage(msg);
    };
    ws.onclose = () => {
      if (stopped) return;
      setTimeout(connect, delay);
      delay = Math.min(delay * 2, 5000);
    };
  }
  connect();
  return { stop() { stopped = true; ws.close(); } };
}

function countdown(el, secondsLeft) {
  if (el._timer) clearInterval(el._timer);
  if (secondsLeft == null) { el.hidden = true; return; }
  const end = performance.now() + secondsLeft * 1000;
  const tick = () => {
    const left = Math.max(0, Math.ceil((end - performance.now()) / 1000));
    el.textContent = left > 0 ? `${left} с` : "Время вышло";
    if (left === 0) clearInterval(el._timer);
  };
  el.hidden = false;
  tick();
  el._timer = setInterval(tick, 250);
}
