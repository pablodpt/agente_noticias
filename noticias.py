"""Recolección de noticias por ticker (Yahoo Finance RSS + Google News RSS) y macro."""
import hashlib
import html
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser

from config import (FUENTES_RSS_MACRO, HORAS_VENTANA_NOTICIAS,
                    MAX_NOTICIAS_POR_TICKER, NOMBRES, PALABRAS_URGENTES)
from sentimiento import analizar

log = logging.getLogger(__name__)


def _feeds_ticker(ticker: str) -> list[str]:
    nombre = NOMBRES.get(ticker, ticker)
    consulta = quote_plus(f'"{ticker}" OR "{nombre}" stock')
    return [
        f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
        f"https://news.google.com/rss/search?q={consulta}+when:2d&hl=en-US&gl=US&ceid=US:en",
    ]


def _fecha(entry):
    for campo in ("published_parsed", "updated_parsed"):
        v = entry.get(campo)
        if v:
            try:
                return datetime.fromtimestamp(time.mktime(v), tz=timezone.utc)
            except Exception:
                pass
    return None


def _clave(titulo: str) -> str:
    return hashlib.sha1(titulo.lower().strip().encode()).hexdigest()[:16]


def noticias_de_ticker(ticker: str) -> list[dict]:
    limite = datetime.now(timezone.utc) - timedelta(hours=HORAS_VENTANA_NOTICIAS)
    vistas, salida = set(), []

    for url in _feeds_ticker(ticker):
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            log.warning("Feed %s: %s", url, e)
            continue

        for entry in feed.entries[:25]:
            titulo = html.unescape(entry.get("title", "").strip())
            if not titulo:
                continue
            k = _clave(titulo)
            if k in vistas:
                continue
            vistas.add(k)

            fecha = _fecha(entry)
            if fecha and fecha < limite:
                continue

            resumen = html.unescape(entry.get("summary", ""))[:400]
            etiqueta, score = analizar(f"{titulo}. {resumen}")
            texto_low = f"{titulo} {resumen}".lower()
            urgente = any(p in texto_low for p in PALABRAS_URGENTES)

            salida.append({
                "id": f"news:{ticker}:{k}",
                "ticker": ticker,
                "titulo": titulo,
                "resumen": resumen,
                "link": entry.get("link", ""),
                "fuente": entry.get("source", {}).get("title") or feed.feed.get("title", "RSS"),
                "fecha": fecha,
                "sentimiento": etiqueta,
                "score": score,
                "urgente": urgente,
            })

    salida.sort(key=lambda n: (n["urgente"], n["score"], n["fecha"] or datetime.min.replace(tzinfo=timezone.utc)),
                reverse=True)
    return salida[:MAX_NOTICIAS_POR_TICKER * 3]


def noticias_portafolio(tickers: list[str]) -> dict[str, list[dict]]:
    return {t: noticias_de_ticker(t) for t in tickers}


def noticias_macro(maximo: int = 5) -> list[dict]:
    limite = datetime.now(timezone.utc) - timedelta(hours=HORAS_VENTANA_NOTICIAS)
    out, vistas = [], set()
    for fuente, url in FUENTES_RSS_MACRO.items():
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for entry in feed.entries[:10]:
            titulo = html.unescape(entry.get("title", "").strip())
            if not titulo or _clave(titulo) in vistas:
                continue
            vistas.add(_clave(titulo))
            fecha = _fecha(entry)
            if fecha and fecha < limite:
                continue
            etiqueta, score = analizar(titulo)
            out.append({"fuente": fuente, "titulo": titulo, "link": entry.get("link", ""),
                        "sentimiento": etiqueta, "score": score})
    out.sort(key=lambda n: n["score"], reverse=True)
    return out[:maximo]
