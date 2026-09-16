"""
Tools que el agente explicador puede invocar (tool-calling real vía
Gemini -- ver agent/services.py). Cada función:
- Recibe `symbol` (además de sus propios parámetros) y devuelve un dict
  serializable a JSON.
- Nunca lanza excepción -- ante cualquier fallo devuelve
  {"error": "..."} (o, en get_recent_news, una lista vacía) para que el
  agente pueda leerlo y explicarlo en la respuesta en vez de crashear.
"""

import logging

import yfinance as yf

from market_data.models import Ticker

logger = logging.getLogger(__name__)


def get_price_history(symbol: str, days: int = 30) -> dict:
    """Últimas `days` PriceBar del ticker (date, close, volume), más antigua primero."""
    try:
        ticker = Ticker.objects.get(symbol=symbol.upper())
        bars = list(ticker.price_bars.order_by("-date")[:days])
        bars.reverse()
        return {
            "symbol": ticker.symbol,
            "bars": [
                {
                    "date": bar.date.isoformat(),
                    "close": float(bar.close),
                    "volume": bar.volume,
                }
                for bar in bars
            ],
        }
    except Ticker.DoesNotExist:
        return {"error": f"ticker '{symbol}' no encontrado"}
    except Exception as exc:
        logger.exception("get_price_history: error inesperado para %s", symbol)
        return {"error": str(exc)}


def get_technical_indicators(symbol: str) -> dict:
    """El Score más reciente del ticker: score, date y su dict de components completo."""
    try:
        ticker = Ticker.objects.get(symbol=symbol.upper())
        score = ticker.scores.order_by("-date").first()
        if score is None:
            return {"error": "sin score calculado, correr compute_scores primero"}
        return {
            "symbol": ticker.symbol,
            "score": score.score,
            "date": score.date.isoformat(),
            "components": score.components,
        }
    except Ticker.DoesNotExist:
        return {"error": f"ticker '{symbol}' no encontrado"}
    except Exception as exc:
        logger.exception("get_technical_indicators: error inesperado para %s", symbol)
        return {"error": str(exc)}


def get_recent_news(symbol: str) -> dict:
    """
    Top 3 titulares recientes vía yfinance (`.news`, gratis, ya es
    dependencia del proyecto). Si yfinance no devuelve nada o falla,
    devuelve lista vacía -- nunca error, las noticias son un dato
    complementario, no crítico para la explicación.
    """
    try:
        raw_items = yf.Ticker(symbol.upper()).news or []
    except Exception:
        logger.exception("get_recent_news: yfinance falló para %s", symbol)
        return {"symbol": symbol.upper(), "news": []}

    headlines = []
    for item in raw_items[:3]:
        content = item.get("content", item) or {}
        provider = content.get("provider") or {}
        canonical_url = content.get("canonicalUrl") or {}
        headlines.append(
            {
                "title": content.get("title", ""),
                "publisher": provider.get("displayName", ""),
                "link": canonical_url.get("url", ""),
            }
        )

    return {"symbol": symbol.upper(), "news": headlines}
