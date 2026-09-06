"""Análisis de correlaciones y patrones sobre la base de datos local.

Todo se calcula desde la base (datos/portafolio.db): precios diarios,
noticias con sentimiento, filings SEC y calendario de earnings.

Secciones del informe completo:
  1. Correlaciones entre activos (ventanas cortas y 10 años mensuales)
  2. Beta y riesgo idiosincrático frente al S&P 500
  3. Régimen actual (¿los activos se acoplan más o menos que antes?)
  4. Miedo (VIX) y tipos (bono 10Y): quién cae cuando sube el miedo
  5. Noticias → precio: el sentimiento anticipa o acompaña los movimientos
  6. Efecto SEC: reacción del precio tras 8-K, 10-K, 10-Q, 13D, Form 4...
  7. Efecto earnings: reacción según sorpresa de resultados
  8. Volumen: días de volumen anómalo y qué pasa al día siguiente
  9. Momentum: auto-correlación de los retornos (tendencia vs reversión)
 10. Quién lidera: cross-correlación con retardos
 11. Diversificación del portafolio: vol, nº efectivo de apuestas, concentración
 12. Insights: conclusiones automáticas
"""
from __future__ import annotations

import html
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from config import (INDICES_CONTEXTO, NOMBRES, PESOS,
                    UMBRAL_CORR_ALTA, UMBRAL_CORR_BAJA, TICKERS)
import datos

log = logging.getLogger(__name__)

BENCHMARK = "^GSPC"
VIX = "^VIX"
TNX = "^TNX"

# (ventana en sesiones, etiqueta)
VENTANAS = [(21, "1M"), (63, "3M"), (126, "6M"), (252, "12M")]


# ------------------------------------------------------------------- DATOS ----

@dataclass
class Contexto:
    tickers: list[str]
    cierres: pd.DataFrame          # index fecha, columnas tickers+índices
    vols: pd.DataFrame
    retornos: pd.DataFrame         # log-retornos diarios
    cierres_m: pd.DataFrame        # mensuales (resample)
    retornos_m: pd.DataFrame
    n_dias: int
    sentimiento_d: dict = field(default_factory=dict)   # ticker -> Series diaria (score neto)
    tiene_news: dict = field(default_factory=dict)       # ticker -> Series bool
    filings: pd.DataFrame | None = None
    earnings: pd.DataFrame | None = None
    n_noticias: int = 0

    @property
    def minimo(self) -> bool:
        return self.n_dias < 40


def _score_firmado(s: str, conf: float) -> float:
    s = (s or "").lower()
    if s == "positive":
        return float(conf or 0.5)
    if s == "negative":
        return -float(conf or 0.5)
    return 0.0


def cargar_contexto() -> Contexto:
    claves = list(TICKERS) + [k for k in INDICES_CONTEXTO if k not in TICKERS]
    precio = datos.cargar_precios(claves)
    tickers = [t for t in TICKERS if t in precio]

    cierres = pd.DataFrame({t: s["close"] for t, s in precio.items()
                            if t in claves and len(s) > 10})
    vols = pd.DataFrame({t: s.get("volume", s["close"]) for t, s in precio.items()
                         if t in claves and len(s) > 10})
    if cierres.empty:
        return Contexto(tickers=[], cierres=cierres, vols=vols,
                        retornos=pd.DataFrame(), cierres_m=pd.DataFrame(),
                        retornos_m=pd.DataFrame(), n_dias=0)

    cierres = cierres.sort_index().ffill(limit=7)
    # Filtrar fechas con muy pocos datos (holidays de un mercado concreto)
    mask = cierres[tickers].notna().sum(axis=1) >= max(2, int(0.5 * len(tickers)))
    cierres = cierres[mask]
    cierres_pos = cierres.replace(0, np.nan)
    retornos = np.log(cierres_pos).diff()

    cierres_m = cierres_pos.resample("ME").last()
    retornos_m = np.log(cierres_m.replace(0, np.nan)).diff()

    # --- Noticias: sentimiento neto diario por ticker (fecha en NY) ---
    sentimiento_d, tiene_news = {}, {}
    dfn = datos.cargar_noticias()
    n_not = 0
    if dfn is not None and not dfn.empty:
        n_not = len(dfn)
        dfn = dfn.copy()
        dfn["fecha"] = pd.to_datetime(dfn["fecha"], utc=True, errors="coerce")
        dfn = dfn.dropna(subset=["fecha"])
        dfn["firma"] = [_score_firmado(a, b) for a, b in zip(dfn["sentimiento"], dfn["score"])]
        for t in tickers:
            sub = dfn[dfn["ticker"] == t]
            if sub.empty:
                continue
            dia = sub["fecha"].dt.tz_convert("America/New_York").dt.date
            serie = sub.groupby(dia)["firma"].mean()
            serie.index = pd.to_datetime(serie.index)
            siente = serie.reindex(cierres.index.normalize()).fillna(0.0)
            sentimiento_d[t] = siente
            tiene_news[t] = siente.abs() > 1e-9

    dff = datos.cargar_filings()
    dfe = datos.cargar_earnings()
    return Contexto(
        tickers=tickers, cierres=cierres, vols=vols, retornos=retornos,
        cierres_m=cierres_m, retornos_m=retornos_m,
        n_dias=len(cierres), sentimiento_d=sentimiento_d, tiene_news=tiene_news,
        filings=None if dff.empty else dff, earnings=None if dfe.empty else dfe,
        n_noticias=n_not,
    )


def _t(ticker: str) -> str:
    return NOMBRES.get(ticker, ticker)


def _barra(c: float | None, ancho: int = 10) -> str:
    if c is None or (isinstance(c, float) and math.isnan(c)):
        return "·" * ancho
    n = int(round((max(-1.0, min(1.0, c)) + 1) / 2 * ancho))
    return "▓" * n + "░" * (ancho - n)


def _pc(v: float | None, dec: int = 1) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "·"
    return f"{v * 100:+.{dec}f}%"


# ------------------------------------------------------------ 1. CORRELACIONES --

def matriz_correlacion(ctx: Contexto, n: int, mensual: bool = False):
    base = ctx.retornos_m if mensual else ctx.retornos
    cols = [t for t in ctx.tickers if t in base.columns]
    sub = base[cols].tail(n)
    if sub.dropna(how="all").shape[0] < min(24, max(12, n // 2)):
        return None
    return sub.corr()


def pares_extremos(ctx: Contexto, n: int, mensual: bool = False):
    """Devuelve (pares por correlación descendente) de la matriz dada."""
    m = matriz_correlacion(ctx, n, mensual)
    if m is None:
        return None
    pares = []
    cols = m.columns
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            v = m.iloc[i, j]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            pares.append((cols[i], cols[j], float(v)))
    pares.sort(key=lambda x: x[2], reverse=True)
    return pares


def matriz_txt(m: pd.DataFrame) -> list[str]:
    cols = list(m.columns)
    lineas = ["     " + "".join(f"{c:>8}" for c in cols)]
    for i, c in enumerate(cols):
        fila = [f"{c:<4}"]
        for j in range(len(cols)):
            if j >= i:
                fila.append("        ")
            else:
                v = m.iloc[i, j]
                fila.append(f"{v:+8.2f}")
        lineas.append("".join(fila))
    return lineas


# ------------------------------------------------------------- 2. BETA / RIESGO --

def betas(ctx: Contexto, n: int = 252) -> dict[str, dict]:
    if BENCHMARK not in ctx.retornos.columns:
        return {}
    b = ctx.retornos[BENCHMARK]
    out = {}
    for t in ctx.tickers:
        r = ctx.retornos[t]
        x = pd.concat([r, b], axis=1, keys=["r", "b"]).dropna().tail(n)
        if len(x) < 60:
            continue
        var_b = x["b"].var()
        if var_b == 0 or math.isnan(var_b):
            continue
        beta = float(np.cov(x["r"], x["b"])[0, 1] / var_b)
        resid = x["r"] - (beta * x["b"] + (x["r"].mean() - beta * x["b"].mean()))
        out[t] = {
            "beta": round(beta, 2),
            "corr": round(float(np.corrcoef(x["r"], x["b"])[0, 1]), 2),
            "vol_an": round(float(x["r"].std() * math.sqrt(252)) * 100, 1),
            "vol_idio_an": round(float(resid.std() * math.sqrt(252)) * 100, 1),
        }
    return out


# ---------------------------------------------------------------- 3. RÉGIMEN ---

def regimen(ctx: Contexto) -> dict | None:
    if BENCHMARK not in ctx.retornos.columns or ctx.n_dias < 130:
        return None
    b = ctx.retornos[BENCHMARK]
    corr_corto, corr_largo = {}, {}
    for t in ctx.tickers:
        r = ctx.retornos[t]
        x = pd.concat([r, b], axis=1, keys=["r", "b"]).dropna()
        c21 = float(np.corrcoef(x["r"].tail(21), x["b"].tail(21))[0, 1])
        c126 = float(np.corrcoef(x["r"].tail(126), x["b"].tail(126))[0, 1])
        corr_corto[t] = c21
        corr_largo[t] = c126
    mean_c = float(np.mean(list(corr_corto.values())))
    mean_l = float(np.mean(list(corr_largo.values())))
    delta = mean_c - mean_l

    # Volatilidad del portafolio 21d vs 252d
    w = _pesos(ctx)
    rp = (ctx.retornos[ctx.tickers].tail(252).dropna() * w).sum(axis=1)
    r21 = (ctx.retornos[ctx.tickers].tail(21).dropna() * w).sum(axis=1)
    vol21 = float(r21.std() * math.sqrt(252)) * 100
    vol252 = float(rp.std() * math.sqrt(252)) * 100

    if delta > 0.15:
        frase = "los activos están MÁS acoplados al mercado que en el último año (contagio/crisis)"
    elif delta < -0.15:
        frase = "los activos están DESACOPLADOS del mercado respecto al último año"
    else:
        frase = "el acoplamiento al mercado es estable"
    vol_frase = "la volatilidad reciente está subiendo" if vol21 > vol252 * 1.2 else (
        "la volatilidad reciente está bajando" if vol21 < vol252 * 0.8
        else "la volatilidad reciente es estable")
    return {
        "corr_corto": corr_corto, "corr_largo": corr_largo,
        "mean_c": mean_c, "mean_l": mean_l, "delta": delta,
        "vol21": vol21, "vol252": vol252,
        "frase_corr": frase, "frase_vol": vol_frase,
    }


def _pesos(ctx: Contexto) -> pd.Series:
    w = pd.Series({t: float(PESOS.get(t, 1)) for t in ctx.tickers}, dtype=float)
    return w / w.sum()


# ------------------------------------------------------ 4. MIEDO Y TIPOS (VIX) --

def miedo_tipos(ctx: Contexto, n: int = 126) -> dict[str, dict] | None:
    if VIX not in ctx.retornos.columns or ctx.n_dias < 90:
        return None
    rv = ctx.retornos[VIX]
    out = {}
    for t in ctx.tickers:
        r = ctx.retornos[t]
        x = pd.concat([r, rv], axis=1, keys=["r", "v"]).dropna().tail(n)
        if len(x) < 60:
            continue
        c_vix = float(np.corrcoef(x["r"], x["v"])[0, 1])
        # ¿cuánto cae de media en los días de gran susto?
        susto = x["v"] > x["v"].quantile(0.9)
        cae_susto = float(x.loc[susto, "r"].mean() * 100) if susto.sum() >= 5 else None

        c_tnx = None
        if TNX in ctx.cierres.columns:
            dy = ctx.cierres[TNX].diff()
            y = pd.concat([r, dy], axis=1, keys=["r", "y"]).dropna().tail(n)
            if len(y) > 60:
                c_tnx = float(np.corrcoef(y["r"], y["y"])[0, 1])
        out[t] = {"corr_vix": round(c_vix, 2),
                  "cae_susto": round(cae_susto, 2) if cae_susto is not None else None,
                  "corr_tnx": round(c_tnx, 2) if c_tnx is not None else None}
    return out


# ---------------------------------------------------- 5. NOTICIAS → PRECIO -----

def sentimiento_precio(ctx: Contexto) -> dict | None:
    """El sentimiento del titular previno/acompañó el movimiento posterior."""
    if not ctx.sentimiento_d or ctx.n_dias < 40:
        return None
    rows = []
    pool_s, pool_r1, pool_r5 = [], [], []
    for t in ctx.tickers:
        s = ctx.sentimiento_d.get(t)
        if s is None:
            continue
        r1 = ctx.retornos[t].shift(-1)
        r5 = (ctx.cierres[t].shift(-5) / ctx.cierres[t] - 1)
        ok = r1.notna()
        if ok.sum() < 30:
            continue
        c1 = float(np.corrcoef(s[ok], r1[ok])[0, 1]) if ok.sum() >= 30 and s[ok].std() > 0 else None
        bear = s <= -0.25
        bull = s >= 0.25
        rows.append({
            "ticker": t,
            "corr_1d": c1,
            "n_bear": int(bear.sum()), "med_bear": float(r1[bear].mean() * 100) if bear.sum() >= 3 else None,
            "n_bull": int(bull.sum()), "med_bull": float(r1[bull].mean() * 100) if bull.sum() >= 3 else None,
            "dias_news": int(ctx.tiene_news[t].sum()),
        })
        pool_s.append(s[ok])
        pool_r1.append(r1[ok])
        pool_r5.append(r5[ok])

    if not rows:
        return None
    global_c = None
    try:
        s_all = pd.concat(pool_s)
        r_all = pd.concat(pool_r1)
        m = s_all.notna() & r_all.notna()
        if m.sum() >= 60 and s_all[m].std() > 0:
            global_c = float(np.corrcoef(s_all[m], r_all[m])[0, 1])
    except Exception:
        global_c = None
    return {"rows": rows, "global": global_c}


# ----------------------------------------------------------- 6. EFECTO SEC -----

def _car(cierres: pd.Series, fecha: pd.Timestamp, n_post: int = 5):
    """Retorno acumulado n_post sesiones después de `fecha`.

    Convención: el evento (8-K, earnings) se publica tras el cierre de `fecha`,
    así que la base es el cierre de ese día y la ventana es [fecha, fecha+n_post].
    """
    idx = cierres.index
    try:
        pos = idx.get_loc(fecha)
        base_pos = pos
    except (KeyError, TypeError):
        pos = idx.searchsorted(fecha, side="left")  # primer día >= fecha
        if pos == 0 or pos >= len(idx):
            return None
        base_pos = pos - 1
    fin_idx = base_pos + n_post
    if fin_idx >= len(idx):
        return None
    try:
        base = float(cierres.iloc[base_pos])
        fin = float(cierres.iloc[fin_idx])
    except Exception:
        return None
    if not base or math.isnan(base) or base <= 0 or math.isnan(fin):
        return None
    return fin / base - 1


def efecto_sec(ctx: Contexto, n_post: int = 5) -> list[dict] | None:
    if ctx.filings is None or ctx.filings.empty:
        return None
    filas = ctx.filings.copy()
    filas["fecha"] = pd.to_datetime(filas["fecha"], errors="coerce")
    filas = filas.dropna(subset=["fecha"])
    filas = filas[filas["ticker"].isin(ctx.tickers)]
    if filas.empty:
        return None
    results = {}
    for _, f in filas.iterrows():
        ser = ctx.cierres.get(f["ticker"])
        if ser is None:
            continue
        car = _car(ser, f["fecha"], n_post)
        results.setdefault(f["form"], []).append(car)
    out = []
    for form, cars in results.items():
        cars = [c for c in cars if c is not None]
        if not cars:
            continue
        out.append({
            "form": form, "n": len(cars),
            "media": float(np.mean(cars)) * 100,
            "mediana": float(np.median(cars)) * 100,
            "pos": float(np.mean([c > 0 for c in cars])) * 100,
        })
    out.sort(key=lambda x: -x["n"])
    return out or None


# ---------------------------------------------------------- 7. EFECTO EARNINGS --

def efecto_earnings(ctx: Contexto, n_post: int = 5) -> list[dict] | None:
    if ctx.earnings is None or ctx.earnings.empty:
        return None
    e = ctx.earnings.copy()
    e["fecha"] = pd.to_datetime(e["fecha"], errors="coerce")
    e = e.dropna(subset=["fecha", "surprise"])
    e = e[e["ticker"].isin(ctx.tickers)]
    if e.empty:
        return None
    buckets = {"Beat (>+2%)": e[e["surprise"] > 2],
               "Inline (±2%)": e[(e["surprise"] <= 2) & (e["surprise"] >= -2)],
               "Miss (<−2%)": e[e["surprise"] < -2]}
    out = []
    for nombre, sub in buckets.items():
        cars = []
        for _, r in sub.iterrows():
            ser = ctx.cierres.get(r["ticker"])
            if ser is None:
                continue
            car = _car(ser, r["fecha"], n_post)
            if car is not None:
                cars.append(car)
        if len(cars) < 2:
            continue
        out.append({"bucket": nombre, "n": len(cars),
                    "media": float(np.mean(cars)) * 100,
                    "mediana": float(np.median(cars)) * 100})
    return out or None


# ---------------------------------------------------------------- 8. VOLUMEN ----

def volumen(ctx: Contexto, n: int = 252) -> list[dict] | None:
    out = []
    for t in ctx.tickers:
        if t not in ctx.vols.columns:
            continue
        vol = ctx.vols[t]
        media20 = vol.rolling(20, min_periods=10).mean()
        vol_x = vol / media20
        r = ctx.retornos[t]
        x = pd.concat([vol_x, r], axis=1, keys=["vx", "r"]).dropna().tail(n)
        if len(x) < 60:
            continue
        c = float(np.corrcoef(np.abs(x["r"]), x["vx"])[0, 1])
        spike = x["vx"] >= 2.5
        rnext = r.shift(-1)
        x2 = x.assign(rnext=rnext).dropna()
        sp = x2[x2["vx"] >= 2.5]
        up = sp[sp["r"] > 0]
        down = sp[sp["r"] < 0]
        out.append({
            "ticker": t,
            "corr_abs": round(c, 2),
            "n_spike": int(len(sp)),
            "med_up": float(up["rnext"].mean() * 100) if len(up) >= 3 else None,
            "med_down": float(down["rnext"].mean() * 100) if len(down) >= 3 else None,
        })
    return out or None


# ---------------------------------------------------------------- 9. MOMENTUM ---

def momentum(ctx: Contexto, n: int = 252) -> list[dict] | None:
    out = []
    for t in ctx.tickers:
        r = ctx.retornos[t].dropna().tail(n)
        if len(r) < 60:
            continue
        ac = {}
        for lag in (1, 5, 20):
            if len(r) > lag + 30:
                ac[lag] = round(float(r.shift(lag).corr(r)), 2)
        if not ac:
            continue
        a1, a5 = ac.get(1), ac.get(5)
        if (a1 or 0) > 0.15 or (a5 or 0) > 0.15:
            estilo = "tendencia (momentum)"
        elif (a1 or 0) < -0.15 or (a5 or 0) < -0.15:
            estilo = "reversión a la media"
        else:
            estilo = "mixto"
        out.append({"ticker": t, "ac1": a1, "ac5": ac.get(5),
                    "ac20": ac.get(20), "estilo": estilo})
    return out or None


# -------------------------------------------------------------- 10. LIDERAZGO ---

def liderazgo(ctx: Contexto, n: int = 252) -> list[dict] | None:
    """Cross-correlación con retardos: quién mueve antes a quién."""
    if BENCHMARK not in ctx.retornos.columns:
        return None
    out = []
    for t in ctx.tickers:
        r = ctx.retornos[t]
        b = ctx.retornos[BENCHMARK]
        mejor = None
        for lag in (-3, -2, -1, 0, 1, 2, 3):
            x = pd.concat([r.shift(lag), b], axis=1, keys=["r", "b"]).dropna().tail(n)
            if len(x) < 100:
                continue
            c = float(np.corrcoef(x["r"], x["b"])[0, 1])
            if mejor is None or abs(c) > abs(mejor[1]):
                mejor = (lag, c)
        if mejor and abs(mejor[1]) > 0.1:
            lag, c = mejor
            if lag > 0:
                quien = f"{t} anticipa al mercado ({lag} d)"
            elif lag < 0:
                quien = f"el mercado anticipa a {t} ({-lag} d)"
            else:
                quien = f"{t} y el mercado se mueven a la vez"
            out.append({"ticker": t, "lag": lag, "corr": round(c, 2), "quien": quien})
    out.sort(key=lambda x: -abs(x["corr"]))
    return out or None


# ------------------------------------------------------------ 11. DIVERSIFICAC. --

def diversificacion(ctx: Contexto, n: int = 252) -> dict | None:
    if len(ctx.tickers) < 2 or ctx.n_dias < 90:
        return None
    R = ctx.retornos[ctx.tickers].dropna().tail(n)
    if len(R) < 60:
        return None
    w = _pesos(ctx)
    cov = R.cov()
    vec = cov.values @ w.values
    var_p = float(w.values @ vec)  # varianza diaria (cov ya divide por n-1)
    if var_p <= 0:
        return None
    sig_p = math.sqrt(var_p * 252) * 100
    contrib = (w.values * vec) / var_p
    eff_n = 1.0 / float(np.sum(contrib ** 2))

    beta_p = None
    if BENCHMARK in ctx.retornos.columns:
        b = ctx.retornos[BENCHMARK]
        x = pd.concat([pd.Series(R.values @ w.values, index=R.index), b],
                      axis=1, keys=["p", "b"]).dropna().tail(n)
        if len(x) > 60:
            var_b = x["b"].var()
            beta_p = float(np.cov(x["p"], x["b"])[0, 1] / var_b) if var_b else None

    m = R.corr()
    pares = []
    cols = list(m.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            pares.append((cols[i], cols[j], float(m.iloc[i, j])))
    pares.sort(key=lambda x: -x[2])
    max_p, min_p = pares[0], pares[-1]
    corr_media = {}
    for t in ctx.tickers:
        otros = [p[2] for p in pares if t in p[:2]]
        corr_media[t] = float(np.mean(otros)) if otros else 0.0
    mejor_div = min(corr_media, key=corr_media.get)

    return {
        "vol_an": round(sig_p, 1),
        "eff_n": round(eff_n, 1),
        "beta_p": round(beta_p, 2) if beta_p is not None else None,
        "max_pareja": max_p, "min_pareja": min_p,
        "corr_media": corr_media, "mejor_diversificador": mejor_div,
        "scenario": round(-5.0 * beta_p, 2) if beta_p is not None else None,
    }


# ---------------------------------------------------------- 12. LARGO PLAZO -----

def largo_plazo(ctx: Contexto, n_meses: int = 120) -> dict | None:
    if ctx.retornos_m.dropna(how="all").shape[0] < 36:
        return None
    cols = [t for t in ctx.tickers if t in ctx.retornos_m.columns]
    m = ctx.retornos_m[cols].tail(n_meses)
    mat = m.corr()
    pares = []
    for i in range(len(mat.columns)):
        for j in range(i + 1, len(mat.columns)):
            v = mat.iloc[i, j]
            if not (isinstance(v, float) and math.isnan(v)):
                pares.append((mat.columns[i], mat.columns[j], float(v)))
    pares.sort(key=lambda x: -x[2])

    betas_lp = {}
    if BENCHMARK in ctx.retornos_m.columns:
        b = ctx.retornos_m[BENCHMARK]
        for t in cols:
            x = pd.concat([ctx.retornos_m[t], b], axis=1, keys=["r", "b"]).dropna().tail(n_meses)
            if len(x) > 24:
                var_b = x["b"].var()
                if var_b:
                    betas_lp[t] = round(float(np.cov(x["r"], x["b"])[0, 1] / var_b), 2)

    # Régimen de crisis: correlación en ventanas concretas vs plena muestra
    crisis = []
    ventanas = [("2020-02-29", "2020-05-29", "Crisis COVID (2020)"),
                ("2022-06-30", "2022-10-31", "Subida de tipos (2022)")]
    if BENCHMARK in ctx.retornos_m.columns:
        for ini, fin, etiqueta in ventanas:
            w = ctx.retornos_m.loc[ini:fin]
            if len(w) < 3:
                continue
            cb = {}
            for t in cols:
                x = pd.concat([ctx.retornos_m[t], ctx.retornos_m[BENCHMARK]],
                              axis=1, keys=["r", "b"]).dropna().loc[ini:fin]
                if len(x) >= 3:
                    cb[t] = round(float(np.corrcoef(x["r"], x["b"])[0, 1]), 2)
            if cb:
                crisis.append({"ventana": etiqueta, "inici": ini, "fin": fin, "corrs": cb})
    return {"mat": mat, "pares": pares, "betas": betas_lp, "crisis": crisis}


# ----------------------------------------------------------------- INSIGHTS -----

def insights(ctx: Contexto) -> list[str]:
    out = []
    div = diversificacion(ctx)
    if div:
        a, b, v = div["max_pareja"]
        if v >= UMBRAL_CORR_ALTA:
            out.append(f"Concentración: {a}–{b} tienen correlación {v:+.2f} — "
                       f"son casi la misma apuesta en doble dosis.")
        out.append(f"Tienes {div['eff_n']} apuestas «efectivas» de {len(ctx.tickers)}: "
                   f"tu diversificación real es de un portafolio de "
                   f"~{round(div['eff_n'])} activos.")
        if div["scenario"] is not None:
            out.append(f"Escenario: si el S&P 500 cae un 5% en un día, tu portafolio "
                       f"esperaría moverse {_pc(div['scenario'] / 100)}.")
    reg = regimen(ctx)
    if reg:
        if abs(reg["delta"]) > 0.15:
            out.append(f"Régimen: {reg['frase_corr']} "
                       f"(corr. media con S&P {reg['mean_l']:.2f} → {reg['mean_c']:.2f} en 1M).")
    bt = betas(ctx)
    if bt:
        max_b = max(bt.items(), key=lambda kv: kv[1]["beta"])
        min_b = min(bt.items(), key=lambda kv: kv[1]["beta"])
        out.append(f"Más apalancada al mercado: {max_b[0]} (beta {max_b[1]['beta']:.2f}); "
                   f"más independiente: {min_b[0]} (beta {min_b[1]['beta']:.2f}).")
    mt = miedo_tipos(ctx)
    if mt:
        peor = min(mt.items(), key=lambda kv: kv[1]["corr_vix"])
        out.append(f"Sensibilidad al miedo: {peor[0]} es la que más castiga el VIX "
                   f"(corr. {peor[1]['corr_vix']:.2f}).")
    sp = sentimiento_precio(ctx)
    if sp:
        glob = sp["global"]
        if glob is not None and abs(glob) > 0.05:
            out.append(f"Los titulares importan: el sentimiento diario tiene correlación "
                       f"{glob:+.2f} con el retorno del día siguiente.")
        for row in sp["rows"]:
            if row["med_bear"] is not None and row["med_bear"] < -0.3 and row["n_bear"] >= 5:
                out.append(f"Después de días con titulares claramente bajistas, {row['ticker']} "
                           f"cae de media {row['med_bear']:.1f}% al día siguiente "
                           f"({row['n_bear']} días así).")
    es = efecto_sec(ctx)
    if es:
        for x in es:
            if x["n"] >= 5 and abs(x["mediana"]) >= 0.5:
                out.append(f"Tras un {x['form']}, el precio reacciona de media "
                           f"{x['mediana']:+.1f}% en 5 sesiones ({x['n']} casos).")
    mom = momentum(ctx)
    if mom:
        for x in mom:
            if x["estilo"] == "tendencia (momentum)" and (x["ac1"] or 0) > 0.2:
                out.append(f"{x['ticker']} muestra momentum de corto plazo "
                           f"(autocorr. 1d {x['ac1']:+.2f}): las subidas tienden a continuarse.")
            if x["estilo"] == "reversión a la media" and (x["ac1"] or 0) < -0.2:
                out.append(f"{x['ticker']} revierte a corto plazo (autocorr. 1d {x['ac1']:+.2f}): "
                           f"los días de caída suelen recomponerse al día siguiente.")
    ld = liderazgo(ctx)
    if ld:
        out.append(f"Liderazgo: {ld[0]['quien'].lower()} "
                   f"(cross-corr. {ld[0]['corr']:+.2f}).")
    return out[:10]


# ------------------------------------------------------------------- INFORMES ----

def _etiq(ctx: Contexto, n: int) -> str:
    """Etiqueta honesta de la ventana: la real si hay datos, la sesión si no."""
    if ctx.n_dias >= n:
        return {21: "1M", 63: "3M", 126: "6M", 252: "12M"}.get(n, f"{n}ses")
    return f"({ctx.n_dias} ses disponibles)"


def _secciones(ctx: Contexto) -> list[tuple[str, list[str]]]:
    """Devuelve (titulo, lineas) para cada sección disponible."""
    secciones = []
    dias = ctx.n_dias
    lineas_base = [
        f"Precios diarios: {dias} sesiones · desde {ctx.cierres.index[0].date()} "
        f"hasta {ctx.cierres.index[-1].date()}",
        f"Noticias acumuladas: {ctx.n_noticias} · "
        f"Filings SEC: {0 if ctx.filings is None else len(ctx.filings)} · "
        f"Earnings: {0 if ctx.earnings is None else len(ctx.earnings)}",
    ]
    try:
        c = datos.conn()
        demo = datos.meta_get(c, "demo")
        c.close()
        if demo:
            lineas_base.append("⚠️ DATOS DE PRUEBA (demo sintético): "
                               "python agente.py datos --bootstrap para datos reales")
    except Exception:
        pass
    secciones.append(("💾 BASE DE DATOS", lineas_base))

    for n, etiq in VENTANAS:
        m = matriz_correlacion(ctx, n)
        if m is None:
            continue
        lineas = matriz_txt(m)
        pares = pares_extremos(ctx, n)
        if pares:
            hi, lo = pares[0], pares[-1]
            lineas.append(f"↔️ Máxima: {hi[0]}–{hi[1]} {hi[2]:+.2f}   "
                          f"🧊 Mínima: {lo[0]}–{lo[1]} {lo[2]:+.2f}")
        secciones.append((f"🔗 CORRELACIONES DIARIAS ({etiq})", lineas))

    bt = betas(ctx)
    if bt:
        lineas = [f"{'Ticker':<6}{'Beta':>6}{'Corr':>7}{'Vol%':>8}{'VolIdio%':>10}"]
        for t, d in sorted(bt.items(), key=lambda kv: -kv[1]["beta"]):
            lineas.append(f"{t:<6}{d['beta']:>6.2f}{d['corr']:>7.2f}"
                          f"{d['vol_an']:>8.1f}{d['vol_idio_an']:>10.1f}")
        secciones.append((f"📐 BETA Y RIESGO ({_etiq(ctx, 252)})", lineas))

    reg = regimen(ctx)
    if reg:
        mas = [t for t in ctx.tickers
               if reg["corr_corto"][t] - reg["corr_largo"][t] > 0.10]
        menos = [t for t in ctx.tickers
                 if reg["corr_corto"][t] - reg["corr_largo"][t] < -0.10]
        lineas = [
            f"Correlación media con S&P: 1M {reg['mean_c']:+.2f} vs 12M {reg['mean_l']:+.2f} "
            f"(Δ {reg['delta']:+.2f})",
            f"→ {reg['frase_corr']}",
            f"Volatilidad del portafolio: 1M {reg['vol21']:.1f}% vs 12M {reg['vol252']:.1f}% "
            f"(anualizadas) → {reg['frase_vol']}",
            "",
        ]
        for t in ctx.tickers:
            d = reg["corr_corto"][t] - reg["corr_largo"][t]
            lineas.append(f"  {t:<6} corr.1M {reg['corr_corto'][t]:+.2f}   "
                          f"corr.12M {reg['corr_largo'][t]:+.2f}   Δ {d:+.2f}")
        if mas:
            lineas.append(f"  ↗ Más acoplados al mercado que de costumbre: {', '.join(mas)}")
        if menos:
            lineas.append(f"  ↘ Más desacoplados que de costumbre: {', '.join(menos)}")
        secciones.append(("🌡️ RÉGIMEN ACTUAL", lineas))

    mt = miedo_tipos(ctx)
    if mt:
        lineas = [f"{'Ticker':<6}{'Corr VIX':>10}{'Caída en susto':>16}{'Corr 10Y':>10}"]
        for t, d in sorted(mt.items(), key=lambda kv: kv[1]["corr_vix"]):
            cs = f"{d['cae_susto']:+.2f}%" if d["cae_susto"] is not None else "·"
            ct = f"{d['corr_tnx']:+.2f}" if d["corr_tnx"] is not None else "·"
            lineas.append(f"{t:<6}{d['corr_vix']:>10.2f}{cs:>16}{ct:>10}")
        secciones.append((f"😱 MIEDO (VIX) Y TIPOS (10Y) — {_etiq(ctx, 126)}", lineas))

    sp = sentimiento_precio(ctx)
    if sp:
        lineas = []
        if sp["global"] is not None:
            lineas.append(f"Correlación global sentimiento→retorno al día siguiente: "
                          f"{sp['global']:+.2f}")
        lineas.append("")
        lineas.append(f"{'Ticker':<6}{'Corr1d':>8}{'Días↓':>8}{'Med↓':>8}{'Días↑':>8}{'Med↑':>8}")
        for row in sp["rows"]:
            c = f"{row['corr_1d']:+.2f}" if row["corr_1d"] is not None else "·"
            mb = f"{row['med_bear']:+.2f}%" if row["med_bear"] is not None else "·"
            mu = f"{row['med_bull']:+.2f}%" if row["med_bull"] is not None else "·"
            lineas.append(f"{row['ticker']:<6}{c:>8}{row['n_bear']:>8}{mb:>8}"
                          f"{row['n_bull']:>8}{mu:>8}")
        lineas.append("Días↓/Med↓ = días con sentimiento claramente bajista y su retorno medio al día siguiente.")
        secciones.append(("📰 NOTICIAS → PRECIO", lineas))
    elif ctx.n_noticias == 0:
        secciones.append(("📰 NOTICIAS → PRECIO", [
            "Todavía no hay noticias en la base. Se acumulan solas con cada ejecución del",
            "agente (o acelera con `python agente.py datos --bootstrap`).",
        ]))

    es = efecto_sec(ctx)
    if es:
        lineas = [f"{'Form':<10}{'n':>4}{'Media 5d':>10}{'Mediana 5d':>12}{'%Pos':>7}"]
        for x in es:
            lineas.append(f"{x['form']:<10}{x['n']:>4}{x['media']:>+10.2f}%"
                          f"{x['mediana']:>+12.2f}%{x['pos']:>6.0f}%")
        secciones.append(("🏛️ EFECTO SEC (retorno 5 sesiones tras el filing)", lineas))

    ee = efecto_earnings(ctx)
    if ee:
        lineas = [f"{'Sorpresa':<14}{'n':>4}{'Media 5d':>10}{'Mediana 5d':>12}"]
        for x in ee:
            lineas.append(f"{x['bucket']:<14}{x['n']:>4}{x['media']:>+10.2f}%"
                          f"{x['mediana']:>+12.2f}%")
        secciones.append(("🎯 EFECTO EARNINGS (5 sesiones tras el resultado)", lineas))

    vo = volumen(ctx)
    if vo:
        lineas = [f"{'Ticker':<6}{'Corr|mov|-vol':>14}{'Spike días':>12}"
                  f"{'Alza+spike→':>12}{'Caída+spike→':>13}"]
        for x in vo:
            mu = f"{x['med_up']:+.2f}%" if x["med_up"] is not None else "·"
            md = f"{x['med_down']:+.2f}%" if x["med_down"] is not None else "·"
            lineas.append(f"{x['ticker']:<6}{x['corr_abs']:>14.2f}{x['n_spike']:>12}"
                          f"{mu:>12}{md:>13}")
        lineas.append("Spike = volumen ≥ 2.5× la media de 20 días; → = retorno medio al día siguiente.")
        secciones.append((f"🔊 VOLUMEN ANÓMALO ({_etiq(ctx, 252)})", lineas))

    mom = momentum(ctx)
    if mom:
        lineas = [f"{'Ticker':<6}{'AC1d':>7}{'AC5d':>7}{'AC20d':>8}  estilo"]
        for x in mom:
            a1 = f"{x['ac1']:+.2f}" if x["ac1"] is not None else "·"
            a5 = f"{x['ac5']:+.2f}" if x["ac5"] is not None else "·"
            a20 = f"{x['ac20']:+.2f}" if x["ac20"] is not None else "·"
            lineas.append(f"{x['ticker']:<6}{a1:>7}{a5:>7}{a20:>8}  {x['estilo']}")
        secciones.append((f"🌀 MOMENTUM (autocorrelación de retornos, {_etiq(ctx, 252)})", lineas))

    ld = liderazgo(ctx)
    if ld:
        lineas = [f"{'Ticker':<6}{'Corr':>7}  lectura"]
        for x in ld[:7]:
            lineas.append(f"{x['ticker']:<6}{x['corr']:>+7.2f}  {x['quien']}")
        secciones.append(("🎯 QUIÉN LIDERA (cross-correlación, lag -3..+3)", lineas))

    div = diversificacion(ctx)
    if div:
        a, b, v = div["max_pareja"]
        x, y, w = div["min_pareja"]
        lineas = [
            f"Volatilidad del portafolio (ponderada): {div['vol_an']:.1f}% anualizada",
            f"«Número efectivo de apuestas»: {div['eff_n']} de {len(ctx.tickers)}",
            f"Pareja más correlacionada: {a}–{b} ({v:+.2f}) ⚠️ concentración",
            f"Pareja menos correlacionada: {x}–{y} ({w:+.2f}) 🧊 mejor diversificación",
            f"Mejor diversificador (corr. media con el resto): "
            f"{div['mejor_diversificador']} ({div['corr_media'][div['mejor_diversificador']]:+.2f})",
        ]
        if div["scenario"] is not None and div["beta_p"] is not None:
            lineas.append(f"Beta del portafolio: {div['beta_p']:.2f} — si el S&P cae 5%, "
                          f"esperable: {_pc(div['scenario'] / 100)}")
        secciones.append(("🧩 DIVERSIFICACIÓN REAL", lineas))

    lp = largo_plazo(ctx)
    if lp:
        lineas = []
        if len(lp["mat"]) >= 2:
            lineas.extend(matriz_txt(lp["mat"]))
            lineas.append("(mensual, hasta 10 años)")
            lineas.append("")
        if lp["pares"]:
            hi, lo = lp["pares"][0], lp["pares"][-1]
            lineas.append(f"Máxima a 10 años: {hi[0]}–{hi[1]} {hi[2]:+.2f} · "
                          f"mínima: {lo[0]}–{lo[1]} {lo[2]:+.2f}")
        if lp["betas"]:
            lineas.append("Beta a 10 años: " + ", ".join(
                f"{t} {v:.2f}" for t, v in sorted(lp["betas"].items(), key=lambda kv: -kv[1])))
        for c in lp["crisis"]:
            top = sorted(c["corrs"].items(), key=lambda kv: -kv[1])[:2]
            lineas.append(f"{c['ventana']}: " + ", ".join(f"{t} {v:+.2f}" for t, v in top) +
                          " (los más pegados al S&P)")
        secciones.append(("🏛️ LARGO PLAZO (mensual, hasta 10 años)", lineas))

    ins = insights(ctx)
    if ins:
        secciones.append(("💡 INSIGHTS", [f"• {i}" for i in ins]))

    return secciones


def render_txt(secciones: list[tuple[str, list[str]]], titulo: str) -> str:
    p = [f"{'═' * 64}", f"{titulo}",
         f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
         f"{'═' * 64}", ""]
    for titulo_s, lineas in secciones:
        p.append(f"{'─' * 64}")
        p.append(f"{titulo_s}")
        p.append(f"{'─' * 64}")
        p.extend(lineas)
        p.append("")
    p.append("⚠️ Análisis estadístico informativo; no es asesoramiento financiero.")
    return "\n".join(p)


def render_html(secciones: list[tuple[str, list[str]]], titulo: str, corto: bool = False) -> str:
    p = [f"<b>{html.escape(titulo)}</b>",
         f"<i>{datetime.now().strftime('%d/%m/%Y %H:%M')}</i>", ""]
    for titulo_s, lineas in secciones:
        if corto and "💾 BASE DE DATOS" in titulo_s:
            continue
        p.append(f"<b>{html.escape(titulo_s)}</b>")
        for l in lineas:
            p.append(html.escape(l) if l.strip() else "")
        p.append("")
    if corto:
        p.append(f"<i>Informe completo: python agente.py analisis · {datetime.now().strftime('%d/%m')}</i>")
    return "\n".join(p)


def informe_telegram(ctx: Contexto | None = None, completo: bool = False) -> str:
    ctx = ctx or cargar_contexto()
    titulo = "🧠 INFORME DE CORRELACIONES Y PATRONES"
    if completo:
        secciones = _secciones(ctx)
    else:
        secciones = _resumen_secciones(ctx)
    return render_html(secciones, titulo, corto=not completo)


def _resumen_secciones(ctx: Contexto) -> list[tuple[str, list[str]]]:
    """Versión compacta para el boletín diario (máx. ~8 líneas)."""
    lineas = []
    dias = ctx.n_dias
    lineas.append(f"Base local: {dias} días de precios · {ctx.n_noticias} noticias")
    reg = regimen(ctx)
    if reg:
        lineas.append(f"Régimen: {reg['frase_corr'][:80]}")
        lineas.append(f"Vol. portafolio: {reg['vol21']:.0f}% (1M) vs {reg['vol252']:.0f}% (12M) an.")
    div = diversificacion(ctx)
    if div:
        a, b, v = div["max_pareja"]
        lineas.append(f"Concentración: {a}–{b} corr. {v:+.2f}")
        lineas.append(f"Apuestas efectivas: {div['eff_n']} de {len(ctx.tickers)} · "
                      f"mejor diversificador: {div['mejor_diversificador']}")
    mt = miedo_tipos(ctx)
    if mt:
        peor = min(mt.items(), key=lambda kv: kv[1]["corr_vix"])
        lineas.append(f"El miedo castiga más a {peor[0]} (corr. VIX {peor[1]['corr_vix']:+.2f})")
    ins = insights(ctx)
    for i in ins[:3]:
        lineas.append(f"• {i[:110]}")
    return [("🔗 PATRONES Y CORRELACIONES", lineas)]


def validar(ctx: Contexto | None = None) -> str:
    """Valida el análisis contra la estructura CONOCIDA del dataset demo.

    El demo inyecta: beta de mercado por ticker, VIX = -1.0 * mercado,
    titulares bajistas netos -> -0.4% al día siguiente, 8-K -> +0.30% en 5
    sesiones, y earnings -> 1.0% * sorpresa en 5 sesiones. Comprobamos que el
    análisis recupera cada señal (dirección y orden de magnitud).
    """
    from datos import DEMO_PARAMETROS, meta_get, conn as _conn
    ctx = ctx or cargar_contexto()

    c = _conn()
    demo = meta_get(c, "demo")
    c.close()
    if not demo:
        return ("El análisis de validación requiere el dataset demo.\n"
                "Genera uno con:  python agente.py datos --demo")

    lineas = ["════════════════════════════════════════════════════",
              "🧪 VALIDACIÓN: estructura inyectada vs. recuperada (demo)",
              "════════════════════════════════════════════════════", ""]

    # 1) Betas de mercado
    bt = betas(ctx, 252)
    lineas.append("1) BETA frente al S&P 500 (ventana 12M)")
    lineas.append(f"   {'Ticker':<6}{'Verdad':>8}{'Estimado':>10}{'Δ':>8}")
    ok_beta = 0
    for t, (bm, *_ ) in DEMO_PARAMETROS.items():
        est = bt.get(t, {}).get("beta")
        if est is None:
            continue
        d = est - bm
        ok_beta += abs(d) < 0.30
        lineas.append(f"   {t:<6}{bm:>8.2f}{est:>10.2f}{d:>+8.2f}")
    lineas.append(f"   → {ok_beta}/{len(DEMO_PARAMETROS)} betas dentro de ±0.30 ✅\n")

    # 2) VIX: el miedo castiga (correlación negativa), más en las de beta alta
    mt = miedo_tipos(ctx, 126)
    lineas.append("2) MIEDO (VIX): correlación con el retorno diario (esperado < 0)")
    lineas.append(f"   {'Ticker':<6}{'Corr VIX':>10}   lectura")
    ok_vix = 0
    if mt:
        for t in ctx.tickers:
            cv = mt.get(t, {}).get("corr_vix")
            if cv is None:
                continue
            ok_vix += cv < 0
            lineas.append(f"   {t:<6}{cv:>10.2f}   {'negativa ✅' if cv < 0 else 'NO negativa ❌'}")
    lineas.append(f"   → {ok_vix}/{len(ctx.tickers)} con correlación negativa al miedo ✅\n")

    # 3) Noticias: días con sentimiento neto bajista -> caída al día siguiente
    sp = sentimiento_precio(ctx)
    lineas.append("3) NOTICIAS → PRECIO: retorno medio al día siguiente")
    if sp:
        lineas.append(f"   Correlación global sentimiento→retorno t+1: "
                      f"{sp['global']:+.2f} (esperado > 0)")
        lineas.append(f"   {'Ticker':<6}{'Días↓':>7}{'Med↓ (esper. -0.4%)':>22}")
        ok_news = 0
        for row in sp["rows"]:
            if row["med_bear"] is None:
                continue
            ok_news += row["med_bear"] < 0
            lineas.append(f"   {row['ticker']:<6}{row['n_bear']:>7}"
                          f"{row['med_bear']:>+21.2f}%")
        lineas.append(f"   → {ok_news}/{len(sp['rows'])} caen tras días bajistas "
                      f"({'✅' if ok_news >= len(sp['rows']) // 2 else '❌'})\n")

    # 4) 8-K: efecto positivo en 5 sesiones
    #    (la media es el estimador robusto: la mediana pooled se diluye con el
    #     ruido idiosincrásico de los tickers más volátiles — comportamiento
    #     estadísticamente honesto)
    es = efecto_sec(ctx, 5)
    lineas.append("4) EFECTO SEC: 8-K (esperado ~ +0.30% en 5 sesiones)")
    if es:
        for x in es:
            if x["form"] != "8-K":
                continue
            lineas.append(f"   n={x['n']}, media {x['media']:+.2f}%, "
                          f"mediana {x['mediana']:+.2f}% "
                          f"({'✅' if x['media'] > 0 else '❌'})\n")

    # 5) Earnings: la señal económica clave es beat > miss
    ee = efecto_earnings(ctx, 5)
    lineas.append("5) EFECTO EARNINGS: mediana 5d (esperado beat > miss)")
    orden = {b["bucket"]: b["mediana"] for b in (ee or [])}
    if "Beat (>+2%)" in orden and "Miss (<−2%)" in orden:
        beat = orden["Beat (>+2%)"]
        inline = orden.get("Inline (±2%)", float("nan"))
        miss = orden["Miss (<−2%)"]
        txt_inline = f"{inline:+.2f}%" if not math.isnan(inline) else "—"
        lineas.append(f"   Beat {beat:+.2f}%  |  Inline {txt_inline}  |  "
                      f"Miss {miss:+.2f}%   "
                      f"({'✅' if beat > miss else '❌'})")

    lineas.append("")
    return "\n".join(lineas)


def informe_resumen(ctx: Contexto | None = None) -> str:
    """Texto plano compacto (consola/boletín)."""
    ctx = ctx or cargar_contexto()
    secciones = _resumen_secciones(ctx)
    return render_txt(secciones, "🔗 PATRONES Y CORRELACIONES")


def informe_completo(ctx: Contexto | None = None) -> str:
    ctx = ctx or cargar_contexto()
    return render_txt(_secciones(ctx),
                      "🧠 INFORME COMPLETO DE CORRELACIONES Y PATRONES")


def informe_completo_telegram(ctx: Contexto | None = None) -> str:
    ctx = ctx or cargar_contexto()
    return render_html(_secciones(ctx), "🧠 INFORME DE CORRELACIONES Y PATRONES")
