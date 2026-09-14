from ucits.fondos import universo
from ucits.motor import cargar_screener, construir_portafolio, filtrar
from ucits.scoring import puntuar
from ucits.metricas import metricas_estimadas


def test_universo_solo_ucits_no_etf():
    u = universo()
    assert len(u) >= 70
    assert all(f["ucits"] and not f["etf"] for f in u)
    isins = [f["isin"] for f in u]
    assert len(isins) == len(set(isins))
    clases = {f["clase_activo"] for f in u}
    assert {"monetario", "renta_fija", "renta_variable", "oro"} <= clases


def test_screener_scores():
    data = cargar_screener()
    assert data["resumen"]["n"] == len(data["fondos"])
    f = data["fondos"][0]
    assert "score_final" in f and "ter" in f and "sharpe_3y" in f
    assert all(0 <= x["score_calidad"] <= 100 for x in data["fondos"])


def test_portafolio_suma_100():
    pf = construir_portafolio("equilibrado", "equilibrado")
    s = round(sum(p["peso"] for p in pf["posiciones"]), 1)
    assert 99.5 <= s <= 100.5
    assert pf["ter_ponderado"] > 0
    assert pf["n"] >= 5
    assert max(p["peso"] for p in pf["posiciones"]) <= 29
    # sin ETF
    assert all(p["isin"].startswith(("LU", "IE", "FR", "ES", "DE", "GB", "FI")) for p in pf["posiciones"])


def test_filtro_ter():
    data = cargar_screener()
    baratos = filtrar(data["fondos"], max_ter=0.3)
    assert baratos
    assert all(f["ter"] <= 0.3 for f in baratos)


def test_metricas_mm_bajo_dd():
    mm = next(f for f in universo() if f["clase_activo"] == "monetario")
    m = metricas_estimadas(mm)
    assert abs(m["max_dd"]) < 5
    oro = next(f for f in universo() if f["clase_activo"] == "oro")
    mo = metricas_estimadas(oro)
    assert abs(mo["max_dd"]) > abs(m["max_dd"])


def test_puntuar_idempotente():
    data = cargar_screener()
    a = [f["score_calidad"] for f in data["fondos"]]
    puntuar(data["fondos"])
    b = [f["score_calidad"] for f in data["fondos"]]
    assert a == b
