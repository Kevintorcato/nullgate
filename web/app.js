"use strict";
const $ = (s, el = document) => el.querySelector(s);
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  return r.json();
};
const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) });
const esc = (s) => (s || "").toString().replace(/[&<>"]/g,
  (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));

const ICON = { wifi: "📶", cpu: "🖥️", network: "🌐", info: "ℹ️" };
let CAT = null, SCAN = [], CURRENT = "wifi", capTimer = null;

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2200);
}

async function boot() {
  CAT = await api("/api/catalog");
  $("#ver").textContent = "v" + CAT.version;
  const tabs = $("#tabs");
  const secs = [...CAT.sections, { id: "sistema", title: "Sistema", icon: "cpu" }]
    .filter((s, i, a) => a.findIndex((x) => x.id === s.id) === i);
  tabs.innerHTML = secs.map((s) =>
    `<button data-sec="${s.id}"><span class="ic">${ICON[s.icon] || "•"}</span>${esc(s.title)}</button>`).join("");
  tabs.querySelectorAll("button").forEach((b) =>
    b.onclick = () => go(b.dataset.sec));
  go("wifi");
}

function go(sec) {
  CURRENT = sec;
  $("#tabs").querySelectorAll("button").forEach((b) =>
    b.classList.toggle("on", b.dataset.sec === sec));
  if (capTimer) { clearInterval(capTimer); capTimer = null; }
  if (sec === "wifi") viewWifi();
  else if (sec === "sistema") viewSystem();
  else viewActions(sec);
}

/* ---------------- WiFi ---------------- */
function viewWifi() {
  $("#view").innerHTML = `
    <div class="warnbox">⚠️ Ferramenta de auditoria. Use só nas suas redes ou com autorização.</div>
    <div class="card">
      <h3>Scan de redes</h3>
      <button class="btn" id="btnScan">Escanear</button>
      <div id="scanList"></div>
    </div>`;
  $("#btnScan").onclick = doScan;
  if (SCAN.length) renderScan();
}

async function doScan() {
  const b = $("#btnScan");
  b.disabled = true; b.innerHTML = `<span class="spin"></span>Escaneando ~12s…`;
  const r = await post("/api/wifi/scan", { seconds: 12 });
  b.disabled = false; b.textContent = "Escanear de novo";
  if (!r.ok) { toast(r.error || "falha no scan"); return; }
  SCAN = r.networks || [];
  renderScan();
}

function renderScan() {
  const el = $("#scanList");
  if (!SCAN.length) { el.innerHTML = `<div class="center">nenhuma rede encontrada</div>`; return; }
  el.innerHTML = SCAN.map((n, i) => `
    <div class="net ${n.meu ? "mine" : ""}">
      <div>
        <div class="nm">${esc(n.essid)} ${n.meu ? '<span class="tagmine">minha</span>' : ""}</div>
        <div class="meta">${n.qtd_aps} AP · canais ${esc(n.canais.join(",") || "?")} · ${esc(n.seguranca || "")}</div>
      </div>
      <button class="btn pick" data-i="${i}">Capturar</button>
    </div>`).join("");
  el.querySelectorAll(".pick").forEach((b) =>
    b.onclick = () => startCapture(SCAN[b.dataset.i]));
}

/* ---------------- Captura ---------------- */
async function startCapture(net) {
  const r = await post("/api/wifi/capture/start", {
    bssids: net.aps, channels: net.canais.map(Number).filter(Boolean),
  });
  if (!r.ok) { toast(r.error || "falha"); return; }
  viewCapture(net);
}

function viewCapture(net) {
  $("#view").innerHTML = `
    <div class="card">
      <h3>Capturando — ${esc(net.essid)}</h3>
      <div class="kv"><span class="k">Tempo</span><span class="v" id="cT">0s</span></div>
      <div class="kv"><span class="k">Capturado</span><span class="v" id="cB">0 KB</span></div>
      <div class="kv"><span class="k">Handshakes</span><span class="v" id="cH">0</span></div>
      <div class="muted" id="cHint" style="margin-top:8px">Aguardando um aparelho reconectar na rede…</div>
      <div class="row" style="margin-top:12px">
        <button class="btn danger" id="btnStop">Parar</button>
        <button class="btn" id="btnExport" disabled>Exportar</button>
      </div>
    </div>
    <div id="exportBox"></div>`;
  $("#btnStop").onclick = stopCapture;
  $("#btnExport").onclick = doExport;
  capTimer = setInterval(pollCapture, 3000);
  pollCapture();
}

async function pollCapture() {
  const s = await api("/api/wifi/capture/status");
  if (!s.ok) return;
  $("#cT").textContent = s.segundos + "s";
  $("#cB").textContent = (s.bytes / 1024).toFixed(1) + " KB";
  $("#cH").textContent = s.handshakes;
  const hasHS = s.handshakes > 0;
  $("#btnExport").disabled = !hasHS;
  if (hasHS) $("#cHint").innerHTML = "✅ Handshake capturado! Pode exportar.";
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
  const blob = new Blob([r.conteudo], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = r.arquivo; document.body.appendChild(a); a.click(); a.remove();
  $("#exportBox").innerHTML = `
    <div class="card"><h3>Exportado</h3>
      <div class="kv"><span class="k">Arquivo</span><span class="v">${esc(r.arquivo)}</span></div>
      <div class="kv"><span class="k">Handshakes</span><span class="v">${r.linhas}</span></div>
      <div class="muted" style="margin-top:8px">${esc(r.dica)}</div>
      <pre>${esc(r.conteudo.slice(0, 400))}${r.conteudo.length > 400 ? "\n…" : ""}</pre>
    </div>`;
  toast("baixado — joga na GPU");
}

/* ---------------- Sistema / Info ---------------- */
async function viewSystem() {
  $("#view").innerHTML = `<div class="center"><span class="spin"></span>lendo o Card…</div>`;
  const s = await api("/api/status");
  const sw = s.software, hw = s.hardware, pw = s.energia;
  const kv = (k, v) => `<div class="kv"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>`;
  const ramPct = hw.ram_uso_pct ?? 0;
  const ifaces = s.interfaces.map((i) => {
    const on = i.estado === "up" || i.ip !== "-";
    return `<div class="kv"><span class="k">${esc(i.nome)} <span class="muted">${esc(i.papel)}</span></span>
      <span class="v"><span class="pill ${on ? "on" : "off"}">${esc(i.modo)}</span> ${esc(i.ip)}</span></div>`;
  }).join("");
  $("#view").innerHTML = `
    <div class="card"><h3>Software</h3>
      ${kv("Nullgate", "v" + sw.nullgate)}
      ${kv("Sistema", sw.os)}
      ${kv("Kernel", sw.kernel + " (" + sw.arch + ")")}
      ${kv("Host", sw.hostname)}
      ${kv("Ligado há", sw.uptime)}
    </div>
    <div class="card"><h3>Hardware</h3>
      ${kv("Modelo", hw.modelo)}
      ${kv("CPU", hw.cpu_cores + " núcleos")}
      ${kv("Temperatura", hw.temp_c != null ? hw.temp_c + " °C" : "—")}
      ${kv("Disco", hw.disco)}
      <div class="kv"><span class="k">RAM</span><span class="v">${hw.ram_livre_mb} / ${hw.ram_total_mb} MB livres</span></div>
      <div class="bar"><i style="width:${ramPct}%"></i></div>
    </div>
    <div class="card"><h3>Energia</h3>
      ${kv("Bateria", pw.bateria_pct != null ? pw.bateria_pct + "%" : "—")}
      ${kv("Estado", pw.status || "—")}
      ${kv("Tensão", pw.tensao_v != null ? pw.tensao_v + " V" : "—")}
      ${pw.bateria_pct != null ? `<div class="bar"><i style="width:${pw.bateria_pct}%"></i></div>` : ""}
    </div>
    <div class="card"><h3>Interfaces</h3>${ifaces || '<div class="muted">nenhuma</div>'}
      ${s.usb_wifi.length ? `<div class="kv"><span class="k">Adaptador USB</span><span class="v">${esc(s.usb_wifi[0])}</span></div>` : '<div class="muted" style="margin-top:8px">nenhum adaptador USB WiFi</div>'}
    </div>
    <button class="btn sec" onclick="viewSystem()">Atualizar</button>`;
}

/* ---------------- Ações genéricas (Rede/nmap) ---------------- */
function viewActions(secId) {
  const sec = CAT.sections.find((s) => s.id === secId);
  if (!sec) { $("#view").innerHTML = `<div class="center">seção vazia</div>`; return; }
  $("#view").innerHTML = sec.actions.map((a) => `
    <div class="card">
      <h3>${esc(a.nome)}</h3>
      <div class="muted" style="margin-bottom:6px">${esc(a.desc || "")}</div>
      ${(a.campos || []).map((c) =>
        `<label>${esc(c.label)}</label><input data-f="${esc(c.nome)}" data-a="${esc(a.id)}" placeholder="${esc(c.placeholder || "")}">`).join("")}
      <button class="btn" data-run="${esc(a.id)}">Rodar</button>
      <pre data-out="${esc(a.id)}" style="display:none"></pre>
    </div>`).join("");
  $("#view").querySelectorAll("[data-run]").forEach((b) =>
    b.onclick = () => runAction(secId, b.dataset.run));
}

async function runAction(secId, id) {
  const fields = {};
  document.querySelectorAll(`[data-a="${id}"]`).forEach((i) => fields[i.dataset.f] = i.value);
  const out = $(`[data-out="${id}"]`);
  out.style.display = "block"; out.textContent = "rodando…";
  const r = await post("/api/run", { id, fields });
  out.textContent = r.ok ? (r.stdout || r.stderr || "(sem saída)") : (r.error || "erro");
}

boot();
