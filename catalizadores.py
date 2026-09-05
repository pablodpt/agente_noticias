"""Datos de catalizadores: noticias, presentaciones SEC, analistas y resultados.

Son las fuentes más lentas, así que el escáner solo las consulta para los
finalistas (por defecto los 25 mejores candidatos).
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

import mercado
import noticias
import sec

log = logging.getLogger(__name__)

DIAS_ANALISTAS = 45


def _upgrades(ticker: str, dias: int = DIAS_ANALISTAS) -> list[dict]:
    """Últimos movimientos de analistas (mejoras y recortes de calificación)."""
    try:
        df = yf.Ticker(ticker).get_upgrades_downgrades()
    except Exception:
        try:
            df = yf.Ticker(ticker).upgrades_downgrades
        except Exception:
            return []
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []

    limite = datetime.now(timezone.utc) - timedelta(days=dias)
    out = []
    try:
        for fecha, fila in df.tail(12).iterrows():
            try:
                cuando = pd.Timestamp(fecha)
                cuando = cuando.to_pydatetime().replace(tzinfo=timezone.utc) \
                    if cuando.tzinfo is None else cuando.to_pydatetime()
            except Exception:
                continue
            if cuando < limite:
                continue
            out.append({
                "fecha": cuando,
                "firma": str(fila.get("Firm", "") or ""),
                "desde": str(fila.get("FromGrade", "") or ""),
                "hasta": str(fila.get("ToGrade", "") or ""),
                "accion": str(fila.get("Action", "") or "").lower(),
            })
    except Exception as e:
        log.debug("Analistas %s: %s", ticker, e)
    return out


def _uno(ticker: str, dias_sec: int) -> tuple[str, dict]:
    info: dict = {"noticias": [], "filings": [], "upgrades": [], "earnings": None}
    try:
        info["noticias"] = noticias.noticias_de_ticker(ticker)[:6]
    except Exception as e:
        log.debug("Noticias %s: %s", ticker, e)
    try:
        info["filings"] = sec.presentaciones(ticker, dias=dias_sec)[:5]
    except Exception as e:
        log.debug("SEC %s: %s", ticker, e)
    info["upgrades"] = _upgrades(ticker)
    try:
        info["earnings"] = mercado.proximo_earnings(ticker)
    except Exception as e:
        log.debug("Earnings %s: %s", ticker, e)
    return ticker, info


def recolectar(tickers: list[str], dias_sec: int = 3, hilos: int = 8,
               avance=None) -> dict[str, dict]:
    """Recolecta catalizadores de varios tickers en paralelo."""
    if not tickers:
        return {}
    salida: dict[str, dict] = {}
    inicio = time.time()
    with ThreadPoolExecutor(max_workers=max(1, min(hilos, len(tickers)))) as pool:
        for i, (t, info) in enumerate(pool.map(lambda x: _uno(x, dias_sec), tickers), 1):
            salida[t] = info
            if avance and i % 10 == 0:
                avance(f"catalizadores {i}/{len(tickers)}")
    log.info("Catalizadores recogidos de %s valores en %.1fs.", len(salida), time.time() - inicio)
    return salida
