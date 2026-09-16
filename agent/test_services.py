"""
Tests de explain_score. NUNCA le pegan a la API real de Gemini: se
mockea agent.services.genai.Client por completo. Los tipos de
google.genai.types sí se usan reales (Content, Part, FunctionCall,
FunctionResponse) -- son objetos livianos sin red, y usarlos reales deja
la prueba más fiel a la forma real de la conversación multi-turno.
"""

import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from google.genai import types

from market_data.models import PriceBar, Ticker
from scoring.models import Score

from .services import MAX_TOOL_CALL_ITERATIONS, AgentError, explain_score


def _fake_response(function_calls=None, text=""):
    response = MagicMock()
    response.function_calls = function_calls or []
    response.text = text
    response.candidates = [MagicMock(content=types.Content(role="model", parts=[]))]
    return response


def _fake_call(name, args):
    return types.FunctionCall(name=name, args=args)


@override_settings(GEMINI_API_KEY="fake-key-for-tests", GEMINI_MODEL="gemini-test")
class ExplainScoreToolCallingTests(TestCase):
    def setUp(self):
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        Score.objects.create(
            ticker=self.ticker,
            date=datetime.date(2026, 1, 2),
            score=70,
            components={"atr14": 2.0, "trend_points": 40},
        )
        PriceBar.objects.create(
            ticker=self.ticker,
            date=datetime.date(2026, 1, 2),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=1000,
        )

    def test_multi_step_tool_calling_calls_local_functions_in_order(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            _fake_response(
                function_calls=[_fake_call("get_technical_indicators", {"symbol": "AAPL"})]
            ),
            _fake_response(
                function_calls=[_fake_call("get_price_history", {"symbol": "AAPL", "days": 10})]
            ),
            _fake_response(text="AAPL tiene un score de 70, con tendencia alcista confirmada."),
        ]

        with patch("agent.services.get_technical_indicators") as mock_indicators, patch(
            "agent.services.get_price_history"
        ) as mock_history, patch("agent.services.genai.Client", return_value=mock_client):
            mock_indicators.return_value = {"score": 70}
            mock_history.return_value = {"bars": []}

            result = explain_score("AAPL")

        mock_indicators.assert_called_once_with(symbol="AAPL")
        mock_history.assert_called_once_with(symbol="AAPL", days=10)
        self.assertEqual(
            result["tool_calls"],
            [
                {"tool": "get_technical_indicators", "args": {"symbol": "AAPL"}},
                {"tool": "get_price_history", "args": {"symbol": "AAPL", "days": 10}},
            ],
        )
        self.assertIn("70", result["texto"])
        self.assertEqual(mock_client.models.generate_content.call_count, 3)

    def test_ticker_without_score_does_not_crash_and_tool_reports_absence(self):
        Ticker.objects.create(symbol="ZZZ", name="Sin score S.A.")

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            _fake_response(
                function_calls=[_fake_call("get_technical_indicators", {"symbol": "ZZZ"})]
            ),
            _fake_response(
                text="ZZZ todavía no tiene un score calculado, así que no puedo explicar su setup técnico."
            ),
        ]

        with patch("agent.services.genai.Client", return_value=mock_client):
            # get_technical_indicators NO se mockea acá -- corre real
            # contra la DB para probar que el error de la tool llega tal
            # cual a Gemini.
            result = explain_score("ZZZ")

        self.assertIn("no tiene", result["texto"].lower())
        self.assertEqual(
            result["tool_calls"],
            [{"tool": "get_technical_indicators", "args": {"symbol": "ZZZ"}}],
        )

        # contents es la MISMA lista mutable durante toda la corrida (el
        # mock no la copia), así que para leer lo que se envió en la
        # segunda llamada hay que indexar la posición que ese append dejó
        # fija (índice 2) -- contents[-1] sería el turno del modelo
        # agregado DESPUÉS de la segunda llamada, ya mutado para cuando
        # el test lo inspecciona.
        second_call_kwargs = mock_client.models.generate_content.call_args_list[1].kwargs
        function_response_part = second_call_kwargs["contents"][2].parts[0]
        self.assertEqual(
            function_response_part.function_response.response,
            {"error": "sin score calculado, correr compute_scores primero"},
        )

    def test_missing_api_key_raises_clear_error_not_traceback(self):
        with override_settings(GEMINI_API_KEY=""):
            with self.assertRaises(AgentError) as ctx:
                explain_score("AAPL")

        self.assertIn("GEMINI_API_KEY", str(ctx.exception))

    def test_gemini_api_failure_raises_clear_agent_error(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = TimeoutError("Gemini no respondió")

        with patch("agent.services.genai.Client", return_value=mock_client):
            with self.assertRaises(AgentError) as ctx:
                explain_score("AAPL")

        self.assertIn("Gemini", str(ctx.exception))

    def test_non_converging_loop_is_cut_at_max_iterations(self):
        mock_client = MagicMock()
        # Siempre pide la misma tool, nunca devuelve texto final.
        mock_client.models.generate_content.side_effect = [
            _fake_response(
                function_calls=[_fake_call("get_technical_indicators", {"symbol": "AAPL"})]
            )
        ] * (MAX_TOOL_CALL_ITERATIONS + 5)

        with patch("agent.services.genai.Client", return_value=mock_client):
            result = explain_score("AAPL")

        self.assertEqual(
            mock_client.models.generate_content.call_count, MAX_TOOL_CALL_ITERATIONS
        )
        self.assertEqual(len(result["tool_calls"]), MAX_TOOL_CALL_ITERATIONS)
        self.assertIn("no convergió", result["texto"])

    def test_recent_news_failure_does_not_break_full_flow(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            _fake_response(function_calls=[_fake_call("get_recent_news", {"symbol": "AAPL"})]),
            _fake_response(text="No encontré noticias recientes, pero el score es sólido."),
        ]

        with patch("agent.tools.yf.Ticker", side_effect=ConnectionError("boom")), patch(
            "agent.services.genai.Client", return_value=mock_client
        ):
            result = explain_score("AAPL")

        self.assertEqual(
            result["tool_calls"], [{"tool": "get_recent_news", "args": {"symbol": "AAPL"}}]
        )
        # Ver comentario equivalente más arriba: índice fijo (2), no [-1].
        second_call_kwargs = mock_client.models.generate_content.call_args_list[1].kwargs
        function_response_part = second_call_kwargs["contents"][2].parts[0]
        self.assertEqual(
            function_response_part.function_response.response,
            {"symbol": "AAPL", "news": []},
        )
        self.assertIn("score", result["texto"].lower())
