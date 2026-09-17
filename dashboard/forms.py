from django import forms

from market_data.models import Watchlist


class WatchlistForm(forms.ModelForm):
    class Meta:
        model = Watchlist
        fields = ["name"]


class TickerAddForm(forms.Form):
    """
    Solo valida los campos del formulario -- el get_or_create por symbol y
    la prevención de WatchlistItem duplicado (con su mensaje vía
    django.contrib.messages) viven en la vista, que sí tiene acceso a
    `request`. Nunca golpea yfinance ni ningún servicio externo acá: la
    carga de precios sigue siendo responsabilidad del botón "Refrescar
    precios" ya existente.
    """

    symbol = forms.CharField(max_length=20, label="Símbolo")
    name = forms.CharField(max_length=255, label="Nombre")
    exchange = forms.CharField(max_length=20, required=False, label="Exchange")

    def clean_symbol(self):
        return self.cleaned_data["symbol"].strip().upper()
