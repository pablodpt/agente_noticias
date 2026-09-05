"""Detector de oportunidades del mercado.

Modos de uso:

    python oportunidades.py preview            # escanea e imprime por consola
    python oportunidades.py diario             # scan rápido + Telegram + informe
    python oportunidades.py semanal            # scan profundo (todos los fundamentales)
    python oportunidades.py escanear --modo semanal --universo mezcla --top 20
    python oportunidades.py selftest           # comprueba la lógica SIN red
    python oportunidades.py universo           # lista el universo configurado
"""
from __future__ import annotations

import argparse
import logging
import sys

from config import (BENCHMARK, DIR_INFORMES, TELEGRAM_CHAT_ID, TELEGRAM_TOKEN,
                    TTL_OPORTUNIDAD_HORAS)
from estado import Estado
from informe import guardar, html, markdown, telegram
from scanner import Fuentes, escanear
from telegram_bot import enviar_telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("oportunidades")

_ICONOS = {"momentum": "📈", "reversion": "🎯", "valor": "💎", "catalizador": "⚡"}


# ------------------------------------------------------------------ Consola --

def imprimir_resumen(resultado, maximo: int = 20) -> None:
    r = resultado.regimen or {}
    print()
    print("═" * 78)
    print(f"  OPORTUNIDADES · {resultado.universo} · informe {resultado.modo}")
    print(f"  {resultado.generado.strftime('%d/%m/%Y %H:%M')} ({resultado.generado.tzname()})"
          f" · {resultado.total_universo} valores · {resultado.analizados} analizados"
          f" · {resultado.duracion_seg:.0f}s")
    if r.get("precio"):
        print(f"  {BENCHMARK}: {r['precio']} · régimen {r.get('etiqueta')}"
              + (f" · VIX {r['miedo']}" if r.get("miedo") else ""))
    print("═" * 78)

    if resultado.avisos:
        print()
        for aviso in resultado.avisos:
            print(f"  ⚠️  {aviso}")

    if not resultado.ideas:
        print("\n  Ningún valor supera el umbral en esta pasada.\n")
        return

    print(f"\n  {'#':>2}  {'PTS':>4}  {'TICKER':<8} {'NOMBRE':<26} {'SECTOR':<20} MOTORES")
    print("  " + "─" * 74)
    for i, idea in enumerate(resultado.ideas[:maximo], 1):
        motores = " ".join(_ICONOS.get(m, "•") for m in idea.motores)
        nombre = (idea.nombre or idea.ticker)[:26]
        sector = (idea.sector or "—")[:20]
        marca = " 🔕" if idea.ya_avisada else ""
        print(f"  {i:>2}  {idea.puntos:>4.0f}  {idea.ticker:<8} {nombre:<26} "
              f"{sector:<20} {motores}{marca}")

    print("\n  Detalle de las 5 primeras:")
    for idea in resultado.ideas[:5]:
        print(f"\n  ▸ {idea.ticker} · {idea.nombre} — {idea.puntos:.0f}/100 "
              f"(confianza {idea.confianza * 100:.0f}%, horizonte {idea.horizonte})")
        for s in sorted(idea.senales, key=lambda x: -x.puntos):
            print(f"      {_ICONOS.get(s.motor, '•')} {s.etiqueta}: {s.puntos:.0f} pts")
        for razon in idea.razones[:4]:
            print(f"      + {razon}")
        for riesgo in idea.riesgos[:2]:
            print(f"      ! {riesgo}")
    print()


# ------------------------------------------------------------------- Acciones --

def _ejecutar(args, modo: str) -> int:
    estado = Estado() if not getattr(args, "repetir", False) else None
    resultado = escanear(
        modo=modo,
        nombre_universo=getattr(args, "universo", None),
        top=getattr(args, "top", None),
        tickers_extra=[t.strip().upper() for t in (getattr(args, "tickers", "") or "").split(",")
                       if t.strip()],
        con_fundamentales=not getattr(args, "sin_fundamentales", False),
        con_catalizadores=not getattr(args, "sin_noticias", False),
        forzar_fundamentales=getattr(args, "forzar_fundamentales", False),
        refrescar_universo=getattr(args, "refrescar_universo", False),
        estado=estado,
        avance=lambda m: log.info("· %s", m),
    )

    imprimir_resumen(resultado)

    if getattr(args, "sin_informe", False):
        rutas = {}
    elif not resultado.con_datos:
        log.warning("Sin datos de precios: no se escribe ningún informe.")
        rutas = {}
    else:
        rutas = guardar(resultado, directorio=getattr(args, "salida", None) or DIR_INFORMES)
        for nombre, ruta in rutas.items():
            log.info("Informe escrito: %s", ruta)

    enviar = not getattr(args, "sin_telegram", False)
    if enviar:
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
            log.error("Faltan TELEGRAM_TOKEN y TELEGRAM_CHAT_ID en el .env: no se envía nada.")
        else:
            maximo = getattr(args, "limite_telegram", None)
            mensaje = telegram(resultado, max_ideas=maximo,
                               solo_nuevas=estado is not None)
            ok = enviar_telegram(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, mensaje)
            if ok and estado is not None:
                for idea in resultado.nuevas[:maximo] if maximo else resultado.nuevas:
                    estado.marcar(idea.clave_estado or f"op:{idea.ticker}",
                                  ttl_horas=TTL_OPORTUNIDAD_HORAS)
                estado.guardar()
            log.info("Telegram: %s", "enviado ✅" if ok else "falló ❌")

    return 0 if resultado.ideas else 2


def _preview(args) -> int:
    """Igual que escanear pero sin Telegram ni deduplicación."""
    args.sin_telegram = True
    return _ejecutar(args, getattr(args, "modo", "semanal"))


def _universo(args) -> int:
    import universo as u
    if getattr(args, "refrescar", False):
        datos = u.sp500(refrescar=True)
        print(f"S&P 500 refrescado: {len(datos)} valores")
    lista, descripcion = u.obtener_universo(getattr(args, "nombre", None))
    print(f"Universo activo: {descripcion} → {len(lista)} valores")
    por_sector: dict[str, int] = {}
    for e in lista:
        por_sector[e["sector"]] = por_sector.get(e["sector"], 0) + 1
    for sector, n in sorted(por_sector.items(), key=lambda x: -x[1]):
        print(f"  {n:>4}  {sector}")
    if getattr(args, "listar", False):
        print("\n" + ", ".join(e["ticker"] for e in lista))
    return 0


# ------------------------------------------------------------------- Selftest --

def _serie(precio_inicial: float, deriva: float, ruido: float, sesiones: int, semilla: int):
    import numpy as np
    rng = np.random.default_rng(semilla)
    pasos = rng.normal(deriva, ruido, sesiones)
    precios = precio_inicial * (1 + pasos).cumprod()
    precios = [precio_inicial] + list(precios)
    return precios


def _datos_sinteticos():
    """Crea un mercado falso con patrones conocidos para probar los motores."""
    import numpy as np
    import pandas as pd

    sesiones = 320
    fechas = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=sesiones)

    def marco(precios, volumen_base=2_000_000, pico_final=1.0):
        if len(precios) < sesiones:
            precios = [precios[0]] * (sesiones - len(precios)) + list(precios)
        rng = np.random.default_rng(abs(hash(tuple(precios[::40]))) % 10_000)
        cierre = np.array(precios[-sesiones:], dtype=float)
        apertura = cierre * (1 + rng.normal(0, 0.004, sesiones))
        alto = np.maximum(cierre, apertura) * (1 + abs(rng.normal(0, 0.005, sesiones)))
        bajo = np.minimum(cierre, apertura) * (1 - abs(rng.normal(0, 0.005, sesiones)))
        volumen = rng.integers(0.7 * volumen_base, 1.4 * volumen_base, sesiones).astype(float)
        volumen[-1] *= pico_final
        df = pd.DataFrame({"Open": apertura, "High": alto, "Low": bajo, "Close": cierre,
                           "Adj Close": cierre, "Volume": volumen}, index=fechas)
        return df

    precios: dict[str, pd.DataFrame] = {}

    # 1) Tendencia fuerte → debe salir por MOMENTUM
    sube = _serie(40, 0.0035, 0.008, sesiones - 1, 1)
    precios["TENDENCIA"] = marco(sube, pico_final=2.2)

    # 2) Tendencia de fondo + caída reciente → debe salir por REVERSIÓN
    caida_dias = 60
    sube_larga = list(_serie(40, 0.0055, 0.006, sesiones - caida_dias, 2))
    caida = list(np.linspace(sube_larga[-1], sube_larga[-1] * 0.79, caida_dias))
    caida[-1] = caida[-2] * 1.02                      # último día rebota
    precios["CAIDA"] = marco(sube_larga + caida[1:], pico_final=3.0)

    # 3) Barata y de calidad → debe salir por VALOR
    plana = _serie(60, 0.0002, 0.009, sesiones - 1, 3)
    precios["VALOR"] = marco(plana)

    # 4) Con noticias, analistas y resultados → debe salir por CATALIZADOR
    suave = _serie(50, 0.0016, 0.007, sesiones - 1, 4)
    df_noticia = marco(suave, pico_final=3.2)
    pos_c = df_noticia.columns.get_loc("Close")
    pos_a = df_noticia.columns.get_loc("Adj Close")
    pos_h = df_noticia.columns.get_loc("High")
    cierre_final = float(df_noticia.iloc[-1, pos_c]) * 1.03
    df_noticia.iloc[-1, pos_c] = cierre_final
    df_noticia.iloc[-1, pos_a] = cierre_final
    df_noticia.iloc[-1, pos_h] = max(float(df_noticia.iloc[-1, pos_h]), cierre_final * 1.004)
    precios["NOTICIA"] = df_noticia

    # 5) Ruido: valores normales que no deberían destacar
    for i in range(56):
        t = f"RUIDO{i:02d}"
        precios[t] = marco(_serie(30 + i, 0.0002, 0.014, sesiones - 1, 100 + i))

    # Referencia: índice con deriva suave
    precios[BENCHMARK] = marco(_serie(300, 0.0006, 0.006, sesiones - 1, 7))
    precios["^VIX"] = marco([15.0] * sesiones, pico_final=1.0)

    return precios


def _fundamentos_sinteticos(tickers: list[str]) -> dict:
    """Fundamentales inventados: VALOR es la mejor empresa del grupo."""
    import random
    out = {}
    for i, t in enumerate(tickers):
        rng = random.Random(t)
        if t == "VALOR":
            out[t] = {"nombre": "Valor Barato SA", "sector": "Tecnología", "pe": 9.5,
                      "pe_fwd": 8.0, "peg": 0.5, "ps": 1.1, "pb": 1.3, "ev_ebitda": 6.0,
                      "roe": 26.0, "roa": 12.0, "margen_bruto": 50.0, "margen_operativo": 22.0,
                      "margen_neto": 18.0, "deuda_capital": 35.0, "liquidez": 2.4,
                      "crecimiento_ingresos": 12.0, "crecimiento_beneficios": 15.0,
                      "fcf_yield": 9.0, "dividendo_yield": 1.0, "payout": 25.0,
                      "potencial_pct": 22.0, "recomendacion": "buy", "analistas": 25,
                      "objetivo": 73.0, "precio": 60.0, "capitalizacion": 30e9}
        elif t == "CAIDA":
            out[t] = {"nombre": "Rebote SA", "sector": "Tecnología", "pe": 22.0, "pe_fwd": 18.0,
                      "peg": 1.6, "ps": 3.0, "pb": 4.0, "ev_ebitda": 14.0, "roe": 18.0,
                      "margen_neto": 12.0, "deuda_capital": 60.0, "liquidez": 1.6,
                      "crecimiento_ingresos": 6.0, "crecimiento_beneficios": 4.0,
                      "fcf_yield": 3.0, "dividendo_yield": 0.0, "payout": 0.0,
                      "potencial_pct": 10.0, "recomendacion": "buy", "precio": 100.0,
                      "capitalizacion": 20e9}
        else:
            out[t] = {"nombre": f"Empresa {t}", "sector": "Tecnología" if i % 3 == 0 else
                      ("Financieras" if i % 3 == 1 else "Salud"),
                      "pe": rng.uniform(18, 40), "pe_fwd": rng.uniform(16, 35),
                      "peg": rng.uniform(1.2, 3.0), "ps": rng.uniform(2, 8),
                      "pb": rng.uniform(2, 10), "ev_ebitda": rng.uniform(12, 30),
                      "roe": rng.uniform(5, 22), "roa": rng.uniform(2, 10),
                      "margen_bruto": rng.uniform(25, 55), "margen_operativo": rng.uniform(5, 20),
                      "margen_neto": rng.uniform(3, 15), "deuda_capital": rng.uniform(40, 160),
                      "liquidez": rng.uniform(1.0, 2.5), "crecimiento_ingresos": rng.uniform(-2, 10),
                      "crecimiento_beneficios": rng.uniform(-5, 12), "fcf_yield": rng.uniform(0, 6),
                      "dividendo_yield": rng.uniform(0, 2.5), "payout": rng.uniform(10, 70),
                      "potencial_pct": rng.uniform(-5, 12), "recomendacion": "hold",
                      "precio": 40.0, "capitalizacion": 8e9}
        out[t]["_ts"] = "2026-01-01T00:00:00+00:00"
    return out


def _catalizadores_sinteticos(tickers: list[str]) -> dict:
    import datetime as dt
    out = {t: {"noticias": [], "filings": [], "upgrades": [], "earnings": None} for t in tickers}
    if "NOTICIA" in out:
        out["NOTICIA"] = {
            "noticias": [
                {"titulo": "La empresa bate previsiones y eleva su guía anual",
                 "sentimiento": "positive", "score": 0.95, "urgente": True,
                 "link": "https://example.com/1", "fuente": "Test"},
                {"titulo": "Un gran fondo abre posición en el valor",
                 "sentimiento": "positive", "score": 0.85, "urgente": False,
                 "link": "https://example.com/2", "fuente": "Test"},
            ],
            "filings": [{"form": "8-K", "descripcion": "Hecho relevante",
                         "fecha": dt.date.today()}],
            "upgrades": [{"accion": "up", "firma": "Banco Test", "desde": "hold",
                          "hasta": "buy", "fecha": dt.datetime.now()}],
            "earnings": {"ticker": "NOTICIA",
                         "fecha": dt.date.today() + dt.timedelta(days=3),
                         "dias": 3},
        }
    return out


def _selftest() -> int:
    """Comprueba el pipeline completo sin tocar la red."""
    print("\n🧪 Selftest del detector de oportunidades (datos sintéticos, sin red)\n")

    precios = _datos_sinteticos()
    fuentes = Fuentes(
        precios=lambda tickers, avance=None: {t: precios[t] for t in tickers if t in precios},
        referencia=lambda: (precios.get(BENCHMARK), precios.get("^VIX")),
        fundamentales=lambda tickers, avance=None, forzar=False: _fundamentos_sinteticos(tickers),
        catalizadores=lambda tickers, avance=None: _catalizadores_sinteticos(tickers),
    )

    logging.getLogger("oportunidades").setLevel(logging.WARNING)
    sectores = ["Tecnología", "Financieras", "Salud", "Industriales", "Energía"]
    meta_extra = {t: {"nombre": f"Empresa {t}",
                      "sector": sectores[i % len(sectores)]}
                  for i, t in enumerate(precios)}
    res = escanear(modo="semanal", nombre_universo="personalizado",
                   tickers_extra=list(precios), meta_extra=meta_extra,
                   fuentes=fuentes, avance=lambda m: None)
    logging.getLogger("oportunidades").setLevel(logging.INFO)

    imprimir_resumen(res, maximo=12)

    errores = []
    if not res.ideas:
        errores.append("No se ha generado ninguna idea")

    def busca(ticker: str):
        return res.idea(ticker)

    # 1) El valor en tendencia debe puntuar por momentum y quedar arriba.
    t = busca("TENDENCIA")
    if not t:
        errores.append("TENDENCIA no aparece entre las ideas")
    elif "momentum" not in t.motores:
        errores.append(f"TENDENCIA no activa el motor momentum (tiene {t.motores})")
    elif res.ideas.index(t) > 3:
        errores.append(f"TENDENCIA debería estar en el top 4 y está en la posición "
                       f"{res.ideas.index(t) + 1}")

    # 2) La caída dentro de tendencia alcista debe salir por reversión.
    c = busca("CAIDA")
    if not c:
        errores.append("CAIDA no aparece entre las ideas")
    elif "reversion" not in c.motores:
        errores.append(f"CAIDA no activa el motor de sobreventa (tiene {c.motores})")

    # 3) La empresa barata y de calidad debe salir por valor.
    v = busca("VALOR")
    if not v:
        errores.append("VALOR no aparece entre las ideas")
    elif "valor" not in v.motores:
        errores.append(f"VALOR no activa el motor de valor (tiene {v.motores})")

    # 4) El valor con noticias y analistas debe salir por catalizador.
    n = busca("NOTICIA")
    if not n:
        errores.append("NOTICIA no aparece entre las ideas")
    elif "catalizador" not in n.motores:
        errores.append(f"NOTICIA no activa el motor catalizador (tiene {n.motores})")

    # 5) Los ruidosos no deberían comerse el top. Un random walk con la
    #    volatilidad típica produce falsos momentum con cierta frecuencia, así
    #    que el corte es generoso: basta con que los casos construidos destaquen.
    ruidosos = [i.ticker for i in res.ideas if i.ticker.startswith("RUIDO")]
    if len(ruidosos) > 12:
        errores.append(f"Demasiados valores aleatorios en el top: {ruidosos}")

    # 6) Los informes se generan sin errores.
    try:
        md, ht = markdown(res), html(res)
        tg = telegram(res)
        if "oportunidades" not in md.lower() or "<html" not in ht \
                or "OPORTUNIDADES" not in tg:
            errores.append("Los informes no contienen el contenido esperado")
    except Exception as e:
        errores.append(f"Error generando informes: {e}")

    print("  Comprobaciones:")
    for linea in [
        f"    · TENDENCIA → {busca('TENDENCIA').motores if busca('TENDENCIA') else 'AUSENTE'}",
        f"    · CAIDA     → {busca('CAIDA').motores if busca('CAIDA') else 'AUSENTE'}",
        f"    · VALOR     → {busca('VALOR').motores if busca('VALOR') else 'AUSENTE'}",
        f"    · NOTICIA   → {busca('NOTICIA').motores if busca('NOTICIA') else 'AUSENTE'}",
        f"    · valores aleatorios en el top: {len(ruidosos)}",
    ]:
        print(linea)
    print()

    if errores:
        print("❌ Selftest FALLIDO:")
        for e in errores:
            print(f"   - {e}")
        return 1
    print("✅ Selftest correcto: los cuatro motores funcionan y el informe se genera.\n")
    return 0


# ----------------------------------------------------------------------- CLI --

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Detector de oportunidades del mercado",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    sub = ap.add_subparsers(dest="comando")

    def opciones(p, defecto_modo: str | None = None):
        if defecto_modo:
            p.add_argument("--modo", default=defecto_modo, choices=["diario", "semanal", "rapido"])
        else:
            p.add_argument("--modo", default="semanal", choices=["diario", "semanal", "rapido"])
        p.add_argument("--universo", default=None,
                       choices=["sp500", "personalizado", "mezcla", "portafolio"],
                       help="Universo a escanear (por defecto, el de config.py)")
        p.add_argument("--tickers", default="", help="Tickers extra separados por comas")
        p.add_argument("--top", type=int, default=None, help="Cuántas ideas mostrar")
        p.add_argument("--limite-telegram", type=int, default=None,
                       help="Máximo de ideas a enviar por Telegram")
        p.add_argument("--sin-telegram", action="store_true", help="No enviar nada a Telegram")
        p.add_argument("--sin-fundamentales", action="store_true", help="No pedir fundamentales")
        p.add_argument("--sin-noticias", action="store_true", help="No buscar catalizadores")
        p.add_argument("--forzar-fundamentales", action="store_true",
                       help="Ignorar la caché de fundamentales")
        p.add_argument("--refrescar-universo", action="store_true",
                       help="Refrescar la lista del S&P 500 desde Wikipedia")
        p.add_argument("--repetir", action="store_true",
                       help="Enviar aunque la idea ya se avisó hace poco")
        p.add_argument("--sin-informe", action="store_true", help="No escribir ficheros")
        p.add_argument("--salida", default=None, help=f"Carpeta de informes (por defecto {DIR_INFORMES})")

    p = sub.add_parser("escanear", help="Escanea el mercado")
    opciones(p)
    p.set_defaults(func=lambda a: _ejecutar(a, a.modo))

    p = sub.add_parser("diario", help="Scan diario (rápido)")
    opciones(p, "diario")
    p.set_defaults(func=lambda a: _ejecutar(a, "diario"))

    p = sub.add_parser("semanal", help="Scan semanal (profundo)")
    opciones(p, "semanal")
    p.set_defaults(func=lambda a: _ejecutar(a, "semanal"))

    p = sub.add_parser("preview", help="Escanea e imprime por consola (ni Telegram ni dedupe)")
    opciones(p)
    p.set_defaults(func=_preview)

    p = sub.add_parser("selftest", help="Prueba la lógica con datos sintéticos (sin red)")
    p.set_defaults(func=lambda a: _selftest())

    p = sub.add_parser("universo", help="Muestra el universo configurado")
    p.add_argument("--refrescar", action="store_true", help="Refrescar el S&P 500")
    p.add_argument("--listar", action="store_true", help="Imprimir todos los tickers")
    p.add_argument("nombre", nargs="?", default=None)
    p.set_defaults(func=_universo)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        sys.exit(0)
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
