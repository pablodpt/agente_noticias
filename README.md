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
agente.py         Orquestador y CLI (boletin / vigilar / daemon / test / preview)
config.py         Portafolio, umbrales y credenciales
mercado.py        Precios, rupturas, volumen, medias móviles, calendario de earnings
noticias.py       RSS por ticker (Yahoo + Google News) y macro
sec.py            SEC EDGAR: 10-K, 10-Q, 8-K, 13D/G, Form 4...
sentimiento.py    Análisis alcista/bajista (léxico rápido o FinBERT)
estado.py         Deduplicación: evita repetirte la misma alerta
telegram_bot.py   Envío con troceado seguro y reintentos
```

`estado.json` guarda lo ya notificado durante 36 h para que no te llegue dos veces
la misma alerta. Está en `.gitignore`.

---

⚠️ Este proyecto es informativo y **no constituye asesoramiento financiero**.
Fuentes: Yahoo Finance, SEC EDGAR y feeds RSS públicos.
