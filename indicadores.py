"""Descarga de precios por lotes y cálculo de indicadores técnicos.

Pensado para escanear cientos de valores: descarga en lotes con reintentos y
calcula todas las métricas que usan los motores de oportunidades.
"""
import logging
import math
import time

import pandas as pd
import yfinance as yf

from config import PAUSA_ENTRE_LOTES, SESIONES_MINIMAS, TAMANO_LOTE

log = logging.getLogger(__name__)

CAMPOS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


# ------------------------------------------------------------------ Descarga --

def _limpiar(df: pd.DataFrame) -> pd.DataFrame:
    """Deja el DataFrame con las columnas que usamos y sin huecos."""
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    faltan = [c for c in CAMPOS if c not in df.columns]
    for c in faltan:
        df[c] = pd.NA
    df = df[CAMPOS].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=["Close"])
    df = df[df["Close"] > 0]
    if df["Volume"].notna().sum():
        df["Volume"] = df["Volume"].fillna(0)
    else:
        df["Volume"] = 0.0
    return df


def _descargar_lote(tickers: list[str], periodo: str, intervalo: str) -> dict[str, pd.DataFrame]:
    """Descarga un lote con yfinance y lo separa por ticker."""
    if not tickers:
        return {}
    bruto = yf.download(tickers, period=periodo, interval=intervalo,
                        group_by="ticker", auto_adjust=False, threads=True,
                        progress=False, multi_level_index=True)
    if bruto is None or bruto.empty:
        return {}

    out: dict[str, pd.DataFrame] = {}
    columnas = bruto.columns

    if isinstance(columnas, pd.MultiIndex):
        nivel0 = {str(c) for c in columnas.get_level_values(0)}
        for t in tickers:
            try:
                if t in nivel0:
                    sub = bruto[t]
                elif t in {str(c) for c in columnas.get_level_values(-1)}:
                    sub = bruto.xs(t, axis=1, level=-1)
                else:
                    continue
                limpio = _limpiar(sub)
                if not limpio.empty:
                    out[t] = limpio
            except Exception:
                continue
    else:
        t = tickers[0]
        limpio = _limpiar(bruto)
        if not limpio.empty:
            out[t] = limpio
    return out


def _descargar_uno(ticker: str, periodo: str, intervalo: str) -> pd.DataFrame:
    return _limpiar(yf.Ticker(ticker).history(period=periodo, interval=intervalo,
                                              auto_adjust=False))


def _hay_conexion(intervalo: str = "1d") -> bool:
    """Sondeo rápido: si este ticker no llega, no tiene sentido seguir."""
    from config import BENCHMARK
    try:
        return not _descargar_uno(BENCHMARK, "5d", intervalo).empty
    except Exception as e:
        log.warning("Sondeo de conexión fallido: %s", e)
        return False


def descargar_precios(tickers: list[str], periodo: str = "1y", intervalo: str = "1d",
                      lote: int = TAMANO_LOTE, pausa: float = PAUSA_ENTRE_LOTES,
                      avance=None, sondeo: bool = True) -> tuple[dict[str, pd.DataFrame],
                                                                 list[str]]:
    """Descarga históricos de muchos tickers.

    Devuelve (datos, fallidos). Reintenta por mitades antes de dar un lote por
    perdido, para que un fallo puntual no se lleve 60 valores por delante. Si no
    hay conexión, aborta pronto en lugar de reintentar uno a uno.
    """
    datos: dict[str, pd.DataFrame] = {}
    fallidos: list[str] = []
    if not tickers:
        return datos, fallidos

    if sondeo and not _hay_conexion(intervalo):
        log.error("No se puede contactar con Yahoo Finance. Se aborta la descarga.")
        return {}, list(tickers)

    pendientes = [list(tickers[i:i + lote]) for i in range(0, len(tickers), lote)]
    total = len(pendientes)
    fallos_seguidos = 0

    while pendientes:
        grupo = pendientes.pop(0)
        if avance:
            avance(f"lote de {len(grupo)} tickers ({len(datos)} ok, {len(fallidos)} fallidos)")
        antes = len(datos)
        try:
            datos.update(_descargar_lote(grupo, periodo, intervalo))
        except Exception as e:
            log.warning("Fallo al descargar el lote (%s). Lo divido.", e)
            if len(grupo) > 8:
                mitad = len(grupo) // 2
                pendientes.insert(0, grupo[mitad:])
                pendientes.insert(0, grupo[:mitad])
            else:
                for t in grupo:
                    try:
                        df = _descargar_uno(t, periodo, intervalo)
                        if not df.empty:
                            datos[t] = df
                        else:
                            fallidos.append(t)
                    except Exception:
                        fallidos.append(t)
        if len(datos) > antes:
            fallos_seguidos = 0
        else:
            fallos_seguidos += 1
            if fallos_seguidos >= 4 and not datos:
                log.error("Cuatro lotes seguidos sin datos. Aborto la descarga "
                          "(¿problemas de red o de límites de la API?).")
                for resto in pendientes:
                    fallidos.extend(resto)
                break
        if pendientes or pausa:
            time.sleep(pausa)

    if total:
        log.info("Descarga terminada: %s tickers con datos, %s sin datos.",
                 len(datos), len(fallidos))
    return datos, fallidos


# --------------------------------------------------------------- Indicadores --

def _rsi(serie: pd.Series, ventana: int = 14) -> float | None:
    if len(serie) < ventana + 1:
        return None
    delta = serie.diff()
    ganancia = delta.clip(lower=0).ewm(alpha=1 / ventana, min_periods=ventana,
                                       adjust=False).mean()
    perdida = (-delta.clip(upper=0)).ewm(alpha=1 / ventana, min_periods=ventana,
                                         adjust=False).mean()
    ultima_perdida = float(perdida.iloc[-1])
    if ultima_perdida == 0:
        return 100.0
    rs = float(ganancia.iloc[-1]) / ultima_perdida
    return 100 - 100 / (1 + rs)


def _atr(df: pd.DataFrame, ventana: int = 14) -> float | None:
    if len(df) < ventana + 1:
        return None
    alto, bajo, cierre = df["High"], df["Low"], df["Close"]
    previo = cierre.shift(1)
    tr = pd.concat([(alto - bajo), (alto - previo).abs(), (bajo - previo).abs()],
                   axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / ventana, min_periods=ventana, adjust=False).mean().iloc[-1])


def _retorno(serie: pd.Series, sesiones: int) -> float | None:
    if len(serie) <= sesiones or float(serie.iloc[-1 - sesiones]) == 0:
        return None
    return (float(serie.iloc[-1]) / float(serie.iloc[-1 - sesiones]) - 1) * 100


def calcular(df: pd.DataFrame, referencia: pd.Series | None = None) -> dict | None:
    """Calcula todos los indicadores de un ticker.

    ``referencia`` es la serie de precios ajustados del benchmark (SPY) para
    fuerza relativa y beta.
    """
    if df is None or df.empty or len(df) < SESIONES_MINIMAS:
        return None

    px = df["Adj Close"].where(df["Adj Close"].notna(), df["Close"]).astype(float)
    cierre = df["Close"].astype(float)
    volumen = df["Volume"].astype(float)

    precio = float(cierre.iloc[-1])
    if not math.isfinite(precio) or precio <= 0:
        return None

    def sma(n: int) -> float | None:
        return float(px.tail(n).mean()) if len(px) >= n else None

    sma20, sma50, sma150, sma200 = sma(20), sma(50), sma(150), sma(200)
    ultimo = float(px.iloc[-1])

    # --- momento y tendencia ---
    retornos_diarios = px.pct_change().dropna()
    volatilidad = (float(retornos_diarios.tail(63).std()) * math.sqrt(252) * 100
                   if len(retornos_diarios) >= 20 else None)
    atr14 = _atr(df, 14)

    ancho20 = None
    if len(df) >= 20:
        rango_alto = float(df["High"].tail(20).max())
        rango_bajo = float(df["Low"].tail(20).min())
        medio = float(px.tail(20).mean())
        if medio:
            ancho20 = (rango_alto - rango_bajo) / medio * 100
    ancho60 = None
    if len(df) >= 60:
        rango_alto = float(df["High"].tail(60).max())
        rango_bajo = float(df["Low"].tail(60).min())
        medio = float(px.tail(60).mean())
        if medio:
            ancho60 = (rango_alto - rango_bajo) / medio * 100
    estrechez = (ancho20 / ancho60) if (ancho20 and ancho60) else None

    max52 = float(px.tail(252).max()) if len(px) >= 252 else float(px.max())
    min52 = float(px.tail(252).min()) if len(px) >= 252 else float(px.min())
    rango52 = max52 - min52

    # Ruptura: cierre por encima del máximo de las N sesiones anteriores.
    ruptura20 = False
    if len(df) >= 21:
        ruptura20 = float(df["High"].iloc[-1]) > float(df["High"].iloc[-21:-1].max())
    ruptura55 = False
    if len(df) >= 56:
        ruptura55 = float(df["High"].iloc[-1]) > float(df["High"].iloc[-56:-1].max())

    vol_medio20 = float(volumen.tail(21).iloc[:-1].mean()) if len(volumen) >= 21 else float(volumen.mean())
    vol_ultimo = float(volumen.iloc[-1])
    vol_ratio = vol_ultimo / vol_medio20 if vol_medio20 else None
    vol_medio5 = float(volumen.tail(5).mean())
    vol_medio60 = float(volumen.tail(60).mean()) if len(volumen) >= 60 else vol_medio20

    # Racha de cierres consecutivos en la misma dirección.
    racha, signo = 0, 0
    for i in range(len(px) - 1, 0, -1):
        s = 1 if float(px.iloc[i]) > float(px.iloc[i - 1]) else (-1 if float(px.iloc[i]) < float(px.iloc[i - 1]) else 0)
        if s == 0:
            break
        if signo == 0:
            signo = s
        elif s != signo:
            break
        racha += 1
        if racha >= 10:
            break

    indic = {
        "ticker": None,                      # lo rellena el escáner
        "precio": round(precio, 2),
        "precio_ajustado": round(ultimo, 4),
        "sesiones": int(len(df)),
        "fecha": df.index[-1].date().isoformat(),

        "cambio_1d": _r(_retorno(px, 1)),
        "ret_5d": _r(_retorno(px, 5)),
        "ret_21d": _r(_retorno(px, 21)),
        "ret_63d": _r(_retorno(px, 63)),
        "ret_126d": _r(_retorno(px, 126)),
        "ret_252d": _r(_retorno(px, 252)),

        "sma20": _r(sma20), "sma50": _r(sma50), "sma150": _r(sma150), "sma200": _r(sma200),
        "sobre_sma20": bool(sma20 and ultimo > sma20),
        "sobre_sma50": bool(sma50 and ultimo > sma50),
        "sobre_sma200": bool(sma200 and ultimo > sma200),
        "sma20_sobre_sma50": bool(sma20 and sma50 and sma20 > sma50),
        "sma50_sobre_sma200": bool(sma50 and sma200 and sma50 > sma200),
        "pendiente_sma50": _r(_pendiente(px, 50, 20)),
        "dist_sma200_pct": _r((ultimo / sma200 - 1) * 100 if sma200 else None),

        "rsi14": _r(_rsi(px, 14)),
        "rsi2": _r(_rsi(px, 2)),
        "atr14": _r(atr14),
        "atr_pct": _r((atr14 / precio * 100) if atr14 else None),
        "volatilidad": _r(volatilidad),

        "max_52w": _r(max52), "min_52w": _r(min52),
        "dist_max52_pct": _r((ultimo / max52 - 1) * 100 if max52 else None),
        "dist_min52_pct": _r((ultimo / min52 - 1) * 100 if min52 else None),
        "pos_rango_52w": _r((ultimo - min52) / rango52 if rango52 else None),
        "dias_desde_max52": _r(_dias_desde_maximo(px)),

        "ancho_20d": _r(ancho20), "ancho_60d": _r(ancho60), "estrechez": _r(estrechez),
        "ruptura_20d": ruptura20, "ruptura_55d": ruptura55,

        "volumen": vol_ultimo,
        "vol_medio20": vol_medio20,
        "vol_ratio": _r(vol_ratio),
        "vol_tendencia": _r(vol_medio5 / vol_medio60 if vol_medio60 else None),
        "dolares_medios": _r(float((cierre * volumen).tail(20).mean())),

        "z_sma20": _r(_z(px, 20)),
        "gap_pct": _r((float(df["Open"].iloc[-1]) / float(cierre.iloc[-2]) - 1) * 100
                      if len(cierre) >= 2 and float(cierre.iloc[-2]) else None),
        "racha_dias": racha if signo > 0 else -racha,
        "ultima_sesion_alcista": bool(len(px) >= 2 and float(px.iloc[-1]) > float(px.iloc[-2])),
        "cierre_en_rango_alto": _r(_posicion_en_rango(df)),

        "beta": None, "rs_63d": None, "rs_126d": None, "rs_252d": None,
    }

    if referencia is not None:
        indic.update(_comparar(px, referencia))
    return indic


def _comparar(px: pd.Series, referencia: pd.Series) -> dict:
    """Beta y fuerza relativa frente al benchmark."""
    out = {"beta": None, "rs_63d": None, "rs_126d": None, "rs_252d": None}
    try:
        junto = pd.concat({"a": px.pct_change(), "b": referencia.pct_change()},
                          axis=1, join="inner").dropna()
        if len(junto) >= 60:
            cola = junto.tail(252)
            varianza = float(cola["b"].var())
            if varianza:
                out["beta"] = round(float(cola["a"].cov(cola["b"]) / varianza), 3)
    except Exception:
        pass
    for campo, sesiones in (("rs_63d", 63), ("rs_126d", 126), ("rs_252d", 252)):
        a, b = _retorno(px, sesiones), _retorno(referencia, sesiones)
        if a is not None and b is not None:
            out[campo] = round(a - b, 2)
    return out


# --------------------------------------------------------- Régimen de mercado --

def regimen_mercado(bench: pd.DataFrame | None, miedo: pd.DataFrame | None = None) -> dict:
    """Contexto: cómo está el mercado (determina los pesos de cada motor)."""
    info = {
        "precio": None, "sma200": None, "sobre_sma200": None,
        "cambio_1d": None, "ret_63d": None, "ret_252d": None,
        "dd_52w": None, "miedo": None, "etiqueta": "desconocido", "ajuste": {},
    }
    if bench is not None and not bench.empty and len(bench) >= 2:
        px = bench["Adj Close"].where(bench["Adj Close"].notna(), bench["Close"]).astype(float)
        cierre = bench["Close"].astype(float)
        info["precio"] = round(float(cierre.iloc[-1]), 2)
        info["cambio_1d"] = _r(_retorno(px, 1))
        info["ret_63d"] = _r(_retorno(px, 63))
        info["ret_252d"] = _r(_retorno(px, 252))
        if len(px) >= 200:
            sma200 = float(px.tail(200).mean())
            info["sma200"] = round(sma200, 2)
            info["sobre_sma200"] = float(px.iloc[-1]) > sma200
            dist = float(px.iloc[-1]) / float(px.tail(252).max()) - 1 if len(px) >= 252 else None
            info["dd_52w"] = _r(dist * 100 if dist is not None else None)

    if miedo is not None and not miedo.empty:
        try:
            serie = miedo["Adj Close"].where(miedo["Adj Close"].notna(),
                                             miedo["Close"]).astype(float)
            info["miedo"] = round(float(serie.iloc[-1]), 2)
        except Exception:
            pass

    sobre = info.get("sobre_sma200")
    vix = info.get("miedo")
    if sobre is True and (vix is None or vix < 20):
        info["etiqueta"] = "alcista"
    elif sobre is False:
        info["etiqueta"] = "correctivo"
    else:
        info["etiqueta"] = "neutral"

    # Ajuste de pesos según el entorno (multiplicadores).
    if info["etiqueta"] == "correctivo":
        info["ajuste"] = {"momentum": 0.6, "reversion": 1.2, "valor": 1.15, "catalizador": 0.9}
    elif info["etiqueta"] == "alcista":
        info["ajuste"] = {"momentum": 1.15, "reversion": 0.9, "valor": 1.0, "catalizador": 1.0}
    else:
        info["ajuste"] = {"momentum": 1.0, "reversion": 1.0, "valor": 1.0, "catalizador": 1.0}
    if vix is not None and vix >= 30:
        info["ajuste"]["catalizador"] = info["ajuste"].get("catalizador", 1.0) * 0.7
    return info


# ------------------------------------------------------------------ Utilidades --

def _r(valor, decimales: int = 2):
    if valor is None:
        return None
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, decimales)


def _pendiente(px: pd.Series, media: int, atrás: int) -> float | None:
    if len(px) < media + atrás:
        return None
    actual = float(px.tail(media).mean())
    previo = float(px.iloc[-(media + atrás):-atrás].mean())
    if not previo:
        return None
    return (actual / previo - 1) * 100


def _z(px: pd.Series, ventana: int) -> float | None:
    if len(px) < ventana:
        return None
    cola = px.tail(ventana)
    desviacion = float(cola.std())
    if not desviacion:
        return None
    return (float(cola.iloc[-1]) - float(cola.mean())) / desviacion


def _dias_desde_maximo(px: pd.Series) -> int | None:
    if len(px) < 2:
        return None
    maximo = float(px.max())
    for i in range(len(px) - 1, -1, -1):
        if float(px.iloc[i]) >= maximo:
            return len(px) - 1 - i
    return None


def _posicion_en_rango(df: pd.DataFrame) -> float | None:
    """0 = cerró en el mínimo del día, 1 = cerró en el máximo."""
    try:
        alto = float(df["High"].iloc[-1])
        bajo = float(df["Low"].iloc[-1])
        cierre = float(df["Close"].iloc[-1])
        if alto <= bajo:
            return None
        return (cierre - bajo) / (alto - bajo)
    except Exception:
        return None
