"use strict";
const $ = (s, el = document) => el.querySelector(s);
const api = async (path, opts) => (await fetch(path, opts)).json();
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) });
const esc = (s) => (s || "").toString().replace(/[&<>"]/g,
  (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));

const ICON = { wifi: "📶", bt: "🔵", cpu: "🖥️", network: "🌐", config: "⚙️", info: "ℹ️", files: "📁" };
let CAT = null, SCAN = [], CURRENT = "wifi", capTimer = null, feedTimer = null, capturing = false;
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

function applyBg(url) {
  const h = document.documentElement;
  if (url) { h.style.setProperty("--bgimg", `url("${url}?t=${Date.now()}")`); h.classList.add("hasbg"); }
  else { h.classList.remove("hasbg"); h.style.removeProperty("--bgimg"); }
}
async function loadBg() {
  try { const r = await api("/api/background"); applyBg(r.url); } catch (e) {}
}
loadBg();

function toast(m){const t=$("#toast");t.textContent=m;t.classList.add("show");
  setTimeout(()=>t.classList.remove("show"),2300);}

/* ===== FX hacker ===== */
const HX = "0123456789abcdef";
const rnd = (a, b) => Math.floor(Math.random() * (b - a + 1)) + a;
const pick = (a) => a[rnd(0, a.length - 1)];
const hx = (n) => Array.from({ length: n }, () => HX[rnd(0, 15)]).join("");
const rmac = () => Array.from({ length: 6 }, () => hx(2)).join(":");
function fxGarbage() {
  const kind = pick(["BEACON", "PROBE-REQ", "PROBE-RSP", "EAPOL", "AUTH", "ASSOC", "DATA", "QOS", "RTS/CTS", "DEAUTH"]);
  return `[0x${hx(4)}] ${rmac()} ch${rnd(1, 11)} ${rnd(-92, -38)}dBm  ${kind}  seq=${rnd(0, 4095)}`;
}
function fxRun(title, estMs, feed) {
  const ov = document.createElement("div"); ov.className = "fx";
  ov.innerHTML = `<div class="fx-box">
    <div class="fx-title">${esc(title)}<span class="cur">█</span></div>
    <div class="fx-con" id="fxcon"></div>
    <div class="fx-bar"><i id="fxbar"></i></div>
    <div class="fx-pct" id="fxpct">0%</div></div>`;
  document.body.appendChild(ov);
  const con = ov.querySelector("#fxcon"), bar = ov.querySelector("#fxbar"), pct = ov.querySelector("#fxpct");
  const push = (txt, cls) => { const d = document.createElement("div");
    d.className = "fxl " + (cls || ""); d.textContent = txt; con.appendChild(d);
    while (con.children.length > 40) con.removeChild(con.firstChild);
    con.scrollTop = con.scrollHeight; };
  const f = (feed || []).slice(); let fi = 0, warned = false; const t0 = Date.now();
  const tick = setInterval(() => {
    const el = Date.now() - t0;
    let p;
    if (el < estMs) {
      p = (el / estMs) * 96;
    } else {
      // passou do estimado: nao trava — sobe bem devagar e avisa que ta rodando
      p = Math.min(99, 96 + (el - estMs) / 1000 * 0.35);
      if (!warned && el > estMs + 1500) { push("» ainda processando… (adaptador lento, aguarde)", "ok"); warned = true; }
    }
    bar.style.width = p + "%"; pct.textContent = Math.floor(p) + "%";
    if (fi < f.length && Math.random() < 0.45) push("» " + f[fi++], "ok");
    push(fxGarbage()); if (Math.random() < 0.5) push(fxGarbage());
  }, 85);
  // limite de seguranca: nunca fica preso pra sempre
  const safety = setTimeout(() => { clearInterval(tick);
    push("‼ demorou demais — tenta o 🛟 Reset", "err"); setTimeout(() => ov.remove(), 1800); }, 80000);
  return {
    done: (msg) => { clearTimeout(safety); clearInterval(tick); bar.style.width = "100%"; pct.textContent = "100%";
      push("✔ " + (msg || "concluído"), "done");
      setTimeout(() => ov.remove(), 650); },
    fail: (msg) => { clearTimeout(safety); clearInterval(tick); push("‼ " + (msg || "erro"), "err");
      setTimeout(() => ov.remove(), 1100); },
  };
}

async function boot() {
  CAT = await api("/api/catalog");
  $("#ver").textContent = "v" + CAT.version;
  await loadIfaces();
  const secs = [
    { id: "wifi", title: "WiFi", icon: "wifi" },
    { id: "bt", title: "Bluetooth", icon: "bt" },
    { id: "rede", title: "Rede", icon: "network" },
    { id: "files", title: "Arquivos", icon: "files" },
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
  if (capTimer) { clearInterval(capTimer); capTimer = null; } if (feedTimer) { clearInterval(feedTimer); feedTimer = null; }
  if (capturing) { capturing = false; post("/api/wifi/capture/stop").catch(() => {}); }  // nao deixa a placa presa
  ({ wifi: viewWifi, bt: viewBt, rede: viewRede, files: viewFiles, sistema: viewSystem, config: viewConfig }[sec] || viewWifi)();
}

/* ---------------- WiFi ---------------- */
function viewWifi() {
  $("#view").innerHTML = `
    <div class="warn">⚠️ Auditoria. Só nas suas redes ou com autorização.</div>
    ${ifaceBar()}
    <div class="row">
      <button class="btn" id="btnScan">Escanear redes</button>
      <button class="btn sec" id="btnSaved" style="max-width:30%">Salvas</button>
      <button class="btn danger" id="btnResetW" style="max-width:30%" title="destravar adaptador">🛟 Reset</button>
    </div>
    <div id="scanList" class="menu"></div>`;
  bindIface();
  $("#btnScan").onclick = doScan;
  $("#btnSaved").onclick = viewSaved;
  $("#btnResetW").onclick = async () => {
    const fx = fxRun("RESET " + IFACE, 16000, [
      "cortando energia do USB…", "aguardando reconexão…",
      "re-energizando adaptador…", "voltando ao modo managed…"]);
    const r = await post("/api/wifi/reset", { iface: IFACE });
    r.ok ? fx.done(r.msg || "resetado") : fx.fail(r.error || "falha");
  };
  if (SCAN.length) renderScan();
}
async function doScan() {
  const fx = fxRun("SCAN 2.4GHz · " + IFACE, 12500, [
    "levantando rádio " + IFACE + "…", "modo monitor: ON",
    "pulando canais 1→13…", "capturando beacons…",
    "coletando probe requests…", "mapeando BSSIDs…", "agrupando ESSIDs…"]);
  const r = await post("/api/wifi/scan", { seconds: 12, iface: IFACE });
  if (!r.ok) { fx.fail(r.error || "falha no scan"); return; }
  SCAN = r.networks || [];
  fx.done(SCAN.length + " redes encontradas");
  setTimeout(renderScan, 680);
}
function renderScan() {
  const el = $("#scanList");
  if (!SCAN.length) { el.innerHTML = `<div class="empty">nenhuma rede</div>`; return; }
  el.innerHTML = SCAN.map((n, i) => `
    <div class="item ${n.meu ? "mine" : ""}" data-i="${i}">
      <div class="it-main">
        <div class="it-t">${esc(n.essid)} ${n.meu ? '<span class="tag">minha</span>' : ""} ${n.tem_wpa3 ? '<span class="tag" style="color:#36e2a0;border-color:#36e2a0">WPA3</span>' : ""}</div>
        <div class="it-s">${n.qtd_aps} AP · ch ${esc(n.canais.join(",") || "?")} · ${esc(n.seguranca || "")}</div>
      </div><div class="chev">capturar ›</div>
    </div>`).join("");
  el.querySelectorAll(".item").forEach((it) =>
    it.onclick = () => openNet(SCAN[it.dataset.i]));
}

/* ---------------- Detalhes da rede + recon ---------------- */
function wpsBadge(w) {
  if (w === "aberto") return '<span class="pill" style="color:var(--warn);border-color:var(--warn)">WPS aberto ⚠️</span>';
  if (w === "travado") return '<span class="pill on">WPS travado</span>';
  if (w === "nao") return '<span class="pill off">sem WPS</span>';
  return '<span class="pill off">WPS ?</span>';
}
let NET_TARGET = null;  // bssid do AP escolhido, ou null = todos
function normAps(net) {
  const src = net.aps || net.bssids || [];
  return src.map((a) => typeof a === "string"
    ? { bssid: a, canal: (net.canais || [])[0] || "", seguranca: net.seguranca || "?", wpa3: !!net.tem_wpa3 }
    : a);
}
function openNet(net, saved) {
  const r = saved || null;
  NET_TARGET = null;
  const aps = normAps(net);
  const wpa3 = net.tem_wpa3 || aps.some((a) => a.wpa3);
  const apOpts = `<option value="">Todos os APs (${aps.length})</option>` +
    aps.map((a) => `<option value="${a.bssid}">${esc(a.bssid)} · ch${esc(a.canal)} · ${esc(a.seguranca)}${a.wpa3 ? " 🛡️" : ""}</option>`).join("");
  $("#view").innerHTML = `
    <button class="btn sec" onclick="go('wifi')" style="margin-bottom:10px">‹ voltar</button>
    <div class="card">
      <h3>${esc(net.essid)} ${net.meu ? '<span class="tag">minha</span>' : ""}</h3>
      <div class="kv"><span class="k">Pontos de acesso</span><span class="v">${aps.length}</span></div>
      <div class="kv"><span class="k">Canais</span><span class="v">${esc((net.canais||[]).join(", ") || "?")}</span></div>
      <div class="kv"><span class="k">Segurança</span><span class="v">${esc(net.seguranca || "?")} ${wpa3 ? '<span class="pill on">WPA3 🛡️</span>' : ""}</span></div>
      <div id="reconExtra">${r ? reconRows(r) : ""}</div>
      <div id="analyzeOut" class="muted" style="margin-top:8px"></div>
    </div>
    <div class="card"><h3>Ponto de acesso alvo</h3>
      <select id="apSel">${apOpts}</select>
      <div class="muted" id="apNote" style="margin-top:6px"></div>
    </div>
    <div class="card"><h3>Ataques</h3>
      <button class="btn" id="btnAnalyze">🔍 Analisar (recon + WPS)</button>
      <button class="btn" id="btnCapA" style="margin-top:8px">💥 Capturar + Deauth (ativo)</button>
      <button class="btn sec" id="btnCapP" style="margin-top:8px">🎧 Capturar passivo (só escuta)</button>
      <button class="btn danger" id="btnDeauth" style="margin-top:8px">📡 Só Deauth (derrubar)</button>
      <div class="muted" style="margin-top:8px">O modo <b>ativo</b> derruba e captura no mesmo processo. ${wpa3 ? '<b style="color:#ffb454">WPA3 bloqueia deauth (PMF)</b> — mire um AP WPA2 do mesh.' : ""}</div>
    </div>`;
  const apNote = () => {
    const ap = aps.find((a) => a.bssid === NET_TARGET);
    $("#apNote").innerHTML = ap && ap.wpa3
      ? '⚠️ AP WPA3 — deauth provavelmente não funciona (PMF).'
      : (NET_TARGET ? "AP WPA2 — deauth deve funcionar." : "mira todos os APs da rede.");
  };
  $("#apSel").onchange = (e) => { NET_TARGET = e.target.value || null; apNote(); };
  apNote();
  $("#btnAnalyze").onclick = () => analyzeNet(net);
  $("#btnCapA").onclick = () => startCapture(net, "ativo");
  $("#btnCapP").onclick = () => startCapture(net, "passivo");
  $("#btnDeauth").onclick = () => deauthNet(net);
}
function targetBssids(net) {
  const aps = normAps(net);
  return NET_TARGET ? [NET_TARGET] : aps.map((a) => a.bssid);
}
function targetChannels(net) {
  const aps = normAps(net);
  const list = NET_TARGET ? aps.filter((a) => a.bssid === NET_TARGET) : aps;
  return [...new Set(list.map((a) => Number(a.canal)).filter(Boolean))];
}
async function deauthNet(net) {
  const aps = normAps(net);
  const ap = NET_TARGET ? aps.find((a) => a.bssid === NET_TARGET) : aps.find((a) => !a.wpa3) || aps[0];
  if (!ap) { toast("sem BSSID"); return; }
  const bssid = ap.bssid, ch = ap.canal;
  if (ap.wpa3) toast("⚠️ AP WPA3 — deauth pode não funcionar");
  const fx = fxRun("DEAUTH · " + bssid, 10500, [
    "travando canal " + (ch || "?") + "…", "montando quadros 802.11 deauth…",
    "alvo: " + bssid, "injetando broadcast…", "derrubando clientes…",
    "aguardando reconexão…"]);
  const r = await post("/api/wifi/deauth", { bssid, channel: Number(ch) || null, iface: IFACE, seconds: 10 });
  if (!r.ok) { fx.fail(r.error || "falha"); return; }
  fx.done((r.pacotes || 0) + " pacotes enviados");
  setTimeout(() => { $("#analyzeOut").innerHTML =
    `📡 deauth enviado. Agora rode <b>Capturar</b> — os clientes vão reconectar e soltar o handshake.`; }, 680);
}
function deauthBadge(d) {
  if (d === "bloqueado") return '<span class="pill on">imune (WPA3/PMF) 🛡️</span>';
  if (d === "parcial") return '<span class="pill" style="color:#ffb454;border-color:#ffb454">parcial (WPA3)</span>';
  return '<span class="pill" style="color:var(--warn);border-color:var(--warn)">funciona ⚠️</span>';
}
function reconRows(r) {
  return `
    <div class="kv"><span class="k">Cifra</span><span class="v">${esc(r.cifra || "?")} / ${esc(r.auth || "?")}</span></div>
    <div class="kv"><span class="k">WPS</span><span class="v">${wpsBadge(r.wps)}</span></div>
    <div class="kv"><span class="k">Deauth</span><span class="v">${deauthBadge(r.deauth)}</span></div>
    <div class="kv"><span class="k">Clientes</span><span class="v">${r.clientes}</span></div>
    <div class="kv"><span class="k">Captura viável</span><span class="v">${r.captura_viavel ? "✅ sim" : "⚠️ sem cliente"}</span></div>`;
}
async function analyzeNet(net) {
  const fx = fxRun("RECON · " + (net.essid || ""), 21000, [
    "fixando canal " + ((net.canais || [])[0] || "?") + "…",
    "sniffando quadros de gestão…", "lendo RSN/cifra…",
    "sondando WPS (wash)…", "enumerando clientes…",
    "avaliando vetor de ataque…"]);
  const r = await post("/api/wifi/recon", {
    essid: net.essid, bssids: targetBssids(net),
    channels: targetChannels(net), iface: IFACE });
  if (!r.ok) { fx.fail(r.error || "falha"); return; }
  fx.done(r.wps === "aberto" ? "WPS ABERTO detectado!" : (r.seguranca || "ok"));
  setTimeout(() => {
    $("#reconExtra").innerHTML = reconRows(r);
    $("#analyzeOut").innerHTML = r.wps === "aberto"
      ? "<b style='color:var(--warn)'>WPS aberto — vulnerável!</b>"
      : (r.captura_viavel ? "✅ pronto pra capturar handshake." : "⚠️ sem cliente agora; tente mais tarde.");
  }, 680);
  toast("análise salva");
}

/* ---------------- Salvas ---------------- */
async function viewSaved() {
  $("#view").innerHTML = `<div class="center"><span class="spin"></span>carregando…</div>`;
  const r = await api("/api/wifi/saved");
  const redes = r.redes || [];
  $("#view").innerHTML = `
    <button class="btn sec" onclick="go('wifi')" style="margin-bottom:10px">‹ voltar</button>
    <h3 style="color:var(--mut);font-size:14px;text-transform:uppercase;margin:4px 0 10px">Redes analisadas</h3>
    <div class="menu" id="savedList"></div>`;
  const el = $("#savedList");
  if (!redes.length) { el.innerHTML = `<div class="empty">nenhuma rede salva ainda</div>`; return; }
  el.innerHTML = redes.map((n, i) => `
    <div class="item ${n.wps === "aberto" ? "mine" : ""}" data-i="${i}">
      <div class="it-main">
        <div class="it-t">${esc(n.essid)}</div>
        <div class="it-s">${esc(n.seguranca || "?")} · ${n.wps === "aberto" ? "WPS aberto ⚠️" : "WPS " + esc(n.wps || "?")} · ${n.clientes} cli</div>
      </div><div class="chev">›</div>
    </div>`).join("");
  el.querySelectorAll(".item").forEach((it) =>
    it.onclick = () => { const n = redes[it.dataset.i];
      openNet({ essid: n.essid, aps: n.bssids, bssids: n.bssids, canais: n.canais,
        seguranca: n.seguranca, qtd_aps: (n.bssids||[]).length }, n); });
}

/* ---------------- Captura ---------------- */
async function startCapture(net, mode) {
  const r = await post("/api/wifi/capture/start", {
    bssids: targetBssids(net), channels: targetChannels(net),
    iface: IFACE, mode: mode || "ativo", essid: net.essid });
  if (!r.ok) { toast(r.error || "falha"); return; }
  viewCapture(net, mode || "ativo");
}
function viewCapture(net, mode) {
  $("#view").innerHTML = `
    <div class="card">
      <h3>Capturando — ${esc(net.essid)} <span class="muted">(${esc(IFACE)} · ${esc(mode || "ativo")})</span></h3>
      <div class="kv"><span class="k">Tempo</span><span class="v" id="cT">0s</span></div>
      <div class="kv"><span class="k">Capturado</span><span class="v" id="cB">0 KB</span></div>
      <div class="kv"><span class="k">Handshakes</span><span class="v" id="cH">0</span></div>
      <div class="muted" id="cHint" style="margin-top:8px">Aguardando um aparelho reconectar…</div>
      <div class="fx-con" id="capFeed" style="height:120px;margin-top:10px"></div>
      <div class="row" style="margin-top:12px">
        <button class="btn danger" id="btnStop">Parar</button>
        <button class="btn" id="btnExport" disabled>Exportar</button>
      </div>
    </div><div id="exportBox"></div>`;
  $("#btnStop").onclick = stopCapture;
  $("#btnExport").onclick = doExport;
  const feed = $("#capFeed");
  feedTimer = setInterval(() => {
    const d = document.createElement("div"); d.className = "fxl"; d.textContent = fxGarbage();
    feed.appendChild(d); while (feed.children.length > 18) feed.removeChild(feed.firstChild);
    feed.scrollTop = feed.scrollHeight;
  }, 220);
  capturing = true;
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
  capturing = false;
  if (capTimer) { clearInterval(capTimer); capTimer = null; } if (feedTimer) { clearInterval(feedTimer); feedTimer = null; }
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

/* ---------------- Bluetooth ---------------- */
let BT = [];
function viewBt() {
  $("#view").innerHTML = `
    <div class="warn">⚠️ Scan/recon de dispositivos. Sem spam de BLE (não mexe com aparelho de terceiros).</div>
    <button class="btn" id="btnBt">Escanear Bluetooth</button>
    <div id="btList" class="menu"></div>`;
  $("#btnBt").onclick = doBtScan;
  if (BT.length) renderBt();
}
async function doBtScan() {
  const fx = fxRun("BLUETOOTH SCAN", 13000, [
    "ligando rádio BT…", "discovery BR/EDR + LE…",
    "ouvindo advertisements…", "lendo RSSI…", "resolvendo nomes…", "identificando fabricantes…"]);
  const r = await post("/api/bt/scan", { seconds: 12 });
  if (!r.ok) { fx.fail(r.error || "falha"); return; }
  BT = r.dispositivos || [];
  fx.done(BT.length + " dispositivos");
  setTimeout(renderBt, 680);
}
function renderBt() {
  const el = $("#btList");
  if (!BT.length) { el.innerHTML = `<div class="empty">nenhum dispositivo</div>`; return; }
  el.innerHTML = BT.map((d, i) => `
    <div class="item" data-i="${i}">
      <div class="it-main">
        <div class="it-t">${esc(d.nome || "(sem nome)")} ${d.fabricante ? '<span class="tag">'+esc(d.fabricante)+'</span>' : ""}</div>
        <div class="it-s">${esc(d.mac)} · ${esc(d.tipo)}${d.rssi != null ? " · " + d.rssi + " dBm" : ""}</div>
      </div><div class="chev">info ›</div>
    </div>`).join("");
  el.querySelectorAll(".item").forEach((it) => it.onclick = () => btInfo(BT[it.dataset.i]));
}
async function btInfo(d) {
  const fx = fxRun("RECON · " + (d.nome || d.mac), 4000, [
    "conectando SDP/GATT…", "enumerando serviços…", "lendo características…"]);
  const r = await post("/api/bt/info", { mac: d.mac });
  fx.done(r.ok ? "ok" : "falha");
  setTimeout(() => {
    const kv = (k, v) => v ? `<div class="kv"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>` : "";
    $("#view").innerHTML = `
      <button class="btn sec" onclick="go('bt')" style="margin-bottom:10px">‹ voltar</button>
      <div class="card"><h3>${esc(d.nome || d.mac)}</h3>
        ${kv("MAC", r.mac)} ${kv("Nome", r.name)} ${kv("Classe", r.class)}
        ${kv("Tipo", d.tipo)} ${kv("Sinal", d.rssi + " dBm")}
        ${kv("Pareado", r.paired)} ${kv("Conectado", r.connected)}
        <div class="kv"><span class="k">Serviços</span><span class="v">${(r.servicos || []).length}</span></div>
      </div>
      ${(r.servicos || []).length ? `<div class="card"><h3>Serviços (UUIDs)</h3><pre>${esc((r.servicos||[]).join("\n"))}</pre></div>` : ""}`;
  }, 680);
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
    <div class="card"><h3>Fundo personalizado</h3>
      <div class="muted" style="margin-bottom:6px">Imagem ou GIF (png/jpg/gif/webp). Fica salvo no Card.</div>
      <input type="file" id="bgFile" accept="image/*">
      <div class="row" style="margin-top:8px">
        <button class="btn" id="bgUp">Enviar fundo</button>
        <button class="btn danger" id="bgClr">Remover</button>
      </div>
      <div id="bgOut" class="muted" style="margin-top:8px"></div>
    </div>
    <div class="card"><h3>Interface de ataque (padrão)</h3>
      ${ifaceBar()}
      <div class="muted" style="margin-top:6px">Usada no scan e captura. A wlan0 é a interna (conexão); o adaptador USB é o rádio.</div>
      <button class="btn danger" id="btnReset" style="margin-top:10px">🛟 Resetar rádio (destravar)</button>
      <div class="muted" id="resetOut" style="margin-top:6px">Se a placa travar em modo monitor, isso mata tudo e volta ao normal.</div>
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
  $("#btnReset").onclick = async () => {
    $("#resetOut").textContent = "resetando…";
    const r = await post("/api/wifi/reset", { iface: IFACE });
    $("#resetOut").textContent = r.ok ? "✅ " + r.msg : (r.error || "falha");
    toast("rádio resetado");
  };
  $("#themeSel").onchange = (e) => { applyTheme(e.target.value); toast("tema: " + e.target.value); };
  $("#bgUp").onclick = async () => {
    const f = $("#bgFile").files[0];
    if (!f) { toast("escolha um arquivo"); return; }
    $("#bgOut").textContent = "enviando…";
    const fd = new FormData(); fd.append("file", f);
    const r = await (await fetch("/api/background", { method: "POST", body: fd })).json();
    if (r.ok) { applyBg(r.url); $("#bgOut").textContent = "✅ fundo aplicado"; toast("fundo no ar"); }
    else { $("#bgOut").textContent = r.error || "falha"; }
  };
  $("#bgClr").onclick = async () => {
    await post("/api/background/clear"); applyBg(null);
    $("#bgOut").textContent = "fundo removido"; toast("removido");
  };
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

/* ---------------- Arquivos salvos ---------------- */
async function viewFiles() {
  $("#view").innerHTML = `<div class="center"><span class="spin"></span>carregando…</div>`;
  const r = await api("/api/files");
  const fs = r.files || [];
  const human = (b) => b > 1048576 ? (b/1048576).toFixed(1)+" MB" : (b/1024).toFixed(1)+" KB";
  const when = (ts) => new Date(ts*1000).toLocaleString("pt-BR");
  $("#view").innerHTML = `
    <h3 style="color:var(--mut);font-size:14px;text-transform:uppercase;margin:4px 0 10px">Testes salvos</h3>
    <div class="menu" id="filesList"></div>
    <div class="muted" style="margin-top:10px">Handshakes e capturas ficam salvos no Card. Baixe os .hc22000 pra quebrar na GPU.</div>`;
  const el = $("#filesList");
  if (!fs.length) { el.innerHTML = `<div class="empty">nenhum arquivo salvo ainda</div>`; return; }
  el.innerHTML = fs.map((f) => `
    <div class="item">
      <div class="it-main">
        <div class="it-t">${f.tipo === "handshake" ? "🔑" : "📦"} ${esc(f.nome)}</div>
        <div class="it-s">${human(f.bytes)} · ${when(f.ts)}</div>
      </div>
      <div style="display:flex;gap:6px">
        <a class="chev" href="/api/files/download/${encodeURIComponent(f.nome)}" download>baixar</a>
        <span class="chev" data-del="${esc(f.nome)}" style="color:var(--warn)">apagar</span>
      </div>
    </div>`).join("");
  el.querySelectorAll("[data-del]").forEach((b) =>
    b.onclick = async () => { await post("/api/files/delete", { name: b.dataset.del }); viewFiles(); });
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
