# Trading Research & Scoring Platform — contexto del proyecto

Repo 2 de 3 del portafolio (proyecto ancla). Ver `../CLAUDE.md` y `../PORTFOLIO_ROADMAP.md` para el contexto completo.

## Problema que resuelve
Automatizar la evaluación técnica de un watchlist de acciones (setup, tendencia, momentum) y explicar el porqué del score, no solo el número.

## Stack de este repo
- Django + DRF
- pandas, numpy, pandas-ta para indicadores técnicos
- Celery + django-celery-beat para refresco periódico de precios
- Neon Postgres (mismo patrón de env var que el repo 1; instancia Neon separada)
- Gemini API con function-calling para el agente explicador

## Estado actual
Django + DRF configurados, modelos de `market_data` con sus migraciones y admin registrado. La ingesta de precios ya existe como comando manual: `python manage.py load_prices [--tickers=AAPL,MSFT] [--days=90]` descarga OHLCV vía yfinance y hace upsert idempotente en `PriceBar`. Todavía sin Celery/scheduling automático (corrida a mano por ahora -- ver sección de scheduling más abajo), sin scoring y sin el agente de IA -- eso son los próximos pasos, uno por vez, con tests antes de seguir.

## Modelo de datos
Implementado (`market_data`):
- `Ticker(symbol, name, exchange, sector, active)`
- `Watchlist(name)`
- `WatchlistItem(watchlist, ticker)` -- unique constraint por (watchlist, ticker)
- `PriceBar(ticker, date, open, high, low, close, volume)` -- unique constraint por (ticker, date), orden por fecha descendente

Pendiente (próximos pasos, referencia de diseño):
- `Score(ticker, fecha, score, componentes_json)` (`scoring`)
- `AgentExplanation(score_id, texto, timestamp)` (`agent`)

## Scheduling de la ingesta
`load_prices` corre a mano por ahora. La arquitectura de scheduling automático (Celery + django-celery-beat) se implementa recién en la fase de hardening, replicando la decisión ya documentada en `01-etl-data-pipeline/README.md`: Celery Beat queda como diseño, pero lo que efectivamente dispara la ingesta en producción es un cron de GitHub Actions llamando a un management command directo (sin worker ni broker) -- un worker de Celery Beat 24/7 no entra en el free tier de Render.

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
