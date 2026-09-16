from django.core.management.base import BaseCommand

from ...services import AgentError, explain_and_persist


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
            explanation, error = explain_and_persist(symbol)
        except AgentError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        if error:
            self.stderr.write(self.style.WARNING(error))
            return

        self.stdout.write(explanation.texto)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Tools invocadas:"))
        if explanation.tool_calls:
            for call in explanation.tool_calls:
                self.stdout.write(f"  - {call['tool']}({call['args']})")
        else:
            self.stdout.write("  (ninguna)")

        self.stdout.write(self.style.SUCCESS("AgentExplanation guardada."))
