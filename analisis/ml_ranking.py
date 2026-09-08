"""3) MACHINE LEARNING: modelo de ranking transversal con validación walk-forward.

Entrena un LightGBM que, cada fin de mes, intenta ordenar los ~500 valores del
S&P 500 según su retorno relativo del mes siguiente. Se evalúa como se evalúa un
factor (IC, deciles, spread), no con «accuracy», y se compara SIEMPRE contra dos
referencias: el momentum 12-1 a secas y el compuesto de factores. Si el modelo
no gana a la referencia fuera de muestra, no merece la complejidad.

Anti-trampas incorporadas:
  * Walk-forward: se reentrena cada N meses solo con datos anteriores; la
    predicción se hace sobre meses que el modelo nunca ha visto.
  * Purga/embargo: entre el último mes de entrenamiento y el primero de test se
    dejan fuera los meses cuyo retorno futuro solapa con el test.
  * Features solo de precio/volumen, normalizadas por fecha (rangos
    transversales); los `fundamentals` NO entran porque son un snapshot de hoy
    y meterlos sería mirar el futuro.
  * Filtro de alta en el índice (`tickers.date_added`) para el sesgo de supervivencia.
  * Objetivo: rango percentil del retorno del mes siguiente frente al universo
    (regresión sobre 0-1). Predice «mejor o peor que los demás», no el precio.

Por último, entrena con TODO el histórico y saca el ranking de hoy con SHAP-like
importancias (ganancia) para saber qué mira el modelo.

Uso:
    python analisis/ml_ranking.py --db /ruta/sp500.duckdb
    python analisis/ml_ranking.py --db ... --horizonte 21 --reentrenar 6 --min-anios 4

Salida en analisis/salida/ml/.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import (a_largo, aplicar_mascara, cargar_fundamentales, cargar_precios,  # noqa: E402
                   cargar_tickers, conectar, fines_de_mes, forzar_utf8, ic_transversal,
                   metricas, parser_base, preparar_salida, rank_transversal, resumen_base,
                   t_newey_west, tabla, titulo)

warnings.filterwarnings("ignore")


# ------------------------------------------------------------- features --

def construir_features(adj: pd.DataFrame, close: pd.DataFrame, volumen: pd.DataFrame,
                       sectores: pd.Series) -> dict[str, pd.DataFrame]:
    """Panel de features de precio/volumen. Todas se calculan solo con el pasado."""
    r = adj.pct_change(fill_method=None)
    logp = np.log(adj)
    mkt = r.mean(axis=1)
    rel = r.sub(mkt, axis=0)                       # retorno relativo al universo
    dolares = close * volumen

    def ret(n, salto=0):
        return logp.shift(salto) - logp.shift(n)

    f = {
        "mom_12_1": ret(252, 21), "mom_6_1": ret(126, 21), "mom_3_1": ret(63, 21),
        "ret_1m": ret(21), "ret_1w": ret(5),
        "rel_12_1": (rel.shift(21).rolling(231).sum()), "rel_1m": rel.rolling(21).sum(),
        "vol_20": r.rolling(20).std(), "vol_60": r.rolling(60).std(), "vol_250": r.rolling(250).std(),
        "vol_ratio": r.rolling(20).std() / r.rolling(250).std(),
        "dd_252": adj / adj.rolling(252).max() - 1,
        "dist_min_252": adj / adj.rolling(252).min() - 1,
        "sma50": adj / adj.rolling(50).mean() - 1,
        "sma200": adj / adj.rolling(200).mean() - 1,
        "sma50_200": adj.rolling(50).mean() / adj.rolling(200).mean() - 1,
        "pend_200": adj.rolling(200).mean().pct_change(21, fill_method=None),
        "z_20": (adj - adj.rolling(20).mean()) / adj.rolling(20).std(),
        "rsi_14": _rsi(adj, 14),
        "vol_rel_20_120": volumen.rolling(20).mean() / volumen.rolling(120).mean(),
        "dolares_60_log": np.log(dolares.rolling(60).mean()),
        "amihud_20": (r.abs() / dolares).rolling(20).mean() * 1e9,
        "sesgo_60": r.rolling(60).skew(),
        "max_ret_1m": r.rolling(21).max(),
        "beta_120": r.rolling(120).cov(mkt).div(mkt.rolling(120).var(), axis=0),
        "corr_120": r.rolling(120).corr(mkt),
        "up_days_1m": (r > 0).rolling(21).mean(),
    }
    # momentum relativo al sector y momentum del sector
    sect = sectores.reindex(adj.columns).fillna("Desconocido")
    mom_sect = f["mom_12_1"].T.groupby(sect.to_numpy()).transform("mean").T
    f["mom_sector"] = mom_sect
    f["mom_vs_sector"] = f["mom_12_1"] - mom_sect
    ret1m_sect = f["ret_1m"].T.groupby(sect.to_numpy()).transform("mean").T
    f["ret1m_vs_sector"] = f["ret_1m"] - ret1m_sect
    return f


def _rsi(px: pd.DataFrame, n: int) -> pd.DataFrame:
    d = px.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def features_regimen(adj: pd.DataFrame) -> pd.DataFrame:
    """Variables de mercado (iguales para todos los valores en una fecha)."""
    r = adj.pct_change(fill_method=None)
    mkt = r.mean(axis=1)
    idx = (1 + mkt.fillna(0)).cumprod()
    return pd.DataFrame({
        "mkt_ret_1m": idx.pct_change(21, fill_method=None),
        "mkt_ret_12m": idx.pct_change(252, fill_method=None),
        "mkt_vol_20": mkt.rolling(20).std() * np.sqrt(252),
        "mkt_sma200": idx / idx.rolling(200).mean() - 1,
        "corr_media_60": _corr_media(r, 60),
        "dispersion_20": r.std(axis=1).rolling(20).mean(),
    })


def _corr_media(r: pd.DataFrame, n: int) -> pd.Series:
    """Correlación media aproximada: var(media) / media(var), barata de calcular."""
    var_mkt = r.mean(axis=1).rolling(n).var()
    var_med = r.rolling(n).var().mean(axis=1)
    return (var_mkt / var_med).clip(0, 1)


def montar_panel(features: dict, regimen: pd.DataFrame, adj: pd.DataFrame, fechas: pd.DatetimeIndex,
                 horizonte_meses: int, retardo: int, sectores: pd.Series, min_dolares: float) -> pd.DataFrame:
    """Une features (rangos transversales) + objetivo en un DataFrame largo por (fecha, símbolo)."""
    liquido = np.exp(features["dolares_60_log"]) >= min_dolares
    columnas = {}
    for nombre, f in features.items():
        muestra = f.shift(retardo).where(liquido).reindex(fechas)
        columnas["rk_" + nombre] = a_largo(rank_transversal(muestra), "rk_" + nombre)
    panel = pd.concat(columnas.values(), axis=1)
    # objetivo: retorno del mes siguiente y su rango transversal
    px = adj.reindex(fechas)
    fwd = px.shift(-horizonte_meses) / px - 1
    exceso = fwd.sub(fwd.mean(axis=1), axis=0)
    panel = panel.join(a_largo(fwd, "fwd_ret"), how="left")
    panel = panel.join(a_largo(exceso, "fwd_exceso"), how="left")
    panel = panel.join(a_largo(rank_transversal(fwd), "objetivo"), how="left")
    reg = regimen.shift(retardo).reindex(fechas)
    panel = panel.join(reg, on="date")
    # Categorías fijas (todas las del universo) para que los códigos que ve LightGBM
    # sean los mismos al entrenar y al puntuar el día de hoy.
    categorias = sorted(sectores.fillna("Desconocido").unique().tolist() + ["Desconocido"])
    categorias = sorted(set(categorias))
    sect = sectores.reindex(panel.index.get_level_values("symbol")).fillna("Desconocido")
    panel["sector"] = pd.Categorical(sect.to_numpy(), categories=categorias)
    return panel


# ------------------------------------------------------------ modelo --

def parametros(semilla: int) -> dict:
    return dict(objective="regression", learning_rate=0.03, num_leaves=15, min_child_samples=200,
                feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, lambda_l2=5.0,
                n_estimators=400, verbose=-1, random_state=semilla, n_jobs=-1)


def walk_forward(panel: pd.DataFrame, cols: list[str], fechas: pd.DatetimeIndex, min_meses: int,
                 cada: int, horizonte_meses: int, semilla: int) -> tuple[pd.Series, pd.DataFrame]:
    import lightgbm as lgb
    pred = pd.Series(np.nan, index=panel.index, name="pred")
    importancias = []
    fechas_test = [f for f in fechas if f in panel.index.get_level_values("date")]
    i = min_meses
    n_modelos = 0
    while i < len(fechas_test):
        f_test = fechas_test[i:i + cada]
        # embargo: los últimos 'horizonte' meses antes del test tienen objetivo que pisa el test
        f_train = fechas_test[:max(i - horizonte_meses, 1)]
        tr = panel.loc[panel.index.get_level_values("date").isin(f_train)].dropna(subset=["objetivo"])
        te = panel.loc[panel.index.get_level_values("date").isin(f_test)]
        if len(tr) < 2000 or te.empty:
            i += cada
            continue
        modelo = lgb.LGBMRegressor(**parametros(semilla))
        modelo.fit(tr[cols], tr["objetivo"], categorical_feature=["sector"] if "sector" in cols else "auto")
        pred.loc[te.index] = modelo.predict(te[cols])
        importancias.append(pd.Series(modelo.booster_.feature_importance("gain"), index=cols,
                                      name=str(f_test[0].date())))
        n_modelos += 1
        i += cada
    imp = pd.concat(importancias, axis=1) if importancias else pd.DataFrame()
    print(f"  {n_modelos} modelos entrenados (walk-forward cada {cada} meses, embargo {horizonte_meses} mes(es)).")
    return pred, imp


def evaluar(panel: pd.DataFrame, senal: str, n_q: int = 10) -> dict:
    """IC mensual, deciles y spread D10-D1 de una señal (columna del panel)."""
    df = panel[[senal, "fwd_ret", "fwd_exceso"]].dropna()
    if df.empty:
        return {}
    ancho_s = df[senal].unstack("symbol")
    ancho_f = df["fwd_ret"].unstack("symbol")
    ic = ic_transversal(ancho_s, ancho_f)
    q = np.ceil(rank_transversal(ancho_s) * n_q).clip(1, n_q)
    filas = []
    for fecha in ancho_s.index:
        qi, fi = q.loc[fecha], ancho_f.loc[fecha]
        ok = qi.notna() & fi.notna()
        if ok.sum() < 50:
            continue
        fila = {"date": fecha}
        for k in range(1, n_q + 1):
            fila[f"D{k}"] = fi[ok & (qi == k)].mean()
        fila["universo"] = fi[ok].mean()
        filas.append(fila)
    dec = pd.DataFrame(filas).set_index("date")
    dec["spread"] = dec[f"D{n_q}"] - dec["D1"]
    dec["top_exceso"] = dec[f"D{n_q}"] - dec["universo"]
    m = metricas(dec["spread"], 12)
    mt = metricas(dec["top_exceso"], 12)
    return {"ic": ic, "deciles": dec,
            "resumen": {"IC_medio": ic.mean(), "IC_tstat_NW": t_newey_west(ic, 1), "IC_IR": ic.mean() / ic.std(),
                        "IC_%positivo": 100 * (ic > 0).mean(), "D10-D1_anual_%": m.get("CAGR_%"),
                        "D10-D1_Sharpe": m.get("Sharpe"), "D10_exceso_anual_%": mt.get("CAGR_%"),
                        "MaxDD_spread_%": m.get("MaxDD_%"), "meses": len(dec)}}


# ------------------------------------------------------------ gráficos --

def graficos(evals: dict[str, dict], imp: pd.DataFrame, carpeta: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for nombre, ev in evals.items():
        axes[0].plot((1 + ev["deciles"]["spread"]).cumprod(), label=nombre, lw=1.8 if nombre == "modelo_ML" else 1.1)
        axes[1].plot(ev["ic"].rolling(12).mean(), label=nombre, lw=1.8 if nombre == "modelo_ML" else 1.1)
    axes[0].set_yscale("log")
    axes[0].set_title("D10 − D1 acumulado (fuera de muestra)")
    axes[1].axhline(0, color="k", lw=.8)
    axes[1].set_title("IC media móvil 12 meses (fuera de muestra)")
    for ax in axes:
        ax.grid(alpha=.3)
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(carpeta / "modelo_vs_referencias.png", dpi=130)
    plt.close(fig)

    fig, axes = plt.subplots(1, len(evals), figsize=(4.5 * len(evals), 3.8), squeeze=False)
    for ax, (nombre, ev) in zip(axes[0], evals.items()):
        d = ev["deciles"]
        vals = [100 * 12 * d[f"D{k}"].mean() for k in range(1, 11)]
        ax.bar(range(1, 11), vals, color=plt.cm.RdYlGn(np.linspace(0.1, 0.9, 10)))
        ax.set_title(f"{nombre}: retorno anualizado por decil (%)", fontsize=9)
        ax.set_xticks(range(1, 11))
        ax.grid(axis="y", alpha=.3)
    fig.tight_layout()
    fig.savefig(carpeta / "deciles.png", dpi=130)
    plt.close(fig)

    if not imp.empty:
        media = imp.div(imp.sum()).mean(axis=1).sort_values().tail(20)
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.barh(media.index, 100 * media.to_numpy(), color="#34495e")
        ax.set_title("Importancia media (ganancia, %) en los modelos walk-forward — top 20")
        ax.grid(axis="x", alpha=.3)
        fig.tight_layout()
        fig.savefig(carpeta / "importancias.png", dpi=130)
        plt.close(fig)


# ---------------------------------------------------------------- main --

def main() -> None:
    forzar_utf8()
    ap = parser_base(__doc__.split("\n\n")[0], "ml")
    ap.add_argument("--horizonte", type=int, default=1, help="Horizonte del objetivo en meses")
    ap.add_argument("--retardo", type=int, default=1, help="Sesiones entre señal y entrada")
    ap.add_argument("--min-anios", type=float, default=4.0, help="Años mínimos de historial antes del primer test")
    ap.add_argument("--reentrenar", type=int, default=6, help="Cada cuántos meses se reentrena")
    ap.add_argument("--min-dolares", type=float, default=5e6, help="Volumen medio mínimo en dólares")
    ap.add_argument("--sin-regimen", action="store_true", help="No usar variables de régimen de mercado")
    ap.add_argument("--sin-sector", action="store_true", help="No usar el sector como categoría")
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--sin-graficos", action="store_true")
    args = ap.parse_args()

    con = conectar(args.db)
    carpeta = preparar_salida(args.salida)
    ficha = resumen_base(con)
    titulo(f"BASE: {ficha['tickers']} tickers · {ficha['primera_fecha']} → {ficha['ultima_fecha']}")
    info = cargar_tickers(con)
    paneles = aplicar_mascara(cargar_precios(con, args.desde, args.hasta), info,
                              activar=not args.sin_filtro_alta)
    fund = cargar_fundamentales(con)
    con.close()
    adj, close, vol = paneles["adj_close"], paneles["close"], paneles["volume"]
    sectores = info.set_index("symbol")["sector"]

    print("Calculando features...")
    feats = construir_features(adj, close, vol, sectores)
    reg = features_regimen(adj)
    fechas = fines_de_mes(adj.index)
    panel = montar_panel(feats, reg, adj, fechas, args.horizonte, args.retardo, sectores, args.min_dolares)
    cols = [c for c in panel.columns if c.startswith("rk_")]
    if not args.sin_regimen:
        cols += list(reg.columns)
    if not args.sin_sector:
        cols.append("sector")
    n_fechas = panel.index.get_level_values("date").nunique()
    print(f"Panel: {len(panel):,} observaciones (fecha × valor), {n_fechas} meses, {len(cols)} features.")

    # ---- walk-forward ----------------------------------------------------------
    titulo("VALIDACIÓN WALK-FORWARD (cada predicción se hace con un modelo que solo vio el pasado)")
    min_meses = int(args.min_anios * 12)
    pred, imp = walk_forward(panel, cols, fechas, min_meses, args.reentrenar, args.horizonte, args.semilla)
    panel["modelo_ML"] = pred
    # referencias sobre exactamente las mismas fechas/valores en que hay predicción
    valid = panel["modelo_ML"].notna()
    panel["ref_momentum_12_1"] = panel["rk_mom_12_1"].where(valid)
    comp = panel[["rk_mom_12_1", "rk_vol_250", "rk_dd_252", "rk_sma200"]].copy()
    comp["rk_vol_250"] = 1 - comp["rk_vol_250"]           # baja volatilidad
    panel["ref_compuesto"] = comp.mean(axis=1).where(valid)

    evals = {}
    for s in ("modelo_ML", "ref_momentum_12_1", "ref_compuesto"):
        ev = evaluar(panel, s)
        if ev:
            evals[s] = ev
    if "modelo_ML" not in evals:
        sys.exit("No hubo suficientes datos para validar el modelo (prueba --min-anios menor).")
    resumen = pd.DataFrame({k: v["resumen"] for k, v in evals.items()}).T
    tabla(resumen, decimales=3)
    ic_ml, ic_ref = evals["modelo_ML"]["ic"], evals["ref_momentum_12_1"]["ic"]
    dif = (ic_ml - ic_ref).dropna()
    t_dif = t_newey_west(dif, 1)
    veredicto = ("el modelo aporta algo real sobre el momentum" if t_dif > 2 else
                 "la diferencia NO es significativa: el momentum a secas hace casi lo mismo")
    print(f"\nIC(modelo) − IC(momentum) medio = {dif.mean():+.4f}, t-stat NW = {t_dif:+.2f} ({veredicto}).")
    print("Guía: IC fuera de muestra de 0.02-0.05 ya es útil; > 0.10 en datos diarios de large caps es sospechoso de fuga.")

    por_anio = pd.DataFrame({k: v["ic"].groupby(v["ic"].index.year).mean() for k, v in evals.items()})
    por_anio["spread_ML_%"] = evals["modelo_ML"]["deciles"]["spread"].groupby(
        evals["modelo_ML"]["deciles"].index.year).apply(lambda s: 100 * ((1 + s).prod() - 1))
    titulo("IC MEDIO POR AÑO (fuera de muestra) — estabilidad")
    tabla(por_anio, filas=15, decimales=3)

    if not imp.empty:
        media_imp = imp.div(imp.sum()).mean(axis=1).sort_values(ascending=False)
        titulo("QUÉ MIRA EL MODELO (importancia media por ganancia, %)")
        print("  " + "\n  ".join(f"{k:<22} {100*v:5.1f}%" for k, v in media_imp.head(15).items()))
        imp.to_csv(carpeta / "importancias_walk_forward.csv")

    resumen.to_csv(carpeta / "resumen_modelo.csv")
    por_anio.to_csv(carpeta / "ic_por_anio.csv")
    panel.loc[valid, ["modelo_ML", "ref_momentum_12_1", "ref_compuesto", "fwd_ret", "fwd_exceso"]] \
        .to_csv(carpeta / "predicciones_fuera_de_muestra.csv")

    # ---- modelo final y ranking de hoy -----------------------------------------
    import lightgbm as lgb
    titulo(f"RANKING DE HOY ({adj.index[-1].date()}) con el modelo entrenado en todo el histórico")
    tr = panel.dropna(subset=["objetivo"])
    final = lgb.LGBMRegressor(**parametros(args.semilla))
    final.fit(tr[cols], tr["objetivo"], categorical_feature=["sector"] if "sector" in cols else "auto")
    ultima = adj.index[[-1]]
    hoy = montar_panel(feats, reg, adj, pd.DatetimeIndex(ultima), args.horizonte, 0, sectores, args.min_dolares)
    hoy = hoy.dropna(subset=[c for c in cols if c != "sector"], thresh=int(0.8 * (len(cols) - 1)))
    hoy["pred"] = final.predict(hoy[cols])
    hoy = hoy.droplevel("date")
    hoy["percentil_modelo"] = hoy["pred"].rank(pct=True)
    salida = pd.DataFrame({
        "sector": hoy["sector"].astype(str),
        "percentil_modelo": hoy["percentil_modelo"],
        "mom_12_1": hoy["rk_mom_12_1"], "ret_1m": hoy["rk_ret_1m"], "vol_250": hoy["rk_vol_250"],
        "dd_252": hoy["rk_dd_252"], "sma200": hoy["rk_sma200"], "vol_rel": hoy["rk_vol_rel_20_120"],
        "pe": fund["pe_ratio"].reindex(hoy.index), "market_cap_bn": fund["market_cap"].reindex(hoy.index) / 1e9,
    }).sort_values("percentil_modelo", ascending=False)
    salida.to_csv(carpeta / "ranking_hoy.csv")
    print(f"TOP {args.top} (percentil_modelo = posición 0-1 en el ranking; el resto son rangos de las features):")
    tabla(salida, filas=args.top, decimales=2)
    print(f"\nBOTTOM 10 (los que el modelo espera que lo hagan peor que el índice):")
    tabla(salida.tail(10), filas=10, decimales=2)

    if not args.sin_graficos:
        graficos(evals, imp, carpeta)

    informe = [
        "# Modelo de ranking (LightGBM) — validación walk-forward", "",
        f"- Datos: {ficha['tickers']} valores, {ficha['primera_fecha']} → {ficha['ultima_fecha']}; "
        f"objetivo: rango del retorno a {args.horizonte} mes(es); reentreno cada {args.reentrenar} meses; "
        f"primer test tras {args.min_anios:g} años; embargo {args.horizonte} mes(es); retardo {args.retardo} sesión.",
        f"- Features: {len(cols)} (rangos transversales de precio/volumen"
        f"{'' if args.sin_regimen else ' + régimen de mercado'}{'' if args.sin_sector else ' + sector'}). "
        "Sin fundamentales (son un snapshot de hoy: usarlos sería mirar el futuro).", "",
        "## Fuera de muestra: modelo vs referencias", "", resumen.round(3).to_markdown(), "",
        f"IC(modelo) − IC(momentum): media {dif.mean():+.4f}, t-stat NW {t_newey_west(dif, 1):+.2f}.", "",
        "## IC por año", "", por_anio.round(3).to_markdown(), "",
        "## Importancias", "", (media_imp.head(20).mul(100).round(1).to_frame("%").to_markdown() if not imp.empty else ""), "",
        f"## Ranking de hoy ({adj.index[-1].date()}) — top {args.top}", "", salida.head(args.top).round(2).to_markdown(), "",
        "![](modelo_vs_referencias.png)", "![](deciles.png)", "![](importancias.png)", "",
        "_Herramienta informativa; no es asesoramiento financiero._",
    ]
    (carpeta / "informe.md").write_text("\n".join(informe), encoding="utf-8")
    print(f"\nArchivos guardados en {carpeta}/")


if __name__ == "__main__":
    main()
