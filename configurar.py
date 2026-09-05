"""Asistente para crear el archivo .env en tu máquina.

Uso:

    python configurar.py             # te pregunta las claves y escribe .env
    python configurar.py --probar    # comprueba el .env actual mandando un mensaje
    python configurar.py --mostrar   # enseña qué hay cargado (ocultando el token)

El archivo .env queda en la raíz del repo, con permisos 600 y ignorado por git.
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import sys
from pathlib import Path

RUTA_ENV = Path(__file__).resolve().parent / ".env"
RUTA_EJEMPLO = Path(__file__).resolve().parent / ".env.example"

PLANTILLA = """# Credenciales del agente (generado por configurar.py)
TELEGRAM_TOKEN={token}
TELEGRAM_CHAT_ID={chat_id}
SEC_USER_AGENT={sec_agent}
TZ_USUARIO={tz}

# --- Opcional: descomenta y ajusta ---
# UNIVERSO=sp500
# TOP_DIARIO=8
# TOP_SEMANAL=15
# PESOS_ESTRATEGIA=momentum:0.30,reversion:0.25,valor:0.25,catalizador:0.20
# UMBRAL_FINAL=50
# TTL_OPORTUNIDAD_HORAS=96
"""

RE_TOKEN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
RE_CHAT = re.compile(r"^-?\d{5,20}$")


# ------------------------------------------------------------------ Utilidades --

def _enmascarar(valor: str | None) -> str:
    if not valor:
        return "(vacío)"
    if len(valor) <= 10:
        return "*" * len(valor)
    return f"{valor[:5]}…{valor[-4:]} ({len(valor)} caracteres)"


def leer_env_actual() -> dict[str, str]:
    """Lee el .env existente para no perder lo que ya estuviera puesto."""
    datos: dict[str, str] = {}
    if not RUTA_ENV.exists():
        return datos
    for linea in RUTA_ENV.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        datos[clave.strip()] = valor.strip().strip('"').strip("'")
    return datos


def _preguntar(texto: str, actual: str = "", oculto: bool = False,
               obligatorio: bool = True, validar=None) -> str:
    """Pregunta por consola con el valor actual como defecto."""
    pista = f" [{_enmascarar(actual) if oculto else actual or 'vacío'}]" if actual else ""
    while True:
        try:
            respuesta = input(f"{texto}{pista}: ").strip()
        except EOFError:
            print()
            return actual
        if not respuesta:
            respuesta = actual
        if not respuesta and not obligatorio:
            return ""
        if not respuesta:
            print("   ⚠️  Este dato es necesario.")
            continue
        if validar and not validar(respuesta):
            print("   ⚠️  El formato no parece correcto. Ejemplo: "
                  f"{validar.__doc__ or ''}")
            continue
        return respuesta


def _val_token(v: str) -> bool:
    "123456789:AAF..."
    return bool(RE_TOKEN.match(v))


def _val_chat(v: str) -> bool:
    "123456789"
    return bool(RE_CHAT.match(v))


# ---------------------------------------------------------------------- Acciones --

def configurar() -> int:
    if not sys.stdin.isatty():
        print("Este asistente es interactivo: ejecútalo en una terminal "
              "(python configurar.py).")
        print("Si quieres traerte las claves desde GitHub, lanza el workflow "
              "«Generar .env para uso local» desde la pestaña Actions.")
        return 1

    actual = leer_env_actual()
    print()
    print("🔧 Configuración del agente")
    print("─" * 60)
    if actual:
        print(f"Ya existe un .env con {len(actual)} claves; pulsa Enter para "
              "mantener cada valor.\n")
    print("Cómo conseguir los datos:")
    print("  · Token      → @BotFather en Telegram → /newbot")
    print("  · Chat id    → @userinfobot (y recuerda escribirle algo a tu bot)")
    print("  · SEC agent  → la SEC exige un correo; pon el tuyo\n")

    token = _preguntar("Token del bot de Telegram", actual.get("TELEGRAM_TOKEN", ""),
                       oculto=True, validar=_val_token)
    chat = _preguntar("Tu chat id de Telegram", actual.get("TELEGRAM_CHAT_ID", ""),
                      validar=_val_chat)
    sec = _preguntar("Contacto para la SEC (nombre y correo)",
                     actual.get("SEC_USER_AGENT", "agente-noticias tu-email@dominio.com"))
    tz = _preguntar("Zona horaria", actual.get("TZ_USUARIO", "Europe/Madrid"))

    contenido = PLANTILLA.format(token=token, chat_id=chat, sec_agent=sec, tz=tz)

    # Conserva cualquier clave extra que el usuario tuviera en su .env
    extra = {k: v for k, v in actual.items()
             if k not in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "SEC_USER_AGENT",
                          "TZ_USUARIO") and v}
    if extra:
        contenido += "\n# --- Claves que ya tenías en tu .env ---\n"
        contenido += "".join(f"{k}={v}\n" for k, v in extra.items())

    RUTA_ENV.write_text(contenido, encoding="utf-8")
    try:
        os.chmod(RUTA_ENV, stat.S_IRUSR | stat.S_IWUSR)      # 600
    except OSError:
        pass
    print(f"\n✅ Escrito {RUTA_ENV} (permisos 600, ignorado por git).")

    try:
        from dotenv import load_dotenv
        load_dotenv(RUTA_ENV, override=True)
    except Exception:
        pass

    if input("\n¿Enviamos un mensaje de prueba a Telegram? [S/n]: ").strip().lower() \
            not in ("n", "no"):
        return probar()
    return 0


def probar() -> int:
    """Comprueba que el .env actual funciona mandando un mensaje."""
    from dotenv import load_dotenv
    load_dotenv(RUTA_ENV, override=True)
    token = os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("❌ Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en el .env.")
        return 1
    from telegram_bot import probar_conexion
    ok = probar_conexion(token, chat)
    print("✅ Mensaje enviado: revisa Telegram." if ok
          else "❌ No se ha podido enviar. Revisa el token y el chat id.")
    return 0 if ok else 1


def mostrar() -> int:
    from dotenv import load_dotenv
    if not RUTA_ENV.exists():
        print("No hay .env todavía. Crea uno con: python configurar.py")
        return 1
    load_dotenv(RUTA_ENV, override=True)
    print(f"\nArchivo: {RUTA_ENV}")
    for clave in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "SEC_USER_AGENT",
                  "TZ_USUARIO", "UNIVERSO", "TOP_DIARIO", "TOP_SEMANAL"):
        valor = os.getenv(clave)
        oculto = clave in ("TELEGRAM_TOKEN",)
        print(f"  {clave:<18} {_enmascarar(valor) if oculto else (valor or '(vacío)')}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Crea y comprueba el archivo .env")
    ap.add_argument("--probar", action="store_true", help="Enviar mensaje de prueba")
    ap.add_argument("--mostrar", action="store_true", help="Mostrar la configuración")
    args = ap.parse_args()

    if args.probar:
        sys.exit(probar())
    if args.mostrar:
        sys.exit(mostrar())
    sys.exit(configurar())


if __name__ == "__main__":
    main()
