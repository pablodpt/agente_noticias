"""Consulta de presentaciones (10-K, 10-Q, 8-K...) en SEC EDGAR."""
import functools
import logging
from datetime import date, datetime, timedelta

import requests

from config import (DIAS_SEC_BOLETIN, FORMULARIOS_SEC, FORMULARIOS_SEC_URGENTES,
                    SEC_USER_AGENT)

log = logging.getLogger(__name__)

_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
_MAPA_TICKERS = "https://www.sec.gov/files/company_tickers.json"

DESCRIPCION = {
    "10-K": "📕 Informe anual (10-K)",
    "10-Q": "📗 Informe trimestral (10-Q)",
    "8-K": "⚡ Hecho relevante (8-K)",
    "S-1": "📄 Registro de emisión (S-1)",
    "SC 13D": "🕵️ Participación significativa activista (13D)",
    "SC 13G": "🏦 Participación significativa pasiva (13G)",
    "DEF 14A": "🗳️ Convocatoria de junta (DEF 14A)",
    "4": "👤 Operación de directivo (Form 4)",
}


@functools.lru_cache(maxsize=1)
def _mapa_cik() -> dict:
    try:
        r = requests.get(_MAPA_TICKERS, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in r.json().values()}
    except Exception as e:
        log.warning("No se pudo descargar el mapa de CIKs de la SEC: %s", e)
        return {}


def cik_de(ticker: str) -> str | None:
    return _mapa_cik().get(ticker.upper())


def presentaciones(ticker: str, dias: int = DIAS_SEC_BOLETIN) -> list[dict]:
    """Presentaciones recientes del ticker en los últimos `dias`."""
    cik = cik_de(ticker)
    if not cik:
        return []
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=25)
        r.raise_for_status()
        recientes = r.json().get("filings", {}).get("recent", {})
    except Exception as e:
        log.warning("SEC %s: %s", ticker, e)
        return []

    formularios = recientes.get("form", [])
    fechas = recientes.get("filingDate", [])
    accesos = recientes.get("accessionNumber", [])
    docs = recientes.get("primaryDocument", [])
    limite = date.today() - timedelta(days=dias)

    out = []
    for i, form in enumerate(formularios[:150]):
        if form not in FORMULARIOS_SEC:
            continue
        try:
            f = datetime.strptime(fechas[i], "%Y-%m-%d").date()
        except Exception:
            continue
        if f < limite:
            continue
        acc = accesos[i].replace("-", "")
        doc = docs[i] if i < len(docs) else ""
        enlace = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}" if doc else \
                 f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"
        out.append({
            "id": f"sec:{ticker}:{accesos[i]}",
            "ticker": ticker,
            "form": form,
            "descripcion": DESCRIPCION.get(form, form),
            "fecha": f,
            "link": enlace,
            "urgente": form in FORMULARIOS_SEC_URGENTES,
        })
    return sorted(out, key=lambda x: x["fecha"], reverse=True)


def presentaciones_portafolio(tickers: list[str], dias: int = DIAS_SEC_BOLETIN) -> dict[str, list[dict]]:
    return {t: presentaciones(t, dias) for t in tickers}
