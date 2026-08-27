import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Fuentes RSS ---
FUENTES_RSS = {
    "Reuters": "https://feeds.reuters.com/reuters/businessNews",
    "CNBC": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Investing": "https://www.investing.com/rss/news.rss",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
    "FXStreet": "https://www.fxstreet.com/rss/news",
}

# --- Palabras clave por categoría ---
PALABRAS_CLAVE = {
    "acciones": ["stock", "shares", "earnings", "eps", "ipo", "merger",
                 "acquisition", "buyback", "guidance", "nasdaq", "s&p"],
    "commodities": ["oil", "crude", "gold", "silver", "copper", "wheat",
                    "corn", "opec", "barrel", "commodity", "natural gas"],
    "bonos": ["treasury", "yield", "bond", "fomc", "federal reserve",
              "interest rate", "fed ", "ecb", "rate hike", "rate cut"],
}

# --- Tickers a monitorear ---
TICKERS = {
    "acciones": ["^GSPC", "^IXIC", "^DJI"],
    "commodities": ["GC=F", "CL=F", "SI=F", "HG=F"],
    "bonos": ["^TNX", "TLT"],
}

NOMBRES_TICKERS = {
    "^GSPC": "S&P 500", "^IXIC": "NASDAQ", "^DJI": "DOW JONES",
    "GC=F": "🥇 Oro", "CL=F": "🛢️ Petroleo WTI", "SI=F": "Plata", "HG=F": "Cobre",
    "^TNX": "Yield Bono 10Y", "TLT": "ETF Bonos 20+Y",
}

# --- Ajustes ---
SENTIMIENTO_MINIMO = 0.60
MAX_NOTICIAS_POR_CICLO = 8
