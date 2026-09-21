"""OSPM (O.S. 433) — valida contra el padrón propio (`clientes_ospm`), sin
servicio externo.

Es la MISMA tabla que usa el legacy: el padrón es uno solo, así que el PHP
viejo y la API validan siempre contra el mismo dato. `obras/ospm/padron.py`
reemplaza el padrón entero, igual que `importar_padron_ospm.php`.

Activo → autoriza y factura de verdad (ver `ValidadorOspm.validar()`); es la
primera obra social "contra padrón" que efectivamente autoriza — hasta el
2026-09-20 cualquier resultado se grababa `rechazada`, así que nunca facturaba
ni abría una factura para OSPM.
"""
import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClientesOspm, DetalleFacturacionCMC
from app.modules.validaciones.core.contrato import CERO, Contexto, ResultadoValidacion, ValidadorOS
from app.modules.validaciones.core.grabado import DETALLE_ACTIVO
from app.modules.validaciones.obras.ospm.routes import router as _router
from app.modules.validaciones.obras.ospm.schemas import EntradaOspm

# Sólo los códigos de consulta (42*) tienen el tope de uno por afiliado y día.
# Es una regla del convenio de OSPM, replicada de grabar_prestacion_ospm_1.php.
PREFIJO_CONSULTA = "42"


class ValidadorOspm(ValidadorOS):
    def __init__(self):
        super().__init__(
            nro=433,
            nombre="OSPM",
            entrada=EntradaOspm,
            modalidad="contra padrón",
            router=_router,
            prefijo="/ospm",
        )

    async def validar(self, ctx: Contexto, entrada: EntradaOspm) -> ResultadoValidacion:
        """OSPM no tiene servicio de autorización en línea: se resuelve con un
        solo dato local, **el afiliado**, buscado en el padrón (`clientes_ospm`)
        por DNI.

        | Afiliado | Resultado |
        |---|---|
        | activo | `autorizada` — factura |
        | existe, inactivo | `rechazada` — "Rechazado. Afiliado suspendido" |
        | no está en el padrón | `rechazada` — "Rechazado. Afiliado inexistente" |

        Los dos rechazos quedan igual de grabados que el autorizado (fila en
        `detalle_facturacion` con `estado='X'`, sin facturar) — a diferencia de
        un DNI vacío o un padrón sin importar, que siguen siendo errores duros
        (422) porque no son un hecho sobre un afiliado real, sino un problema
        de la carga o del sistema.
        """
        doc = (entrada.documento or "").strip()
        if not doc:
            raise HTTPException(422, "Falta el DNI del afiliado.")

        afiliado = await self._buscar_afiliado(ctx.db, doc)

        if afiliado is not None and await self._duplicado(
            ctx.db, codigo=entrada.codigo, nro_afiliado=doc, fecha=ctx.fecha,
        ):
            raise HTTPException(
                422,
                f"Por convenio, el afiliado {doc} y la prestación {entrada.codigo} no pueden "
                "cargarse más de una vez en la misma fecha.",
            )

        precio = await ctx.precio(entrada.codigo)

        if afiliado is None:
            estado, detalle = "rechazada", "Rechazado. Afiliado inexistente"
            nombre_afiliado = ""
            traza_padron = {"documento": doc, "encontrado": False}
        elif not afiliado.activo:
            estado, detalle = "rechazada", "Rechazado. Afiliado suspendido"
            nombre_afiliado = afiliado.nombre
            traza_padron = {
                "documento": doc, "cuit": afiliado.CUIT,
                "nombre": afiliado.nombre, "activo": afiliado.activo,
            }
        else:
            estado, detalle = "autorizada", "Autorizado. Afiliado activo en el padrón de OSPM."
            nombre_afiliado = afiliado.nombre
            traza_padron = {
                "documento": doc, "cuit": afiliado.CUIT,
                "nombre": afiliado.nombre, "activo": afiliado.activo,
            }

        return ResultadoValidacion(
            estado=estado,
            detalle=detalle,
            codigo=entrada.codigo,
            precio=precio,
            nro_afiliado=doc,
            nombre_afiliado=nombre_afiliado,
            # Sin nº de autorización: OSPM no es un servicio en línea, no hay
            # ningún número que la obra social le dé al Colegio (a diferencia
            # de Sancor/Nobis/OSPJN).
            nro_autorizacion=None,
            coseguro=CERO,  # OSPM no cobra coseguro (el legacy lo fija en 0)
            traza={"padron": traza_padron},
        )

    async def _buscar_afiliado(self, db: AsyncSession, doc: str) -> Optional[ClientesOspm]:
        """Busca el afiliado por DNI. `None` si no está en el padrón — ya no es
        un error, es uno de los tres desenlaces posibles de `validar()`.

        El padrón vacío sigue siendo un error duro (422): con el padrón sin
        importar NADIE valida, y el prestador no tiene forma de saber que el
        problema no es su DNI.
        """
        fila = (
            await db.execute(select(ClientesOspm).where(ClientesOspm.DU == doc))
        ).scalar_one_or_none()

        if fila is None:
            total = int((await db.execute(select(func.count(ClientesOspm.ID)))).scalar_one() or 0)
            if total == 0:
                raise HTTPException(
                    422,
                    "El padrón de OSPM todavía no fue importado. Avisá al Colegio "
                    "para que cargue el padrón vigente.",
                )
        return fila

    async def _duplicado(
        self, db: AsyncSession, *, codigo: str, nro_afiliado: str, fecha: datetime.date
    ) -> bool:
        """¿Ya hay una consulta cargada para ese afiliado, código y día?

        Por convenio OSPM admite una sola consulta (códigos 42*) por afiliado y
        fecha. Se mira sobre `detalle_facturacion` —no sobre `guardar_atencion`, que
        es del legacy— y se ignoran las anuladas/fuera de factura: si la anterior se
        dio de baja, el cupo del día vuelve a estar libre.
        """
        if not codigo.startswith(PREFIJO_CONSULTA):
            return False

        existe = (
            await db.execute(
                select(DetalleFacturacionCMC.id_detalle_prestaciones)
                .where(
                    DetalleFacturacionCMC.cod_obr == str(self.nro),
                    DetalleFacturacionCMC.cod_nom == codigo,
                    DetalleFacturacionCMC.dni_p == nro_afiliado,
                    DetalleFacturacionCMC.fecha_practica == fecha,
                    DetalleFacturacionCMC.estado == DETALLE_ACTIVO,
                )
                .limit(1)
            )
        ).first()
        return existe is not None
