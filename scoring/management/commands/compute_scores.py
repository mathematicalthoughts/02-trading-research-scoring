import logging
from datetime import date as date_cls

from django.core.management.base import BaseCommand

from market_data.models import Ticker
from scoring.models import Score
from scoring.services import compute_score

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Calcula el Score técnico (tendencia/momentum/volumen) de un "
        "conjunto de tickers a partir de su PriceBar y hace "
        "update_or_create en Score por (ticker, date=hoy) -- idempotente. "
        "Corrida manual, mismo patrón que load_prices."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tickers",
            type=str,
            default=None,
            help=(
                "Símbolos separados por coma (ej. AAPL,MSFT). Si se omite, "
                "se usan todos los Ticker con active=True."
            ),
        )

    def handle(self, *args, **options):
        tickers_arg = options["tickers"]

        if tickers_arg:
            symbols = [s.strip().upper() for s in tickers_arg.split(",") if s.strip()]
            tickers = list(Ticker.objects.filter(symbol__in=symbols))
            missing = sorted(set(symbols) - {t.symbol for t in tickers})
            if missing:
                self.stderr.write(
                    self.style.WARNING(
                        f"Tickers no encontrados en la base, se omiten: "
                        f"{', '.join(missing)}"
                    )
                )
        else:
            tickers = list(Ticker.objects.filter(active=True))

        if not tickers:
            self.stdout.write(self.style.WARNING("No hay tickers para procesar."))
            return

        today = date_cls.today()
        processed = 0
        skipped = 0
        errored = 0
        upserted = 0

        for ticker in tickers:
            processed += 1
            try:
                score, details = compute_score(ticker)
            except Exception as exc:
                errored += 1
                logger.error(
                    "compute_scores: error inesperado en %s: %s", ticker.symbol, exc
                )
                self.stderr.write(self.style.ERROR(f"FAIL {ticker.symbol}: {exc}"))
                continue

            if score is None:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"SKIP {ticker.symbol}: {details}"))
                continue

            Score.objects.update_or_create(
                ticker=ticker,
                date=today,
                defaults={"score": score, "components": details},
            )
            upserted += 1
            self.stdout.write(self.style.SUCCESS(f"OK   {ticker.symbol}: score={score}"))

        summary = (
            f"compute_scores: {processed} ticker(s) procesado(s), "
            f"{skipped} omitido(s) por falta de histórico, "
            f"{errored} con error, "
            f"{upserted} score(s) creado(s)/actualizado(s)."
        )
        logger.info(summary)
        self.stdout.write(self.style.SUCCESS(summary))
