from rest_framework import serializers

from agent.models import AgentExplanation
from market_data.models import Ticker, Watchlist
from scoring.models import Score


class TickerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ticker
        fields = ["symbol", "name", "exchange", "sector", "active"]


class WatchlistSerializer(serializers.ModelSerializer):
    tickers = serializers.SerializerMethodField()

    class Meta:
        model = Watchlist
        fields = ["id", "name", "tickers"]

    def get_tickers(self, obj):
        # obj.items.all() -- si la vista prefetch_related("items__ticker"),
        # esto no dispara queries extra por watchlist.
        return TickerSerializer(
            [item.ticker for item in obj.items.all()], many=True
        ).data


class ScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Score
        fields = ["score", "date", "components"]


class AgentExplanationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentExplanation
        fields = ["texto", "tool_calls", "timestamp"]
