"""Datos de mercado: precios, rupturas, volumen anómalo y calendario de earnings."""
import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from config import (DIAS_AVISO_EARNINGS, UMBRAL_GAP_PCT, UMBRAL_MOVIMIENTO_PCT,
                    UMBRAL_VOLUMEN_X, VENTANA_RUPTURA_DIAS)

log = logging.getLogger(__name__)


def _historico(ticker: str, periodo: str = "1y") -> pd.DataFrame:
    try:
        df = yf.Ticker(ticker).history(period=periodo, auto_adjust=False)
        return df.dropna(subset=["Close"])
    except Exception as e:
        log.warning("Histórico %s: %s", ticker, e)
        return pd.DataFrame()


def snapshot(ticker: str) -> dict | None:
    """Foto actual del ticker con todas las métricas que usamos."""
    df = _historico(ticker)
    if df.empty or len(df) < 2:
        return None

    cierre_actual = float(df["Close"].iloc[-1])
    cierre_previo = float(df["Close"].iloc[-2])
    apertura = float(df["Open"].iloc[-1])
    volumen = float(df["Volume"].iloc[-1] or 0)

    prev = df.iloc[:-1]
    vol_medio = float(prev["Volume"].tail(20).mean() or 0)

    ventana = prev.tail(VENTANA_RUPTURA_DIAS)
    max_ventana = float(ventana["High"].max()) if not ventana.empty else cierre_actual
    min_ventana = float(ventana["Low"].min()) if not ventana.empty else cierre_actual

    anio = prev.tail(252)
    max_52w = float(anio["High"].max()) if not anio.empty else cierre_actual
    min_52w = float(anio["Low"].min()) if not anio.empty else cierre_actual

    sma20 = float(df["Close"].tail(20).mean())
    sma50 = float(df["Close"].tail(50).mean()) if len(df) >= 50 else sma20
    sma200 = float(df["Close"].tail(200).mean()) if len(df) >= 200 else sma50

    return {
        "ticker": ticker,
        "precio": round(cierre_actual, 2),
        "cambio_pct": round((cierre_actual / cierre_previo - 1) * 100, 2),
        "gap_pct": round((apertura / cierre_previo - 1) * 100, 2),
        "volumen": volumen,
        "volumen_x": round(volumen / vol_medio, 2) if vol_medio else 0.0,
        "max_ventana": round(max_ventana, 2),
        "min_ventana": round(min_ventana, 2),
        "max_52w": round(max_52w, 2),
        "min_52w": round(min_52w, 2),
        "sma20": round(sma20, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "fecha": df.index[-1].date().isoformat(),
        "rango_5d": round((float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-6]) - 1) * 100, 2)
        if len(df) >= 6 else None,
    }


def detectar_eventos_precio(snap: dict) -> list[dict]:
    """Devuelve la lista de eventos técnicos relevantes de un snapshot."""
    if not snap:
        return []
    ev = []
    t, fecha = snap["ticker"], snap["fecha"]

    if abs(snap["cambio_pct"]) >= UMBRAL_MOVIMIENTO_PCT:
        sube = snap["cambio_pct"] > 0
        ev.append({
            "clave": f"mov:{t}:{fecha}:{'up' if sube else 'down'}",
            "tipo": "movimiento",
            "urgente": True,
            "titulo": f"{'🚀 Subida' if sube else '🔻 Caída'} fuerte {snap['cambio_pct']:+.2f}%",
            "detalle": f"Precio {snap['precio']} · volumen {snap['volumen_x']}× la media",
        })

    if abs(snap["gap_pct"]) >= UMBRAL_GAP_PCT:
        ev.append({
            "clave": f"gap:{t}:{fecha}",
            "tipo": "gap",
            "urgente": True,
            "titulo": f"↕️ Gap de apertura {snap['gap_pct']:+.2f}%",
            "detalle": "Apertura muy separada del cierre anterior.",
        })

    if snap["precio"] >= snap["max_52w"]:
        ev.append({
            "clave": f"max52:{t}:{fecha}",
            "tipo": "ruptura",
            "urgente": True,
            "titulo": "📈 RUPTURA AL ALZA: nuevo máximo de 52 semanas",
            "detalle": f"{snap['precio']} supera el techo previo de {snap['max_52w']}",
        })
    elif snap["precio"] > snap["max_ventana"]:
        ev.append({
            "clave": f"maxv:{t}:{fecha}",
            "tipo": "ruptura",
            "urgente": True,
            "titulo": f"📈 Ruptura al alza del rango de {VENTANA_RUPTURA_DIAS} sesiones",
            "detalle": f"{snap['precio']} supera {snap['max_ventana']}",
        })

    if snap["precio"] <= snap["min_52w"]:
        ev.append({
            "clave": f"min52:{t}:{fecha}",
            "tipo": "ruptura",
            "urgente": True,
            "titulo": "📉 RUPTURA A LA BAJA: nuevo mínimo de 52 semanas",
            "detalle": f"{snap['precio']} perfora el suelo previo de {snap['min_52w']}",
        })
    elif snap["precio"] < snap["min_ventana"]:
        ev.append({
            "clave": f"minv:{t}:{fecha}",
            "tipo": "ruptura",
            "urgente": True,
            "titulo": f"📉 Ruptura a la baja del rango de {VENTANA_RUPTURA_DIAS} sesiones",
            "detalle": f"{snap['precio']} perfora {snap['min_ventana']}",
        })

    if snap["volumen_x"] >= UMBRAL_VOLUMEN_X:
        ev.append({
            "clave": f"vol:{t}:{fecha}",
            "tipo": "volumen",
            "urgente": abs(snap["cambio_pct"]) >= UMBRAL_MOVIMIENTO_PCT / 2,
            "titulo": f"🔊 Volumen anómalo {snap['volumen_x']}× la media de 20 días",
            "detalle": "Suele acompañar noticias o movimientos institucionales.",
        })

    # Cruce de la SMA200 (señal estructural)
    if snap["sma200"]:
        dist = (snap["precio"] / snap["sma200"] - 1) * 100
        if -1 <= dist <= 1:
            ev.append({
                "clave": f"sma200:{t}:{fecha}",
                "tipo": "tecnico",
                "urgente": False,
                "titulo": "⚖️ Precio pegado a la media de 200 sesiones",
                "detalle": f"SMA200 = {snap['sma200']} ({dist:+.2f}%)",
            })
    return ev


def proximo_earnings(ticker: str) -> dict | None:
    """Fecha del próximo reporte de resultados, si Yahoo la publica."""
    try:
        tk = yf.Ticker(ticker)
        fechas = []

        try:
            cal = tk.calendar
            if isinstance(cal, dict):
                for v in (cal.get("Earnings Date") or []):
                    fechas.append(v)
            elif hasattr(cal, "empty") and not cal.empty and "Earnings Date" in cal.index:
                fechas.extend(list(cal.loc["Earnings Date"].values))
        except Exception:
            pass

        try:
            df = tk.get_earnings_dates(limit=12)
            if df is not None and not df.empty:
                fechas.extend(list(df.index))
        except Exception:
            pass

        hoy = date.today()
        candidatas = []
        for f in fechas:
            d = _a_fecha(f)
            if d and d >= hoy:
                candidatas.append(d)
        if not candidatas:
            return None
        proxima = min(candidatas)
        return {
            "ticker": ticker,
            "fecha": proxima,
            "dias": (proxima - hoy).days,
        }
    except Exception as e:
        log.debug("Earnings %s: %s", ticker, e)
        return None


def calendario_earnings(tickers: list[str]) -> list[dict]:
    out = []
    for t in tickers:
        e = proximo_earnings(t)
        if e and e["dias"] <= max(DIAS_AVISO_EARNINGS, 90):
            out.append(e)
    return sorted(out, key=lambda x: x["fecha"])


def _a_fecha(v):
    try:
        if isinstance(v, date) and not isinstance(v, datetime):
            return v
        if isinstance(v, datetime):
            return v.date()
        return pd.Timestamp(v).date()
    except Exception:
        return None
