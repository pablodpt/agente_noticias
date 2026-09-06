"""Base de datos local (SQLite) para exprimir los datos en el tiempo.

Tablas:
  precios   OHLCV diario por ticker (incluye los índices de contexto)
  noticias  titulares con sentimiento (histórico que se acumula)
  filings   presentaciones SEC (10-K, 10-Q, 8-K, 13D/G, Form 4...)
  earnings  fechas de resultados con sorpresa EPS (si Yahoo las publica)
  meta      estado interno (última persistencia, etc.)

El agente persiste datos en cada ejecución (ver persistir_hoy) y el comando
`python agente.py datos --bootstrap` rellena la base con años de histórico
para poder calcular correlaciones desde el primer día.
"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd

from config import (AÑOS_BOOTSTRAP, ARCHIVO_DATOS, INDICES_CONTEXTO,
                    PERSISTIR_CADA_HORAS, TICKERS)

log = logging.getLogger(__name__)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS precios (
    ticker     TEXT NOT NULL,
    fecha      TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    adj_close  REAL,
    volume     REAL,
    PRIMARY KEY (ticker, fecha)
);
CREATE INDEX IF NOT EXISTS idx_precios_fecha ON precios (fecha);

CREATE TABLE IF NOT EXISTS noticias (
    id           TEXT PRIMARY KEY,
    ticker       TEXT,
    titulo       TEXT,
    resumen      TEXT,
    link         TEXT,
    fuente       TEXT,
    fecha        TEXT,
    sentimiento  TEXT,
    score        REAL,
    urgente      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_noticias_ticker ON noticias (ticker, fecha);

CREATE TABLE IF NOT EXISTS filings (
    id           TEXT PRIMARY KEY,
    ticker       TEXT,
    form         TEXT,
    descripcion  TEXT,
    fecha        TEXT,
    link         TEXT,
    urgente      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_filings_ticker ON filings (ticker, fecha);

CREATE TABLE IF NOT EXISTS earnings (
    ticker       TEXT NOT NULL,
    fecha        TEXT NOT NULL,
    actual       REAL,
    estimate     REAL,
    surprise     REAL,
    PRIMARY KEY (ticker, fecha)
);

CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def conn(ruta: str | None = None) -> sqlite3.Connection:
    ruta = ruta or ARCHIVO_DATOS
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    c = sqlite3.connect(ruta)
    c.executescript(ESQUEMA)
    return c


# ------------------------------------------------------------------- GUARDAR --

def _f(v):
    try:
        if v is None:
            return None
        v = float(v)
        return None if pd.isna(v) else v
    except Exception:
        return None


def normalizar_precios(df: pd.DataFrame) -> pd.DataFrame:
    """Pasa columnas de yfinance (Open/High/...) al formato de la base (minúsculas)."""
    if df is None or df.empty:
        return df
    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low", "Close": "close",
        "Adj Close": "adj_close", "Volume": "volume"})
    if "adj_close" not in df.columns and "close" in df.columns:
        df["adj_close"] = df["close"]
    return df


def guardar_precios(c, ticker: str, df: pd.DataFrame) -> int:
    """Upsert de un DataFrame OHLCV (índice = fecha, columnas open/high/low/close/volume)."""
    if df is None or df.empty:
        return 0
    df = normalizar_precios(df)
    filas = []
    for d, r in df.iterrows():
        fecha = d.date() if hasattr(d, "date") else pd.Timestamp(d).date()
        filas.append((ticker, fecha.isoformat(),
                      _f(r.get("open")), _f(r.get("high")), _f(r.get("low")),
                      _f(r.get("close")), _f(r.get("adj_close")), _f(r.get("volume"))))
    c.executemany(
        "INSERT OR REPLACE INTO precios (ticker, fecha, open, high, low, close, adj_close, volume) "
        "VALUES (?,?,?,?,?,?,?,?)", filas)
    c.commit()
    return len(filas)


def guardar_noticias(c, lista: list[dict]) -> int:
    n = 0
    for x in lista or []:
        c.execute(
            "INSERT OR REPLACE INTO noticias (id, ticker, titulo, resumen, link, fuente, fecha, sentimiento, score, urgente) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (x["id"], x.get("ticker"), x.get("titulo"), x.get("resumen"), x.get("link"),
             x.get("fuente"), (x["fecha"].isoformat() if x.get("fecha") else None),
             x.get("sentimiento"), _f(x.get("score")), int(bool(x.get("urgente")))))
        n += 1
    c.commit()
    return n


def guardar_filings(c, lista: list[dict]) -> int:
    n = 0
    for x in lista or []:
        c.execute(
            "INSERT OR REPLACE INTO filings (id, ticker, form, descripcion, fecha, link, urgente) "
            "VALUES (?,?,?,?,?,?,?)",
            (x["id"], x.get("ticker"), x.get("form"), x.get("descripcion"),
             x["fecha"].isoformat() if x.get("fecha") else None, x.get("link"),
             int(bool(x.get("urgente")))))
        n += 1
    c.commit()
    return n


def guardar_earnings(c, ticker: str, df: pd.DataFrame) -> int:
    """df con índice fecha y columnas (actual, estimate, surprise) de get_earnings_dates."""
    if df is None or df.empty:
        return 0
    n = 0
    for d, r in df.iterrows():
        try:
            fecha = d.date().isoformat()
        except Exception:
            continue
        c.execute(
            "INSERT OR REPLACE INTO earnings (ticker, fecha, actual, estimate, surprise) VALUES (?,?,?,?,?)",
            (ticker, fecha, _f(r.get("actual")), _f(r.get("estimate")), _f(r.get("surprise"))))
        n += 1
    c.commit()
    return n


def meta_get(c, clave: str) -> str | None:
    r = c.execute("SELECT valor FROM meta WHERE clave=?", (clave,)).fetchone()
    return r[0] if r else None


def meta_set(c, clave: str, valor: str):
    c.execute("INSERT OR REPLACE INTO meta (clave, valor) VALUES (?,?)", (clave, valor))
    c.commit()


# --------------------------------------------------------------------- CARGAR --

def cargar_precios(tickers: list[str], desde: str | None = None) -> dict[str, pd.DataFrame]:
    out = {}
    for t in tickers:
        c = conn()
        try:
            if desde:
                df = pd.read_sql_query(
                    "SELECT fecha, open, high, low, close, adj_close, volume FROM precios "
                    "WHERE ticker=? AND fecha>=? ORDER BY fecha", c, params=(t, desde))
            else:
                df = pd.read_sql_query(
                    "SELECT fecha, open, high, low, close, adj_close, volume FROM precios "
                    "WHERE ticker=? ORDER BY fecha", c, params=(t,))
            if df.empty:
                continue
            df["fecha"] = pd.to_datetime(df["fecha"])
            out[t] = df.set_index("fecha")
        finally:
            c.close()
    return out


def _tabla(c, tabla: str) -> pd.DataFrame:
    return pd.read_sql_query(f"SELECT * FROM {tabla} ORDER BY fecha", c)


def cargar_noticias(desde: str | None = None) -> pd.DataFrame:
    c = conn()
    try:
        if desde:
            return pd.read_sql_query("SELECT * FROM noticias WHERE fecha>=? ORDER BY fecha", c,
                                     params=(desde,))
        return _tabla(c, "noticias")
    finally:
        c.close()


def cargar_filings() -> pd.DataFrame:
    c = conn()
    try:
        return _tabla(c, "filings")
    finally:
        c.close()


def cargar_earnings() -> pd.DataFrame:
    c = conn()
    try:
        df = pd.read_sql_query("SELECT * FROM earnings ORDER BY fecha", c)
        return df
    finally:
        c.close()


def ultimo_dia(c, ticker: str) -> str | None:
    r = c.execute("SELECT MAX(fecha) FROM precios WHERE ticker=?", (ticker,)).fetchone()
    return r[0] if r and r[0] else None


def resumen() -> dict:
    c = conn()
    try:
        out = {}
        for tabla, filtro in [("precios", "COUNT(DISTINCT ticker)"),
                              ("noticias", "COUNT(*)"),
                              ("filings", "COUNT(*)"),
                              ("earnings", "COUNT(*)")]:
            r = c.execute(f"SELECT {filtro}, MIN(fecha), MAX(fecha) FROM {tabla}").fetchone()
            out[tabla] = {"filas": r[0] or 0, "desde": r[1], "hasta": r[2]}
        r = c.execute("SELECT SUM(filas) FROM (SELECT COUNT(*) AS filas FROM precios GROUP BY ticker)").fetchone()
        out["precios"]["días"] = r[0] if r else 0
        out["ultimo_persist"] = meta_get(c, "ultimo_persist")
        return out
    finally:
        c.close()


# ----------------------------------------------------------- IMPORTAR (externo) --
# Sirve para cargar datos descargados fuera del agente (p. ej. por un script
# o por una red que no permite las llamadas directas):
#   DIR/<TICKER>__1d.json   -> JSON de la API de chart de Yahoo (cualquier rango)
#   DIR/<TICKER>__1mo.json  -> idem, para datos mensuales
#   DIR/<TICKER>__rss.txt   -> RSS de Google News volcado a texto plano

def importar_yahoo_json(c, ticker: str, ruta: str) -> int:
    with open(ruta, encoding="utf-8") as f:
        data = json.load(f)
    res = data["chart"]["result"][0]
    meta = res.get("meta", {})
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    adj = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []
    off = meta.get("gmtoffset", -14400)
    tz = timezone(timedelta(seconds=off))
    filas = []
    for i, t in enumerate(ts):
        fecha = datetime.fromtimestamp(t, tz=tz).date().isoformat()
        filas.append((ticker, fecha,
                      _f((q.get("open") or [None] * len(ts))[i]),
                      _f((q.get("high") or [None] * len(ts))[i]),
                      _f((q.get("low") or [None] * len(ts))[i]),
                      _f((q.get("close") or [None] * len(ts))[i]),
                      _f((adj or [None] * len(ts))[i]),
                      _f((q.get("volume") or [None] * len(ts))[i])))
    c.executemany(
        "INSERT OR REPLACE INTO precios (ticker, fecha, open, high, low, close, adj_close, volume) "
        "VALUES (?,?,?,?,?,?,?,?)", filas)
    c.commit()
    return len(filas)


def importar_rss_google(c, ticker: str, ruta: str) -> int:
    """Parsea el volcado (markdown-ificado) de un RSS de Google News."""
    import re
    with open(ruta, encoding="utf-8") as f:
        texto = f.read()
    items = re.findall(
        r'<a href="(https://news\.google\.com/rss/articles/[^"]+)">([^<]+)</a>'
        r'&nbsp;&nbsp;<font color="#6f6f6f">([^<]+)</font>', texto)
    fechas = re.findall(r'([A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT)', texto)
    n = 0
    for i, (link, titulo, fuente) in enumerate(items):
        titulo = titulo.strip()
        if not titulo:
            continue
        fecha = None
        if i < len(fechas):
            try:
                fecha = datetime.strptime(fechas[i], "%a, %d %b %Y %H:%M:%S GMT",
                                          ).replace(tzinfo=timezone.utc)
            except Exception:
                fecha = None
        from noticias import _clave
        from sentimiento import analizar
        etiqueta, score = analizar(f"{titulo}. {fuente}")
        n_ = guardar_noticias(c, [{
            "id": f"news:{ticker}:{_clave(titulo)}",
            "ticker": ticker, "titulo": titulo, "resumen": "", "link": link,
            "fuente": fuente, "fecha": fecha, "sentimiento": etiqueta,
            "score": score, "urgente": False,
        }])
        n += n_
    return n


def importar_csv(c, ticker: str, ruta: str) -> int:
    """CSV compacto: filas `ts,close[,volume]` (ts = epoch unix de apertura)."""
    filas = []
    tz = timezone(timedelta(seconds=-14400))  # EDT por defecto (mercados US)
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("ts,"):
                continue
            partes = [p for p in linea.split(",")]
            try:
                ts = int(partes[0])
                close = float(partes[1])
                volumen = float(partes[2]) if len(partes) > 2 and partes[2] else None
            except (ValueError, IndexError):
                continue
            fecha = datetime.fromtimestamp(ts, tz=tz).date().isoformat()
            filas.append((ticker, fecha, close, close, close, close, close, volumen))
    c.executemany(
        "INSERT OR REPLACE INTO precios (ticker, fecha, open, high, low, close, adj_close, volume) "
        "VALUES (?,?,?,?,?,?,?,?)", filas)
    c.commit()
    return len(filas)


def _titulo_rss_yahoo(texto: str) -> str:
    """Separa título de descripción en el volcado plano del RSS de Yahoo."""
    for i in range(min(150, len(texto) - 1)):
        a, b = texto[i], texto[i + 1]
        if (a.islower() or a.isdigit()) and b.isupper():
            return texto[:i + 1].strip()
    for i in range(min(150, len(texto) - 1)):
        if texto[i] == "." and texto[i + 1].isupper():
            return texto[:i + 1].strip()
    return texto[:120].strip()


def importar_rss_yahoo(c, ticker: str, ruta: str) -> int:
    """Parsea el volcado plano del RSS de Yahoo Finance (feeds.finance.yahoo.com)."""
    import re
    with open(ruta, encoding="utf-8") as f:
        texto = f.read()
    patron = re.compile(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
        r"[^A-Za-z]*?"
        r"([A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} \+0000)"
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
    # pares (uuid, fecha); el bloque de texto termina en el siguiente uuid
    marcas = [(m.start(1), m.group(2)) for m in patron.finditer(texto)]
    lista = []
    for i, (pos, fecha_txt) in enumerate(marcas):
        # el texto del item va desde el uuid hasta el siguiente uuid (o 600 chars)
        fin = marcas[i + 1][0] if i + 1 < len(marcas) else min(pos + 600, len(texto))
        bloque = texto[pos:fin]
        m = re.search(re.escape(fecha_txt) + r"(.{10,})", bloque)
        if not m:
            continue
        titulo = _titulo_rss_yahoo(m.group(1))
        if len(titulo) < 15:
            continue
        try:
            fecha = datetime.strptime(fecha_txt, "%a, %d %b %Y %H:%M:%S +0000",
                                      ).replace(tzinfo=timezone.utc)
        except ValueError:
            fecha = None
        from noticias import _clave
        from sentimiento import analizar
        etiqueta, score = analizar(titulo)
        lista.append({
            "id": f"news:{ticker}:{_clave(titulo)}", "ticker": ticker,
            "titulo": titulo, "resumen": "", "link": "", "fuente": "Yahoo Finance",
            "fecha": fecha, "sentimiento": etiqueta, "score": score, "urgente": False,
        })
    return guardar_noticias(c, lista)


def importar_raw(c, directorio: str) -> dict:
    resumen_import = {}
    for nombre in sorted(os.listdir(directorio)):
        ruta = os.path.join(directorio, nombre)
        if nombre.endswith("__rss_g.txt"):
            ticker = nombre.split("__")[0].strip()
            resumen_import[nombre] = importar_rss_google(c, ticker, ruta)
        elif nombre.endswith("__rss.txt"):
            ticker = nombre.split("__")[0].strip()
            resumen_import[nombre] = importar_rss_yahoo(c, ticker, ruta)
        elif nombre.endswith("__1d.csv") or nombre.endswith("__1mo.csv"):
            ticker = nombre.split("__")[0].strip()
            resumen_import[nombre] = importar_csv(c, ticker, ruta)
        elif nombre.endswith(".json"):
            ticker = nombre.split("__")[0].strip()
            resumen_import[nombre] = importar_yahoo_json(c, ticker, ruta)
    return resumen_import


# ------------------------------------------------------------- PERSISTENCIA LIVE --

def _periodo_para(ticker: str, c) -> str:
    """Rango a pedir según el hueco desde la última fecha guardada."""
    ultimo = ultimo_dia(c, ticker)
    if not ultimo:
        return f"{max(AÑOS_BOOTSTRAP, 2)}y"
    hueco = (datetime.now(timezone.utc) - datetime.fromisoformat(ultimo).replace(tzinfo=timezone.utc)).days
    if hueco <= 40:
        return "1mo"
    if hueco <= 130:
        return "3mo"
    if hueco <= 280:
        return "6mo"
    return "1y"


def persistir_hoy(force: bool = False) -> str:
    """Persiste precios, noticias, filings y earnings en la base local.

    Devuelve un mensaje de estado. Si la última persistencia es más reciente
    que PERSISTIR_CADA_HORAS y no se pide fuerza, no hace nada.
    """
    c = conn()
    try:
        if not force:
            ult = meta_get(c, "ultimo_persist")
            if ult:
                try:
                    t0 = datetime.fromisoformat(ult)
                    if (datetime.now(timezone.utc) - t0) < timedelta(hours=PERSISTIR_CADA_HORAS):
                        return f"persistencia reciente ({ult[:16]}), se omite"
                except ValueError:
                    pass

        import mercado  # import perezoso: mercado necesita yfinance

        # 1) Precios incrementales
        n_precios = 0
        for t in list(TICKERS) + list(INDICES_CONTEXTO):
            try:
                df = mercado._historico(t, periodo=_periodo_para(t, c))
                if not df.empty:
                    n_precios += guardar_precios(c, t, df)
            except Exception as e:
                log.warning("persistir precios %s: %s", t, e)

        # 2) Noticias
        n_news = 0
        try:
            import noticias as mod_not
            for t, lista in mod_not.noticias_portafolio(TICKERS).items():
                n_news += guardar_noticias(c, lista)
        except Exception as e:
            log.warning("persistir noticias: %s", e)

        # 3) Filings SEC
        n_fil = 0
        try:
            import sec as mod_sec
            for t, lista in mod_sec.presentaciones_portafolio(TICKERS, dias=30).items():
                n_fil += guardar_filings(c, lista)
        except Exception as e:
            log.warning("persistir filings: %s", e)

        # 4) Earnings (histórico corto)
        n_earn = 0
        try:
            import yfinance as yf
            for t in TICKERS:
                try:
                    df = yf.Ticker(t).get_earnings_dates(limit=12)
                    if df is not None and not df.empty:
                        cols = {str(k).lower(): k for k in df.columns}
                        out = pd.DataFrame(index=df.index)
                        out["actual"] = df[[k for k, v in cols.items() if "actual" in v][:1]].values \
                            if any("actual" in v for v in cols) else None
                        out["estimate"] = df[[k for k, v in cols.items() if "estimat" in v][:1]].values \
                            if any("estimat" in v for v in cols) else None
                        out["surprise"] = df[[k for k, v in cols.items() if "surpris" in v][:1]].values \
                            if any("surpris" in v for v in cols) else None
                        n_earn += guardar_earnings(c, t, out)
                except Exception:
                    pass
        except Exception as e:
            log.debug("persistir earnings: %s", e)

        meta_set(c, "ultimo_persist", datetime.now(timezone.utc).isoformat())
        return (f"persistido: {n_precios} precios, {n_news} noticias, "
                f"{n_fil} filings, {n_earn} earnings")
    finally:
        c.close()


# ---------------------------------------------------------- DEMO / VALIDACIÓN --

# Estructura «verdad» conocida del dataset demo (factor mercado m, factor tech f):
#   r_i(t) = beta_m_i * m_t + beta_f_i * f_t + eps_t
#   vix_ret = -0.55 * m_t + ruido   (el miedo cae cuando el mercado sube)
# Efectos inyectados: titulares muy bajistas -> -0.4% al día siguiente;
# 8-K -> +0.30% en 5 sesiones; beat de earnings -> +0.08%/día por cada % de sorpresa.
DEMO_PARAMETROS = {
    "AAPL": (0.85, 0.30, 0.010, 250.0, 55_000_000),
    "MSFT": (0.80, 0.45, 0.010, 420.0, 22_000_000),
    "NVDA": (1.35, 0.95, 0.018, 130.0, 260_000_000),
    "AMZN": (0.95, 0.35, 0.011, 180.0, 45_000_000),
    "GOOGL": (0.90, 0.40, 0.010, 170.0, 28_000_000),
    "TSLA": (1.30, 0.55, 0.022, 260.0, 110_000_000),
    "META": (1.00, 0.65, 0.011, 500.0, 18_000_000),
}
DEMO_VAR_M, DEMO_VAR_F = 0.011 ** 2, 0.013 ** 2


def generar_demo(dias: int = 504, semilla: int = 42) -> dict:
    """Rellena la base con un dataset SINTÉTICO pero realista, con estructura de
    correlación CONOCIDA, para validar que el análisis extrae lo que hay dentro.

    Los datos son de prueba (precios, noticias, filings y earnings generados).
    Para datos reales en tu entorno:  python agente.py datos --bootstrap
    """
    import numpy as np

    rng = np.random.default_rng(semilla)
    c = conn()
    try:
        c.execute("DELETE FROM meta WHERE clave='demo'")
        fechas = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=dias)
        n = len(fechas)

        m = rng.normal(0.0003, np.sqrt(DEMO_VAR_M), n)
        f = rng.normal(0.0004, np.sqrt(DEMO_VAR_F), n)

        # Régimen reciente: los activos se acoplan más al mercado en las últimas 3 semanas
        boost = np.zeros(n)
        boost[-21:] = 0.30

        cierres, vols = {}, {}
        for t, (bm, bf, sig_eps, p0, vol_base) in DEMO_PARAMETROS.items():
            eps = rng.normal(0.0, sig_eps, n)
            r = (bm + boost) * m + bf * f + eps
            neto = _demo_noticias(c, rng, t, fechas, n)
            for i in range(n - 1):  # día de sentimiento neto <= -0.25 -> -0.4% al día siguiente
                if neto[i] <= -0.25:
                    r[i + 1] -= 0.004
            cierres[t] = p0 * np.exp(np.cumsum(r))
            vols[t] = vol_base * np.exp(rng.normal(0, 0.6, n)) * (1 + 12 * np.abs(r))

        p_gspc = 5000 * np.exp(np.cumsum(m))
        p_ixic = 15000 * np.exp(np.cumsum(1.15 * m + 0.4 * f + rng.normal(0, 0.008, n)))
        vix_ret = -1.0 * m + rng.normal(0, 0.02, n)
        vix = np.clip(18 * np.exp(np.cumsum(vix_ret) - np.cumsum(vix_ret).mean()), 12, 80)
        tnx = 4.0 + np.cumsum(np.clip(rng.normal(0, 0.018, n), -0.05, 0.05))

        # Efectos inyectados en el precio (la reacción ocurre DESPUÉS del evento,
        # como en la vida real: 8-K y earnings se publican después del cierre).
        # Las fechas de eventos se dispersan por ticker para que el event study
        # no mida el movimiento de mercado de 3 fechas comunes.
        tickers_list = list(DEMO_PARAMETROS)
        eventos_8k = {t: [j for j in range(10, n - 6, 9)] for t in tickers_list}
        for t, idxs in eventos_8k.items():
            for i in idxs:  # 8-K -> +0.30% acumulado en las 5 sesiones siguientes
                cierres[t][i + 1:i + 6] *= (1 + 0.003 / 5)
        eventos_earn = []
        for ti, t in enumerate(tickers_list):
            for q in range(4):
                i = int(n * (0.1 + 0.25 * q)) + ti * 6
                if i + 6 < n:
                    surprise = float(rng.normal(0.5, 3.0))
                    cierres[t][i + 1:i + 6] *= (1 + 0.010 * surprise / 5)
                    eventos_earn.append((t, i, surprise))

        # Solo AHORA se materializan los DataFrames (ya con todos los efectos)
        n_precios = 0
        for t in DEMO_PARAMETROS:
            n_precios += guardar_precios(c, t, _demo_df(fechas, cierres[t], vols[t]))
        n_precios += guardar_precios(c, "^GSPC", _demo_df(fechas, p_gspc,
                                                          rng.normal(3e9, 3e8, n)))
        n_precios += guardar_precios(c, "^IXIC", _demo_df(fechas, p_ixic,
                                                          rng.normal(3e9, 3e8, n)))
        n_precios += guardar_precios(c, "^VIX", _demo_df(fechas, vix, np.zeros(n)))
        n_precios += guardar_precios(c, "^TNX", _demo_df(fechas, tnx, np.zeros(n)))

        n_fil = 0
        for ti, t in enumerate(tickers_list):
            idxs = eventos_8k[t]
            lista = [{"id": f"sec:{t}:demo{i:04d}", "ticker": t, "form": "8-K",
                      "descripcion": "⚡ Hecho relevante (8-K)",
                      "fecha": fechas[i].date(), "link": "https://www.sec.gov/",
                      "urgente": True} for i in idxs]
            for frac in (0.25, 0.5, 0.75):  # 10-Q trimestrales (efecto ~0), fechas dispersas
                i = int(n * frac) + ti * 6
                if i + 6 < n:
                    lista.append({"id": f"sec:{t}:demoq{i:04d}", "ticker": t, "form": "10-Q",
                                  "descripcion": "📗 Informe trimestral (10-Q)",
                                  "fecha": fechas[i].date(), "link": "https://www.sec.gov/",
                                  "urgente": True})
            i_k = n // 2 + ti * 6  # 10-K anual (efecto ~0)
            if i_k + 6 < n:
                lista.append({"id": f"sec:{t}:demok{i:04d}", "ticker": t, "form": "10-K",
                              "descripcion": "📕 Informe anual (10-K)",
                              "fecha": fechas[i_k].date(), "link": "https://www.sec.gov/",
                              "urgente": True})
            n_fil += guardar_filings(c, lista)

        n_earn = 0
        for t, i, surprise in eventos_earn:
            n_earn += guardar_earnings(c, t, pd.DataFrame(
                {"actual": [1.20 + surprise / 100], "estimate": [1.20],
                 "surprise": [surprise]}, index=pd.DatetimeIndex([fechas[i]])))

        meta_set(c, "demo", f"semilla={semilla},dias={n},generado={datetime.now(timezone.utc).isoformat()}")
        meta_set(c, "ultimo_persist", datetime.now(timezone.utc).isoformat())
        return {"precios": n_precios, "noticias": c.execute("SELECT COUNT(*) FROM noticias").fetchone()[0],
                "filings": n_fil, "earnings": n_earn}
    finally:
        c.close()


def _demo_df(fechas, precios, volumen) -> pd.DataFrame:
    df = pd.DataFrame(index=fechas)
    df["close"] = precios
    df["adj_close"] = precios
    df["open"] = precios * (1 + 0.002)
    df["high"] = precios * 1.01
    df["low"] = precios * 0.99
    df["volume"] = volumen
    return df


def _demo_guardar(c, fechas, ticker, precios, volumen):
    guardar_precios(c, ticker, _demo_df(fechas, precios, volumen))


def _demo_noticias(c, rng, ticker, fechas, n) -> "np.ndarray":
    """Genera titulares (con palabras del léxico de sentimiento) y devuelve el
    score neto por día (media de scores firmados: +0.8 alcista, -0.8 bajista,
    0 neutro; 0 los días sin titulares) — el mismo agregado que mide el análisis."""
    import numpy as np
    positivos = ["{t} beats estimates and surges on strong results",
                 "{t} upgraded to buy on bullish outlook",
                 "{t} soars as record demand lifts the stock",
                 "{t} raises guidance; stock rally continues",
                 "{t} wins major contract, shares gain"]
    negativos = ["{t} plunges after guidance cut and weak outlook",
                 "{t} faces lawsuit and probe; shares tumble",
                 "{t} downgraded on bearish forecast, stock falls",
                 "{t} misses estimates; shares sink on weak profit",
                 "{t} warns on demand; shares drop sharply"]
    neutros = ["{t} to report results next week; analysts watch closely",
               "{t} unveils new product line at its annual event",
               "{t} announces leadership changes on the operations side",
               "Analysts split on {t} ahead of the earnings release"]
    from noticias import _clave
    firma = {"positive": 0.8, "negative": -0.8, "neutral": 0.0}
    neto = np.zeros(n)
    por_dia = np.zeros(n)
    lista = []
    for i, f in enumerate(fechas):
        if rng.random() > 0.35:
            continue
        for j in range(int(rng.integers(1, 4))):
            u = rng.random()
            if u < 0.38:
                plantilla, sen = rng.choice(positivos), "positive"
            elif u < 0.62:
                plantilla, sen = rng.choice(negativos), "negative"
            else:
                plantilla, sen = rng.choice(neutros), "neutral"
            titulo = plantilla.format(t=ticker)
            lista.append({
                "id": f"news:{ticker}:{_clave(titulo)}{i:04d}{j}",
                "ticker": ticker, "titulo": titulo, "resumen": "",
                "link": f"https://finance.yahoo.com/quote/{ticker}",
                "fuente": "Demo",
                "fecha": f.to_pydatetime().replace(hour=16, minute=30,
                                                   tzinfo=timezone.utc),
                "sentimiento": sen, "score": 0.8 if sen != "neutral" else 0.5,
                "urgente": False})
            neto[i] += firma[sen]
            por_dia[i] += 1
    con_noticias = por_dia > 0
    neto[con_noticias] /= por_dia[con_noticias]
    guardar_noticias(c, lista)
    return neto


# ------------------------------------------------------------------- BOOTSTRAP --

def bootstrap(con_noticias: bool = True, con_sec: bool = True,
              con_earnings: bool = True) -> dict:
    """Rellena la base con años de histórico. Para el primer uso."""
    import mercado

    c = conn()
    out = {"precios": 0, "noticias": 0, "filings": 0, "earnings": 0}
    try:
        # Precios: años completos de diarios
        for t in list(TICKERS) + list(INDICES_CONTEXTO):
            log.info("bootstrap precios %s (%sy)...", t, AÑOS_BOOTSTRAP)
            df = mercado._historico(t, periodo=f"{AÑOS_BOOTSTRAP}y")
            if df.empty:
                log.warning("sin datos para %s", t)
                continue
            out["precios"] += guardar_precios(c, t, df)

        if con_noticias:
            # Noticias históricas por ventanas de Google News (cuando cuando)
            from noticias import _clave
            import feedparser
            from sentimiento import analizar
            for t in TICKERS:
                for when in ("1m", "3m", "6m", "1y"):
                    try:
                        url = (f"https://news.google.com/rss/search?q=%22{t}%22+stock+"
                               f"when:{when}&hl=en-US&gl=US&ceid=US:en")
                        feed = feedparser.parse(url)
                        lista = []
                        for e in feed.entries[:100]:
                            titulo = (e.get("title") or "").strip()
                            if not titulo:
                                continue
                            fecha = None
                            for campo in ("published_parsed", "updated_parsed"):
                                v = e.get(campo)
                                if v:
                                    fecha = datetime.fromtimestamp(v[0], tz=timezone.utc)
                                    break
                            etiqueta, score = analizar(titulo)
                            lista.append({
                                "id": f"news:{t}:{_clave(titulo)}", "ticker": t,
                                "titulo": titulo, "resumen": "",
                                "link": e.get("link", ""),
                                "fuente": (e.get("source") or {}).get("title", "Google News"),
                                "fecha": fecha, "sentimiento": etiqueta, "score": score,
                                "urgente": False,
                            })
                        out["noticias"] += guardar_noticias(c, lista)
                        import time
                        time.sleep(1.5)
                    except Exception as e:
                        log.warning("bootstrap noticias %s %s: %s", t, when, e)

        if con_sec:
            # Historial completo de filings (los últimos ~1000)
            import sec as mod_sec
            import requests
            for t in TICKERS:
                cik = mod_sec.cik_de(t)
                if not cik:
                    continue
                try:
                    r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                                     headers=mod_sec._HEADERS, timeout=30)
                    r.raise_for_status()
                    rec = r.json().get("filings", {}).get("recent", {})
                    formas = rec.get("form", [])
                    fechas = rec.get("filingDate", [])
                    accesos = rec.get("accessionNumber", [])
                    docs = rec.get("primaryDocument", [])
                    lista = []
                    for i, form in enumerate(formas):
                        if form not in mod_sec.FORMULARIOS_SEC:
                            continue
                        try:
                            f = datetime.strptime(fechas[i], "%Y-%m-%d").date()
                        except Exception:
                            continue
                        acc = accesos[i].replace("-", "")
                        doc = docs[i] if i < len(docs) else ""
                        enlace = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}"
                                  if doc else f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={cik}")
                        lista.append({
                            "id": f"sec:{t}:{accesos[i]}", "ticker": t, "form": form,
                            "descripcion": mod_sec.DESCRIPCION.get(form, form),
                            "fecha": f,
                            "link": enlace,
                            "urgente": form in mod_sec.FORMULARIOS_SEC_URGENTES,
                        })
                    out["filings"] += guardar_filings(c, lista)
                except Exception as e:
                    log.warning("bootstrap sec %s: %s", t, e)

        if con_earnings:
            import yfinance as yf
            for t in TICKERS:
                try:
                    df = yf.Ticker(t).get_earnings_dates(limit=24)
                    if df is not None and not df.empty:
                        cols = {str(k).lower(): k for k in df.columns}
                        out2 = pd.DataFrame(index=df.index)
                        out2["actual"] = (df[[k for k, v in cols.items() if "actual" in v][:1]].values
                                          if any("actual" in v for v in cols) else None)
                        out2["estimate"] = (df[[k for k, v in cols.items() if "estimat" in v][:1]].values
                                            if any("estimat" in v for v in cols) else None)
                        out2["surprise"] = (df[[k for k, v in cols.items() if "surpris" in v][:1]].values
                                            if any("surpris" in v for v in cols) else None)
                        out["earnings"] += guardar_earnings(c, t, out2)
                except Exception as e:
                    log.debug("bootstrap earnings %s: %s", t, e)

        meta_set(c, "ultimo_persist", datetime.now(timezone.utc).isoformat())
        return out
    finally:
        c.close()
