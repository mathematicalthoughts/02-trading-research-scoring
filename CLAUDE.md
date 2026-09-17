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
- Ingesta de precios (comando manual): `python manage.py load_prices [--tickers=AAPL,MSFT] [--days=90]` descarga OHLCV vía yfinance y hace upsert idempotente en `PriceBar`. `load_prices_for_ticker` saltea (no guarda, no rompe el resto) cualquier fila con Open/High/Low/Close/Volume = NaN -- común en la barra del día en curso, o por dividendos/splits sin ajustar todavía. **Bug real ya resuelto:** hasta hace poco el loop de escritura vivía fuera del try/except (que solo cubría el fetch), así que una fila NaN hacía explotar `int(NaN)` sin capturar y se propagaba como un 500 crudo en `DashboardView.post` (que solo atrapa `PriceLoadError`) al refrescar un ticker recién agregado -- todo el fetch+escritura vive ahora bajo el mismo try/except.
- Motor de scoring (comando manual): `python manage.py compute_scores [--tickers=AAPL,MSFT]` calcula el `Score` técnico (0-100) de cada ticker a partir de sus `PriceBar` y hace upsert idempotente en `Score` por (ticker, fecha=hoy). Requiere al menos 50 `PriceBar` por ticker (lo que pide SMA50); si no alcanza, loguea el motivo y sigue con el resto sin romper la corrida.
- Agente explicador (comando manual): `python manage.py explain_score --symbol=AAPL` corre tool-calling real sobre Gemini (el modelo decide qué tools invocar) y persiste el resultado como `AgentExplanation`. Ver "Agente explicador" más abajo. La lógica de "correr el agente y persistir" vive en una sola función (`agent/services.py::explain_and_persist`), compartida entre este comando y el endpoint `GET /api/scores/<symbol>/explain/` -- ver "API (DRF)".
- API REST (`api/`, DRF con `APIView`, no `ModelViewSet` genérico -- ningún endpoint es CRUD estándar): expone `market_data`, `scoring` y `agent` vía HTTP. Ver "API (DRF)" más abajo.
- Backtesting (comando manual): `python manage.py backtest_score --ticker=AAPL [--horizon=10]` corre el backtest ingenuo del `Score` contra retornos reales. Ver "Backtesting" más abajo.
- Frontend server-side (`dashboard/`, 3 vistas HTML): Dashboard, detalle de ticker (con chart de precio+SMAs y el agente explicador) y backtest. Ver "Frontend" más abajo.

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

`_PROMPT_TEMPLATE` le pide explícitamente al modelo que no mencione los nombres de las tools ni su sintaxis de llamada (ej. `get_price_history(...)`) en el texto final -- sin esa instrucción, esas frases aparecían pegadas al final de `explanation.texto`, duplicando lo que ya se muestra aparte en el `agent-trace-item` de `ticker_detail.html`. Solo un test de mock confirma que la instrucción está en el prompt enviado; que Gemini efectivamente deje de hacerlo se verifica a mano contra una corrida real.

**Retry con backoff (`_generate_content_with_retry`):** hasta 3 reintentos con backoff exponencial + jitter (2s, 4s, 8s) **solo** ante un 503/`UNAVAILABLE` de Gemini (`google.genai.errors.ServerError` con `code == 503`) -- se confirmó en una corrida real (2026-09-16) que el modelo puede devolver esto por sobrecarga transitoria del lado de Google, sin que el proyecto haya hecho nada mal. Cualquier otro error (401 `UNAUTHENTICATED`, 400, etc. -- subclases de `google.genai.errors.ClientError`) falla inmediato como `AgentError`: no son transitorios, reintentarlos sería solo ruido y demora. Mismo criterio que `01-etl-data-pipeline` aplica a sus reintentos de yfinance: reintentar únicamente lo que es efectivamente transitorio, nunca un error de configuración o de datos.

## API (DRF)
4 endpoints en `api/`, todos `APIView` (no `ModelViewSet` genérico -- la lógica de cada uno no es CRUD estándar):
- `GET /api/watchlist/` -- lista todos los `Watchlist` con sus tickers.
- `POST /api/watchlist/<int:pk>/refresh/` -- para cada `Ticker` del watchlist corre `load_prices_for_ticker` (90 días) y después `compute_score`; devuelve un resumen JSON (`tickers_procesados`, `errores_de_precio`, `scores_actualizados`, `omitidos_por_falta_de_historico`) -- mismo espíritu que el resumen de los management commands, como response en vez de stdout. Un ticker roto no tumba el resto. 404 si el watchlist no existe.
- `GET /api/scores/<str:symbol>/` -- el `Score` más reciente de ese ticker (symbol normalizado a upper). Dos 404 distintos con mensaje distinto: ticker inexistente vs. ticker sin ningún `Score` calculado todavía.
- `GET /api/scores/<str:symbol>/explain/` -- llama a `explain_and_persist(symbol)` y devuelve el `AgentExplanation` serializado. 404 si no hay `Score` (no se pudo persistir); **502** (nunca un 500 crudo) si `explain_and_persist` propaga `AgentError` (Gemini agotó reintentos, o `GEMINI_API_KEY` faltante).

**Decisión de diseño (intencional):** `/explain/` llama a Gemini de forma **síncrona** dentro del request-response -- el loop de tool-calling son 2-4 round trips y puede tardar varios segundos. Para un portafolio de demo esto es aceptable (mover esto a async/Celery queda para la fase de hardening, si hace falta), pero por eso mismo hay que protegerlo con throttling agresivo: cada llamada consume cupo real del free tier de Gemini, y un scraper o bot pegándole a este endpoint en producción puede agotar la cuota del día para todo el proyecto.

**Throttling:** `api/throttling.py::AgentExplainThrottle` (subclase de `AnonRateThrottle`, scope `"agent_explain"`) aplica **solo** en `/explain/`, vía `DEFAULT_THROTTLE_RATES = {"agent_explain": "5/hour"}` en `settings.py`. Los otros 3 endpoints no tienen throttle propio -- solo leen de la base o corren yfinance (sin límite de cuota de terceros con costo real), así que no necesitan un límite tan agresivo.

## Frontend
`dashboard/` (server-side, Django templates) -- 3 vistas, ninguna toca `api/`, `agent/`, `scoring/` ni `backtesting/` como lógica de negocio, solo las **consume** (llaman a `market_data.services`, `scoring.services`, `backtesting.services` directo):
- `GET /dashboard/` (`DashboardView.get`) -- lista los `Watchlist` con sus tickers, el último `PriceBar.close` (columna "Precio") y el `Score` más reciente de cada uno. `prefetch_related("items__ticker__scores", "items__ticker__price_bars")` sobre `api/views.py::WatchlistListView`, sin N+1 (`ticker.scores.all()[0]` / `ticker.price_bars.all()[0]` sobre el queryset ya prefetcheado -- nunca `.first()`, que dispara una query nueva y rompe el prefetch cache; verificado con `CaptureQueriesContext` en un test, mismo conteo de queries con 2 tickers que con 4). `POST /dashboard/` (`DashboardView.post`, botón "Refrescar precios") corre el mismo loop que `api/views.py::WatchlistRefreshView` -- `load_prices_for_ticker` + `compute_score` por ticker -- pero invocando los servicios directo, no la vista DRF; el resumen se muestra como mensaje flash (`django.contrib.messages`), no como JSON. **`compute_score(ticker)` solo calcula, no persiste** -- la vista hace su propio `Score.objects.update_or_create(ticker, date=hoy, defaults={score, components})`, igual que ya hace `scoring/management/commands/compute_scores.py`. *(Bug real encontrado corriendo el flujo completo contra yfinance real, no con pytest: antes de este fix "Refrescar precios" reportaba "N score(s) actualizado(s)" sin haber guardado ningún `Score` -- `api/views.py::WatchlistRefreshView` tiene el mismo problema y sigue sin corregir, fuera de alcance de `dashboard/`.)*
- `GET /dashboard/ticker/<symbol>/` (`TickerDetailView`) -- Score vigente con su `components`, chart de precio+SMA20+SMA50 (Chart.js, últimos 60 `PriceBar`; la SMA se recalcula día a día llamando `scoring.indicators.sma` repetidamente, mismo patrón que usa `backtesting/services.py` para el score), un sparkline en el header (estilo Apple Stocks, sin ejes/leyenda, últimos 20 puntos de la MISMA serie `close` que ya calculó `_price_chart_data` -- no repite la query de precios) coloreado alcista/bajista según si el último close del período es mayor o menor al primero, y la sección "Agente explicador". Sin `Score` calculado: estado vacío explicando que hace falta `compute_scores`, nunca un error crudo. Sin `PriceBar` (`chart_data` es `None`): tampoco se muestra el sparkline, sin crashear.
- `GET /dashboard/ticker/<symbol>/backtest/` (`BacktestView`) -- formulario `horizon_days` (default 10, GET), llama a `run_backtest` directo, grafica el retorno promedio por bucket como barras divergentes alrededor de cero (verde/rojo según signo), y muestra **siempre** el bloque de limitaciones (mismo texto que "Backtesting" arriba) visible en la página, nunca en un tooltip.

**CRUD de Watchlist/Ticker** (antes solo existía por el admin de Django, insuficiente para una demo en vivo):
- `GET/POST /dashboard/watchlists/new/` (`WatchlistCreateView`, form `WatchlistForm`) -- crea un `Watchlist` (solo `name`, único).
- `GET/POST /dashboard/watchlists/<pk>/delete/` (`WatchlistDeleteView`) -- **acción destructiva con confirmación explícita**: el GET muestra nombre + cantidad de tickers vinculados, el POST recién ahí borra (nunca un botón de un solo click). Borrar un watchlist no borra `Ticker`/`PriceBar`/`Score`, solo el watchlist y sus `WatchlistItem`.
- `GET/POST /dashboard/watchlists/<pk>/tickers/add/` (`WatchlistTickerAddView`, form `TickerAddForm`) -- el form pide **solo `symbol`** (normalizado a upper/strip). La vista llama a `market_data.services.fetch_ticker_metadata(symbol)` (yfinance `get_info()`) para resolver `name`/`exchange` automáticamente; si el símbolo no resuelve a un instrumento real, levanta `TickerMetadataError` y la vista muestra "no encontramos ese ticker, revisá el símbolo" **sin crear nada** -- no existe otra forma de saber si el symbol es válido antes de que reviente en el refresh. Recién si resuelve: `Ticker.objects.get_or_create(symbol=..., defaults={"name":..., "exchange":...})` (si el símbolo ya existe como `Ticker`, se reusa tal cual, no se pisa nombre/exchange) y después `WatchlistItem.objects.get_or_create(watchlist, ticker)` -- si el link ya existía, no se duplica y se informa por `messages.error` en vez de romper. También hay un form idéntico (solo el campo symbol) embebido directo en `dashboard.html` (siempre visible dentro de cada panel de watchlist) que postea a esta misma vista.

  **Excepción documentada a "nunca llamar servicios externos desde una vista sync del dashboard":** originalmente el form pedía `name`/`exchange` a mano, lo cual permitía crear un `Ticker` con un `symbol` inválido -- el error recién aparecía como un 500 al apretar "Refrescar precios" más tarde (yfinance no encontraba histórico). `fetch_ticker_metadata` es la única llamada a un servicio externo desde una vista sync de `dashboard/`, y se justifica porque agregar un ticker es una acción administrativa de baja frecuencia (no un endpoint público expuesto a tráfico externo), no porque la regla general haya cambiado -- cualquier otra vista nueva sigue debiendo consumir solo los servicios internos.
- `POST /dashboard/watchlists/<pk>/tickers/<item_id>/remove/` (`WatchlistTickerRemoveView`) -- solo POST (GET da 405 a propósito). Sin confirmación aparte: remover un ticker del watchlist no borra ningún dato, solo el link, y se puede volver a agregar.
- `GET /dashboard/watchlists/<pk>/export.csv` (`WatchlistExportView`, sin template) -- `HttpResponse(content_type="text/csv")` con `Content-Disposition: attachment; filename="watchlist_<slug-del-nombre>_<YYYY-MM-DD>.csv"`. Columnas: `Ticker, Nombre, Score, Fecha, Tendencia, Momentum, Volumen, RSI14, SMA20, SMA50, ATR14, VolRelativo` -- las últimas 8 salen de `Score.components` (mismas claves que ya usa `ticker_detail.html`). Un ticker sin `Score` genera su fila igual, con esas columnas vacías (nunca se omite del CSV).

**Ranking (`DashboardView.get`):** los tickers de cada watchlist se ordenan por `score.score` descendente, con los sin `Score` siempre al final (comparar `None` contra `int` rompería, así que el sort usa `(score is None, -score o 0, symbol)` como clave). Al ticker en el **primer lugar** (ya ordenado), si su banda es `band-top` (`score >= 80`, mismo corte de `backtesting.services.DEFAULT_BUCKET_EDGES` que ya usan los pills) se le agrega el indicador "★ Mejor setup" junto al pill. Si ningún ticker del watchlist llega a `band-top`, no se muestra ningún indicador -- nunca se corona al "mejor de los peores".

**Decisión de arquitectura (intencional, no te desvíes de esto sin repensarlo primero):** las vistas de `dashboard/` llaman a los servicios Python directo -- nunca hacen una request HTTP interna a `api/`. La única excepción es el botón **"Explicar"** en `ticker_detail.html`: ese sí hace `fetch()` client-side a `GET /api/scores/<symbol>/explain/`, el endpoint DRF ya existente, para que `AgentExplainThrottle` (5/hora) siga siendo el **único** punto de control de cuota hacia Gemini. Si el agente se llamara directo desde una vista SSR, se abriría un segundo camino sin límite hacia una API paga -- eso es lo que esta regla evita. Si ya existe una `AgentExplanation` para el `Score` vigente, se muestra server-side de entrada, sin gastar cupo. El fetch maneja 200 (muestra texto + traza de tools), 429 (throttled -- mensaje en texto plano: "alcanzaste el límite de consultas al agente") y 502 (`AgentError` -- el mensaje de la excepción, en texto plano) explícitamente, nunca un error crudo de JS en consola; muestra un spinner mientras espera (la llamada real tarda varios segundos por el tool-calling).

**Sistema de color:** tokens en `dashboard/static/dashboard/styles.css` como variables CSS, con variante dark vía `prefers-color-scheme` (sin toggle manual). El acento de marca (`#B9791F` / `#E3A23F`) está reservado **solo** para nav activo, foco y botón primario -- nunca para alcista/bajista. Alcista (`#1F8F68` / `#3FB68A`) y bajista (`#C23F33` / `#E2685C`) están reservados exclusivamente para señal de score/retorno (pills, deltas, barras del backtest, el indicador "★ Mejor setup"), nunca decorativos; se usan como texto/borde sobre un fondo tintado (`rgba`, no el color sólido de fondo) para no depender de blanco-sobre-color en el cálculo de contraste AA. Los pills de banda de score reusan los cortes de `backtesting.services.DEFAULT_BUCKET_EDGES` (40/60/80) vía `dashboard/presentation.py::score_band_class` -- no hay umbrales nuevos inventados en el frontend. Tipografía: Archivo (700/600, títulos), Work Sans (400/500, texto de interfaz), IBM Plex Mono (400/500/600, todo número/precio/score/traza del agente) vía Google Fonts. Escala: `h1` 1.4rem/700 (título de página / símbolo de ticker, sin uppercase forzado); `.panel__title` (los `h2` de cada sección) 0.8rem uppercase, letter-spacing 0.04em, color muted -- tratamiento "eyebrow" de dashboard, no título de documento; `h3`/`h4` 0.95rem.

**Por qué CSS propio y no un framework JS (React/Vue/etc.):** cero build step, cero infraestructura nueva -- coherente con el presupuesto cero del portafolio (mismo criterio que llevó a Neon/Render free tier y GitHub Actions en vez de un worker 24/7). Bootstrap se sacó del proyecto por completo (ver más abajo): ningún template usaba su grid/utilidades, y su CSS pisaba silenciosamente el color de texto dentro de `.panel` -- todo el look sale de `styles.css`. Chart.js (CDN) para los 2 charts (precio+SMAs, backtest) -- misma lógica de "sin build step".

**Nota de una regresión ya resuelta (queda documentada para no repetirla):** la clase se llama `.panel`, no `.card`, a propósito -- Bootstrap define su propio componente `.card` con `color: var(--bs-body-color)` fijo en el claro de Bootstrap (nunca se seteaba `data-bs-theme="dark"`); como nuestro `.card` no definía `color` explícito, el de Bootstrap ganaba y el texto quedaba oscuro sobre fondo oscuro en dark mode. El fix real fue sacar el `<link>` a Bootstrap de `base.html` (ningún template lo necesitaba); el rename a `.panel` es la salvaguarda para que una futura librería de CSS no vuelva a chocar con un nombre de clase tan genérico.

**Nota de infraestructura:** `STORAGES["staticfiles"]` usa `whitenoise.storage.CompressedManifestStaticFilesStorage` solo cuando `not DEBUG and not TESTING` (requiere el manifest que genera `collectstatic` en el build de Render); en dev local y en tests cae a `django.contrib.staticfiles.storage.StaticFilesStorage` sin manifest -- si no, `{% static %}` rompe con "Missing staticfiles manifest entry" fuera de un `collectstatic` real.

## Scheduling de la ingesta y el scoring
`load_prices`, `compute_scores` y `explain_score` corren a mano por ahora. La arquitectura de scheduling automático (Celery + django-celery-beat) se implementa recién en la fase de hardening, replicando la decisión ya documentada en `01-etl-data-pipeline/README.md`: Celery Beat queda como diseño, pero lo que efectivamente dispara la ingesta/scoring en producción es un cron de GitHub Actions llamando a los management commands directo (sin worker ni broker) -- un worker de Celery Beat 24/7 no entra en el free tier de Render.

## Apps Django
`market_data`, `scoring`, `backtesting`, `agent`, `api`, `dashboard` -- las 6 con modelos y/o lógica implementados (ver secciones arriba). `backtesting` y `dashboard` no tienen modelos propios (no persisten nada, solo calculan/renderizan).

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
