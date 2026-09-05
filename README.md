# 💼📈 Agente de mercados → Telegram

Dos herramientas en el mismo repo, independientes entre sí:

| | Qué hace | Cuándo se ejecuta |
|---|---|---|
| **🔎 Detector de oportunidades** (`oportunidades.py`) | Escanea cientos de valores buscando ideas de inversión con cuatro motores distintos | Diario y semanal |
| **💼 Agente de portafolio** (`agente.py`) | Vigila **tu cartera**: boletín diario y alertas urgentes | Cada 30 min + boletín a las 18:00 |

---

# 0. 🔑 Claves: en GitHub Actions y en tu máquina

Las dos herramientas necesitan las mismas tres claves:

| Clave | Para qué | Dónde conseguirla |
|---|---|---|
| `TELEGRAM_TOKEN` | Enviarte mensajes | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHAT_ID` | Saber a quién escribirte | [@userinfobot](https://t.me/userinfobot) (y **escribe algo a tu bot** una vez) |
| `SEC_USER_AGENT` | La SEC exige identificar al cliente | Tu nombre y correo: `pablo pablo@dominio.com` |

## En GitHub Actions (sin servidor)

Las guardas como **secrets**: *Settings → Secrets and variables → Actions → New
repository secret*. Los workflows ya las usan desde ahí.

## En tu máquina (archivo `.env`)

Los secrets de GitHub **no se pueden leer desde la API** (solo escribir), así que
tienes dos formas de tener el `.env` en local:

**A. Recuperarlas desde GitHub (lo más rápido si ya las guardaste allí)**

Un solo comando, que hace todo el recorrido (lanza el workflow, espera, descarga
el artefacto, instala el `.env` con permisos 600 y borra el artefacto):

```bash
python configurar.py --desde-github      # necesita el CLI gh autenticado
```

> ⚠️ La **API** de GitHub solo puede lanzar workflows que existan en la **rama por
> defecto** (`main`). Si aún no has fusionado la rama, te dará 404: usa entonces el
> camino manual o la opción B.

A mano, desde la web:

1. **Actions → Generar .env para uso local → Run workflow** (en el desplegable
   *Use workflow from* elige tu rama si el workflow aún no está en `main`).
2. Descarga el artefacto **`env-local`** que genera.
3. Descomprímelo y deja el `.env` en la carpeta del proyecto.
4. **Borra el artefacto** en cuanto lo tengas: contiene las credenciales en texto
   plano. Caduca en 1 día, pero mejor no dejarlo.

**B. Escribirlas a mano con el asistente**

```bash
python configurar.py            # te las pide, valida el formato y escribe .env
python configurar.py --probar   # manda un mensaje de prueba a Telegram
python configurar.py --mostrar  # enseña qué hay cargado (oculta el token)
```

El asistente guarda el `.env` con **permisos 600** (solo tú) y reutiliza los valores
que ya tuvieras. El archivo está en `.gitignore`, así que nunca se sube al repo.

> También puedes copiar `.env.example` a `.env` y rellenarlo a mano con tu editor.

---

# 1. 🔎 Detector de oportunidades

Escanea un universo (por defecto el **S&P 500**), puntúa cada valor de 0 a 100 y
te manda por Telegram —y deja en el repo— un informe con las mejores ideas,
**por qué** aparecen y **qué riesgos** tienen.

## 1.1 Los cuatro motores

La gracia no es un indicador mágico, sino que cuatro lentes distintas puntúan el
mismo valor y se combinan:

| Motor | Busca | Horizonte | Peso |
|---|---|---|---|
| 📈 **Momentum** | Tendencia alineada (precio > SMA20 > SMA50 > SMA200), momento 12-1 meses, fuerza relativa frente al S&P 500, rupturas con volumen | 1-3 meses | 30 % |
| 🎯 **Sobreventa** | Caídas fuertes dentro de una tendencia alcista de fondo: RSI bajo, desviación de su media, descuento del 15-30 % desde máximos, volumen de capitulación | 2-8 semanas | 25 % |
| 💎 **Valor y calidad** | Múltiplos baratos **comparados con su sector** (PER, PEG, EV/EBITDA, FCF yield), rentabilidad, balance sano y crecimiento; con filtro anti-trampa de valor | 6-18 meses | 25 % |
| ⚡ **Catalizador** | Resultados inminentes, noticias con sesgo alcista, presentaciones SEC (8-K, 13D), mejoras de analistas y reacciones del precio con volumen | días-2 meses | 20 % |

**Puntuación final** = media de los motores que han dado señal, ponderada por esos pesos,
más una bonificación por **confluencia** (si varios motores coinciden en el mismo valor)
menos penalizaciones por volatilidad extrema o sobrecompra.

Los pesos **se adaptan al régimen del mercado**: si el S&P 500 está por debajo de su
media de 200 sesiones, el momentum pesa menos y la sobreventa/valor pesan más.

## 1.2 Uso rápido

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python configurar.py                 # crea el .env con tus claves (ver sección 0)

python oportunidades.py selftest          # ✅ comprueba la lógica SIN red (datos sintéticos)
python oportunidades.py preview           # escanea e imprime por consola (no envía nada)
python oportunidades.py diario            # scan rápido → Telegram + informe
python oportunidades.py semanal           # scan profundo → Telegram + informe
```

Opciones útiles:

```bash
python oportunidades.py escanear --modo semanal --top 20
python oportunidades.py escanear --universo mezcla --sin-telegram   # S&P 500 + tu lista + tu cartera
python oportunidades.py escanear --tickers AAPL,SAN.MC,BTC-USD --modo diario
python oportunidades.py universo --listar
python oportunidades.py universo --refrescar                        # re-baja la lista del S&P 500
```

| Comando | Qué hace |
|---|---|
| `escanear` | Escanea con las opciones que le des (`--modo diario\|semanal\|rapido`) |
| `diario` / `semanal` | Atajos con la configuración pensada para cada periodicidad |
| `preview` | Solo consola: ni Telegram ni deduplicación (ideal para trastear) |
| `selftest` | Prueba los cuatro motores con datos sintéticos, sin tocar la red |
| `universo` | Muestra el universo configurado y su reparto por sectores |

## 1.3 Diferencias entre el modo diario y el semanal

| | Diario | Semanal |
|---|---|---|
| Cuándo | cada día laborable, tras el cierre de Wall Street | una vez por semana (sábado) |
| Fundamentales | solo los ~70 candidatos con mejor pinta | todo el universo |
| Ideas en el informe | 8 | 15 |
| Coste | ~5-10 min | ~15-30 min |

El diario es un **filtro de novedades**: qué se está moviendo hoy. El semanal es
un **barrido profundo**: dónde hay valor y calidad de verdad.

## 1.4 Automatización

Ya viene con dos workflows de GitHub Actions (gratis):

| Workflow | Cuándo | Qué hace |
|---|---|---|
| `oportunidades-diario.yml` | 20:20 y 21:20 UTC, de lunes a viernes<sup>1</sup> | Scan diario → Telegram + artefacto |
| `oportunidades-semanal.yml` | sábados 08:00 UTC | Scan profundo → Telegram + informe commiteado |

<sup>1</sup> Se lanza dos veces porque España y EE. UU. cambian de hora en fechas
distintas; el propio job solo continúa cuando en Nueva York son las 16 h
(mercado recién cerrado).

Secrets necesarios (**Settings → Secrets and variables → Actions**):

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SEC_USER_AGENT` → p. ej. `mi-agente tu-email@dominio.com`

Puedes lanzarlos a mano desde **Actions → Run workflow** para probarlos.

> ⚠️ **Los workflows programados solo se ejecutan desde la rama por defecto**
> (`main`). Si trabajas en otra rama, los cron no saltarán hasta que la fusiones;
> el lanzamiento manual desde la pestaña Actions sí funciona en cualquier rama.

En local, si prefieres **cron**:

```cron
20 20 * * 1-5  cd /ruta && .venv/bin/python oportunidades.py diario
0   8 * * 6    cd /ruta && .venv/bin/python oportunidades.py semanal
```

## 1.5 Dónde acaban los informes

```
informes/ultimo.md      último informe (el semanal se commitea al repo)
informes/ultimo.html    el mismo informe en HTML, para leer cómodamente
informes/historico/     copias con fecha (no se versionan)
informes/ejemplo.html   ejemplo de formato generado con datos sintéticos
```

En GitHub Actions además se suben como **artefacto** descargable (30-90 días).

## 1.6 Ajustes (todo por variables de entorno en `.env`)

| Variable | Por defecto | Qué hace |
|---|---|---|
| `UNIVERSO` | `sp500` | `sp500`, `personalizado`, `mezcla` o `portafolio` |
| `ARCHIVO_UNIVERSO` | `datos/mi_universo.txt` | Tu lista de tickers (uno por línea) |
| `UNIVERSO_TICKERS` | — | Lista en línea: `AAPL,SAN.MC,BTC-USD` |
| `MAX_TICKERS_UNIVERSO` | `0` | Limita el tamaño (útil para pruebas: `40`) |
| `TOP_DIARIO` / `TOP_SEMANAL` | `8` / `15` | Cuántas ideas muestra cada informe |
| `MAX_POR_SECTOR` | `3` | Máximo de ideas del mismo sector (diversifica) |
| `UMBRAL_FINAL` | `50` | Puntuación mínima para entrar en el informe |
| `PESOS_ESTRATEGIA` | `momentum:0.30,...` | Pesos de cada motor |
| `PRECIO_MINIMO` | `5` | Descarta chicharros |
| `VOLUMEN_DOLARES_MIN` | `20000000` | Volumen medio diario mínimo en dólares |
| `SESIONES_MINIMAS` | `200` | Historial mínimo para analizar un valor |
| `VOLATILIDAD_ALTA` / `MAX` | `60` / `90` | Penalizaciones por volatilidad anualizada |
| `FUND_TOP_DIARIO` / `FUND_TODOS_SEMANAL` | `70` / `600` | A cuántos valores se les piden fundamentales |
| `MAX_TICKERS_NOTICIAS` | `25` | A cuántos finalistas se les buscan catalizadores |
| `CACHE_FUND_HORAS` | `12` | Horas que se reutilizan los fundamentales descargados |
| `TTL_OPORTUNIDAD_HORAS` | `96` | Días que una idea avisada no se repite por Telegram |
| `BENCHMARK` / `INDICE_MIEDO` | `SPY` / `^VIX` | Referencia para fuerza relativa y régimen |

## 1.7 Cómo interpretarlo (importante)

- La puntuación **no es una predicción**: es una forma de ordenar dónde mirar.
- Cada idea lleva **horizonte** y **riesgos**. Lee los riesgos antes que las razones.
- Los motores son deliberadamente distintos: una idea de valor puede tardar un año
  en funcionar y una de catalizador, unos días.
- Usa el informe como **lista de investigación**, no como orden de compra.

---

# 2. 💼 Agente de portafolio

Vigila **tu cartera** y avisa por Telegram: boletín diario a las 18:00 (hora de Madrid)
y alertas urgentes inmediatas (rupturas, gaps, volumen anómalo, 8-K, noticias
críticas, resultados hoy o mañana).

## 2.1 Configura tu cartera

Edita `PORTAFOLIO` en `config.py`:

```python
PORTAFOLIO = [
    {"ticker": "AAPL", "nombre": "Apple", "peso": 20},
    {"ticker": "SAN.MC", "nombre": "Santander", "peso": 15},   # bolsa española
    {"ticker": "BTC-USD", "nombre": "Bitcoin", "peso": 5},     # cripto
]
```

`nombre` y `peso` son opcionales (el peso solo ordena la relevancia en el boletín).
Sirve cualquier símbolo de Yahoo Finance: acciones US, `.MC` Madrid, `.DE` Frankfurt,
ETFs, cripto.

> Los datos de **SEC EDGAR** (10-K, 10-Q, 8-K) solo existen para cotizadas de EE. UU.
> Para el resto de tickers, el resto de funciones sigue operando con normalidad.

## 2.2 Crea tu bot de Telegram

1. [@BotFather](https://t.me/BotFather) → `/newbot` → copia el **token**.
2. [@userinfobot](https://t.me/userinfobot) → copia tu **chat id**.
3. Envía un mensaje cualquiera al bot (si no, no puede escribirte).

```bash
python configurar.py      # asistente: crea .env con permisos 600
# o a mano:  cp .env.example .env  y rellena TELEGRAM_TOKEN y TELEGRAM_CHAT_ID
```

## 2.3 Uso

```bash
python agente.py test       # comprueba la conexión con Telegram
python agente.py preview    # imprime el boletín por consola, sin enviar nada
python agente.py boletin    # genera y ENVÍA el boletín
python agente.py vigilar    # una pasada de detección de eventos urgentes
python agente.py daemon     # bucle continuo (boletín a su hora + vigilancia)
```

Y en automático, con los workflows `boletin-diario.yml` y `vigilancia.yml`, o con
systemd/cron como se explica más abajo.

---

# 3. Estructura del proyecto

```
oportunidades.py         CLI del detector (escanear / diario / semanal / preview / selftest)
scanner.py            ⭐ Orquesta el escaneo: descarga, motores, ranking y dedupe
estrategias.py        ⭐ Los cuatro motores (momentum, sobreventa, valor, catalizador)
indicadores.py           Descarga por lotes + 40 indicadores técnicos por valor
fundamentales.py         Fundamentales de Yahoo con caché y percentiles por sector
catalizadores.py         Noticias, SEC, analistas y resultados de los finalistas
universo.py              Universo: S&P 500 (empaquetado + refresco), lista propia, cartera
informe.py               Informes en Markdown, HTML y resumen para Telegram

agente.py                CLI del agente de cartera (boletin / vigilar / daemon)
mercado.py               Precios, rupturas, volumen, medias, calendario de earnings
noticias.py              RSS por ticker (Yahoo + Google News) y macro
sec.py                   SEC EDGAR: 10-K, 10-Q, 8-K, 13D/G, Form 4...
sentimiento.py           Análisis alcista/bajista (léxico rápido o FinBERT)

config.py                Toda la configuración y los umbrales
configurar.py            Asistente para crear y comprobar el .env en local
estado.py                Deduplicación para no repetir avisos (estado.json)
telegram_bot.py          Envío a Telegram con troceado y reintentos
datos/sp500.json         Lista del S&P 500 empaquetada (funciona sin red)
datos/mi_universo.txt    Tu lista personalizada
datos/fundamentales_cache.json   Caché (no se versiona)
informes/                Informes generados
```

`estado.json` guarda lo ya notificado para que no te llegue dos veces la misma
alerta; está en `.gitignore`.

---

# 4. Ejecución en tu propio servidor

```ini
# /etc/systemd/system/agente-portafolio.service
[Unit]
Description=Agente de Portafolio
After=network-online.target

[Service]
WorkingDirectory=/ruta/a/agente_noticias
ExecStart=/ruta/a/agente_noticias/.venv/bin/python agente.py daemon
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```cron
0 18 * * 1-5  cd /ruta && .venv/bin/python agente.py boletin
*/30 14-23 * * 1-5  cd /ruta && .venv/bin/python agente.py vigilar
20 20 * * 1-5 cd /ruta && .venv/bin/python oportunidades.py diario
0 8 * * 6     cd /ruta && .venv/bin/python oportunidades.py semanal
```

> Los cron de GitHub Actions pueden retrasarse unos minutos cuando la plataforma
> va cargada; si necesitas puntualidad al segundo, usa tu propia máquina.

---

⚠️ Este proyecto es informativo y **no constituye asesoramiento financiero**.
Un script que puntúa valores no conoce tu situación, tus impuestos ni tu
tolerancia al riesgo. Fuentes: Yahoo Finance, SEC EDGAR y feeds RSS públicos.
