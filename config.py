"""Configuración del Agente de Portafolio.

Edita PORTAFOLIO con tus tickers reales. Todo lo demás tiene valores por defecto
sensatos y puede sobreescribirse por variables de entorno.
"""
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------- Telegram --
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --------------------------------------------------------------- Horarios --
# Zona horaria del usuario (España). El boletín diario sale a HORA_BOLETIN.
TZ = ZoneInfo(os.getenv("TZ_USUARIO", "Europe/Madrid"))
HORA_BOLETIN = int(os.getenv("HORA_BOLETIN", "18"))
MINUTO_BOLETIN = int(os.getenv("MINUTO_BOLETIN", "0"))

# Cada cuántos segundos revisa eventos urgentes el modo daemon.
INTERVALO_VIGILANCIA_SEG = int(os.getenv("INTERVALO_VIGILANCIA_SEG", "900"))

# -------------------------------------------------------------- Portafolio --
# ⚠️ EDITA ESTA LISTA. Puedes poner solo el ticker: {"ticker": "AAPL"}
# "nombre" es opcional (si falta se pide a Yahoo). "peso" es opcional y solo
# se usa para ordenar la relevancia en el boletín.
PORTAFOLIO = [
    {"ticker": "AAPL",  "nombre": "Apple",     "peso": 20},
    {"ticker": "MSFT",  "nombre": "Microsoft", "peso": 20},
    {"ticker": "NVDA",  "nombre": "NVIDIA",    "peso": 15},
    {"ticker": "AMZN",  "nombre": "Amazon",    "peso": 15},
    {"ticker": "GOOGL", "nombre": "Alphabet",  "peso": 10},
    {"ticker": "TSLA",  "nombre": "Tesla",     "peso": 10},
    {"ticker": "META",  "nombre": "Meta",      "peso": 10},
]

TICKERS = [p["ticker"] for p in PORTAFOLIO]
NOMBRES = {p["ticker"]: p.get("nombre", p["ticker"]) for p in PORTAFOLIO}
PESOS = {p["ticker"]: p.get("peso", 1) for p in PORTAFOLIO}

# Índices de contexto que se muestran en el boletín (no generan alertas).
INDICES_CONTEXTO = {
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ",
    "^VIX": "VIX (miedo)",
    "^TNX": "Bono 10Y US",
}

# ------------------------------------------------- Umbrales de alerta LIVE --
# Movimiento intradía (%) que dispara alerta urgente inmediata.
UMBRAL_MOVIMIENTO_PCT = float(os.getenv("UMBRAL_MOVIMIENTO_PCT", "3.0"))
# Volumen respecto a la media de 20 días que se considera anómalo.
UMBRAL_VOLUMEN_X = float(os.getenv("UMBRAL_VOLUMEN_X", "2.5"))
# Ruptura de máximos/mínimos: ventana en días de sesión.
VENTANA_RUPTURA_DIAS = int(os.getenv("VENTANA_RUPTURA_DIAS", "52"))  # ~52 sesiones
# Ruptura de máximo/mínimo de 52 SEMANAS también se vigila (siempre activa).
# Gap de apertura (%) respecto al cierre anterior.
UMBRAL_GAP_PCT = float(os.getenv("UMBRAL_GAP_PCT", "3.0"))

# ------------------------------------------------------------- Fundamentos --
# Días de antelación para avisar de un earnings próximo.
DIAS_AVISO_EARNINGS = int(os.getenv("DIAS_AVISO_EARNINGS", "10"))
# Formularios SEC que consideramos relevantes.
FORMULARIOS_SEC = ["10-K", "10-Q", "8-K", "S-1", "SC 13D", "SC 13G", "DEF 14A", "4"]
FORMULARIOS_SEC_URGENTES = ["8-K", "10-K", "10-Q"]
DIAS_SEC_BOLETIN = int(os.getenv("DIAS_SEC_BOLETIN", "3"))

# User-Agent obligatorio para la API de SEC EDGAR.
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "agente-noticias contacto@example.com")

# ----------------------------------------------------------------- Noticias --
MAX_NOTICIAS_POR_TICKER = int(os.getenv("MAX_NOTICIAS_POR_TICKER", "4"))
HORAS_VENTANA_NOTICIAS = int(os.getenv("HORAS_VENTANA_NOTICIAS", "26"))

# Feeds macro generales (contexto del boletín).
FUENTES_RSS_MACRO = {
    "CNBC": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
}

# Palabras que marcan una noticia como URGENTE (alerta inmediata).
PALABRAS_URGENTES = [
    "halted", "trading halt", "bankruptcy", "chapter 11", "fraud", "sec probe",
    "investigation", "subpoena", "lawsuit", "recall", "guidance cut", "cuts guidance",
    "slashes", "plunge", "plunges", "soars", "surges", "acquisition", "acquires",
    "merger", "takeover", "buyout", "ceo steps down", "ceo resigns", "resignation",
    "downgrade", "upgrade", "profit warning", "delisting", "short seller",
    "beats estimates", "misses estimates", "earnings beat", "earnings miss",
    "stock split", "dividend cut", "layoffs", "data breach", "antitrust",
]

# --------------------------------------------------------------- Sentimiento --
# FinBERT es preciso pero descarga ~440MB. Si está en False se usa un
# analizador léxico ligero (suficiente para priorizar titulares).
USAR_FINBERT = os.getenv("USAR_FINBERT", "false").lower() in ("1", "true", "yes")
SENTIMIENTO_MINIMO = float(os.getenv("SENTIMIENTO_MINIMO", "0.75"))

# ------------------------------------------------------------------- Estado --
ARCHIVO_ESTADO = os.getenv("ARCHIVO_ESTADO", "estado.json")
# Horas que un evento ya notificado permanece silenciado.
TTL_DEDUP_HORAS = int(os.getenv("TTL_DEDUP_HORAS", "36"))
