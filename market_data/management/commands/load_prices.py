import logging

from django.core.management.base import BaseCommand

from market_data.models import Ticker
from market_data.services import PriceLoadError, load_prices_for_ticker

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Descarga el histórico diario OHLCV vía yfinance para un conjunto de "
        "tickers y lo guarda en PriceBar (update_or_create por ticker+date, "
        "idempotente). Corrida manual -- todavía sin Celery Beat ni "
        "scheduling automático (ver 'Estado actual' en CLAUDE.md; la "
        "arquitectura de scheduling se define en la fase de hardening, "
        "replicando la decisión de 01-etl-data-pipeline: Celery Beat como "
        "diseño, GitHub Actions como lo que efectivamente corre en el free "
        "tier de Render)."
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
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Días hacia atrás a descargar (default: 90).",
        )

    def handle(self, *args, **options):
        days = options["days"]
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

        processed = 0
        errors = []
        rows_upserted = 0

        for ticker in tickers:
            processed += 1
            try:
                rows = load_prices_for_ticker(ticker, days)
            except PriceLoadError as exc:
                errors.append(ticker.symbol)
                logger.error("load_prices: error en %s: %s", ticker.symbol, exc)
                self.stderr.write(self.style.ERROR(f"FAIL {ticker.symbol}: {exc}"))
                continue

            rows_upserted += rows
            self.stdout.write(
                self.style.SUCCESS(f"OK   {ticker.symbol}: {rows} fila(s)")
            )

        summary = (
            f"load_prices: {processed} ticker(s) procesado(s), "
            f"{len(errors)} con error, "
            f"{rows_upserted} fila(s) de PriceBar creada(s)/actualizada(s)."
        )
        logger.info(summary)
        self.stdout.write(self.style.SUCCESS(summary))
