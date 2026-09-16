from rest_framework.throttling import AnonRateThrottle


class AgentExplainThrottle(AnonRateThrottle):
    """
    Throttle propio (scope "agent_explain", ver DEFAULT_THROTTLE_RATES en
    settings.py) para GET /api/scores/<symbol>/explain/ -- el único
    endpoint que efectivamente golpea la API de Gemini. Los otros 3
    endpoints leen de la propia base o corren yfinance, sin consumir
    cuota de un servicio de terceros con límite diario, así que no
    necesitan este límite agresivo.
    """

    scope = "agent_explain"
