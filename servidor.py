"""API + interfaz web del screener UCITS."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ucits.motor import (
    bic_agrupado, cargar_screener, construir_portafolio, detectar_regimen,
    filtrar, meta_filtros, payload_publico,
)
from ucits.portafolio import catalogo_modos
from ucits.categorias import CATEGORIAS

log = logging.getLogger("faro")
WEB = Path(__file__).parent / "web"

app = FastAPI(title="FARO UCITS", version="1.0.0",
              description="Screener de fondos UCITS (no ETF) y constructor de portafolio por régimen.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _headers(request, call_next):
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    if "x-frame-options" in resp.headers:
        del resp.headers["x-frame-options"]
    resp.headers["Content-Security-Policy"] = "frame-ancestors *"
    return resp


@app.get("/api/estado")
def estado():
    data = cargar_screener()
    return {
        "ok": True,
        "ts": data["ts"],
        "nav_vivo": data["nav_vivo"],
        "resumen": data["resumen"],
        "regimen": {
            "id": data["regimen"]["id"],
            "nombre": data["regimen"]["nombre"],
            "color": data["regimen"]["color"],
            "resumen": data["regimen"]["resumen"],
            "indicadores": data["regimen"]["indicadores"],
            "alternativas": data["regimen"]["alternativas"],
        },
        "filtros": meta_filtros(data["fondos"]),
        "disclaimer": "Informativo. No constituye asesoramiento financiero ni recomendación de compra.",
    }


@app.get("/api/screener")
def screener(
    q: str | None = None,
    clase: str | None = None,
    tema: str | None = None,
    gestora: str | None = None,
    divisa: str | None = None,
    max_ter: float | None = Query(default=None),
    min_sharpe: float | None = Query(default=None),
    max_dd: float | None = Query(default=None),
    solo_bic: bool = False,
    orden: str = "score_final",
):
    data = cargar_screener()
    fondos = filtrar(data["fondos"], q=q, clase=clase, tema=tema, gestora=gestora,
                     max_ter=max_ter, min_sharpe=min_sharpe, max_dd=max_dd,
                     divisa=divisa, solo_bic=solo_bic)
    rev = True
    if orden in ("ter", "max_dd", "rank_global"):
        rev = False
    fondos = sorted(fondos, key=lambda x: (x.get(orden) is None, x.get(orden) if x.get(orden) is not None else 0),
                    reverse=rev)
    return {
        "n": len(fondos),
        "nav_vivo": data["nav_vivo"],
        "ts": data["ts"],
        "fondos": [payload_publico(f) for f in fondos],
    }


@app.get("/api/fondo/{fid}")
def fondo(fid: str):
    data = cargar_screener()
    f = next((x for x in data["fondos"] if x["id"] == fid or x["isin"] == fid), None)
    if not f:
        return JSONResponse({"error": "fondo no encontrado"}, status_code=404)
    peers = [payload_publico(x) for x in data["fondos"]
             if x["tema"] == f["tema"] and x["id"] != f["id"]][:5]
    return {"fondo": payload_publico(f), "peers": peers, "tesis": f.get("tesis")}


@app.get("/api/best-in-class")
def bic():
    data = cargar_screener()
    return {"grupos": bic_agrupado(data["fondos"]), "por": "tema"}


@app.get("/api/regimen")
def regimen(id: str | None = None):
    if id:
        return detectar_regimen(id)
    return cargar_screener()["regimen"]


@app.get("/api/portafolio")
def portafolio(modo: str = "automatico", perfil: str = "equilibrado",
               regimen: str | None = None):
    return construir_portafolio(modo, perfil, regimen)


@app.get("/api/modos")
def modos():
    return {"modos": catalogo_modos(),
            "perfiles": ["conservador", "equilibrado", "agresivo"]}


@app.get("/api/categorias")
def categorias():
    return CATEGORIAS


@app.post("/api/refrescar")
def refrescar():
    data = cargar_screener(forzar_vivo=True)
    return {"ok": True, "nav_vivo": data["nav_vivo"], "n": data["resumen"]["n"], "ts": data["ts"]}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")


def crear_app() -> FastAPI:
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
