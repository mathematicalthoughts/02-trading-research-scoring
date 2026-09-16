"""
Glue de presentación puro (sin lógica de negocio): mapea un score a una
clase CSS de "banda" para los pills de la UI. Reusa el mismo corte de
buckets que backtesting/services.py (40/60/80) -- no se inventan
umbrales nuevos acá, solo se consumen.
"""

from backtesting.services import DEFAULT_BUCKET_EDGES, _bucket_label

_BAND_CSS_CLASS = {
    "0-40": "band-low",
    "40-60": "band-mid",
    "60-80": "band-high",
    "80-100": "band-top",
}


def score_band_class(score):
    """Clase CSS del pill de score, o "" si `score` es None (sin Score calculado)."""
    if score is None:
        return ""
    label = _bucket_label(score, DEFAULT_BUCKET_EDGES)
    return _BAND_CSS_CLASS.get(label, "")
