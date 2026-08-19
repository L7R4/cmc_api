"""OSPM (O.S. 433) — valida contra el padrón propio (`clientes_ospm`), sin
servicio externo.

Es la MISMA tabla que usa el legacy: el padrón es uno solo, así que el PHP
viejo y la API validan siempre contra el mismo dato. `obras/ospm/padron.py`
reemplaza el padrón entero, igual que `importar_padron_ospm.php`.
"""
import datetime

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
        """OSPM no tiene servicio de autorización: se resuelve con un solo dato
        local, **el afiliado**, buscado en el padrón por DNI. Si no está, no se
        graba nada.

        De ahí salen dos desenlaces:

        | Afiliado | Resultado |
        |---|---|
        | activo | `rechazada` — el afiliado gestiona la autorización en la O.S. |
        | inactivo | `rechazada` — no figura en el padrón |

        Los dos desenlaces graban `rechazada` y se distinguen por el motivo. El
        afiliado activo era `pendiente` hasta que se unificó el criterio: acá no
        se autoriza nada, así que la prestación no se factura, y un estado
        propio hacía leer como "en trámite" algo que el Colegio no está
        tramitando.

        Que un código pueda saltearse la autorización es criterio por convenio y
        todavía no está resuelto acá, así que se toma siempre el caso restrictivo:
        mejor mandar a gestionar de más que dar por autorizado algo que la obra
        social después rechaza.
        """
        afiliado = await self._afiliado(ctx.db, entrada.documento)
        doc = afiliado.documento

        if await self._duplicado(ctx.db, codigo=entrada.codigo, nro_afiliado=doc, fecha=ctx.fecha):
            raise HTTPException(
                422,
                f"Por convenio, el afiliado {doc} y la prestación {entrada.codigo} no pueden "
                "cargarse más de una vez en la misma fecha.",
            )

        precio = await ctx.precio(entrada.codigo)

        if not afiliado.activo:
            estado, detalle = "rechazada", "El afiliado no figura activo en el padrón de OSPM."
        else:
            estado, detalle = (
                "rechazada",
                "Pendiente de autorización de la obra social. El afiliado tiene que "
                "gestionarla en OSPM.",
            )

        return ResultadoValidacion(
            estado=estado,
            detalle=detalle,
            codigo=entrada.codigo,
            precio=precio,
            nro_afiliado=doc,
            nombre_afiliado=afiliado.nombre,
            # Sin nº de autorización: la da la obra social cuando el afiliado la
            # gestiona, no el Colegio.
            nro_autorizacion=None,
            coseguro=CERO,  # OSPM no cobra coseguro (el legacy lo fija en 0)
            traza={
                # `clientes_ospm` es la tabla del legacy: DU, CUIT, AFILIADO,
                # ACTIVO y nada más. No guarda cuándo se importó el padrón, así
                # que la traza deja lo que sí se sabe — con qué fila se resolvió
                # el afiliado y en qué estado figuraba al momento de validar.
                #
                # Acá había un `afiliado.importado_at.isoformat()` sobre un
                # atributo que el modelo nunca tuvo: **toda** carga de OSPM
                # moría con AttributeError → 500. Ver el docstring del módulo.
                "padron": {
                    "documento": doc,
                    "cuit": afiliado.CUIT,
                    "nombre": afiliado.nombre,
                    "activo": afiliado.activo,
                },
            },
        )

    async def _afiliado(self, db: AsyncSession, documento: str) -> ClientesOspm:
        doc = (documento or "").strip()
        if not doc:
            raise HTTPException(422, "Falta el DNI del afiliado.")

        fila = (
            await db.execute(select(ClientesOspm).where(ClientesOspm.DU == doc))
        ).scalar_one_or_none()

        if fila is None:
            total = int((await db.execute(select(func.count(ClientesOspm.ID)))).scalar_one() or 0)
            if total == 0:
                # Distinguirlo importa: con el padrón vacío NADIE valida, y el
                # prestador no tiene forma de saber que el problema no es su DNI.
                raise HTTPException(
                    422,
                    "El padrón de OSPM todavía no fue importado. Avisá al Colegio "
                    "para que cargue el padrón vigente.",
                )
            raise HTTPException(422, f"El DNI {doc} no figura en el padrón de OSPM.")

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
