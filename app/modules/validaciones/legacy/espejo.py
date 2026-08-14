"""Réplica de las prestaciones del panel nuevo en la tabla del sistema viejo.

## Qué hace y por qué

Mientras el legacy siga facturando desde `guardar_atencion`, una prestación
cargada por la API nueva tiene que aparecer también allá o se pierde en la
facturación del mes. Esto es un puente **temporal**: el día que el legacy se
apague se borra la carpeta `legacy/` y no queda nada que limpiar en el sistema
nuevo (ver el `__init__.py` del paquete).

Sólo se replica lo que entra por esta API. Lo que se carga en el sistema viejo
sigue su camino de siempre y no pasa por acá.

## Dos reglas que no se negocian

**1. Falla abierto.** El dueño del dato es `detalle_facturacion`. Si el espejo
no puede escribir —la tabla cambió, el código no está en `codigo_descripcion`,
la obra social no tiene perfil— la prestación del médico **igual queda
cargada**. Se registra en el log y sigue. Al revés sería regalarle al sistema
viejo un veto sobre el nuevo.

**2. Corre después del commit del sistema nuevo.** Nunca comparte transacción
con `grabar_prestacion`: si compartieran, un error del espejo arrastraría la
prestación real en el rollback, que es justo lo que la regla 1 quiere evitar.

## Cómo se vinculan las dos filas

El ID de `guardar_atencion` se guarda en `detalle_facturacion.validacion_respuesta`,
bajo la clave `"legacy"`. Es la columna JSON de traza que ya usa el módulo para
anotar el resultado crudo de cada obra social —`ValidadorOS.anular()` escribe ahí
mismo la clave `"anulacion"`—, así que no hace falta ninguna migración: el
puente entero se instala y se desinstala sin tocar el esquema. Cuando el espejo
se apague, la clave queda como dato histórico inerte.
"""
import datetime
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CodigoDescripcion, DetalleFacturacionCMC, GuardarAtencion, ListadoMedico
from app.modules.validaciones.legacy import mapeo
from app.modules.validaciones.legacy.perfiles import perfil_de

log = logging.getLogger(__name__)

# Clave bajo la que se anota el vínculo en `validacion_respuesta`.
CLAVE_TRAZA = "legacy"


async def replicar_alta(
    db: AsyncSession, detalle: DetalleFacturacionCMC, medico: ListadoMedico
) -> Optional[int]:
    """Copia la prestación a `guardar_atencion`. Devuelve el ID creado, o `None`.

    `None` no es un error del que haya que enterarse en el endpoint: significa
    "esta prestación no se espejó", y el motivo queda en el log.
    """
    perfil = perfil_de(int(detalle.cod_obr))
    if perfil is None:
        log.warning(
            "Espejo legacy: la obra social %s no tiene perfil en legacy/perfiles.py; "
            "la prestación %s queda sólo en detalle_facturacion.",
            detalle.cod_obr,
            detalle.id_detalle_prestaciones,
        )
        return None

    try:
        con_hono_sana = await _tipo_de_codigo(db, detalle.cod_nom or "")
        fila = mapeo.construir_fila(
            detalle=detalle,
            medico=medico,
            perfil=perfil,
            con_hono_sana=con_hono_sana,
            hoy=datetime.date.today(),
        )
        db.add(fila)
        await db.flush()
        _anotar_vinculo(detalle, fila.ID)
        await db.commit()
        # El log va DENTRO del try a propósito: quedó afuera una vez y un
        # atributo mal escrito ahí escapó del fail-open, después de haber
        # commiteado la fila espejo. Nada de esta función puede propagar.
        log.info(
            "Espejo legacy: prestación %s replicada en guardar_atencion %s (O.S. %s).",
            detalle.id_detalle_prestaciones,
            fila.ID,
            detalle.cod_obr,
        )
        return fila.ID
    except Exception:
        # El detalle ya está commiteado: este rollback sólo descarta lo que el
        # espejo alcanzó a poner en la sesión, y la deja usable para el caller.
        await db.rollback()
        log.exception(
            "Espejo legacy: falló la réplica de la prestación %s (O.S. %s). "
            "La prestación quedó cargada en el sistema nuevo.",
            detalle.id_detalle_prestaciones,
            detalle.cod_obr,
        )
        return None


async def replicar_baja(db: AsyncSession, detalle: DetalleFacturacionCMC) -> bool:
    """Marca `EXISTE='N'` en la fila espejo. Devuelve si se llegó a marcar.

    Es la misma baja lógica que hace el legacy (`borra_atencion*.php`:
    `UPDATE guardar_atencion SET EXISTE='N' WHERE ID=?`) y la que el resto de
    sus consultas espera, porque casi todas filtran por `EXISTE='S'`.
    """
    legacy_id = _id_vinculado(detalle)
    if legacy_id is None:
        # Normal para prestaciones cargadas antes de que existiera el espejo.
        log.info(
            "Espejo legacy: la prestación %s no tiene fila espejo; nada que dar de baja.",
            detalle.id_detalle_prestaciones,
        )
        return False

    try:
        fila = await db.get(GuardarAtencion, legacy_id)
        if fila is None:
            log.warning(
                "Espejo legacy: guardar_atencion %s (de la prestación %s) ya no existe.",
                legacy_id,
                detalle.id_detalle_prestaciones,
            )
            return False
        fila.EXISTE = "N"
        await db.commit()
    except Exception:
        await db.rollback()
        log.exception(
            "Espejo legacy: falló la baja de guardar_atencion %s (prestación %s). "
            "La prestación quedó anulada en el sistema nuevo; hay que darla de "
            "baja a mano en el sistema viejo.",
            legacy_id,
            detalle.id_detalle_prestaciones,
        )
        return False

    return True


async def _tipo_de_codigo(db: AsyncSession, codigo: str) -> str:
    """`C_P_H_S` del código: consulta / práctica / honorario / sanatorio.

    El legacy agrupa la facturación por esta letra, así que sale de la misma
    tabla de la que la lee él (`codigo_descripcion`) y no de una equivalencia
    del nomenclador nuevo — que para el mismo código puede no coincidir.
    """
    if not codigo:
        return mapeo.TIPO_CODIGO_DEFECTO
    valor = await db.scalar(
        select(CodigoDescripcion.C_P_H_S).where(CodigoDescripcion.CODIGO == codigo).limit(1)
    )
    return valor or mapeo.TIPO_CODIGO_DEFECTO


def _anotar_vinculo(detalle: DetalleFacturacionCMC, legacy_id: int) -> None:
    """Guarda el ID de la fila espejo en la traza de la prestación.

    Se copia y se reasigna el dict entero: mutarlo en el lugar no marca sucia la
    columna JSON y el cambio no se persiste, en silencio. Mismo cuidado que
    toman los `ValidadorOS.anular()` con la clave `"anulacion"`.
    """
    traza = dict(detalle.validacion_respuesta or {})
    traza[CLAVE_TRAZA] = {"guardar_atencion_id": legacy_id}
    detalle.validacion_respuesta = traza


def _id_vinculado(detalle: DetalleFacturacionCMC) -> Optional[int]:
    bloque = (detalle.validacion_respuesta or {}).get(CLAVE_TRAZA) or {}
    legacy_id = bloque.get("guardar_atencion_id")
    return legacy_id if isinstance(legacy_id, int) else None
