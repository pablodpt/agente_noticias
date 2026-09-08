"""2) ESTRUCTURA DE CORRELACIONES del S&P 500 y riesgo real de tu cartera.

Qué calcula:

  A. RÉGIMEN: correlación media entre pares, 'absorption ratio' (% de varianza que
     explica el primer componente principal) y dispersión transversal, semana a semana
     durante los 10 años. Cuando todo se mueve a la vez, la diversificación desaparece.
  B. CLUSTERS: agrupa los ~500 valores por cómo se mueven de verdad (clustering
     jerárquico sobre la matriz de correlación con shrinkage de Ledoit-Wolf) y lo
     compara con los sectores GICS. Saca los valores 'mal clasificados' (cotizan con
     otro grupo) y la matriz de correlación media entre sectores.
  C. TU CARTERA: volatilidad, beta, contribución al riesgo de cada posición, número
     efectivo de apuestas independientes, y qué valores del índice diversificarían de
     verdad (baja correlación con lo que ya tienes) frente a cuáles son redundantes.
  D. DESACOPLES: qué valores se han movido hoy de forma anómala respecto a su cluster
     y al mercado (residuo > 2.5σ). Es una alerta mucho más limpia que un umbral fijo
     del 3%, que salta para todos cuando cae el índice.
  E. HRP vs EQUIPONDERADO (opcional, --hrp): backtest mensual de Hierarchical Risk
     Parity, que usa la estructura de clusters para repartir el riesgo.
  F. PARES (opcional, --pares): pares cointegrados dentro de la misma industria, con
     half-life y z-score actual del spread.

Uso:
    python analisis/correlaciones.py --db /ruta/sp500.duckdb
    python analisis/correlaciones.py --db ... --cartera "AAPL:20,MSFT:20,NVDA:15,AMZN:15" --hrp --pares

Salida en analisis/salida/correlaciones/.
"""
from __future__ import annotations

import sys
import warnings
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comun import (aplicar_mascara, cargar_fundamentales, cargar_precios, cargar_tickers,  # noqa: E402
                   conectar, fines_de_mes, forzar_utf8, metricas, parsear_cartera,
                   parser_base, preparar_salida, resumen_base, tabla, titulo)

warnings.filterwarnings("ignore", category=RuntimeWarning)


# ------------------------------------------------------------ utilidades --

def cov_shrink(r: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Covarianza y correlación con shrinkage de Ledoit-Wolf sobre una ventana.

    Con 500 activos y ~250 observaciones la matriz muestral es casi singular y
    llena de ruido; el shrinkage la 'encoge' hacia una estructura simple y la
    hace utilizable para clustering y asignación.
    """
    from sklearn.covariance import LedoitWolf
    cols = [c for c in r.columns if r[c].notna().mean() >= 0.8]
    x = r[cols].fillna(0.0).to_numpy()
    lw = LedoitWolf().fit(x)
    cov = lw.covariance_
    sd = np.sqrt(np.diag(cov))
    corr = cov / np.outer(sd, sd)
    np.fill_diagonal(corr, 1.0)
    return cov, np.clip(corr, -1, 1), cols


def corr_a_distancia(corr: np.ndarray) -> np.ndarray:
    d = np.sqrt(np.clip(0.5 * (1 - corr), 0, None))
    d = 0.5 * (d + d.T)
    np.fill_diagonal(d, 0.0)
    return d


def clusterizar(corr: np.ndarray, n_clusters: int = 0) -> tuple[np.ndarray, np.ndarray, int]:
    """Clustering jerárquico (average linkage). Si n_clusters=0 elige k por silueta."""
    from scipy.cluster.hierarchy import fcluster, leaves_list, linkage
    from scipy.spatial.distance import squareform
    from sklearn.metrics import silhouette_score
    dist = corr_a_distancia(corr)
    Z = linkage(squareform(dist, checks=False), method="average")
    if n_clusters <= 0:
        mejor, mejor_k = -1.0, 8
        for k in range(6, 21):
            lab = fcluster(Z, k, criterion="maxclust")
            if len(np.unique(lab)) < 2:
                continue
            s = silhouette_score(dist, lab, metric="precomputed")
            if s > mejor:
                mejor, mejor_k = s, k
        n_clusters = mejor_k
    etiquetas = fcluster(Z, n_clusters, criterion="maxclust")
    return etiquetas, leaves_list(Z), n_clusters


def hrp_pesos(cov: np.ndarray, orden: np.ndarray) -> np.ndarray:
    """Hierarchical Risk Parity (López de Prado): bisección recursiva sobre el orden del dendrograma."""
    def var_cluster(idx):
        sub = cov[np.ix_(idx, idx)]
        ivp = 1 / np.diag(sub)
        ivp /= ivp.sum()
        return float(ivp @ sub @ ivp)

    w = np.ones(cov.shape[0])
    grupos = [list(orden)]
    while grupos:
        nuevos = []
        for g in grupos:
            if len(g) <= 1:
                continue
            m = len(g) // 2
            g1, g2 = g[:m], g[m:]
            v1, v2 = var_cluster(g1), var_cluster(g2)
            a = 1 - v1 / (v1 + v2) if (v1 + v2) > 0 else 0.5
            w[g1] *= a
            w[g2] *= (1 - a)
            nuevos += [g1, g2]
        grupos = nuevos
    return w / w.sum()


# ------------------------------------------------------------ A. régimen --

def serie_regimen(r: pd.DataFrame, ventana: int, paso: int) -> pd.DataFrame:
    filas = []
    for t in range(ventana, len(r), paso):
        sub = r.iloc[t - ventana:t]
        sub = sub.loc[:, sub.notna().mean() >= 0.9]
        if sub.shape[1] < 30:
            continue
        x = np.array(sub.sub(sub.mean()).fillna(0.0).to_numpy(), dtype=float, copy=True)
        x = x / (x.std(axis=0, ddof=1) + 1e-12)
        c = (x.T @ x) / (len(x) - 1)
        n = c.shape[0]
        media = (c.sum() - n) / (n * (n - 1))
        eig = np.sort(np.linalg.eigvalsh(c))[::-1]
        filas.append({
            "date": r.index[t - 1],
            "corr_media": media,
            "absorcion_pc1": eig[0] / eig.sum(),
            "absorcion_pc5": eig[:5].sum() / eig.sum(),
            "dispersion_%": 100 * sub.std(axis=1).mean(),
            "vol_mercado_%": 100 * sub.mean(axis=1).std() * np.sqrt(252),
            "n": n,
        })
    return pd.DataFrame(filas).set_index("date")


# ---------------------------------------------------------- C. cartera --

def analizar_cartera(r: pd.DataFrame, cov: np.ndarray, corr: np.ndarray, cols: list[str],
                     tickers_cart: list[str], pesos_cart: dict[str, float],
                     mercado: pd.Series, info: pd.DataFrame, fund: pd.DataFrame) -> dict:
    pos = {c: i for i, c in enumerate(cols)}
    presentes = [t for t in tickers_cart if t in pos]
    ausentes = [t for t in tickers_cart if t not in pos]
    if len(presentes) < 2:
        return {"error": f"Solo {len(presentes)} valores de la cartera están en la base: {presentes}"}
    idx = [pos[t] for t in presentes]
    w = np.array([pesos_cart.get(t, 1.0) for t in presentes], dtype=float)
    w /= w.sum()
    C = cov[np.ix_(idx, idx)] * 252
    R = corr[np.ix_(idx, idx)]
    var_p = float(w @ C @ w)
    vol_p = np.sqrt(var_p)
    mcr = C @ w / vol_p                       # riesgo marginal
    rc = w * mcr / vol_p                      # contribución al riesgo (suma 1)
    vols = np.sqrt(np.diag(C))
    ratio_div = float(w @ vols / vol_p)       # >1 = hay diversificación
    enb = 1 / float((rc ** 2).sum())          # nº efectivo de apuestas (Herfindahl)
    off = R[np.triu_indices(len(idx), 1)]

    ret_cart = (r[presentes].fillna(0.0) * w).sum(axis=1)
    beta = float(np.cov(ret_cart, mercado.reindex(ret_cart.index).fillna(0.0))[0, 1]
                 / mercado.var())

    detalle = pd.DataFrame({
        "peso_%": 100 * w, "vol_anual_%": 100 * vols, "contrib_riesgo_%": 100 * rc,
        "corr_media_resto": [(R[i].sum() - 1) / (len(idx) - 1) for i in range(len(idx))],
        "sector": info.set_index("symbol")["sector"].reindex(presentes).to_numpy(),
    }, index=presentes)

    # candidatos que diversifican: baja correlación con la cartera como conjunto
    corr_con_cart = r[cols].corrwith(ret_cart).drop(labels=presentes, errors="ignore")
    liquido = fund["avg_volume"].reindex(cols).fillna(0) * r[cols].notna().sum().reindex(cols)  # solo para evitar NaN
    cand = pd.DataFrame({
        "corr_con_cartera": corr_con_cart,
        "vol_anual_%": 100 * r[corr_con_cart.index].std() * np.sqrt(252),
        "sector": info.set_index("symbol")["sector"].reindex(corr_con_cart.index),
        "market_cap_bn": fund["market_cap"].reindex(corr_con_cart.index) / 1e9,
    }).dropna(subset=["corr_con_cartera"])
    # efecto de añadir un 10% del candidato (reescalando el resto): nueva vol de cartera
    nuevas = []
    for t in cand.index:
        j = pos[t]
        idx2 = idx + [j]
        w2 = np.append(0.9 * w, 0.1)
        C2 = cov[np.ix_(idx2, idx2)] * 252
        nuevas.append(100 * np.sqrt(float(w2 @ C2 @ w2)))
    cand["vol_cartera_si_añades_10%"] = nuevas
    cand["reduccion_vol_pp"] = 100 * vol_p - cand["vol_cartera_si_añades_10%"]
    diversifican = cand.sort_values("corr_con_cartera").head(15)
    redundantes = cand.sort_values("corr_con_cartera", ascending=False).head(10)

    return {
        "presentes": presentes, "ausentes": ausentes, "vol_%": 100 * vol_p, "beta": beta,
        "ratio_diversificacion": ratio_div, "apuestas_efectivas": enb,
        "corr_media_pares": float(off.mean()), "corr_max_par": float(off.max()),
        "detalle": detalle.sort_values("contrib_riesgo_%", ascending=False),
        "matriz": pd.DataFrame(R, index=presentes, columns=presentes),
        "diversifican": diversifican, "redundantes": redundantes,
    }


# --------------------------------------------------------- D. desacoples --

def desacoples(r: pd.DataFrame, etiquetas: pd.Series, mercado: pd.Series, ventana: int = 120,
               umbral: float = 2.5) -> pd.DataFrame:
    sub = r.iloc[-ventana:]
    m = mercado.reindex(sub.index).fillna(0.0).to_numpy()
    filas = []
    for cl, cols in etiquetas.groupby(etiquetas).groups.items():
        cols = [c for c in cols if c in sub.columns]
        if len(cols) < 3:
            continue
        bloque = sub[cols].fillna(0.0)
        suma = bloque.sum(axis=1)
        for c in cols:
            y = bloque[c].to_numpy()
            resto = ((suma - bloque[c]) / (len(cols) - 1)).to_numpy()
            X = np.column_stack([np.ones(len(y)), m, resto])
            try:
                beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            except np.linalg.LinAlgError:
                continue
            res = y - X @ beta
            sd = res[:-5].std(ddof=3)
            if not np.isfinite(sd) or sd == 0:
                continue
            z = res / sd
            filas.append({
                "symbol": c, "cluster": cl, "z_hoy": z[-1], "z_max_5d": z[-5:][np.argmax(np.abs(z[-5:]))],
                "ret_hoy_%": 100 * y[-1], "explicado_%": 100 * (X[-1] @ beta),
                "residuo_%": 100 * res[-1], "beta_mercado": beta[1], "beta_cluster": beta[2],
                "R2": 1 - res.var() / y.var() if y.var() > 0 else np.nan,
            })
    df = pd.DataFrame(filas).set_index("symbol")
    df["alerta"] = np.where(np.abs(df["z_hoy"]) >= umbral, "HOY",
                            np.where(np.abs(df["z_max_5d"]) >= umbral, "5 días", ""))
    return df.reindex(df["z_hoy"].abs().sort_values(ascending=False).index)


# ------------------------------------------------------------ E. HRP --

def backtest_hrp(r: pd.DataFrame, ventana: int = 252) -> tuple[pd.DataFrame, pd.DataFrame]:
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform
    fechas = fines_de_mes(r.index)
    filas, rotacion = [], []
    prev = {}
    for i in range(len(fechas) - 1):
        fin = r.index.get_loc(fechas[i])
        if fin < ventana:
            continue
        hist = r.iloc[fin - ventana + 1:fin + 1]
        cov, corr, cols = cov_shrink(hist)
        if len(cols) < 30:
            continue
        Z = linkage(squareform(corr_a_distancia(corr), checks=False), method="average")
        w_hrp = hrp_pesos(cov, leaves_list(Z))
        sd = np.sqrt(np.diag(cov))
        w_iv = (1 / sd) / (1 / sd).sum()
        w_ew = np.full(len(cols), 1 / len(cols))
        prox = r.loc[fechas[i]:fechas[i + 1], cols].iloc[1:].fillna(0.0)
        acum = (1 + prox).prod() - 1
        filas.append({"date": fechas[i + 1], "Equiponderado": float(acum @ w_ew),
                      "Inversa_vol": float(acum @ w_iv), "HRP": float(acum @ w_hrp),
                      "n": len(cols), "HRP_peso_max_%": 100 * w_hrp.max()})
        actual = dict(zip(cols, w_hrp))
        if prev:
            todas = set(actual) | set(prev)
            rotacion.append(0.5 * sum(abs(actual.get(t, 0) - prev.get(t, 0)) for t in todas))
        prev = actual
    res = pd.DataFrame(filas).set_index("date")
    m = pd.DataFrame({c: metricas(res[c], 12) for c in ("Equiponderado", "Inversa_vol", "HRP")}).T
    m.loc["HRP", "rotacion_mensual_%"] = 100 * np.mean(rotacion) if rotacion else np.nan
    return res, m


# ------------------------------------------------------------ F. pares --

def buscar_pares(adj: pd.DataFrame, info: pd.DataFrame, ventana: int = 504, corr_min: float = 0.6,
                 max_pares: int = 3000) -> pd.DataFrame:
    from statsmodels.tsa.stattools import coint
    logp = np.log(adj.iloc[-ventana:])
    logp = logp.loc[:, logp.notna().mean() >= 0.95].ffill().bfill()
    r = logp.diff()
    ind = info.set_index("symbol")["industry"].reindex(logp.columns)
    candidatos = []
    for industria, grupo in ind.groupby(ind):
        cols = list(grupo.index)
        if len(cols) < 2:
            continue
        c = r[cols].corr()
        for a, b in combinations(cols, 2):
            if c.loc[a, b] >= corr_min:
                candidatos.append((c.loc[a, b], a, b, industria))
    candidatos.sort(reverse=True)
    filas = []
    for rho, a, b, industria in candidatos[:max_pares]:
        y, x = logp[a].to_numpy(), logp[b].to_numpy()
        try:
            _, pval, _ = coint(y, x, trend="c", autolag="aic", maxlag=5)
        except Exception:
            continue
        if pval > 0.05:
            continue
        X = np.column_stack([np.ones(len(x)), x])
        (alpha, beta), *_ = np.linalg.lstsq(X, y, rcond=None)
        s = y - (alpha + beta * x)
        ds, s1 = np.diff(s), s[:-1]
        Xs = np.column_stack([np.ones(len(s1)), s1])
        (_, phi), *_ = np.linalg.lstsq(Xs, ds, rcond=None)
        if phi >= 0:
            continue
        half_life = -np.log(2) / np.log(1 + phi)
        if not (2 <= half_life <= 60):
            continue
        z = (s[-1] - s[-60:].mean()) / (s[-60:].std(ddof=1) + 1e-12)
        filas.append({"par": f"{a}/{b}", "industria": industria, "corr_diaria": rho, "p_valor": pval,
                      "hedge_ratio": beta, "half_life_dias": half_life, "z_spread_hoy": z,
                      "señal": ("largo " + a + " / corto " + b) if z <= -2 else
                               (("corto " + a + " / largo " + b) if z >= 2 else "")})
    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.sort_values("p_valor").set_index("par")
    return df


# ------------------------------------------------------------ gráficos --

def graficos(reg: pd.DataFrame, sect_corr: pd.DataFrame, conting: pd.DataFrame,
             cart: dict | None, hrp: pd.DataFrame | None, carpeta: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
    axes[0].plot(reg["corr_media"], color="#1f77b4")
    axes[0].set_title("Correlación media entre pares (ventana móvil)")
    axes[1].plot(reg["absorcion_pc1"], color="#d62728")
    axes[1].set_title("Absorption ratio: % de varianza explicada por el 1er componente principal")
    axes[2].plot(reg["dispersion_%"], color="#2ca02c")
    axes[2].set_title("Dispersión transversal media de retornos diarios (%)")
    for ax in axes:
        ax.grid(alpha=.3)
    fig.tight_layout()
    fig.savefig(carpeta / "regimen_correlacion.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 7.5))
    im = ax.imshow(sect_corr.to_numpy(), cmap="RdYlGn_r", vmin=0, vmax=1)
    ax.set_xticks(range(len(sect_corr)))
    ax.set_xticklabels(sect_corr.columns, rotation=60, ha="right", fontsize=8)
    ax.set_yticks(range(len(sect_corr)))
    ax.set_yticklabels(sect_corr.index, fontsize=8)
    for i in range(len(sect_corr)):
        for j in range(len(sect_corr)):
            ax.text(j, i, f"{sect_corr.iat[i, j]:.2f}", ha="center", va="center", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=.8)
    ax.set_title("Correlación media entre sectores (diagonal = dentro del sector)")
    fig.tight_layout()
    fig.savefig(carpeta / "heatmap_sectores.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 6))
    im = ax.imshow(conting.to_numpy(), cmap="Blues", aspect="auto")
    ax.set_xticks(range(conting.shape[1]))
    ax.set_xticklabels(conting.columns, rotation=60, ha="right", fontsize=8)
    ax.set_yticks(range(conting.shape[0]))
    ax.set_yticklabels([f"cluster {c}" for c in conting.index], fontsize=8)
    for i in range(conting.shape[0]):
        for j in range(conting.shape[1]):
            v = conting.iat[i, j]
            if v:
                ax.text(j, i, str(v), ha="center", va="center", fontsize=7)
    ax.set_title("Clusters estadísticos × sectores GICS (nº de valores)")
    fig.tight_layout()
    fig.savefig(carpeta / "clusters_vs_sectores.png", dpi=130)
    plt.close(fig)

    if cart and "matriz" in cart:
        M = cart["matriz"]
        fig, ax = plt.subplots(figsize=(6.5, 5.5))
        im = ax.imshow(M.to_numpy(), cmap="RdYlGn_r", vmin=0, vmax=1)
        ax.set_xticks(range(len(M)))
        ax.set_xticklabels(M.columns, rotation=45, ha="right")
        ax.set_yticks(range(len(M)))
        ax.set_yticklabels(M.index)
        for i in range(len(M)):
            for j in range(len(M)):
                ax.text(j, i, f"{M.iat[i, j]:.2f}", ha="center", va="center", fontsize=8)
        fig.colorbar(im, ax=ax, shrink=.8)
        ax.set_title("Correlaciones dentro de tu cartera")
        fig.tight_layout()
        fig.savefig(carpeta / "cartera_correlaciones.png", dpi=130)
        plt.close(fig)

    if hrp is not None and not hrp.empty:
        fig, ax = plt.subplots(figsize=(11, 5))
        for c in ("Equiponderado", "Inversa_vol", "HRP"):
            ax.plot((1 + hrp[c]).cumprod(), label=c)
        ax.set_yscale("log")
        ax.legend()
        ax.grid(alpha=.3)
        ax.set_title("Universo completo: equiponderado vs inversa de volatilidad vs HRP (rebalanceo mensual)")
        fig.tight_layout()
        fig.savefig(carpeta / "hrp_vs_equiponderado.png", dpi=130)
        plt.close(fig)


# ----------------------------------------------------------------- main --

def main() -> None:
    forzar_utf8()
    ap = parser_base(__doc__.split("\n\n")[0], "correlaciones")
    ap.add_argument("--ventana", type=int, default=252, help="Sesiones para la matriz de correlación actual")
    ap.add_argument("--ventana-regimen", type=int, default=63, help="Sesiones de la ventana móvil del régimen")
    ap.add_argument("--paso", type=int, default=5, help="Cada cuántas sesiones se recalcula el régimen")
    ap.add_argument("--n-clusters", type=int, default=0, help="Nº de clusters (0 = automático por silueta)")
    ap.add_argument("--cartera", default=None,
                    help='Tickers y pesos: "AAPL:20,MSFT:20,NVDA:15". Por defecto, PORTAFOLIO de config.py')
    ap.add_argument("--umbral-z", type=float, default=2.5, help="Z del residuo para marcar un desacople")
    ap.add_argument("--hrp", action="store_true", help="Backtest HRP vs equiponderado (≈1 min)")
    ap.add_argument("--pares", action="store_true", help="Buscar pares cointegrados por industria")
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

    adj = paneles["adj_close"]
    r = np.log(adj).diff()
    r = r.where(r.abs() < 0.6)                      # descarta errores de datos groseros
    sectores = info.set_index("symbol")["sector"]
    caps = fund["market_cap"].reindex(adj.columns).fillna(0.0)
    w_cap = caps / caps.sum() if caps.sum() > 0 else pd.Series(1 / len(adj.columns), index=adj.columns)
    mercado = (r.fillna(0.0) * w_cap).sum(axis=1)   # proxy del índice (capitalizaciones de hoy)
    informe = [f"# Correlaciones — S&P 500 ({ficha['primera_fecha']} → {ficha['ultima_fecha']})", ""]

    # ---- A. régimen ----------------------------------------------------------
    reg = serie_regimen(r, args.ventana_regimen, args.paso)
    reg.to_csv(carpeta / "regimen_correlacion.csv")
    ult = reg.iloc[-1]
    pct = {c: 100 * (reg[c] <= ult[c]).mean() for c in ("corr_media", "absorcion_pc1", "dispersion_%")}
    titulo("A. RÉGIMEN DE CORRELACIÓN (ventana móvil)")
    print(f"Hoy: correlación media {ult['corr_media']:.2f} (percentil {pct['corr_media']:.0f} de la historia) · "
          f"absorption PC1 {ult['absorcion_pc1']:.2f} (p{pct['absorcion_pc1']:.0f}) · "
          f"dispersión {ult['dispersion_%']:.2f}% (p{pct['dispersion_%']:.0f})")
    anual = reg.groupby(reg.index.year)[["corr_media", "absorcion_pc1", "dispersion_%", "vol_mercado_%"]].mean()
    tabla(anual, filas=15, decimales=2)
    peores = reg.nlargest(5, "corr_media")[["corr_media", "absorcion_pc1", "vol_mercado_%"]]
    print("\nSemanas con mayor correlación media (todo cae junto):")
    tabla(peores, decimales=2)
    informe += ["## A. Régimen", "",
                f"Hoy: correlación media **{ult['corr_media']:.2f}** (percentil {pct['corr_media']:.0f}), "
                f"absorption PC1 **{ult['absorcion_pc1']:.2f}** (p{pct['absorcion_pc1']:.0f}), "
                f"dispersión {ult['dispersion_%']:.2f}% (p{pct['dispersion_%']:.0f}).", "",
                anual.round(3).to_markdown(), "", "![](regimen_correlacion.png)", ""]

    # ---- B. clusters ---------------------------------------------------------
    ventana = r.iloc[-args.ventana:]
    cov, corr, cols = cov_shrink(ventana)
    etiquetas, orden, k = clusterizar(corr, args.n_clusters)
    lab = pd.Series(etiquetas, index=cols, name="cluster")
    sect = sectores.reindex(cols).fillna("Desconocido")
    from sklearn.metrics import adjusted_rand_score
    ari = adjusted_rand_score(sect.to_numpy(), lab.to_numpy())
    conting = pd.crosstab(lab, sect)
    C = pd.DataFrame(corr, index=cols, columns=cols)

    filas = []
    for cl in sorted(lab.unique()):
        miembros = list(lab.index[lab == cl])
        sub = C.loc[miembros, miembros].to_numpy()
        n = len(miembros)
        intra = (sub.sum() - n) / (n * (n - 1)) if n > 1 else np.nan
        dom = sect[miembros].value_counts()
        top = caps.reindex(miembros).sort_values(ascending=False).index[:6]
        filas.append({"cluster": cl, "n": n, "sector_dominante": dom.index[0],
                      "pureza_%": 100 * dom.iloc[0] / n, "corr_intra": intra,
                      "vol_media_%": 100 * ventana[miembros].std().mean() * np.sqrt(252),
                      "ejemplos": ", ".join(top)})
    resumen_cl = pd.DataFrame(filas).set_index("cluster").sort_values("n", ascending=False)
    dominante = resumen_cl["sector_dominante"]
    detalle = pd.DataFrame({"sector": sect, "cluster": lab})
    detalle["sector_cluster"] = detalle["cluster"].map(dominante)
    detalle["corr_con_su_sector"] = [
        C.loc[t, [u for u in cols if sect[u] == sect[t] and u != t]].mean() if (sect == sect[t]).sum() > 1 else np.nan
        for t in cols]
    detalle["corr_con_su_cluster"] = [
        C.loc[t, [u for u in cols if lab[u] == lab[t] and u != t]].mean() if (lab == lab[t]).sum() > 1 else np.nan
        for t in cols]
    detalle["corr_media_universo"] = (C.sum(axis=1) - 1) / (len(cols) - 1)
    detalle.to_csv(carpeta / "clusters.csv")
    resumen_cl.to_csv(carpeta / "clusters_resumen.csv")
    fuera = detalle[(detalle["sector"] != detalle["sector_cluster"])].copy()
    fuera["dif"] = fuera["corr_con_su_cluster"] - fuera["corr_con_su_sector"]
    fuera = fuera.sort_values("dif", ascending=False)

    sect_corr = pd.DataFrame(index=sorted(sect.unique()), columns=sorted(sect.unique()), dtype=float)
    for a in sect_corr.index:
        ia = [t for t in cols if sect[t] == a]
        for b in sect_corr.columns:
            ib = [t for t in cols if sect[t] == b]
            m = C.loc[ia, ib].to_numpy()
            sect_corr.loc[a, b] = (m.sum() - len(ia)) / (len(ia) * (len(ia) - 1)) if a == b and len(ia) > 1 else m.mean()
    sect_corr.to_csv(carpeta / "correlacion_sectores.csv")

    titulo(f"B. CLUSTERS ESTADÍSTICOS (últimas {args.ventana} sesiones, k={k}, ARI vs sectores = {ari:.2f})")
    print("ARI ≈ 1 significaría que los clusters coinciden con los sectores GICS; 0, que no tienen nada que ver.")
    tabla(resumen_cl, filas=25, decimales=2)
    print("\nValores que cotizan con OTRO grupo (su correlación con el cluster supera a la de su sector):")
    tabla(fuera[["sector", "cluster", "sector_cluster", "corr_con_su_sector", "corr_con_su_cluster"]], filas=15)
    print("\nCorrelación media entre sectores:")
    tabla(sect_corr, filas=12, decimales=2)
    informe += [f"## B. Clusters (k={k}, ARI vs GICS = {ari:.2f})", "", resumen_cl.round(2).to_markdown(), "",
                "Valores que cotizan con otro grupo:", "",
                fuera[["sector", "cluster", "sector_cluster", "corr_con_su_sector", "corr_con_su_cluster"]]
                .head(15).round(2).to_markdown(), "", "![](clusters_vs_sectores.png)", "![](heatmap_sectores.png)", ""]

    # ---- C. cartera ----------------------------------------------------------
    tick_cart, pesos_cart = parsear_cartera(args.cartera)
    cart = None
    if tick_cart:
        cart = analizar_cartera(ventana, cov, corr, cols, tick_cart, pesos_cart,
                                mercado.reindex(ventana.index), info, fund)
        titulo("C. TU CARTERA")
        if "error" in cart:
            print(cart["error"])
        else:
            if cart["ausentes"]:
                print(f"(No están en la base y se ignoran: {', '.join(cart['ausentes'])})")
            print(f"Volatilidad anual {cart['vol_%']:.1f}% · beta {cart['beta']:.2f} frente al índice · "
                  f"correlación media entre posiciones {cart['corr_media_pares']:.2f} (máx. {cart['corr_max_par']:.2f})")
            print(f"Apuestas efectivas: {cart['apuestas_efectivas']:.1f} de {len(cart['presentes'])} posiciones · "
                  f"ratio de diversificación {cart['ratio_diversificacion']:.2f} "
                  f"(1 = como tener un solo activo; cuanto más alto, mejor)")
            tabla(cart["detalle"], decimales=2)
            print("\nValores del índice que MÁS diversificarían tu cartera (menor correlación con el conjunto):")
            tabla(cart["diversifican"], filas=15, decimales=2)
            print("\nValores que serían REDUNDANTES (casi lo mismo que ya tienes):")
            tabla(cart["redundantes"], filas=10, decimales=2)
            cart["detalle"].to_csv(carpeta / "cartera_riesgo.csv")
            cart["diversifican"].to_csv(carpeta / "cartera_diversificadores.csv")
            informe += ["## C. Cartera", "",
                        f"Vol {cart['vol_%']:.1f}% · beta {cart['beta']:.2f} · corr. media {cart['corr_media_pares']:.2f} · "
                        f"apuestas efectivas **{cart['apuestas_efectivas']:.1f}/{len(cart['presentes'])}** · "
                        f"ratio de diversificación {cart['ratio_diversificacion']:.2f}", "",
                        cart["detalle"].round(2).to_markdown(), "", "Diversificadores:", "",
                        cart["diversifican"].round(2).to_markdown(), "", "![](cartera_correlaciones.png)", ""]

    # ---- D. desacoples -------------------------------------------------------
    des = desacoples(r, lab, mercado, umbral=args.umbral_z)
    des.to_csv(carpeta / "desacoples_hoy.csv")
    titulo(f"D. DESACOPLES a {r.index[-1].date()} (residuo frente a mercado + cluster, |z| ≥ {args.umbral_z})")
    alertas = des[des["alerta"] != ""]
    print(f"{len(alertas)} valores con movimiento idiosincrático anómalo (hoy o en los últimos 5 días):")
    tabla(alertas[["cluster", "ret_hoy_%", "explicado_%", "residuo_%", "z_hoy", "z_max_5d", "R2", "alerta"]],
          filas=25, decimales=2)
    informe += ["## D. Desacoples", "",
                alertas[["cluster", "ret_hoy_%", "explicado_%", "residuo_%", "z_hoy", "z_max_5d", "alerta"]]
                .head(25).round(2).to_markdown(), ""]

    # ---- E. HRP ----------------------------------------------------------------
    hrp_res = None
    if args.hrp:
        titulo("E. HRP vs EQUIPONDERADO vs INVERSA DE VOLATILIDAD (universo completo, mensual)")
        hrp_res, hrp_m = backtest_hrp(r, args.ventana)
        tabla(hrp_m, decimales=2)
        hrp_res.to_csv(carpeta / "hrp_backtest.csv")
        informe += ["## E. HRP", "", hrp_m.round(2).to_markdown(), "", "![](hrp_vs_equiponderado.png)", ""]

    # ---- F. pares --------------------------------------------------------------
    if args.pares:
        titulo("F. PARES COINTEGRADOS DENTRO DE LA MISMA INDUSTRIA (Engle-Granger, 2 años)")
        pares = buscar_pares(adj, info)
        if pares.empty:
            print("No se encontraron pares que pasen los filtros.")
        else:
            pares.to_csv(carpeta / "pares_cointegrados.csv")
            print(f"{len(pares)} pares con p < 0.05 y half-life entre 2 y 60 días. Los de |z| ≥ 2 tienen señal ahora:")
            tabla(pares, filas=20, decimales=3)
            informe += ["## F. Pares", "", pares.head(20).round(3).to_markdown(), ""]

    if not args.sin_graficos:
        graficos(reg, sect_corr, conting, cart, hrp_res, carpeta)
    informe.append("_Herramienta informativa; no es asesoramiento financiero._")
    (carpeta / "informe.md").write_text("\n".join(informe), encoding="utf-8")
    print(f"\nArchivos guardados en {carpeta}/")


if __name__ == "__main__":
    main()
