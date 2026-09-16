from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from market_data.models import Ticker


class Score(models.Model):
    ticker = models.ForeignKey(Ticker, on_delete=models.CASCADE, related_name="scores")
    date = models.DateField()
    score = models.IntegerField(validators=[MinValueValidator(0), MaxValueValidator(100)])
    components = models.JSONField()
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["ticker", "date"],
                name="unique_score_per_ticker_date",
            )
        ]

    def __str__(self):
        return f"{self.ticker.symbol} {self.date}: {self.score}"
