"""Crea una base DuckDB SINTÉTICA con el mismo esquema que la tuya.

Sirve para probar los scripts de `analisis/` sin tener a mano la base real
(o para que otra persona pueda ejecutar el código). Los precios se generan con
un modelo de factores (mercado + sector + ruido idiosincrático) al que se le
planta un poco de momentum y de efecto tamaño; los fundamentales son números
plausibles pero inventados. Los resultados sobre esta base NO significan nada.

    python analisis/crear_db_prueba.py --salida /tmp/sp500_prueba.duckdb --tickers 150
"""
from __future__ import annotations

import argparse
import os
import string
from datetime import date, datetime

import numpy as np
import pandas as pd

SECTORES = {
    "Information Technology": ["Semiconductors", "Software", "IT Services", "Hardware"],
    "Health Care": ["Pharmaceuticals", "Biotechnology", "Health Care Equipment", "Managed Care"],
    "Financials": ["Banks", "Insurance", "Capital Markets", "Consumer Finance"],
    "Consumer Discretionary": ["Retail", "Automobiles", "Hotels & Leisure", "Apparel"],
    "Communication Services": ["Interactive Media", "Telecom", "Entertainment"],
    "Industrials": ["Aerospace & Defense", "Machinery", "Railroads", "Building Products"],
    "Consumer Staples": ["Beverages", "Food Products", "Household Products"],
    "Energy": ["Oil & Gas E&P", "Oil & Gas Equipment", "Refining"],
    "Utilities": ["Electric Utilities", "Multi-Utilities"],
    "Real Estate": ["REITs", "Real Estate Services"],
    "Materials": ["Specialty Chemicals", "Metals & Mining", "Construction Materials"],
}
PESOS_SECTOR = np.array([0.30, 0.10, 0.13, 0.10, 0.09, 0.08, 0.06, 0.04, 0.03, 0.03, 0.04])


def _simbolos(n: int, rng: np.random.Generator) -> list[str]:
    fijos = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "TSLA", "META", "JPM", "XOM", "JNJ",
             "UNH", "PG", "HD", "KO", "PEP", "AMD", "AVGO", "COST", "LLY", "V"]
    out = list(fijos[:n])
    letras = string.ascii_uppercase
    while len(out) < n:
        s = "".join(rng.choice(list(letras), size=int(rng.integers(2, 5))))
        if s not in out:
            out.append(s)
    return out


def generar(n_tickers: int, inicio: str, fin: str, semilla: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(semilla)
    fechas = pd.bdate_range(inicio, fin)          # días laborables ≈ sesiones
    T, N = len(fechas), n_tickers
    simbolos = _simbolos(N, rng)

    sect_nombres = list(SECTORES)
    sector_idx = rng.choice(len(sect_nombres), size=N, p=PESOS_SECTOR / PESOS_SECTOR.sum())
    # Los 7 primeros (mega-tech) al sector tecnológico/comunicación para que la cartera tenga sentido
    for i, s in enumerate(["Information Technology", "Information Technology", "Information Technology",
                           "Consumer Discretionary", "Communication Services",
                           "Consumer Discretionary", "Communication Services"][:N]):
        sector_idx[i] = sect_nombres.index(s)

    # --- factores ---------------------------------------------------------
    vol_mkt = np.full(T, 0.010)
    # dos episodios de estrés (tipo 2020 y 2022) con volatilidad alta
    for a, b, v in ((int(T * 0.42), int(T * 0.45), 0.035), (int(T * 0.65), int(T * 0.75), 0.018)):
        vol_mkt[a:b] = v
    mkt = rng.normal(0.00035, 1.0, T) * vol_mkt
    mkt[int(T * 0.42):int(T * 0.435)] -= 0.012   # crash
    n_sect = len(sect_nombres)
    sect = rng.normal(0, 0.006, (T, n_sect))

    beta = np.clip(rng.normal(1.0, 0.3, N), 0.3, 2.2)
    vol_idio = np.clip(rng.lognormal(np.log(0.015), 0.35, N), 0.006, 0.05)
    deriva = rng.normal(0.00015, 0.00025, N)      # dispersión de calidad
    corr_sect = np.clip(rng.normal(0.9, 0.3, N), 0.2, 1.6)

    ret = np.empty((T, N))
    eps = rng.standard_t(df=4, size=(T, N)) / np.sqrt(2) * vol_idio
    ret[:] = deriva + beta[None, :] * mkt[:, None] + corr_sect[None, :] * sect[:, sector_idx] + eps
    # momentum plantado: el rango del retorno 12-1 meses empuja el retorno futuro un poco
    for t in range(260, T):
        if t % 21 == 0:
            r12 = np.log1p(ret[t - 252:t - 21]).sum(axis=0)
            rango = (pd.Series(r12).rank(pct=True).to_numpy() - 0.5)
            ret[t:t + 21] += 0.00025 * rango[None, :]
    ret = np.clip(ret, -0.45, 0.6)

    log_px = np.log(rng.uniform(20, 300, N))[None, :] + np.cumsum(ret, axis=0)
    adj = np.exp(log_px)

    # dividendos: ~60% pagan trimestral; splits: unos pocos
    div_rows, split_rows = [], []
    paga = rng.random(N) < 0.6
    yields = np.where(paga, rng.uniform(0.005, 0.035, N), 0.0)
    ratio_acum = np.ones(N)
    factor_div = np.ones((T, N))
    for i in range(N):
        if paga[i]:
            for t in range(63, T, 63):
                importe = adj[t, i] * yields[i] / 4
                div_rows.append((simbolos[i], fechas[t].date(), round(float(importe), 4)))
                factor_div[:t, i] *= (1 - yields[i] / 4)
        if rng.random() < 0.12:
            t = int(rng.integers(int(T * 0.2), int(T * 0.9)))
            r = float(rng.choice([2.0, 3.0, 4.0, 1.5]))
            split_rows.append((simbolos[i], fechas[t].date(), r))
            ratio_acum[i] = r
    # close 'sin ajustar' = adj / factor_div, y multiplicado por el split antes de su fecha
    close = adj / factor_div
    for s, d, r in split_rows:
        i = simbolos.index(s)
        idx = fechas.get_indexer([pd.Timestamp(d)])[0]
        close[:idx, i] *= r
    ruido = rng.uniform(0.0, 0.02, (T, N))
    open_ = close * (1 + rng.normal(0, 0.006, (T, N)))
    high = np.maximum(open_, close) * (1 + ruido)
    low = np.minimum(open_, close) * (1 - ruido)
    vol_base = rng.lognormal(np.log(3e6), 0.9, N)
    volume = (vol_base[None, :] * np.exp(rng.normal(0, 0.5, (T, N)))
              * (1 + 8 * np.abs(ret))).astype(np.int64)

    # --- altas en el índice (algunos entran tarde) ------------------------
    date_added = []
    for i in range(N):
        if rng.random() < 0.35:
            t = int(rng.integers(0, T - 260))
            date_added.append(fechas[t].date())
        else:
            date_added.append(date(int(rng.integers(1957, 2014)), int(rng.integers(1, 13)), 1))

    # --- tablas -----------------------------------------------------------
    largo = []
    for i, s in enumerate(simbolos):
        df = pd.DataFrame({
            "symbol": s, "date": fechas.date, "open": open_[:, i], "high": high[:, i],
            "low": low[:, i], "close": close[:, i], "adj_close": adj[:, i], "volume": volume[:, i],
        })
        # borra unas pocas filas al azar para simular huecos de descarga
        df = df.drop(index=rng.choice(df.index, size=int(rng.integers(0, 4)), replace=False))
        largo.append(df)
    prices = pd.concat(largo, ignore_index=True)

    tickers = pd.DataFrame({
        "symbol": simbolos,
        "company_name": [f"{s} Corp." for s in simbolos],
        "sector": [sect_nombres[k] for k in sector_idx],
        "industry": [rng.choice(SECTORES[sect_nombres[k]]) for k in sector_idx],
        "date_added": date_added,
        "headquarters": "Somewhere, USA",
        "active": True,
        "last_updated": datetime.now(),
    })

    ult = adj[-1]
    shares = rng.lognormal(np.log(8e8), 0.8, N)
    mcap = ult * shares
    eps = ult / np.clip(rng.lognormal(np.log(22), 0.45, N), 5, 120)
    rev = mcap / np.clip(rng.lognormal(np.log(4), 0.7, N), 0.5, 40)
    funds = []
    for snap in (pd.Timestamp(fin) - pd.Timedelta(days=1), pd.Timestamp(fin)):
        j = rng.normal(1, 0.01, N)
        funds.append(pd.DataFrame({
            "symbol": simbolos, "snapshot_date": snap.date(),
            "market_cap": mcap * j, "pe_ratio": np.where(eps > 0, ult / eps, np.nan) * j,
            "forward_pe": ult / (eps * rng.uniform(1.0, 1.25, N)),
            "peg_ratio": rng.uniform(0.5, 4, N), "price_to_book": rng.lognormal(np.log(4), 0.8, N),
            "price_to_sales": mcap / rev, "dividend_yield": yields * 100,
            "payout_ratio": np.where(paga, rng.uniform(0.1, 0.8, N), 0),
            "beta": beta * j, "eps": eps, "forward_eps": eps * rng.uniform(1.0, 1.25, N),
            "profit_margin": rng.normal(0.14, 0.09, N), "operating_margin": rng.normal(0.18, 0.1, N),
            "roe": rng.normal(0.18, 0.15, N), "roa": rng.normal(0.07, 0.05, N),
            "debt_to_equity": np.clip(rng.lognormal(np.log(60), 0.9, N), 0, 600),
            "current_ratio": rng.lognormal(np.log(1.4), 0.4, N), "quick_ratio": rng.lognormal(np.log(1.0), 0.4, N),
            "revenue": rev, "revenue_growth": rng.normal(0.07, 0.12, N),
            "earnings_growth": rng.normal(0.08, 0.3, N), "free_cashflow": rev * rng.normal(0.12, 0.08, N),
            "fifty_two_week_high": adj[-252:].max(axis=0), "fifty_two_week_low": adj[-252:].min(axis=0),
            "fifty_day_average": adj[-50:].mean(axis=0), "two_hundred_day_avg": adj[-200:].mean(axis=0),
            "avg_volume": volume[-90:].mean(axis=0), "shares_outstanding": shares,
            "float_shares": shares * rng.uniform(0.85, 1.0, N),
            "short_ratio": rng.lognormal(np.log(2.5), 0.5, N),
            "recommendation": rng.choice(["strong_buy", "buy", "hold", "sell"], N, p=[0.15, 0.55, 0.27, 0.03]),
            "target_mean_price": ult * rng.uniform(0.9, 1.35, N),
        }))
    fundamentals = pd.concat(funds, ignore_index=True)

    dividends = pd.DataFrame(div_rows, columns=["symbol", "date", "amount"])
    splits = pd.DataFrame(split_rows, columns=["symbol", "date", "ratio"])
    update_log = pd.DataFrame([{
        "run_id": 1, "started_at": datetime.now(), "finished_at": datetime.now(), "status": "OK",
        "details": f"tickers={N}, prices={len(prices)}, funds={len(fundamentals)}, "
                   f"div={len(dividends)}, splits={len(splits)} (SINTÉTICO)",
    }])
    return {"prices": prices, "tickers": tickers, "fundamentals": fundamentals,
            "dividends": dividends, "splits": splits, "update_log": update_log}


def escribir(ruta: str, tablas: dict[str, pd.DataFrame]) -> None:
    import duckdb
    if os.path.exists(ruta):
        os.remove(ruta)
    con = duckdb.connect(ruta)
    try:
        for nombre, df in tablas.items():
            con.register("df_tmp", df)
            con.execute(f"CREATE TABLE {nombre} AS SELECT * FROM df_tmp")
            con.unregister("df_tmp")
        con.execute("ALTER TABLE prices ALTER date TYPE DATE")
        con.execute("ALTER TABLE tickers ALTER date_added TYPE DATE")
        con.execute("ALTER TABLE fundamentals ALTER snapshot_date TYPE DATE")
        con.execute("ALTER TABLE dividends ALTER date TYPE DATE")
        if len(tablas["splits"]):
            con.execute("ALTER TABLE splits ALTER date TYPE DATE")
    finally:
        con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", default="/tmp/sp500_prueba.duckdb")
    ap.add_argument("--tickers", type=int, default=150)
    ap.add_argument("--inicio", default="2015-09-07")
    ap.add_argument("--fin", default="2026-09-05")
    ap.add_argument("--semilla", type=int, default=7)
    args = ap.parse_args()

    tablas = generar(args.tickers, args.inicio, args.fin, args.semilla)
    escribir(args.salida, tablas)
    print(f"Base sintética creada en {args.salida}")
    for k, v in tablas.items():
        print(f"  {k:<13} {len(v):>9,} filas")


if __name__ == "__main__":
    main()
