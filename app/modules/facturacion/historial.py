"""Historial de cada prestación: la tabla `audit_log_facturacion`.

Cada cambio importante de una fila de `detalle_facturacion` deja un evento: qué pasó,
quién, cuándo, por qué y qué campos cambiaron (antes y después). A diferencia de la
auditoría general (`audit_log_general`, que registra peticiones HTTP en segundo plano y
se purga al año), el evento:

* se escribe en la **misma transacción** que el cambio: o quedan los dos o ninguno;
* es por prestación: un cierre de 800 filas deja 800 eventos (en una sola inserción);
* no se edita, no se borra y no se purga.

Uso: quien cambia la fila arma los eventos con `evento()` y los pasa a `registrar()`
antes de su `commit()`. `registrar()` nunca hace commit.

Quién lo hizo sale de la petición en curso (`app/core/contexto.usuario_ctx`, lo deja
`AuditMiddleware`): rol `medico` → médico, cualquier otro rol → Colegio, sin petición
(cron, scripts) → sistema. Quien sabe que el actor es otro (la obra social, una
importación) lo pasa explícito.
"""
import datetime
from decimal import Decimal
from typing import Any, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.contexto import usuario_ctx
from app.core.logging import request_id_ctx
from app.db.models import AuditLogFacturacion, DetalleFacturacionCMC

# ── Tipos de evento ──────────────────────────────────────────────────────────
CARGADA = "cargada"
IMPORTADA = "importada"
EDITADA = "editada"
AUDITADA = "auditada"
DESAUDITADA = "desauditada"
MOVIDA_PERIODO = "movida_periodo"
ELIMINADA = "eliminada"
RECHAZADA_OS = "rechazada_os"
PERIODO_CERRADO = "periodo_cerrado"
LIQUIDADA = "liquidada"
LIQUIDACION_REVERTIDA = "liquidacion_revertida"
RECOTIZADA = "recotizada"
DOCUMENTO_AGREGADO = "documento_agregado"
DOCUMENTO_REEMPLAZADO = "documento_reemplazado"
DOCUMENTO_QUITADO = "documento_quitado"

# ── Quién ────────────────────────────────────────────────────────────────────
ACTOR_COLEGIO = "colegio"
ACTOR_MEDICO = "medico"
ACTOR_SISTEMA = "sistema"
ACTOR_OS = "os"
ACTOR_IMPORTACION = "importacion"

# ── Por qué se elimina ───────────────────────────────────────────────────────
# Los que elige el personal del Colegio al eliminar desde facturación. El médico no
# carga motivo.
MOTIVOS_ELIMINACION = ("duplicado", "error_carga", "sin_documentacion", "sin_autorizacion", "otro")
MOTIVO_OTRO = "otro"
# Interno: la carga reemplaza o quita integrantes al editar un equipo (sin diálogo).
MOTIVO_EQUIPO_EDITADO = "equipo_editado"
MOTIVOS_ELIMINACION_API = (*MOTIVOS_ELIMINACION, MOTIVO_EQUIPO_EDITADO)

# Campos de negocio que se siguen en `cambios`. `calculo_snapshot` y
# `validacion_respuesta` quedan afuera a propósito: son JSON grandes.
CAMPOS_SEGUIDOS = (
    "cod_med", "cod_clinica", "cod_nom", "nomenclador_id", "fecha_practica",
    "dni_p", "nom_ape_p", "autorizacion", "nro_orden", "cantidad", "sesion", "porc",
    "via", "tipo", "manual", "honorarios", "gastos", "ayudante", "coseguro",
    "importe_total", "cod_obr", "periodo", "version", "grupo_equipo_id", "sin_valorizar",
)


def validar_motivo_eliminacion(codigo: Optional[str], texto: Optional[str]) -> tuple[str, Optional[str]]:
    """El motivo que manda el Colegio al eliminar: código obligatorio, texto obligatorio
    sólo con «otro». Devuelve los dos ya limpios."""
    texto = (texto or "").strip()[:255] or None
    if codigo not in MOTIVOS_ELIMINACION_API:
        raise HTTPException(422, "Elegí el motivo por el que se elimina la prestación.")
    if codigo == MOTIVO_OTRO and not texto:
        raise HTTPException(422, "Con el motivo «Otro» hay que escribir el detalle.")
    return codigo, texto


# Códigos que la tabla guarda como texto pero que a veces llegan como número (la columna
# física de `cod_med` es numérica; el servicio la escribe como texto): se comparan como texto.
_COMO_TEXTO = {"cod_med", "cod_nom", "cod_obr", "dni_p", "autorizacion", "nro_orden", "periodo"}


def _valor(v: Any, campo: Optional[str] = None) -> Any:
    """Lo que va al JSON: montos como texto con dos decimales (así 60 y 60.00 no
    cuentan como cambio), fechas en ISO, el resto tal cual."""
    if campo in _COMO_TEXTO and v is not None:
        return str(v)
    if isinstance(v, (Decimal, float)):
        return f"{Decimal(str(v)):.2f}"
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def foto(row: DetalleFacturacionCMC) -> dict:
    """Los campos seguidos de la fila, listos para comparar con `diferencias()`."""
    return {c: _valor(getattr(row, c), c) for c in CAMPOS_SEGUIDOS}


def diferencias(antes: dict, despues: dict) -> Optional[dict]:
    cambios = {c: [antes.get(c), despues.get(c)] for c in despues if antes.get(c) != despues.get(c)}
    return cambios or None


def contexto_de(row: DetalleFacturacionCMC, **extra) -> dict:
    ctx = {"cod_obr": _valor(row.cod_obr, "cod_obr"), "periodo": row.periodo, "version": row.version}
    if row.grupo_equipo_id is not None:
        ctx["grupo_equipo_id"] = row.grupo_equipo_id
    ctx.update({k: _valor(v) for k, v in extra.items() if v is not None})
    return ctx


def _actor(actor_tipo: Optional[str]) -> tuple[str, Optional[int]]:
    nro_socio, rol = usuario_ctx.get()
    if actor_tipo is None:
        if rol is None:
            actor_tipo = ACTOR_SISTEMA
        else:
            actor_tipo = ACTOR_MEDICO if rol == "medico" else ACTOR_COLEGIO
    return actor_tipo, nro_socio


def evento(
    row: DetalleFacturacionCMC,
    tipo: str,
    *,
    estado_antes: Optional[str] = None,
    estado_despues: Optional[str] = None,
    actor_tipo: Optional[str] = None,
    motivo_codigo: Optional[str] = None,
    motivo_texto: Optional[str] = None,
    cambios: Optional[dict] = None,
    contexto: Optional[dict] = None,
) -> dict:
    """Un evento de `row`, sin escribir. `contexto` reemplaza al que se arma de la fila."""
    actor, socio = _actor(actor_tipo)
    request_id = request_id_ctx.get()
    return {
        "prestacion_id": row.id_detalle_prestaciones,
        "tipo": tipo,
        "estado_antes": estado_antes,
        "estado_despues": estado_despues,
        "actor_tipo": actor,
        "actor_socio": socio,
        "motivo_codigo": motivo_codigo,
        "motivo_texto": (motivo_texto or "")[:255] or None,
        "cambios": cambios,
        "contexto": contexto if contexto is not None else contexto_de(row),
        "request_id": None if request_id == "-" else request_id,
    }


def eventos_importacion(filas: Sequence[DetalleFacturacionCMC], importador: str) -> list[dict]:
    """El alta de las filas que trajo una importación (ya con id). Las que nacen en X
    (fuera de factura) llevan como motivo lo que dijo el archivo."""
    return [
        evento(
            f, IMPORTADA, estado_despues=f.estado, actor_tipo=ACTOR_IMPORTACION,
            motivo_texto=f.validacion_detalle if f.estado == "X" else None,
            contexto=contexto_de(f, importador=importador, importe_total=f.importe_total),
        )
        for f in filas
    ]


async def registrar(db: AsyncSession, eventos: Sequence[dict]) -> None:
    """Inserta los eventos en una sola sentencia. Sin commit: va en la transacción de
    quien llama, que es la del cambio."""
    if eventos:
        await db.execute(insert(AuditLogFacturacion), list(eventos))
