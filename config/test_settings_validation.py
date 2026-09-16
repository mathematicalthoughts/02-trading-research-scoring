"""
Valida que config/settings.py falle con un ImproperlyConfigured legible
cuando DATABASE_URL llega vacía o mal formada (en vez del traceback críptico
de dj_database_url, "Scheme '://' is unknown") -- mismo patrón que
01-etl-data-pipeline/config/test_settings_validation.py.

Cada test fuerza la re-ejecución del módulo de settings (importlib.reload)
bajo un DATABASE_URL distinto vía monkeypatch, y siempre lo deja restaurado
al valor real del entorno antes de terminar.
"""

import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured

import config.settings as settings_module


def _reload_with(monkeypatch, value):
    monkeypatch.setenv("DATABASE_URL", value)
    return lambda: importlib.reload(settings_module)


@pytest.mark.parametrize(
    "bad_value",
    ["", "not-a-url-at-all", "://missing-scheme-before-slashes"],
    ids=["vacia", "sin-separador-de-esquema", "empieza-con-separador"],
)
def test_invalid_database_url_raises_improperly_configured(monkeypatch, bad_value):
    reload_settings = _reload_with(monkeypatch, bad_value)
    try:
        with pytest.raises(ImproperlyConfigured, match="DATABASE_URL"):
            reload_settings()
    finally:
        monkeypatch.undo()
        importlib.reload(settings_module)


def test_valid_database_url_does_not_raise_and_configures_database(monkeypatch):
    reload_settings = _reload_with(
        monkeypatch, "postgresql://scott:tiger@localhost:5432/trading_test"
    )
    try:
        reload_settings()
        assert settings_module.DATABASES["default"]["NAME"] == "trading_test"
        assert settings_module.DATABASES["default"]["USER"] == "scott"
        assert settings_module.DATABASES["default"]["HOST"] == "localhost"
    finally:
        monkeypatch.undo()
        importlib.reload(settings_module)


# --- ALLOWED_HOSTS + RENDER_EXTERNAL_HOSTNAME -------------------------------


def test_render_external_hostname_is_added_to_allowed_hosts(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "trading-scoring-abcd.onrender.com")
    try:
        importlib.reload(settings_module)
        assert "trading-scoring-abcd.onrender.com" in settings_module.ALLOWED_HOSTS
        assert "localhost" in settings_module.ALLOWED_HOSTS  # se suma, no reemplaza
    finally:
        monkeypatch.undo()
        importlib.reload(settings_module)


def test_no_render_external_hostname_leaves_allowed_hosts_unaffected(monkeypatch):
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    try:
        importlib.reload(settings_module)
        assert "" not in settings_module.ALLOWED_HOSTS
    finally:
        monkeypatch.undo()
        importlib.reload(settings_module)
