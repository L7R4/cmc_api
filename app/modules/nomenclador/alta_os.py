"""Etapa 3 del nomenclador: el código DADO DE ALTA en una obra social.

Flujo del nomenclador, cada etapa usable por separado:
  1. Código (catálogo)             → nm_nomenclador
  2. Quién factura (plantilla)     → nm_plantilla_especialidad_codigo (+ sin restricción)
  3. Alta en la O.S. (sin precio)  → nm_codigo_obra_social + nm_valor_especialidad   ← acá
  4. Precio                        → nm_valores (+ componentes + historial)

El único requisito duro entre etapas: no hay precio sin alta (4 necesita 3).

Mientras dure la migración, los datos del par (descripción, sin restricción,
autorización, ayudantes, categoría, complejidad) viven en `nm_codigo_obra_social`
Y en cada `Valor` activo del par: lo que se edita acá se copia a las variantes
(`sincronizar_valores`), y lo que se edita desde una variante vuelve al par
(`sincronizar_par_desde_valor`).
"""
from __future__ import annotations

import datetime
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import and_, case, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.catalogs import Especialidad, ObrasSociales
from app.db.models.cmc_facturacion import DetalleFacturacionCMC
from app.db.models.nomenclador_cmc import (
    CodigoObraSocial,
    HistorialPrecioCodigo,
    NomencladorCMC,
    NomencladorPlantillaEspecialidad,
    Valor,
    ValorEspecialidad,
)
from app.modules.nomenclador import service
from app.modules.nomenclador.schemas import (
    AltaCodigoItem,
    AltaCodigoResultado,
    AltaCodigosIn,
    CodigoObraSocialOut,
    CodigoObraSocialUpdate,
    CodigoPorOSItem,
    CodigosPorOSOut,
    FichaCodigoOut,
    FichaObraSocialItem,
)

SIN_ALTA, SIN_PRECIO, CON_PRECIO, SUSPENDIDO = "sin_alta", "sin_precio", "con_precio", "suspendido"
ESTADOS = (SIN_ALTA, SIN_PRECIO, CON_PRECIO, SUSPENDIDO)

# Datos del par que también viven (copiados) en cada Valor activo.
_CAMPOS_SINCRONIZADOS = (
    "descripcion", "categoria", "complejidad", "requiere_autorizacion", "cantidad_ayudantes",
)


def estado_de(par: Optional[CodigoObraSocial], tiene_precio: bool) -> str:
    if par is None:
        return SIN_ALTA
    if par.estado == "suspendido":
        return SUSPENDIDO
    return CON_PRECIO if tiene_precio else SIN_PRECIO


# ─── Lectura ─────────────────────────────────────────────────────────────────

async def get_par(
    db: AsyncSession, obra_social_nro: int, nomenclador_id: int
) -> Optional[CodigoObraSocial]:
    return (await db.execute(select(CodigoObraSocial).where(
        CodigoObraSocial.obra_social_nro == obra_social_nro,
        CodigoObraSocial.nomenclador_id == nomenclador_id,
    ))).scalar_one_or_none()


async def get_par_por_codigo(
    db: AsyncSession, obra_social_nro: int, codigo: str
) -> Optional[CodigoObraSocial]:
    return (await db.execute(select(CodigoObraSocial).where(
        CodigoObraSocial.obra_social_nro == obra_social_nro,
        CodigoObraSocial.codigo == codigo,
    ))).scalars().first()


async def tiene_precio(db: AsyncSession, obra_social_nro: int, nomenclador_id: int) -> bool:
    return (await db.execute(select(Valor.id).where(
        Valor.obra_social_nro == obra_social_nro,
        Valor.nomenclador_id == nomenclador_id,
        Valor.estado == "activo",
    ).limit(1))).scalar_one_or_none() is not None


async def exigir_alta_activa(db: AsyncSession, obra_social_nro: int, nomenclador_id: int) -> CodigoObraSocial:
    """409 si el código no está dado de alta (o está suspendido) en la O.S.: la carga
    manual de precio (etapa 4) lo exige. Las herramientas masivas que crean alta y
    precio juntos no pasan por acá (ver `asegurar_pares`)."""
    par = await get_par(db, obra_social_nro, nomenclador_id)
    if par is None:
        raise HTTPException(
            409, "El código no está dado de alta en esta obra social. Dalo de alta primero "
                 "(Códigos por obra social) y después cargale el precio.",
        )
    if par.estado == "suspendido":
        raise HTTPException(409, "El código está suspendido en esta obra social. Reactivalo primero.")
    return par


async def detalle_par(db: AsyncSession, obra_social_nro: int, nomenclador_id: int) -> CodigoObraSocialOut:
    nom = await db.get(NomencladorCMC, nomenclador_id)
    if nom is None:
        raise HTTPException(404, "Código no encontrado")
    par = await get_par(db, obra_social_nro, nomenclador_id)
    con_precio = await tiene_precio(db, obra_social_nro, nomenclador_id)
    esps = sorted(await service.especialidades_habilitadas_de(db, nom.codigo, obra_social_nro))
    return CodigoObraSocialOut(
        obra_social_nro=obra_social_nro, nomenclador_id=nom.id, codigo=nom.codigo,
        descripcion=par.descripcion if par else None,
        descripcion_colegio=nom.descripcion,
        categoria=par.categoria if par else None,
        complejidad=par.complejidad if par else None,
        requiere_autorizacion=par.requiere_autorizacion if par else None,
        cantidad_ayudantes=par.cantidad_ayudantes if par else None,
        observacion=par.observacion if par else None,
        sin_restriccion_especialidad=bool(par and par.sin_restriccion_especialidad),
        especialidades=esps,
        estado=estado_de(par, con_precio),
        tiene_precio=con_precio,
    )


# ─── Alta (etapa 3) ──────────────────────────────────────────────────────────

async def _plantilla(db: AsyncSession, codigo: str) -> list[int]:
    return list((await db.execute(
        select(NomencladorPlantillaEspecialidad.especialidad_id_colegio)
        .where(NomencladorPlantillaEspecialidad.codigo == codigo)
        .order_by(NomencladorPlantillaEspecialidad.especialidad_id_colegio)
    )).scalars())


async def _validar_especialidades(db: AsyncSession, ids: Iterable[int]) -> None:
    ids = set(ids)
    if not ids:
        return
    validas = set((await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE).where(Especialidad.ID_COLEGIO_ESPE.in_(ids))
    )).scalars())
    if ids - validas:
        raise ValueError(service.mensaje_especialidades_inexistentes(ids - validas))


async def _dar_de_alta_uno(
    db: AsyncSession, item: AltaCodigoItem, body: AltaCodigosIn, usuario: Optional[str],
) -> AltaCodigoResultado:
    nom = await db.get(NomencladorCMC, item.nomenclador_id)
    if nom is None or not nom.activo:
        raise ValueError("El código no existe o está inactivo en el catálogo")
    os_ok = (await db.execute(select(ObrasSociales.ID).where(
        ObrasSociales.NRO_OBRASOCIAL == item.obra_social_nro
    ))).scalar_one_or_none()
    if os_ok is None:
        raise ValueError(f"La obra social {item.obra_social_nro} no existe")

    par = await get_par(db, item.obra_social_nro, nom.id)
    if par is not None and par.estado == "activo":
        return AltaCodigoResultado(
            obra_social_nro=item.obra_social_nro, nomenclador_id=nom.id, codigo=nom.codigo,
            estado="ya_existia", motivo="Ya estaba dado de alta",
        )

    estado = "reactivado" if par is not None else "creado"
    if par is None:
        par = CodigoObraSocial(
            obra_social_nro=item.obra_social_nro, nomenclador_id=nom.id, codigo=nom.codigo,
            creado_por=usuario,
        )
        db.add(par)
    par.estado = "activo"
    par.descripcion = (item.descripcion or "").strip() or par.descripcion or nom.descripcion
    if body.requiere_autorizacion is not None:
        par.requiere_autorizacion = body.requiere_autorizacion
    if body.cantidad_ayudantes is not None:
        par.cantidad_ayudantes = body.cantidad_ayudantes or None

    # Quién factura: lo pedido > lo que el par ya tenía configurado > la plantilla.
    ya_configuradas = await service.especialidades_habilitadas_de(db, nom.codigo, item.obra_social_nro)
    if item.sin_restriccion_especialidad is not None:
        par.sin_restriccion_especialidad = item.sin_restriccion_especialidad
    elif not ya_configuradas and not par.sin_restriccion_especialidad:
        par.sin_restriccion_especialidad = bool(nom.sin_restriccion_especialidad)

    if item.especialidades is not None:
        await _validar_especialidades(db, item.especialidades)
        await service.reemplazar_especialidades(db, item.obra_social_nro, nom.codigo, item.especialidades)
    elif not ya_configuradas and not par.sin_restriccion_especialidad:
        plantilla = await _plantilla(db, nom.codigo)
        if plantilla:
            await db.execute(insert(ValorEspecialidad), [
                {"obra_social_nro": item.obra_social_nro, "codigo": nom.codigo,
                 "especialidad_id_colegio": e}
                for e in plantilla
            ])
    await db.flush()

    if estado == "reactivado":
        await sincronizar_valores(db, par)
    habilitadas = await service.especialidades_habilitadas_de(db, nom.codigo, item.obra_social_nro)
    return AltaCodigoResultado(
        obra_social_nro=item.obra_social_nro, nomenclador_id=nom.id, codigo=nom.codigo,
        estado=estado,
        sin_quien_factura=not par.sin_restriccion_especialidad and not habilitadas,
    )


async def dar_de_alta(db: AsyncSession, body: AltaCodigosIn, usuario: Optional[str]) -> list[AltaCodigoResultado]:
    """Da de alta (o reactiva) cada (O.S., código), sin precio. Cada item en su
    propio savepoint: uno que falla no deshace los demás. No hace commit."""
    out: list[AltaCodigoResultado] = []
    vistos: set[tuple[int, int]] = set()
    for item in body.items:
        clave = (item.obra_social_nro, item.nomenclador_id)
        if clave in vistos:
            continue
        vistos.add(clave)
        try:
            async with db.begin_nested():
                out.append(await _dar_de_alta_uno(db, item, body, usuario))
        except (ValueError, HTTPException) as e:
            nom = await db.get(NomencladorCMC, item.nomenclador_id)
            out.append(AltaCodigoResultado(
                obra_social_nro=item.obra_social_nro, nomenclador_id=item.nomenclador_id,
                codigo=nom.codigo if nom else "?", estado="error",
                motivo=str(e.detail if isinstance(e, HTTPException) else e),
            ))
    return out


async def asegurar_pares(db: AsyncSession, valores: Iterable[dict]) -> None:
    """Crea el par de cada (O.S., código) que todavía no lo tenga, con los datos del
    valor que se está creando. Lo llaman los puntos únicos de alta de `Valor`
    (`service.persistir_valor` / `persistir_valores_en_bloque`, `_crear_valor_con_
    componentes`, `_clonar_valor`): las herramientas que crean precio sin pasar por
    la etapa 3 (Completar NN, CSV, replicar) dejan igual el código dado de alta.
    No toca pares existentes. No hace commit."""
    por_clave: dict[tuple[int, int], dict] = {}
    for v in valores:
        por_clave.setdefault((v["obra_social_nro"], v["nomenclador_id"]), v)
    if not por_clave:
        return
    existentes = set((await db.execute(
        select(CodigoObraSocial.obra_social_nro, CodigoObraSocial.nomenclador_id).where(
            CodigoObraSocial.obra_social_nro.in_({k[0] for k in por_clave}),
            CodigoObraSocial.nomenclador_id.in_({k[1] for k in por_clave}),
        )
    )).all())
    nuevos = [
        {
            "obra_social_nro": os_nro, "nomenclador_id": nom_id, "codigo": v["codigo"],
            "descripcion": v.get("descripcion"), "categoria": v.get("categoria"),
            "complejidad": v.get("complejidad"),
            "requiere_autorizacion": v.get("requiere_autorizacion"),
            "cantidad_ayudantes": v.get("cantidad_ayudantes"),
            "sin_restriccion_especialidad": bool(v.get("sin_restriccion_especialidad")),
            "estado": "activo",
        }
        for (os_nro, nom_id), v in por_clave.items() if (os_nro, nom_id) not in existentes
    ]
    if nuevos:
        await db.execute(insert(CodigoObraSocial), nuevos)


def campos_de_valor(valor: Valor) -> dict:
    return {
        "obra_social_nro": valor.obra_social_nro, "nomenclador_id": valor.nomenclador_id,
        "codigo": valor.codigo, "descripcion": valor.descripcion, "categoria": valor.categoria,
        "complejidad": valor.complejidad, "requiere_autorizacion": valor.requiere_autorizacion,
        "cantidad_ayudantes": valor.cantidad_ayudantes,
        "sin_restriccion_especialidad": valor.sin_restriccion_especialidad,
    }


# ─── Edición del par ─────────────────────────────────────────────────────────

async def sincronizar_valores(
    db: AsyncSession, par: CodigoObraSocial, *, cerrar_dependientes: bool = False,
) -> None:
    """Copia los datos del par a todas sus variantes activas (transición: mientras
    las variantes los sigan guardando). "Sin restricción" pasa por
    `fijar_sin_restriccion_par`, que valida que no quede una NE huérfana."""
    for v in await service.variantes_del_par(db, par.obra_social_nro, par.codigo):
        for campo in _CAMPOS_SINCRONIZADOS:
            setattr(v, campo, getattr(par, campo))
    await service.fijar_sin_restriccion_par(
        db, par.obra_social_nro, par.codigo, bool(par.sin_restriccion_especialidad),
        cerrar_dependientes=cerrar_dependientes,
    )


async def sincronizar_par_desde_valor(db: AsyncSession, valor: Valor) -> None:
    """Inverso de `sincronizar_valores`: después de editar los datos del par desde una
    variante (lápiz del código en Precios por O.S.), los deja también en el par."""
    par = await get_par(db, valor.obra_social_nro, valor.nomenclador_id)
    if par is None:
        await asegurar_pares(db, [campos_de_valor(valor)])
        return
    for campo in _CAMPOS_SINCRONIZADOS:
        setattr(par, campo, getattr(valor, campo))
    par.sin_restriccion_especialidad = bool(valor.sin_restriccion_especialidad)
    await db.flush()


async def actualizar_par(
    db: AsyncSession, obra_social_nro: int, nomenclador_id: int, cambios: CodigoObraSocialUpdate,
) -> CodigoObraSocialOut:
    par = await get_par(db, obra_social_nro, nomenclador_id)
    if par is None:
        raise HTTPException(404, "El código no está dado de alta en esta obra social")
    datos = cambios.model_dump(exclude_unset=True)
    for campo in ("descripcion", "categoria", "complejidad", "requiere_autorizacion",
                  "cantidad_ayudantes", "observacion"):
        if campo in datos:
            valor = datos[campo]
            if isinstance(valor, str):
                valor = valor.strip() or None
            setattr(par, campo, valor)
    if "sin_restriccion_especialidad" in datos:
        par.sin_restriccion_especialidad = bool(datos["sin_restriccion_especialidad"])
    cerrar = cambios.cerrar_precios
    try:
        if "especialidades" in datos and datos["especialidades"] is not None:
            await _validar_especialidades(db, datos["especialidades"])
            await service.reemplazar_especialidades(
                db, obra_social_nro, par.codigo, datos["especialidades"],
                cerrar_dependientes=cerrar,
            )
        await db.flush()
        await sincronizar_valores(db, par, cerrar_dependientes=cerrar)
    except service.PreciosDependientesError as e:
        # La pantalla pregunta si cerrar esos precios y reintenta con cerrar_precios.
        nombres = await service.nombres_de_especialidades(
            db, [v.especialidad_id_colegio for v in e.valores]
        )
        ayer = datetime.date.today() - datetime.timedelta(days=1)
        raise HTTPException(409, {
            "tipo": "precios_dependientes",
            "mensaje": e.mensaje,
            "cierre": ayer.isoformat(),
            "precios": [
                {
                    "id": v.id,
                    "especialidad": (
                        nombres.get(v.especialidad_id_colegio, f"Especialidad {v.especialidad_id_colegio}")
                        if v.especialidad_id_colegio is not None else "Cualquier especialidad"
                    ),
                    "vigencia_desde": v.vigencia_desde.isoformat(),
                }
                for v in e.valores
            ],
        })
    except ValueError as e:
        raise HTTPException(422, str(e))
    await db.flush()
    return await detalle_par(db, obra_social_nro, nomenclador_id)


async def cambiar_estado(
    db: AsyncSession, obra_social_nro: int, nomenclador_id: int, estado: str,
) -> CodigoObraSocialOut:
    par = await get_par(db, obra_social_nro, nomenclador_id)
    if par is None:
        raise HTTPException(404, "El código no está dado de alta en esta obra social")
    par.estado = estado
    await db.flush()
    return await detalle_par(db, obra_social_nro, nomenclador_id)


# ─── Listado por obra social (pantalla "Códigos por obra social") ─────────────

async def listar_por_os(
    db: AsyncSession, obra_social_nro: int, *, estado: Optional[str] = None,
    q: Optional[str] = None, tipo: Optional[str] = None, page: int = 1, size: int = 50,
) -> CodigosPorOSOut:
    N, P = NomencladorCMC, CodigoObraSocial
    con_precio = (
        select(Valor.nomenclador_id).where(
            Valor.obra_social_nro == obra_social_nro, Valor.estado == "activo",
        ).distinct().subquery()
    )
    esp_os = (
        select(ValorEspecialidad.codigo, func.count().label("n"))
        .where(ValorEspecialidad.obra_social_nro == obra_social_nro)
        .group_by(ValorEspecialidad.codigo).subquery()
    )
    esp_pl = (
        select(NomencladorPlantillaEspecialidad.codigo, func.count().label("n"))
        .group_by(NomencladorPlantillaEspecialidad.codigo).subquery()
    )
    estado_sql = case(
        (P.id.is_(None), SIN_ALTA),
        (P.estado == "suspendido", SUSPENDIDO),
        (con_precio.c.nomenclador_id.is_not(None), CON_PRECIO),
        else_=SIN_PRECIO,
    ).label("estado")

    base = (
        select(
            N.id, N.codigo, N.descripcion, P.descripcion.label("desc_os"), estado_sql,
            P.sin_restriccion_especialidad, func.coalesce(esp_os.c.n, 0).label("esp_os"),
            func.coalesce(esp_pl.c.n, 0).label("esp_pl"), N.sin_restriccion_especialidad.label("pl_sr"),
        )
        .select_from(N)
        .outerjoin(P, and_(P.nomenclador_id == N.id, P.obra_social_nro == obra_social_nro))
        .outerjoin(con_precio, con_precio.c.nomenclador_id == N.id)
        .outerjoin(esp_os, esp_os.c.codigo == N.codigo)
        .outerjoin(esp_pl, esp_pl.c.codigo == N.codigo)
        .where(N.activo.is_(True))
    )
    if q and q.strip():
        like = f"%{q.strip()}%"
        base = base.where(or_(N.codigo.like(f"{q.strip()}%"), N.descripcion.like(like), P.descripcion.like(like)))
    if tipo:
        # Como `q`, acota también los conteos por estado: muestran lo que hay de ese tipo.
        base = base.where(service.condicion_tipo(
            service.categoria_os_sql(obra_social_nro, N.id, N.categoria), tipo,
        ))

    sub = base.subquery()
    conteos = {e: 0 for e in ESTADOS}
    for e, n in (await db.execute(select(sub.c.estado, func.count()).group_by(sub.c.estado))).all():
        conteos[e] = n
    filtrado = select(sub)
    if estado:
        filtrado = filtrado.where(sub.c.estado == estado)
    total = (await db.execute(select(func.count()).select_from(filtrado.subquery()))).scalar_one()
    filas = (await db.execute(
        filtrado.order_by(sub.c.codigo).offset((page - 1) * size).limit(size)
    )).all()
    return CodigosPorOSOut(
        obra_social_nro=obra_social_nro, total=total, page=page, size=size, conteos=conteos,
        items=[
            CodigoPorOSItem(
                nomenclador_id=f.id, codigo=f.codigo, descripcion_colegio=f.descripcion,
                descripcion_os=f.desc_os, estado=f.estado,
                sin_restriccion_especialidad=bool(f.sin_restriccion_especialidad),
                especialidades_os=f.esp_os, especialidades_plantilla=f.esp_pl,
                plantilla_sin_restriccion=bool(f.pl_sr),
            )
            for f in filas
        ],
    )


# ─── Ficha del código ────────────────────────────────────────────────────────

async def ficha(db: AsyncSession, nomenclador_id: int) -> FichaCodigoOut:
    nom = await db.get(NomencladorCMC, nomenclador_id)
    if nom is None:
        raise HTTPException(404, "Código no encontrado")
    hoy = datetime.date.today()

    obras = (await db.execute(
        select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
        .where(ObrasSociales.activo.is_(True))
        .order_by(ObrasSociales.OBRA_SOCIAL)
    )).all()
    pares = {
        p.obra_social_nro: p for p in (await db.execute(
            select(CodigoObraSocial).where(CodigoObraSocial.nomenclador_id == nom.id)
        )).scalars()
    }
    esp_por_os = dict((await db.execute(
        select(ValorEspecialidad.obra_social_nro, func.count())
        .where(ValorEspecialidad.codigo == nom.codigo)
        .group_by(ValorEspecialidad.obra_social_nro)
    )).all())
    con_precio = set((await db.execute(
        select(Valor.obra_social_nro).where(
            Valor.nomenclador_id == nom.id, Valor.estado == "activo",
        ).distinct()
    )).scalars())
    # Precio vigente hoy, por O.S.: la fila más reciente por variante.
    vigentes: dict[int, dict] = {}
    for h in (await db.execute(
        select(HistorialPrecioCodigo).where(
            HistorialPrecioCodigo.nomenclador_id == nom.id,
            HistorialPrecioCodigo.vigencia_desde <= hoy,
            or_(HistorialPrecioCodigo.vigencia_hasta.is_(None), HistorialPrecioCodigo.vigencia_hasta >= hoy),
        ).order_by(HistorialPrecioCodigo.vigencia_desde.desc())
    )).scalars():
        vigentes.setdefault(h.obra_social_nro, {}).setdefault((h.origen, h.especialidad_id_colegio), h)
    sin_valorizar = dict((await db.execute(
        select(DetalleFacturacionCMC.cod_obr, func.count()).where(
            DetalleFacturacionCMC.cod_nom == nom.codigo,
            DetalleFacturacionCMC.estado == "A",
            DetalleFacturacionCMC.sin_valorizar.is_not(None),
        ).group_by(DetalleFacturacionCMC.cod_obr)
    )).all())

    items: list[FichaObraSocialItem] = []
    conteos = {e: 0 for e in ESTADOS}
    for nro, nombre in obras:
        par = pares.get(nro)
        estado = estado_de(par, nro in con_precio)
        conteos[estado] += 1
        variantes = vigentes.get(nro, {})
        tipo = total = desde = None
        if variantes:
            filas = list(variantes.values())
            desde = max(f.vigencia_desde for f in filas)
            ne_con_esp = [f for f in filas if f.origen == "NE" and f.especialidad_id_colegio is not None]
            if ne_con_esp:
                tipo = "por_especialidad"
            else:
                tipo = "igual"
                # NE sin especialidad (par sin restricción) gana a NN, como en el lookup.
                f = next((f for f in filas if f.origen == "NE"), filas[0])
                total = f.precio_total
        items.append(FichaObraSocialItem(
            obra_social_nro=nro, nombre=nombre, estado=estado,
            sin_restriccion_especialidad=bool(par and par.sin_restriccion_especialidad),
            especialidades=esp_por_os.get(nro, 0),
            precio_tipo=tipo, precio_total=total,
            variantes=len(variantes), vigencia_desde=desde,
            prestaciones_sin_valorizar=sin_valorizar.get(str(nro), 0),
        ))
    return FichaCodigoOut(
        nomenclador_id=nom.id, codigo=nom.codigo, descripcion=nom.descripcion,
        categoria=nom.categoria, complejidad=nom.complejidad, activo=nom.activo,
        plantilla_especialidades=await _plantilla(db, nom.codigo),
        plantilla_sin_restriccion=bool(nom.sin_restriccion_especialidad),
        conteos=conteos, obras_sociales=items,
    )


# ─── Etapa 2 → O.S. existentes ("Actualizar en obras sociales") ───────────────

async def _nombres_os(db: AsyncSession, nros: Iterable[int]) -> dict[int, str]:
    return dict((await db.execute(
        select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
        .where(ObrasSociales.NRO_OBRASOCIAL.in_(set(nros)))
    )).all())


async def propagar_plantilla(
    db: AsyncSession, nom: NomencladorCMC, obra_social_nros: list[int], modo: str, dry_run: bool,
):
    """Lleva la plantilla del Colegio (etapa 2) a las O.S. que YA tienen el código
    dado de alta. `agregar`: suma lo que falta. `igualar`: además quita lo que la
    O.S. tenga de más, también las especialidades con precio NE activo: esas dejan de
    poder facturar y su precio se da de baja desde hoy (`quita_con_precio` las informa,
    para que la vista previa lo avise). Las O.S. "sin restricción" o sin alta se
    saltean. Cada O.S. en su savepoint. No hace commit."""
    from app.modules.nomenclador.schemas import PropagarEspecialidadesItem

    plantilla = set(await _plantilla(db, nom.codigo))
    nombres = await _nombres_os(db, obra_social_nros)
    out: list = []
    for nro in dict.fromkeys(obra_social_nros):
        nombre = nombres.get(nro, str(nro))
        par = await get_par(db, nro, nom.id)
        if par is None:
            out.append(PropagarEspecialidadesItem(
                obra_social_nro=nro, nombre=nombre, estado="salteada",
                motivo="No tiene el código dado de alta"))
            continue
        if par.sin_restriccion_especialidad:
            out.append(PropagarEspecialidadesItem(
                obra_social_nro=nro, nombre=nombre, estado="salteada",
                motivo="Sin restricción: lo factura cualquier especialidad"))
            continue
        actuales = await service.especialidades_habilitadas_de(db, nom.codigo, nro)
        agrega = sorted(plantilla - actuales)
        quita: list[int] = []
        quita_con_precio: list[int] = []
        if modo == "igualar":
            con_precio = set((await db.execute(select(Valor.especialidad_id_colegio).where(
                Valor.obra_social_nro == nro, Valor.codigo == nom.codigo,
                Valor.origen == "NE", Valor.estado == "activo",
                Valor.especialidad_id_colegio.is_not(None),
            ))).scalars())
            sobran = actuales - plantilla
            quita = sorted(sobran)
            quita_con_precio = sorted(sobran & con_precio)
        if not agrega and not quita:
            out.append(PropagarEspecialidadesItem(
                obra_social_nro=nro, nombre=nombre, estado="sin_cambios"))
            continue
        try:
            if not dry_run:
                async with db.begin_nested():
                    await service.reemplazar_especialidades(
                        db, nro, nom.codigo, sorted((actuales | set(agrega)) - set(quita)),
                        cerrar_dependientes=True,
                    )
            out.append(PropagarEspecialidadesItem(
                obra_social_nro=nro, nombre=nombre, estado="actualizada",
                agrega=agrega, quita=quita, quita_con_precio=quita_con_precio))
        except ValueError as e:
            out.append(PropagarEspecialidadesItem(
                obra_social_nro=nro, nombre=nombre, estado="error", motivo=str(e)))
    return out


async def guardar_y_aplicar(
    db: AsyncSession, nom: NomencladorCMC, obra_social_nros: list[int], usuario: Optional[str],
):
    """"Guardar y aplicar" de la página del código: en las O.S. que NO tienen el
    código lo da de alta sin precio (con la plantilla); en las que SÍ, suma las
    especialidades de la plantilla que les falten. Nunca crea precios. No hace commit."""
    from app.modules.nomenclador.schemas import (
        AltaCodigoItem as _Item, AltaCodigosIn as _In, AplicarAltaItem,
    )

    nombres = await _nombres_os(db, obra_social_nros)
    out: list = []
    con_alta = [n for n in dict.fromkeys(obra_social_nros) if await get_par(db, n, nom.id)]
    sin_alta = [n for n in dict.fromkeys(obra_social_nros) if n not in con_alta]

    for r in await dar_de_alta(
        db, _In(items=[_Item(obra_social_nro=n, nomenclador_id=nom.id) for n in sin_alta]), usuario,
    ) if sin_alta else []:
        out.append(AplicarAltaItem(
            obra_social_nro=r.obra_social_nro, nombre=nombres.get(r.obra_social_nro, ""),
            estado="alta_creada" if r.estado in ("creado", "reactivado") else
                   ("sin_cambios" if r.estado == "ya_existia" else "error"),
            motivo=r.motivo, sin_quien_factura=r.sin_quien_factura,
        ))
    for p in await propagar_plantilla(db, nom, con_alta, "agregar", dry_run=False) if con_alta else []:
        out.append(AplicarAltaItem(
            obra_social_nro=p.obra_social_nro, nombre=p.nombre,
            estado={"actualizada": "especialidades_agregadas", "sin_cambios": "sin_cambios",
                    "salteada": "sin_cambios", "error": "error"}[p.estado],
            motivo=p.motivo, especialidades_agregadas=p.agrega,
        ))
    out.sort(key=lambda i: i.nombre)
    return out
