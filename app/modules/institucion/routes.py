"""Datos del Colegio: CUIT, CBU, domicilio, teléfonos y casillas de correo.

## Permisos

`catalogo:leer` / `catalogo:editar` para los datos y contactos — el permiso del
personal administrativo, que es quien los mantiene.

Para las **contraseñas** hay además una lista nominal: sólo los NRO_SOCIO de
`INSTITUCION_CLAVES_SOCIOS` (hoy ANA 29920 y GRACIELA 30140). Tener
`rbac:gestionar` no alcanza. Ese chequeo vive en el handler (`_exigir_claves`) y
no en la matriz porque con `RBAC_ENFORCE=False` la matriz loguea pero deja pasar.

## La contraseña

Se cifra, no se hashea; el porqué está en `app/core/secretos.py`. Leerla es un
`POST` y no un `GET` porque `app/middleware/audit.py` sólo registra métodos
mutantes: con un `GET` las lecturas no dejarían rastro.
"""
import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.deps import get_current_user
from app.core.config import settings
from app.core.secretos import SecretoNoDisponible, cifrado_disponible, cifrar, descifrar
from app.db.database import get_db
from app.db.models import Institucion, InstitucionEmail, InstitucionTelefono
from app.modules.institucion.schemas import (
    EmailIn, EmailOut, InstitucionIn, InstitucionOut, PasswordIn, PasswordRevelada,
    TelefonoIn, TelefonoOut,
)

router = APIRouter()  # El scope lo declara app/auth/authz.py::SCOPES_POR_RUTA (fuente unica de autorizacion).


def _socios_habilitados() -> frozenset[int]:
    """Los NRO_SOCIO que pueden ver contraseñas, según la configuración.

    Se lee en cada llamada y no una sola vez al importar: así cambiar la
    variable y reiniciar alcanza, sin depender de en qué orden se importó qué.
    Son dos elementos; el costo es irrelevante.
    """
    crudo = settings.INSTITUCION_CLAVES_SOCIOS or ""
    return frozenset(
        int(p) for p in (t.strip() for t in crudo.split(",")) if p.isdigit()
    )


def _puede_ver_claves(usuario: dict) -> bool:
    """Si este usuario está en la lista nominal de acceso a las contraseñas."""
    crudo = usuario.get("nro_socio")
    try:
        return int(crudo) in _socios_habilitados()
    except (TypeError, ValueError):
        # Sin `nro_socio` legible no se puede afirmar que esté en la lista, y
        # ante la duda no se muestra una credencial.
        return False


def _exigir_claves(usuario: dict) -> None:
    """Corta con 403 a quien no esté en la lista.

    Este chequeo va en el handler y **no** sólo en `SCOPES_POR_RUTA`, y la razón
    importa: hoy `RBAC_ENFORCE` está en `False`, así que la matriz loguea pero
    deja pasar. Un control que sólo viviera ahí no estaría aplicándose. Acá sí.
    """
    if not _puede_ver_claves(usuario):
        raise HTTPException(
            403,
            "Sólo el personal autorizado puede ver o cambiar las contraseñas "
            "de las casillas del Colegio.",
        )


async def _cargar(db: AsyncSession) -> Optional[Institucion]:
    """La fila con sus hijos ya cargados, o `None` si todavía no existe.

    El `selectinload` no es una optimización: sin él, serializar la respuesta
    dispararía un lazy-load y en SQLAlchemy async eso es un `MissingGreenlet`,
    no una query extra.
    """
    stmt = (
        select(Institucion)
        .options(selectinload(Institucion.telefonos), selectinload(Institucion.emails))
        .order_by(Institucion.id)
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def _obtener(db: AsyncSession) -> Institucion:
    """La fila única, creándola vacía si es la primera vez.

    El alta implícita evita un estado que no le sirve a nadie: una pantalla que
    no se puede abrir hasta que alguien corra un INSERT a mano. Como es un
    singleton, se toma siempre la de menor `id` en vez de asumir `id=1`: si un
    import descuidado dejara dos filas, la pantalla seguiría mostrando siempre la
    misma en vez de alternar según el orden que devuelva MySQL.
    """
    fila = await _cargar(db)
    if fila:
        return fila

    db.add(Institucion())
    await db.commit()
    # Se relee en vez de refrescar el objeto recién agregado: `actualizado_en`
    # lo pone MySQL con un `server_default`, así que después del commit el
    # atributo está expirado y leerlo sería otro lazy-load.
    fila = await _cargar(db)
    assert fila is not None  # se acaba de insertar en esta misma sesión
    return fila


def _email_out(fila: InstitucionEmail) -> EmailOut:
    """Serializa una casilla **sin** la contraseña.

    Se arma a mano en vez de con `from_attributes` sobre el modelo entero para
    que `password_cifrada` no pueda colarse: si mañana alguien agrega el campo al
    schema de salida por error, este constructor explícito no se lo pasa.
    """
    return EmailOut(
        id=fila.id,
        etiqueta=fila.etiqueta,
        direccion=fila.direccion,
        servidor_entrante=fila.servidor_entrante,
        servidor_saliente=fila.servidor_saliente,
        notas=fila.notas,
        tiene_password=bool(fila.password_cifrada),
        password_actualizada_en=fila.password_actualizada_en,
    )


def _out(fila: Institucion, usuario: dict) -> InstitucionOut:
    salida = InstitucionOut.model_validate(fila)
    salida.emails = [_email_out(e) for e in fila.emails]
    salida.secretos_disponibles = cifrado_disponible()
    # Para que la pantalla no ofrezca lo que la API va a rechazar. No es el
    # control —ese está en `_exigir_claves`—, es sólo la UI diciendo la verdad.
    salida.puede_ver_claves = _puede_ver_claves(usuario)
    return salida


async def _refrescar(db: AsyncSession, usuario: dict) -> InstitucionOut:
    """Relee todo después de un cambio y serializa.

    Se relee en vez de refrescar el objeto en memoria porque hay dos cosas que
    el commit dejó desactualizadas y no una: las colecciones (un teléfono nuevo
    no aparece) y `actualizado_en`, que lo escribe MySQL. Un `refresh` parcial
    resolvería la primera y dejaría la segunda expirada.
    """
    fila = await _cargar(db)
    assert fila is not None  # el singleton ya existe a esta altura
    return _out(fila, usuario)


def _marcar_edicion(fila: Institucion, usuario: dict) -> None:
    """Deja constancia de quién tocó los datos institucionales.

    `uid` puede venir `None` en tokens viejos (ver `app/auth/deps.py`), y eso no
    puede impedir guardar: se registra lo que haya.
    """
    uid = usuario.get("uid")
    fila.actualizado_por = int(uid) if uid is not None else None
    fila.actualizado_en = datetime.datetime.now()


# ── Datos generales ──────────────────────────────────────────────────────────

@router.get("/", response_model=InstitucionOut)
async def ver_institucion(
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    """Todo junto: datos fiscales, bancarios, teléfonos y casillas.

    Un solo request porque la pantalla los muestra en una sola vista y son
    pocas filas — partirlo en tres endpoints sería tráfico extra sin ningún
    consumidor que quiera las partes por separado.
    """
    return _out(await _obtener(db), usuario)


@router.put("/", response_model=InstitucionOut)
async def guardar_institucion(
    body: InstitucionIn,
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    """Reemplaza los datos generales. No toca teléfonos ni casillas.

    Es un `PUT` y aplica todos los campos del body, incluidos los que vienen
    `None`: la pantalla manda el formulario completo, así que un campo que llega
    vacío es un campo que el usuario borró. Un `PATCH` que ignorara los `None`
    haría imposible borrar un dato cargado por error.
    """
    fila = await _obtener(db)
    for campo, valor in body.model_dump().items():
        setattr(fila, campo, valor)
    _marcar_edicion(fila, usuario)
    await db.commit()
    return await _refrescar(db, usuario)


# ── Teléfonos ────────────────────────────────────────────────────────────────

@router.post("/telefonos", response_model=TelefonoOut, status_code=status.HTTP_201_CREATED)
async def agregar_telefono(
    body: TelefonoIn,
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    fila = await _obtener(db)
    tel = InstitucionTelefono(institucion_id=fila.id, **body.model_dump())
    db.add(tel)
    _marcar_edicion(fila, usuario)
    await db.commit()
    await db.refresh(tel)
    return TelefonoOut.model_validate(tel)


@router.put("/telefonos/{telefono_id}", response_model=TelefonoOut)
async def editar_telefono(
    body: TelefonoIn,
    telefono_id: int = PathParam(..., ge=1),
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    tel = await db.get(InstitucionTelefono, telefono_id)
    if not tel:
        raise HTTPException(404, "Teléfono no encontrado")
    for campo, valor in body.model_dump().items():
        setattr(tel, campo, valor)
    _marcar_edicion(await _obtener(db), usuario)
    await db.commit()
    await db.refresh(tel)
    return TelefonoOut.model_validate(tel)


@router.delete("/telefonos/{telefono_id}", status_code=status.HTTP_204_NO_CONTENT)
async def borrar_telefono(
    telefono_id: int = PathParam(..., ge=1),
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
) -> Response:
    """Borrado real, no lógico: un teléfono viejo en la lista es peor que
    ninguno — alguien lo llama y no atiende nadie. No hay nada que auditar en
    el número en sí, y el registro de quién lo borró queda en `audit_log`."""
    tel = await db.get(InstitucionTelefono, telefono_id)
    if not tel:
        raise HTTPException(404, "Teléfono no encontrado")
    await db.delete(tel)
    _marcar_edicion(await _obtener(db), usuario)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Casillas de correo ───────────────────────────────────────────────────────

@router.post("/mails", response_model=EmailOut, status_code=status.HTTP_201_CREATED)
async def agregar_mail(
    body: EmailIn,
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    """Da de alta una casilla. La contraseña se carga después, aparte."""
    fila = await _obtener(db)
    mail = InstitucionEmail(institucion_id=fila.id, **body.model_dump())
    db.add(mail)
    _marcar_edicion(fila, usuario)
    await db.commit()
    await db.refresh(mail)
    return _email_out(mail)


@router.put("/mails/{mail_id}", response_model=EmailOut)
async def editar_mail(
    body: EmailIn,
    mail_id: int = PathParam(..., ge=1),
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    """Edita los datos de la casilla. **La contraseña queda como estaba.**

    `EmailIn` no tiene campo de contraseña justamente para que esto sea cierto
    por construcción y no por acordarse de excluirlo acá.
    """
    mail = await db.get(InstitucionEmail, mail_id)
    if not mail:
        raise HTTPException(404, "Casilla no encontrada")
    for campo, valor in body.model_dump().items():
        setattr(mail, campo, valor)
    _marcar_edicion(await _obtener(db), usuario)
    await db.commit()
    await db.refresh(mail)
    return _email_out(mail)


@router.delete("/mails/{mail_id}", status_code=status.HTTP_204_NO_CONTENT)
async def borrar_mail(
    mail_id: int = PathParam(..., ge=1),
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
) -> Response:
    """Borra la casilla y, con ella, la contraseña cifrada."""
    mail = await db.get(InstitucionEmail, mail_id)
    if not mail:
        raise HTTPException(404, "Casilla no encontrada")
    await db.delete(mail)
    _marcar_edicion(await _obtener(db), usuario)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Contraseñas ──────────────────────────────────────────────────────────────

@router.put("/mails/{mail_id}/password", response_model=EmailOut)
async def guardar_password(
    body: PasswordIn,
    mail_id: int = PathParam(..., ge=1),
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    """Guarda o borra la contraseña de una casilla.

    `password: null` (o vacío) la borra. La respuesta es el `EmailOut` de
    siempre: confirma que quedó guardada mostrando `tiene_password`, sin
    devolver el valor.

    Sin `SECRETOS_KEY` responde 503 y **no escribe nada**. Es el punto donde el
    diseño falla cerrado: la alternativa —guardarla en claro «por ahora»— es
    exactamente lo que este módulo existe para evitar.
    """
    _exigir_claves(usuario)

    mail = await db.get(InstitucionEmail, mail_id)
    if not mail:
        raise HTTPException(404, "Casilla no encontrada")

    texto = (body.password or "").strip()
    if not texto:
        mail.password_cifrada = None
        mail.password_actualizada_en = None
    else:
        try:
            mail.password_cifrada = cifrar(texto)
        except SecretoNoDisponible as exc:
            raise HTTPException(503, str(exc)) from exc
        mail.password_actualizada_en = datetime.datetime.now()

    _marcar_edicion(await _obtener(db), usuario)
    await db.commit()
    await db.refresh(mail)
    return _email_out(mail)


@router.post("/mails/{mail_id}/password/revelar", response_model=PasswordRevelada)
async def revelar_password(
    mail_id: int = PathParam(..., ge=1),
    db: AsyncSession = Depends(get_db),
    usuario=Depends(get_current_user),
):
    """Devuelve la contraseña en claro. El único endpoint que lo hace.

    **Es un `POST` aunque no modifique nada**, y eso es a propósito:
    `app/middleware/audit.py` sólo audita métodos mutantes, así que un `GET`
    dejaría las lecturas de credenciales sin rastro. Con `POST`, cada vez que
    alguien mira una clave queda la fila en `audit_log` con usuario, IP y hora.

    El cuerpo de la respuesta no pasa por auditoría —el middleware guarda el
    request, no el response—, así que la clave no termina escrita en la tabla.

    Además del scope, exige estar en la lista nominal de
    `INSTITUCION_CLAVES_SOCIOS` — hoy ANA y GRACIELA. Ver `_exigir_claves`.
    """
    _exigir_claves(usuario)

    mail = await db.get(InstitucionEmail, mail_id)
    if not mail:
        raise HTTPException(404, "Casilla no encontrada")
    if not mail.password_cifrada:
        raise HTTPException(404, "Esta casilla no tiene contraseña guardada.")

    try:
        texto = descifrar(mail.password_cifrada)
    except SecretoNoDisponible as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        # Se guardó con otra llave. Es un 409 y no un 500: el servidor está
        # bien, el dato es irrecuperable y lo que hay que hacer es volver a
        # cargar la contraseña.
        raise HTTPException(409, str(exc)) from exc

    return PasswordRevelada(id=mail.id, direccion=mail.direccion, password=texto)


@router.get("/telefonos", response_model=List[TelefonoOut])
async def listar_telefonos(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    """Los teléfonos sueltos, para quien no necesita el resto de la ficha.

    Existe para que otras pantallas (pie del boletín, plantillas de mail) puedan
    pedir sólo esto sin traerse las casillas de correo — que es la tabla con las
    credenciales — en el mismo response.
    """
    fila = await _obtener(db)
    return [TelefonoOut.model_validate(t) for t in fila.telefonos]
