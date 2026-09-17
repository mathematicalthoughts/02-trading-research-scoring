from django.urls import path

from .views import (
    BacktestView,
    DashboardView,
    TickerDetailView,
    WatchlistCreateView,
    WatchlistDeleteView,
    WatchlistExportView,
    WatchlistTickerAddView,
    WatchlistTickerRemoveView,
)

app_name = "dashboard"

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("watchlists/new/", WatchlistCreateView.as_view(), name="watchlist-create"),
    path(
        "watchlists/<int:pk>/delete/",
        WatchlistDeleteView.as_view(),
        name="watchlist-delete",
    ),
    path(
        "watchlists/<int:pk>/tickers/add/",
        WatchlistTickerAddView.as_view(),
        name="watchlist-ticker-add",
    ),
    path(
        "watchlists/<int:pk>/tickers/<int:item_id>/remove/",
        WatchlistTickerRemoveView.as_view(),
        name="watchlist-ticker-remove",
    ),
    path(
        "watchlists/<int:pk>/export.csv",
        WatchlistExportView.as_view(),
        name="watchlist-export",
    ),
    path("ticker/<str:symbol>/", TickerDetailView.as_view(), name="ticker-detail"),
    path(
        "ticker/<str:symbol>/backtest/",
        BacktestView.as_view(),
        name="ticker-backtest",
    ),
]
