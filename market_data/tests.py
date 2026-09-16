import datetime
from decimal import Decimal

from django.contrib.admin.sites import site
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import PriceBar, Ticker, Watchlist, WatchlistItem


class TickerModelTests(TestCase):
    def test_creates_ticker(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        self.assertEqual(str(ticker), "AAPL")

    def test_symbol_is_unique(self):
        Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Ticker.objects.create(symbol="AAPL", name="Apple Inc. (dup)")

    def test_new_fields_have_expected_defaults(self):
        ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        self.assertEqual(ticker.exchange, "")
        self.assertEqual(ticker.sector, "")
        self.assertTrue(ticker.active)

    def test_new_fields_can_be_set(self):
        ticker = Ticker.objects.create(
            symbol="AAPL",
            name="Apple Inc.",
            exchange="NASDAQ",
            sector="Technology",
            active=False,
        )
        self.assertEqual(ticker.exchange, "NASDAQ")
        self.assertEqual(ticker.sector, "Technology")
        self.assertFalse(ticker.active)


class WatchlistModelTests(TestCase):
    def test_creates_watchlist(self):
        watchlist = Watchlist.objects.create(name="Tech")
        self.assertEqual(str(watchlist), "Tech")

    def test_name_is_unique(self):
        Watchlist.objects.create(name="Tech")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Watchlist.objects.create(name="Tech")


class WatchlistItemModelTests(TestCase):
    def setUp(self):
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")
        self.watchlist = Watchlist.objects.create(name="Tech")

    def test_creates_watchlist_item(self):
        item = WatchlistItem.objects.create(
            watchlist=self.watchlist, ticker=self.ticker
        )
        self.assertEqual(str(item), "AAPL @ Tech")

    def test_ticker_cannot_repeat_in_same_watchlist(self):
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=self.ticker)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WatchlistItem.objects.create(
                    watchlist=self.watchlist, ticker=self.ticker
                )

    def test_same_ticker_allowed_in_different_watchlist(self):
        other_watchlist = Watchlist.objects.create(name="Momentum")
        WatchlistItem.objects.create(watchlist=self.watchlist, ticker=self.ticker)
        item = WatchlistItem.objects.create(
            watchlist=other_watchlist, ticker=self.ticker
        )
        self.assertEqual(item.ticker, self.ticker)


class PriceBarModelTests(TestCase):
    def setUp(self):
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")

    def _make_bar(self, **overrides):
        defaults = dict(
            ticker=self.ticker,
            date=datetime.date(2026, 9, 1),
            open=Decimal("150.1234"),
            high=Decimal("151.5000"),
            low=Decimal("149.0000"),
            close=Decimal("150.7500"),
            volume=1_000_000,
        )
        defaults.update(overrides)
        return PriceBar.objects.create(**defaults)

    def test_creates_price_bar(self):
        bar = self._make_bar()
        self.assertEqual(str(bar), "AAPL 2026-09-01")
        self.assertEqual(bar.close, Decimal("150.7500"))

    def test_ticker_and_date_pair_is_unique(self):
        self._make_bar()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._make_bar()

    def test_same_date_allowed_for_different_ticker(self):
        other_ticker = Ticker.objects.create(symbol="MSFT", name="Microsoft Corp.")
        self._make_bar()
        bar = self._make_bar(ticker=other_ticker)
        self.assertEqual(bar.ticker, other_ticker)

    def test_default_ordering_is_by_date_descending(self):
        older = self._make_bar(date=datetime.date(2026, 8, 1))
        newer = self._make_bar(date=datetime.date(2026, 9, 1))
        self.assertEqual(list(PriceBar.objects.all()), [newer, older])


class AdminRegistrationTests(TestCase):
    def test_models_are_registered_in_admin(self):
        self.assertIn(Ticker, site._registry)
        self.assertIn(Watchlist, site._registry)
        self.assertIn(WatchlistItem, site._registry)
        self.assertIn(PriceBar, site._registry)
