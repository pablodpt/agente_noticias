"""Generación de informes: Markdown, HTML y resumen para Telegram."""
from __future__ import annotations

import html as html_lib
import os
from datetime import datetime

from config import DIR_INFORMES, INFORME_PUBLICO, PESOS_ESTRATEGIA, SECTORES_ES
from estrategias import MOTORES as MOTORES_ORDENADOS
from scanner import Idea, Resultado

_ICONOS = {"momentum": "📈", "reversion": "🎯", "valor": "💎", "catalizador": "⚡"}
_DESCARTE = {
    "precio_bajo": "precio por debajo del mínimo",
    "iliquido": "poco volumen negociado",
    "historial_corto": "historial insuficiente",
    "sin_indicadores": "sin datos de precios",
}


# ------------------------------------------------------------------ Formato --

def _n(valor, decimales: int = 2, sufijo: str = "") -> str:
    if isinstance(valor, bool) or valor is None:
        return "—"
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return "—"
    if v != v:
        return "—"
    return f"{v:,.{decimales}f}{sufijo}".replace(",", "X").replace(".", ",").replace("X", ".")


def _p(valor, decimales: int = 2, signo: bool = True) -> str:
    """Porcentaje con formato español (coma decimal)."""
    if isinstance(valor, bool) or valor is None:
        return "—"
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return "—"
    if v != v:
        return "—"
    texto = f"{v:+.{decimales}f}" if signo else f"{v:.{decimales}f}"
    return texto.replace(".", ",") + "%"


def _esc(texto) -> str:
    return html_lib.escape(str(texto if texto is not None else ""))


def _sector(nombre: str) -> str:
    return SECTORES_ES.get(nombre, nombre or "—")


def _cuando(fecha: datetime) -> str:
    return fecha.strftime("%d/%m/%Y %H:%M")


def _clase(idea: Idea) -> str:
    if idea.puntos >= 70:
        return "alta"
    if idea.puntos >= 58:
        return "media"
    return "baja"


def _titulo(res: Resultado) -> str:
    return f"Oportunidades · {res.universo} · informe {res.modo}"


# ------------------------------------------------------------------ Markdown --

def _metricas(idea: Idea) -> list[tuple[str, str]]:
    ind, fund = idea.ind or {}, idea.fund or {}
    filas = [
        ("Precio", f"{_n(ind.get('precio'))} ({_p(ind.get('cambio_1d'))})"),
        ("RSI 14", _n(ind.get("rsi14"), 0)),
        ("12 meses", _p(ind.get("ret_252d"))),
        ("6 meses", _p(ind.get("ret_126d"))),
        ("Fuerza rel. 6m", _p(ind.get("rs_126d")) if ind.get("rs_126d") is not None else "—"),
        ("Volatilidad", _n(ind.get("volatilidad"), 0, "%")),
        ("Máx. 52s", _p(ind.get("dist_max52_pct"), 1)),
    ]
    if fund:
        filas += [
            ("PER (fwd)", _n(fund.get("pe_fwd"), 1)),
            ("ROE", _n(fund.get("roe"), 1, "%")),
            ("Margen neto", _n(fund.get("margen_neto"), 1, "%")),
            ("Ingresos", _p(fund.get("crecimiento_ingresos"), 1)),
            ("FCF yield", _n(fund.get("fcf_yield"), 1, "%")),
            ("Potencial", _p(fund.get("potencial_pct"), 1)),
        ]
    return filas


def markdown(res: Resultado) -> str:
    r = res.regimen or {}
    lineas = [
        f"# 🔎 Informe de oportunidades · {res.universo}",
        "",
        f"**Modo:** {res.modo} · **Generado:** {_cuando(res.generado)} · "
        f"**Duración:** {_n(res.duracion_seg, 0)} s",
        "",
        "## 🧭 Contexto de mercado",
        "",
    ]
    if r.get("precio"):
        estado = "por encima" if r.get("sobre_sma200") else "por debajo"
        lineas.append(f"- **{res.universo.split(' + ')[0]} / referencia ({_ref(res)})**: "
                      f"{_n(r.get('precio'))} puntos "
                      f"({_p(r.get('cambio_1d'))} hoy, {_p(r.get('ret_252d'))} en 12 meses)")
        if r.get("sma200"):
            lineas.append(f"- Media de 200 sesiones: {_n(r.get('sma200'))} → el índice está "
                          f"**{estado}** de ella")
    if r.get("miedo"):
        lineas.append(f"- VIX (índice de miedo): {_n(r.get('miedo'))}")
    lineas += [
        f"- **Régimen detectado:** {r.get('etiqueta', 'desconocido')} → "
        f"pesos ajustados: "
        + ", ".join(f"{k} ×{v:g}" for k, v in (r.get("ajuste") or {}).items()),
        "",
        f"Universo analizado: **{res.total_universo}** valores · con datos: "
        f"**{res.con_datos}** · tras filtros: **{res.analizados}**",
        "",
    ]
    descartes = ", ".join(f"{v} por {_DESCARTE.get(k, k)}" for k, v in res.descartes.items() if v)
    if descartes:
        lineas += [f"Descartados: {descartes}.", ""]

    if not res.ideas:
        lineas += ["## Resultado", "",
                   "Ningún valor ha superado el umbral mínimo en esta pasada. "
                   "No pasa nada: a veces el mejor movimiento es no hacer nada.", ""]
    else:
        lineas += [f"## 🏆 Top {len(res.ideas)} oportunidades", "",
                   "| # | Pts | Ticker | Nombre | Sector | Precio | Motores | Horizonte |",
                   "|---:|---:|---|---|---|---|---|---|"]
        for i, idea in enumerate(res.ideas, 1):
            motores = " ".join(_ICONOS.get(m, "•") for m in idea.motores)
            lineas.append(
                f"| {i} | **{idea.puntos:.0f}** | [{idea.ticker}]({idea.enlace}) | "
                f"{idea.nombre} | {_sector(idea.sector)} | {_n(idea.precio)} | {motores} | "
                f"{idea.horizonte} |")
        lineas.append("")

        for i, idea in enumerate(res.ideas, 1):
            banderas = " · ".join(
                f"{_ICONOS.get(s.motor, '•')} {s.etiqueta} ({s.puntos:.0f})"
                for s in sorted(idea.senales, key=lambda x: -x.puntos))
            lineas += [f"### {i}. {idea.ticker} · {idea.nombre} — {idea.puntos:.0f}/100",
                       "",
                       f"**Sector:** {_sector(idea.sector)} · **Confianza:** "
                       f"{idea.confianza * 100:.0f}% · **Horizonte:** {idea.horizonte}",
                       "",
                       f"**Señales:** {banderas}",
                       ""]
            if idea.ya_avisada:
                lineas += ["> 🔕 Ya avisada en los últimos días (no se reenvía por Telegram).", ""]
            if idea.razones:
                lineas += ["**Por qué aparece:**"] + [f"- {x}" for x in idea.razones] + [""]
            if idea.riesgos:
                lineas += ["**Riesgos:**"] + [f"- ⚠️ {x}" for x in idea.riesgos] + [""]
            lineas += ["| Métrica | Valor |", "|---|---|"]
            lineas += [f"| {k} | {v} |" for k, v in _metricas(idea)]
            lineas += ["", f"[Ver {idea.ticker} en Yahoo Finance]({idea.enlace})", ""]

        lineas += ["## 🧩 Por motor", ""]
        for motor, (nombre, icono, _) in MOTORES_ORDENADOS.items():
            ideas = [i for i in res.ideas if motor in i.motores]
            if not ideas:
                continue
            lineas.append(f"- {icono} **{nombre}** ({PESOS_ESTRATEGIA.get(motor, 0) * 100:.0f}% "
                          f"del peso): " + ", ".join(f"{i.ticker} ({i.puntos:.0f})" for i in ideas))
        lineas.append("")

    if res.avisos:
        lineas += ["## ⚠️ Avisos"] + [f"- {a}" for a in res.avisos] + [""]

    lineas += [
        "---",
        "",
        "### Cómo leer este informe",
        "",
        "- La **puntuación (0-100)** es la media ponderada de los motores que han dado señal: "
        + ", ".join(f"{v[1]} {k} ({PESOS_ESTRATEGIA.get(k, 0) * 100:.0f}%)"
                    for k, v in MOTORES_ORDENADOS.items()) + ".",
        "- Si varios motores coinciden en el mismo valor, la idea recibe una bonificación.",
        "- Cada idea lleva su **horizonte** orientativo y sus **riesgos**: no son órdenes de compra.",
        "",
        "*Informe automático generado con datos públicos de Yahoo Finance, SEC EDGAR y RSS. "
        "Esto no es asesoramiento financiero.*",
        "",
    ]
    return "\n".join(lineas)


def _ref(res: Resultado) -> str:
    from config import BENCHMARK
    return BENCHMARK


# ---------------------------------------------------------------------- HTML --

_CSS = """
:root{--bg:#0f1117;--card:#171a23;--line:#252a36;--txt:#e6e9ef;--dim:#9aa3b2;
--verde:#2ecc71;--ambar:#f1c40f;--rojo:#e74c3c;--azul:#5b9cf8}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px 64px;background:var(--bg);color:var(--txt);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px}
h2{font-size:19px;margin:34px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.sub{color:var(--dim);font-size:13px;margin-bottom:24px}
.ctx{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.ctx ul{margin:8px 0 0;padding-left:20px;color:var(--dim)}
table{width:100%;border-collapse:collapse;margin:12px 0 4px;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
td.num,th.num{text-align:right}
.idea{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:18px 20px;margin:16px 0}
.idea h3{margin:0 0 4px;font-size:18px}
.badge{display:inline-block;min-width:52px;text-align:center;padding:3px 10px;border-radius:999px;
font-weight:700;font-size:13px;margin-right:8px}
.alta{background:rgba(46,204,113,.18);color:var(--verde)}
.media{background:rgba(241,196,15,.18);color:var(--ambar)}
.baja{background:rgba(231,76,60,.16);color:var(--rojo)}
.meta{color:var(--dim);font-size:13px;margin-bottom:10px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.chip{background:#20242f;border:1px solid var(--line);border-radius:999px;
padding:3px 10px;font-size:12px;color:var(--txt)}
ul.raz,ul.rie{margin:8px 0 0;padding-left:20px}
ul.rie li{color:#f6b7b0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-top:14px}
.kv{background:#11141c;border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.kv b{display:block;color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
a{color:var(--azul)}
.foot{margin-top:40px;color:var(--dim);font-size:12px;border-top:1px solid var(--line);padding-top:14px}
.aviso{background:rgba(241,196,15,.1);border:1px solid rgba(241,196,15,.35);
border-radius:10px;padding:10px 14px;color:#f4d77a;font-size:13px;margin:14px 0}
.vacio{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}
"""


def html(res: Resultado) -> str:
    r = res.regimen or {}
    p = ["<!doctype html><html lang='es'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1'>",
         f"<title>{_esc(_titulo(res))}</title><style>{_CSS}</style></head><body><div class='wrap'>"]
    p.append(f"<h1>🔎 Oportunidades · {_esc(res.universo)}</h1>")
    p.append(f"<div class='sub'>Informe <b>{_esc(res.modo)}</b> · {_esc(_cuando(res.generado))} · "
             f"generado en {_n(res.duracion_seg, 0)} s · {res.total_universo} valores del "
             f"universo, {res.analizados} analizados</div>")

    # contexto
    p.append("<h2>🧭 Contexto de mercado</h2><div class='ctx'><ul>")
    if r.get("precio"):
        estado = "por encima" if r.get("sobre_sma200") else "por debajo"
        p.append(f"<li>Referencia ({_esc(_ref(res))}): <b>{_n(r.get('precio'))}</b> "
                 f"({_p(r.get('cambio_1d'))} hoy · {_p(r.get('ret_252d'))} en 12 meses)</li>")
        if r.get("sma200"):
            p.append(f"<li>Media de 200 sesiones: {_n(r.get('sma200'))} → el índice está "
                     f"<b>{estado}</b></li>")
    if r.get("miedo"):
        p.append(f"<li>VIX (miedo): <b>{_n(r.get('miedo'))}</b></li>")
    p.append(f"<li>Régimen: <b>{_esc(r.get('etiqueta', '—'))}</b> · ajuste de pesos: "
             f"{_esc(', '.join(f'{k} ×{v:g}' for k, v in (r.get('ajuste') or {}).items()))}</li>")
    p.append("</ul></div>")

    if not res.ideas:
        p.append("<h2>Resultado</h2><div class='vacio'>Ningún valor ha superado el umbral "
                 "mínimo en esta pasada. A veces el mejor movimiento es no hacer nada.</div>")
    else:
        p.append(f"<h2>🏆 Top {len(res.ideas)}</h2><table>")
        p.append("<tr><th>#</th><th>Pts</th><th>Ticker</th><th>Nombre</th><th>Sector</th>"
                 "<th class='num'>Precio</th><th>Motores</th><th>Horizonte</th></tr>")
        for i, idea in enumerate(res.ideas, 1):
            motores = " ".join(_ICONOS.get(m, "•") for m in idea.motores)
            p.append(f"<tr><td>{i}</td>"
                     f"<td><span class='badge {_clase(idea)}'>{idea.puntos:.0f}</span></td>"
                     f"<td><a href='{_esc(idea.enlace)}'>{_esc(idea.ticker)}</a></td>"
                     f"<td>{_esc(idea.nombre)}</td><td>{_esc(_sector(idea.sector))}</td>"
                     f"<td class='num'>{_n(idea.precio)}</td><td>{motores}</td>"
                     f"<td>{_esc(idea.horizonte)}</td></tr>")
        p.append("</table>")

        for i, idea in enumerate(res.ideas, 1):
            p.append("<div class='idea'>")
            p.append(f"<h3><span class='badge {_clase(idea)}'>{idea.puntos:.0f}</span>"
                     f"{_esc(idea.ticker)} · {_esc(idea.nombre)}</h3>")
            p.append(f"<div class='meta'>{_esc(_sector(idea.sector))} · confianza "
                     f"{idea.confianza * 100:.0f}% · horizonte {_esc(idea.horizonte)}"
                     f"{' · 🔕 ya avisada' if idea.ya_avisada else ''}</div>")
            p.append("<div class='chips'>" + "".join(
                f"<span class='chip'>{_ICONOS.get(s.motor, '•')} {_esc(s.etiqueta)} "
                f"{s.puntos:.0f}</span>" for s in sorted(idea.senales, key=lambda x: -x.puntos))
                + "</div>")
            if idea.razones:
                p.append("<b>Por qué aparece</b><ul class='raz'>" +
                         "".join(f"<li>{_esc(x)}</li>" for x in idea.razones) + "</ul>")
            if idea.riesgos:
                p.append("<b>Riesgos</b><ul class='rie'>" +
                         "".join(f"<li>{_esc(x)}</li>" for x in idea.riesgos) + "</ul>")
            p.append("<div class='grid'>" + "".join(
                f"<div class='kv'><b>{_esc(k)}</b>{_esc(v)}</div>" for k, v in _metricas(idea))
                + "</div>")
            p.append(f"<div style='margin-top:12px'><a href='{_esc(idea.enlace)}'>"
                     f"Ver {_esc(idea.ticker)} en Yahoo Finance →</a></div>")
            p.append("</div>")

    for aviso in res.avisos:
        p.append(f"<div class='aviso'>⚠️ {_esc(aviso)}</div>")

    p.append("<div class='foot'>Informe automático con datos públicos de Yahoo Finance, "
             "SEC EDGAR y feeds RSS. <b>Esto no es asesoramiento financiero</b>: son ideas "
             "para investigar, no órdenes de compra.</div>")
    p.append("</div></body></html>")
    return "".join(p)


# ------------------------------------------------------------------ Telegram --

def telegram(res: Resultado, max_ideas: int | None = None, solo_nuevas: bool = True) -> str:
    r = res.regimen or {}
    ideas = (res.nuevas if solo_nuevas else res.ideas)
    if max_ideas:
        ideas = ideas[:max_ideas]

    cabe = (f"<b>🔎 OPORTUNIDADES · {_esc(res.universo)}</b>\n"
            f"<i>{_esc(_cuando(res.generado))} · informe {_esc(res.modo)} · "
            f"{res.analizados} valores analizados</i>\n")
    if r.get("precio"):
        cabe += (f"📊 {_esc(_ref(res))} {_n(r.get('precio'))} ({_p(r.get('cambio_1d'))}) · "
                 f"régimen <b>{_esc(r.get('etiqueta', '—'))}</b>\n")

    if not ideas:
        cuerpo = ("\nSin ideas nuevas que superen el umbral en esta pasada. "
                  "Nada que hacer por hoy.")
        return cabe + cuerpo

    trozos = []
    for i, idea in enumerate(ideas, 1):
        lineas = [f"\n<b>{i}. {_esc(idea.ticker)} · {_esc(idea.nombre)}</b> "
                  f"— <b>{idea.puntos:.0f}/100</b>"]
        lineas.append(" ".join(f"{_ICONOS.get(s.motor, '•')}{_esc(s.etiqueta)}"
                               for s in sorted(idea.senales, key=lambda x: -x.puntos))
                      + f" · {_esc(idea.horizonte)}")
        precio = idea.precio
        extra = []
        if precio:
            extra.append(f"💵 {_n(precio)} ({_p(idea.ind.get('cambio_1d'))})")
        fund = idea.fund or {}
        if fund.get("pe_fwd"):
            extra.append(f"PER fwd {_n(fund.get('pe_fwd'), 1)}")
        if fund.get("roe") is not None:
            extra.append(f"ROE {_n(fund.get('roe'), 0, '%')}")
        if extra:
            lineas.append(" · ".join(extra))
        for razon in idea.razones[:3]:
            lineas.append(f"• {_esc(razon)}")
        for riesgo in idea.riesgos[:2]:
            lineas.append(f"⚠️ <i>{_esc(riesgo)}</i>")
        lineas.append(f"🔗 <a href='{_esc(idea.enlace)}'>Ver en Yahoo Finance</a>")
        trozos.append("\n".join(lineas))

    pie = ("\n" + "─" * 22 + "\n<i>Ideas para investigar, no asesoramiento financiero.</i>")
    return cabe + "\n".join(trozos) + pie


# --------------------------------------------------------------- Ficheros --

def guardar(res: Resultado, directorio: str = DIR_INFORMES,
            nombre_base: str | None = None, publico: bool = True) -> dict[str, str]:
    """Escribe el informe en disco. Devuelve las rutas generadas."""
    os.makedirs(directorio, exist_ok=True)
    historico = os.path.join(directorio, "historico")
    os.makedirs(historico, exist_ok=True)

    sello = res.generado.strftime("%Y-%m-%d")
    base = nombre_base or f"oportunidades-{res.modo}-{sello}"
    rutas = {}

    md, ht = markdown(res), html(res)
    for nombre, contenido in ((f"{base}.md", md), (f"{base}.html", ht)):
        ruta = os.path.join(historico, nombre)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(contenido)
        rutas[nombre] = ruta

    if publico:
        for nombre, contenido in ((INFORME_PUBLICO, md),
                                  (INFORME_PUBLICO.replace(".md", ".html"), ht)):
            ruta = os.path.join(directorio, nombre)
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(contenido)
            rutas[nombre] = ruta
    return rutas
