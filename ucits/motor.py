"""Orquestación: universo → métricas → scores → régimen → portafolio."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .fondos import universo
from .metricas import enriquecer, intentar_nav_yahoo, ahora_iso
from .scoring import aplicar_regimen, best_in_class, puntuar, resumen_universo
from .regimen import detectar, regimen_por_id
from .portafolio import catalogo_modos, construir

log = logging.getLogger(__name__)

CACHE = Path(os.getenv("UCITS_CACHE", "datos/cache_nav.json"))
_MEM: dict = {"fondos": None, "regimen": None, "ts": None, "vivo": False}


def _cargar_cache_nav() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def refrescar_nav(fondos: list[dict] | None = None) -> dict:
    fondos = fondos or universo()
    simbolos = []
    for f in fondos:
        if f.get("yahoo"):
            simbolos.append(f["yahoo"])
        simbolos.append(f["isin"])
    simbolos = list(dict.fromkeys(simbolos))
    series = intentar_nav_yahoo(simbolos)
    nav_map = {k: v for k, v in series.items()}
    _MEM["vivo"] = bool(nav_map)
    if nav_map:
        log.info("NAV vivo para %s símbolos.", len(nav_map))
    return nav_map


def cargar_screener(forzar_vivo: bool = False) -> dict:
    if _MEM["fondos"] is not None and not forzar_vivo:
        return {
            "fondos": _MEM["fondos"],
            "regimen": _MEM["regimen"],
            "resumen": resumen_universo(_MEM["fondos"]),
            "ts": _MEM["ts"],
            "nav_vivo": _MEM["vivo"],
        }
    nav_map = refrescar_nav() if forzar_vivo else {}
    fondos = [enriquecer(f, nav_map) for f in universo()]
    fondos = puntuar(fondos)
    regimen = detectar()
    fondos = aplicar_regimen(fondos, regimen)
    fondos.sort(key=lambda x: x.get("score_final", 0), reverse=True)
    _MEM.update({"fondos": fondos, "regimen": regimen, "ts": ahora_iso(), "vivo": bool(nav_map)})
    return {
        "fondos": fondos,
        "regimen": regimen,
        "resumen": resumen_universo(fondos),
        "ts": _MEM["ts"],
        "nav_vivo": _MEM["vivo"],
    }


def detectar_regimen(clave: str | None = None) -> dict:
    data = cargar_screener()
    if clave:
        reg = regimen_por_id(clave)
        fondos = aplicar_regimen(list(data["fondos"]), reg)
        _MEM["fondos"] = fondos
        _MEM["regimen"] = reg
        return reg
    return data["regimen"]


def construir_portafolio(modo: str = "automatico", perfil: str = "equilibrado",
                         regimen_id: str | None = None) -> dict:
    data = cargar_screener()
    reg = regimen_por_id(regimen_id) if regimen_id else data["regimen"]
    fondos = aplicar_regimen(list(data["fondos"]), reg)
    return construir(fondos, modo, perfil, reg)


def filtrar(fondos: list[dict], q: str | None = None, clase: str | None = None,
            tema: str | None = None, gestora: str | None = None,
            max_ter: float | None = None, min_sharpe: float | None = None,
            max_dd: float | None = None, divisa: str | None = None,
            solo_bic: bool = False) -> list[dict]:
    out = fondos
    if q:
        ql = q.lower()
        out = [f for f in out if ql in f["nombre"].lower() or ql in f["isin"].lower()
               or ql in f["gestora"].lower() or ql in f["tema"].lower()]
    if clase:
        out = [f for f in out if f["clase_activo"] == clase]
    if tema:
        out = [f for f in out if f["tema"] == tema]
    if gestora:
        out = [f for f in out if f["gestora"] == gestora]
    if max_ter is not None:
        out = [f for f in out if f["ter"] <= max_ter]
    if min_sharpe is not None:
        out = [f for f in out if (f.get("sharpe_3y") or -99) >= min_sharpe]
    if max_dd is not None:
        out = [f for f in out if (f.get("max_dd") or -999) >= max_dd]
    if divisa:
        out = [f for f in out if f["divisa"] == divisa]
    if solo_bic:
        out = [f for f in out if f.get("podio")]
    return out


def payload_publico(f: dict) -> dict:
    campos = (
        "id", "nombre", "isin", "gestora", "domicilio", "divisa", "clase_activo",
        "categoria", "cat_nombre", "tema", "estilo", "ter", "sri", "bic", "tesis",
        "ret_1y", "ret_3y", "ret_5y", "vol_1y", "vol_3y", "max_dd", "sharpe_1y",
        "sharpe_3y", "sortino_3y", "calmar_3y", "ulcer", "exceso_1y", "exceso_5y",
        "fuente", "confianza", "score_calidad", "score_coste", "score_regimen",
        "score_final", "rank_global", "rank_clase", "rank_tema", "n_tema",
        "best_in_class", "podio", "nav", "fecha_nav",
    )
    return {k: f.get(k) for k in campos}


def meta_filtros(fondos: list[dict]) -> dict:
    return {
        "clases": sorted({f["clase_activo"] for f in fondos}),
        "temas": sorted({f["tema"] for f in fondos}),
        "gestoras": sorted({f["gestora"] for f in fondos}),
        "divisas": sorted({f["divisa"] for f in fondos}),
        "modos": catalogo_modos(),
        "perfiles": ["conservador", "equilibrado", "agresivo"],
    }


def bic_agrupado(fondos: list[dict]) -> list[dict]:
    grupos = best_in_class(fondos, por="tema", top=2)
    out = []
    for tema, lista in sorted(grupos.items()):
        out.append({
            "tema": tema,
            "clase": lista[0]["clase_activo"] if lista else None,
            "fondos": [payload_publico(x) for x in lista],
        })
    return out
