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

    estado: str  # autorizada | rechazada | pendiente | cargada
    detalle: str
    # El código que se cotiza y se graba. En Sancor es el SUSTITUTO (ver
    # `obras/sancor/reglas.py`), no el que tipeó el médico: es lo que Sancor
    # autoriza y lo que el Colegio factura. El original queda en `traza`.
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

    async def validar(self, ctx: Contexto, entrada: EntradaBase) -> ResultadoValidacion:
        raise NotImplementedError(f"{self.nombre} no implementó validar()")

    async def anular(self, fila: DetalleFacturacionCMC) -> Optional[Anulacion]:
        """Anula en la O.S. la autorización de `fila`. Default: no-op — la baja
        es sólo local, no hay nada que avisarle a la obra social."""
        return None
