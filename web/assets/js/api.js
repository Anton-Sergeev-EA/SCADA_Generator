// HTTP API и WebSocket с автоматическим переподключением.
let token = null;
try { token = sessionStorage.getItem("scada.token"); } catch { /* нет хранилища */ }

export function setToken(value) {
  token = value;
  try { sessionStorage.setItem("scada.token", value); } catch { /* нет хранилища */ }
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-API-Token"] = token;
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

export function connectWS(onMessage, onState) {
  let ws;
  let retry = 500;
  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    onState("connecting");
    ws.onopen = () => { retry = 500; onState("open"); };
    ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
    ws.onclose = () => {
      onState("closed");
      setTimeout(open, retry);
      retry = Math.min(retry * 2, 8000);
    };
    ws.onerror = () => ws.close();
  };
  open();
}
