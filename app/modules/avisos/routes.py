"""ABM de avisos para los socios de la app móvil (web admin).

Todo el módulo exige el scope `avisos:gestionar`. La lectura de los avisos
activos la sirve el BFF móvil (GET /api/mobile/avisos), no acá.

ALCANCE ACTUAL — leer antes de tocar
────────────────────────────────────
Estos endpoints PERSISTEN el aviso y lo publican para el app, que lo ve al
consultar /api/mobile/avisos (pull). Todavía NO despachan una notificación push
que despierte el teléfono: para eso falta (a) una tabla de tokens de dispositivo
por socio, que el app tendría que registrar al iniciar sesión, y (b) credenciales
de un proveedor (FCM o Expo Push) en Settings. Mientras no exista, cada aviso
queda con push_estado='pendiente'. Ver ESTADOS_PUSH en models/avisos_push.py.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.db.models.avisos_push import TIPOS_AVISO, AvisoPush
from app.modules.avisos import push, service
from app.modules.avisos.schemas import AvisoCreate, AvisoOut, AvisoUpdate
from app.modules.dispositivos import service as dispositivos_service

router = APIRouter()  # El scope lo declara app/auth/authz.py::SCOPES_POR_RUTA (fuente unica de autorizacion).

MAX_PAGE_SIZE = 200


def _to_out(obj: AvisoPush, autor_nombre: Optional[str] = None) -> AvisoOut:
    return AvisoOut(
        id=obj.id,
        titulo=obj.titulo,
        mensaje=obj.mensaje,
        tipo=obj.tipo,
        publicado_at=obj.publicado_at,
        activo=obj.activo,
        push_estado=obj.push_estado,
        push_error=obj.push_error,
        destinatarios=obj.destinatarios,
        enviado_por=obj.enviado_por,
        enviado_por_nombre=(autor_nombre or None),
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


@router.get("/tipos", response_model=List[str])
async def listar_tipos():
    """Catálogo fijo de tipos — pobla el select del formulario."""
    return list(TIPOS_AVISO)


@router.get("/", response_model=List[AvisoOut])
async def listar_avisos(
    response: Response,
    tipo: Optional[str] = Query(None, description="Filtrar por tipo (exacto)"),
    activo: Optional[bool] = Query(None, description="Filtrar por estado"),
    q: Optional[str] = Query(None, max_length=120, description="Busca en título y mensaje"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
):
    """Historial paginado, el más reciente primero. El total va en el header
    X-Total-Count (mismo criterio que el resto del panel)."""
    total = await service.contar(db, tipo=tipo, activo=activo, q=q)
    response.headers["X-Total-Count"] = str(total)
    filas = await service.listar(db, tipo=tipo, activo=activo, q=q, skip=skip, limit=limit)
    autores = await service.nombres_por_id(db, {a.enviado_por for a in filas if a.enviado_por})
    return [_to_out(a, autores.get(a.enviado_por or 0)) for a in filas]


@router.get("/{aviso_id}", response_model=AvisoOut)
async def obtener_aviso(
    aviso_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    obj = await service.obtener_o_404(db, aviso_id)
    autores = await service.nombres_por_id(db, {obj.enviado_por} if obj.enviado_por else set())
    return _to_out(obj, autores.get(obj.enviado_por or 0))


@router.post("/", response_model=AvisoOut, status_code=status.HTTP_201_CREATED)
async def crear_aviso(
    payload: AvisoCreate,
    db: AsyncSession = Depends(get_db),
    token_user: dict = Depends(get_current_user),
):
    """Publica un aviso para todos los socios.

    Queda visible en el app en cuanto se crea. El despacho de la push todavía no
    está implementado (ver el docstring del módulo): el aviso nace con
    push_estado='pendiente' y ahí es donde va la llamada al proveedor cuando
    exista.
    """
    autor = await service.autor_id(db, token_user)
    obj = AvisoPush(**payload.model_dump(), enviado_por=autor)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    autores = await service.nombres_por_id(db, {autor} if autor else set())
    return _to_out(obj, autores.get(autor or 0))


@router.patch("/{aviso_id}", response_model=AvisoOut)
async def actualizar_aviso(
    payload: AvisoUpdate,
    aviso_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Corrige el texto o baja el aviso del app (activo=false), sin borrarlo."""
    obj = await service.obtener_o_404(db, aviso_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    autores = await service.nombres_por_id(db, {obj.enviado_por} if obj.enviado_por else set())
    return _to_out(obj, autores.get(obj.enviado_por or 0))


@router.delete("/{aviso_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_aviso(
    aviso_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Borra el registro. Para sacarlo del app conservando el historial, usar
    PATCH con activo=false."""
    obj = await service.obtener_o_404(db, aviso_id)
    await db.delete(obj)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{aviso_id}/enviar", response_model=AvisoOut)
async def enviar_push(
    aviso_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
):
    """Despacha la notificación push de un aviso ya publicado.

    Endpoint SEPARADO del alta a propósito: crear el aviso y notificarlo son dos
    decisiones distintas, y así POST / sigue comportándose igual que siempre.
    Se puede reintentar — sirve para reenviar uno que quedó en 'error'.

    Actualiza push_estado / push_error / destinatarios del aviso y da de baja los
    tokens que Expo reporte como muertos (DeviceNotRegistered).
    """
    obj = await service.obtener_o_404(db, aviso_id)
    if not obj.activo:
        raise HTTPException(
            status_code=409,
            detail="El aviso está dado de baja: reactivalo antes de notificar.",
        )

    tokens = await dispositivos_service.tokens_activos(db)
    if not tokens:
        raise HTTPException(
            status_code=409,
            detail="No hay dispositivos registrados todavía. El aviso ya es visible en la app.",
        )

    resultado = await push.enviar(
        tokens, titulo=obj.titulo, cuerpo=obj.mensaje, aviso_id=obj.id
    )
    if resultado.tokens_muertos:
        await dispositivos_service.desactivar_tokens(db, resultado.tokens_muertos)

    obj.push_estado = resultado.estado
    obj.push_error = resultado.error
    obj.destinatarios = resultado.enviados
    await db.commit()
    await db.refresh(obj)

    autores = await service.nombres_por_id(db, {obj.enviado_por} if obj.enviado_por else set())
    return _to_out(obj, autores.get(obj.enviado_por or 0))
