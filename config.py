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

# ============================================================================
#  DETECTOR DE OPORTUNIDADES  (escanear el mercado en busca de ideas)
# ============================================================================
#
# Universo a escanear:
#   "sp500"        -> los ~500 valores del S&P 500 (lista propia o descargada)
#   "personalizado"-> tus tickers en datos/mi_universo.txt (o UNIVERSO_TICKERS)
#   "mezcla"       -> S&P 500 + tu portafolio + lista personalizada
UNIVERSO = os.getenv("UNIVERSO", "sp500").lower()
ARCHIVO_UNIVERSO = os.getenv("ARCHIVO_UNIVERSO", "datos/mi_universo.txt")
# Lista alternativa en línea: "AAPL,SAN.MC,BTC-USD"
UNIVERSO_TICKERS = [t.strip().upper() for t in os.getenv("UNIVERSO_TICKERS", "").split(",") if t.strip()]

# Si la lista empaquetada tiene más de X días, se intenta refrescar de Wikipedia.
UNIVERSO_REFRESCAR_DIAS = int(os.getenv("UNIVERSO_REFRESCAR_DIAS", "30"))
UNIVERSO_AUTOACTUALIZAR = os.getenv("UNIVERSO_AUTOACTUALIZAR", "true").lower() in ("1", "true", "yes")
# 0 = sin límite. Útil para pruebas rápidas.
MAX_TICKERS_UNIVERSO = int(os.getenv("MAX_TICKERS_UNIVERSO", "0"))

# Referencia para comparar (fuerza relativa, beta, régimen de mercado).
BENCHMARK = os.getenv("BENCHMARK", "SPY")
INDICE_MIEDO = os.getenv("INDICE_MIEDO", "^VIX")

# Cuántas ideas muestra cada informe.
TOP_DIARIO = int(os.getenv("TOP_DIARIO", "8"))
TOP_SEMANAL = int(os.getenv("TOP_SEMANAL", "15"))
# Como máximo N ideas del mismo sector, para diversificar la lista final.
MAX_POR_SECTOR = int(os.getenv("MAX_POR_SECTOR", "3"))

# ------------------------------------------------- Filtros de liquidez/calidad --
# Descarta chicharros, valores ilíquidos y historiales demasiado cortos.
PRECIO_MINIMO = float(os.getenv("PRECIO_MINIMO", "5"))
VOLUMEN_DOLARES_MIN = float(os.getenv("VOLUMEN_DOLARES_MIN", "20000000"))
SESIONES_MINIMAS = int(os.getenv("SESIONES_MINIMAS", "200"))
# Volatilidad anualizada (%) a partir de la cual penalizamos la idea.
VOLATILIDAD_ALTA = float(os.getenv("VOLATILIDAD_ALTA", "60"))
VOLATILIDAD_MAX = float(os.getenv("VOLATILIDAD_MAX", "90"))

# ---------------------------------------------------------- Pesos y umbrales --
# Peso de cada motor en la puntuación final (se normaliza con los motores que
# hayan dado señal). Puedes sobreescribirlos por entorno:
#   PESOS_ESTRATEGIA="momentum:0.4,valor:0.3,reversion:0.2,catalizador:0.1"
PESOS_ESTRATEGIA = {"momentum": 0.30, "reversion": 0.25, "valor": 0.25, "catalizador": 0.20}
_pesos_env = os.getenv("PESOS_ESTRATEGIA", "")
if _pesos_env:
    for _par in _pesos_env.split(","):
        if ":" in _par:
            _k, _v = _par.split(":", 1)
            try:
                PESOS_ESTRATEGIA[_k.strip()] = float(_v)
            except ValueError:
                pass

# Puntuación mínima (0-100) que debe sacar un motor para emitir señal.
UMBRAL_SENAL = float(os.getenv("UMBRAL_SENAL", "45"))
# Puntuación final mínima para entrar en el informe.
UMBRAL_FINAL = float(os.getenv("UMBRAL_FINAL", "50"))
# Bonificación por confluencia: varios motores de acuerdo sobre el mismo valor.
BONUS_CONFLUENCIA = float(os.getenv("BONUS_CONFLUENCIA", "4"))
BONUS_CONFLUENCIA_MAX = float(os.getenv("BONUS_CONFLUENCIA_MAX", "12"))

# ------------------------------------------------------------- Coste del scan --
# Descargar fundamentales de 500 valores es lento. El scan diario solo los pide
# para los candidatos con mejor pinta; el semanal, para todos.
FUND_TOP_DIARIO = int(os.getenv("FUND_TOP_DIARIO", "70"))
FUND_TODOS_SEMANAL = int(os.getenv("FUND_TODOS_SEMANAL", "600"))
# Noticias y presentaciones SEC solo para los finalistas (son muy lentas).
MAX_TICKERS_NOTICIAS = int(os.getenv("MAX_TICKERS_NOTICIAS", "25"))
HILOS_FUNDAMENTALES = int(os.getenv("HILOS_FUNDAMENTALES", "12"))
CACHE_FUND_HORAS = int(os.getenv("CACHE_FUND_HORAS", "12"))
TAMANO_LOTE = int(os.getenv("TAMANO_LOTE", "60"))
PAUSA_ENTRE_LOTES = float(os.getenv("PAUSA_ENTRE_LOTES", "1.0"))

# Horas que una idea enviada permanece silenciada (no se repite).
TTL_OPORTUNIDAD_HORAS = int(os.getenv("TTL_OPORTUNIDAD_HORAS", "96"))

# Días de antelación con los que un resultado trimestral cuenta como catalizador.
DIAS_CATALIZADOR_EARNINGS = int(os.getenv("DIAS_CATALIZADOR_EARNINGS", "7"))

# Carpeta donde se guardan los informes.
DIR_INFORMES = os.getenv("DIR_INFORMES", "informes")
# Publicar el último informe en el repo (lo que sube el workflow).
INFORME_PUBLICO = os.getenv("INFORME_PUBLICO", "ultimo.md")

# Contexto: sectores en español para los informes.
SECTORES_ES = {
    "Information Technology": "Tecnología",
    "Financials": "Financieras",
    "Health Care": "Salud",
    "Consumer Discretionary": "Consumo cíclico",
    "Consumer Staples": "Consumo defensivo",
    "Communication Services": "Comunicación",
    "Industrials": "Industriales",
    "Energy": "Energía",
    "Utilities": "Utilidades",
    "Real Estate": "Inmobiliario",
    "Materials": "Materiales",
}


def forzar_utf8() -> None:
    """Evita errores de codificación en Windows.

    La consola de Windows puede estar en cp1252, que no representa los
    emoticonos de los mensajes: al imprimirlos (o al redirigir la salida a un
    fichero) Python lanzaría UnicodeEncodeError. Forzamos UTF-8 y, si algo no
    se puede representar, lo sustituimos en lugar de cascarnos.
    """
    import sys
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
