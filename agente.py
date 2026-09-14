"""FARO — agente de screening UCITS (no ETF) y, opcionalmente, boletín Telegram.

Modos de uso:
  python agente.py web         -> Lanza el screener (interfaz web).
  python agente.py screener    -> Ranking best-in-class por consola.
  python agente.py portafolio  -> Construye un portafolio (modo/perfil).
  python agente.py preview     -> Boletín UCITS por consola (no envía).
  python agente.py boletin     -> Envía el boletín UCITS por Telegram.
  python agente.py vigilar     -> Alertas de tickers (módulo legado).
  python agente.py daemon      -> Bucle continuo (boletín + vigilancia).
  python agente.py test        -> Prueba la conexión con Telegram.
"""
import argparse
import html
import logging
import sys
import time
from datetime import datetime, timezone

from config import (DIAS_AVISO_EARNINGS, HORA_BOLETIN, INDICES_CONTEXTO,
                    INTERVALO_VIGILANCIA_SEG, MAX_NOTICIAS_POR_TICKER,
                    MINUTO_BOLETIN, NOMBRES, PESOS, TELEGRAM_CHAT_ID,
                    TELEGRAM_TOKEN, TICKERS, TZ)
from ucits.motor import cargar_screener, construir_portafolio
from estado import Estado
from mercado import calendario_earnings, detectar_eventos_precio, snapshot
from noticias import noticias_macro, noticias_portafolio
from sec import presentaciones_portafolio
from sentimiento import ETIQUETAS_ES
from telegram_bot import enviar_telegram, probar_conexion

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("agente")

LINEA = "─" * 24


def _esc(t: str) -> str:
    return html.escape(t or "")


def _nombre(t: str) -> str:
    return NOMBRES.get(t, t)


def _flecha(pct: float) -> str:
    return "🟢" if pct >= 0 else "🔴"


# ---------------------------------------------------------- BOLETÍN UCITS --

def construir_boletin_ucits(modo: str = "automatico", perfil: str = "equilibrado") -> str:
    data = cargar_screener()
    pf = construir_portafolio(modo, perfil)
    r = data["regimen"]
    ahora = datetime.now(TZ)
    p = [
        "<b>🔦 FARO UCITS — boletín</b>",
        f"<i>{ahora.strftime('%d/%m/%Y %H:%M')} ({ahora.tzname()})</i>",
        "",
        f"<b>Régimen:</b> {r['nombre']}",
        _esc(r["resumen"]),
        "",
        f"<b>Portafolio · {pf['nombre']} · {pf['perfil']}</b>",
        f"TER pond. {pf['ter_ponderado']}% · Sharpe 3Y {pf['sharpe_3y_pond']} · 1Y {pf['ret_1y_pond']}%",
        "",
    ]
    for pos in pf["posiciones"]:
        p.append(
            f"• <b>{pos['peso']:.1f}%</b> {_esc(pos['nombre'])} "
            f"<code>{pos['isin']}</code> TER {pos['ter']}%"
        )
    p += ["", "<b>Podio por clase</b>"]
    vistos = set()
    for f in data["fondos"]:
        if f["clase_activo"] in vistos:
            continue
        if not f.get("best_in_class"):
            continue
        vistos.add(f["clase_activo"])
        p.append(
            f"★ {f['clase_activo']}: {_esc(f['nombre'])} "
            f"(score {f['score_calidad']}, TER {f['ter']}%)"
        )
    p.append("")
    p.append("<i>Informativo. No es asesoramiento. Universo UCITS, sin ETF.</i>")
    return "\n".join(p)


def imprimir_screener(top: int = 25):
    data = cargar_screener()
    print(f"Régimen: {data['regimen']['nombre']}")
    print(f"{data['resumen']['n']} fondos · TER medio {data['resumen']['ter_medio']}%")
    print(f"{'SCORE':>6} {'TER':>5} {'1Y':>7} {'SH3':>5} {'DD':>7}  FONDO")
    for f in data["fondos"][:top]:
        print(f"{f['score_final']:6.1f} {f['ter']:5.2f} {f.get('ret_1y') or 0:6.1f}% "
              f"{f.get('sharpe_3y') or 0:5.2f} {f.get('max_dd') or 0:6.1f}%  "
              f"{f['nombre'][:54]}")


# ------------------------------------------------------------------ BOLETÍN --

def construir_boletin() -> str:
    log.info("Recopilando datos del portafolio (%s tickers)...", len(TICKERS))

    snaps = {}
    for t in TICKERS:
        s = snapshot(t)
        if s:
            snaps[t] = s

    noticias = noticias_portafolio(TICKERS)
    filings = presentaciones_portafolio(TICKERS)
    earnings = calendario_earnings(TICKERS)
    macro = noticias_macro()

    ahora = datetime.now(TZ)
    p = [f"<b>💼 BOLETÍN DIARIO DE PORTAFOLIO</b>",
         f"<i>{ahora.strftime('%d/%m/%Y %H:%M')} ({ahora.tzname()})</i>", ""]

    # --- Resumen de mercado ---
    if snaps:
        ordenados = sorted(snaps.values(), key=lambda s: s["cambio_pct"], reverse=True)
        media = sum(s["cambio_pct"] * PESOS.get(s["ticker"], 1) for s in snaps.values()) / \
            max(sum(PESOS.get(t, 1) for t in snaps), 1)
        p.append(f"<b>📊 RENDIMIENTO ({media:+.2f}% ponderado)</b>")
        for s in ordenados:
            extra = ""
            if s["precio"] >= s["max_52w"]:
                extra = "  ⭐ máx. 52s"
            elif s["precio"] <= s["min_52w"]:
                extra = "  ⚠️ mín. 52s"
            p.append(f"{_flecha(s['cambio_pct'])} <b>{s['ticker']}</b> "
                     f"{s['precio']} ({s['cambio_pct']:+.2f}%){extra}")
        p.append("")

    # --- Contexto de índices ---
    ctx = []
    for simbolo, nombre in INDICES_CONTEXTO.items():
        s = snapshot(simbolo)
        if s:
            ctx.append(f"{_flecha(s['cambio_pct'])} {nombre}: {s['precio']} ({s['cambio_pct']:+.2f}%)")
    if ctx:
        p += ["<b>🌍 CONTEXTO DE MERCADO</b>"] + ctx + [""]

    # --- Calendario de resultados ---
    if earnings:
        p.append("<b>📅 CALENDARIO DE RESULTADOS</b>")
        for e in earnings[:12]:
            aviso = " 🔔" if e["dias"] <= DIAS_AVISO_EARNINGS else ""
            cuando = "HOY" if e["dias"] == 0 else (
                "mañana" if e["dias"] == 1 else f"en {e['dias']} días")
            p.append(f"• <b>{e['ticker']}</b> — {e['fecha'].strftime('%d/%m/%Y')} ({cuando}){aviso}")
        p.append("")

    # --- Presentaciones SEC ---
    lineas_sec = []
    for t, lista in filings.items():
        for f in lista[:3]:
            lineas_sec.append(
                f"• <b>{t}</b> {f['descripcion']} — {f['fecha'].strftime('%d/%m')} "
                f"<a href='{f['link']}'>ver</a>")
    if lineas_sec:
        p += ["<b>🏛️ SEC EDGAR (últimos días)</b>"] + lineas_sec[:15] + [""]

    # --- Noticias por ticker ---
    p.append("<b>📰 NOTICIAS RELEVANTES</b>")
    hubo = False
    for t in sorted(TICKERS, key=lambda x: PESOS.get(x, 1), reverse=True):
        lista = [n for n in noticias.get(t, []) if n["sentimiento"] != "neutral" or n["urgente"]]
        lista = lista[:MAX_NOTICIAS_POR_TICKER]
        if not lista:
            continue
        hubo = True
        p.append(f"\n<b>▸ {t} · {_nombre(t)}</b>")
        for n in lista:
            marca = "🚨 " if n["urgente"] else ""
            senal = ETIQUETAS_ES.get(n["sentimiento"], "")
            p.append(f"{marca}{senal} <a href='{n['link']}'>{_esc(n['titulo'][:150])}</a>")
    if not hubo:
        p.append("Sin titulares destacables en las últimas 24 h.")
    p.append("")

    # --- Macro ---
    if macro:
        p.append("<b>🌐 MACRO / MERCADO GENERAL</b>")
        for n in macro:
            p.append(f"• <a href='{n['link']}'>{_esc(n['titulo'][:140])}</a>")
        p.append("")

    p.append(f"<i>{LINEA}\nNo es asesoramiento financiero. Datos: Yahoo Finance, SEC EDGAR, RSS.</i>")
    return "\n".join(p)


def enviar_boletin() -> bool:
    mensaje = construir_boletin_ucits()
    ok = enviar_telegram(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, mensaje)
    log.info("Boletín UCITS %s", "enviado ✅" if ok else "FALLÓ ❌")
    return ok


# ------------------------------------------------------------------ ALERTAS --

def detectar_urgentes(estado: Estado) -> list[str]:
    """Devuelve mensajes de alerta urgentes aún no notificados."""
    alertas = []

    for t in TICKERS:
        s = snapshot(t)
        if not s:
            continue
        eventos = [e for e in detectar_eventos_precio(s) if e["urgente"]]
        for e in eventos:
            if estado.ya_visto(e["clave"]):
                continue
            estado.marcar(e["clave"])
            alertas.append(
                f"<b>🚨 ALERTA — {t} · {_nombre(t)}</b>\n\n"
                f"{e['titulo']}\n{_esc(e['detalle'])}\n\n"
                f"💵 Precio: <b>{s['precio']}</b> ({s['cambio_pct']:+.2f}%)\n"
                f"📐 Rango 52s: {s['min_52w']} – {s['max_52w']}\n"
                f"📈 SMA20 {s['sma20']} · SMA50 {s['sma50']} · SMA200 {s['sma200']}\n"
                f"🔗 <a href='https://finance.yahoo.com/quote/{t}'>Ver en Yahoo Finance</a>"
            )

    # Noticias marcadas como urgentes
    for t, lista in noticias_portafolio(TICKERS).items():
        for n in lista:
            if not n["urgente"] or estado.ya_visto(n["id"]):
                continue
            estado.marcar(n["id"])
            alertas.append(
                f"<b>🚨 NOTICIA URGENTE — {t} · {_nombre(t)}</b>\n\n"
                f"{ETIQUETAS_ES.get(n['sentimiento'], '')} "
                f"<b>{_esc(n['titulo'])}</b>\n\n"
                f"📡 {_esc(str(n['fuente']))}\n"
                f"🔗 <a href='{n['link']}'>Leer noticia</a>"
            )

    # Presentaciones SEC urgentes del día
    for t, lista in presentaciones_portafolio(TICKERS, dias=2).items():
        for f in lista:
            if not f["urgente"] or estado.ya_visto(f["id"]):
                continue
            estado.marcar(f["id"])
            alertas.append(
                f"<b>🏛️ NUEVA PRESENTACIÓN SEC — {t}</b>\n\n"
                f"{f['descripcion']}\nFecha: {f['fecha'].strftime('%d/%m/%Y')}\n"
                f"🔗 <a href='{f['link']}'>Abrir documento</a>"
            )

    # Resultados hoy o mañana
    for e in calendario_earnings(TICKERS):
        if e["dias"] > 1:
            continue
        clave = f"earn:{e['ticker']}:{e['fecha']}"
        if estado.ya_visto(clave):
            continue
        estado.marcar(clave)
        cuando = "HOY" if e["dias"] == 0 else "MAÑANA"
        alertas.append(
            f"<b>📅 RESULTADOS {cuando} — {e['ticker']} · {_nombre(e['ticker'])}</b>\n\n"
            f"Fecha del reporte: {e['fecha'].strftime('%d/%m/%Y')}\n"
            f"Prepárate para volatilidad elevada."
        )

    return alertas


def vigilar_una_vez() -> int:
    estado = Estado()
    alertas = detectar_urgentes(estado)
    for a in alertas:
        enviar_telegram(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, a)
    estado.guardar()
    log.info("Vigilancia: %s alerta(s) enviada(s).", len(alertas))
    return len(alertas)


# ------------------------------------------------------------------- DAEMON --

def daemon():
    log.info("Daemon iniciado. Boletín a las %02d:%02d (%s), vigilancia cada %ss.",
             HORA_BOLETIN, MINUTO_BOLETIN, TZ, INTERVALO_VIGILANCIA_SEG)
    estado = Estado()
    while True:
        try:
            ahora = datetime.now(TZ)
            hoy = ahora.date().isoformat()

            if (ahora.hour, ahora.minute) >= (HORA_BOLETIN, MINUTO_BOLETIN) \
                    and not estado.boletin_enviado_hoy(hoy):
                if enviar_boletin():
                    estado.marcar_boletin(hoy)
                    estado.guardar()

            for a in detectar_urgentes(estado):
                enviar_telegram(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, a)
            estado.guardar()
        except KeyboardInterrupt:
            log.info("Detenido por el usuario.")
            return
        except Exception as e:
            log.exception("Error en el ciclo del daemon: %s", e)
        time.sleep(INTERVALO_VIGILANCIA_SEG)


# --------------------------------------------------------------------- CLI --

def main():
    ap = argparse.ArgumentParser(description="FARO — screener UCITS (no ETF)")
    ap.add_argument("modo", nargs="?", default="web",
                    choices=["web", "screener", "portafolio", "boletin", "vigilar",
                             "daemon", "test", "preview", "preview-tickers"])
    ap.add_argument("--perfil", default="equilibrado",
                    choices=["conservador", "equilibrado", "agresivo"])
    ap.add_argument("--cartera", default="automatico",
                    help="Modo de portafolio: automatico, defensivo, equilibrado, "
                         "crecimiento, anti_inflacion, recesion, expansion, stagflation")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if args.modo == "web":
        import uvicorn
        from servidor import app as webapp
        log.info("FARO en http://%s:%s", args.host, args.port)
        uvicorn.run(webapp, host=args.host, port=args.port, log_level="info")
        return

    if args.modo == "screener":
        imprimir_screener()
        return

    if args.modo == "portafolio":
        pf = construir_portafolio(args.cartera, args.perfil)
        print(f"{pf['nombre']} · {pf['perfil']} · TER {pf['ter_ponderado']}%")
        print(pf["idea"])
        for p in pf["posiciones"]:
            print(f"  {p['peso']:5.1f}%  {p['isin']}  TER {p['ter']:.2f}%  {p['nombre']}")
        return

    if args.modo == "preview":
        print(construir_boletin_ucits(args.cartera, args.perfil))
        return

    if args.modo == "preview-tickers":
        print(construir_boletin())
        return

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Configura TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en el archivo .env")
        sys.exit(1)

    if args.modo == "test":
        sys.exit(0 if probar_conexion(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID) else 1)
    elif args.modo == "boletin":
        sys.exit(0 if enviar_boletin() else 1)
    elif args.modo == "vigilar":
        vigilar_una_vez()
    elif args.modo == "daemon":
        daemon()


if __name__ == "__main__":
    main()
