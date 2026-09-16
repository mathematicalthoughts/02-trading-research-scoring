from django.db import models

from scoring.models import Score


class AgentExplanation(models.Model):
    score = models.ForeignKey(Score, on_delete=models.CASCADE, related_name="explanations")
    texto = models.TextField()
    tool_calls = models.JSONField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"Explicación de {self.score} @ {self.timestamp:%Y-%m-%d %H:%M}"
