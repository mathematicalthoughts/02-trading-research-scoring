from market_data.models import Watchlist


def nav_watchlists(request):
    """
    Disponible en todos los templates de dashboard/ vía TEMPLATES en
    settings.py -- alimenta el rail lateral (Dashboard + tickers de
    cada watchlist) sin que cada vista tenga que repetir esta query.
    """
    return {"nav_watchlists": Watchlist.objects.prefetch_related("items__ticker")}
