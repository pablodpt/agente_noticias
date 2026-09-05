"""Análisis de sentimiento financiero.

Por defecto usa un analizador léxico ligero (rápido, sin descargas).
Si USAR_FINBERT=true se carga ProsusAI/finbert (más preciso, ~440MB).
"""
import logging
import re

from config import USAR_FINBERT

log = logging.getLogger(__name__)

_POSITIVAS = {
    "beat": 2, "beats": 2, "surge": 2, "surges": 2, "soar": 2, "soars": 2, "rally": 2,
    "rallies": 2, "jump": 2, "jumps": 2, "record": 2, "upgrade": 2, "upgraded": 2,
    "outperform": 2, "raises": 2, "raise": 1, "growth": 1, "profit": 1, "strong": 1,
    "gains": 1, "gain": 1, "boost": 1, "boosts": 1, "wins": 1, "win": 1, "approval": 1,
    "buyback": 2, "dividend hike": 2, "expands": 1, "bullish": 2, "optimistic": 1,
    "top": 1, "tops": 2, "breakthrough": 2, "partnership": 1, "contract": 1,
}
_NEGATIVAS = {
    "miss": 2, "misses": 2, "plunge": 2, "plunges": 2, "sink": 2, "sinks": 2,
    "tumble": 2, "tumbles": 2, "slump": 2, "crash": 3, "downgrade": 2, "downgraded": 2,
    "underperform": 2, "cut": 1, "cuts": 2, "slashes": 2, "warning": 2, "warns": 2,
    "loss": 2, "losses": 2, "weak": 1, "decline": 1, "declines": 1, "falls": 1,
    "fall": 1, "drop": 1, "drops": 1, "lawsuit": 2, "probe": 2, "investigation": 2,
    "fraud": 3, "bankruptcy": 3, "layoffs": 2, "recall": 2, "halted": 3, "delisting": 3,
    "bearish": 2, "short seller": 3, "antitrust": 2, "breach": 2, "resigns": 2,
}

_finbert = None


def _cargar_finbert():
    global _finbert
    if _finbert is None:
        from transformers import pipeline  # import perezoso
        log.info("Cargando FinBERT (primera vez descarga ~440MB)...")
        _finbert = pipeline("text-classification", model="ProsusAI/finbert")
    return _finbert


def analizar(texto: str) -> tuple[str, float]:
    """Devuelve (etiqueta, confianza) con etiqueta en {positive, negative, neutral}."""
    texto = (texto or "").strip()
    if not texto:
        return "neutral", 0.0

    if USAR_FINBERT:
        try:
            r = _cargar_finbert()(texto[:512], truncation=True)[0]
            return r["label"].lower(), float(r["score"])
        except Exception as e:
            log.warning("FinBERT falló (%s). Uso el analizador léxico.", e)

    return _lexico(texto)


def _lexico(texto: str) -> tuple[str, float]:
    t = " " + re.sub(r"[^a-z ]", " ", texto.lower()) + " "
    pos = sum(p for w, p in _POSITIVAS.items() if f" {w} " in t)
    neg = sum(p for w, p in _NEGATIVAS.items() if f" {w} " in t)
    total = pos + neg
    if total == 0:
        return "neutral", 0.5
    if pos == neg:
        return "neutral", 0.5
    etiqueta = "positive" if pos > neg else "negative"
    confianza = 0.5 + 0.5 * (abs(pos - neg) / total)
    return etiqueta, round(min(confianza, 0.99), 3)


ETIQUETAS_ES = {
    "positive": "📈 ALCISTA",
    "negative": "📉 BAJISTA",
    "neutral": "➖ NEUTRAL",
}
