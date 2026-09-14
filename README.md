# FARO — screener UCITS Europa (no ETF)

Agente para **elegir fondos UCITS disponibles en Europa que no son ETF** y construir un portafolio según el **momento de mercado**.

El universo es amplio en temáticas (liquidez, crédito, calidad, value, tech, IA, agua, salud, clima, India, Japón, mineras de oro…) pero **cerrado y exigente**: solo referentes de su clase. El ranking combina **TER, Sharpe, Sortino, rentabilidad, máximo drawdown y Calmar**, y un **encaje de régimen**.

```
python agente.py web          # interfaz
python agente.py screener     # ranking en consola
python agente.py portafolio --cartera automatico --perfil equilibrado
python agente.py preview      # boletín UCITS por consola
```

> Informativo. **No es asesoramiento financiero** ni una recomendación de compra.

---

## Qué resuelve

| Pieza | Cómo |
|---|---|
| Universo | ~80 UCITS (SICAV / FCP / OEIC / FI). **Cero ETF/ETC**. |
| Coste | TER de la clase minorista. Penaliza el 1% extra de gastos como alpha negativo. |
| Riesgo/retorno | Sharpe 3Y, Sortino, retorno 1Y/3Y/5Y, max drawdown, Calmar, Ulcer. |
| Best in class | Percentiles **dentro de su clase de activo**, podio por tema. |
| Ciclo | Expansión, selectiva, late-cycle, recesión, estanflación, recovery. |
| Portafolio | Sleeves monetario / RF / mixto / RV / oro → 1–3 BIC por sleeve, inverse-vol, techo de concentración. |

Modos de cartera: `automatico`, `defensivo`, `equilibrado`, `crecimiento`, `anti_inflacion`, `recesion`, `expansion`, `stagflation`.  
Perfiles: `conservador`, `equilibrado`, `agresivo`.

**Oro:** en UCITS el oro físico cotiza casi solo como ETC (excluido). La manga de oro usa **mineras** (BGF World Gold, Bakersteel, Ninety One…): beta oro con apalancamiento operativo y drawdowns mayores.

---

## Arranque

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python agente.py web --host 0.0.0.0 --port 8000
```

Abre la UI. Pestañas: **Régimen · Screener · Best in class · Portafolio · Método**.

Si Yahoo Finance responde, las métricas salen del **valor liquidativo**. Si no (red, cookies, etc.), FARO **no inventa una serie de NAV**: ancla a medias de categoría europeas (~sep-2026) y aplica una prima best-in-class menos penalización de TER. La UI etiqueta esos números como `est.`

```bash
# refrescar VL vivo
curl -X POST http://localhost:8000/api/refrescar
```

---

## Telegram (opcional)

El boletín diario pasa a ser el **resumen UCITS** (régimen + cartera automática + podio).

```bash
cp .env.example .env   # TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
python agente.py test
python agente.py boletin
python agente.py daemon
```

La vigilancia de tickers cotizados del repo original sigue en `vigilar` / `preview-tickers`.

---

## Cómo puntúa (resumen)

Score de calidad **dentro de la clase** (monetario, RF, mixto, RV, oro):

- Sharpe 3Y, Sortino, retorno 3Y y 1Y, max DD, Calmar, TER, convicción BIC (1–5).
- Si las métricas son estimadas, **sube el peso de TER y BIC** y baja el de retornos.

Score final = **68% calidad + 32% encaje de régimen**.

El constructor **no usa Markowitz** (inestable con TER distintos y 5 años de VL). Asigna sleeves por modo y, dentro, elige best-in-class diversificando gestora y tema.

---

## API

| Método | Ruta |
|---|---|
| GET | `/api/estado` |
| GET | `/api/screener?clase=&tema=&max_ter=&min_sharpe=&solo_bic=` |
| GET | `/api/best-in-class` |
| GET | `/api/portafolio?modo=automatico&perfil=equilibrado` |
| GET | `/api/fondo/{id}` |
| GET | `/api/regimen` |
| POST | `/api/refrescar` |

---

## Layout

```
agente.py            CLI (web / screener / portafolio / boletin / daemon)
servidor.py          FastAPI + UI
ucits/fondos.py      Universo curado (ISIN, TER, tema, tesis)
ucits/categorias.py  Medias de categoría
ucits/metricas.py    VL → Sharpe / DD / …
ucits/scoring.py     Percentiles y BIC
ucits/regimen.py     Ciclo de mercado
ucits/portafolio.py  Sleeves + inverse-vol
web/                 Interfaz
```

Edita `ucits/fondos.py` para añadir un UCITS (nunca un ETF). Campos mínimos: ISIN, TER, clase, categoría, tema, `bic` 1–5 y tesis.

---

⚠️ Rentabilidades pasadas no predicen las futuras. Prefiere **clases más baratas** del mismo fondo si tu plataforma las ofrece (EBN, banca privada). Fuentes: KID/factsheets de las gestoras, medias de categoría públicas, Yahoo Finance cuando está disponible.
