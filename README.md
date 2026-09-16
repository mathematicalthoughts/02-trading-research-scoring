# Trading Research & Scoring Platform

> **Estado:** 🚧 en construcción (Fase 2 del roadmap del portafolio — proyecto ancla)

<!-- GIF o captura del demo funcionando va aquí -->

## Qué hace
Evalúa técnicamente un watchlist de acciones (tendencia, momentum, volumen) con un score compuesto 0-100, y un agente de IA con tool-calling que explica en lenguaje natural por qué un ticker obtuvo ese score — invocando las mismas funciones internas del sistema, no un texto genérico.

## Demo
🔗 (link al deploy en Render — agregar cuando esté desplegado)

## Arquitectura
<!-- Diagrama simple: watchlist → ingesta de precios → cálculo de indicadores → scoring → agente explicador (tool-calling) -->

## Quickstart local
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # completar DATABASE_URL, REDIS_URL, GEMINI_API_KEY
python manage.py migrate
python manage.py runserver
```

## Decisiones técnicas
- **Por qué tool-calling y no un prompt con datos pre-insertados:** el agente decide qué información necesita (precio, indicadores, noticias) en vez de recibir todo precalculado — es el patrón real que se usa en producción con agentes.
- **Por qué scoring propio y no un indicador de terceros:** permite explicar cada componente del score en la entrevista con criterio propio, no citando una librería como caja negra.

## Tests
```bash
pytest --cov
```

## Roadmap futuro
- [ ] Backtesting histórico del score contra retornos reales
- [ ] Alertas cuando un ticker cruza un umbral de score
- [ ] Exportar ranking a CSV/PDF
