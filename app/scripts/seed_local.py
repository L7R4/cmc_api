from __future__ import annotations

import asyncio
import os
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import hash_password
from app.db.database import AsyncSessionLocal
from app.db.models import (
    ListadoMedico,
    Role,
    Permission,
    UserRole,
    RolePermission,
    ObrasSociales,
    Noticia,
)


DEFAULT_PERMISSIONS = [
    "rbac:gestionar",
    "medicos:leer",
    "medicos:agregar",
    "medicos:editar_perfil",
    "medicos:eliminar",
]


async def _get_or_create_role(db: AsyncSession, name: str, description: str | None = None) -> Role:
    role = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if role:
        return role
    role = Role(name=name, description=description)
    db.add(role)
    await db.flush()
    return role


async def _get_or_create_permission(db: AsyncSession, code: str, description: str | None = None) -> Permission:
    perm = (await db.execute(select(Permission).where(Permission.code == code))).scalar_one_or_none()
    if perm:
        return perm
    perm = Permission(code=code, description=description)
    db.add(perm)
    await db.flush()
    return perm


async def _ensure_role_permission(db: AsyncSession, role_id: int, permission_id: int) -> None:
    link = (await db.execute(
        select(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.permission_id == permission_id,
        )
    )).scalar_one_or_none()
    if link:
        return
    db.add(RolePermission(role_id=role_id, permission_id=permission_id))


async def _ensure_user_role(db: AsyncSession, user_id: int, role_id: int) -> None:
    link = (await db.execute(
        select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.role_id == role_id,
        )
    )).scalar_one_or_none()
    if link:
        return
    db.add(UserRole(user_id=user_id, role_id=role_id))


async def _get_or_create_demo_admin(db: AsyncSession) -> ListadoMedico:
    nro_socio = int(os.getenv("SEED_ADMIN_NRO_SOCIO", "9999"))
    password = os.getenv("SEED_ADMIN_PASSWORD", "admin123")
    matricula = int(os.getenv("SEED_ADMIN_MATRICULA", "1234"))

    user = (await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == nro_socio))).scalar_one_or_none()
    if user:
        # Ensure the account is usable for demo
        changed = False
        if getattr(user, "EXISTE", "N") != "S":
            user.EXISTE = "S"
            changed = True
        if not getattr(user, "hashed_password", None):
            user.hashed_password = hash_password(password)
            changed = True
        if changed:
            db.add(user)
        return user

    # IMPORTANT: ListadoMedico.ID is NOT autoincrement in this schema.
    # We use a high ID to avoid collisions with real datasets.
    user = ListadoMedico(
        ID=nro_socio,
        NRO_SOCIO=nro_socio,
        NOMBRE="Demo Admin",
        MATRICULA_PROV=matricula,
        MATRICULA_NAC=matricula,
        EXISTE="S",
        INGRESAR="A",
        hashed_password=hash_password(password),
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_public_data(db: AsyncSession) -> None:
    # One Obra Social
    obra = (await db.execute(select(ObrasSociales).where(ObrasSociales.ID == 1))).scalar_one_or_none()
    if not obra:
        db.add(ObrasSociales(ID=1, NRO_OBRASOCIAL=1, OBRA_SOCIAL="Obra Social Demo", MARCA="S", VER_VALOR="S"))

    # One News item
    noticia = (await db.execute(select(Noticia).where(Noticia.titulo == "Welcome to the Local Demo"))).scalar_one_or_none()
    if not noticia:
        db.add(
            Noticia(
                titulo="Welcome to the Local Demo",
                resumen="Small demo dataset created automatically at container startup.",
                contenido="This repository is configured to run locally with Docker Compose. Open /docs to explore the endpoints.",
                autor="CMC API",
                publicada=True,
                tipo="Noticia",
            )
        )


async def main() -> None:
    async with AsyncSessionLocal() as db:
        # 1) RBAC base
        admin_role = await _get_or_create_role(db, "admin", "Full access for local demo")

        perms = []
        for code in DEFAULT_PERMISSIONS:
            perms.append(await _get_or_create_permission(db, code))

        for perm in perms:
            await _ensure_role_permission(db, admin_role.id, perm.id)

        # 2) Demo user
        demo = await _get_or_create_demo_admin(db)
        await _ensure_user_role(db, demo.ID, admin_role.id)

        # 3) Small public dataset
        await _seed_public_data(db)

        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
