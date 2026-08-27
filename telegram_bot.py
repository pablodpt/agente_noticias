import requests

def enviar_telegram(token: str, chat_id: str, mensaje: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    while mensaje:
        fragmento, mensaje = mensaje[:4000], mensaje[4000:]
        try:
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": fragmento,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"❌ Error enviando a Telegram: {e}")
            return False
    return True


def probar_conexion(token: str, chat_id: str) -> bool:
    ok = enviar_telegram(token, chat_id, "✅ <b>Agente de Noticias Financieras</b> conectado.")
    print("✅ Telegram OK" if ok else "❌ Revisa token/chat_id")
    return ok
