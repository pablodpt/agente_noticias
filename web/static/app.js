const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  estado: null,
  fondos: [],
  orden: "score_final",
};

const CLASE_LABEL = {
  monetario: "Monetario",
  renta_fija: "Renta fija",
  mixto: "Mixto",
  renta_variable: "Renta variable",
  oro: "Oro / minas",
};

function fmt(n, d = 2, suf = "") {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const s = Number(n).toFixed(d);
  return (n > 0 && suf === "%" ? "+" : "") + s + suf;
}
function clsNum(n) {
  if (n === null || n === undefined) return "";
  return n >= 0 ? "pos" : "neg";
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(path);
  return r.json();
}

function setTab(id) {
  $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === id));
  $$(".view").forEach((v) => v.classList.toggle("on", v.id === "view-" + id));
}

$$("#tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

function renderMeta(est) {
  const vivo = est.nav_vivo ? "VL vivo" : "métricas ancladas a categoría";
  $("#top-meta").innerHTML = `${est.resumen.n} fondos · ${est.resumen.temas} temas · ${vivo}`;
}

function renderDashboard(est, fondos) {
  $("#rc-name").textContent = est.regimen.nombre;
  $("#rc-resumen").textContent = est.regimen.resumen;
  $("#rc-alts").innerHTML = est.regimen.alternativas
    .map((a) => `<button class="chip ${a.id === est.regimen.id ? "on" : ""}" data-reg="${a.id}">${a.nombre}</button>`)
    .join("");
  $$("#rc-alts .chip").forEach((c) =>
    c.addEventListener("click", async () => {
      await api("/api/regimen?id=" + c.dataset.reg);
      await boot(false);
    })
  );

  const r = est.resumen;
  $("#kpis").innerHTML = [
    ["Universo", r.n, "fondos UCITS"],
    ["Temáticas", r.temas, "best-in-class"],
    ["TER medio", r.ter_medio.toFixed(2) + "%", "gastos corrientes"],
    ["Gestoras", r.gestoras, "casas"],
    ["RV / RF / MM", `${r.clases.renta_variable}/${r.clases.renta_fija}/${r.clases.monetario}`, "mix del universo"],
  ]
    .map(([k, v, s]) => `<div class="kpi"><span>${k}</span><b>${v}</b><span>${s}</span></div>`)
    .join("");

  const ind = est.regimen.indicadores || {};
  const bars = [
    ["VIX", ind.vix, 40],
    ["Value − Growth 1Y", (ind.rv_value_1y || 0) - (ind.rv_growth_1y || 0), 30],
    ["EM 1Y", ind.em_1y, 40],
    ["Tech 1Y", ind.tech_1y, 50],
    ["Oro mineras 1Y", ind.oro_mineras_1y, 50],
    ["TIPS 10Y real", ind.tips_10y, 4],
    ["IG euro 1Y", ind.rf_euro_corp_1y, 10],
  ];
  $("#ind-bars").innerHTML = bars
    .map(([lab, val, max]) => {
      const v = val ?? 0;
      const w = Math.min(100, Math.abs(v) / max * 100);
      return `<div class="bar-row"><span>${lab}</span><div class="bar-track"><div class="bar-fill" style="width:${w}%"></div></div><span class="${clsNum(v)}">${fmt(v, 2)}</span></div>`;
    })
    .join("");

  const top = [...fondos].sort((a, b) => (b.score_final || 0) - (a.score_final || 0)).slice(0, 8);
  $("#top-fit").innerHTML = top
    .map(
      (f) => `<div class="row" data-id="${f.id}"><div><div class="fname">${f.nombre}</div><div class="muted">${CLASE_LABEL[f.clase_activo]} · ${f.tema}</div></div><div class="muted">TER ${fmt(f.ter)}%</div><div class="score">${fmt(f.score_final, 1)}</div></div>`
    )
    .join("");
  $$("#top-fit .row").forEach((el) => el.addEventListener("click", () => openFondo(el.dataset.id)));
}

function renderFilters(est) {
  const f = est.filtros;
  $("#filters").innerHTML = `
    <input id="q" placeholder="Buscar nombre, ISIN, gestora…" />
    <select id="f-clase"><option value="">Clase</option>${f.clases.map((c) => `<option value="${c}">${CLASE_LABEL[c] || c}</option>`).join("")}</select>
    <select id="f-tema"><option value="">Tema</option>${f.temas.map((c) => `<option value="${c}">${c}</option>`).join("")}</select>
    <select id="f-gestora"><option value="">Gestora</option>${f.gestoras.map((c) => `<option value="${c}">${c}</option>`).join("")}</select>
    <select id="f-ter"><option value="">TER máx.</option><option value="0.5">≤ 0,50%</option><option value="1">≤ 1,00%</option><option value="1.5">≤ 1,50%</option><option value="2">≤ 2,00%</option></select>
    <select id="f-sh"><option value="">Sharpe 3Y mín.</option><option value="0.3">≥ 0,30</option><option value="0.6">≥ 0,60</option><option value="1">≥ 1,00</option></select>
    <label class="chip" style="padding:8px 10px"><input type="checkbox" id="f-bic" /> Solo podio</label>
  `;
  ["q", "f-clase", "f-tema", "f-gestora", "f-ter", "f-sh", "f-bic"].forEach((id) => {
    $("#" + id).addEventListener("input", loadScreener);
    $("#" + id).addEventListener("change", loadScreener);
  });
}

async function loadScreener() {
  const p = new URLSearchParams();
  const q = $("#q")?.value;
  if (q) p.set("q", q);
  const clase = $("#f-clase")?.value;
  if (clase) p.set("clase", clase);
  const tema = $("#f-tema")?.value;
  if (tema) p.set("tema", tema);
  const g = $("#f-gestora")?.value;
  if (g) p.set("gestora", g);
  const ter = $("#f-ter")?.value;
  if (ter) p.set("max_ter", ter);
  const sh = $("#f-sh")?.value;
  if (sh) p.set("min_sharpe", sh);
  if ($("#f-bic")?.checked) p.set("solo_bic", "true");
  p.set("orden", state.orden);
  const data = await api("/api/screener?" + p.toString());
  const tb = $("#tabla tbody");
  tb.innerHTML = data.fondos
    .map((f) => {
      const est = f.fuente !== "nav_vivo" ? `<span class="pill">est.</span>` : "";
      const bic = f.best_in_class ? `<span class="pill">#1 ${f.tema}</span>` : "";
      return `<tr data-id="${f.id}">
        <td><div class="fname">${f.nombre}</div><div class="fisin">${f.isin} · ${f.gestora} ${est} ${bic}</div></td>
        <td>${CLASE_LABEL[f.clase_activo] || f.clase_activo}<div class="muted">${f.tema} · ${f.estilo}</div></td>
        <td class="num">${fmt(f.ter)}%</td>
        <td class="num ${clsNum(f.ret_1y)}">${fmt(f.ret_1y, 2, "%")}</td>
        <td class="num ${clsNum(f.ret_3y)}">${fmt(f.ret_3y, 2, "%")}</td>
        <td class="num">${fmt(f.sharpe_3y)}</td>
        <td class="num ${clsNum(f.max_dd)}">${fmt(f.max_dd, 1, "%")}</td>
        <td class="num score">${fmt(f.score_final, 1)}</td>
      </tr>`;
    })
    .join("");
  $$("#tabla tbody tr").forEach((tr) => tr.addEventListener("click", () => openFondo(tr.dataset.id)));
}

$$("#tabla thead th").forEach((th, i) => {
  const map = [null, null, "ter", "ret_1y", "ret_3y", "sharpe_3y", "max_dd", "score_final"];
  th.addEventListener("click", () => {
    if (!map[i]) return;
    state.orden = map[i];
    loadScreener();
  });
});

async function renderBic() {
  const data = await api("/api/best-in-class");
  $("#bic-grid").innerHTML = data.grupos
    .map((g) => {
      const items = g.fondos
        .map(
          (f, i) => `<div class="item" data-id="${f.id}"><div class="fname">${i + 1}. ${f.nombre}</div>
          <div class="muted">TER ${fmt(f.ter)}% · Sharpe ${fmt(f.sharpe_3y)} · 1Y ${fmt(f.ret_1y, 1, "%")} · score ${fmt(f.score_calidad, 1)}</div></div>`
        )
        .join("");
      return `<article class="bic-card"><h3>${g.tema}</h3>${items}</article>`;
    })
    .join("");
  $$("#bic-grid .item").forEach((el) => el.addEventListener("click", () => openFondo(el.dataset.id)));
}

async function renderModos(est) {
  const modos = est.filtros.modos;
  $("#sel-modo").innerHTML = modos.map((m) => `<option value="${m.id}">${m.nombre}</option>`).join("");
}

async function buildPortfolio() {
  const modo = $("#sel-modo").value;
  const perfil = $("#sel-perfil").value;
  const pf = await api(`/api/portafolio?modo=${modo}&perfil=${perfil}`);
  $("#pf-summary").innerHTML = [
    ["Modo", pf.nombre, pf.idea],
    ["Fondos", pf.n, "posiciones"],
    ["TER pond.", fmt(pf.ter_ponderado) + "%", "gastos cartera"],
    ["Sharpe 3Y", fmt(pf.sharpe_3y_pond), "ponderado"],
    ["1Y / 3Y", `${fmt(pf.ret_1y_pond, 1)} / ${fmt(pf.ret_3y_pond, 1)}%`, "aprox. no correlacionado"],
  ]
    .map(([k, v, s]) => `<div class="kpi"><span>${k}</span><b>${v}</b><span>${s}</span></div>`)
    .join("");

  const colors = {
    monetario: "#8b97a6",
    renta_fija: "#6ea8ff",
    mixto: "#c4a0ff",
    renta_variable: "#5ee0c5",
    oro: "#d4a84b",
  };
  drawPie(pf.sleeves, colors);
  $("#pf-sleeves").innerHTML = Object.entries(pf.sleeves)
    .filter(([, w]) => w > 0)
    .map(([k, w]) => `<span><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${colors[k]};margin-right:6px"></i>${CLASE_LABEL[k]} ${w}%</span>`)
    .join("");

  $("#pf-pos").innerHTML = pf.posiciones
    .map(
      (p) => `<div class="row" data-id="${p.id}">
        <div><div class="fname">${p.nombre}</div><div class="muted">${p.isin} · ${p.gestora} · ${p.tema}${p.best_in_class ? " · #1 tema" : ""}</div></div>
        <div class="muted">TER ${fmt(p.ter)}%</div>
        <div class="score">${fmt(p.peso, 1)}%</div>
      </div>`
    )
    .join("");
  $$("#pf-pos .row").forEach((el) => el.addEventListener("click", () => openFondo(el.dataset.id)));
}

function drawPie(sleeves, colors) {
  const c = $("#pie");
  const ctx = c.getContext("2d");
  const entries = Object.entries(sleeves).filter(([, w]) => w > 0);
  const total = entries.reduce((a, [, w]) => a + w, 0) || 1;
  const cx = c.width / 2, cy = c.height / 2, r = 118;
  ctx.clearRect(0, 0, c.width, c.height);
  let a = -Math.PI / 2;
  entries.forEach(([k, w]) => {
    const sl = (w / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, r, a, a + sl);
    ctx.closePath();
    ctx.fillStyle = colors[k] || "#888";
    ctx.fill();
    a += sl;
  });
  ctx.beginPath();
  ctx.fillStyle = "#121820";
  ctx.arc(cx, cy, 68, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#e9eef4";
  ctx.font = "500 16px Fraunces, serif";
  ctx.textAlign = "center";
  ctx.fillText("Sleeves", cx, cy + 6);
}

async function openFondo(id) {
  const data = await api("/api/fondo/" + id);
  const f = data.fondo;
  const d = $("#drawer");
  d.hidden = false;
  $("#drawer-panel").innerHTML = `
    <button class="chip" data-close>Cerrar</button>
    <p class="kicker">${CLASE_LABEL[f.clase_activo]} · ${f.tema}</p>
    <h2>${f.nombre}</h2>
    <p class="muted">${f.isin} · ${f.gestora} · ${f.domicilio} · ${f.divisa} · SRI ${f.sri}/7</p>
    <p>${f.tesis || ""}</p>
    <div class="kv">
      <div><span>TER</span><b>${fmt(f.ter)}%</b></div>
      <div><span>Score calidad</span><b>${fmt(f.score_calidad, 1)}</b></div>
      <div><span>Ret 1Y</span><b class="${clsNum(f.ret_1y)}">${fmt(f.ret_1y, 2, "%")}</b></div>
      <div><span>Ret 3Y ann.</span><b class="${clsNum(f.ret_3y)}">${fmt(f.ret_3y, 2, "%")}</b></div>
      <div><span>Ret 5Y ann.</span><b class="${clsNum(f.ret_5y)}">${fmt(f.ret_5y, 2, "%")}</b></div>
      <div><span>Sharpe 3Y</span><b>${fmt(f.sharpe_3y)}</b></div>
      <div><span>Sortino 3Y</span><b>${fmt(f.sortino_3y)}</b></div>
      <div><span>Max drawdown</span><b class="${clsNum(f.max_dd)}">${fmt(f.max_dd, 1, "%")}</b></div>
      <div><span>Vol 3Y</span><b>${fmt(f.vol_3y)}%</b></div>
      <div><span>Calmar 3Y</span><b>${fmt(f.calmar_3y)}</b></div>
      <div><span>Exceso vs cat. 1Y</span><b class="${clsNum(f.exceso_1y)}">${fmt(f.exceso_1y, 2, "%")}</b></div>
      <div><span>Encaje régimen</span><b>${fmt(f.score_regimen, 1)}</b></div>
    </div>
    <p class="fine">Fuente métricas: ${f.fuente === "nav_vivo" ? "valor liquidativo vivo" : "estimado anclado a media de categoría + prima best-in-class − TER"}. Confianza ${fmt((f.confianza || 0) * 100, 0)}%. Rank tema ${f.rank_tema}/${f.n_tema}.</p>
    <h3>Pares del mismo tema</h3>
    ${(data.peers || []).map((p) => `<div class="row" style="cursor:pointer" data-id="${p.id}"><div>${p.nombre}</div><div class="score">${fmt(p.score_calidad, 1)}</div></div>`).join("") || "<p class='muted'>Sin pares</p>"}
  `;
  $$("[data-close]", d).forEach((b) => b.addEventListener("click", () => (d.hidden = true)));
  $$("#drawer-panel [data-id]").forEach((el) => el.addEventListener("click", () => openFondo(el.dataset.id)));
}
$("#drawer .drawer-bg")?.addEventListener("click", () => ($("#drawer").hidden = true));
document.addEventListener("click", (e) => {
  if (e.target.dataset && e.target.dataset.close !== undefined) $("#drawer").hidden = true;
});

$("#btn-build").addEventListener("click", buildPortfolio);

async function boot(first = true) {
  const est = await api("/api/estado");
  state.estado = est;
  renderMeta(est);
  const scr = await api("/api/screener?orden=score_final");
  state.fondos = scr.fondos;
  renderDashboard(est, scr.fondos);
  if (first) {
    renderFilters(est);
    renderModos(est);
    await loadScreener();
    await renderBic();
    await buildPortfolio();
  } else {
    await loadScreener();
    await renderBic();
  }
}

boot().catch((e) => {
  document.body.insertAdjacentHTML("beforeend", `<p style="padding:20px;color:#ff6b81">Error cargando FARO: ${e}</p>`);
});
