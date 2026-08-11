"""Lógica de las solicitudes de cambio de datos.

Las funciones de lectura no mutan; `resolver()` deja el commit al caller
(mismo criterio que modules/nomenclador/service.py).
"""
from __future__ import annotations

import datetime
from typing import Dict, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.medico import ListadoMedico
from app.db.models.solicitud_cambio import (
    ESTADOS_SOLICITUD_CAMBIO,
    SolicitudCambioMedico,
)

# Tope de solicitudes pendientes por médico. Evita que un cliente comprometido
# (o un doble-tap en el app) inunde la bandeja del admin.
MAX_PENDIENTES_POR_MEDICO = 10

# ── Qué puede pedir cambiar el médico ─────────────────────────────────────────
#
# LISTA BLANCA, y corta a propósito: son sus datos de contacto y poco más. Todo
# lo que define su situación en el Colegio —matrícula, categoría, fechas de
# ingreso, ANSSAL, malapraxis, `existe`— NO está y no debe estarlo: eso lo
# determina el Colegio, no el socio. Un campo que no esté acá se ignora tanto al
# crear la solicitud como al aplicarla, así que agregar uno es una decisión
# explícita y no un descuido.
#
# clave de API → columna de `listado_medico` (mismo criterio que FIELD_MAP).
CAMPOS_EDITABLES_POR_MEDICO: dict[str, str] = {
    "domicilio_particular": "DOMICILIO_PARTICULAR",
    "tele_particular": "TELE_PARTICULAR",
    "celular_particular": "CELULAR_PARTICULAR",
    "mail_particular": "MAIL_PARTICULAR",
    "domicilio_consulta": "DOMICILIO_CONSULTA",
    "telefono_consulta": "TELEFONO_CONSULTA",
    "localidad": "localidad",
    "provincia": "PROVINCIA",
    "codigo_postal": "CODIGO_POSTAL",
    "cbu": "cbu",
}

# NUNCA editables por el médico, ni por formulario ni por reclamo suelto.
#
# Están escritos aparte —y no sólo ausentes de la lista blanca— porque son los
# que más "naturalmente" alguien agregaría sin pensarlo: el nombre de registro
# identifica al profesional en todos los padrones y en la facturación, y el
# número de socio es SU IDENTIFICADOR, la clave con la que se le liquida. Si el
# socio pudiera cambiarlos, aunque sea pidiendo aprobación, un error de tipeo
# aprobado por distracción rompe el vínculo con su historial.
#
# Sólo el Colegio los toca, desde el ABM del legajo.
CAMPOS_NUNCA_EDITABLES: frozenset[str] = frozenset(
    {
        "name",        # Nombre (registro)
        "nombre_",
        "apellido",
        "nro_socio",   # N° de socio
        "matricula_prov",
        "matricula_nac",
        "categoria",
        "existe",
        "fecha_ingreso",
        "anssal",
    }
)

# Invariante: lo prohibido no puede colarse en la lista blanca. Si alguien
# agrega uno de esos campos arriba, el módulo no arranca y se ve en el acto,
# en vez de descubrirse cuando ya se aprobó un cambio de número de socio.
assert not (
    CAMPOS_NUNCA_EDITABLES & set(CAMPOS_EDITABLES_POR_MEDICO)
), "Hay campos prohibidos en la lista blanca de cambios del médico"

# Etiquetas para la bandeja del admin y para el formulario del médico.
ETIQUETAS_CAMPOS: dict[str, str] = {
    "domicilio_particular": "Domicilio particular",
    "tele_particular": "Teléfono particular",
    "celular_particular": "Celular particular",
    "mail_particular": "E-mail particular",
    "domicilio_consulta": "Domicilio de consulta",
    "telefono_consulta": "Teléfono de consulta",
    "localidad": "Localidad",
    "provincia": "Provincia",
    "codigo_postal": "Código postal",
    "cbu": "CBU",
}

# Largo máximo por valor. Coincide con `valor_propuesto` para que un cambio
# quepa igual en las columnas viejas.
MAX_LARGO_VALOR = 255


async def contar_por_estado(db: AsyncSession) -> Dict[str, int]:
    """Un solo GROUP BY para los cuatro contadores de los badges."""
    rows = (
        await db.execute(
            select(SolicitudCambioMedico.estado, func.count(SolicitudCambioMedico.id))
            .group_by(SolicitudCambioMedico.estado)
        )
    ).all()
    counts = {estado: 0 for estado in ESTADOS_SOLICITUD_CAMBIO}
    for estado, cantidad in rows:
        counts[str(estado)] = int(cantidad)
    counts["total"] = sum(counts[e] for e in ESTADOS_SOLICITUD_CAMBIO)
    return counts


async def listar(
    db: AsyncSession,
    estado: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[Sequence[tuple[SolicitudCambioMedico, Optional[str]]], int]:
    """(filas, total). Cada fila es (solicitud, nombre del médico).

    El nombre sale de un LEFT JOIN por ListadoMedico.ID (PK, no duplica filas);
    si el médico fue borrado, medico_id queda NULL y el nombre viene vacío —
    nro_socio se conserva igual en la solicitud.
    """
    stmt = (
        select(SolicitudCambioMedico, ListadoMedico.NOMBRE)
        .outerjoin(ListadoMedico, ListadoMedico.ID == SolicitudCambioMedico.medico_id)
    )
    count_stmt = select(func.count(SolicitudCambioMedico.id))
    if estado:
        stmt = stmt.where(SolicitudCambioMedico.estado == estado)
        count_stmt = count_stmt.where(SolicitudCambioMedico.estado == estado)

    stmt = (
        stmt.order_by(SolicitudCambioMedico.created_at.desc(), SolicitudCambioMedico.id.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    total = int((await db.execute(count_stmt)).scalar_one() or 0)
    return [(r[0], r[1]) for r in rows], total


async def obtener_o_404(
    db: AsyncSession, solicitud_id: int
) -> tuple[SolicitudCambioMedico, Optional[str]]:
    row = (
        await db.execute(
            select(SolicitudCambioMedico, ListadoMedico.NOMBRE)
            .outerjoin(ListadoMedico, ListadoMedico.ID == SolicitudCambioMedico.medico_id)
            .where(SolicitudCambioMedico.id == solicitud_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return row[0], row[1]


async def nombres_por_id(db: AsyncSession, ids: set[int]) -> Dict[int, str]:
    """Resuelve varios ListadoMedico.ID → NOMBRE en una query (para revisado_por)."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(ListadoMedico.ID, ListadoMedico.NOMBRE).where(ListadoMedico.ID.in_(ids))
        )
    ).all()
    return {int(i): (n or "") for i, n in rows}


async def resolver(
    db: AsyncSession,
    solicitud_id: int,
    nuevo_estado: str,
    revisado_por: Optional[int],
    respuesta_admin: Optional[str],
) -> SolicitudCambioMedico:
    """Aprueba o rechaza. Sólo se puede resolver una vez.

    Al APROBAR una solicitud de formulario completo (la que trae `cambios`), los
    valores se escriben en `listado_medico` en la misma transacción: es lo que
    hace que aprobar signifique algo y que el admin no tenga que retipear.

    Las solicitudes viejas —las de un solo campo que manda la app móvil— siguen
    sin tocar el legajo: ahí `valor_propuesto` es texto libre que el socio
    escribió y no está atado a ninguna columna, así que aplicarlo a ciegas sería
    peor que dejarlo a criterio del admin.
    """
    if nuevo_estado not in ("aprobada", "rechazada"):
        raise HTTPException(status_code=422, detail="Estado destino inválido")

    obj = await db.get(SolicitudCambioMedico, solicitud_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    if obj.estado != "pendiente":
        raise HTTPException(
            status_code=409, detail=f"La solicitud ya fue resuelta (estado={obj.estado})"
        )

    obj.estado = nuevo_estado
    obj.revisado_por = revisado_por
    obj.revisado_at = datetime.datetime.now()
    obj.respuesta_admin = respuesta_admin

    if nuevo_estado == "aprobada" and obj.cambios:
        aplicados = await _aplicar_cambios(db, obj)
        if aplicados:
            obj.aplicado_at = datetime.datetime.now()

    return obj


async def _aplicar_cambios(
    db: AsyncSession, obj: SolicitudCambioMedico
) -> list[str]:
    """Escribe en `listado_medico` los cambios aprobados. Devuelve qué se aplicó.

    Se vuelve a filtrar por la lista blanca aunque ya se haya filtrado al crear:
    entre el alta y la aprobación puede haber pasado cualquier cosa (una
    migración, un cambio de la lista), y esta es la escritura real.
    """
    if not obj.medico_id:
        # El médico fue borrado: queda la solicitud como histórico, sin aplicar.
        return []

    medico = await db.get(ListadoMedico, obj.medico_id)
    if medico is None:
        return []

    aplicados: list[str] = []
    for campo, valores in (obj.cambios or {}).items():
        columna = CAMPOS_EDITABLES_POR_MEDICO.get(campo)
        if not columna or not hasattr(medico, columna):
            continue
        propuesto = (valores or {}).get("propuesto")
        propuesto = "" if propuesto is None else str(propuesto).strip()
        setattr(medico, columna, propuesto[:MAX_LARGO_VALOR])
        aplicados.append(campo)

    if aplicados:
        # Se deja constancia de qué se escribió, para poder auditarlo después.
        traza = dict(obj.cambios or {})
        for campo in aplicados:
            traza[campo] = {**(traza.get(campo) or {}), "aplicado": True}
        obj.cambios = traza
        await db.flush()

    return aplicados


async def crear_desde_formulario(
    db: AsyncSession,
    *,
    nro_socio: int,
    medico_id: Optional[int],
    propuestos: dict[str, Optional[str]],
    mensaje: str,
) -> SolicitudCambioMedico:
    """Alta desde el formulario completo del portal del socio.

    El médico manda TODOS sus datos editables; acá se compara contra lo que hay
    hoy en `listado_medico` y **sólo se guardan los que efectivamente cambian**.
    Así la bandeja del admin muestra el diff y no un volcado del legajo entero.

    Puntos importantes:

    * `valor_actual` se lee de la BASE, no del payload. Si lo mandara el cliente,
      un formulario desactualizado (o manipulado) haría que el admin apruebe
      comparando contra algo que ya no es cierto.
    * Los campos fuera de `CAMPOS_EDITABLES_POR_MEDICO` se descartan en silencio:
      no son negociables y no tiene sentido explicarle al cliente cuáles son.
    * Sin cambios reales no se crea nada: es un 422, no una solicitud vacía.
    """
    await _gate_pendientes(db, nro_socio)

    medico = await db.get(ListadoMedico, medico_id) if medico_id else None
    if medico is None:
        raise HTTPException(404, "No se encontró tu legajo.")

    cambios: dict[str, dict] = {}
    for campo, propuesto in (propuestos or {}).items():
        columna = CAMPOS_EDITABLES_POR_MEDICO.get(campo)
        if not columna or not hasattr(medico, columna):
            continue

        actual = getattr(medico, columna)
        actual_txt = "" if actual is None else str(actual).strip()
        nuevo_txt = "" if propuesto is None else str(propuesto).strip()

        if nuevo_txt == actual_txt:
            continue  # no cambió: no ensucia la solicitud
        if len(nuevo_txt) > MAX_LARGO_VALOR:
            raise HTTPException(
                422,
                f"El valor de «{ETIQUETAS_CAMPOS.get(campo, campo)}» es demasiado largo.",
            )

        cambios[campo] = {"actual": actual_txt, "propuesto": nuevo_txt}

    if not cambios:
        raise HTTPException(422, "No modificaste ningún dato.")

    # Las columnas viejas se llenan con el primer cambio para que la bandeja y
    # los listados existentes sigan mostrando algo con sentido.
    primero = next(iter(cambios))

    obj = SolicitudCambioMedico(
        nro_socio=nro_socio,
        medico_id=medico_id,
        campo=primero[:40],
        valor_actual=cambios[primero]["actual"][:MAX_LARGO_VALOR] or None,
        valor_propuesto=cambios[primero]["propuesto"][:MAX_LARGO_VALOR] or None,
        cambios=cambios,
        mensaje=mensaje.strip() or "Corrección de datos desde el portal.",
        estado="pendiente",
    )
    db.add(obj)
    return obj


async def _gate_pendientes(db: AsyncSession, nro_socio: int) -> None:
    """Tope de pendientes por médico, compartido por los dos altas."""
    pendientes = int(
        (
            await db.execute(
                select(func.count(SolicitudCambioMedico.id)).where(
                    SolicitudCambioMedico.nro_socio == nro_socio,
                    SolicitudCambioMedico.estado == "pendiente",
                )
            )
        ).scalar_one()
        or 0
    )
    if pendientes >= MAX_PENDIENTES_POR_MEDICO:
        raise HTTPException(
            status_code=429,
            detail="Ya tenés varias solicitudes pendientes. Esperá a que las revisen.",
        )


async def crear_desde_movil(
    db: AsyncSession,
    nro_socio: int,
    medico_id: Optional[int],
    campo: str,
    valor_actual: Optional[str],
    valor_propuesto: Optional[str],
    mensaje: str,
) -> SolicitudCambioMedico:
    """Alta desde la app. nro_socio/medico_id vienen del token, nunca del body."""
    pendientes = int(
        (
            await db.execute(
                select(func.count(SolicitudCambioMedico.id)).where(
                    SolicitudCambioMedico.nro_socio == nro_socio,
                    SolicitudCambioMedico.estado == "pendiente",
                )
            )
        ).scalar_one()
        or 0
    )
    if pendientes >= MAX_PENDIENTES_POR_MEDICO:
        raise HTTPException(
            status_code=429,
            detail="Ya tenés varias solicitudes pendientes. Esperá a que las revisen.",
        )

    obj = SolicitudCambioMedico(
        nro_socio=nro_socio,
        medico_id=medico_id,
        campo=campo,
        valor_actual=valor_actual,
        valor_propuesto=valor_propuesto,
        mensaje=mensaje,
        estado="pendiente",
    )
    db.add(obj)
    return obj
