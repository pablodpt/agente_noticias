"""Agente de Portafolio.

Modos de uso:
  python agente.py boletin    -> Genera y envía el boletín diario (18:00).
  python agente.py vigilar    -> Una pasada de detección de eventos urgentes.
  python agente.py daemon     -> Bucle continuo: vigila + envía el boletín a su hora.
  python agente.py test       -> Prueba la conexión con Telegram.
  python agente.py preview    -> Imprime el boletín por consola (no envía nada).

  Base de datos local (SQLite) con análisis de correlaciones:
  python agente.py datos                -> Persiste precios/noticias/filings ahora.
  python agente.py datos --bootstrap    -> Rellena la base con años de histórico.
  python agente.py datos --resumen      -> Estado de la base.
  python agente.py datos --importar-raw DIR  -> Importa datos descargados a mano.
  python agente.py analisis             -> Informe completo de correlaciones (consola).
  python agente.py analisis --markdown  -> Y además guarda un .md en informes/.
  python agente.py analisis --enviar    -> Y además envía el informe por Telegram.
"""
import argparse
import html
import logging
import os
import sys
import time
from datetime import datetime, timezone

from config import (DIAS_AVISO_EARNINGS, HORA_BOLETIN, INDICES_CONTEXTO,
                    INTERVALO_VIGILANCIA_SEG, MAX_NOTICIAS_POR_TICKER,
                    MINUTO_BOLETIN, NOMBRES, PESOS, TELEGRAM_CHAT_ID,
                    TELEGRAM_TOKEN, TICKERS, TZ)
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


# ------------------------------------------------------------------ BOLETÍN --

def _persistir_fondo():
    """Nutre la base local para las correlaciones; nunca rompe el boletín."""
    try:
        import datos
        log.info("Base local: %s", datos.persistir_hoy())
    except Exception as e:
        log.warning("No se pudo persistir en la base local: %s", e)


def construir_boletin() -> str:
    log.info("Recopilando datos del portafolio (%s tickers)...", len(TICKERS))
    _persistir_fondo()

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

    # --- Patrones y correlaciones (base de datos local) ---
    try:
        from correlaciones import _resumen_secciones, cargar_contexto
        ctx = cargar_contexto()
        if not ctx.minimo:
            secs = _resumen_secciones(ctx)
            if secs:
                p.append(f"<b>{_esc(secs[0][0])}</b>")
                p.extend(_esc(l) for l in secs[0][1] if l.strip())
                p.append("")
    except Exception as e:
        log.debug("Sección de correlaciones omitida: %s", e)

    p.append(f"<i>{LINEA}\nNo es asesoramiento financiero. Datos: Yahoo Finance, SEC EDGAR, RSS, base local.</i>")
    return "\n".join(p)


def enviar_boletin() -> bool:
    mensaje = construir_boletin()
    ok = enviar_telegram(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, mensaje)
    log.info("Boletín %s", "enviado ✅" if ok else "FALLÓ ❌")
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
    _persistir_fondo()
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
            _persistir_fondo()
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


# ----------------------------------------------------------- DATOS / ANÁLISIS --

def modo_datos(args) -> int:
    import datos

    if args.demo:
        out = datos.generar_demo()
        print("\n✅ Base de datos DEMO generada (SINTÉTICA, para validar el análisis):")
        for k, v in out.items():
            print(f"  {k:<10} {v} filas")
        print("\n⚠️  Los datos son de prueba con estructura de correlación conocida.")
        print("   Para reemplazarlos por datos reales:  python agente.py datos --bootstrap")
        print("   Ver el análisis:                      python agente.py analisis")
        return 0

    if args.resumen:
        r = datos.resumen()
        print("\n📊 ESTADO DE LA BASE DE DATOS LOCAL")
        print(f"Ruta: {datos.ARCHIVO_DATOS}\n")
        p = r.get("precios", {})
        print(f"  {'precios':<10} {p.get('filas', 0):>4} series  "
              f"{p.get('días', 0):>7} sesiones   "
              f"{p.get('desde') or '—'} → {p.get('hasta') or '—'}")
        for tabla in ("noticias", "filings", "earnings"):
            d = r.get(tabla, {})
            print(f"  {tabla:<10} {d.get('filas', 0):>11} filas   "
                  f"{(d.get('desde') or '—')[:10]} → {(d.get('hasta') or '—')[:10]}")
        if r.get("ultimo_persist"):
            print(f"\n  Última persistencia: {r['ultimo_persist']}")
        return 0

    if args.importar_raw:
        if not os.path.isdir(args.importar_raw):
            log.error("%s no es un directorio", args.importar_raw)
            return 1
        c = datos.conn()
        out = datos.importar_raw(c, args.importar_raw)
        c.close()
        print(f"\n📥 Importados {len(out)} ficheros de {args.importar_raw}:")
        for k, v in out.items():
            print(f"  {k}: {v} filas")
        return 0

    if args.bootstrap:
        print("⏳ Rellenando la base con años de histórico (precios, noticias,")
        print("   filings SEC y earnings). Puede tardar varios minutos...\n")
        out = datos.bootstrap()
        print("\n✅ Bootstrap completado:")
        for k, v in out.items():
            print(f"  {k:<10} {v} filas")
        print("\nAhora puedes ver el análisis:  python agente.py analisis")
        return 0

    msg = datos.persistir_hoy(force=args.fuerza)
    print(f"💾 {msg}")
    return 0


def modo_analisis(args) -> int:
    from correlaciones import (cargar_contexto, informe_completo,
                               informe_completo_telegram, validar)

    ctx = cargar_contexto()
    if ctx.n_dias == 0:
        log.error("La base está vacía. Primero ejecuta:  python agente.py datos --bootstrap")
        return 1
    if ctx.n_dias < 40:
        log.warning("Solo hay %s días de precios; algunas secciones estarán limitadas.", ctx.n_dias)

    if args.validar:
        print(validar(ctx))
        return 0

    if args.markdown:
        os.makedirs("informes", exist_ok=True)
        ruta = os.path.join("informes", f"correlaciones_{datetime.now():%Y%m%d_%H%M}.md")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(informe_completo(ctx))
        print(f"📝 Informe guardado en {ruta}\n")

    if args.enviar:
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            log.error("Para --enviar necesitas TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en .env")
            return 1
        ok = enviar_telegram(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, informe_completo_telegram(ctx))
        log.info("Informe de correlaciones %s", "enviado ✅" if ok else "FALLÓ ❌")

    print(informe_completo(ctx))
    return 0


# --------------------------------------------------------------------- CLI --

def main():
    ap = argparse.ArgumentParser(description="Agente de Portafolio → Telegram")
    ap.add_argument("modo", nargs="?", default="boletin",
                    choices=["boletin", "vigilar", "daemon", "test", "preview",
                             "datos", "analisis"])
    ap.add_argument("--bootstrap", action="store_true",
                    help="datos: rellena la base con años de histórico (real)")
    ap.add_argument("--demo", action="store_true",
                    help="datos: rellena la base con un dataset sintético de prueba")
    ap.add_argument("--resumen", action="store_true",
                    help="datos: muestra el estado de la base")
    ap.add_argument("--importar-raw", metavar="DIR",
                    help="datos: importa ficheros descargados a mano (JSON/RSS)")
    ap.add_argument("--fuerza", action="store_true",
                    help="datos: fuerza la persistencia aunque sea reciente")
    ap.add_argument("--enviar", action="store_true",
                    help="analisis: envía el informe por Telegram")
    ap.add_argument("--markdown", action="store_true",
                    help="analisis: guarda el informe en informes/*.md")
    ap.add_argument("--validar", action="store_true",
                    help="analisis: valida contra la estructura conocida del demo")
    args = ap.parse_args()

    if args.modo == "datos":
        sys.exit(modo_datos(args))
    elif args.modo == "analisis":
        sys.exit(modo_analisis(args))
    elif args.modo == "preview":
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
