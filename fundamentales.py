"""Fundamentales de Yahoo Finance con caché en disco.

Pedir los fundamentales de 500 valores es lento, así que:
  * se cachean en ``datos/fundamentales_cache.json`` durante unas horas,
  * se descargan en paralelo,
  * y el escáner solo los pide para los candidatos que lo necesitan.
"""
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yfinance as yf

from config import CACHE_FUND_HORAS, HILOS_FUNDAMENTALES

log = logging.getLogger(__name__)

RUTA_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "datos", "fundamentales_cache.json")

# qué nos interesa guardar de cada valor
_CAMPOS = {
    "nombre": ("shortName", "longName"),
    "sector": ("sector",),
    "industria": ("industry",),
    "pais": ("country",),
    "moneda": ("currency",),
    "capitalizacion": ("marketCap",),
    "precio": ("currentPrice", "regularMarketPrice", "previousClose"),
    "pe": ("trailingPE",),
    "pe_fwd": ("forwardPE",),
    "peg": ("trailingPegRatio", "pegRatio"),
    "ps": ("priceToSalesTrailing12Months",),
    "pb": ("priceToBook",),
    "ev_ebitda": ("enterpriseToEbitda",),
    "ev_ingresos": ("enterpriseToRevenue",),
    "roe": ("returnOnEquity",),
    "roa": ("returnOnAssets",),
    "margen_bruto": ("grossMargins",),
    "margen_operativo": ("operatingMargins",),
    "margen_neto": ("profitMargins",),
    "deuda_capital": ("debtToEquity",),
    "liquidez": ("currentRatio",),
    "fcf": ("freeCashflow",),
    "ingresos": ("totalRevenue",),
    "ebitda": ("ebitda",),
    "crecimiento_ingresos": ("revenueGrowth",),
    "crecimiento_beneficios": ("earningsGrowth",),
    "crecimiento_bpa_trim": ("earningsQuarterlyGrowth",),
    "dividendo_yield": ("dividendYield", "trailingAnnualDividendYield"),
    "payout": ("payoutRatio",),
    "beta": ("beta",),
    "bpa": ("trailingEps",),
    "bpa_fwd": ("forwardEps",),
    "objetivo": ("targetMeanPrice",),
    "objetivo_alto": ("targetHighPrice",),
    "objetivo_bajo": ("targetLowPrice",),
    "recomendacion": ("recommendationKey",),
    "analistas": ("numberOfAnalystOpinions",),
    "corto_pct": ("shortPercentOfFloat",),
    "instituciones_pct": ("heldPercentInstitutions",),
    "insiders_pct": ("heldPercentInsiders",),
    "profit_margin": ("profitMargins",),
}

# Métricas que se comparan dentro del sector. True = "más bajo es mejor".
_METRICAS_RELATIVAS = {
    "pe": True, "pe_fwd": True, "peg": True, "ps": True, "pb": True,
    "ev_ebitda": True, "ev_ingresos": True, "deuda_capital": True,
    "payout": True, "corto_pct": True,
    "roe": False, "roa": False, "margen_bruto": False, "margen_operativo": False,
    "margen_neto": False, "fcf_yield": False, "crecimiento_ingresos": False,
    "crecimiento_beneficios": False, "liquidez": False, "rendimiento_fcf": False,
}

_MIN_GRUPO = 5   # sectores con menos valores no dan percentiles fiables


# ------------------------------------------------------------------ Descarga --

def _extraer(info: dict) -> dict:
    out = {}
    for destino, origenes in _CAMPOS.items():
        for origen in origenes:
            valor = info.get(origen)
            if valor is not None:
                out[destino] = valor
                break
        else:
            out[destino] = None

    # normalizaciones (Yahoo mezcla tanto por uno y por cien)
    for campo in ("roe", "roa", "margen_bruto", "margen_operativo", "margen_neto",
                  "crecimiento_ingresos", "crecimiento_beneficios", "crecimiento_bpa_trim",
                  "payout", "dividendo_yield", "corto_pct", "instituciones_pct",
                  "insiders_pct"):
        v = out.get(campo)
        if isinstance(v, (int, float)):
            v = float(v)
            out[campo] = v * 100 if abs(v) <= 1.5 else v

    precio = out.get("precio")
    cap = out.get("capitalizacion")
    fcf = out.get("fcf")
    if isinstance(fcf, (int, float)) and isinstance(cap, (int, float)) and cap > 0:
        out["fcf_yield"] = round(float(fcf) / float(cap) * 100, 2)
    else:
        out["fcf_yield"] = None
    if isinstance(out.get("ingresos"), (int, float)) and float(out["ingresos"]) > 0 \
            and isinstance(fcf, (int, float)):
        out["margen_fcf"] = round(float(fcf) / float(out["ingresos"]) * 100, 2)
    else:
        out["margen_fcf"] = None

    objetivo = out.get("objetivo")
    if isinstance(objetivo, (int, float)) and isinstance(precio, (int, float)) and precio > 0:
        out["potencial_pct"] = round((float(objetivo) / float(precio) - 1) * 100, 2)
    else:
        out["potencial_pct"] = None

    # valoración extra: rentabilidad por dividendo ya viene en %
    out["_ts"] = datetime.now(timezone.utc).isoformat()
    return out


def _uno(ticker: str) -> tuple[str, dict | None]:
    try:
        tk = yf.Ticker(ticker)
        try:
            info = tk.get_info()
        except Exception:
            info = tk.info
        if not info:
            return ticker, None
        return ticker, _extraer(info)
    except Exception as e:
        log.debug("Fundamentales %s: %s", ticker, e)
        return ticker, None


def _cargar_cache() -> dict:
    if not os.path.exists(RUTA_CACHE):
        return {}
    try:
        with open(RUTA_CACHE, encoding="utf-8") as f:
            datos = json.load(f)
        return datos.get("datos", {}) if isinstance(datos, dict) else {}
    except Exception as e:
        log.warning("Caché de fundamentales ilegible (%s). Se ignora.", e)
        return {}


def _guardar_cache(datos: dict) -> None:
    try:
        os.makedirs(os.path.dirname(RUTA_CACHE), exist_ok=True)
        tmp = RUTA_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"generado": datetime.now(timezone.utc).isoformat(), "datos": datos},
                      f, ensure_ascii=False)
        os.replace(tmp, RUTA_CACHE)
    except Exception as e:
        log.warning("No se pudo guardar la caché de fundamentales: %s", e)


def _antiguedad_horas(marca: str | None) -> float:
    if not marca:
        return float("inf")
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(marca)).total_seconds() / 3600
    except Exception:
        return float("inf")


def obtener(tickers: list[str], forzar: bool = False, hilos: int = HILOS_FUNDAMENTALES,
            ttl_horas: float = CACHE_FUND_HORAS, avance=None) -> dict[str, dict]:
    """Devuelve {ticker: fundamentales} usando la caché cuando es reciente."""
    if not tickers:
        return {}

    cache = _cargar_cache()
    resultado: dict[str, dict] = {}
    pendientes: list[str] = []

    for t in tickers:
        entrada = cache.get(t)
        if entrada and not forzar and _antiguedad_horas(entrada.get("_ts")) < ttl_horas:
            resultado[t] = entrada
        else:
            pendientes.append(t)

    if pendientes:
        log.info("Descargando fundamentales de %s valores (%s venían de caché)...",
                 len(pendientes), len(resultado))
        inicio = time.time()
        with ThreadPoolExecutor(max_workers=max(1, min(hilos, len(pendientes)))) as pool:
            for i, (t, datos) in enumerate(pool.map(_uno, pendientes), 1):
                if datos:
                    resultado[t] = datos
                if avance and i % 25 == 0:
                    avance(f"fundamentales {i}/{len(pendientes)}")
        log.info("Fundamentales descargados en %.1fs (%s con datos).",
                 time.time() - inicio, sum(1 for t in pendientes if t in resultado))
        _guardar_cache({**cache, **{t: resultado[t] for t in pendientes if t in resultado}})

    return resultado


# -------------------------------------------------------- Percentiles sector --


def percentiles_por_sector(fund: dict[str, dict], sectores: dict[str, str]) -> dict[str, dict]:
    """Para cada valor, su percentil (0-1) dentro de su sector.

    Siempre con la misma orientación: **1.0 = mejor**. Es decir, en métricas
    como el PER (donde menos es mejor) el percentil ya viene invertido.
    """
    if not fund:
        return {}

    # agrupar por sector
    grupos: dict[str, list[str]] = {}
    for t in fund:
        grupos.setdefault(sectores.get(t, "Desconocido"), []).append(t)

    salida: dict[str, dict] = {t: {} for t in fund}
    metricas = [m for m in _METRICAS_RELATIVAS if m != "rendimiento_fcf"]

    for sector, miembros in grupos.items():
        if len(miembros) < _MIN_GRUPO:
            continue
        for metrica in metricas:
            pares = [(t, fund[t].get(metrica)) for t in miembros]
            pares = [(t, float(v)) for t, v in pares
                     if isinstance(v, (int, float)) and v == v and abs(float(v)) != float("inf")]
            if len(pares) < _MIN_GRUPO:
                continue
            ordenados = sorted(pares, key=lambda p: p[1])
            n = len(ordenados)
            for pos, (t, _) in enumerate(ordenados):
                pct = pos / (n - 1) if n > 1 else 0.5
                if _METRICAS_RELATIVAS[metrica]:
                    pct = 1 - pct
                salida[t][f"{metrica}_pct"] = round(pct, 4)
    return salida


def resumen_rapido(fund: dict) -> str:
    if not fund:
        return "sin datos"
    partes = []
    for campo, fmt in (("pe_fwd", "PER fwd {:.1f}"), ("roe", "ROE {:.0f}%"),
                       ("margen_neto", "margen {:.0f}%"), ("crecimiento_ingresos", "ingresos {:+.0f}%")):
        v = fund.get(campo)
        if isinstance(v, (int, float)):
            partes.append(fmt.format(float(v)))
    return " · ".join(partes) if partes else "sin datos"
