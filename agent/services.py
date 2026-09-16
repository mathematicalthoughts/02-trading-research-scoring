"""
explain_score(symbol): agente explicador con tool-calling REAL sobre
Gemini -- el modelo decide qué tools invocar y con qué argumentos; acá
no hay ningún prompt fijo con datos pre-insertados. El loop de
tool-calling es manual (automatic_function_calling deshabilitado) para
poder registrar cada llamada, en orden, y así poder demostrar en
entrevista que es tool-calling real.

SDK: google-genai (`from google import genai`) -- es el SDK oficial
vigente; el paquete anterior, google-generativeai, está deprecado (EOL
2025-11-30, ver requirements.txt).
"""

import logging
import random
import time

from django.conf import settings
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .tools import get_price_history, get_recent_news, get_technical_indicators

logger = logging.getLogger(__name__)

MAX_TOOL_CALL_ITERATIONS = 5

# Retry con backoff exponencial + jitter, SOLO para 503/UNAVAILABLE de
# Gemini (sobrecarga transitoria del servidor -- confirmado en una
# corrida real contra la API, ver CLAUDE.md). Cualquier otro error (401,
# 400, etc.) no es transitorio: reintentarlo sería solo ruido, así que
# se propaga de inmediato como AgentError, sin pasar por acá.
MAX_GEMINI_RETRIES = 3  # reintentos además del intento inicial (4 intentos en total)
RETRY_BACKOFF_BASE_SECONDS = 2  # 2s, 4s, 8s (más jitter)


def _is_retryable_unavailable_error(exc: Exception) -> bool:
    return isinstance(exc, genai_errors.ServerError) and getattr(exc, "code", None) == 503


def _generate_content_with_retry(client, *, model, contents, config):
    """
    client.models.generate_content con reintentos SOLO ante un
    503/UNAVAILABLE. Cualquier otra excepción (incluido un 4xx de
    genai_errors.ClientError) se propaga en el primer intento, sin
    reintentar.
    """
    total_attempts = MAX_GEMINI_RETRIES + 1

    for attempt in range(1, total_attempts + 1):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except genai_errors.APIError as exc:
            is_last_attempt = attempt == total_attempts
            if not _is_retryable_unavailable_error(exc) or is_last_attempt:
                raise

            delay = (RETRY_BACKOFF_BASE_SECONDS**attempt) + random.uniform(0, 1)
            logger.warning(
                "explain_score: Gemini 503/UNAVAILABLE (intento %s de %s), "
                "reintentando en %.1fs",
                attempt,
                total_attempts,
                delay,
            )
            time.sleep(delay)


_TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="get_price_history",
        description=(
            "Devuelve el histórico reciente de precios (fecha, cierre, "
            "volumen) de un ticker, ordenado del más antiguo al más "
            "reciente."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Símbolo del ticker, ej. AAPL.",
                },
                "days": {
                    "type": "integer",
                    "description": "Cantidad de días hacia atrás a traer (default 30).",
                },
            },
            "required": ["symbol"],
        },
    ),
    types.FunctionDeclaration(
        name="get_technical_indicators",
        description=(
            "Devuelve el Score técnico más reciente de un ticker ya "
            "calculado: score (0-100), fecha, y el detalle de cada "
            "indicador (sma20, sma50, rsi14, relative_volume, atr14) "
            "junto con los sub-puntajes de tendencia/momentum/volumen."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Símbolo del ticker, ej. AAPL.",
                },
            },
            "required": ["symbol"],
        },
    ),
    types.FunctionDeclaration(
        name="get_recent_news",
        description="Devuelve hasta 3 titulares de noticias recientes de un ticker.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Símbolo del ticker, ej. AAPL.",
                },
            },
            "required": ["symbol"],
        },
    ),
]

_PROMPT_TEMPLATE = (
    "Sos un analista técnico. Tenés disponibles tres herramientas: "
    "get_price_history (histórico de precios), get_technical_indicators "
    "(el Score técnico ya calculado, con el detalle de cada indicador) y "
    "get_recent_news (titulares recientes). Decidí vos mismo cuáles "
    "necesitás y en qué orden -- no asumas ningún dato, consultalo con "
    "las herramientas. Si get_technical_indicators devuelve un error "
    "(por ejemplo, porque todavía no se calculó ningún score), decilo "
    "explícitamente en la respuesta en vez de inventar un score.\n\n"
    "Cuando tengas suficiente información, respondé en 3-5 líneas, en "
    "español, explicando el setup técnico del ticker {symbol}: el "
    "score y el porqué (tendencia, momentum, volumen), mencionando el "
    "ATR como referencia de volatilidad (no como parte del score)."
)


class AgentError(Exception):
    """Error irrecuperable al explicar un score: config faltante o falla de la API de Gemini."""


def explain_score(symbol: str) -> dict:
    """
    Devuelve {"texto": str, "tool_calls": [{"tool": str, "args": dict}, ...]}
    con la traza completa, en orden, de qué tools invocó el modelo.
    """
    if not settings.GEMINI_API_KEY:
        raise AgentError(
            "GEMINI_API_KEY no está configurada -- seteala en .env para "
            "poder usar el agente explicador."
        )

    # Dict armado adentro de la función (no a nivel de módulo) para que
    # los tests puedan mockear agent.services.get_price_history/etc. y
    # que el dispatch los recoja en cada corrida.
    tool_functions = {
        "get_price_history": get_price_history,
        "get_technical_indicators": get_technical_indicators,
        "get_recent_news": get_recent_news,
    }

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    tool = types.Tool(function_declarations=_TOOL_DECLARATIONS)
    config = types.GenerateContentConfig(
        tools=[tool],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text=_PROMPT_TEMPLATE.format(symbol=symbol.upper()))],
        )
    ]

    tool_calls_made = []

    for _ in range(MAX_TOOL_CALL_ITERATIONS):
        try:
            response = _generate_content_with_retry(
                client, model=settings.GEMINI_MODEL, contents=contents, config=config
            )
        except Exception as exc:
            raise AgentError(f"Error llamando a la API de Gemini: {exc}") from exc

        contents.append(response.candidates[0].content)

        function_calls = response.function_calls
        if not function_calls:
            return {"texto": response.text or "", "tool_calls": tool_calls_made}

        function_response_parts = []
        for call in function_calls:
            call_args = dict(call.args or {})
            tool_calls_made.append({"tool": call.name, "args": call_args})

            tool_fn = tool_functions.get(call.name)
            result = (
                {"error": f"tool desconocida: {call.name}"}
                if tool_fn is None
                else tool_fn(**call_args)
            )

            function_response_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=call.name, response=result
                    )
                )
            )

        contents.append(types.Content(role="user", parts=function_response_parts))

    logger.warning(
        "explain_score: '%s' no convergió en %s iteraciones de tool-calling",
        symbol,
        MAX_TOOL_CALL_ITERATIONS,
    )
    return {
        "texto": (
            "No se pudo generar una explicación final: el modelo no "
            f"convergió tras {MAX_TOOL_CALL_ITERATIONS} iteraciones de "
            "tool-calling."
        ),
        "tool_calls": tool_calls_made,
    }
