"""Constructor de portafolio por modo de mercado y perfil de riesgo.

Método: asignación jerárquica (sleeves de clase → 1-3 best-in-class por sleeve)
con techo de concentración, sesgo a TER bajo y encaje de régimen.
No es Markowitz clásico (inestable con 5 años de VL y TER distintos).
"""
from __future__ import annotations

from copy import deepcopy

# Pesos objetivo de clase por MODO × PERFIL
# perfil: conservador / equilibrado / agresivo
MODOS = {
    "automatico": None,  # se rellena con el régimen
    "defensivo": {
        "nombre": "Defensivo",
        "idea": "Preservar y cobrar cupón. Para recesión, necesidad de liquidez o capital a 1-3 años.",
        "sleeves": {
            "conservador": {"monetario": 50, "renta_fija": 35, "mixto": 10, "oro": 5, "renta_variable": 0},
            "equilibrado": {"monetario": 35, "renta_fija": 35, "mixto": 15, "oro": 10, "renta_variable": 5},
            "agresivo": {"monetario": 20, "renta_fija": 35, "mixto": 20, "oro": 10, "renta_variable": 15},
        },
    },
    "equilibrado": {
        "nombre": "Equilibrado permanente",
        "idea": "Core multi-activo para no tener que adivinar el ciclo. Rebalanceo anual.",
        "sleeves": {
            "conservador": {"monetario": 15, "renta_fija": 40, "mixto": 15, "oro": 10, "renta_variable": 20},
            "equilibrado": {"monetario": 8, "renta_fija": 27, "mixto": 12, "oro": 10, "renta_variable": 43},
            "agresivo": {"monetario": 5, "renta_fija": 15, "mixto": 10, "oro": 10, "renta_variable": 60},
        },
    },
    "crecimiento": {
        "nombre": "Crecimiento",
        "idea": "Horizonte >7 años. La RV de calidad + un colchón de oro y monetario.",
        "sleeves": {
            "conservador": {"monetario": 10, "renta_fija": 20, "mixto": 10, "oro": 10, "renta_variable": 50},
            "equilibrado": {"monetario": 5, "renta_fija": 10, "mixto": 8, "oro": 10, "renta_variable": 67},
            "agresivo": {"monetario": 3, "renta_fija": 5, "mixto": 5, "oro": 7, "renta_variable": 80},
        },
    },
    "anti_inflacion": {
        "nombre": "Anti-inflación",
        "idea": "Linkers, oro/minas, infra, calidad con pricing power. Duration corta.",
        "sleeves": {
            "conservador": {"monetario": 20, "renta_fija": 30, "mixto": 10, "oro": 20, "renta_variable": 20},
            "equilibrado": {"monetario": 10, "renta_fija": 25, "mixto": 10, "oro": 25, "renta_variable": 30},
            "agresivo": {"monetario": 5, "renta_fija": 15, "mixto": 8, "oro": 30, "renta_variable": 42},
        },
    },
    "recesion": {
        "nombre": "Recesión",
        "idea": "Cash + govies + crédito corto + un seguro de oro. RV solo calidad defensiva.",
        "sleeves": {
            "conservador": {"monetario": 45, "renta_fija": 40, "mixto": 5, "oro": 10, "renta_variable": 0},
            "equilibrado": {"monetario": 30, "renta_fija": 40, "mixto": 10, "oro": 12, "renta_variable": 8},
            "agresivo": {"monetario": 20, "renta_fija": 35, "mixto": 12, "oro": 13, "renta_variable": 20},
        },
    },
    "expansion": {
        "nombre": "Expansión",
        "idea": "Beta de calidad + satélites tech/EM. El efectivo es un coste de oportunidad.",
        "sleeves": {
            "conservador": {"monetario": 10, "renta_fija": 25, "mixto": 10, "oro": 8, "renta_variable": 47},
            "equilibrado": {"monetario": 5, "renta_fija": 12, "mixto": 8, "oro": 8, "renta_variable": 67},
            "agresivo": {"monetario": 2, "renta_fija": 5, "mixto": 5, "oro": 8, "renta_variable": 80},
        },
    },
    "stagflation": {
        "nombre": "Estanflación",
        "idea": "Oro, minas, linkers, infra. Castiga growth caro y duration larga.",
        "sleeves": {
            "conservador": {"monetario": 25, "renta_fija": 25, "mixto": 10, "oro": 25, "renta_variable": 15},
            "equilibrado": {"monetario": 12, "renta_fija": 20, "mixto": 10, "oro": 30, "renta_variable": 28},
            "agresivo": {"monetario": 5, "renta_fija": 12, "mixto": 8, "oro": 35, "renta_variable": 40},
        },
    },
}

MAPA_REGIMEN_MODO = {
    "expansion": "expansion",
    "expansion_selectiva": "expansion",
    "late_cycle": "anti_inflacion",
    "recesion": "recesion",
    "stagflation": "stagflation",
    "recovery": "expansion",
}

# Preferencias extra de tema dentro de cada modo (bonus al ranking)
BONUS_TEMA = {
    "defensivo": {"liquidez": 2, "corto_flexible": 2, "euro_gobierno": 1.5, "credito_corto": 1.5, "prudente": 1},
    "equilibrado": {"calidad_global": 1.5, "strategic_income": 1.2, "oro_mineras": 1, "global_core": 1.3},
    "crecimiento": {"calidad_global": 1.6, "growth_global": 1.3, "tecnologia": 1.1, "global_core": 1.4},
    "anti_inflacion": {"oro_mineras": 2, "linkers": 2, "infraestructuras": 1.8, "mineria": 1.4, "calidad_global": 1.2},
    "recesion": {"liquidez": 2, "euro_gobierno": 2, "corto_flexible": 1.8, "salud": 1.2, "calidad_global": 1.3},
    "expansion": {"tecnologia": 1.4, "growth_global": 1.3, "emergentes": 1.2, "calidad_global": 1.2, "india": 1.1},
    "stagflation": {"oro_mineras": 2, "mineria": 1.6, "linkers": 1.8, "infraestructuras": 1.5, "value_oro": 1.4},
}

MAX_FONDO = {
    "conservador": 18,
    "equilibrado": 22,
    "agresivo": 28,
}


def _score_pick(f: dict, modo: str) -> float:
    base = float(f.get("score_final") or f.get("score_calidad") or 50)
    bonus = BONUS_TEMA.get(modo, {}).get(f["tema"], 1.0)
    # Premia TER bajo y BIC
    coste = max(0, 2.2 - float(f["ter"])) * 4
    return base * bonus + coste + 3 * float(f.get("bic", 3))


def _elige(candidatos: list[dict], n: int, modo: str, ya: set[str]) -> list[dict]:
    pool = [c for c in candidatos if c["id"] not in ya]
    pool.sort(key=lambda x: _score_pick(x, modo), reverse=True)
    # Diversificar gestora y tema
    chosen = []
    gestoras = set()
    temas = set()
    for f in pool:
        if len(chosen) >= n:
            break
        if f["gestora"] in gestoras and len(pool) > n + 2:
            # permitir segunda de misma gestora solo si no hay alternativa
            continue
        if f["tema"] in temas and n > 1 and len(chosen) < n:
            # preferir otro tema
            alt = next((x for x in pool if x["tema"] not in temas and x["id"] not in ya
                        and x["id"] not in {c["id"] for c in chosen}), None)
            if alt:
                f = alt
        chosen.append(f)
        gestoras.add(f["gestora"])
        temas.add(f["tema"])
        ya.add(f["id"])
    if len(chosen) < n:
        for f in pool:
            if f["id"] in {c["id"] for c in chosen}:
                continue
            chosen.append(f)
            ya.add(f["id"])
            if len(chosen) >= n:
                break
    return chosen


def _n_por_sleeve(peso: float, perfil: str) -> int:
    if peso <= 0:
        return 0
    if peso < 10:
        return 1
    if peso < 22:
        return 2 if perfil != "conservador" else 1
    if peso < 45:
        return 3 if perfil != "conservador" else 2
    return 4 if perfil == "agresivo" else 3


def construir(fondos: list[dict], modo: str, perfil: str, regimen: dict) -> dict:
    if perfil not in ("conservador", "equilibrado", "agresivo"):
        perfil = "equilibrado"
    modo_real = modo
    if modo == "automatico" or modo not in MODOS or MODOS[modo] is None:
        modo_real = MAPA_REGIMEN_MODO.get(regimen.get("id"), "equilibrado")
    meta = MODOS[modo_real]
    sleeves = meta["sleeves"][perfil]
    techo = MAX_FONDO[perfil]
    ya: set[str] = set()
    posiciones = []

    for clase, peso_clase in sleeves.items():
        if peso_clase <= 0:
            continue
        n = _n_por_sleeve(peso_clase, perfil)
        cand = [f for f in fondos if f["clase_activo"] == clase]
        if modo_real == "anti_inflacion" and clase == "renta_fija":
            cand = [f for f in cand if f["tema"] in
                    ("linkers", "credito_corto", "corto_flexible", "income_corto", "strategic_income")] or cand
        if modo_real == "recesion" and clase == "renta_fija":
            cand = [f for f in cand if f["tema"] in
                    ("euro_gobierno", "corto_flexible", "credito_corto", "calidad_credito")] or cand
        if modo_real == "expansion" and clase == "renta_variable":
            # mezcla core calidad + satélites
            pass
        picks = _elige(cand, n, modo_real, ya)
        if not picks:
            continue
        # Inverse-vol con suelo y techo por fondo (sin reventar el cap al renormalizar)
        vols = [max(0.35, float(p.get("vol_3y") or p.get("vol_1y") or 8)) for p in picks]
        inv = [1 / v for v in vols]
        s = sum(inv) or 1
        brutos = [peso_clase * (x / s) for x in inv]
        brutos = [min(techo, b) for b in brutos]
        extra = peso_clase - sum(brutos)
        i = 0
        guard = 0
        while extra > 0.05 and guard < 40:
            hueco = techo - brutos[i % len(brutos)]
            if hueco > 0.01:
                add = min(hueco, extra)
                brutos[i % len(brutos)] += add
                extra -= add
            i += 1
            guard += 1
        for p, w in zip(picks, brutos):
            item = deepcopy(p)
            item["peso"] = round(w, 2)
            item["sleeve"] = clase
            posiciones.append(item)

    # renormalizar a 100
    tot = sum(p["peso"] for p in posiciones) or 1
    for p in posiciones:
        p["peso"] = round(100.0 * p["peso"] / tot, 2)
    drift = round(100 - sum(p["peso"] for p in posiciones), 2)
    if posiciones:
        posiciones[0]["peso"] = round(posiciones[0]["peso"] + drift, 2)
    posiciones.sort(key=lambda x: -x["peso"])

    ter_pond = sum(p["peso"] * p["ter"] for p in posiciones) / 100
    sharpe_pond = _pond(posiciones, "sharpe_3y")
    ret1_pond = _pond(posiciones, "ret_1y")
    ret3_pond = _pond(posiciones, "ret_3y")
    dd_pond = _pond(posiciones, "max_dd")  # aproximación (no es DD de cartera)
    vol_pond = _pond(posiciones, "vol_3y") or _pond(posiciones, "vol_1y")

    return {
        "modo": modo_real,
        "modo_solicitado": modo,
        "perfil": perfil,
        "nombre": meta["nombre"],
        "idea": meta["idea"],
        "regimen": {"id": regimen.get("id"), "nombre": regimen.get("nombre")},
        "sleeves": sleeves,
        "posiciones": _slim(posiciones),
        "n": len(posiciones),
        "ter_ponderado": round(ter_pond, 2),
        "sharpe_3y_pond": sharpe_pond,
        "ret_1y_pond": ret1_pond,
        "ret_3y_pond": ret3_pond,
        "max_dd_aprox": dd_pond,
        "vol_3y_pond": vol_pond,
        "aviso_dd": "El max drawdown ponderado NO es el DD de la cartera (la correlación lo cambia).",
        "disclaimer": "Informativo. No es asesoramiento ni recomendación de compra.",
    }


def _pond(pos, campo):
    num = den = 0.0
    for p in pos:
        v = p.get(campo)
        if v is None:
            continue
        num += p["peso"] * float(v)
        den += p["peso"]
    return round(num / den, 2) if den else None


def _slim(posiciones: list[dict]) -> list[dict]:
    keep = ("id", "nombre", "isin", "gestora", "clase_activo", "tema", "estilo",
            "divisa", "ter", "peso", "sleeve", "score_calidad", "score_final",
            "sharpe_3y", "ret_1y", "ret_3y", "max_dd", "vol_3y", "fuente",
            "best_in_class", "rank_tema", "bic", "tesis")
    return [{k: p.get(k) for k in keep} for p in posiciones]


def catalogo_modos() -> list[dict]:
    out = [{"id": "automatico", "nombre": "Automático (según régimen)",
            "idea": "Elige el modo que mejor encaja con el régimen detectado hoy."}]
    for k, v in MODOS.items():
        if k == "automatico":
            continue
        out.append({"id": k, "nombre": v["nombre"], "idea": v["idea"]})
    return out
