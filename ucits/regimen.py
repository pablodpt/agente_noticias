"""Detección de régimen de mercado para rotar el portafolio."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Snapshot de mercado ~11-14 sep 2026 (fuentes públicas: CBOE VIX, FRED TIPS).
# Se sustituye si Yahoo responde.
SNAPSHOT_MERCADO = {
    "fecha": "2026-09-11",
    "vix": 15.84,
    "tips_10y": 2.55,
    "mm_eur_1y": 2.12,
    "rv_global_1y": 15.48,
    "rv_value_1y": 22.88,
    "rv_growth_1y": 8.33,
    "tech_1y": 44.79,
    "em_1y": 33.94,
    "oro_mineras_1y": 42.0,
    "rf_euro_corp_1y": -0.87,
    "fuente": "snapshot_publico",
}

# Encaje 0-1 de cada tema/clase por régimen.
REGIMENES = {
    "expansion": {
        "nombre": "Expansión / Risk-on",
        "color": "#3ee0b2",
        "resumen": "Volatilidad baja, beta funciona. Prioriza RV growth/tech, HY y emergentes.",
        "pesos_clase": {"renta_variable": 1.0, "renta_fija": 0.35, "mixto": 0.45, "monetario": 0.15, "oro": 0.25},
        "pesos_tema": {
            "tecnologia": 1.0, "inteligencia_artificial": 0.95, "robotica_ia": 0.9, "growth_global": 0.9,
            "growth_usa": 0.85, "growth_visionario": 0.8, "disrupcion": 0.75, "emergentes": 0.85,
            "asia": 0.8, "india": 0.85, "high_yield": 0.7, "consumo": 0.7, "small_usa": 0.75,
            "liquidez": 0.15, "euro_gobierno": 0.2, "oro_mineras": 0.35,
        },
    },
    "expansion_selectiva": {
        "nombre": "Expansión selectiva",
        "color": "#7dd3c7",
        "resumen": "Risk-on con rotación: value, EM, Japón y oro ganan a growth clásico. Tech sigue vivo.",
        "pesos_clase": {"renta_variable": 0.9, "oro": 0.7, "renta_fija": 0.4, "mixto": 0.55, "monetario": 0.25},
        "pesos_tema": {
            "value_global": 1.0, "value_europa": 0.95, "emergentes": 0.9, "asia": 0.9, "india": 0.85,
            "japon": 0.85, "tecnologia": 0.8, "oro_mineras": 0.85, "mineria": 0.75, "calidad_global": 0.7,
            "dividendo_global": 0.65, "high_yield": 0.55, "euro_gobierno": 0.25, "liquidez": 0.25,
            "inteligencia_artificial": 0.7, "china_consumo": 0.6,
        },
    },
    "late_cycle": {
        "nombre": "Final de ciclo / Inflación residual",
        "color": "#e0b43e",
        "resumen": "Calidad, infra, dividendo, crédito corto y oro. Reduce duration y small caps.",
        "pesos_clase": {"renta_variable": 0.55, "renta_fija": 0.6, "oro": 0.85, "mixto": 0.7, "monetario": 0.5},
        "pesos_tema": {
            "calidad_global": 0.95, "calidad_europa": 0.9, "infraestructuras": 0.9, "dividendo_global": 0.85,
            "oro_mineras": 0.9, "linkers": 0.85, "credito_corto": 0.8, "income_corto": 0.8,
            "corto_flexible": 0.85, "salud": 0.75, "agua": 0.7, "strategic_income": 0.65,
            "growth_visionario": 0.25, "small_usa": 0.2, "high_yield": 0.35,
        },
    },
    "recesion": {
        "nombre": "Recesión / Risk-off",
        "color": "#ff5c7a",
        "resumen": "Preservar. Monetarios, gobierno euro, crédito corto, calidad defensiva y algo de oro.",
        "pesos_clase": {"monetario": 1.0, "renta_fija": 0.85, "oro": 0.7, "mixto": 0.5, "renta_variable": 0.25},
        "pesos_tema": {
            "liquidez": 1.0, "euro_gobierno": 0.95, "corto_flexible": 0.9, "credito_corto": 0.8,
            "calidad_global": 0.7, "salud": 0.65, "oro_mineras": 0.6, "prudente": 0.75,
            "absoluto": 0.7, "value_oro": 0.7, "tecnologia": 0.15, "high_yield": 0.1,
            "emergentes": 0.15, "growth_visionario": 0.1,
        },
    },
    "stagflation": {
        "nombre": "Estanflación",
        "color": "#d4a017",
        "resumen": "Oro, minas, linkers, infra, calidad de pricing power. Evita duration larga y growth caro.",
        "pesos_clase": {"oro": 1.0, "renta_fija": 0.55, "renta_variable": 0.4, "mixto": 0.55, "monetario": 0.6},
        "pesos_tema": {
            "oro_mineras": 1.0, "mineria": 0.9, "linkers": 0.9, "infraestructuras": 0.8,
            "value_global": 0.7, "value_oro": 0.85, "clima": 0.5, "calidad_global": 0.6,
            "liquidez": 0.55, "growth_usa": 0.15, "growth_visionario": 0.1, "euro_gobierno": 0.25,
        },
    },
    "recovery": {
        "nombre": "Recuperación / Early-cycle",
        "color": "#6ea8ff",
        "resumen": "Value, small caps, HY, emergentes y cíclicos. Duration media, menos efectivo.",
        "pesos_clase": {"renta_variable": 0.95, "renta_fija": 0.55, "mixto": 0.5, "oro": 0.3, "monetario": 0.2},
        "pesos_tema": {
            "value_europa": 1.0, "value_global": 0.95, "small_usa": 0.9, "small_global": 0.85,
            "high_yield": 0.85, "emergentes": 0.8, "japon": 0.75, "mineria": 0.6,
            "liquidez": 0.2, "oro_mineras": 0.35, "salud": 0.4,
        },
    },
}


def clasificar(indicadores: dict) -> str:
    vix = indicadores.get("vix") or 18
    value_vs_growth = (indicadores.get("rv_value_1y") or 0) - (indicadores.get("rv_growth_1y") or 0)
    oro = indicadores.get("oro_mineras_1y") or 0
    em = indicadores.get("em_1y") or 0
    corp = indicadores.get("rf_euro_corp_1y") or 0
    tips = indicadores.get("tips_10y") or 1.8

    if vix >= 28:
        return "recesion"
    if oro >= 25 and tips >= 2.2 and value_vs_growth >= 8 and vix >= 20:
        return "stagflation"
    if vix <= 18 and value_vs_growth >= 6 and em >= 20:
        return "expansion_selectiva"
    if vix <= 16 and (indicadores.get("tech_1y") or 0) >= 20 and value_vs_growth < 4:
        return "expansion"
    if corp < 0 and oro >= 15 and vix < 22:
        return "late_cycle"
    if vix < 22 and value_vs_growth >= 4:
        return "recovery"
    return "expansion_selectiva"


def detectar(nav_indices: dict | None = None) -> dict:
    ind = dict(SNAPSHOT_MERCADO)
    if nav_indices:
        ind.update({k: v for k, v in nav_indices.items() if v is not None})
        ind["fuente"] = "mixto_vivo" if nav_indices else ind["fuente"]
    clave = clasificar(ind)
    base = REGIMENES[clave]
    # default tema weights
    pesos_tema = {**{t: 0.35 for t in _temas_conocidos()}, **base["pesos_tema"]}
    return {
        "id": clave,
        "nombre": base["nombre"],
        "color": base["color"],
        "resumen": base["resumen"],
        "pesos_clase": base["pesos_clase"],
        "pesos_tema": pesos_tema,
        "indicadores": ind,
        "actualizado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "alternativas": [
            {"id": k, "nombre": v["nombre"], "color": v["color"]}
            for k, v in REGIMENES.items()
        ],
    }


def _temas_conocidos() -> list[str]:
    from .fondos import universo
    return sorted({f["tema"] for f in universo()})


def regimen_por_id(clave: str) -> dict:
    if clave not in REGIMENES:
        clave = "expansion_selectiva"
    base = REGIMENES[clave]
    pesos_tema = {**{t: 0.35 for t in _temas_conocidos()}, **base["pesos_tema"]}
    return {
        "id": clave,
        "nombre": base["nombre"],
        "color": base["color"],
        "resumen": base["resumen"],
        "pesos_clase": base["pesos_clase"],
        "pesos_tema": pesos_tema,
        "indicadores": SNAPSHOT_MERCADO,
        "actualizado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "alternativas": [
            {"id": k, "nombre": v["nombre"], "color": v["color"]}
            for k, v in REGIMENES.items()
        ],
    }
