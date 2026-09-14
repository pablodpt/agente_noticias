"""Motor de screening UCITS (no ETF) y construcción de portafolio por régimen."""
from .motor import cargar_screener, construir_portafolio, detectar_regimen

__all__ = ["cargar_screener", "construir_portafolio", "detectar_regimen"]
