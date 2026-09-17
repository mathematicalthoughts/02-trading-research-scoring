from django import forms

from market_data.models import Watchlist


class WatchlistForm(forms.ModelForm):
    class Meta:
        model = Watchlist
        fields = ["name"]


class TickerAddForm(forms.Form):
    """
    Solo valida el symbol -- name/exchange se resuelven vía
    market_data.services.fetch_ticker_metadata (yfinance) en la vista, que
    también hace el get_or_create por symbol y la prevención de
    WatchlistItem duplicado (con su mensaje vía django.contrib.messages).
    Ver CLAUDE.md, sección "Frontend", para por qué esta es la única
    excepción a "nunca llamar servicios externos desde una vista sync del
    dashboard" -- la carga de precios sigue siendo responsabilidad
    exclusiva del botón "Refrescar precios" ya existente.
    """

    symbol = forms.CharField(
        max_length=20,
        label="Símbolo",
        widget=forms.TextInput(attrs={"placeholder": "NVDA"}),
    )

    def clean_symbol(self):
        return self.cleaned_data["symbol"].strip().upper()
