from django.core.management.base import BaseCommand

from scoring.models import Score

from ...models import AgentExplanation
from ...services import AgentError, explain_score


class Command(BaseCommand):
    help = (
        "Corre el agente explicador (tool-calling real sobre Gemini) para "
        "un ticker y persiste el resultado como AgentExplanation, ligado "
        "al Score más reciente de ese ticker."
    )

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, required=True)

    def handle(self, *args, **options):
        symbol = options["symbol"].strip().upper()

        try:
            result = explain_score(symbol)
        except AgentError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        self.stdout.write(result["texto"])
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Tools invocadas:"))
        if result["tool_calls"]:
            for call in result["tool_calls"]:
                self.stdout.write(f"  - {call['tool']}({call['args']})")
        else:
            self.stdout.write("  (ninguna)")

        latest_score = Score.objects.filter(ticker__symbol=symbol).order_by("-date").first()
        if latest_score is None:
            self.stderr.write(
                self.style.WARNING(
                    f"No hay Score persistido para '{symbol}' -- la "
                    "explicación no se guardó como AgentExplanation "
                    "(corré compute_scores primero)."
                )
            )
            return

        AgentExplanation.objects.create(
            score=latest_score,
            texto=result["texto"],
            tool_calls=result["tool_calls"],
        )
        self.stdout.write(self.style.SUCCESS("AgentExplanation guardada."))
