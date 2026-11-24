"use strict";
const $ = (s, el = document) => el.querySelector(s);
const api = async (path, opts) => (await fetch(path, opts)).json();
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) });
const esc = (s) => (s || "").toString().replace(/[&<>"]/g,
  (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));

const ICON = { wifi: "📶", cpu: "🖥️", network: "🌐", config: "⚙️", info: "ℹ️" };
let CAT = null, SCAN = [], CURRENT = "wifi", capTimer = null;
let IFACES = [], IFACE = localStorage.getItem("ng_iface") || "wlan1";

const THEMES = [
  { id: "dark", nome: "Dark" },
  { id: "hacker", nome: "Hacker 🟢" },
  { id: "itachi", nome: "Itachi 🔴" },
  { id: "peppa", nome: "Peppa Pig 🐷" },
];
function applyTheme(t) {
  if (t && t !== "dark") document.documentElement.dataset.theme = t;
  else document.documentElement.removeAttribute("data-theme");
  localStorage.setItem("ng_theme", t || "dark");
}
applyTheme(localStorage.getItem("ng_theme") || "dark");

function toast(m){const t=$("#toast");t.textContent=m;t.classList.add("show");
  setTimeout(()=>t.classList.remove("show"),2300);}

async function boot() {
  CAT = await api("/api/catalog");
  $("#ver").textContent = "v" + CAT.version;
  await loadIfaces();
  const secs = [
    { id: "wifi", title: "WiFi", icon: "wifi" },
    { id: "rede", title: "Rede", icon: "network" },
    { id: "sistema", title: "Sistema", icon: "cpu" },
    { id: "config", title: "Config", icon: "config" },
  ];
  $("#tabs").innerHTML = secs.map((s) =>
    `<button data-sec="${s.id}"><span class="ic">${ICON[s.icon]}</span>${esc(s.title)}</button>`).join("");
  $("#tabs").querySelectorAll("button").forEach((b) => b.onclick = () => go(b.dataset.sec));
  go("wifi");
}

async function loadIfaces() {
  const r = await api("/api/interfaces");
  IFACES = (r.ifaces || []);
  if (!IFACES.find((i) => i.nome === IFACE) && IFACES[0]) IFACE = IFACES[0].nome;
}

function ifaceBar() {
  const opts = IFACES.map((i) =>
    `<option value="${i.nome}" ${i.nome === IFACE ? "selected" : ""}>${esc(i.nome)} · ${esc(i.papel || "")}</option>`).join("");
  return `<div class="ifbar"><label>Interface</label>
    <select id="ifSel">${opts || `<option>${esc(IFACE)}</option>`}</select></div>`;
}
function bindIface() {
  const s = $("#ifSel"); if (!s) return;
  s.onchange = () => { IFACE = s.value; localStorage.setItem("ng_iface", IFACE); toast("interface: " + IFACE); };
}

function go(sec) {
  CURRENT = sec;
  $("#tabs").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.sec === sec));
  if (capTimer) { clearInterval(capTimer); capTimer = null; }
  ({ wifi: viewWifi, rede: viewRede, sistema: viewSystem, config: viewConfig }[sec] || viewWifi)();
}

/* ---------------- WiFi ---------------- */
function viewWifi() {
  $("#view").innerHTML = `
    <div class="warn">⚠️ Auditoria. Só nas suas redes ou com autorização.</div>
    ${ifaceBar()}
    <button class="btn" id="btnScan">Escanear redes</button>
    <div id="scanList" class="menu"></div>`;
  bindIface();
  $("#btnScan").onclick = doScan;
  if (SCAN.length) renderScan();
}
async function doScan() {
  const b = $("#btnScan");
  b.disabled = true; b.innerHTML = `<span class="spin"></span>Escaneando ~12s…`;
  const r = await post("/api/wifi/scan", { seconds: 12, iface: IFACE });
  b.disabled = false; b.textContent = "Escanear de novo";
  if (!r.ok) { toast(r.error || "falha"); return; }
  SCAN = r.networks || []; renderScan();
}
function renderScan() {
  const el = $("#scanList");
  if (!SCAN.length) { el.innerHTML = `<div class="empty">nenhuma rede</div>`; return; }
  el.innerHTML = SCAN.map((n, i) => `
    <div class="item ${n.meu ? "mine" : ""}" data-i="${i}">
      <div class="it-main">
        <div class="it-t">${esc(n.essid)} ${n.meu ? '<span class="tag">minha</span>' : ""}</div>
        <div class="it-s">${n.qtd_aps} AP · ch ${esc(n.canais.join(",") || "?")} · ${esc(n.seguranca || "")}</div>
      </div><div class="chev">capturar ›</div>
    </div>`).join("");
  el.querySelectorAll(".item").forEach((it) =>
    it.onclick = () => startCapture(SCAN[it.dataset.i]));
}

/* ---------------- Captura ---------------- */
async function startCapture(net) {
  const r = await post("/api/wifi/capture/start", {
    bssids: net.aps, channels: net.canais.map(Number).filter(Boolean), iface: IFACE });
  if (!r.ok) { toast(r.error || "falha"); return; }
  viewCapture(net);
}
function viewCapture(net) {
  $("#view").innerHTML = `
    <div class="card">
      <h3>Capturando — ${esc(net.essid)} <span class="muted">(${esc(IFACE)})</span></h3>
      <div class="kv"><span class="k">Tempo</span><span class="v" id="cT">0s</span></div>
      <div class="kv"><span class="k">Capturado</span><span class="v" id="cB">0 KB</span></div>
      <div class="kv"><span class="k">Handshakes</span><span class="v" id="cH">0</span></div>
      <div class="muted" id="cHint" style="margin-top:8px">Aguardando um aparelho reconectar…</div>
      <div class="row" style="margin-top:12px">
        <button class="btn danger" id="btnStop">Parar</button>
        <button class="btn" id="btnExport" disabled>Exportar</button>
      </div>
    </div><div id="exportBox"></div>`;
  $("#btnStop").onclick = stopCapture;
  $("#btnExport").onclick = doExport;
  capTimer = setInterval(pollCapture, 3000); pollCapture();
}
async function pollCapture() {
  const s = await api("/api/wifi/capture/status");
  if (!s.ok) return;
  $("#cT").textContent = s.segundos + "s";
  $("#cB").textContent = (s.bytes / 1024).toFixed(1) + " KB";
  $("#cH").textContent = s.handshakes;
  $("#btnExport").disabled = !(s.handshakes > 0);
  if (s.handshakes > 0) $("#cHint").innerHTML = "✅ Handshake capturado! Pode exportar.";
  if (!s.rodando && capTimer) { clearInterval(capTimer); capTimer = null; }
}
async function stopCapture() {
  if (capTimer) { clearInterval(capTimer); capTimer = null; }
  const r = await post("/api/wifi/capture/stop");
  toast(r.handshakes ? `parado · ${r.handshakes} handshake(s)` : "parado · sem handshake");
  await pollCapture().catch(() => {});
  if (r.handshakes) $("#btnExport").disabled = false;
}
async function doExport() {
  const r = await api("/api/wifi/export");
  if (!r.ok) { toast(r.error || "sem handshake"); return; }
  const url = URL.createObjectURL(new Blob([r.conteudo], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url; a.download = r.arquivo; document.body.appendChild(a); a.click(); a.remove();
  $("#exportBox").innerHTML = `<div class="card"><h3>Exportado</h3>
    <div class="kv"><span class="k">Arquivo</span><span class="v">${esc(r.arquivo)}</span></div>
    <div class="kv"><span class="k">Handshakes</span><span class="v">${r.linhas}</span></div>
    <div class="muted" style="margin-top:8px">${esc(r.dica)}</div></div>`;
  toast("baixado — joga na GPU");
}

/* ---------------- Rede (catálogo) ---------------- */
function viewRede() {
  const sec = CAT.sections.find((s) => s.id === "rede");
  if (!sec) { $("#view").innerHTML = `<div class="empty">vazio</div>`; return; }
  $("#view").innerHTML = `<div class="menu" id="redeMenu">` + sec.actions.map((a) =>
    `<div class="item" data-a="${esc(a.id)}">
      <div class="it-main"><div class="it-t">${esc(a.nome)}</div>
        <div class="it-s">${esc(a.desc || "")}</div></div><div class="chev">›</div>
    </div>`).join("") + `</div><div id="actBox"></div>`;
  $("#redeMenu").querySelectorAll(".item").forEach((it) =>
    it.onclick = () => openAction(sec.actions.find((a) => a.id === it.dataset.a)));
}
function openAction(a) {
  $("#actBox").innerHTML = `<div class="card"><h3>${esc(a.nome)}</h3>
    ${(a.campos || []).map((c) =>
      `<label>${esc(c.label)}</label><input id="f_${esc(c.nome)}" placeholder="${esc(c.placeholder || "")}">`).join("")}
    <button class="btn" id="runA">Rodar</button>
    <pre id="outA" style="display:none"></pre></div>`;
  $("#runA").onclick = async () => {
    const fields = {};
    (a.campos || []).forEach((c) => fields[c.nome] = ($(`#f_${c.nome}`) || {}).value || "");
    const o = $("#outA"); o.style.display = "block"; o.textContent = "rodando…";
    const r = await post("/api/run", { id: a.id, fields });
    o.textContent = r.ok ? (r.stdout || r.stderr || "(sem saída)") : (r.error || "erro");
  };
}

/* ---------------- Config / Hotspot ---------------- */
async function viewConfig() {
  $("#view").innerHTML = `<div class="center"><span class="spin"></span>carregando…</div>`;
  const hs = await api("/api/hotspot/status");
  const opts = IFACES.map((i) =>
    `<option value="${i.nome}" ${i.nome === "wlan0" ? "selected" : ""}>${esc(i.nome)} · ${esc(i.papel || "")}</option>`).join("");
  const curTheme = localStorage.getItem("ng_theme") || "dark";
  const themeOpts = THEMES.map((t) =>
    `<option value="${t.id}" ${t.id === curTheme ? "selected" : ""}>${esc(t.nome)}</option>`).join("");
  $("#view").innerHTML = `
    <div class="card"><h3>Tema</h3>
      <select id="themeSel">${themeOpts}</select>
    </div>
    <div class="card"><h3>Interface de ataque (padrão)</h3>
      ${ifaceBar()}
      <div class="muted" style="margin-top:6px">Usada no scan e captura. A wlan0 é a interna (conexão); o adaptador USB é o rádio.</div>
    </div>
    <div class="card"><h3>Hotspot</h3>
      <div class="kv"><span class="k">Status</span><span class="v">
        <span class="pill ${hs.ativo ? "on" : "off"}">${hs.ativo ? "ligado" : "desligado"}</span>
        ${hs.ativo ? esc(hs.ssid || "") : ""}</span></div>
      <label>Interface do hotspot</label>
      <select id="hsIf">${opts}</select>
      <label>Nome da rede (SSID)</label>
      <input id="hsSsid" placeholder="Nullgate" value="${esc(hs.ssid || "Nullgate")}">
      <label>Senha (mín. 8)</label>
      <input id="hsPass" placeholder="mínimo 8 caracteres">
      <div class="warn" style="margin-top:10px">Criar hotspot na <b>wlan0</b> derruba a conexão atual do Card (normal em campo).</div>
      <div class="row" style="margin-top:10px">
        <button class="btn" id="hsOn">Criar / Ligar</button>
        <button class="btn danger" id="hsOff">Desligar</button>
      </div>
      <div id="hsOut"></div>
    </div>`;
  bindIface();
  $("#themeSel").onchange = (e) => { applyTheme(e.target.value); toast("tema: " + e.target.value); };
  $("#hsOn").onclick = async () => {
    const r = await post("/api/hotspot/start", {
      iface: $("#hsIf").value, ssid: $("#hsSsid").value, password: $("#hsPass").value });
    $("#hsOut").innerHTML = r.ok
      ? `<div class="okbox" style="margin-top:10px">✅ ${esc(r.msg)}<br>${esc(r.dica || "")}</div>`
      : `<div class="warn" style="margin-top:10px">${esc(r.error)}</div>`;
    if (r.ok) toast("hotspot no ar");
  };
  $("#hsOff").onclick = async () => { const r = await post("/api/hotspot/stop"); toast(r.msg || "ok"); viewConfig(); };
}

/* ---------------- Sistema ---------------- */
async function viewSystem() {
  $("#view").innerHTML = `<div class="center"><span class="spin"></span>lendo o Card…</div>`;
  const s = await api("/api/status");
  const sw = s.software, hw = s.hardware, pw = s.energia;
  const kv = (k, v) => `<div class="kv"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>`;
  const ifaces = s.interfaces.map((i) => {
    const on = i.estado === "up" || i.ip !== "-";
    return `<div class="kv"><span class="k">${esc(i.nome)} <span class="muted">${esc(i.papel)}</span></span>
      <span class="v"><span class="pill ${on ? "on" : "off"}">${esc(i.modo)}</span> ${esc(i.ip)}</span></div>`;
  }).join("");
  $("#view").innerHTML = `
    <div class="card"><h3>Software</h3>${kv("Nullgate", "v" + sw.nullgate)}${kv("Sistema", sw.os)}
      ${kv("Kernel", sw.kernel + " (" + sw.arch + ")")}${kv("Host", sw.hostname)}${kv("Ligado há", sw.uptime)}</div>
    <div class="card"><h3>Hardware</h3>${kv("Modelo", hw.modelo)}${kv("CPU", hw.cpu_cores + " núcleos")}
      ${kv("Temperatura", hw.temp_c != null ? hw.temp_c + " °C" : "—")}${kv("Disco", hw.disco)}
      <div class="kv"><span class="k">RAM</span><span class="v">${hw.ram_livre_mb} / ${hw.ram_total_mb} MB</span></div>
      <div class="bar"><i style="width:${hw.ram_uso_pct ?? 0}%"></i></div></div>
    <div class="card"><h3>Energia</h3>${kv("Bateria", pw.bateria_pct != null ? pw.bateria_pct + "%" : "—")}
      ${kv("Estado", pw.status || "—")}${kv("Tensão", pw.tensao_v != null ? pw.tensao_v + " V" : "—")}
      ${pw.bateria_pct != null ? `<div class="bar"><i style="width:${pw.bateria_pct}%"></i></div>` : ""}</div>
    <div class="card"><h3>Interfaces</h3>${ifaces}
      ${s.usb_wifi.length ? kv("Adaptador USB", s.usb_wifi[0]) : '<div class="muted" style="margin-top:8px">sem adaptador USB</div>'}</div>
    <button class="btn sec" onclick="viewSystem()">Atualizar</button>`;
}

boot();
