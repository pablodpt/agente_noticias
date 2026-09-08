# 📊 Análisis sobre la base DuckDB del S&P 500

Tres herramientas para exprimir tu base local (`prices` con ~10 años de OHLCV de 503
valores, `tickers` con sector/industria/fecha de alta, y el snapshot de `fundamentals`).
Solo leen la base (`read_only=True`); nunca escriben en ella.

| Script | Pregunta que responde | Tiempo aprox. (503 valores) |
|---|---|---|
| `factores.py` | ¿Qué características han anticipado que una acción batiera al índice? Backtest de 7 factores + corte transversal de hoy con valor/calidad | ~5 s |
| `correlaciones.py` | ¿Cómo se agrupan de verdad los valores, cuánto diversifica tu cartera, qué se ha desacoplado hoy? Régimen, clusters, HRP, pares | ~15 s (~45 s con `--hrp --pares`) |
| `ml_ranking.py` | ¿Un modelo LightGBM ordena mejor los retornos futuros que el momentum a secas? Walk-forward honesto + ranking de hoy | ~30 s |

## Instalación

```bash
pip install -r analisis/requirements.txt
export SP500_DB=/ruta/a/tu/sp500.duckdb      # o pasa --db en cada comando
```

Si tu pipeline de actualización tiene la base abierta en escritura, DuckDB no dejará
abrirla en lectura desde otro proceso: ciérralo o trabaja sobre una copia del fichero.

## Uso

```bash
python analisis/factores.py --db $SP500_DB
python analisis/factores.py --db $SP500_DB --neutral-sector --coste-bps 15 --top 40

python analisis/correlaciones.py --db $SP500_DB                     # usa PORTAFOLIO de config.py
python analisis/correlaciones.py --db $SP500_DB --cartera "AAPL:20,MSFT:20,NVDA:15,AMZN:15,GOOGL:10" --hrp --pares

python analisis/ml_ranking.py --db $SP500_DB
python analisis/ml_ranking.py --db $SP500_DB --horizonte 3 --reentrenar 12 --sin-regimen
```

Cada script imprime el resumen por consola y deja en `analisis/salida/<nombre>/` un
`informe.md` con tablas y gráficos PNG, más los CSV con todo el detalle. La carpeta
`salida/` está en `.gitignore`.

Opciones comunes: `--desde/--hasta` (acotar fechas; `--hasta 2024-12-31` analiza «como
si» fuera ese día), `--sin-filtro-alta` (ver abajo), `--sin-graficos`.

## Qué hace cada uno

### 1. `factores.py` — backtest de factores

Cada fin de mes ordena el universo por un factor, lo parte en quintiles equiponderados
y mide el retorno del mes siguiente. Reporta IC (Spearman factor → retorno futuro) con
t-stat de Newey-West, diferencial Q5−Q1 bruto y neto de costes, exceso del mejor
quintil, drawdown, rotación y el desglose por año.

Factores: momentum 12-1 y 6-1, reversión a 1 mes, baja volatilidad, cercanía al máximo
de 52 semanas, precio/SMA200, baja beta, y un compuesto configurable (`--combinado`).

Termina con el **corte transversal de hoy**: pondera los factores de precio por el IC
que han demostrado y los cruza con valor (E/P, FCF yield, B/P, S/P), calidad (ROE,
márgenes, ROA, apalancamiento) y crecimiento del snapshot de `fundamentals`, todo en
percentiles dentro del sector. Marca «posibles trampas de valor» (barato y cayendo) y
«momentum caro».

Lectura rápida: `IC_tstat_NW > 2` y quintiles monótonos (ver `quintiles.png`) ⇒ el
factor ha funcionado de forma sólida. Un factor con un año enorme y el resto planos es
sospechoso.

### 2. `correlaciones.py` — estructura del mercado y tu cartera

- **A. Régimen**: correlación media entre pares, absorption ratio (varianza del primer
  componente) y dispersión, en ventana móvil. Percentil histórico del valor de hoy.
- **B. Clusters**: clustering jerárquico sobre la correlación con shrinkage Ledoit-Wolf
  (k automático por silueta). Compara con GICS (ARI), lista los valores que cotizan
  con otro grupo, y la matriz de correlación media entre sectores.
- **C. Cartera**: volatilidad, beta, contribución al riesgo de cada posición, número
  efectivo de apuestas, y los valores del índice que más diversificarían (con la
  reducción de volatilidad que supondría añadir un 10 %) frente a los redundantes.
- **D. Desacoples**: regresión de cada valor contra mercado + su cluster; residuo de
  hoy en desviaciones típicas. Es el sustituto natural del «umbral del 3 %» del
  agente de alertas: solo salta lo que se mueve por motivos propios.
- **E. `--hrp`**: backtest mensual de Hierarchical Risk Parity vs equiponderado vs
  inversa de volatilidad sobre todo el universo.
- **F. `--pares`**: pares cointegrados (Engle-Granger) dentro de la misma industria con
  half-life de 2-60 días y z-score actual del spread.

### 3. `ml_ranking.py` — modelo de ranking con validación honesta

LightGBM que predice el **rango percentil** del retorno del mes siguiente a partir de
~37 features de precio/volumen normalizadas por fecha (momentum a varios plazos,
volatilidades, drawdown, distancia a medias, RSI, volumen relativo, iliquidez de
Amihud, beta, momentum sectorial…) más variables de régimen y el sector.

Validación **walk-forward con embargo**: se reentrena cada N meses solo con el pasado
y predice meses que nunca vio. Se evalúa como un factor (IC, deciles, spread D10−D1)
y se compara **contra el momentum 12-1 y contra el compuesto de factores** en las
mismas fechas; imprime el t-stat de la diferencia de IC para que sepas si el modelo
aporta algo real o es ruido caro. Luego entrena con todo el histórico y saca el ranking
de hoy con las importancias.

Los `fundamentals` **no** entran en el modelo: son una foto de hoy y usarlos para
predecir el pasado sería mirar el futuro.

## Limitaciones que debes tener en cuenta

- **Sesgo de supervivencia.** Los 503 símbolos son los miembros *actuales* del índice.
  Por defecto cada valor solo entra en el universo desde su `date_added`, lo que
  elimina la parte «elegimos hoy a los que subieron para entrar», pero los que
  salieron del índice (quiebras, adquisiciones, degradaciones) no están en la base.
  Los retornos absolutos salen algo optimistas; la **comparación entre factores y
  entre modelos** sigue siendo válida. Si quieres quitarlo del todo, añade a la base
  una tabla con la pertenencia histórica (la tabla de cambios de la Wikipedia sirve) y
  los precios de los salientes.
- **`fundamentals` es un snapshot.** Solo sirve para el corte transversal de hoy, no
  para backtestear valor/calidad. Si tu pipeline empieza a guardar un snapshot cada
  día o semana (es lo que ya parece hacer: hay dos `snapshot_date`), en unos meses
  tendrás un histórico real y el ASOF JOIN es inmediato.
- **Costes.** El diferencial «neto» de `factores.py` usa una estimación lineal
  (rotación × puntos básicos). Con 503 valores líquidos es razonable; no lo es para
  operar de verdad cestas grandes.
- **Nada de esto es asesoramiento financiero.** Son herramientas de análisis.

## Probar sin la base real

`crear_db_prueba.py` genera una base sintética con el mismo esquema (modelo de
factores con un poco de momentum plantado) para comprobar que todo corre:

```bash
python analisis/crear_db_prueba.py --salida /tmp/sp500_prueba.duckdb --tickers 150
python analisis/factores.py --db /tmp/sp500_prueba.duckdb
```

Los resultados sobre esa base no significan nada; solo validan el código.
