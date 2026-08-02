"""
Utilidades de zona horaria para MyAlmacén.
El negocio opera en Colombia (America/Bogota, UTC-5, sin horario de verano).
Usar SIEMPRE estas funciones en vez de datetime.today()/date.today()/datetime.now()
para cualquier lógica de "día de negocio": ventas, cierres de caja, reportes,
vencimientos de crédito, suscripciones.
"""
from datetime import datetime, date
from zoneinfo import ZoneInfo

BOGOTA_TZ = ZoneInfo("America/Bogota")


def ahora_bogota() -> datetime:
    """Datetime actual, tz-aware, en hora de Bogotá."""
    return datetime.now(BOGOTA_TZ)


def hoy_bogota() -> date:
    """Fecha actual (date) en hora de Bogotá."""
    return ahora_bogota().date()


def ahora_bogota_naive() -> datetime:
    """
    Datetime actual en hora de Bogotá SIN tzinfo, listo para insertar en
    columnas TIMESTAMP (sin zona) de SQLite/Postgres sin que el driver
    reinterprete el offset. Usar esto (no ahora_bogota()) al hacer INSERT.
    """
    return ahora_bogota().replace(tzinfo=None)
