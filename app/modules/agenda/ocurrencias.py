"""Dónde cae un evento del almanaque. Lo usan la grilla del mes y los avisos del día."""
import calendar
import datetime
from typing import List

from app.db.models import AgendaEvento


def dia_valido(anio: int, mes: int, dia: int) -> datetime.date:
    """El día pedido dentro de ese mes, corrido al último si no existe.

    El 31 en un mes de 30 y el 29 de febrero fuera de año bisiesto se resuelven
    hacia atrás y no salteándose el mes: una tarea "el 31 se cierra el período"
    tiene que aparecer en abril, y el cumpleaños del 29/2 se saluda el 28. Que
    el evento desaparezca del calendario sería peor que mostrarlo un día antes.
    """
    ultimo = calendar.monthrange(anio, mes)[1]
    return datetime.date(anio, mes, min(dia, ultimo))


def ocurrencias(evento: AgendaEvento, anio: int, mes: int) -> List[datetime.date]:
    """En qué días de ese mes cae este evento. Lista vacía si no cae."""
    if evento.recurrencia == "unica":
        if evento.fecha and evento.fecha.year == anio and evento.fecha.month == mes:
            return [evento.fecha]
        return []

    if evento.recurrencia == "anual":
        if evento.mes == mes and evento.dia:
            return [dia_valido(anio, mes, evento.dia)]
        return []

    # mensual: cae todos los meses, siempre.
    if evento.dia:
        return [dia_valido(anio, mes, evento.dia)]
    return []
