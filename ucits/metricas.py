"""Métricas de riesgo/retorno a partir de series de VL (o fallback de categoría)."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .categorias import CATEGORIAS, r3y_aprox

log = logging.getLogger(__name__)

PERIODOS = {
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "1y": 252,
    "3y": 252 * 3,
    "5y": 252 * 5,
}


def _ret_anualizado(precios: pd.Series, sesiones: int) -> float | None:
    if precios is None or len(precios) < max(20, sesiones // 6):
        return None
    s = precios.dropna()
    n = min(len(s) - 1, sesiones)
    if n < 10:
        return None
    r = float(s.iloc[-1] / s.iloc[-1 - n] - 1)
    years = n / 252.0
    if years <= 0 or r <= -0.999:
        return None
    if years < 0.95:
        return round(r * 100, 2)
    return round(((1 + r) ** (1 / years) - 1) * 100, 2)


def _vol_anual(rets: pd.Series) -> float | None:
    r = rets.dropna()
    if len(r) < 20:
        return None
    return round(float(r.std() * math.sqrt(252) * 100), 2)


def _max_drawdown(precios: pd.Series) -> float | None:
    s = precios.dropna()
    if len(s) < 20:
        return None
    pico = s.cummax()
    dd = s / pico - 1
    return round(float(dd.min() * 100), 2)


def _sharpe(rets: pd.Series, rf_anual: float) -> float | None:
    r = rets.dropna()
    if len(r) < 40:
        return None
    rf_d = rf_anual / 252.0
    ex = r - rf_d
    vol = float(ex.std())
    if vol <= 0:
        return None
    return round(float(ex.mean() / vol * math.sqrt(252)), 2)


def _sortino(rets: pd.Series, rf_anual: float) -> float | None:
    r = rets.dropna()
    if len(r) < 40:
        return None
    rf_d = rf_anual / 252.0
    ex = r - rf_d
    neg = ex[ex < 0]
    down = float(neg.std()) if len(neg) > 5 else 0.0
    if down <= 0:
        return None
    return round(float(ex.mean() / down * math.sqrt(252)), 2)


def _calmar(ret_3y: float | None, max_dd: float | None) -> float | None:
    if ret_3y is None or not max_dd or max_dd >= 0:
        return None
    return round(ret_3y / abs(max_dd), 2)


def _ulcer(precios: pd.Series) -> float | None:
    s = precios.dropna()
    if len(s) < 40:
        return None
    dd = (s / s.cummax() - 1) * 100
    return round(float(np.sqrt((dd ** 2).mean())), 2)


def metricas_desde_nav(nav: pd.Series, rf_anual: float = 0.02) -> dict:
    s = nav.dropna().astype(float).sort_index()
    if len(s) < 25:
        return {}
    rets = s.pct_change().dropna()
    r1 = _ret_anualizado(s, 252)
    r3 = _ret_anualizado(s, 252 * 3)
    r5 = _ret_anualizado(s, 252 * 5)
    mdd = _max_drawdown(s)
    out = {
        "nav": round(float(s.iloc[-1]), 4),
        "fecha_nav": str(s.index[-1].date()) if hasattr(s.index[-1], "date") else str(s.index[-1]),
        "ret_1m": _ret_anualizado(s, 21),
        "ret_3m": _ret_anualizado(s, 63),
        "ret_6m": _ret_anualizado(s, 126),
        "ret_1y": r1,
        "ret_3y": r3,
        "ret_5y": r5,
        "vol_1y": _vol_anual(rets.tail(252)),
        "vol_3y": _vol_anual(rets.tail(252 * 3)),
        "max_dd": mdd,
        "sharpe_1y": _sharpe(rets.tail(252), rf_anual),
        "sharpe_3y": _sharpe(rets.tail(252 * 3), rf_anual),
        "sortino_3y": _sortino(rets.tail(252 * 3), rf_anual),
        "calmar_3y": _calmar(r3, mdd),
        "ulcer": _ulcer(s.tail(252 * 3)),
        "fuente": "nav_vivo",
        "confianza": 0.95,
        "sesiones": int(len(s)),
    }
    return out


def metricas_estimadas(fondo: dict) -> dict:
    """Fallback honesto: ancla de categoría + prima best-in-class − penalización TER.

    No inventa una serie de VL. Sirve para rankear cuando Yahoo/Morningstar
    no responden. La UI lo etiqueta como 'estimado'.
    """
    cat = CATEGORIAS[fondo["categoria"]]
    bic = int(fondo.get("bic", 3))
    ter = float(fondo["ter"])
    ter_penal = (ter - cat["ter_tipico"]) * 0.55
    prima = (bic - 3) * 0.95 - ter_penal

    r1 = cat["r1y"] + prima * 0.55
    r5 = cat["r5y"] + prima * 0.40
    r3 = r3y_aprox({"r1y": r1, "r5y": r5})

    vol = cat["vol_tipica"] * (1.04 - 0.02 * bic)
    mdd = cat["max_dd_tipico"] * (1.06 - 0.03 * bic)
    rf = cat["rf"] / 100.0
    sharpe3 = None
    if vol > 0.15:
        sharpe3 = round((r3 / 100.0 - rf) / (vol / 100.0), 2)
    sortino3 = round(sharpe3 * 1.35, 2) if sharpe3 is not None else None
    calmar = _calmar(r3, mdd)

    return {
        "nav": None,
        "fecha_nav": None,
        "ret_1m": round(r1 / 12, 2),
        "ret_3m": round(r1 / 4, 2),
        "ret_6m": round(r1 / 2, 2),
        "ret_1y": round(r1, 2),
        "ret_3y": round(r3, 2),
        "ret_5y": round(r5, 2),
        "vol_1y": round(vol, 2),
        "vol_3y": round(vol * 1.05, 2),
        "max_dd": round(mdd, 2),
        "sharpe_1y": round((r1 / 100.0 - rf) / (vol / 100.0), 2) if vol else None,
        "sharpe_3y": sharpe3,
        "sortino_3y": sortino3,
        "calmar_3y": calmar,
        "ulcer": round(abs(mdd) * 0.45, 2),
        "fuente": "estimado_categoria",
        "confianza": 0.45 + 0.05 * bic,
        "sesiones": 0,
        "exceso_1y": round(r1 - cat["r1y"], 2),
        "exceso_5y": round(r5 - cat["r5y"], 2),
    }


def intentar_nav_yahoo(simbolos: list[str], periodo: str = "5y") -> dict[str, pd.Series]:
    """Descarga en bloque. Devuelve {} si la red/Yahoo fallan."""
    if not simbolos:
        return {}
    try:
        import yfinance as yf
    except Exception as e:
        log.info("yfinance no disponible: %s", e)
        return {}
    try:
        data = yf.download(
            simbolos,
            period=periodo,
            auto_adjust=True,
            progress=False,
            threads=True,
            group_by="ticker",
        )
    except Exception as e:
        log.warning("Yahoo no respondió (%s). Se usa snapshot/estimado.", e)
        return {}
    if data is None or len(data) == 0:
        return {}

    series: dict[str, pd.Series] = {}
    if isinstance(data.columns, pd.MultiIndex):
        for s in simbolos:
            try:
                col = data[s]["Close"].dropna() if "Close" in data[s] else data[s].iloc[:, 0].dropna()
            except Exception:
                continue
            if len(col) >= 25:
                series[s] = col
    else:
        col = data["Close"].dropna() if "Close" in data.columns else data.iloc[:, 0].dropna()
        if len(col) >= 25 and len(simbolos) == 1:
            series[simbolos[0]] = col
    return series


def enriquecer(fondo: dict, nav_map: dict[str, pd.Series] | None = None) -> dict:
    cat = CATEGORIAS[fondo["categoria"]]
    rf = cat["rf"] / 100.0
    nav_map = nav_map or {}
    nav = None
    for key in (fondo.get("yahoo"), fondo.get("isin"), fondo["id"]):
        if key and key in nav_map:
            nav = nav_map[key]
            break
    if nav is not None:
        m = metricas_desde_nav(nav, rf)
        if m:
            m["exceso_1y"] = round(m["ret_1y"] - cat["r1y"], 2) if m.get("ret_1y") is not None else None
            m["exceso_5y"] = round(m["ret_5y"] - cat["r5y"], 2) if m.get("ret_5y") is not None else None
            out = {**fondo, **m, "cat_nombre": cat["nombre"],
                   "cat_r1y": cat["r1y"], "cat_r5y": cat["r5y"]}
            return out
    m = metricas_estimadas(fondo)
    return {**fondo, **m, "cat_nombre": cat["nombre"],
            "cat_r1y": cat["r1y"], "cat_r5y": cat["r5y"]}


def ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
