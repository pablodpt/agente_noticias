import html
import logging
from datetime import datetime, timezone

import feedparser
import yfinance as yf
from transformers import pipeline

from config import (FUENTES_RSS, PALABRAS_CLAVE, TICKERS, NOMBRES_TICKERS,
                    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
                    SENTIMIENTO_MINIMO, MAX_NOTICIAS_POR_CICLO)
from telegram_bot import enviar_telegram, probar_conexion

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

log.info("Cargando FinBERT... (la primera vez descarga ~440MB, ten paciencia)")
finbert = pipeline("text-classification", model="ProsusAI/finbert")

ETIQUETAS_ES = {
    "positive": "📈 ALCISTA",
    "negative": "📉 BAJISTA",
    "neutral":  "➖ NEUTRAL",
}


def obtener_noticias() -> list[dict]:
    noticias = []
    for fuente, url in FUENTES_RSS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                titulo = entry.get("title", "").strip()
                if titulo:
                    noticias.append({
                        "fuente": fuente,
                        "titulo": html.unescape(titulo),
                        "resumen": html.unescape(entry.get("summary", ""))[:500],
                        "link": entry.get("link", ""),
                    })
        except Exception as e:
            log.warning(f"Error en {fuente}: {e}")
    log.info(f"📰 {len(noticias)} noticias recolectadas")
    return noticias


def clasificar_categorias(noticia: dict) -> list[str]:
    texto = (noticia["titulo"] + " " + noticia["resumen"]).lower()
    return [cat for cat, kws in PALABRAS_CLAVE.items() if any(kw in texto for kw in kws)]


def obtener_precios(categorias: list[str]) -> dict:
    tickers = []
    for cat in categorias:
        tickers.extend(TICKERS.get(cat, []))
    precios = {}
    for simbolo in set(tickers):
        try:
            info = yf.Ticker(simbolo).history(period="2d")
            if len(info) >= 2:
                cambio = (info["Close"].iloc[-1] / info["Close"].iloc[-2] - 1) * 100
                precios[simbolo] = {
                    "precio": round(info["Close"].iloc[-1], 2),
                    "cambio_dia": round(cambio, 2),
                }
        except Exception:
            pass
    return precios


def ejecutar_ciclo():
    log.info("🔄 Iniciando ciclo de análisis...")
    noticias = obtener_noticias()
    procesadas = []

    for noticia in noticias:
        categorias = clasificar_categorias(noticia)
        if not categorias:
            continue
        texto = f"{noticia['titulo']}. {noticia['resumen']}"
        resultado = finbert(texto[:512], truncation=True)[0]
        sentimiento, score = resultado["label"], round(resultado["score"], 3)

        if sentimiento != "neutral" and score >= SENTIMIENTO_MINIMO:
            noticia.update({"categorias": categorias, "sentimiento": sentimiento, "score": score})
            procesadas.append(noticia)

    procesadas.sort(key=lambda x: x["score"], reverse=True)
    top = procesadas[:MAX_NOTICIAS_POR_CICLO]

    if not top:
        log.info("Sin señales relevantes en este ciclo.")
        return

    mensajes = []
    for i, n in enumerate(top, 1):
        emoji = ETIQUETAS_ES[n["sentimiento"]]
        conf = f"{n['score']*100:.0f}%"
        msg = (
            f"<b>🚨 ALERTA {i}/{len(top)} — {emoji} ({conf})</b>\n\n"
            f"📄 <b>{html.escape(n['titulo'])}</b>\n\n"
            f"🏷️ Categorias: <b>{', '.join(n['categorias']).upper()}</b>\n"
            f"📡 Fuente: {n['fuente']}\n"
            f"🔗 <a href='{n['link']}'>Leer noticia</a>"
        )
        precios = obtener_precios(n["categorias"])
        if precios:
            msg += "\n\n<b>📊 Mercado ahora:</b>"
            for simbolo, d in precios.items():
                flecha = "🟢" if d["cambio_dia"] >= 0 else "🔴"
                msg += f"\n{flecha} {NOMBRES_TICKERS.get(simbolo, simbolo)}: {d['precio']} ({d['cambio_dia']:+.2f}%)"
        mensajes.append(msg)

    boletin = (
        f"<b>💼 BOLETIN FINANCIERO</b>\n"
        f"<i>{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC</i>\n"
        + "\n" + "─" * 25 + "\n\n"
        + ("\n\n" + "─" * 25 + "\n\n").join(mensajes)
    )

    if enviar_telegram(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, boletin):
        log.info(f"📤 Boletin enviado ({len(top)} alertas)")


if __name__ == "__main__":
    if not probar_conexion(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID):
        exit(1)
    ejecutar_ciclo()
