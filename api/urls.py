from django.urls import path

from .views import (
    ScoreDetailView,
    ScoreExplainView,
    WatchlistListView,
    WatchlistRefreshView,
)

urlpatterns = [
    path("watchlist/", WatchlistListView.as_view(), name="watchlist-list"),
    path(
        "watchlist/<int:pk>/refresh/",
        WatchlistRefreshView.as_view(),
        name="watchlist-refresh",
    ),
    path("scores/<str:symbol>/", ScoreDetailView.as_view(), name="score-detail"),
    path("scores/<str:symbol>/explain/", ScoreExplainView.as_view(), name="score-explain"),
]
