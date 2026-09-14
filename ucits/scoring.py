"""Scoring best-in-class: calidad cuantitativa + encaje de régimen."""
from __future__ import annotations

from statistics import mean

CLASES = ("monetario", "renta_fija", "mixto", "renta_variable", "oro")


def _pct_rank(valores: list[float | None], valor: float | None, invertido: bool = False) -> float:
    nums = [v for v in valores if v is not None]
    if valor is None or not nums:
        return 50.0
    if invertido:
        nums = [-x for x in nums]
        valor = -valor
    menor = sum(1 for v in nums if v < valor)
    iguales = sum(1 for v in nums if v == valor)
    return round(100.0 * (menor + 0.5 * iguales) / len(nums), 1)


def puntuar(fondos: list[dict]) -> list[dict]:
    """Añade score_calidad, score_coste, ranks por clase y tema."""
    by_clase: dict[str, list[dict]] = {c: [] for c in CLASES}
    for f in fondos:
        by_clase.setdefault(f["clase_activo"], []).append(f)

    for grupo in by_clase.values():
        sharpes = [g.get("sharpe_3y") for g in grupo]
        sortinos = [g.get("sortino_3y") for g in grupo]
        r3 = [g.get("ret_3y") for g in grupo]
        r1 = [g.get("ret_1y") for g in grupo]
        dds = [g.get("max_dd") for g in grupo]
        ters = [g.get("ter") for g in grupo]
        calmars = [g.get("calmar_3y") for g in grupo]
        bics = [float(g.get("bic", 3)) for g in grupo]

        for g in grupo:
            p_sh = _pct_rank(sharpes, g.get("sharpe_3y"))
            p_so = _pct_rank(sortinos, g.get("sortino_3y"))
            p_r3 = _pct_rank(r3, g.get("ret_3y"))
            p_r1 = _pct_rank(r1, g.get("ret_1y"))
            p_dd = _pct_rank(dds, g.get("max_dd"))  # menos negativo = mejor → no invertido porque -8 > -40
            p_ter = _pct_rank(ters, g.get("ter"), invertido=True)
            p_ca = _pct_rank(calmars, g.get("calmar_3y"))
            p_bic = _pct_rank(bics, float(g.get("bic", 3)))

            # Confianza: si es estimado, bajamos el peso de returns y subimos TER + BIC.
            est = g.get("fuente") != "nav_vivo"
            if est:
                calidad = (
                    0.14 * p_sh + 0.08 * p_so + 0.10 * p_r3 + 0.06 * p_r1
                    + 0.12 * p_dd + 0.22 * p_ter + 0.08 * p_ca + 0.20 * p_bic
                )
            else:
                calidad = (
                    0.22 * p_sh + 0.12 * p_so + 0.16 * p_r3 + 0.08 * p_r1
                    + 0.14 * p_dd + 0.14 * p_ter + 0.08 * p_ca + 0.06 * p_bic
                )
            g["score_calidad"] = round(calidad, 1)
            g["score_coste"] = round(p_ter, 1)
            g["p_sharpe"] = p_sh
            g["p_ter"] = p_ter
            g["p_dd"] = p_dd

    # Rank global y por tema
    fondos_ord = sorted(fondos, key=lambda x: x.get("score_calidad", 0), reverse=True)
    for i, f in enumerate(fondos_ord, 1):
        f["rank_global"] = i

    temas: dict[str, list[dict]] = {}
    for f in fondos:
        temas.setdefault(f["tema"], []).append(f)
    for lista in temas.values():
        lista.sort(key=lambda x: x.get("score_calidad", 0), reverse=True)
        for i, f in enumerate(lista, 1):
            f["rank_tema"] = i
            f["n_tema"] = len(lista)

    clases: dict[str, list[dict]] = {}
    for f in fondos:
        clases.setdefault(f["clase_activo"], []).append(f)
    for lista in clases.values():
        lista.sort(key=lambda x: x.get("score_calidad", 0), reverse=True)
        for i, f in enumerate(lista, 1):
            f["rank_clase"] = i
            f["n_clase"] = len(lista)
            f["best_in_class"] = i == 1
            f["podio"] = i <= 2
    return fondos


def aplicar_regimen(fondos: list[dict], regimen: dict) -> list[dict]:
    pesos_tema = regimen.get("pesos_tema", {})
    pesos_clase = regimen.get("pesos_clase", {})
    for f in fondos:
        fit_t = float(pesos_tema.get(f["tema"], 0.25))
        fit_c = float(pesos_clase.get(f["clase_activo"], 0.25))
        fit = 100 * (0.65 * fit_t + 0.35 * fit_c)
        f["score_regimen"] = round(fit, 1)
        calidad = f.get("score_calidad") or 50
        f["score_final"] = round(0.68 * calidad + 0.32 * fit, 1)
    return fondos


def best_in_class(fondos: list[dict], por: str = "tema", top: int = 2) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = {}
    for f in fondos:
        grupos.setdefault(f[por], []).append(f)
    out = {}
    for k, lista in grupos.items():
        lista = sorted(lista, key=lambda x: x.get("score_calidad", 0), reverse=True)
        out[k] = lista[:top]
    return out


def resumen_universo(fondos: list[dict]) -> dict:
    vivos = sum(1 for f in fondos if f.get("fuente") == "nav_vivo")
    return {
        "n": len(fondos),
        "con_nav_vivo": vivos,
        "ter_medio": round(mean(f["ter"] for f in fondos), 2),
        "temas": len({f["tema"] for f in fondos}),
        "gestoras": len({f["gestora"] for f in fondos}),
        "clases": {c: sum(1 for f in fondos if f["clase_activo"] == c) for c in CLASES},
    }
