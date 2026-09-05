"""Persistencia mínima para evitar avisos duplicados entre ejecuciones."""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from config import ARCHIVO_ESTADO, TTL_DEDUP_HORAS

log = logging.getLogger(__name__)


class Estado:
    def __init__(self, ruta: str = ARCHIVO_ESTADO):
        self.ruta = ruta
        self.datos = {"vistos": {}, "ultimo_boletin": None}
        self._cargar()

    def _cargar(self):
        if os.path.exists(self.ruta):
            try:
                with open(self.ruta, encoding="utf-8") as f:
                    self.datos.update(json.load(f))
            except Exception as e:  # archivo corrupto -> empezamos de cero
                log.warning("No se pudo leer %s (%s). Se reinicia.", self.ruta, e)
        self._purgar()

    def _purgar(self):
        limite = datetime.now(timezone.utc) - timedelta(hours=TTL_DEDUP_HORAS)
        self.datos["vistos"] = {
            k: v for k, v in self.datos.get("vistos", {}).items()
            if _parse(v) and _parse(v) > limite
        }

    def ya_visto(self, clave: str) -> bool:
        return clave in self.datos["vistos"]

    def marcar(self, clave: str):
        self.datos["vistos"][clave] = datetime.now(timezone.utc).isoformat()

    def boletin_enviado_hoy(self, hoy: str) -> bool:
        return self.datos.get("ultimo_boletin") == hoy

    def marcar_boletin(self, hoy: str):
        self.datos["ultimo_boletin"] = hoy

    def guardar(self):
        try:
            tmp = self.ruta + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.datos, f, indent=2)
            os.replace(tmp, self.ruta)
        except Exception as e:
            log.warning("No se pudo guardar el estado: %s", e)


def _parse(valor):
    try:
        return datetime.fromisoformat(valor)
    except Exception:
        return None
