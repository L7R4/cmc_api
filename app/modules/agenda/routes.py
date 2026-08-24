"""Los tres calendarios del Colegio: feriados, cumpleaños y tareas del mes.

Un solo CRUD para los tres, discriminados por `tipo` (ver
`app/db/models/agenda.py` para por qué es una tabla y no tres), más el endpoint
que hace el trabajo interesante: `GET /mes`, que resuelve las recurrencias a
días concretos.

## Por qué la expansión va acá y no en el front

Porque las reglas —el 31 en un mes de 30, el 29 de febrero en un año no
bisiesto— son fáciles de escribir mal y habría que escribirlas de nuevo en cada
consumidor. Hoy son dos: el panel y, cuando lo pida, la app móvil. El backend ya
sabe la respuesta; devolver "día 14, todos los años" y que cada cliente la
convierta es repartir la misma decisión en varios lugares.

Permisos: `catalogo:leer` y `catalogo:editar`, los del personal administrativo.
No se creó ningún scope nuevo.
"""
import calendar
import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query, Response, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.db.models import AgendaEvento, ListadoMedico, Role, UserRole
from app.modules.agenda.schemas import EventoIn, EventoOut, OcurrenciaOut, ResponsableOut

router = APIRouter()  # El scope lo declara app/auth/authz.py::SCOPES_POR_RUTA (fuente unica de autorizacion).


def _dia_valido(anio: int, mes: int, dia: int) -> datetime.date:
    """El día pedido dentro de ese mes, corrido al último si no existe.

    El 31 en un mes de 30 y el 29 de febrero fuera de año bisiesto se resuelven
    hacia atrás y no salteándose el mes: una tarea "el 31 se cierra el período"
    tiene que aparecer en abril, y el cumpleaños del 29/2 se saluda el 28. Que
    el evento desaparezca del calendario sería peor que mostrarlo un día antes.
    """
    ultimo = calendar.monthrange(anio, mes)[1]
    return datetime.date(anio, mes, min(dia, ultimo))


def _ocurrencias(evento: AgendaEvento, anio: int, mes: int) -> List[datetime.date]:
    """En qué días de ese mes cae este evento. Lista vacía si no cae."""
    if evento.recurrencia == "unica":
        if evento.fecha and evento.fecha.year == anio and evento.fecha.month == mes:
            return [evento.fecha]
        return []

    if evento.recurrencia == "anual":
        if evento.mes == mes and evento.dia:
            return [_dia_valido(anio, mes, evento.dia)]
        return []

    # mensual: cae todos los meses, siempre.
    if evento.dia:
        return [_dia_valido(anio, mes, evento.dia)]
    return []


def _marcar(evento: AgendaEvento, usuario: dict) -> None:
    uid = usuario.get("uid")
    evento.actualizado_por = int(uid) if uid is not None else None


@router.get("/", response_model=List[EventoOut])
async def listar_eventos(
    tipo: Optional[str] = Query(None, pattern="^(feriado|cumpleanos|tarea)$"),
    incluir_inactivos: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """El listado plano de un calendario, para la pantalla de edición.

    Sin expandir: acá se administran las reglas ("el 14 de marzo, todos los
    años"), no sus ocurrencias. La grilla del almanaque usa `/mes`.
    """
    stmt = select(AgendaEvento)
    if tipo:
        stmt = stmt.where(AgendaEvento.tipo == tipo)
    if not incluir_inactivos:
        stmt = stmt.where(AgendaEvento.activo.is_(True))

    # Orden calendario: por mes y día para los recurrentes, por fecha para los
    # únicos. `or_` con coalesce sería más elegante en SQL pero ilegible; con
    # decenas de filas, ordenar en Python es gratis y se entiende.
    filas = (await db.execute(stmt)).scalars().all()
    filas = sorted(
        filas,
        key=lambda e: (
            e.mes or (e.fecha.month if e.fecha else 13),
            e.dia or (e.fecha.day if e.fecha else 32),
            e.titulo,
        ),
    )
    return [EventoOut.model_validate(e) for e in filas]


@router.get("/mes", response_model=List[OcurrenciaOut])
async def eventos_del_mes(
    anio: int = Query(..., ge=1900, le=2200),
    mes: int = Query(..., ge=1, le=12),
    tipo: Optional[str] = Query(None, pattern="^(feriado|cumpleanos|tarea)$"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Qué cae en ese mes, ya resuelto a fechas concretas.

    Es lo que dibuja la grilla. Trae los tres calendarios juntos salvo que se
    filtre por `tipo`, porque la vista útil es la que muestra el feriado, el
    cumpleaños y el vencimiento en la misma casilla del almanaque.

    Se filtra en SQL lo que se puede —los únicos, por rango de fecha; los
    anuales, por mes— y el resto se resuelve en Python. Las mensuales caen
    siempre, así que no hay nada que filtrar en ellas.
    """
    primero = datetime.date(anio, mes, 1)
    ultimo = datetime.date(anio, mes, calendar.monthrange(anio, mes)[1])

    stmt = select(AgendaEvento).where(
        AgendaEvento.activo.is_(True),
        or_(
            AgendaEvento.recurrencia == "mensual",
            AgendaEvento.recurrencia == "anual",
            AgendaEvento.fecha.between(primero, ultimo),
        ),
    )
    if tipo:
        stmt = stmt.where(AgendaEvento.tipo == tipo)

    salida: List[OcurrenciaOut] = []
    for evento in (await db.execute(stmt)).scalars().all():
        for dia in _ocurrencias(evento, anio, mes):
            salida.append(
                OcurrenciaOut(**EventoOut.model_validate(evento).model_dump(), ocurre_el=dia)
            )

    salida.sort(key=lambda o: (o.ocurre_el, o.tipo, o.titulo))
    return salida


@router.get("/responsables", response_model=List[ResponsableOut])
async def listar_responsables(
    q: Optional[str] = Query(None, max_length=80),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """El personal del Colegio, para el selector de responsable de una tarea.

    ## Quién entra

    Los que tienen un rol asignado que **no** es `medico`. Son 21 personas
    (admin, facturador, liquidador) contra 2.420 socios.

    El filtro va por `user_role` y **no** por `listado_medico.INGRESAR`, que a
    primera vista parecía el campo indicado ('D'=doctor / 'E'=empleado /
    'A'=administrador). No sirve: en la base hay 2.124 filas con `INGRESAR` en
    NULL y valores fuera de ese conjunto ('F', 'L', 'U', ''). El rol asignado es
    el dato limpio y además es el que gobierna lo que la persona puede hacer.

    Quien no tiene ningún rol queda afuera, que es correcto: sin rol no opera
    en el sistema.

    ## Por qué el evento guarda el nombre y no el id

    `AgendaEvento.responsable` es texto. Un FK sería más prolijo pero rompe dos
    cosas reales: los responsables ya cargados a mano como texto libre, y las
    tareas cuyo responsable es un área ("Facturación") y no una persona. El
    selector escribe el nombre; el id sólo sirve como key de la lista.

    Va gateado con `medico:leer` —el scope administrativo para ver datos de
    otros— y no con `catalogo:leer`, que el rol `medico` sí tiene: un socio no
    tiene por qué obtener el listado del personal del Colegio.
    """
    stmt = (
        select(ListadoMedico.ID, ListadoMedico.NOMBRE, ListadoMedico.NRO_SOCIO, Role.name)
        .join(UserRole, UserRole.user_id == ListadoMedico.ID)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.name != "medico", ListadoMedico.EXISTE == "S")
    )

    # Se agrupa en Python porque una persona puede tener más de un rol y la
    # consulta devuelve una fila por rol. Son ~21 filas: no hay nada que
    # optimizar con un GROUP_CONCAT que además sería específico de MySQL.
    personas: dict[int, ResponsableOut] = {}
    for user_id, nombre, nro_socio, rol in (await db.execute(stmt)).all():
        if user_id not in personas:
            # Los nombres del padrón vienen con espacios de relleno y alguno
            # está en NULL; sin el fallback esa fila sería un ítem en blanco.
            limpio = (nombre or "").strip() or f"Socio #{nro_socio}"
            personas[user_id] = ResponsableOut(id=user_id, nombre=limpio, roles=[])
        personas[user_id].roles.append(rol)

    salida = sorted(personas.values(), key=lambda p: p.nombre)
    if q:
        aguja = q.strip().lower()
        salida = [p for p in salida if aguja in p.nombre.lower()]
    return salida


@router.post("/", response_model=EventoOut, status_code=status.HTTP_201_CREATED)
async def crear_evento(
    body: EventoIn,
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    evento = AgendaEvento(**body.model_dump())
    _marcar(evento, usuario)
    db.add(evento)
    await db.commit()
    await db.refresh(evento)
    return EventoOut.model_validate(evento)


@router.put("/{evento_id}", response_model=EventoOut)
async def editar_evento(
    body: EventoIn,
    evento_id: int = PathParam(..., ge=1),
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    """Reemplaza el evento entero.

    `PUT` y no `PATCH` porque `EventoIn` ya normalizó la coherencia entre
    recurrencia y fechas: aplicar un cambio parcial encima de lo guardado podría
    dejar, por ejemplo, un evento que pasó de anual a mensual conservando el
    `mes` viejo. Con el reemplazo completo, lo que queda en la base es siempre
    lo que el validador aprobó.
    """
    evento = await db.get(AgendaEvento, evento_id)
    if not evento:
        raise HTTPException(404, "Evento no encontrado")
    for campo, valor in body.model_dump().items():
        setattr(evento, campo, valor)
    _marcar(evento, usuario)
    await db.commit()
    await db.refresh(evento)
    return EventoOut.model_validate(evento)


@router.delete("/{evento_id}", status_code=status.HTTP_204_NO_CONTENT)
async def borrar_evento(
    evento_id: int = PathParam(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
) -> Response:
    """Borrado real.

    La baja lógica ya existe como `activo=false` y es la que hay que usar para
    un feriado que dejó de serlo —sigue explicando por qué el Colegio cerró el
    año pasado—. Esto es para lo otro: la fila cargada mal, que no tiene ninguna
    historia que preservar.
    """
    evento = await db.get(AgendaEvento, evento_id)
    if not evento:
        raise HTTPException(404, "Evento no encontrado")
    await db.delete(evento)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
