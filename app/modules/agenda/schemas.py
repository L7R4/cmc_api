"""Entrada y salida de los calendarios.

El trabajo real de este archivo es una sola invariante, en `_coherencia()`: que
la recurrencia y los campos de fecha se correspondan. Sin eso entran filas que
la expansión a días concretos no sabe dónde poner —un `'anual'` sin mes, un
`'unica'` sin fecha— y el evento simplemente no aparece nunca en el calendario,
que es el peor modo de fallar: silencioso y sin error.
"""
import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

TipoEvento = Literal["feriado", "cumpleanos", "tarea"]
Recurrencia = Literal["unica", "anual", "mensual"]

#: Recurrencia por defecto de cada calendario, para cuando el front no la manda.
#: Es lo que la gente quiere decir el 90% de las veces: un feriado es de un año
#: puntual salvo aviso, un cumpleaños se repite cada año y una tarea, cada mes.
RECURRENCIA_POR_TIPO: dict[str, str] = {
    "feriado": "unica",
    "cumpleanos": "anual",
    "tarea": "mensual",
}


class EventoIn(BaseModel):
    tipo: TipoEvento
    titulo: str = Field(..., min_length=1, max_length=200)
    descripcion: Optional[str] = Field(None, max_length=500)

    recurrencia: Optional[Recurrencia] = None
    fecha: Optional[datetime.date] = None
    dia: Optional[int] = Field(None, ge=1, le=31)
    mes: Optional[int] = Field(None, ge=1, le=12)

    medico_id: Optional[int] = Field(None, ge=1)
    responsable: Optional[str] = Field(None, max_length=120)
    color: Optional[str] = Field(None, max_length=9)
    activo: bool = True

    @model_validator(mode="after")
    def _coherencia(self):
        """Completa la recurrencia y exige los campos que ésa necesita.

        También **deriva** lo que se pueda en vez de rechazar: si alguien manda
        un cumpleaños con la fecha completa (que es como lo tiene cargado el
        legajo del socio), se le extraen el día y el mes en vez de devolverle un
        422. La fecha con año se descarta ahí: para un evento anual, el año de
        nacimiento no ubica nada en el almanaque.
        """
        if self.recurrencia is None:
            self.recurrencia = RECURRENCIA_POR_TIPO[self.tipo]

        if self.recurrencia in ("anual", "mensual") and self.fecha and self.dia is None:
            self.dia = self.fecha.day
            if self.recurrencia == "anual" and self.mes is None:
                self.mes = self.fecha.month

        if self.recurrencia == "unica":
            if self.fecha is None:
                raise ValueError("Un evento de fecha única necesita la fecha.")
            # Día y mes son ruido acá: la fecha ya los contiene y dejarlos
            # cargados haría que dos fuentes digan lo mismo y puedan divergir.
            self.dia = self.mes = None

        elif self.recurrencia == "anual":
            if self.dia is None or self.mes is None:
                raise ValueError("Un evento anual necesita día y mes.")
            self.fecha = None

        else:  # mensual
            if self.dia is None:
                raise ValueError("Un evento mensual necesita el día del mes.")
            self.fecha = self.mes = None

        return self


class EventoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo: TipoEvento
    titulo: str
    descripcion: Optional[str] = None
    recurrencia: Recurrencia
    fecha: Optional[datetime.date] = None
    dia: Optional[int] = None
    mes: Optional[int] = None
    medico_id: Optional[int] = None
    responsable: Optional[str] = None
    color: Optional[str] = None
    activo: bool = True


class ResponsableOut(BaseModel):
    """Alguien del personal del Colegio, para el selector de responsable.

    `nombre` es lo que se guarda en `AgendaEvento.responsable` — la columna es
    texto y no un FK. Ver el docstring del endpoint para por qué.
    """

    #: `ListadoMedico.ID`. No se guarda en el evento; sirve como key de la lista.
    id: int
    nombre: str
    #: Uno o más: en la base hay gente con `facturador` y `liquidador` a la vez.
    roles: List[str] = []


class OcurrenciaOut(EventoOut):
    """Un evento ya ubicado en un día concreto del mes consultado.

    Es lo que consume la grilla del calendario: le llega `2026-03-14` y no
    "día 14, todos los años", así que no tiene que replicar las reglas de
    expansión —incluida la del 31 en meses de 30— en TypeScript.
    """

    #: El día concreto en el mes pedido. Siempre presente.
    ocurre_el: datetime.date
