"""El contrato entre `core/pipeline.py` y cada obra social integrada.

Cualquier obra social que se anime a validar en línea (o contra un padrón, o
simplemente registrar una carga manual) implementa `ValidadorOS.validar()` y
devuelve un `ResultadoValidacion`. Es el único punto de encuentro real entre
los 6 flujos: lo que cada una recibe de su propio servicio externo (SOAP,
REST, padrón) se queda en el cliente de esa O.S. — acá sólo llega lo que hace
falta para grabar la fila y responder al prestador. Unificar también los DTOs
de transporte (la respuesta cruda de Sancor/Nobis/OSPJN) repetiría un nivel
más abajo el problema que ya tiene `PrestacionCreate`: un schema-unión donde
cada O.S. usa un subconjunto y nadie sabe cuál.
"""
import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.modules.facturacion.schemas import PrecioResponse
from app.modules.facturacion.service import resolver_precio
from app.modules.validaciones.core.grabado import CERO
from app.modules.validaciones.core.medicos import especialidades_de
from app.modules.validaciones.schemas import EntradaBase


@dataclass
class ResultadoValidacion:
    """Lo que produce cualquier O.S. y consume `grabar_prestacion`."""

    # autorizada | rechazada | cargada. Lo que la O.S. deja a la espera de que
    # el afiliado lo gestione va como `rechazada` con el motivo adelante — ver
    # `core/grabado.py`. "pendiente" ya no se emite; sobrevive sólo en filas
    # viejas.
    estado: str
    detalle: str
    # El código que se cotiza y se graba: **siempre el del Colegio**, el que
    # eligió el médico. Cuando la obra social exige otro (ver `homologar` más
    # abajo), el homologado sólo viaja en el mensaje a la O.S. y queda anotado
    # en `traza["codigo_enviado"]`.
    codigo: str
    precio: PrecioResponse
    nro_afiliado: str  # ya formateado ("123/01")
    nombre_afiliado: str = ""
    nro_autorizacion: Optional[str] = None
    coseguro: Decimal = CERO  # sólo Boreal descuenta
    traza: Optional[dict] = None  # → validacion_respuesta


@dataclass
class Anulacion:
    """Lo que la O.S. dejó al anular una autorización previa."""

    traza: dict
    detalle: Optional[str] = None  # si viene, pisa `validacion_detalle`


@dataclass
class Contexto:
    """Lo que cualquier validador necesita para operar, ya resuelto."""

    db: AsyncSession
    medico: ListadoMedico
    obra_social: int
    periodo: str  # YYYYMM, con el gate de período ya pasado
    fecha: datetime.date
    usuario_carga: int

    async def precio(self, codigo: str, *, exigir_admitido: bool = True) -> PrecioResponse:
        """`resolver_precio` de facturación, con el chequeo de admitido opcional.

        Lo llama el validador, no el pipeline: Sancor lo necesita DOS veces con
        tolerancias distintas — el camino de gestión presencial no factura, así
        que tolera un código sin precio vigente antes que perder la constancia.
        """
        precio = await resolver_precio(
            self.db, str(self.obra_social), self.medico, codigo, self.fecha
        )
        if exigir_admitido and not precio.admitido:
            raise HTTPException(422, precio.motivo or "El código no está habilitado.")
        return precio

    def especialidades(self) -> list[int]:
        return especialidades_de(self.medico)

    def especialidad_principal(self) -> Optional[int]:
        """`NRO_ESPECIALIDAD`, la del slot 1. Es la que decide la homologación.

        Distinta de `especialidades()`, que devuelve las 6 y la usa el lookup de
        precios: para homologar alcanza y sobra con la principal. De 101 médicos
        activos con las especialidades homologadas hoy, uno solo la tiene en un
        slot secundario y nunca cargó esos códigos.
        """
        esp = getattr(self.medico, "NRO_ESPECIALIDAD", None)
        return int(esp) if esp else None


class ValidadorOS:
    """Una obra social integrada. Se instancia una vez, en `obras/<os>/__init__.py`.

    `validar()` es lo único que hace falta sobreescribir para el caso mínimo
    (una carga manual, sin servicio externo). `verificar_prestador()` y
    `anular()` tienen default de no-op — hoy sólo Sancor usa el primero
    (matrícula obligatoria) y sólo Sancor/Nobis el segundo.
    """

    def __init__(
        self,
        *,
        nro: int,
        nombre: str,
        entrada: type[EntradaBase],
        modalidad: str = "en línea",
        router: Optional[APIRouter] = None,
        prefijo: str = "",
    ):
        self.nro = nro
        self.nombre = nombre
        self.entrada = entrada
        self.modalidad = modalidad
        self.router = router
        self.prefijo = prefijo

    def verificar_prestador(self, medico: ListadoMedico) -> None:
        """Precondiciones sobre el médico, ANTES de tocar el período. Default: ninguna."""
        return None

    def homologar(self, codigo: str, especialidad: Optional[int]) -> tuple[str, Optional[str]]:
        """Con qué código hay que hablarle a esta obra social.

        Devuelve `(código a enviar, código del Colegio si hubo homologación)`.
        **Default: identidad** — la mayoría de las obras sociales acepta el
        código del Colegio tal cual. Hoy sólo Sancor lo sobreescribe, con la
        tabla de `obras/sancor/homologador.py`.

        Homologar cambia **sólo lo que se transmite**. Lo que se cotiza, se
        graba y se factura es siempre el código del Colegio; por eso este hook
        lo usan tanto el alta como el buscador de códigos, y no puede volver a
        haber dos criterios como cuando la sustitución vivía dentro del cliente
        de Sancor.

        Recibe la especialidad y no el `Contexto` a propósito: el buscador de
        códigos resuelve sobre un médico, sin período ni fecha, así que armar
        un `Contexto` sólo para consultar la tabla sería de más.
        """
        return (codigo, None)

    async def validar(self, ctx: Contexto, entrada: EntradaBase) -> ResultadoValidacion:
        raise NotImplementedError(f"{self.nombre} no implementó validar()")

    async def anular(self, fila: DetalleFacturacionCMC) -> Optional[Anulacion]:
        """Anula en la O.S. la autorización de `fila`. Default: no-op — la baja
        es sólo local, no hay nada que avisarle a la obra social.

        Puede **vetar la baja** levantando una `HTTPException`: corre antes de
        que `core/pipeline.py::eliminar_prestacion()` toque la fila, así que la
        excepción deja todo como estaba. Es lo que hace Sancor cuando el Z04 no
        vuelve confirmado — sin OK de la obra social no se borra de este lado.
        """
        return None
