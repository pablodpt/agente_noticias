"""Asistente para crear el archivo .env en tu máquina.

Uso:

    python configurar.py                 # te pregunta las claves y escribe .env
    python configurar.py --desde-github  # las baja de tus secrets de GitHub (usa gh)
    python configurar.py --probar        # comprueba el .env actual mandando un mensaje
    python configurar.py --mostrar       # enseña qué hay cargado (ocultando el token)

El archivo .env queda en la raíz del repo, con permisos 600 y ignorado por git.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
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


# ------------------------------------------------- Bajarse las claves de GitHub --

WORKFLOW_ENV = "generar-env.yml"
ARTEFACTO_ENV = "env-local"


def _gh(args: list[str], capturar: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=capturar, text=True)


def _slug() -> str | None:
    """owner/repo leyendo el remoto de git."""
    r = subprocess.run(["git", "config", "--get", "remote.origin.url"],
                       capture_output=True, text=True)
    url = (r.stdout or "").strip()
    if not url:
        r = _gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
        return (r.stdout or "").strip() or None
    url = url.rstrip("/").removesuffix(".git")
    if url.startswith("git@"):
        url = url.split(":", 1)[-1]
    for prefijo in ("https://github.com/", "http://github.com/", "ssh://git@github.com/"):
        if url.startswith(prefijo):
            url = url[len(prefijo):]
    return url or None


def _rama_actual() -> str | None:
    r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                       capture_output=True, text=True)
    return (r.stdout or "").strip() or None


def _nueva_ejecucion(slug: str, desde: datetime, intentos: int = 24) -> str | None:
    """Espera a que aparezca la ejecución que acabamos de lanzar."""
    for _ in range(intentos):
        r = _gh(["run", "list", "--workflow", WORKFLOW_ENV, "--repo", slug,
                 "--limit", "5", "--json", "databaseId,createdAt"])
        try:
            runs = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            runs = []
        for run in runs:
            try:
                creado = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
            except Exception:
                continue
            if creado >= desde:
                return str(run["databaseId"])
        time.sleep(2.5)
    return None


def _esperar_fin(slug: str, run_id: str, timeout: int = 300) -> tuple[str, str]:
    """Devuelve (status, conclusión) cuando la ejecución termina."""
    final = time.time() + timeout
    while time.time() < final:
        r = _gh(["run", "view", run_id, "--repo", slug, "--json", "status,conclusion"])
        try:
            datos = json.loads(r.stdout or "{}")
        except json.JSONDecodeError:
            datos = {}
        status = (datos.get("status") or "").lower()
        conclusion = (datos.get("conclusion") or "").lower()
        if status == "completed":
            return status, conclusion
        time.sleep(5)
    return "timeout", ""


def _borrar_artefacto(slug: str) -> int:
    r = _gh(["api", "--paginate", f"repos/{slug}/actions/artifacts",
             "--jq", f'.artifacts[] | select(.name=="{ARTEFACTO_ENV}") | .id'])
    ids = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
    for id_artefacto in ids:
        _gh(["api", "-X", "DELETE", f"repos/{slug}/actions/artifacts/{id_artefacto}"])
    return len(ids)


def desde_github(borrar: bool | None = None) -> int:
    """Lanza el workflow 'Generar .env para uso local' y se trae el .env."""
    print("\n🔑 Descargar las claves desde los secrets de GitHub\n")

    if shutil.which("gh") is None:
        print("❌ No encuentro el CLI de GitHub (`gh`). Instálalo: https://cli.github.com")
        print("   o crea el .env a mano con: python configurar.py")
        return 1
    if _gh(["auth", "status"]).returncode != 0:
        print("❌ `gh` no está autenticado. Ejecuta antes:  gh auth login")
        return 1

    slug = _slug()
    if not slug:
        print("❌ No consigo saber el repositorio (owner/repo).")
        return 1
    print(f"Repositorio: {slug}")

    rama = _rama_actual()
    desde = datetime.now(timezone.utc).replace(microsecond=0)
    comando = ["workflow", "run", WORKFLOW_ENV, "--repo", slug]
    if rama:
        comando += ["--ref", rama]
    r = _gh(comando)
    if r.returncode != 0 and rama:                # reintento en la rama por defecto
        r = _gh(["workflow", "run", WORKFLOW_ENV, "--repo", slug])
    if r.returncode != 0:
        print("❌ No se ha podido lanzar el workflow. Detalle:")
        print("   " + (r.stderr or "").strip().replace("\n", "\n   "))
        print("\n   Motivo habitual: la API de GitHub solo puede lanzar workflows")
        print("   que existan en la rama por defecto del repo (normalmente main).")
        print("   Opciones:")
        print("     · haz merge/PR de tu rama a main y vuelve a intentarlo, o")
        print("     · lánzalo a mano desde la pestaña Actions (ahí sí puedes")
        print("       elegir la rama) y descarga el artefacto 'env-local'.")
        return 1
    print("▶️  Workflow lanzado, esperando a que arranque...")

    run_id = _nueva_ejecucion(slug, desde)
    if not run_id:
        print("❌ No aparece la ejecución. Revísalo a mano en la pestaña Actions.")
        return 1
    print(f"⏳ Ejecución {run_id} en curso (suele tardar menos de un minuto)...")

    status, conclusion = _esperar_fin(slug, run_id)
    if status == "timeout":
        print("❌ La ejecución tarda demasiado. Mírala en la pestaña Actions.")
        return 1
    if conclusion != "success":
        print(f"❌ El workflow terminó con conclusión «{conclusion or 'desconocida'}».")
        print("   Causa más probable: faltan los secrets TELEGRAM_TOKEN o "
              "TELEGRAM_CHAT_ID en el repo.")
        print("   Míralo en Actions y, si faltan, créalos en Settings → Secrets "
              "and variables → Actions.")
        return 1

    temporal = tempfile.mkdtemp(prefix="env-local-")
    r = _gh(["run", "download", run_id, "--repo", slug, "-n", ARTEFACTO_ENV,
             "-D", temporal])
    if r.returncode != 0:
        print("❌ No se ha podido descargar el artefacto:")
        print("   " + (r.stderr or "").strip())
        return 1

    enviados = list(Path(temporal).rglob(".env"))
    if not enviados:
        print(f"❌ El artefacto no contenía ningún .env (descargado en {temporal}).")
        return 1

    contenido = enviados[0].read_text(encoding="utf-8")
    if "TELEGRAM_TOKEN=" not in contenido:
        print("❌ El .env descargado no trae TELEGRAM_TOKEN. Revisa los secrets.")
        return 1

    RUTA_ENV.write_text(contenido, encoding="utf-8")
    try:
        os.chmod(RUTA_ENV, stat.S_IRUSR | stat.S_IWUSR)      # 600
    except OSError:
        pass
    shutil.rmtree(temporal, ignore_errors=True)

    lineas = [l for l in contenido.splitlines() if l.strip() and not l.startswith("#")]
    print(f"✅ .env instalado en {RUTA_ENV} (permisos 600) con {len(lineas)} claves.")

    if borrar is None:
        borrar = sys.stdin.isatty() and input(
            "\n¿Borramos el artefacto de GitHub? Contiene tus claves en texto "
            "plano. [S/n]: ").strip().lower() not in ("n", "no")
    if borrar:
        n = _borrar_artefacto(slug)
        print(f"🗑️  {'Artefacto borrado.' if n else 'No quedaba artefacto que borrar.'}")
    else:
        print("⚠️  Recuerda borrar el artefacto «env-local» en la pestaña Actions.")

    print("\nSiguiente paso:  python configurar.py --probar")
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
    ap.add_argument("--desde-github", action="store_true",
                    help="Lanza el workflow y se trae el .env de tus secrets (usa gh)")
    ap.add_argument("--probar", action="store_true", help="Enviar mensaje de prueba")
    ap.add_argument("--mostrar", action="store_true", help="Mostrar la configuración")
    args = ap.parse_args()

    if args.desde_github:
        sys.exit(desde_github())
    if args.probar:
        sys.exit(probar())
    if args.mostrar:
        sys.exit(mostrar())
    sys.exit(configurar())


if __name__ == "__main__":
    main()
