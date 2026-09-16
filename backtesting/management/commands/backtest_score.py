from django.core.management.base import BaseCommand, CommandError

from market_data.models import Ticker

from ...services import DEFAULT_HORIZON_DAYS, run_backtest


class Command(BaseCommand):
    help = (
        "Backtest 'ingenuo' del Score técnico de un ticker contra sus "
        "retornos reales horizon_days sesiones después -- agrupa los "
        "puntos por bucket de score e imprime n, retorno promedio y win "
        "rate por bucket. Cero look-ahead: cada score se calcula solo "
        "con datos disponibles hasta esa fecha."
    )

    def add_arguments(self, parser):
        parser.add_argument("--ticker", type=str, required=True)
        parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_DAYS)

    def handle(self, *args, **options):
        symbol = options["ticker"].strip().upper()
        horizon_days = options["horizon"]

        try:
            ticker = Ticker.objects.get(symbol=symbol)
        except Ticker.DoesNotExist:
            raise CommandError(f"Ticker '{symbol}' no encontrado.")

        result = run_backtest(ticker, horizon_days=horizon_days)

        if "reason" in result:
            self.stdout.write(self.style.WARNING(result["reason"]))
            return

        self.stdout.write(
            f"Backtest de {result['ticker']} -- horizon={result['horizon_days']} "
            f"sesiones de trading, {result['total_points']} punto(s) totales"
        )
        self.stdout.write("")

        header = f"{'bucket':<10}{'n':>6}{'retorno prom.':>16}{'win rate':>12}"
        self.stdout.write(self.style.SUCCESS(header))
        self.stdout.write("-" * len(header))
        for label, stats in result["buckets"].items():
            self.stdout.write(
                f"{label:<10}{stats['n']:>6}{stats['avg_return_pct']:>15.2f}%"
                f"{stats['win_rate_pct']:>11.1f}%"
            )
