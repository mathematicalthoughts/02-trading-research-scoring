from django.db import models


class Ticker(models.Model):
    symbol = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    exchange = models.CharField(max_length=20, blank=True)
    sector = models.CharField(max_length=100, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["symbol"]

    def __str__(self):
        return self.symbol


class Watchlist(models.Model):
    name = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class WatchlistItem(models.Model):
    watchlist = models.ForeignKey(
        Watchlist, on_delete=models.CASCADE, related_name="items"
    )
    ticker = models.ForeignKey(
        Ticker, on_delete=models.CASCADE, related_name="watchlist_items"
    )

    class Meta:
        ordering = ["watchlist", "ticker"]
        constraints = [
            models.UniqueConstraint(
                fields=["watchlist", "ticker"],
                name="unique_ticker_per_watchlist",
            )
        ]

    def __str__(self):
        return f"{self.ticker.symbol} @ {self.watchlist.name}"


class PriceBar(models.Model):
    ticker = models.ForeignKey(
        Ticker, on_delete=models.CASCADE, related_name="price_bars"
    )
    date = models.DateField()
    open = models.DecimalField(max_digits=12, decimal_places=4)
    high = models.DecimalField(max_digits=12, decimal_places=4)
    low = models.DecimalField(max_digits=12, decimal_places=4)
    close = models.DecimalField(max_digits=12, decimal_places=4)
    volume = models.BigIntegerField()

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["ticker", "date"],
                name="unique_price_bar_per_ticker_date",
            )
        ]

    def __str__(self):
        return f"{self.ticker.symbol} {self.date}"
