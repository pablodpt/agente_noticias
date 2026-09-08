"""1) BACKTEST DE FACTORES sobre tu base DuckDB del S&P 500.

Responde a la pregunta: «en estos 10 años, ¿qué características de una acción
han anticipado que lo hiciera mejor que el resto del índice al mes siguiente?»

Para cada factor (momentum 12-1, momentum 6-1, reversión 1 mes, baja volatilidad,
cercanía al máximo de 52 semanas, tendencia sobre la SMA200, baja beta):

  * Cada fin de mes ordena los ~500 valores por el factor y los reparte en 5 quintiles.
  * Mide el retorno del mes siguiente de cada quintil (equiponderado) y el diferencial Q5-Q1.
  * Calcula el IC (correlación de Spearman entre el factor y el retorno futuro) y su t-stat.
  * Desglosa por año para ver si el efecto es estable o cosa de un par de años.

Al final construye un CORTE TRANSVERSAL DE HOY: combina los factores de precio
(ponderados por el IC que han demostrado) con valor, calidad y crecimiento del
snapshot de `fundamentals`, y lista los candidatos mejor situados.

Uso:
    python analisis/factores.py --db /ruta/sp500.duckdb
    python analisis/factores.py --db ... --neutral-sector --coste-bps 10 --top 30

Salida en analisis/salida/factores/: resumen_factores.csv, ic_mensual.csv,
quintiles_mensual.csv, por_anio.csv, candidatos_hoy.csv, gráficos PNG e informe.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import (a_largo, aplicar_mascara, cargar_fundamentales, cargar_precios,  # noqa: E402
                   cargar_tickers, conectar, fines_de_mes, forzar_utf8, ic_transversal,
                   metricas, parser_base, preparar_salida, rank_transversal, resumen_base,
                   t_newey_west, tabla, titulo)

FACTORES_PRECIO = {
    # nombre: (descripción, signo ya aplicado -> valor alto = mejor esperado)
    "momentum_12_1": "Retorno de los últimos 12 meses saltando el último (12-1)",
    "momentum_6_1": "Retorno de los últimos 6 meses saltando el último (6-1)",
    "reversion_1m": "Reversión a corto: -retorno del último mes",
    "baja_volatilidad": "-Volatilidad diaria de 60 sesiones",
    "maximo_52s": "Cercanía al máximo de 52 semanas (precio / máximo - 1)",
    "tendencia_200": "Precio sobre su media de 200 sesiones (precio / SMA200 - 1)",
    "baja_beta": "-Beta de 120 sesiones frente al índice equiponderado",
}
# Factores que entran en el 'combinado' si el usuario no indica otros.
COMBINADO_DEFECTO = ["momentum_12_1", "baja_volatilidad", "maximo_52s", "tendencia_200"]


# --------------------------------------------------------------- factores --

def calcular_factores(adj: pd.DataFrame, close: pd.DataFrame, volumen: pd.DataFrame) -> dict[str, pd.DataFrame]:
    r = adj.pct_change(fill_method=None)
    logp = np.log(adj)
    mercado = r.mean(axis=1)  # proxy del índice: media equiponderada del universo
    cov = r.rolling(120, min_periods=80).cov(mercado)
    var = mercado.rolling(120, min_periods=80).var()
    f = {
        "momentum_12_1": logp.shift(21) - logp.shift(252),
        "momentum_6_1": logp.shift(21) - logp.shift(126),
        "reversion_1m": -(logp - logp.shift(21)),
        "baja_volatilidad": -r.rolling(60, min_periods=40).std(),
        "maximo_52s": adj / adj.rolling(252, min_periods=200).max() - 1,
        "tendencia_200": adj / adj.rolling(200, min_periods=150).mean() - 1,
        "baja_beta": -cov.div(var, axis=0),
    }
    # Liquidez en dólares (solo para filtrar: se exigen >= 5 M$/día de media)
    f["_dolares_60"] = (close * volumen).rolling(60, min_periods=40).mean()
    return f


def ranks_por_fecha(factor: pd.DataFrame, sectores: pd.Series | None) -> pd.DataFrame:
    """Rango percentil por fecha; si hay sectores, dentro de cada sector."""
    if sectores is None:
        return rank_transversal(factor)
    out = pd.DataFrame(np.nan, index=factor.index, columns=factor.columns)
    sect = sectores.reindex(factor.columns).fillna("Desconocido")
    for s in sect.unique():
        cols = sect.index[sect == s]
        if len(cols) >= 5:
            out[cols] = factor[cols].rank(axis=1, pct=True)
    return out


# ---------------------------------------------------------------- backtest --

def backtest_factor(rank: pd.DataFrame, fwd: pd.DataFrame, n_q: int = 5) -> dict:
    """Quintiles equiponderados, diferencial Q5-Q1, IC y rotación de la cesta larga."""
    rk, fw = rank.align(fwd, join="inner")
    q = np.ceil(rk * n_q).clip(1, n_q)
    filas = []
    prev_top: set = set()
    rotacion = []
    for fecha in rk.index:
        qi, fi = q.loc[fecha], fw.loc[fecha]
        ok = qi.notna() & fi.notna()
        if ok.sum() < 30:
            continue
        fila = {"date": fecha}
        for k in range(1, n_q + 1):
            fila[f"Q{k}"] = fi[ok & (qi == k)].mean()
        fila["n"] = int(ok.sum())
        filas.append(fila)
        top = set(qi.index[ok & (qi == n_q)])
        if prev_top:
            rotacion.append(len(top - prev_top) / max(len(top), 1))
        prev_top = top
    qdf = pd.DataFrame(filas).set_index("date")
    if qdf.empty:
        return {}
    qdf["spread"] = qdf[f"Q{n_q}"] - qdf["Q1"]
    qdf["universo"] = qdf[[f"Q{k}" for k in range(1, n_q + 1)]].mean(axis=1)
    qdf["exceso_Q5"] = qdf[f"Q{n_q}"] - qdf["universo"]
    ic = ic_transversal(rk, fw)
    return {"quintiles": qdf, "ic": ic, "rotacion": float(np.mean(rotacion)) if rotacion else np.nan}


def resumir(nombre: str, res: dict, coste_bps: float) -> dict:
    qdf, ic = res["quintiles"], res["ic"]
    # coste: la cesta larga renueva 'rotacion' de sus nombres al mes (ida y vuelta -> ×2)
    coste_mensual = 2 * res["rotacion"] * coste_bps / 1e4 if np.isfinite(res["rotacion"]) else 0
    spread_neto = qdf["spread"] - 2 * coste_mensual  # largo + corto
    m_bruto = metricas(qdf["spread"], 12)
    m_neto = metricas(spread_neto, 12)
    m_q5 = metricas(qdf["exceso_Q5"], 12)
    return {
        "factor": nombre,
        "IC_medio": ic.mean(),
        "IC_tstat_NW": t_newey_west(ic, retardos=1),
        "IC_IR": ic.mean() / ic.std() if ic.std() > 0 else np.nan,
        "IC_%positivo": 100 * (ic > 0).mean(),
        "Q5-Q1_anual_%": m_bruto.get("CAGR_%", np.nan),
        "Q5-Q1_Sharpe": m_bruto.get("Sharpe", np.nan),
        "Q5-Q1_neto_anual_%": m_neto.get("CAGR_%", np.nan),
        "Q5_exceso_anual_%": m_q5.get("CAGR_%", np.nan),
        "MaxDD_spread_%": m_bruto.get("MaxDD_%", np.nan),
        "rotacion_Q5_%": 100 * res["rotacion"],
        "meses": len(qdf),
    }


def por_anio(resultados: dict[str, dict]) -> pd.DataFrame:
    filas = []
    for nombre, res in resultados.items():
        ic, qdf = res["ic"], res["quintiles"]
        g_ic = ic.groupby(ic.index.year).mean()
        g_sp = qdf["spread"].groupby(qdf.index.year).apply(lambda s: 100 * ((1 + s).prod() - 1))
        for anio in g_sp.index:
            filas.append({"factor": nombre, "anio": anio, "IC_medio": g_ic.get(anio, np.nan),
                          "spread_%": g_sp[anio]})
    df = pd.DataFrame(filas)
    return df.pivot(index="anio", columns="factor", values="spread_%") if not df.empty else df


# ------------------------------------------------------------------ hoy --

_RECOMENDACION = {"strong_buy": 1.0, "buy": 0.75, "hold": 0.5, "underperform": 0.25, "sell": 0.0,
                  "strong_sell": 0.0}


def _pct_sector(s: pd.Series, sectores: pd.Series) -> pd.Series:
    """Percentil dentro del sector (o del universo si el sector tiene < 5 valores)."""
    s = s.replace([np.inf, -np.inf], np.nan)
    g = sectores.reindex(s.index).fillna("Desconocido")
    tam = g.map(g.value_counts())
    dentro = s.groupby(g).rank(pct=True)
    global_ = s.rank(pct=True)
    return dentro.where(tam >= 5, global_)


def corte_hoy(factores: dict, ranks_hoy: pd.DataFrame, pesos_precio: pd.Series,
              fund: pd.DataFrame, tickers: pd.DataFrame, ultimo_close: pd.Series,
              top: int) -> pd.DataFrame:
    sectores = tickers.set_index("symbol")["sector"]
    idx = ranks_hoy.index

    # Media ponderada por IC, renormalizando los pesos cuando falta algún factor.
    rk = ranks_hoy[pesos_precio.index]
    pesos_validos = rk.notna().astype(float).mul(pesos_precio, axis=1)
    precio_score = rk.fillna(0).mul(pesos_precio, axis=1).sum(axis=1) \
        / pesos_validos.sum(axis=1).replace(0, np.nan)

    f = fund.reindex(idx)
    with np.errstate(divide="ignore", invalid="ignore"):
        ey = np.where(f["pe_ratio"] > 0, 1 / f["pe_ratio"], np.nan)
        fey = np.where(f["forward_pe"] > 0, 1 / f["forward_pe"], np.nan)
        fcfy = f["free_cashflow"] / f["market_cap"]
        bp = np.where(f["price_to_book"] > 0, 1 / f["price_to_book"], np.nan)
        sp = np.where(f["price_to_sales"] > 0, 1 / f["price_to_sales"], np.nan)
    valor = pd.concat([_pct_sector(pd.Series(v, index=idx, dtype=float), sectores)
                       for v in (ey, fey, fcfy, bp, sp)], axis=1).mean(axis=1)
    calidad = pd.concat([
        _pct_sector(f["roe"].astype(float), sectores),
        _pct_sector(f["profit_margin"].astype(float), sectores),
        _pct_sector(f["operating_margin"].astype(float), sectores),
        _pct_sector(f["roa"].astype(float), sectores),
        _pct_sector(-f["debt_to_equity"].astype(float), sectores),
    ], axis=1).mean(axis=1)
    crecimiento = pd.concat([
        _pct_sector(f["revenue_growth"].astype(float), sectores),
        _pct_sector(f["earnings_growth"].astype(float), sectores),
    ], axis=1).mean(axis=1)
    upside = (f["target_mean_price"] / ultimo_close.reindex(idx) - 1) * 100
    recomendacion = f["recommendation"].astype(str).str.lower().map(_RECOMENDACION)

    out = pd.DataFrame({
        "sector": sectores.reindex(idx),
        "precio_score": precio_score,
        "momentum_12_1": ranks_hoy.get("momentum_12_1"),
        "baja_vol": ranks_hoy.get("baja_volatilidad"),
        "max_52s": ranks_hoy.get("maximo_52s"),
        "tendencia": ranks_hoy.get("tendencia_200"),
        "valor": valor,
        "calidad": calidad,
        "crecimiento": crecimiento,
        "upside_analistas_%": upside,
        "recomendacion": recomendacion,
        "pe": f["pe_ratio"], "fwd_pe": f["forward_pe"], "roe": f["roe"],
        "market_cap_bn": f["market_cap"] / 1e9,
    }, index=idx)
    out["total"] = out[["precio_score", "valor", "calidad"]].mean(axis=1, skipna=False)
    out["aviso"] = ""
    trampa = (out["valor"] >= 0.8) & (out["momentum_12_1"] <= 0.2)
    out.loc[trampa, "aviso"] = "posible trampa de valor (barato pero cayendo)"
    caro_mom = (out["valor"] <= 0.2) & (out["precio_score"] >= 0.8)
    out.loc[caro_mom, "aviso"] = "momentum caro: exige stop disciplinado"
    return out.sort_values("total", ascending=False)


# ---------------------------------------------------------------- gráficos --

def graficos(resultados: dict[str, dict], carpeta: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 6))
    for nombre, res in resultados.items():
        acum = (1 + res["quintiles"]["spread"]).cumprod()
        ax.plot(acum.index, acum.to_numpy(), label=nombre, lw=1.6 if nombre == "combinado" else 1.1)
    ax.set_yscale("log")
    ax.set_title("Diferencial Q5 − Q1 acumulado (largo mejor quintil, corto peor; mensual)")
    ax.grid(alpha=.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(carpeta / "spread_acumulado.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 5))
    for nombre, res in resultados.items():
        ax.plot(res["ic"].rolling(12).mean(), label=nombre, lw=1.6 if nombre == "combinado" else 1.0)
    ax.axhline(0, color="k", lw=.8)
    ax.set_title("IC (Spearman factor → retorno del mes siguiente), media móvil 12 meses")
    ax.grid(alpha=.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(carpeta / "ic_rolling.png", dpi=130)
    plt.close(fig)

    n = len(resultados)
    cols = 4
    filas = int(np.ceil(n / cols))
    fig, axes = plt.subplots(filas, cols, figsize=(3.2 * cols, 2.8 * filas), squeeze=False)
    for ax, (nombre, res) in zip(axes.ravel(), resultados.items()):
        q = res["quintiles"]
        medias = [100 * 12 * q[f"Q{k}"].mean() for k in range(1, 6)]
        ax.bar(range(1, 6), medias, color=["#c0392b", "#e67e22", "#7f8c8d", "#27ae60", "#1e8449"])
        ax.set_title(nombre, fontsize=9)
        ax.set_xticks(range(1, 6))
        ax.set_xticklabels([f"Q{k}" for k in range(1, 6)], fontsize=8)
        ax.grid(axis="y", alpha=.3)
    for ax in axes.ravel()[n:]:
        ax.axis("off")
    fig.suptitle("Retorno medio anualizado (%) por quintil — debería ser monótono si el factor sirve", fontsize=10)
    fig.tight_layout()
    fig.savefig(carpeta / "quintiles.png", dpi=130)
    plt.close(fig)


# ------------------------------------------------------------------- main --

def main() -> None:
    forzar_utf8()
    ap = parser_base(__doc__.split("\n\n")[0], "factores")
    ap.add_argument("--neutral-sector", action="store_true",
                    help="Ordenar dentro de cada sector (elimina apuestas sectoriales)")
    ap.add_argument("--retardo", type=int, default=1,
                    help="Sesiones entre el cálculo de la señal y la entrada (1 = operar al día siguiente)")
    ap.add_argument("--coste-bps", type=float, default=10.0,
                    help="Coste por operación (puntos básicos) para el diferencial neto")
    ap.add_argument("--combinado", default=",".join(COMBINADO_DEFECTO),
                    help="Factores del compuesto, separados por comas")
    ap.add_argument("--min-dolares", type=float, default=5e6,
                    help="Volumen medio mínimo en dólares (60 sesiones) para entrar en el universo")
    ap.add_argument("--top", type=int, default=25, help="Candidatos a listar en el corte de hoy")
    ap.add_argument("--sin-graficos", action="store_true")
    args = ap.parse_args()

    con = conectar(args.db)
    carpeta = preparar_salida(args.salida)
    ficha = resumen_base(con)
    titulo(f"BASE: {ficha['tickers']} tickers · {ficha['primera_fecha']} → {ficha['ultima_fecha']} · "
           f"{ficha['filas_precios']:,} filas de precios · fundamentals: snapshot {ficha['ultimo_snapshot']}")

    tickers = cargar_tickers(con)
    paneles = cargar_precios(con, args.desde, args.hasta)
    paneles = aplicar_mascara(paneles, tickers, activar=not args.sin_filtro_alta)
    adj, close, vol = paneles["adj_close"], paneles["close"], paneles["volume"]
    fund = cargar_fundamentales(con)
    con.close()
    sectores = tickers.set_index("symbol")["sector"]
    print(f"Panel: {adj.shape[0]} sesiones × {adj.shape[1]} valores. "
          f"Filtro de alta en el índice: {'NO' if args.sin_filtro_alta else 'sí'}. "
          f"Neutral por sector: {'sí' if args.neutral_sector else 'no'}. Retardo: {args.retardo} sesión(es).")

    # ----- factores y muestreo mensual --------------------------------------
    factores = calcular_factores(adj, close, vol)
    liquido = factores.pop("_dolares_60") >= args.min_dolares
    fechas = fines_de_mes(adj.index)
    fwd = adj.reindex(fechas).pct_change(fill_method=None).shift(-1)      # retorno del mes siguiente
    fwd = fwd.iloc[:-1]

    ranks = {}
    for nombre, f in factores.items():
        f_ret = f.shift(args.retardo).where(liquido).reindex(fwd.index)
        ranks[nombre] = ranks_por_fecha(f_ret, sectores if args.neutral_sector else None)

    comb = [c.strip() for c in args.combinado.split(",") if c.strip() in ranks]
    if len(comb) >= 2:
        apil = pd.concat([ranks[c] for c in comb], axis=0, keys=comb)
        media = apil.groupby(level=1).mean()
        cuenta = apil.groupby(level=1).count()
        ranks["combinado"] = rank_transversal(media.where(cuenta >= 2))
        FACTORES_PRECIO["combinado"] = "Media de percentiles de: " + ", ".join(comb)

    # ----- backtest -----------------------------------------------------------
    resultados, filas = {}, []
    for nombre, rk in ranks.items():
        res = backtest_factor(rk, fwd)
        if not res:
            continue
        resultados[nombre] = res
        filas.append(resumir(nombre, res, args.coste_bps))
    resumen = pd.DataFrame(filas).set_index("factor").sort_values("IC_tstat_NW", ascending=False)

    titulo("RESULTADOS POR FACTOR (rebalanceo mensual, quintiles equiponderados)")
    tabla(resumen, filas=20, decimales=2)
    print("\nLectura: IC_tstat_NW > 2 ⇒ el factor ha ordenado los retornos futuros de forma "
          "estadísticamente sólida. Q5-Q1 anual = diferencial del mejor quintil frente al peor. "
          "El 'neto' descuenta la rotación de la cesta con el coste indicado.")

    anual = por_anio(resultados)
    titulo("DIFERENCIAL Q5−Q1 POR AÑO (%) — ¿es estable o depende de dos años buenos?")
    tabla(anual, filas=20, decimales=1)

    # ----- exportar -----------------------------------------------------------
    resumen.to_csv(carpeta / "resumen_factores.csv")
    anual.to_csv(carpeta / "por_anio.csv")
    pd.concat({k: v["ic"] for k, v in resultados.items()}, axis=1, sort=True).to_csv(carpeta / "ic_mensual.csv")
    pd.concat({k: v["quintiles"] for k, v in resultados.items()}, axis=1, sort=True).to_csv(carpeta / "quintiles_mensual.csv")
    if not args.sin_graficos:
        graficos(resultados, carpeta)

    # ----- corte transversal de hoy ------------------------------------------
    ultima = adj.index[-1]
    ranks_hoy = pd.DataFrame({n: ranks_por_fecha(
        f.where(liquido).iloc[[-1]], sectores if args.neutral_sector else None).iloc[0]
        for n, f in factores.items()})
    ic_medio = resumen["IC_medio"].reindex(list(factores)).fillna(0)
    pesos = ic_medio.clip(lower=0)
    pesos = pesos / pesos.sum() if pesos.sum() > 0 else pd.Series(1 / len(ic_medio), index=ic_medio.index)
    hoy = corte_hoy(factores, ranks_hoy, pesos, fund, tickers, close.iloc[-1], args.top)
    hoy.to_csv(carpeta / "candidatos_hoy.csv")

    titulo(f"CORTE TRANSVERSAL A {ultima.date()} — top {args.top} por 'total' "
           f"(percentiles 0-1; valor/calidad son dentro del sector)")
    cols = ["sector", "total", "precio_score", "momentum_12_1", "baja_vol", "valor", "calidad",
            "crecimiento", "upside_analistas_%", "pe", "aviso"]
    tabla(hoy[cols].dropna(subset=["total"]), filas=args.top, decimales=2)
    print("\nPesos de los factores de precio en 'precio_score' (proporcionales a su IC medio histórico):")
    print("  " + "  ·  ".join(f"{k}: {v:.0%}" for k, v in pesos.items() if v > 0))

    # ----- informe ------------------------------------------------------------
    informe = [
        "# Backtest de factores — S&P 500 (base DuckDB local)", "",
        f"- Datos: {ficha['tickers']} valores, {ficha['primera_fecha']} → {ficha['ultima_fecha']}.",
        f"- Rebalanceo mensual, quintiles equiponderados, retardo {args.retardo} sesión(es), "
        f"coste {args.coste_bps:.0f} pb, filtro de liquidez {args.min_dolares/1e6:.0f} M$/día, "
        f"neutral por sector: {'sí' if args.neutral_sector else 'no'}.",
        f"- Sesgo de supervivencia: cada valor entra desde su `date_added` "
        f"({'desactivado' if args.sin_filtro_alta else 'activado'}); los que salieron del índice no están en la base, "
        "así que los resultados absolutos son algo optimistas. La *comparación entre factores* sí es fiable.", "",
        "## Resumen por factor", "", resumen.round(3).to_markdown(), "",
        "## Diferencial Q5−Q1 por año (%)", "", anual.round(1).to_markdown(), "",
        "## Definiciones", "",
    ] + [f"- **{k}**: {v}" for k, v in FACTORES_PRECIO.items()] + [
        "", f"## Candidatos hoy ({ultima.date()}) — top {args.top}", "",
        hoy[cols].dropna(subset=["total"]).head(args.top).round(2).to_markdown(), "",
        "Valor, calidad y crecimiento salen del snapshot actual de `fundamentals` y por tanto "
        "**no están backtesteados** (la tabla es una foto, no un histórico). Los factores de precio sí.", "",
        "![](spread_acumulado.png)", "![](ic_rolling.png)", "![](quintiles.png)", "",
        "_Herramienta informativa; no es asesoramiento financiero._",
    ]
    (carpeta / "informe.md").write_text("\n".join(informe), encoding="utf-8")
    print(f"\nArchivos guardados en {carpeta}/  (informe.md, CSV y PNG)")


if __name__ == "__main__":
    main()
