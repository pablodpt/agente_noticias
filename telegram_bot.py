"""Envío de mensajes a Telegram con troceado seguro y reintentos."""
import logging
import time

import requests

log = logging.getLogger(__name__)

LIMITE = 3900  # margen bajo el límite real de 4096 caracteres


def _trocear(mensaje: str) -> list[str]:
    """Parte el mensaje por líneas para no romper etiquetas HTML."""
    partes, actual = [], ""
    for linea in mensaje.split("\n"):
        if len(actual) + len(linea) + 1 > LIMITE:
            if actual:
                partes.append(actual)
            while len(linea) > LIMITE:      # línea gigantesca: corte duro
                partes.append(linea[:LIMITE])
                linea = linea[LIMITE:]
            actual = linea
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        partes.append(actual)
    return partes


def enviar_telegram(token: str, chat_id: str, mensaje: str,
                    vista_previa: bool = False) -> bool:
    if not token or not chat_id:
        log.error("Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for parte in _trocear(mensaje):
        for intento in range(3):
            try:
                resp = requests.post(url, json={
                    "chat_id": chat_id,
                    "text": parte,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": not vista_previa,
                }, timeout=20)
                if resp.status_code == 429:
                    espera = resp.json().get("parameters", {}).get("retry_after", 5)
                    time.sleep(espera + 1)
                    continue
                resp.raise_for_status()
                break
            except requests.RequestException as e:
                log.warning("Envío fallido (intento %s/3): %s", intento + 1, e)
                time.sleep(2 * (intento + 1))
        else:
            ok = False
        time.sleep(0.4)
    return ok


def probar_conexion(token: str, chat_id: str) -> bool:
    ok = enviar_telegram(token, chat_id,
                         "✅ <b>Agente de Portafolio</b> conectado correctamente.")
    log.info("Telegram OK" if ok else "Revisa TELEGRAM_TOKEN / TELEGRAM_CHAT_ID")
    return ok
