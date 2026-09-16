from django.contrib import admin

from .models import PriceBar, Ticker, Watchlist, WatchlistItem


@admin.register(Ticker)
class TickerAdmin(admin.ModelAdmin):
    list_display = ("symbol", "name")
    search_fields = ("symbol", "name")


class WatchlistItemInline(admin.TabularInline):
    model = WatchlistItem
    extra = 1


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    inlines = [WatchlistItemInline]


@admin.register(WatchlistItem)
class WatchlistItemAdmin(admin.ModelAdmin):
    list_display = ("watchlist", "ticker")
    list_filter = ("watchlist",)


@admin.register(PriceBar)
class PriceBarAdmin(admin.ModelAdmin):
    list_display = ("ticker", "date", "close", "volume")
    list_filter = ("ticker",)
