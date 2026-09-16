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
Django + DRF configurados, modelos de `market_data`, `scoring` y `agent` con sus migraciones y admin registrado.
- Ingesta de precios (comando manual): `python manage.py load_prices [--tickers=AAPL,MSFT] [--days=90]` descarga OHLCV vía yfinance y hace upsert idempotente en `PriceBar`.
- Motor de scoring (comando manual): `python manage.py compute_scores [--tickers=AAPL,MSFT]` calcula el `Score` técnico (0-100) de cada ticker a partir de sus `PriceBar` y hace upsert idempotente en `Score` por (ticker, fecha=hoy). Requiere al menos 50 `PriceBar` por ticker (lo que pide SMA50); si no alcanza, loguea el motivo y sigue con el resto sin romper la corrida.
- Agente explicador (comando manual): `python manage.py explain_score --symbol=AAPL` corre tool-calling real sobre Gemini (el modelo decide qué tools invocar) y persiste el resultado como `AgentExplanation`. Ver "Agente explicador" más abajo. La lógica de "correr el agente y persistir" vive en una sola función (`agent/services.py::explain_and_persist`), compartida entre este comando y el endpoint `GET /api/scores/<symbol>/explain/` -- ver "API (DRF)".
- API REST (`api/`, DRF con `APIView`, no `ModelViewSet` genérico -- ningún endpoint es CRUD estándar): expone `market_data`, `scoring` y `agent` vía HTTP. Ver "API (DRF)" más abajo.
- Backtesting (comando manual): `python manage.py backtest_score --ticker=AAPL [--horizon=10]` corre el backtest ingenuo del `Score` contra retornos reales. Ver "Backtesting" más abajo.

Todavía sin Celery/scheduling automático (los tres comandos corren a mano por ahora -- ver sección de scheduling más abajo).

## Modelo de datos
Implementado (`market_data`):
- `Ticker(symbol, name, exchange, sector, active)`
- `Watchlist(name)`
- `WatchlistItem(watchlist, ticker)` -- unique constraint por (watchlist, ticker)
- `PriceBar(ticker, date, open, high, low, close, volume)` -- unique constraint por (ticker, date), orden por fecha descendente

Implementado (`scoring`):
- `Score(ticker, date, score, components, computed_at)` -- unique constraint por (ticker, date), orden por -date. `components` guarda cada indicador crudo y su sub-puntaje (ver "Motor de scoring").

Implementado (`agent`):
- `AgentExplanation(score, texto, tool_calls, timestamp)` -- `tool_calls` guarda la traza completa (tool + args, en orden) de lo que el modelo efectivamente invocó.

## Motor de scoring
`scoring/indicators.py` calcula SMA, RSI (Wilder), ATR (Wilder) y volumen relativo a mano, en Python puro -- **decisión de diseño intencional, no un atajo**: (1) `pandas-ta` importa `from numpy import NaN`, eliminado en numpy>=2.0, lo que rompe el import; (2) el roadmap de este portafolio pide poder explicar cada componente del score con criterio propio en la entrevista, no citando una librería como caja negra -- eso aplica también a los indicadores, no solo al score final.

`scoring/services.py::compute_score(ticker)` compone el `Score` (0-100) así:
- **Tendencia (40 pts, 20 c/u):** `close > sma50` y `sma20 > sma50` (cruce alcista).
- **Momentum (30 pts, RSI14):** 40-60 (neutral) = 15 pts; [30,40) o (60,70] (sano, sin extremo) = 30 pts; <30 o >70 (sobrecompra/sobreventa) = 5 pts -- penaliza el extremo, no lo premia.
- **Volumen (30 pts, relative_volume = volumen de hoy / promedio de los 20 días previos):** >=1.5 = 30 pts; [1.0,1.5) = 15 pts; <1.0 = 0 pts.
- **ATR14** se guarda en `components` como referencia de volatilidad (útil para backtesting/stop-loss) pero **no suma ni resta del score** -- no es direccional.

Necesita al menos 50 `PriceBar` (lo que pide SMA50); con menos, `compute_score` devuelve `(None, razón)` sin lanzar excepción.

`scoring/services.py::_compute_score_from_bars(bars)` es la función pura que hace el cálculo real (recibe una lista/queryset de `PriceBar` ya ordenada ascendente, sin tocar la DB); `compute_score(ticker)` es un wrapper delgado que trae los bars del ticker, valida `MIN_BARS_REQUIRED` y delega. Extraída así para que `backtesting/services.py::run_backtest` pueda reusar exactamente la misma matemática del score sin duplicarla -- ver "Backtesting".

## Backtesting
`backtesting/services.py::run_backtest(ticker, horizon_days=10, bucket_edges=(40,60,80))` -- backtest **ingenuo** del `Score` técnico contra retornos futuros reales, la pieza que separa este proyecto de un dashboard decorativo (ver "Definición de listo para entrevista").

**Metodología:** para cada fecha `d` con al menos `MIN_BARS_REQUIRED` (50) `PriceBar` disponibles hasta `d` inclusive, calcula el score con `_compute_score_from_bars(bars[:i+1])` -- **nunca** con bars posteriores a `d`, cero look-ahead, es la pieza más importante de todo el módulo (probado explícitamente: agregar una barra futura con precio absurdo no cambia el score de una fecha anterior). Busca el `PriceBar` `horizon_days` **sesiones de trading** después (no días calendario -- los `PriceBar` ya excluyen fines de semana/feriados) y calcula `forward_return_pct = (close_futuro / close_d - 1) * 100`. Si no hay suficientes bars futuros para una fecha, ese punto se descarta (no se rellena con nada). Agrupa los puntos por bucket de score (`bucket_edges` define los cortes; el último bucket llega hasta 100) y devuelve, por bucket, `n`, retorno promedio y win rate (% de puntos con retorno > 0). Sin historia suficiente para ningún punto, devuelve `{"ticker", "reason"}` en vez de lanzar una excepción.

**Limitaciones -- no se esconden, son señal de madurez reconocerlas en entrevista, no que el backtest sea perfecto:**
- Un solo ticker por corrida, no una cartera ni un universo diversificado.
- Sin costos de transacción ni slippage: el retorno es el del precio de cierre a cierre, nada más.
- No es point-in-time real: usa el `Ticker` tal como existe **hoy** (símbolo, si está activo); no reconstruye qué tickers formaban parte de un índice o watchlist en el pasado, así que no hay supervivencia/sesgo de selección controlado.
- Muestra chica y variable: la cantidad de puntos depende de cuánta historia real tenga cada ticker (con 90 días de `PriceBar` típicos, salen muy pocos puntos) -- los resultados con pocas observaciones por bucket no son estadísticamente concluyentes, son una señal direccional para justificar la ponderación del score, no un backtest de nivel institucional.

## Agente explicador
`agent/services.py::explain_score(symbol)` es tool-calling **real**: el modelo (Gemini) decide qué función invocar y con qué argumentos -- no hay ningún prompt fijo con datos pre-insertados. El loop es manual (`automatic_function_calling` deshabilitado en la SDK) para poder registrar cada llamada, en orden, en `tool_calls` -- esa traza es la prueba de que el tool-calling es real, no un texto fijo.

Tools disponibles (`agent/tools.py`, nunca lanzan excepción, devuelven `{"error": ...}` si algo falla):
- `get_price_history(symbol, days=30)` -- últimas N `PriceBar` (date, close, volume).
- `get_technical_indicators(symbol)` -- el `Score` más reciente con su `components` completo (incluye `atr14`). Sin `Score` calculado devuelve `{"error": "sin score calculado, correr compute_scores primero"}` -- el agente lee esto y lo explica en vez de alucinar un score.
- `get_recent_news(symbol)` -- top 3 titulares vía `.news` de yfinance (gratis, ya es dependencia); si yfinance falla devuelve lista vacía, nunca error (dato complementario, no crítico).

Límite duro de 5 iteraciones de tool-calling (evita loop infinito si el modelo no converge). SDK: **`google-genai`** (`from google import genai`) -- confirmado en PyPI antes de agregarlo: es el paquete activamente mantenido (2.23.0 al momento de esta decisión); el paquete anterior, `google-generativeai`, está deprecado (EOL 2025-11-30).

**Retry con backoff (`_generate_content_with_retry`):** hasta 3 reintentos con backoff exponencial + jitter (2s, 4s, 8s) **solo** ante un 503/`UNAVAILABLE` de Gemini (`google.genai.errors.ServerError` con `code == 503`) -- se confirmó en una corrida real (2026-09-16) que el modelo puede devolver esto por sobrecarga transitoria del lado de Google, sin que el proyecto haya hecho nada mal. Cualquier otro error (401 `UNAUTHENTICATED`, 400, etc. -- subclases de `google.genai.errors.ClientError`) falla inmediato como `AgentError`: no son transitorios, reintentarlos sería solo ruido y demora. Mismo criterio que `01-etl-data-pipeline` aplica a sus reintentos de yfinance: reintentar únicamente lo que es efectivamente transitorio, nunca un error de configuración o de datos.

## API (DRF)
4 endpoints en `api/`, todos `APIView` (no `ModelViewSet` genérico -- la lógica de cada uno no es CRUD estándar):
- `GET /api/watchlist/` -- lista todos los `Watchlist` con sus tickers.
- `POST /api/watchlist/<int:pk>/refresh/` -- para cada `Ticker` del watchlist corre `load_prices_for_ticker` (90 días) y después `compute_score`; devuelve un resumen JSON (`tickers_procesados`, `errores_de_precio`, `scores_actualizados`, `omitidos_por_falta_de_historico`) -- mismo espíritu que el resumen de los management commands, como response en vez de stdout. Un ticker roto no tumba el resto. 404 si el watchlist no existe.
- `GET /api/scores/<str:symbol>/` -- el `Score` más reciente de ese ticker (symbol normalizado a upper). Dos 404 distintos con mensaje distinto: ticker inexistente vs. ticker sin ningún `Score` calculado todavía.
- `GET /api/scores/<str:symbol>/explain/` -- llama a `explain_and_persist(symbol)` y devuelve el `AgentExplanation` serializado. 404 si no hay `Score` (no se pudo persistir); **502** (nunca un 500 crudo) si `explain_and_persist` propaga `AgentError` (Gemini agotó reintentos, o `GEMINI_API_KEY` faltante).

**Decisión de diseño (intencional):** `/explain/` llama a Gemini de forma **síncrona** dentro del request-response -- el loop de tool-calling son 2-4 round trips y puede tardar varios segundos. Para un portafolio de demo esto es aceptable (mover esto a async/Celery queda para la fase de hardening, si hace falta), pero por eso mismo hay que protegerlo con throttling agresivo: cada llamada consume cupo real del free tier de Gemini, y un scraper o bot pegándole a este endpoint en producción puede agotar la cuota del día para todo el proyecto.

**Throttling:** `api/throttling.py::AgentExplainThrottle` (subclase de `AnonRateThrottle`, scope `"agent_explain"`) aplica **solo** en `/explain/`, vía `DEFAULT_THROTTLE_RATES = {"agent_explain": "5/hour"}` en `settings.py`. Los otros 3 endpoints no tienen throttle propio -- solo leen de la base o corren yfinance (sin límite de cuota de terceros con costo real), así que no necesitan un límite tan agresivo.

## Scheduling de la ingesta y el scoring
`load_prices`, `compute_scores` y `explain_score` corren a mano por ahora. La arquitectura de scheduling automático (Celery + django-celery-beat) se implementa recién en la fase de hardening, replicando la decisión ya documentada en `01-etl-data-pipeline/README.md`: Celery Beat queda como diseño, pero lo que efectivamente dispara la ingesta/scoring en producción es un cron de GitHub Actions llamando a los management commands directo (sin worker ni broker) -- un worker de Celery Beat 24/7 no entra en el free tier de Render.

## Apps Django
`market_data`, `scoring`, `backtesting`, `agent`, `api` -- las 5 con modelos y/o lógica implementados (ver secciones arriba). `backtesting` no tiene modelos propios (no persiste corridas, solo calcula y devuelve/imprime).

## Endpoints principales
Los 4 implementados -- ver "API (DRF)" arriba para el detalle:
- `GET /api/watchlist/`
- `POST /api/watchlist/<int:pk>/refresh/`
- `GET /api/scores/<str:symbol>/`
- `GET /api/scores/<str:symbol>/explain/` (dispara el agente, throttled 5/hora)

## Flujo del agente de IA (pieza de mayor señal de este repo) -- implementado
Request → el agente recibe el ticker → invoca tools internas propias (`get_price_history`, `get_technical_indicators`, `get_recent_news`) → el LLM redacta la explicación con esos datos → se persiste como `AgentExplanation` y se devuelve. Ver "Agente explicador" arriba para el detalle de implementación.

## Definición de "listo para entrevista" (Fase 4)
- Backtesting simple del score contra retornos históricos (aunque sea ingenuo) — es lo que separa este proyecto de un dashboard decorativo. **Implementado**, ver "Backtesting" arriba (con sus limitaciones documentadas, no escondidas).
- Tests cubriendo el cálculo de indicadores y el scoring compuesto.
- Deploy en Render, consumiendo datos idealmente ya limpios del repo 1 (o su propia ingesta si el repo 1 no está listo aún).
- README explicando el trade-off de diseño del agente (por qué tool-calling y no RAG aquí).

## Primer prompt sugerido para arrancar
"Lee CLAUDE.md y ../PORTFOLIO_ROADMAP.md. Arranca el proyecto Django con DRF, define los modelos Ticker, PriceBar, Watchlist y WatchlistItem con sus migraciones, y deja el admin de Django configurado para poder cargar un ticker de prueba a mano."
