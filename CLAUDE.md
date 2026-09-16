# Trading Research & Scoring Platform — contexto del proyecto

Repo 2 de 3 del portafolio (proyecto ancla). Ver `../CLAUDE.md` y `../PORTFOLIO_ROADMAP.md` para el contexto completo.

## Problema que resuelve
Automatizar la evaluación técnica de un watchlist de acciones (setup, tendencia, momentum) y explicar el porqué del score, no solo el número.

## Stack de este repo
- Django + DRF
- yfinance para la ingesta de OHLCV
- Indicadores técnicos calculados a mano en Python puro (sin pandas-ta -- ver "Motor de scoring" más abajo)
- Celery + django-celery-beat para refresco periódico de precios (diseño; ver "Scheduling de la ingesta")
- Neon Postgres (mismo patrón de env var que el repo 1; instancia Neon separada)
- Gemini API con function-calling para el agente explicador

## Estado actual
Django + DRF configurados, modelos de `market_data` y `scoring` con sus migraciones y admin registrado.
- Ingesta de precios (comando manual): `python manage.py load_prices [--tickers=AAPL,MSFT] [--days=90]` descarga OHLCV vía yfinance y hace upsert idempotente en `PriceBar`.
- Motor de scoring (comando manual): `python manage.py compute_scores [--tickers=AAPL,MSFT]` calcula el `Score` técnico (0-100) de cada ticker a partir de sus `PriceBar` y hace upsert idempotente en `Score` por (ticker, fecha=hoy). Requiere al menos 50 `PriceBar` por ticker (lo que pide SMA50); si no alcanza, loguea el motivo y sigue con el resto sin romper la corrida.

Todavía sin Celery/scheduling automático (ambos comandos corren a mano por ahora -- ver sección de scheduling más abajo) y sin el agente de IA -- eso es el próximo paso.

## Modelo de datos
Implementado (`market_data`):
- `Ticker(symbol, name, exchange, sector, active)`
- `Watchlist(name)`
- `WatchlistItem(watchlist, ticker)` -- unique constraint por (watchlist, ticker)
- `PriceBar(ticker, date, open, high, low, close, volume)` -- unique constraint por (ticker, date), orden por fecha descendente

Implementado (`scoring`):
- `Score(ticker, date, score, components, computed_at)` -- unique constraint por (ticker, date), orden por -date. `components` guarda cada indicador crudo y su sub-puntaje (ver "Motor de scoring").

Pendiente (próximos pasos, referencia de diseño):
- `AgentExplanation(score_id, texto, timestamp)` (`agent`)

## Motor de scoring
`scoring/indicators.py` calcula SMA, RSI (Wilder), ATR (Wilder) y volumen relativo a mano, en Python puro -- **decisión de diseño intencional, no un atajo**: (1) `pandas-ta` importa `from numpy import NaN`, eliminado en numpy>=2.0, lo que rompe el import; (2) el roadmap de este portafolio pide poder explicar cada componente del score con criterio propio en la entrevista, no citando una librería como caja negra -- eso aplica también a los indicadores, no solo al score final.

`scoring/services.py::compute_score(ticker)` compone el `Score` (0-100) así:
- **Tendencia (40 pts, 20 c/u):** `close > sma50` y `sma20 > sma50` (cruce alcista).
- **Momentum (30 pts, RSI14):** 40-60 (neutral) = 15 pts; [30,40) o (60,70] (sano, sin extremo) = 30 pts; <30 o >70 (sobrecompra/sobreventa) = 5 pts -- penaliza el extremo, no lo premia.
- **Volumen (30 pts, relative_volume = volumen de hoy / promedio de los 20 días previos):** >=1.5 = 30 pts; [1.0,1.5) = 15 pts; <1.0 = 0 pts.
- **ATR14** se guarda en `components` como referencia de volatilidad (útil para backtesting/stop-loss) pero **no suma ni resta del score** -- no es direccional.

Necesita al menos 50 `PriceBar` (lo que pide SMA50); con menos, `compute_score` devuelve `(None, razón)` sin lanzar excepción.

## Scheduling de la ingesta y el scoring
`load_prices` y `compute_scores` corren a mano por ahora. La arquitectura de scheduling automático (Celery + django-celery-beat) se implementa recién en la fase de hardening, replicando la decisión ya documentada en `01-etl-data-pipeline/README.md`: Celery Beat queda como diseño, pero lo que efectivamente dispara la ingesta/scoring en producción es un cron de GitHub Actions llamando a los management commands directo (sin worker ni broker) -- un worker de Celery Beat 24/7 no entra en el free tier de Render.

## Apps Django
`market_data` (con modelos), `scoring`, `agent`, `api` (las últimas tres vacías por ahora, solo esqueleto de app)

## Endpoints principales
Ninguno implementado todavía (`api` está vacía). Planeados para próximos pasos:
- `GET /api/watchlist/`
- `POST /api/watchlist/{id}/refresh/`
- `GET /api/scores/{ticker}/`
- `GET /api/scores/{ticker}/explain/` (dispara el agente)

## Flujo del agente de IA (pieza de mayor señal de este repo)
Request → el agente recibe el ticker → invoca tools internas propias (`get_price_history`, `get_technical_indicators`, `get_recent_news`) → el LLM redacta la explicación con esos datos → se persiste como `AgentExplanation` y se devuelve. Esto debe ser tool-calling real (el LLM decide qué función llamar), no un prompt fijo con datos pre-insertados.

## Definición de "listo para entrevista" (Fase 4)
- Backtesting simple del score contra retornos históricos (aunque sea ingenuo) — es lo que separa este proyecto de un dashboard decorativo.
- Tests cubriendo el cálculo de indicadores y el scoring compuesto.
- Deploy en Render, consumiendo datos idealmente ya limpios del repo 1 (o su propia ingesta si el repo 1 no está listo aún).
- README explicando el trade-off de diseño del agente (por qué tool-calling y no RAG aquí).

## Primer prompt sugerido para arrancar
"Lee CLAUDE.md y ../PORTFOLIO_ROADMAP.md. Arranca el proyecto Django con DRF, define los modelos Ticker, PriceBar, Watchlist y WatchlistItem con sus migraciones, y deja el admin de Django configurado para poder cargar un ticker de prueba a mano."
