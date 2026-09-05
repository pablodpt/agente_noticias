"""Los cuatro motores de detección de oportunidades.

Cada motor mira el mercado con una lente distinta y devuelve una ``Senal``
con una puntuación de 0 a 100, las razones que la explican y sus riesgos:

  * **momentum**    – tendencia y fuerza relativa: comprar lo que ya sube.
  * **reversion**   – caídas exageradas dentro de una tendencia alcista.
  * **valor**       – buenas empresas a precio razonable (evitando trampas).
  * **catalizador** – noticias, resultados, presentaciones SEC y mejoras de
                      analistas que pueden mover el valor a corto plazo.

Ninguno de los cuatro "acierta" por sí solo: la puntuación final del escáner es
la media ponderada de los motores que hayan dado señal.
"""
from dataclasses import dataclass, field

from config import (DIAS_AVISO_EARNINGS, DIAS_CATALIZADOR_EARNINGS, UMBRAL_SENAL,
                    VOLATILIDAD_ALTA)


@dataclass
class Candidato:
    """Todo lo que un motor necesita saber de un valor."""
    ticker: str
    nombre: str
    sector: str
    ind: dict
    fund: dict | None = None
    rel: dict | None = None          # percentiles dentro del sector (1.0 = mejor)
    noticias: list[dict] = field(default_factory=list)
    filings: list[dict] = field(default_factory=list)
    upgrades: list[dict] = field(default_factory=list)
    earnings: dict | None = None


@dataclass
class Senal:
    ticker: str
    motor: str
    puntos: float                    # 0-100
    confianza: float                 # 0-1
    razones: list[str] = field(default_factory=list)
    riesgos: list[str] = field(default_factory=list)
    horizonte: str = ""
    etiqueta: str = ""

    def __post_init__(self):
        self.puntos = _limite(self.puntos, 0, 100)
        self.confianza = _limite(self.confianza, 0.05, 0.95)


MOTORES = {
    "momentum": ("Momentum", "📈", "1-3 meses"),
    "reversion": ("Sobreventa", "🎯", "2-8 semanas"),
    "valor": ("Valor y calidad", "💎", "6-18 meses"),
    "catalizador": ("Catalizador", "⚡", "días-2 meses"),
}


# ------------------------------------------------------------------ Utilidades --

def _limite(x, bajo: float, alto: float) -> float:
    try:
        return max(bajo, min(alto, float(x)))
    except (TypeError, ValueError):
        return bajo


def _num(valor):
    """Devuelve float o None (nunca NaN)."""
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    return None if v != v or v in (float("inf"), float("-inf")) else v


def _escala(valor, lo: float, hi: float) -> float:
    """Normaliza `valor` entre 0 y 1 dentro del rango [lo, hi]."""
    v = _num(valor)
    if v is None or hi == lo:
        return 0.0
    return _limite((v - lo) / (hi - lo), 0, 1)


def _campana(valor, centro: float, ancho: float) -> float:
    """1.0 cuando `valor` == centro, cayendo a 0 a `ancho` de distancia."""
    v = _num(valor)
    if v is None:
        return 0.0
    return _limite(1 - abs(v - centro) / ancho, 0, 1)


def _media_pct(rel: dict | None, campos: list[str]) -> float | None:
    """Media de percentiles disponibles (1.0 = mejor)."""
    if not rel:
        return None
    valores = [_num(rel.get(c)) for c in campos]
    valores = [v for v in valores if v is not None]
    return sum(valores) / len(valores) if valores else None


# -------------------------------------------------------------------- MOMENTUM --

def momentum(c: Candidato) -> Senal | None:
    ind = c.ind
    if not ind:
        return None
    if not (ind.get("sobre_sma200") or ind.get("ruptura_20d")):
        return None                      # sin tendencia de fondo, no hay momentum

    razones, riesgos, puntos = [], [], 0.0

    # --- Tendencia alineada (30) ---
    tendencia = 0.0
    if ind.get("sobre_sma20"):
        tendencia += 1
        razones.append("Precio por encima de su media de 20 sesiones")
    if ind.get("sma20_sobre_sma50"):
        tendencia += 1
        razones.append("Media de 20 por encima de la de 50")
    if ind.get("sma50_sobre_sma200") and ind.get("sobre_sma200"):
        tendencia += 1
        razones.append("Tendencia de largo plazo alcista (precio > SMA50 > SMA200)")
    puntos += 10 * tendencia

    # --- Momento 12-1 meses (25) ---
    r252, r21 = _num(ind.get("ret_252d")), _num(ind.get("ret_21d"))
    momento = None
    if r252 is not None:
        momento = (r252 - r21) if r21 is not None else r252
        puntos += 15 * _escala(momento, -5, 60)
        if momento > 15:
            razones.append(f"Momentum 12-1 meses {momento:+.0f}%")
    r126 = _num(ind.get("ret_126d"))
    if r126 is not None:
        puntos += 10 * _escala(r126, -5, 40)
        if r126 > 10:
            razones.append(f"Sube un {r126:+.0f}% en los últimos 6 meses")

    # --- Fuerza relativa frente al benchmark (25) ---
    rs126 = _num(ind.get("rs_126d"))
    if rs126 is not None:
        puntos += 15 * _escala(rs126, -5, 30)
        if rs126 > 5:
            razones.append(f"Bate al S&P 500 por {rs126:+.0f} puntos en 6 meses")
    rs63 = _num(ind.get("rs_63d"))
    if rs63 is not None:
        puntos += 10 * _escala(rs63, -3, 18)

    # --- Ruptura con volumen (12) ---
    vol_ratio = _num(ind.get("vol_ratio")) or 0
    if ind.get("ruptura_55d"):
        puntos += 7
        razones.append("Máximo de 55 sesiones")
    elif ind.get("ruptura_20d"):
        puntos += 5
        razones.append("Máximo de 20 sesiones")
    if vol_ratio >= 1.5:
        puntos += 5
        razones.append(f"Volumen {vol_ratio:.1f}× lo normal")

    # --- Cercanía a máximos (8) ---
    dist = _num(ind.get("dist_max52_pct"))
    if dist is not None:
        puntos += 8 * _escala(dist, -20, -2)
        if dist > -5:
            razones.append("Cerca de su máximo de 52 semanas")
        elif dist < -25:
            riesgos.append(f"Aún a {dist:.0f}% de sus máximos")

    # --- Penalizaciones ---
    rsi = _num(ind.get("rsi14"))
    if rsi is not None and rsi > 78:
        puntos -= 8
        riesgos.append(f"Sobrecompra (RSI {rsi:.0f})")
    ext = _num(ind.get("dist_sma200_pct"))
    if ext is not None and ext > 60:
        puntos -= 6
        riesgos.append(f"Muy estirado sobre su media de 200 ({ext:+.0f}%)")
    vol = _num(ind.get("volatilidad"))
    if vol is not None and vol > VOLATILIDAD_ALTA:
        puntos -= 5
        riesgos.append(f"Volatilidad alta ({vol:.0f}% anualizada)")

    if puntos < UMBRAL_SENAL or tendencia == 0:
        return None

    confianza = 0.35 + 0.12 * tendencia + (0.1 if ind.get("ruptura_20d") else 0) \
        + (0.08 if (rs126 or 0) > 5 else 0)
    etiqueta, _, horizonte = MOTORES["momentum"]
    return Senal(c.ticker, "momentum", puntos, confianza, razones, riesgos, horizonte, etiqueta)


# ------------------------------------------------------------------- REVERSIÓN --

def reversion(c: Candidato) -> Senal | None:
    """Cazando rebotes: caída fuerte + sobreventa, pero con tendencia de fondo alcista."""
    ind = c.ind
    if not ind:
        return None
    # Filtro duro: si está por debajo de la media de 200, es un cuchillo cayendo.
    if not ind.get("sobre_sma200"):
        return None

    razones, riesgos, puntos = [], [], 0.0

    # --- Sobreventa (35) ---
    rsi = _num(ind.get("rsi14"))
    if rsi is not None:
        puntos += 30 * _escala(45 - rsi, 0, 18)
        if rsi < 35:
            razones.append(f"Sobreventa (RSI {rsi:.0f})")
    rsi2 = _num(ind.get("rsi2"))
    if rsi2 is not None and rsi2 < 10:
        puntos += 5
        razones.append("Sobreventa extrema de muy corto plazo")

    # --- Desviación respecto a su media (25) ---
    z = _num(ind.get("z_sma20"))
    if z is not None and z < 0:
        puntos += 25 * _escala(-z, 0.5, 2.5)
        if z < -1.5:
            razones.append(f"A {abs(z):.1f} desviaciones de su media de 20 sesiones")
    if not ind.get("sobre_sma20"):
        razones.append("Por debajo de su media de 20 sesiones")
        riesgos.append("La media de corto plazo aún no ha girado")

    # --- Tamaño de la caída (20): el punto dulce está en torno al -20% ---
    dd = _num(ind.get("dist_max52_pct"))
    if dd is not None and dd < -8:
        puntos += 20 * _campana(dd, -22, 22)
        if -35 < dd < -12:
            razones.append(f"Descuento del {abs(dd):.0f}% desde máximos")
        elif dd <= -35:
            riesgos.append(f"Caída profunda del {abs(dd):.0f}% desde máximos")

    # --- Señal de giro (10) ---
    pos = _num(ind.get("cierre_en_rango_alto"))
    if pos is not None:
        if pos >= 0.6:
            puntos += 10
            razones.append("Cerró en la parte alta del rango del día (presión compradora)")
        elif pos >= 0.4:
            puntos += 5

    # --- Volumen de capitulación (5) ---
    vol_ratio = _num(ind.get("vol_ratio")) or 0
    if vol_ratio >= 1.8:
        puntos += 5
        razones.append(f"Volumen {vol_ratio:.1f}× lo normal (posible capitulación)")

    # --- Calidad: que la empresa no sea un desastre (5) ---
    fund = c.fund or {}
    roe = _num(fund.get("roe"))
    margen = _num(fund.get("margen_neto"))
    if roe is not None:
        if roe > 15:
            puntos += 5
            razones.append(f"Calidad intacta (ROE {roe:.0f}%)")
        elif roe > 0:
            puntos += 3
    elif margen is not None and margen > 0:
        puntos += 2
    if roe is not None and roe < 0:
        riesgos.append("La empresa pierde dinero")
    deuda = _num(fund.get("deuda_capital"))
    if deuda is not None and deuda > 250:
        riesgos.append(f"Deuda elevada ({deuda:.0f}% sobre capital)")

    # --- Riesgo de resultados próximos ---
    if c.earnings:
        dias = c.earnings.get("dias")
        if dias is not None and dias <= DIAS_AVISO_EARNINGS:
            riesgos.append(f"Presenta resultados en {dias} días")
            puntos -= 5

    if puntos < UMBRAL_SENAL:
        return None
    if not razones:
        return None

    confianza = 0.3 + 0.2 * _escala(35 - (rsi or 40), 0, 20) + \
        (0.1 if (z is not None and z < -1.5) else 0)
    etiqueta, _, horizonte = MOTORES["reversion"]
    return Senal(c.ticker, "reversion", puntos, confianza, razones, riesgos, horizonte, etiqueta)


# ------------------------------------------------------------ VALOR Y CALIDAD --

def valor(c: Candidato) -> Senal | None:
    """Buenas empresas baratas, con filtro anti-trampa-de-valor."""
    fund = c.fund
    if not fund:
        return None
    ind = c.ind or {}
    rel = c.rel or {}
    razones, riesgos, puntos = [], [], 0.0
    usados = 0

    # --- Valoración (40) ---
    pct_valor = _media_pct(rel, ["pe_fwd_pct", "pe_pct", "peg_pct", "ps_pct",
                                 "pb_pct", "ev_ebitda_pct", "fcf_yield_pct"])
    if pct_valor is not None:
        puntos += 40 * pct_valor
        if pct_valor > 0.7:
            razones.append("De lo más barato de su sector por múltiplos")
        elif pct_valor > 0.5:
            razones.append("Múltiplos por debajo de la media de su sector")
        usados += 1
    else:
        pe_fwd = _num(fund.get("pe_fwd")) or _num(fund.get("pe"))
        if pe_fwd and pe_fwd > 0:
            p = _escala(30 - pe_fwd, 0, 22)
            puntos += 30 * p
            if pe_fwd < 15:
                razones.append(f"PER de {pe_fwd:.1f} veces beneficio")
            usados += 1
        fcf_yield = _num(fund.get("fcf_yield"))
        if fcf_yield is not None:
            puntos += 10 * _escala(fcf_yield, 0, 12)
            if fcf_yield > 7:
                razones.append(f"Rentabilidad por flujo de caja libre del {fcf_yield:.1f}%")
            usados += 1

    # --- Calidad (30) ---
    pct_calidad = _media_pct(rel, ["roe_pct", "roa_pct", "margen_operativo_pct",
                                   "margen_neto_pct", "deuda_capital_pct", "liquidez_pct"])
    if pct_calidad is not None:
        puntos += 30 * pct_calidad
        if pct_calidad > 0.7:
            razones.append("Rentabilidad y balance entre los mejores del sector")
        usados += 1
    else:
        roe = _num(fund.get("roe"))
        if roe is not None:
            p = _escala(roe, 0, 25)
            puntos += 20 * p
            if roe > 15:
                razones.append(f"ROE del {roe:.0f}%")
            usados += 1
        deuda = _num(fund.get("deuda_capital"))
        if deuda is not None:
            puntos += 10 * _escala(200 - deuda, 0, 200)

    # --- Crecimiento (20) ---
    pct_crecimiento = _media_pct(rel, ["crecimiento_ingresos_pct", "crecimiento_beneficios_pct"])
    if pct_crecimiento is not None:
        puntos += 20 * pct_crecimiento
        if pct_crecimiento > 0.7:
            razones.append("Crecimiento por encima de sus comparables")
    else:
        ci = _num(fund.get("crecimiento_ingresos"))
        cb = _num(fund.get("crecimiento_beneficios"))
        if ci is not None:
            puntos += 12 * _escala(ci, 0, 20)
            if ci > 8:
                razones.append(f"Ingresos creciendo al {ci:.0f}%")
        if cb is not None:
            puntos += 8 * _escala(cb, 0, 25)

    # --- Confirmación por precio (10): sin esto, cuidado con las trampas ---
    rs126 = _num(ind.get("rs_126d"))
    if rs126 is not None:
        puntos += 10 * _escala(rs126, -20, 10)
        # Una cotización muy castigada suele ser una trampa de valor: lo barato
        # puede seguir bajando. Se penaliza para que no baste con los múltiplos.
        if rs126 < -25:
            puntos -= 12
            riesgos.append(f"El mercado lo castiga con fuerza ({rs126:+.0f} pp vs S&P 500)")
        elif rs126 < -10:
            puntos -= 6
            riesgos.append(f"Va por detrás del mercado ({rs126:+.0f} pp vs S&P 500)")
    if ind.get("sobre_sma200") is False:
        puntos -= 5
        riesgos.append("Cotiza por debajo de su media de 200 sesiones (tendencia bajista)")

    # --- Extras ---
    potencial = _num(fund.get("potencial_pct"))
    if potencial is not None and potencial > 15:
        puntos += min(potencial - 15, 8)
        razones.append(f"Los analistas ven un {potencial:.0f}% de recorrido")
    dy = _num(fund.get("dividendo_yield"))
    payout = _num(fund.get("payout"))
    if dy and dy > 3 and (payout is None or payout < 80):
        puntos += 3
        razones.append(f"Dividendo sostenible del {dy:.1f}%")

    # --- Penalizaciones ---
    pe = _num(fund.get("pe")) or _num(fund.get("pe_fwd"))
    if pe is None or pe <= 0:
        puntos -= 12
        riesgos.append("No tiene beneficios positivos")
    deuda = _num(fund.get("deuda_capital"))
    if deuda is not None and deuda > 250:
        puntos -= 8
        riesgos.append(f"Apalancamiento alto ({deuda:.0f}% deuda/capital)")
    rec = str(fund.get("recomendacion") or "").lower()
    if rec in ("sell", "strong_sell", "underperform"):
        puntos -= 10
        riesgos.append("El consenso de analistas es negativo")

    if usados == 0 or puntos < UMBRAL_SENAL:
        return None

    confianza = 0.45 + 0.1 * usados + (0.08 if (rs126 or -99) > 0 else 0)
    etiqueta, _, horizonte = MOTORES["valor"]
    return Senal(c.ticker, "valor", puntos, confianza, razones, riesgos, horizonte, etiqueta)


# ---------------------------------------------------------------- CATALIZADOR --

def catalizador(c: Candidato) -> Senal | None:
    """Noticias, resultados, presentaciones SEC y movimientos de analistas."""
    ind = c.ind or {}
    razones, riesgos, puntos = [], [], 0.0

    # --- Resultados próximos ---
    if c.earnings:
        dias = c.earnings.get("dias")
        if dias is not None and dias <= DIAS_CATALIZADOR_EARNINGS:
            puntos += 20 if dias > 1 else 25
            cuando = "hoy" if dias == 0 else ("mañana" if dias == 1 else f"en {dias} días")
            razones.append(f"Presenta resultados {cuando}")
            riesgos.append("Los resultados son una apuesta binaria: mucha volatilidad")

    # --- Noticias recientes ---
    pos = sum(1 for n in c.noticias if n.get("sentimiento") == "positive")
    neg = sum(1 for n in c.noticias if n.get("sentimiento") == "negative")
    urgentes = [n for n in c.noticias if n.get("urgente")]
    if pos:
        mejor = max([_num(n.get("score")) or 0 for n in c.noticias
                     if n.get("sentimiento") == "positive"])
        puntos += min(12 * pos + 8 * mejor, 30)
        razones.append(f"{pos} noticia(s) con sesgo alcista")
    if urgentes:
        puntos += min(5 * len(urgentes), 10)
        titulo = urgentes[0]["titulo"][:80]
        razones.append(f"Titular de impacto: «{titulo}»")
    if neg >= 2:
        puntos -= 10
        riesgos.append(f"{neg} noticias con sesgo bajista")
    if not c.noticias:
        riesgos.append("Sin noticias recientes que respalden el movimiento")

    # --- Analistas ---
    mejoras = [u for u in c.upgrades if str(u.get("accion", "")).lower() in
               ("up", "upgrade", "init", "initiated", "main", "reit", "reiterated")]
    recortes = [u for u in c.upgrades if str(u.get("accion", "")).lower() in
                ("down", "downgrade")]
    if mejoras:
        puntos += min(12 + 6 * len(mejoras), 22)
        razones.append(f"{len(mejoras)} mejora(s) reciente(s) de analistas")
    if recortes:
        puntos -= min(10 + 5 * len(recortes), 20)
        riesgos.append(f"{len(recortes)} recorte(s) reciente(s) de analistas")

    # --- Presentaciones SEC relevantes ---
    for f in c.filings[:4]:
        forma = str(f.get("form", ""))
        if forma == "8-K":
            puntos += 8
            razones.append("Hecho relevante (8-K) reciente")
        elif forma in ("SC 13D", "SC 13G"):
            puntos += 12
            razones.append("Movimiento de un gran inversor (13D/G)")

    # --- Reacción del precio ---
    vol_ratio = _num(ind.get("vol_ratio")) or 0
    cambio = _num(ind.get("cambio_1d")) or 0
    gap = _num(ind.get("gap_pct")) or 0
    if vol_ratio >= 2 and cambio > 1.5:
        puntos += 15
        razones.append(f"Sube un {cambio:+.1f}% con {vol_ratio:.1f}× el volumen habitual")
    elif vol_ratio >= 2 and cambio < -1.5:
        riesgos.append(f"Cae un {cambio:+.1f}% con volumen {vol_ratio:.1f}×")
    if abs(gap) >= 3:
        puntos += 8 if gap > 0 else -6
        (razones if gap > 0 else riesgos).append(f"Hueco de apertura del {gap:+.1f}%")

    if puntos < UMBRAL_SENAL or not razones:
        return None

    confianza = 0.3 + 0.05 * len(razones)
    etiqueta, _, horizonte = MOTORES["catalizador"]
    return Senal(c.ticker, "catalizador", puntos, confianza, razones, riesgos,
                 horizonte, etiqueta)


MOTORES_FUN = {
    "momentum": momentum,
    "reversion": reversion,
    "valor": valor,
    "catalizador": catalizador,
}
