"""Universo de valores a escanear.

Fuentes, por orden de preferencia:

1. ``datos/sp500.json`` empaquetado en el repo (funciona sin red).
2. Refresco automático desde la Wikipedia si la lista tiene más de
   ``UNIVERSO_REFRESCAR_DIAS`` días (o con ``--refrescar``).
3. Lista personalizada en ``datos/mi_universo.txt`` / variable UNIVERSO_TICKERS.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone

from config import (ARCHIVO_UNIVERSO, MAX_TICKERS_UNIVERSO, PORTAFOLIO, TICKERS,
                    UNIVERSO, UNIVERSO_AUTOACTUALIZAR, UNIVERSO_REFRESCAR_DIAS,
                    UNIVERSO_TICKERS)

log = logging.getLogger(__name__)

RUTA_DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos")
RUTA_SP500 = os.path.join(RUTA_DATOS, "sp500.json")

_WIKIPEDIA = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Tickers que Yahoo entiende distinto (puntos, guiones...) o que conviene unificar.
_NORMALIZAR = {
    "BRK.B": "BRK-B",
    "BF.B": "BF-B",
}


def normalizar(ticker: str) -> str:
    t = (ticker or "").strip().upper().replace(".", "-")
    return _NORMALIZAR.get(t, t)


# --------------------------------------------------------------- S&P 500 --


def _cargar_sp500_local() -> tuple[list[dict], str]:
    """Carga la lista empaquetada. Devuelve (empresas, fecha_actualizado)."""
    if not os.path.exists(RUTA_SP500):
        return [], ""
    try:
        with open(RUTA_SP500, encoding="utf-8") as f:
            datos = json.load(f)
        empresas = datos.get("empresas", [])
        return empresas, datos.get("actualizado", "")
    except Exception as e:
        log.warning("No se pudo leer %s: %s", RUTA_SP500, e)
    return [], ""


def _guardar_sp500(empresas: list[dict], fuente: str) -> None:
    os.makedirs(RUTA_DATOS, exist_ok=True)
    tmp = RUTA_SP500 + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({
            "actualizado": datetime.now(timezone.utc).date().isoformat(),
            "fuente": fuente,
            "empresas": empresas,
        }, f, ensure_ascii=False, indent=0)
    os.replace(tmp, RUTA_SP500)


def _descargar_sp500() -> list[dict]:
    """Intenta descargar la lista de constituyentes desde la Wikipedia."""
    try:
        import pandas as pd
        tablas = pd.read_html(_WIKIPEDIA)
    except Exception as e:
        log.info("No se pudo refrescar el S&P 500 desde Wikipedia (%s).", type(e).__name__)
        return []

    for tabla in tablas:
        cols = {str(c).lower(): c for c in tabla.columns}
        simbolo = next((c for n, c in cols.items() if "symbol" in n or n == "ticker"), None)
        nombre = next((c for n, c in cols.items() if "security" in n or "company" in n), None)
        sector = next((c for n, c in cols.items() if "gics sector" in n or n == "gics sector"), None)
        if simbolo is None:
            continue
        salida = []
        for _, fila in tabla.iterrows():
            t = normalizar(str(fila[simbolo]))
            if not t or t.lower() == "nan":
                continue
            salida.append({
                "t": t,
                "n": str(fila[nombre]).strip() if nombre is not None else t,
                "s": str(fila[sector]).strip() if sector is not None else "Desconocido",
            })
        if len(salida) > 400:
            log.info("S&P 500 actualizado desde Wikipedia: %s valores.", len(salida))
            return salida
    return []


def sp500(refrescar: bool = False) -> list[dict]:
    """Lista de constituyentes del S&P 500.

    Con ``refrescar=True`` fuerza la descarga. Si falla o no hay red, se usa la
    copia empaquetada.
    """
    empresas, actualizado = _cargar_sp500_local()

    antigua = True
    if actualizado:
        try:
            dias = (datetime.now(timezone.utc).date()
                    - datetime.fromisoformat(actualizado).date()).days
            antigua = dias > UNIVERSO_REFRESCAR_DIAS
        except ValueError:
            antigua = True

    if refrescar or (antigua and UNIVERSO_AUTOACTUALIZAR):
        nuevas = _descargar_sp500()
        if nuevas:
            _guardar_sp500(nuevas, "wikipedia")
            empresas = nuevas

    vistos, salida = set(), []
    for e in empresas:
        t = normalizar(e.get("t", ""))
        if not t or t in vistos:
            continue
        vistos.add(t)
        salida.append({"ticker": t, "nombre": e.get("n", t), "sector": e.get("s", "Desconocido")})
    return salida


# ------------------------------------------------------------ Personalizado --


def personalizado() -> list[dict]:
    """Tu propia lista: fichero de texto y/o variable de entorno."""
    out, vistos = [], set()

    if os.path.exists(ARCHIVO_UNIVERSO):
        try:
            with open(ARCHIVO_UNIVERSO, encoding="utf-8") as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea or linea.startswith("#"):
                        continue
                    t = normalizar(linea.split("#")[0].split("|")[0].split(",")[0])
                    if t and t not in vistos:
                        vistos.add(t)
                        out.append({"ticker": t, "nombre": t, "sector": "Personalizado"})
        except Exception as e:
            log.warning("No se pudo leer %s: %s", ARCHIVO_UNIVERSO, e)

    for t in UNIVERSO_TICKERS:
        t = normalizar(t)
        if t and t not in vistos:
            vistos.add(t)
            out.append({"ticker": t, "nombre": t, "sector": "Personalizado"})

    return out


def portafolio() -> list[dict]:
    return [{"ticker": normalizar(t), "nombre": p.get("nombre", t), "sector": "Mi cartera"}
            for t, p in zip(TICKERS, PORTAFOLIO)]


# ------------------------------------------------------------------ API --


def obtener_universo(modo: str | None = None, refrescar: bool = False,
                     extra: list[str] | None = None) -> tuple[list[dict], str]:
    """Devuelve (lista de {'ticker','nombre','sector'}, descripción)."""
    modo = (modo or UNIVERSO).lower()
    out, vistos, desc = [], set(), []

    def _anadir(elementos: list[dict]):
        for e in elementos:
            t = normalizar(e["ticker"])
            if not t or t in vistos:
                continue
            vistos.add(t)
            out.append({**e, "ticker": t})

    if modo in ("sp500", "mezcla"):
        _anadir(sp500(refrescar=refrescar))
        desc.append("S&P 500")
    if modo in ("personalizado", "mezcla"):
        _anadir(personalizado())
        desc.append("lista personalizada")
    if modo == "portafolio":
        _anadir(portafolio())
        desc.append("mi cartera")
    if modo == "mezcla":
        _anadir(portafolio())
        desc.append("mi cartera")
    if extra:
        _anadir([{"ticker": t, "nombre": t, "sector": "Extra"} for t in extra])
        desc.append("tickers indicados")

    if not out:
        log.warning("El modo de universo '%s' no ha dado ningún ticker; uso el S&P 500.", modo)
        _anadir(sp500(refrescar=refrescar))
        desc = ["S&P 500 (por defecto)"]

    if MAX_TICKERS_UNIVERSO and len(out) > MAX_TICKERS_UNIVERSO:
        log.info("Limitando el universo a %s tickers (MAX_TICKERS_UNIVERSO).",
                 MAX_TICKERS_UNIVERSO)
        out = out[:MAX_TICKERS_UNIVERSO]

    return out, " + ".join(desc)


def es_ticker_valido(ticker: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9\-=.]{1,12}", ticker or ""))
