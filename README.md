# 💼 Agente de Portafolio → Telegram

Agente que vigila **tu portafolio** y te avisa por Telegram:

- **📬 Boletín diario a las 18:00 (hora de Madrid)** con rendimiento, contexto de mercado,
  calendario de resultados, presentaciones SEC y noticias por ticker.
- **🚨 Alertas urgentes inmediatas** cuando pasa algo grande: ruptura al alza o a la baja,
  caída/subida fuerte, gap de apertura, volumen anómalo, 8-K/10-K recién publicado,
  noticia crítica (fraude, demanda, adquisición, recorte de guidance...) o resultados hoy/mañana.

---

## 1. Configura tu portafolio

Edita la lista `PORTAFOLIO` en `config.py`:

```python
PORTAFOLIO = [
    {"ticker": "AAPL", "nombre": "Apple", "peso": 20},
    {"ticker": "SAN.MC", "nombre": "Santander", "peso": 15},   # bolsa española
    {"ticker": "BTC-USD", "nombre": "Bitcoin", "peso": 5},     # cripto
]
```

`nombre` y `peso` son opcionales (el peso solo ordena la relevancia en el boletín).
Funciona con cualquier símbolo de Yahoo Finance: acciones US, `.MC` Madrid, `.DE` Frankfurt, ETFs, cripto.

> Nota: los datos de **SEC EDGAR** (10-K, 10-Q, 8-K) solo existen para empresas cotizadas en EE. UU.
> Para el resto de tickers, el resto de funciones sigue operando con normalidad.

## 2. Crea tu bot de Telegram

1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot` → copia el **token**.
2. Habla con [@userinfobot](https://t.me/userinfobot) → copia tu **chat id**.
3. Envía un mensaje cualquiera a tu bot (si no, no puede escribirte).

Copia `.env.example` a `.env` y rellena:

```bash
cp .env.example .env
```

## 3. Instala y prueba

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python agente.py test       # comprueba la conexión con Telegram
python agente.py preview    # imprime el boletín por consola, sin enviar nada
python agente.py boletin    # genera y ENVÍA el boletín
python agente.py vigilar    # una pasada de detección de eventos urgentes
python agente.py daemon     # bucle continuo (boletín a su hora + vigilancia)
```

## 3.5 Base de datos local y análisis de correlaciones

El agente ahora **acumula todo lo que ve** en una base SQLite local (`datos/portafolio.db`,
gitignoreada): precios diarios OHLCV de los 7 tickers + los 4 índices, noticias con
sentimiento, presentaciones SEC y fechas de earnings con sorpresa EPS. En cada
ejecución (boletín, vigilancia, daemon) se persisten los datos nuevos, de forma
incremental y limitada en el tiempo (`PERSISTIR_CADA_HORAS`, por defecto 4 h).

Sobre esa base, `correlaciones.py` extrae **patrones de todo tipo**:

| Sección | Qué mide |
|---|---|
| 🔗 Correlaciones diarias | Matriz de retornos a 1M / 3M / 6M / 12M + pares extremo (concentración vs. diversificador) |
| 📐 Beta y riesgo | Beta frente al S&P 500, volatilidad total e idiosincrática |
| 🌡️ Régimen actual | ¿Los activos se acoplan más o menos al mercado que antes? (corr. 1M vs 12M) + vol. del portafolio |
| 😱 Miedo y tipos | Correlación con VIX (y caída media en días de susto) y con el bono a 10 años |
| 📰 Noticias → precio | ¿El sentimiento de los titulares anticipa el retorno del día siguiente? (correlación + mediana tras días bajistas/alcistas) |
| 🏛️ Efecto SEC | Retorno acumulado a 5 sesiones tras 8-K, 10-Q, 10-K, 13D/G, Form 4... |
| 🎯 Efecto earnings | Retorno a 5 sesiones según sorpresa de resultados (beat / inline / miss) |
| 🔊 Volumen anómalo | ¿Los días de volumen ≥ 2,5× la media se continúan o revierten? |
| 🌀 Momentum | Autocorrelación de retornos a 1/5/20 días: tendencia vs. reversión a la media |
| 🎯 Quién lidera | Cross-correlación con retardos (−3..+3 días) frente al mercado |
| 🧩 Diversificación real | Vol. del portafolio ponderado, nº efectivo de apuestas, concentración, mejor diversificador, escenario «S&P −5%» |
| 💡 Insights | 5-10 conclusiones automáticas que combinan todo lo anterior |

### Uso

```bash
# Primer uso: rellena la base con 5 años de histórico (precios, noticias,
# filings SEC completos y earnings). Puede tardar unos minutos.
python agente.py datos --bootstrap

# Persistir ahora (respetando el throttle de PERSISTIR_CADA_HORAS)
python agente.py datos                # añade --fuerza para saltarlo
python agente.py datos --resumen      # estado de la base

# Ver el análisis
python agente.py analisis             # informe completo por consola
python agente.py analisis --markdown  # ... y lo guarda en informes/*.md
python agente.py analisis --enviar    # ... y lo envía por Telegram
python agente.py analisis --validar   # (con datos demo) valida el motor

# Dataset sintético de prueba (para probar sin histórico ni Telegram)
python agente.py datos --demo
```

El boletín diario incluye una sección compacta **«🔗 PATRONES Y CORRELACIONES»**
con lo más relevante del día. Con más datos acumulados, las estimaciones se
afinan solas: el event study de SEC gana fuerza con cada nuevo filing, y la
correlación noticias→precio con cada boletín.

> **En GitHub Actions** el runner es efímero: los workflows ya restauran la base
> de la caché de la última ejecución (igual que `estado.json`). Si no hay caché
> (primer run), el agente se autoabastece con `AÑOS_BOOTSTRAP` años de histórico
> antes de generar el boletín. **En tu propio servidor** (modo daemon), la base
> crece incrementalmente en el disco, día a día.

---

## 4. Ejecución automática

### Opción A — GitHub Actions (gratis, sin servidor)

Ya incluida en `.github/workflows/`:

| Workflow | Qué hace | Cuándo |
|---|---|---|
| `boletin-diario.yml` | Envía el boletín completo | 18:00 Madrid, L-V (ajusta solo el horario de verano/invierno) |
| `vigilancia.yml` | Busca eventos urgentes | cada 30 min, 12:00-21:00 UTC, L-V |

En tu repo → **Settings → Secrets and variables → Actions → New repository secret**:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`
- `SEC_USER_AGENT` → p. ej. `mi-agente tu-email@dominio.com` (la SEC lo exige)

Puedes lanzarlos a mano desde la pestaña **Actions → Run workflow** para probar.

> Los cron de GitHub Actions pueden retrasarse unos minutos cuando la plataforma va cargada.
> Si necesitas puntualidad al segundo, usa la opción B.

### Opción B — Servidor / PC propio (modo daemon)

```bash
python agente.py daemon
```

Gestiona él mismo el horario y la deduplicación. Para dejarlo permanente con **systemd**:

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

```bash
sudo systemctl enable --now agente-portafolio
```

O con **cron**, si prefieres pasadas puntuales:

```cron
0 18 * * 1-5  cd /ruta && .venv/bin/python agente.py boletin
*/30 14-23 * * 1-5  cd /ruta && .venv/bin/python agente.py vigilar
```

---

## 5. Ajustes de sensibilidad

Todo se controla por variables de entorno en `.env` (o desde `config.py`):

| Variable | Por defecto | Qué hace |
|---|---|---|
| `UMBRAL_MOVIMIENTO_PCT` | `3.0` | % de movimiento diario que dispara alerta urgente |
| `UMBRAL_GAP_PCT` | `3.0` | % de gap de apertura que dispara alerta |
| `UMBRAL_VOLUMEN_X` | `2.5` | Volumen frente a la media de 20 días considerado anómalo |
| `VENTANA_RUPTURA_DIAS` | `52` | Sesiones para calcular rupturas de rango (además de 52 semanas) |
| `DIAS_AVISO_EARNINGS` | `10` | Días de antelación para marcar 🔔 en el calendario |
| `HORA_BOLETIN` | `18` | Hora del boletín |
| `TZ_USUARIO` | `Europe/Madrid` | Zona horaria |
| `INTERVALO_VIGILANCIA_SEG` | `900` | Frecuencia de vigilancia en modo daemon |
| `USAR_FINBERT` | `false` | `true` = sentimiento con FinBERT (más preciso, descarga ~440 MB) |

Para activar FinBERT descomenta `transformers` y `torch` en `requirements.txt`.

---

## 6. Cómo está organizado

```
agente.py         Orquestador y CLI (boletin / vigilar / daemon / test / preview / datos / analisis)
config.py         Portafolio, umbrales y credenciales
mercado.py        Precios, rupturas, volumen, medias móviles, calendario de earnings
noticias.py       RSS por ticker (Yahoo + Google News) y macro
sec.py            SEC EDGAR: 10-K, 10-Q, 8-K, 13D/G, Form 4...
sentimiento.py    Análisis alcista/bajista (léxico rápido o FinBERT)
datos.py          Base SQLite local: persistencia incremental, bootstrap, dataset demo
correlaciones.py  Análisis de correlaciones y patrones sobre la base local
estado.py         Deduplicación: evita repetirte la misma alerta
telegram_bot.py   Envío con troceado seguro y reintentos
```

`estado.json` guarda lo ya notificado durante 36 h para que no te llegue dos veces
la misma alerta. `datos/portafolio.db` es la base de datos local (precios, noticias,
filings, earnings). Ambos están en `.gitignore`.

---

⚠️ Este proyecto es informativo y **no constituye asesoramiento financiero**.
Fuentes: Yahoo Finance, SEC EDGAR y feeds RSS públicos.
