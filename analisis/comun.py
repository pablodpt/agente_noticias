"""Utilidades compartidas por los análisis sobre la base DuckDB del S&P 500.

Esquema que se espera en la base (el que produce tu pipeline):

    prices(symbol, date, open, high, low, close, adj_close, volume)
    tickers(symbol, company_name, sector, industry, date_added, headquarters, active, last_updated)
    fundamentals(symbol, snapshot_date, market_cap, pe_ratio, forward_pe, ..., target_mean_price)
    dividends(symbol, date, amount) · splits(symbol, date, ratio) · update_log(...)

Todo se lee en modo solo lectura: los scripts nunca escriben en la base.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ_ANALISIS = Path(__file__).resolve().parent
RAIZ_REPO = RAIZ_ANALISIS.parent
DIR_SALIDA = RAIZ_ANALISIS / "salida"

def _leer_env(ruta: Path, clave: str) -> str | None:
    """Lee una clave de un fichero .env sin depender de python-dotenv.

    Soporta 'CLAVE=valor', 'CLAVE = valor', comillas y comentarios con '#'.
    Devuelve None si el fichero no existe o la clave no está.
    """
    try:
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            if k.strip() != clave:
                continue
            v = v.strip()
            if v[:1] in ("'", '"') and v[-1:] == v[:1] and len(v) >= 2:
                v = v[1:-1]                       # entre comillas: se respeta tal cual
            elif " #" in v:
                v = v.split(" #", 1)[0].rstrip()  # comentario al final de la línea
            return v or None
    except (OSError, UnicodeDecodeError):
        pass
    return None

# Dónde buscar la base si no se indica --db ni SP500_DB, en este orden.
# El primero es tu ruta real: C:\Users\pablo\Documents\sp500_db\db\sp500.duckdb
# (Path.home() en Windows es C:\Users\<usuario>, así que también vale en otro PC).
CANDIDATOS_DB = [
    Path.home() / "Documents" / "sp500_db" / "db" / "sp500.duckdb",
    Path.home() / "sp500_db" / "db" / "sp500.duckdb",
    RAIZ_REPO / "datos" / "sp500.duckdb",
]


def localizar_db(explicita: str | None) -> str | None:
    """Ruta a la base: --db > variable SP500_DB > SP500_DB en el .env del repo > candidatas."""
    if explicita:
        return explicita
    env = os.getenv("SP500_DB") or _leer_env(RAIZ_REPO / ".env", "SP500_DB")
    if env:
        return os.path.expanduser(env)
    for c in CANDIDATOS_DB:
        if c.exists():
            return str(c)
    return None


# ------------------------------------------------------------------ consola --

def forzar_utf8() -> None:
    """Evita UnicodeEncodeError en consolas Windows (cp1252)."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def titulo(texto: str) -> None:
    print("\n" + "=" * 78)
    print(texto)
    print("=" * 78)


def tabla(df: pd.DataFrame, filas: int = 20, decimales: int = 2) -> None:
    with pd.option_context("display.width", 160, "display.max_columns", 30,
                           "display.float_format", lambda x: f"{x:,.{decimales}f}"):
        print(df.head(filas).to_string())


# --------------------------------------------------------------- argumentos --

def parser_base(descripcion: str, nombre_salida: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=descripcion,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--db", default=None,
                    help="Ruta al fichero .duckdb. Si se omite: variable SP500_DB o, en su defecto, "
                         "~/Documents/sp500_db/db/sp500.duckdb")
    ap.add_argument("--salida", default=str(DIR_SALIDA / nombre_salida),
                    help="Carpeta donde se guardan CSV, gráficos e informe")
    ap.add_argument("--desde", default=None, help="Primera fecha a cargar (YYYY-MM-DD)")
    ap.add_argument("--hasta", default=None,
                    help="Última fecha a cargar (YYYY-MM-DD). Sirve para analizar 'como si' fuera ese día")
    ap.add_argument("--sin-filtro-alta", action="store_true",
                    help="No excluir a cada valor antes de su fecha de alta en el índice "
                         "(tickers.date_added). Por defecto se excluye para mitigar el sesgo de supervivencia")
    return ap


def preparar_salida(ruta: str) -> Path:
    p = Path(ruta)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ----------------------------------------------------------------- DuckDB --

def conectar(ruta: str | None):
    try:
        import duckdb
    except ImportError:
        sys.exit("Falta duckdb: pip install -r analisis/requirements.txt")
    ruta = localizar_db(ruta)
    if not ruta:
        buscadas = "\n  ".join(str(c) for c in CANDIDATOS_DB)
        sys.exit("No encuentro la base de datos. Indícala con --db (p. ej. "
                 r'--db "C:\Users\pablo\Documents\sp500_db\db\sp500.duckdb")'
                 " o define SP500_DB en el entorno o en el .env.\n"
                 f"Rutas probadas:\n  {buscadas}")
    if not os.path.exists(ruta):
        sys.exit(f"No existe el fichero {ruta}")
    try:
        con = duckdb.connect(ruta, read_only=True)
    except Exception as e:  # p. ej. otro proceso la tiene abierta en escritura
        sys.exit(f"No se pudo abrir {ruta} en modo lectura: {e}\n"
                 "Si tu pipeline de actualización la tiene abierta, ciérralo o trabaja sobre una copia.")
    print(f"Base de datos: {ruta}")
    return con


def cargar_tickers(con) -> pd.DataFrame:
    df = con.execute("""
        SELECT symbol, company_name, sector, industry, date_added, active
        FROM tickers
    """).df()
    df["symbol"] = df["symbol"].astype(str)
    df["sector"] = df["sector"].fillna("Desconocido").astype(str)
    df["industry"] = df["industry"].fillna("Desconocida").astype(str)
    df["date_added"] = pd.to_datetime(df["date_added"], errors="coerce")
    return df.drop_duplicates("symbol").reset_index(drop=True)


def cargar_precios(con, desde: str | None = None, hasta: str | None = None,
                   relleno_max: int = 5) -> dict[str, pd.DataFrame]:
    """Devuelve paneles anchos (fecha × símbolo) de adj_close, close y volume.

    - adj_close nulo se sustituye por close (y ambos deben ser > 0).
    - Huecos aislados (festivos locales, fallos de descarga) se rellenan hacia
      delante hasta `relleno_max` sesiones para no romper los cálculos móviles.
    """
    cond, params = [], []
    if desde:
        cond.append("date >= ?")
        params.append(desde)
    if hasta:
        cond.append("date <= ?")
        params.append(hasta)
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    largo = con.execute(f"""
        SELECT symbol, date, close, adj_close, volume
        FROM prices {where}
        ORDER BY date, symbol
    """, params).df()
    if largo.empty:
        sys.exit("La consulta a prices no devolvió filas (revisa --desde/--hasta).")

    largo["symbol"] = largo["symbol"].astype(str)
    largo["date"] = pd.to_datetime(largo["date"])
    largo = largo.drop_duplicates(["symbol", "date"], keep="last")
    largo["adj_close"] = largo["adj_close"].fillna(largo["close"])
    largo.loc[largo["adj_close"] <= 0, "adj_close"] = np.nan
    largo.loc[largo["close"] <= 0, "close"] = np.nan

    paneles = {}
    for col in ("adj_close", "close", "volume"):
        panel = largo.pivot(index="date", columns="symbol", values=col).sort_index()
        panel.index.name = "date"
        paneles[col] = panel.astype(float)
    if relleno_max:
        paneles["adj_close"] = paneles["adj_close"].ffill(limit=relleno_max)
        paneles["close"] = paneles["close"].ffill(limit=relleno_max)
    return paneles


def cargar_fundamentales(con) -> pd.DataFrame:
    """Último snapshot disponible de cada símbolo (la tabla es una foto, no un histórico)."""
    df = con.execute("""
        SELECT * FROM fundamentals
        QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY snapshot_date DESC) = 1
    """).df()
    df["symbol"] = df["symbol"].astype(str)
    return df.set_index("symbol")


def resumen_base(con) -> dict:
    """Pequeña ficha de la base para imprimir al arrancar."""
    fila = con.execute("""
        SELECT count(DISTINCT symbol), min(date), max(date), count(*) FROM prices
    """).fetchone()
    snaps = con.execute("SELECT count(DISTINCT snapshot_date), max(snapshot_date) FROM fundamentals").fetchone()
    return {"tickers": fila[0], "primera_fecha": fila[1], "ultima_fecha": fila[2],
            "filas_precios": fila[3], "snapshots_fundamentales": snaps[0],
            "ultimo_snapshot": snaps[1]}


# ----------------------------------------------------------- universo/alta --

def mascara_alta(indice: pd.DatetimeIndex, columnas: pd.Index, tickers: pd.DataFrame) -> pd.DataFrame:
    """True donde el valor ya pertenecía al índice (fecha >= date_added).

    Los 503 símbolos son los miembros de HOY: con esta máscara cada valor solo
    entra en el universo desde su alta, lo que elimina la parte del sesgo de
    supervivencia debida a 'elegir hoy a los que subieron para entrar'. La otra
    parte (los que salieron del índice no están en la base) no tiene arreglo
    con estos datos, así que los resultados serán algo optimistas.
    """
    m = pd.DataFrame(True, index=indice, columns=columnas)
    altas = tickers.set_index("symbol")["date_added"].dropna()
    for sym, alta in altas.items():
        if sym in m.columns and alta > indice[0]:
            m.loc[indice < alta, sym] = False
    return m


def aplicar_mascara(paneles: dict[str, pd.DataFrame], tickers: pd.DataFrame,
                    activar: bool) -> dict[str, pd.DataFrame]:
    if not activar:
        return paneles
    base = paneles["adj_close"]
    m = mascara_alta(base.index, base.columns, tickers)
    return {k: v.where(m.reindex(index=v.index, columns=v.columns, fill_value=True))
            for k, v in paneles.items()}


# -------------------------------------------------------------- calendario --

def fines_de_mes(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(idx, index=idx)
    return pd.DatetimeIndex(s.groupby([idx.year, idx.month]).last().to_numpy())


def fines_de_semana(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(idx, index=idx)
    return pd.DatetimeIndex(s.groupby(idx.to_period("W")).last().to_numpy())


# ------------------------------------------------------------- estadística --

def rank_transversal(df: pd.DataFrame) -> pd.DataFrame:
    """Rango percentil (0-1) de cada fila, ignorando NaN."""
    return df.rank(axis=1, pct=True)


def z_transversal(df: pd.DataFrame, limite: float = 3.0) -> pd.DataFrame:
    """Z-score por fila, recortado a ±limite para que los extremos no dominen."""
    z = df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1).replace(0, np.nan), axis=0)
    return z.clip(-limite, limite)


def neutralizar_sector(df: pd.DataFrame, sectores: pd.Series) -> pd.DataFrame:
    """Resta la media del sector en cada fecha (factor 'dentro del sector')."""
    sect = sectores.reindex(df.columns).fillna("Desconocido")
    medias = df.T.groupby(sect.to_numpy()).transform("mean").T
    return df - medias


def ic_transversal(factor: pd.DataFrame, objetivo: pd.DataFrame, minimo: int = 30) -> pd.Series:
    """Correlación de Spearman entre factor y objetivo en cada fila (fecha)."""
    f, o = factor.align(objetivo, join="inner")
    valido = f.notna() & o.notna()
    f, o = f.where(valido), o.where(valido)
    rf, ro = f.rank(axis=1), o.rank(axis=1)
    rf = rf.sub(rf.mean(axis=1), axis=0)
    ro = ro.sub(ro.mean(axis=1), axis=0)
    num = (rf * ro).sum(axis=1)
    den = np.sqrt((rf ** 2).sum(axis=1) * (ro ** 2).sum(axis=1))
    ic = num / den.replace(0, np.nan)
    return ic.where(valido.sum(axis=1) >= minimo).dropna()


def t_newey_west(x: pd.Series, retardos: int = 0) -> float:
    """t-stat de la media con errores robustos a autocorrelación (Newey-West)."""
    v = pd.Series(x).dropna().to_numpy(dtype=float)
    n = len(v)
    if n < 3:
        return float("nan")
    e = v - v.mean()
    var = float(e @ e) / n
    for k in range(1, min(retardos, n - 1) + 1):
        w = 1 - k / (retardos + 1)
        var += 2 * w * float(e[k:] @ e[:-k]) / n
    return float(v.mean() / np.sqrt(var / n)) if var > 0 else float("nan")


def metricas(r: pd.Series, periodos_anio: float) -> dict:
    """CAGR, volatilidad, Sharpe (rf=0), drawdown máximo y % de periodos positivos."""
    r = pd.Series(r).dropna()
    if len(r) < 2:
        return {}
    acum = (1 + r).cumprod()
    n = len(r)
    sd = r.std()
    return {
        "CAGR_%": 100 * (acum.iloc[-1] ** (periodos_anio / n) - 1),
        "Vol_%": 100 * sd * np.sqrt(periodos_anio),
        "Sharpe": (r.mean() / sd * np.sqrt(periodos_anio)) if sd > 0 else np.nan,
        "MaxDD_%": 100 * (acum / acum.cummax() - 1).min(),
        "%positivos": 100 * (r > 0).mean(),
        "Periodos": n,
    }


def a_largo(df: pd.DataFrame, nombre: str) -> pd.Series:
    """Panel ancho → serie larga con índice (date, symbol), sin NaN."""
    s = df.stack()
    s = s.dropna()
    s.name = nombre
    s.index = s.index.set_names(["date", "symbol"])
    return s


# ------------------------------------------------------------------ cartera --

def cartera_por_defecto() -> tuple[list[str], dict[str, float]]:
    """Lee PORTAFOLIO de config.py (el agente de Telegram) sin importarlo.

    Se analiza el fichero con `ast` para no depender de python-dotenv ni
    ejecutar nada; si falla, devuelve una cartera vacía.
    """
    import ast
    ruta = RAIZ_REPO / "config.py"
    try:
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        for nodo in arbol.body:
            if isinstance(nodo, ast.Assign) and any(
                    getattr(t, "id", None) == "PORTAFOLIO" for t in nodo.targets):
                lista = ast.literal_eval(nodo.value)
                tickers = [p["ticker"] for p in lista]
                pesos = {p["ticker"]: float(p.get("peso", 1)) for p in lista}
                return tickers, pesos
    except Exception:
        pass
    return [], {}


def parsear_cartera(texto: str | None) -> tuple[list[str], dict[str, float]]:
    """'AAPL:20,MSFT:20,NVDA' → (['AAPL','MSFT','NVDA'], {'AAPL':20,'MSFT':20,'NVDA':1})."""
    if not texto:
        return cartera_por_defecto()
    tickers, pesos = [], {}
    for trozo in texto.split(","):
        trozo = trozo.strip().upper()
        if not trozo:
            continue
        if ":" in trozo:
            t, p = trozo.split(":", 1)
            tickers.append(t.strip())
            try:
                pesos[t.strip()] = float(p)
            except ValueError:
                pesos[t.strip()] = 1.0
        else:
            tickers.append(trozo)
            pesos[trozo] = 1.0
    return tickers, pesos
