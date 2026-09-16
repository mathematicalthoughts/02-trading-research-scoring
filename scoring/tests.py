from django.contrib.admin.sites import site
from django.db import IntegrityError, transaction
from django.test import TestCase

from market_data.models import Ticker

from .models import Score


class ScoreModelTests(TestCase):
    def setUp(self):
        self.ticker = Ticker.objects.create(symbol="AAPL", name="Apple Inc.")

    def test_creates_score(self):
        score = Score.objects.create(
            ticker=self.ticker,
            date="2026-09-01",
            score=75,
            components={"sma20": 100.0},
        )
        self.assertEqual(str(score), "AAPL 2026-09-01: 75")
        self.assertIsNotNone(score.computed_at)

    def test_ticker_and_date_pair_is_unique(self):
        Score.objects.create(
            ticker=self.ticker, date="2026-09-01", score=50, components={}
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Score.objects.create(
                    ticker=self.ticker, date="2026-09-01", score=60, components={}
                )

    def test_same_date_allowed_for_different_ticker(self):
        other_ticker = Ticker.objects.create(symbol="MSFT", name="Microsoft Corp.")
        Score.objects.create(
            ticker=self.ticker, date="2026-09-01", score=50, components={}
        )
        score = Score.objects.create(
            ticker=other_ticker, date="2026-09-01", score=60, components={}
        )
        self.assertEqual(score.ticker, other_ticker)


class AdminRegistrationTests(TestCase):
    def test_score_is_registered_in_admin(self):
        self.assertIn(Score, site._registry)
