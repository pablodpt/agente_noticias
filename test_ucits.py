import csv
import tempfile
import unittest
from pathlib import Path

from ucits import CARTERA_MODELO, construir_informe_markdown, exportar_csvs, validar_modelo


class UcitsModelTest(unittest.TestCase):
    def test_pesos_y_bloques(self):
        validar_modelo()
        self.assertEqual(sum(p.peso for p in CARTERA_MODELO), 100)
        self.assertEqual(sum(p.peso for p in CARTERA_MODELO if p.bloque == "Core"), 68)
        self.assertEqual(sum(p.peso for p in CARTERA_MODELO if p.bloque == "Satélite"), 24)
        self.assertEqual(sum(p.peso for p in CARTERA_MODELO if p.bloque == "Temático"), 8)

    def test_no_hay_productos_ni_etf_en_la_cartera(self):
        for posicion in CARTERA_MODELO:
            self.assertIn("UCITS", posicion.categoria)
            self.assertNotIn("ETF", posicion.categoria.upper())

    def test_informe_contiene_las_tres_tablas_y_aviso_fiscal(self):
        informe = construir_informe_markdown()
        self.assertIn("Tabla 1 — Diseño de cartera", informe)
        self.assertIn("Tabla 2 — Tipos de fondos", informe)
        self.assertIn("Tabla 3 — Checklist", informe)
        self.assertIn("UCITS, por sí sola, no garantiza", informe)
        self.assertIn("No es asesoramiento personalizado", informe)

    def test_csv_compatible_con_excel(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = exportar_csvs(tmp)
            self.assertEqual(len(paths), 3)
            for path in paths:
                self.assertTrue(path.exists())
                with path.open(encoding="utf-8-sig", newline="") as fh:
                    rows = list(csv.reader(fh, delimiter=";"))
                self.assertGreater(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
