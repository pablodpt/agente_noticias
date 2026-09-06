"""Pruebas locales de los módulos solicitados, sin red ni credenciales."""
import tempfile
import unittest
from pathlib import Path

from bot import LIMITE, _trocear, enviar_telegram
from estado import Estado
from sentimiento import analizar


class ComponentesTest(unittest.TestCase):
    def test_bot_trocea_sin_superar_limite(self):
        partes = _trocear("línea\n" * 1000)
        self.assertGreater(len(partes), 1)
        self.assertTrue(all(len(parte) <= LIMITE for parte in partes))

    def test_bot_rechaza_credenciales_vacias_sin_llamar_a_red(self):
        self.assertFalse(enviar_telegram("", "", "prueba"))

    def test_estado_persiste_y_deduplica(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = str(Path(tmp) / "estado.json")
            estado = Estado(ruta)
            estado.marcar("evento-1")
            estado.marcar_boletin("2026-09-06")
            estado.guardar()

            restaurado = Estado(ruta)
            self.assertTrue(restaurado.ya_visto("evento-1"))
            self.assertTrue(restaurado.boletin_enviado_hoy("2026-09-06"))

    def test_sentimiento_lexico(self):
        self.assertEqual(analizar("Company beats estimates with strong growth")[0], "positive")
        self.assertEqual(analizar("Company misses estimates and cuts guidance")[0], "negative")
        self.assertEqual(analizar("")[0], "neutral")


if __name__ == "__main__":
    unittest.main()
