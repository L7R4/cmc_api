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

# OSPM no emite un número de autorización propio (no hay servicio en línea del
# lado de la obra social, ver el docstring de `validar()`); el Colegio arma uno
# local para que la prestación autorizada tenga con qué identificarse en pantalla
# y en las búsquedas ("Autorización" / `orden_o_autorizacion`), igual que
# Sancor/Nobis/OSPJN. Sin prefijo ni letras a pedido: 8 dígitos, nada más — el
# arranque en `NUMERO_INICIAL` (en vez de 1) es lo que lo distingue de un
# correlativo interno cualquiera.
DIGITOS_VALIDACION = 8
NUMERO_INICIAL = 24534444


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

        | Afiliado | Resultado | Nº de validación |
        |---|---|---|
        | activo | `autorizada` — factura | se genera uno local (`_generar_nro_validacion`) |
        | existe, inactivo | `rechazada` — "Rechazado. Afiliado suspendido" | ninguno |
        | no está en el padrón | `rechazada` — "Rechazado. Afiliado inexistente" | ninguno |

        Mismo criterio que el resto de las obras sociales (ver Sancor): sólo lo
        que factura de verdad se queda con un número — un rechazo no autorizó
        nada, así que no hay nada que identificar.

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
            nro_validacion = None
            traza_padron = {"documento": doc, "encontrado": False}
        elif not afiliado.activo:
            estado, detalle = "rechazada", "Rechazado. Afiliado suspendido"
            nombre_afiliado = afiliado.nombre
            nro_validacion = None
            traza_padron = {
                "documento": doc, "cuit": afiliado.CUIT,
                "nombre": afiliado.nombre, "activo": afiliado.activo,
            }
        else:
            estado, detalle = "autorizada", "Autorizado. Afiliado activo en el padrón de OSPM."
            nombre_afiliado = afiliado.nombre
            nro_validacion = await self._generar_nro_validacion(ctx.db)
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
            nro_autorizacion=nro_validacion,
            coseguro=CERO,  # OSPM no cobra coseguro (el legacy lo fija en 0)
            traza={"padron": traza_padron},
        )

    async def _generar_nro_validacion(self, db: AsyncSession) -> str:
        """Siguiente número local: 8 dígitos, sin letras (ej. "24534445").

        Secuencial simple (MAX + 1) sobre lo ya emitido, arrancando en
        `NUMERO_INICIAL` la primera vez — mismo criterio, y mismo riesgo de
        colisión aceptado por baja concurrencia (app interna, sin precedente de
        `SELECT...FOR UPDATE`), que `crear_clinica` en `facturacion/service.py`.

        El MAX sólo mira valores que sean EXACTAMENTE `DIGITOS_VALIDACION`
        dígitos (regex, no un simple `LIKE`): sin letras no hay forma de
        reconocer "esto lo generó este método" por prefijo, así que hay que
        blindarse de cualquier otra cosa que pudiera haber en `autorizacion`
        para `cod_obr=433` — un valor con más o menos dígitos, o con texto,
        queda afuera del cálculo en vez de romper el `int()` o descarrilar la
        numeración.
        """
        M = DetalleFacturacionCMC
        patron_exacto = f"^[0-9]{{{DIGITOS_VALIDACION}}}$"
        ultimo = (await db.execute(
            select(func.max(M.autorizacion)).where(
                M.cod_obr == str(self.nro), M.autorizacion.regexp_match(patron_exacto),
            )
        )).scalar_one()
        siguiente = int(ultimo) + 1 if ultimo else NUMERO_INICIAL
        return f"{siguiente:0{DIGITOS_VALIDACION}d}"

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
