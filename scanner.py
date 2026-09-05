"""Escáner de oportunidades: orquesta la descarga, los motores y el ranking.

Flujo:

1. Descarga precios del universo (por lotes) y del benchmark.
2. Calcula indicadores y descarta lo ilíquido / sin historial.
3. Pasa los motores rápidos (momentum y sobreventa) a todos los candidatos.
4. Pide fundamentales solo a los mejor situados (todos, en modo semanal).
5. Pasa el motor de valor y calidad.
6. A los finalistas les añade noticias, SEC, analistas y resultados (catalizador).
7. Combina todo en una puntuación 0-100, diversifica por sector y ordena.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import pandas as pd

import catalizadores
import fundamentales
import indicadores
import universo
from config import (BENCHMARK, BONUS_CONFLUENCIA, BONUS_CONFLUENCIA_MAX,
                    DIAS_SEC_BOLETIN, FUND_TOP_DIARIO, FUND_TODOS_SEMANAL,
                    INDICE_MIEDO, MAX_POR_SECTOR, MAX_TICKERS_NOTICIAS,
                    PESOS_ESTRATEGIA, PRECIO_MINIMO, SESIONES_MINIMAS, TICKERS,
                    TOP_DIARIO, TOP_SEMANAL, TZ, UMBRAL_FINAL, VOLATILIDAD_ALTA,
                    VOLATILIDAD_MAX, VOLUMEN_DOLARES_MIN)
from estrategias import MOTORES, MOTORES_FUN, Candidato, Senal

log = logging.getLogger(__name__)

PERIODO = "1y"


# ------------------------------------------------------------------- Modelos --

@dataclass
class Idea:
    ticker: str
    nombre: str
    sector: str
    senales: list[Senal] = field(default_factory=list)
    puntos: float = 0.0
    confianza: float = 0.0
    ind: dict = field(default_factory=dict)
    fund: dict | None = None
    ya_avisada: bool = False
    clave_estado: str = ""

    @property
    def motores(self) -> list[str]:
        return [s.motor for s in self.senales]

    @property
    def etiquetas(self) -> list[str]:
        return [s.etiqueta or MOTORES[s.motor][0] for s in self.senales]

    @property
    def razones(self) -> list[str]:
        out = []
        for s in sorted(self.senales, key=lambda x: -x.puntos):
            out.extend(s.razones)
        return _unicos(out)[:6]

    @property
    def riesgos(self) -> list[str]:
        out = []
        for s in self.senales:
            out.extend(s.riesgos)
        return _unicos(out)[:4]

    @property
    def horizonte(self) -> str:
        if not self.senales:
            return ""
        return sorted(self.senales, key=lambda x: -x.puntos)[0].horizonte

    @property
    def precio(self):
        return self.ind.get("precio")

    @property
    def enlace(self) -> str:
        return f"https://finance.yahoo.com/quote/{self.ticker}"


@dataclass
class Resultado:
    modo: str
    universo: str
    generado: datetime
    ideas: list[Idea] = field(default_factory=list)
    regimen: dict = field(default_factory=dict)
    total_universo: int = 0
    con_datos: int = 0
    analizados: int = 0
    descartes: dict = field(default_factory=dict)
    duracion_seg: float = 0.0
    avisos: list[str] = field(default_factory=list)

    @property
    def nuevas(self) -> list[Idea]:
        return [i for i in self.ideas if not i.ya_avisada]

    def idea(self, ticker: str) -> Idea | None:
        return next((i for i in self.ideas if i.ticker == ticker), None)


def _unicos(items: list[str]) -> list[str]:
    vistos, out = set(), []
    for i in items:
        if i and i not in vistos:
            vistos.add(i)
            out.append(i)
    return out


# --------------------------------------------------------------- Fuentes de datos --

@dataclass
class Fuentes:
    """Permite enchufar datos sintéticos (tests) sin tocar la red."""
    precios: Callable[..., dict] | None = None
    referencia: Callable[[], tuple[pd.DataFrame | None, pd.DataFrame | None]] | None = None
    fundamentales: Callable[..., dict] | None = None
    catalizadores: Callable[..., dict] | None = None

    @classmethod
    def reales(cls) -> "Fuentes":
        return cls(
            precios=lambda tickers, avance=None: indicadores.descargar_precios(
                tickers, periodo=PERIODO, avance=avance)[0],
            referencia=lambda: _descargar_referencia(),
            fundamentales=lambda tickers, avance=None, forzar=False:
                fundamentales.obtener(tickers, forzar=forzar, avance=avance),
            catalizadores=lambda tickers, avance=None: catalizadores.recolectar(
                tickers, dias_sec=DIAS_SEC_BOLETIN, avance=avance),
        )


def _descargar_referencia() -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    datos, _ = indicadores.descargar_precios([BENCHMARK, INDICE_MIEDO], periodo=PERIODO)
    return datos.get(BENCHMARK), datos.get(INDICE_MIEDO)


# -------------------------------------------------------------------- Escaneo --

def _serie_referencia(bench: pd.DataFrame | None) -> pd.Series | None:
    if bench is None or bench.empty:
        return None
    return bench["Adj Close"].where(bench["Adj Close"].notna(), bench["Close"]).astype(float)


def _pasa_filtros(ind: dict, descartes: dict) -> bool:
    if not ind:
        descartes["sin_indicadores"] = descartes.get("sin_indicadores", 0) + 1
        return False
    precio = ind.get("precio") or 0
    if precio < PRECIO_MINIMO:
        descartes["precio_bajo"] = descartes.get("precio_bajo", 0) + 1
        return False
    dolares = ind.get("dolares_medios") or 0
    if dolares < VOLUMEN_DOLARES_MIN:
        descartes["iliquido"] = descartes.get("iliquido", 0) + 1
        return False
    if (ind.get("sesiones") or 0) < SESIONES_MINIMAS:
        descartes["historial_corto"] = descartes.get("historial_corto", 0) + 1
        return False
    return True


def _puntuar(idea: Idea, regimen: dict) -> None:
    """Combina las señales de los motores en una puntuación final 0-100."""
    if not idea.senales:
        return
    ajuste = regimen.get("ajuste", {}) or {}
    pesos, suma, confianza = {}, 0.0, 0.0
    for s in idea.senales:
        peso = PESOS_ESTRATEGIA.get(s.motor, 0.2) * ajuste.get(s.motor, 1.0)
        pesos[s.motor] = peso
        suma += peso
        confianza += peso * s.confianza
    if suma <= 0:
        return

    puntos = sum(pesos[s.motor] * s.puntos for s in idea.senales) / suma
    confianza = confianza / suma

    # Confluencia: si varios motores coinciden, la idea gana solidez.
    if len(idea.senales) > 1:
        puntos += min(BONUS_CONFLUENCIA * (len(idea.senales) - 1), BONUS_CONFLUENCIA_MAX)

    # Penalización por volatilidad extrema.
    vol = idea.ind.get("volatilidad")
    if isinstance(vol, (int, float)):
        if vol > VOLATILIDAD_MAX:
            puntos -= 10
            idea.senales[0].riesgos.append(f"Volatilidad extrema ({vol:.0f}% anualizada)")
        elif vol > VOLATILIDAD_ALTA:
            puntos -= 4

    idea.puntos = max(0.0, min(100.0, puntos))
    idea.confianza = max(0.05, min(0.95, confianza))


def escanear(modo: str = "semanal",
             nombre_universo: str | None = None,
             top: int | None = None,
             tickers_extra: list[str] | None = None,
             con_fundamentales: bool = True,
             con_catalizadores: bool = True,
             forzar_fundamentales: bool = False,
             fuentes: Fuentes | None = None,
             avance: Callable[[str], None] | None = None,
             refrescar_universo: bool = False,
             meta_extra: dict[str, dict] | None = None,
             estado=None) -> Resultado:
    """Escanea el mercado y devuelve las mejores oportunidades ordenadas."""
    inicio = time.time()
    fuentes = fuentes or Fuentes.reales()
    modo = modo.lower()
    if modo not in ("diario", "semanal", "rapido"):
        modo = "semanal"

    def paso(msg: str):
        log.info(msg)
        if avance:
            avance(msg)

    # ------------------------------------------------------------- universo --
    lista, descripcion = universo.obtener_universo(nombre_universo,
                                                   refrescar=refrescar_universo,
                                                   extra=tickers_extra)
    for t, datos in (meta_extra or {}).items():
        for e in lista:
            if e["ticker"] == t:
                e.update({k: v for k, v in datos.items() if v})
    tickers = [e["ticker"] for e in lista]
    meta = {e["ticker"]: e for e in lista}
    resultado = Resultado(modo=modo, universo=descripcion, generado=datetime.now(TZ),
                          total_universo=len(tickers))
    paso(f"Universo: {descripcion} ({len(tickers)} valores)")

    if not tickers:
        resultado.avisos.append("El universo está vacío.")
        return resultado

    # ------------------------------------------------- referencia y precios --
    bench = miedo = None
    try:
        bench, miedo = fuentes.referencia()      # type: ignore[misc]
    except Exception as e:
        log.warning("No se pudo descargar la referencia: %s", e)
    resultado.regimen = indicadores.regimen_mercado(bench, miedo)
    paso(f"Régimen de mercado: {resultado.regimen['etiqueta']}"
         + (f" ({resultado.regimen['precio']} pts)" if resultado.regimen.get("precio") else ""))

    referencia = _serie_referencia(bench)
    precios = fuentes.precios(tickers, avance=avance) if fuentes.precios else {}
    if not precios:
        resultado.avisos.append("No se han podido descargar precios (¿sin conexión?).")
        return resultado
    resultado.con_datos = len(precios)
    paso(f"Precios descargados: {len(precios)}/{len(tickers)} valores")

    # ------------------------------------------------------- indicadores + filtros --
    indicadores_por_ticker: dict[str, dict] = {}
    for t, df in precios.items():
        ind = indicadores.calcular(df, referencia=referencia)
        if not ind:
            resultado.descartes["historial_corto"] = \
                resultado.descartes.get("historial_corto", 0) + 1
            continue
        ind["ticker"] = t
        if not _pasa_filtros(ind, resultado.descartes):
            continue
        indicadores_por_ticker[t] = ind
    resultado.analizados = len(indicadores_por_ticker)
    paso(f"Tras los filtros de liquidez: {len(indicadores_por_ticker)} candidatos")

    # --------------------------------------------- motores rápidos (sin red) --
    candidatos: dict[str, Candidato] = {}
    ideas: dict[str, Idea] = {}
    for t, ind in indicadores_por_ticker.items():
        m = meta.get(t, {})
        cand = Candidato(ticker=t, nombre=m.get("nombre", t), sector=m.get("sector", "Desconocido"),
                         ind=ind)
        for motor in ("momentum", "reversion"):
            senal = MOTORES_FUN[motor](cand)
            if senal:
                idea = ideas.setdefault(
                    t, Idea(ticker=t, nombre=cand.nombre, sector=cand.sector, ind=ind))
                idea.senales.append(senal)
        candidatos[t] = cand

    for t, idea in ideas.items():
        _puntuar(idea, resultado.regimen)
    paso(f"Señales técnicas: {len(ideas)} valores con momentum o sobreventa")

    # --------------------------------------------------------- fundamentales --
    if con_fundamentales and fuentes.fundamentales:
        orden = sorted(candidatos, key=lambda t: -ideas[t].puntos if t in ideas else 0)
        limite = FUND_TODOS_SEMANAL if modo == "semanal" else FUND_TOP_DIARIO
        objetivo = orden[:limite] if limite else orden
        paso(f"Descargando fundamentales de {len(objetivo)} valores...")
        try:
            datos = fuentes.fundamentales(objetivo, avance=avance,
                                          forzar=forzar_fundamentales)   # type: ignore[call-arg]
        except TypeError:
            datos = fuentes.fundamentales(objetivo)                      # type: ignore[call-arg]
        sectores = {t: candidatos[t].sector for t in datos}
        rel = fundamentales.percentiles_por_sector(datos, sectores)
        for t, fund in datos.items():
            if t not in candidatos:
                continue
            candidatos[t].fund = fund
            candidatos[t].rel = rel.get(t, {})
            senal = MOTORES_FUN["valor"](candidatos[t])
            if senal:
                idea = ideas.setdefault(t, Idea(ticker=t, nombre=candidatos[t].nombre,
                                                sector=candidatos[t].sector,
                                                ind=indicadores_por_ticker[t], fund=fund))
                idea.fund = fund
                idea.senales.append(senal)
                _puntuar(idea, resultado.regimen)
        paso(f"Valoraciones recibidas: {len(datos)}; ideas con valor/calidad: "
             f"{sum(1 for i in ideas.values() if 'valor' in i.motores)}")
    else:
        resultado.avisos.append("Escaneo sin fundamentales: el motor de valor no ha participado.")

    # ----------------------------------------------------------- catalizador --
    if con_catalizadores and fuentes.catalizadores:
        orden = sorted(ideas.values(), key=lambda i: -i.puntos)
        finalistas = [i.ticker for i in orden[:MAX_TICKERS_NOTICIAS]]
        # los valores de nuestra cartera siempre se analizan a fondo
        for t in TICKERS:
            if t in candidatos and t not in finalistas:
                finalistas.append(t)
        if finalistas:
            paso(f"Buscando catalizadores en {len(finalistas)} finalistas...")
            try:
                cats = fuentes.catalizadores(finalistas, avance=avance)  # type: ignore[call-arg]
            except TypeError:
                cats = fuentes.catalizadores(finalistas)                 # type: ignore[call-arg]
            for t, info in cats.items():
                if t not in candidatos:
                    continue
                candidatos[t].noticias = info.get("noticias", [])
                candidatos[t].filings = info.get("filings", [])
                candidatos[t].upgrades = info.get("upgrades", [])
                candidatos[t].earnings = info.get("earnings")
                senal = MOTORES_FUN["catalizador"](candidatos[t])
                if senal:
                    idea = ideas.setdefault(t, Idea(ticker=t, nombre=candidatos[t].nombre,
                                                    sector=candidatos[t].sector,
                                                    ind=indicadores_por_ticker[t],
                                                    fund=candidatos[t].fund))
                    idea.senales.append(senal)
                    _puntuar(idea, resultado.regimen)
            paso(f"Finalistas con catalizador: "
                 f"{sum(1 for i in ideas.values() if 'catalizador' in i.motores)}")

    # -------------------------------------------------------------- ranking --
    for idea in ideas.values():
        _puntuar(idea, resultado.regimen)
    ordenadas = sorted(ideas.values(), key=lambda i: (-i.puntos, -i.confianza))
    ordenadas = [i for i in ordenadas if i.puntos >= UMBRAL_FINAL]

    limite_top = top or (TOP_DIARIO if modo == "diario" else TOP_SEMANAL)
    # El tope por sector se flexibiliza si el universo tiene pocos sectores;
    # si no, un universo de un solo sector solo daría MAX_POR_SECTOR ideas.
    sectores_presentes = {i.sector or "Desconocido" for i in ordenadas}
    tope_sector = max(MAX_POR_SECTOR,
                      math.ceil(limite_top / max(1, len(sectores_presentes))))
    seleccion, por_sector = [], {}
    for idea in ordenadas:
        sector = idea.sector or "Desconocido"
        if por_sector.get(sector, 0) >= tope_sector:
            continue
        por_sector[sector] = por_sector.get(sector, 0) + 1
        seleccion.append(idea)
        if len(seleccion) >= limite_top:
            break

    # deduplicación: no repetir la misma idea durante unos días
    if estado is not None:
        for idea in seleccion:
            idea.clave_estado = f"op:{idea.ticker}"
            idea.ya_avisada = estado.ya_visto(idea.clave_estado)

    resultado.ideas = seleccion
    resultado.duracion_seg = round(time.time() - inicio, 1)
    paso(f"Escaneo terminado en {resultado.duracion_seg}s: {len(seleccion)} oportunidades")
    return resultado
