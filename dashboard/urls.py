from django.urls import path

from .views import BacktestView, DashboardView, TickerDetailView

app_name = "dashboard"

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("ticker/<str:symbol>/", TickerDetailView.as_view(), name="ticker-detail"),
    path(
        "ticker/<str:symbol>/backtest/",
        BacktestView.as_view(),
        name="ticker-backtest",
    ),
]
