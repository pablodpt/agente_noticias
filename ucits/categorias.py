"""Medias de categoría (Europa, ~septiembre 2026).

Fuente principal: rankings públicos de fondos UCITS / Finect.
Se usan como ancla de comparación (exceso vs. categoría) y como
fallback si no hay histórico de VL. No son una predicción.
"""

# r1y, r5y: %  | ter_tipico: %  | vol_tipica: % anual
CATEGORIAS = {
    "monetario_eur": {
        "nombre": "Mercado monetario EUR",
        "r1y": 2.12, "r5y": 2.07, "ter_tipico": 0.22, "vol_tipica": 0.30,
        "max_dd_tipico": -0.15, "rf": 2.00,
    },
    "monetario_usd": {
        "nombre": "Mercado monetario USD",
        "r1y": 4.49, "r5y": 3.80, "ter_tipico": 0.25, "vol_tipica": 0.40,
        "max_dd_tipico": -0.20, "rf": 4.20,
    },
    "rf_euro_gov": {
        "nombre": "RF gobierno EUR",
        "r1y": -0.40, "r5y": -1.20, "ter_tipico": 0.70, "vol_tipica": 5.5,
        "max_dd_tipico": -16.0, "rf": 2.00,
    },
    "rf_euro_corp": {
        "nombre": "RF corporativa EUR",
        "r1y": -0.87, "r5y": -0.36, "ter_tipico": 0.85, "vol_tipica": 4.8,
        "max_dd_tipico": -14.0, "rf": 2.00,
    },
    "rf_corto_eur": {
        "nombre": "RF corto / ultra-corto EUR",
        "r1y": 1.79, "r5y": 1.89, "ter_tipico": 0.45, "vol_tipica": 1.2,
        "max_dd_tipico": -2.5, "rf": 2.00,
    },
    "rf_flexible": {
        "nombre": "RF flexible / unconstrained",
        "r1y": 2.77, "r5y": 2.35, "ter_tipico": 1.20, "vol_tipica": 4.2,
        "max_dd_tipico": -8.5, "rf": 2.00,
    },
    "rf_hy": {
        "nombre": "RF high yield",
        "r1y": 6.20, "r5y": 3.40, "ter_tipico": 1.40, "vol_tipica": 6.5,
        "max_dd_tipico": -14.0, "rf": 2.00,
    },
    "rf_em": {
        "nombre": "RF emergentes",
        "r1y": 6.59, "r5y": 2.80, "ter_tipico": 1.35, "vol_tipica": 7.5,
        "max_dd_tipico": -18.0, "rf": 2.00,
    },
    "rf_inflation": {
        "nombre": "RF ligada a inflación",
        "r1y": 1.80, "r5y": 1.10, "ter_tipico": 0.70, "vol_tipica": 6.0,
        "max_dd_tipico": -12.0, "rf": 2.00,
    },
    "mixto_mod": {
        "nombre": "Mixtos moderados EUR",
        "r1y": 8.22, "r5y": 2.91, "ter_tipico": 1.50, "vol_tipica": 8.0,
        "max_dd_tipico": -18.0, "rf": 2.00,
    },
    "mixto_flex": {
        "nombre": "Mixtos flexibles EUR",
        "r1y": 10.26, "r5y": 3.84, "ter_tipico": 1.60, "vol_tipica": 9.5,
        "max_dd_tipico": -20.0, "rf": 2.00,
    },
    "rv_global_blend": {
        "nombre": "RV global large blend",
        "r1y": 15.48, "r5y": 8.11, "ter_tipico": 1.50, "vol_tipica": 14.5,
        "max_dd_tipico": -28.0, "rf": 2.00,
    },
    "rv_global_growth": {
        "nombre": "RV global large growth",
        "r1y": 8.33, "r5y": 3.90, "ter_tipico": 1.70, "vol_tipica": 17.5,
        "max_dd_tipico": -38.0, "rf": 2.00,
    },
    "rv_global_value": {
        "nombre": "RV global large value",
        "r1y": 22.88, "r5y": 10.76, "ter_tipico": 1.55, "vol_tipica": 14.0,
        "max_dd_tipico": -26.0, "rf": 2.00,
    },
    "rv_global_div": {
        "nombre": "RV global alto dividendo",
        "r1y": 16.48, "r5y": 9.32, "ter_tipico": 1.50, "vol_tipica": 13.0,
        "max_dd_tipico": -24.0, "rf": 2.00,
    },
    "rv_usa_blend": {
        "nombre": "RV USA large blend",
        "r1y": 15.75, "r5y": 10.99, "ter_tipico": 1.45, "vol_tipica": 16.0,
        "max_dd_tipico": -30.0, "rf": 2.00,
    },
    "rv_usa_growth": {
        "nombre": "RV USA large growth",
        "r1y": 8.14, "r5y": 7.02, "ter_tipico": 1.70, "vol_tipica": 19.0,
        "max_dd_tipico": -40.0, "rf": 2.00,
    },
    "rv_europa": {
        "nombre": "RV Europa large blend",
        "r1y": 15.68, "r5y": 7.47, "ter_tipico": 1.50, "vol_tipica": 15.0,
        "max_dd_tipico": -28.0, "rf": 2.00,
    },
    "rv_euro": {
        "nombre": "RV zona euro large",
        "r1y": 17.08, "r5y": 7.85, "ter_tipico": 1.45, "vol_tipica": 16.0,
        "max_dd_tipico": -30.0, "rf": 2.00,
    },
    "rv_japon": {
        "nombre": "RV Japón",
        "r1y": 26.92, "r5y": 8.63, "ter_tipico": 1.55, "vol_tipica": 16.5,
        "max_dd_tipico": -28.0, "rf": 2.00,
    },
    "rv_em": {
        "nombre": "RV emergentes",
        "r1y": 33.94, "r5y": 7.37, "ter_tipico": 1.70, "vol_tipica": 18.0,
        "max_dd_tipico": -35.0, "rf": 2.00,
    },
    "rv_asia": {
        "nombre": "RV Asia ex-Japón",
        "r1y": 37.61, "r5y": 6.91, "ter_tipico": 1.75, "vol_tipica": 18.5,
        "max_dd_tipico": -36.0, "rf": 2.00,
    },
    "rv_tech": {
        "nombre": "RV sector tecnología",
        "r1y": 44.79, "r5y": 13.93, "ter_tipico": 1.80, "vol_tipica": 22.0,
        "max_dd_tipico": -42.0, "rf": 2.00,
    },
    "rv_salud": {
        "nombre": "RV sector salud",
        "r1y": 13.30, "r5y": 0.94, "ter_tipico": 1.75, "vol_tipica": 16.0,
        "max_dd_tipico": -28.0, "rf": 2.00,
    },
    "rv_clima": {
        "nombre": "RV clima / medioambiente",
        "r1y": 12.50, "r5y": 4.20, "ter_tipico": 1.80, "vol_tipica": 17.0,
        "max_dd_tipico": -32.0, "rf": 2.00,
    },
    "rv_infra": {
        "nombre": "RV infraestructuras cotizadas",
        "r1y": 14.00, "r5y": 6.50, "ter_tipico": 1.60, "vol_tipica": 13.5,
        "max_dd_tipico": -22.0, "rf": 2.00,
    },
    "rv_small_eu": {
        "nombre": "RV Europa small cap",
        "r1y": 14.50, "r5y": 5.80, "ter_tipico": 1.70, "vol_tipica": 18.0,
        "max_dd_tipico": -34.0, "rf": 2.00,
    },
    "oro_mineras": {
        "nombre": "RV mineras de oro / metales preciosos",
        "r1y": 42.00, "r5y": 8.50, "ter_tipico": 1.95, "vol_tipica": 32.0,
        "max_dd_tipico": -48.0, "rf": 2.00,
    },
    "mineria": {
        "nombre": "RV minería / materiales",
        "r1y": 28.00, "r5y": 9.00, "ter_tipico": 1.90, "vol_tipica": 24.0,
        "max_dd_tipico": -40.0, "rf": 2.00,
    },
}


def r3y_aprox(cat: dict) -> float:
    """Anualizado 3 años interpolando 1Y y 5Y (cuando no hay serie)."""
    return round(0.45 * cat["r1y"] + 0.55 * cat["r5y"], 2)
