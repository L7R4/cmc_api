import csv
import datetime
import io
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.nomenclador_cmc import (
    Convenio,
    Galeno,
    HistorialPrecioCodigo,
    Homologador,
    NomencladorCMC,
    Valor,
    ValorComponente,
)
from app.modules.nomenclador import service
from app.modules.nomenclador.schemas import (
    ActualizacionMasivaResult,
    ActualizarPorcentajeIn,
    ActualizarPorCodigosIn,
    EvolucionPrecioItem,
    HistorialPrecioOut,
    ImportarCSVResult,
    LookupPrecioIn,
    LookupPrecioOut,
    RevertirActualizacionIn,
    ValorCerrarYCrearIn,
    ValorComponenteOut,
    ValorCreate,
    ValorOut,
    ValorUpdate,
)
from app.modules.nomenclador.service import LookupError

router = APIRouter()


# ─── helpers ─────────────────────────────────────────────────────────────────

async def _crear_valor_con_componentes(
    db: AsyncSession,
    obra_social_nro: int,
    convenio_id: int,
    nomenclador_id: int,
    vigencia_desde: datetime.date,
    componentes_in: list,
    descripcion: Optional[str],
    nivel: Optional[int],
    observacion: Optional[str],
) -> Valor:
    nom = await db.get(NomencladorCMC, nomenclador_id)
    if not nom:
        raise HTTPException(404, "Código de nomenclador no encontrado")

    valor = Valor(
        obra_social_nro=obra_social_nro,
        convenio_id=convenio_id,
        nomenclador_id=nomenclador_id,
        codigo=nom.codigo,
        descripcion=descripcion or nom.descripcion,
        nivel=nivel,
        vigencia_desde=vigencia_desde,
        vigencia_hasta=None,
        estado="activo",
        observacion=observacion,
    )
    db.add(valor)
    await db.flush()

    for comp_in in componentes_in:
        comp = ValorComponente(
            valor_id=valor.id,
            **comp_in.model_dump(),
        )
        db.add(comp)
    await db.flush()
    return valor


# ─── CRUD Valores ─────────────────────────────────────────────────────────────

@router.get("/", response_model=List[ValorOut])
async def list_valores(
    obra_social_nro: Optional[int] = Query(None),
    convenio_id: Optional[int] = Query(None),
    codigo: Optional[str] = Query(None),
    nivel: Optional[int] = Query(None),
    estado: Optional[str] = Query(None),
    vigente_a: Optional[datetime.date] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Valor)
    if obra_social_nro:
        stmt = stmt.where(Valor.obra_social_nro == obra_social_nro)
    if convenio_id:
        stmt = stmt.where(Valor.convenio_id == convenio_id)
    if codigo:
        stmt = stmt.where(Valor.codigo == codigo)
    if nivel is not None:
        stmt = stmt.where(Valor.nivel == nivel)
    if estado:
        stmt = stmt.where(Valor.estado == estado)
    if vigente_a:
        stmt = stmt.where(
            Valor.vigencia_desde <= vigente_a,
            (Valor.vigencia_hasta.is_(None)) | (Valor.vigencia_hasta >= vigente_a),
        )
    stmt = stmt.offset((page - 1) * size).limit(size)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=ValorOut, status_code=201)
async def create_valor(body: ValorCreate, db: AsyncSession = Depends(get_db)):
    if not await db.get(Convenio, body.convenio_id):
        raise HTTPException(404, "Convenio no encontrado")

    valor = await _crear_valor_con_componentes(
        db=db,
        obra_social_nro=body.obra_social_nro,
        convenio_id=body.convenio_id,
        nomenclador_id=body.nomenclador_id,
        vigencia_desde=body.vigencia_desde,
        componentes_in=body.componentes,
        descripcion=body.descripcion,
        nivel=body.nivel,
        observacion=body.observacion,
    )

    # Regenerar historial (regla B — carga inicial)
    await service.regenerar_historial_por_valores(valor.id, None, db, motivo="carga_inicial")

    await db.commit()
    await db.refresh(valor)
    return valor


@router.get("/{id}", response_model=ValorOut)
async def get_valor(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Valor, id)
    if not obj:
        raise HTTPException(404, "Valor no encontrado")
    return obj


@router.put("/{id}", response_model=ValorOut)
async def update_valor_metadata(id: int, body: ValorUpdate, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Valor, id)
    if not obj:
        raise HTTPException(404, "Valor no encontrado")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.post("/{id}/cerrar_y_crear", response_model=ValorOut)
async def cerrar_y_crear_valor(
    id: int, body: ValorCerrarYCrearIn, db: AsyncSession = Depends(get_db)
):
    """Cierra el valor actual y crea uno nuevo con la nueva fórmula de componentes."""
    anterior = await db.get(Valor, id)
    if not anterior:
        raise HTTPException(404, "Valor no encontrado")
    if anterior.estado == "cerrado":
        raise HTTPException(409, "El valor ya está cerrado")

    fecha_corte = body.vigencia_desde - datetime.timedelta(days=1)
    anterior.estado = "cerrado"
    anterior.vigencia_hasta = fecha_corte
    await db.flush()

    nuevo = await _crear_valor_con_componentes(
        db=db,
        obra_social_nro=anterior.obra_social_nro,
        convenio_id=anterior.convenio_id,
        nomenclador_id=anterior.nomenclador_id,
        vigencia_desde=body.vigencia_desde,
        componentes_in=body.componentes,
        descripcion=body.descripcion or anterior.descripcion,
        nivel=body.nivel if body.nivel is not None else anterior.nivel,
        observacion=body.observacion or anterior.observacion,
    )

    await service.regenerar_historial_por_valores(
        nuevo.id, fecha_corte, db, motivo="valor_fijo_actualizado"
    )

    await db.commit()
    await db.refresh(nuevo)
    return nuevo


@router.delete("/{id}", status_code=204)
async def delete_valor(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Valor, id)
    if not obj:
        raise HTTPException(404, "Valor no encontrado")
    obj.estado = "cerrado"
    obj.vigencia_hasta = datetime.date.today()
    await db.commit()


@router.get("/{id}/componentes", response_model=List[ValorComponenteOut])
async def list_componentes(id: int, db: AsyncSession = Depends(get_db)):
    if not await db.get(Valor, id):
        raise HTTPException(404, "Valor no encontrado")
    stmt = select(ValorComponente).where(
        ValorComponente.valor_id == id, ValorComponente.activo == True
    ).order_by(ValorComponente.orden)
    result = await db.execute(stmt)
    return result.scalars().all()


# ─── Actualizaciones masivas ──────────────────────────────────────────────────

@router.post("/actualizar_porcentaje", response_model=ActualizacionMasivaResult)
async def actualizar_porcentaje(body: ActualizarPorcentajeIn, db: AsyncSession = Depends(get_db)):
    """Aplica un porcentaje lineal a todos los valores fijos del scope."""
    stmt = select(Valor).where(
        Valor.obra_social_nro == body.obra_social_nro,
        Valor.convenio_id == body.convenio_id,
        Valor.estado == "activo",
    )
    if body.filtro_codigos:
        stmt = stmt.where(Valor.codigo.in_(body.filtro_codigos))
    if body.filtro_rango:
        stmt = stmt.where(
            Valor.codigo >= body.filtro_rango["desde"],
            Valor.codigo <= body.filtro_rango["hasta"],
        )
    result = await db.execute(stmt)
    valores = result.scalars().all()

    factor = Decimal("1") + body.porcentaje / Decimal("100")
    fecha_corte = body.vigencia_desde - datetime.timedelta(days=1)
    actualizados = 0
    errores = []

    for v in valores:
        try:
            # Obtener componentes actuales para clonarlos con precio ajustado
            stmt_comp = select(ValorComponente).where(
                ValorComponente.valor_id == v.id, ValorComponente.activo == True
            )
            comps = (await db.execute(stmt_comp)).scalars().all()

            # Cerrar valor anterior
            v.estado = "cerrado"
            v.vigencia_hasta = fecha_corte
            await db.flush()

            # Crear nuevo valor
            nuevo = Valor(
                obra_social_nro=v.obra_social_nro,
                convenio_id=v.convenio_id,
                nomenclador_id=v.nomenclador_id,
                codigo=v.codigo,
                descripcion=v.descripcion,
                nivel=v.nivel,
                vigencia_desde=body.vigencia_desde,
                vigencia_hasta=None,
                estado="activo",
                observacion=v.observacion,
            )
            db.add(nuevo)
            await db.flush()

            # Clonar componentes ajustando precio fijo
            for c in comps:
                nuevo_vu = None
                if c.galeno_id is None and c.valor_unitario is not None:
                    nuevo_vu = (c.valor_unitario * factor).quantize(Decimal("0.01"))
                else:
                    nuevo_vu = c.valor_unitario
                db.add(ValorComponente(
                    valor_id=nuevo.id,
                    concepto=c.concepto,
                    galeno_id=c.galeno_id,
                    cantidad=c.cantidad,
                    valor_unitario=nuevo_vu,
                    opcional=c.opcional,
                    orden=c.orden,
                    observacion=c.observacion,
                ))
            await db.flush()

            await service.regenerar_historial_por_valores(
                nuevo.id, fecha_corte, db, motivo="valor_fijo_actualizado"
            )
            actualizados += 1
        except Exception as e:
            errores.append({"nomenclador_id": v.nomenclador_id, "motivo": str(e)})

    await db.commit()
    return ActualizacionMasivaResult(actualizados=actualizados, errores=errores)


@router.post("/actualizar_por_codigos", response_model=ActualizacionMasivaResult)
async def actualizar_por_codigos(body: ActualizarPorCodigosIn, db: AsyncSession = Depends(get_db)):
    """Actualiza valores específicos con nuevos precios fijos unitarios."""
    actualizados = 0
    errores = []
    fecha_corte = body.vigencia_desde - datetime.timedelta(days=1)

    for item in body.items:
        try:
            stmt = select(Valor).where(
                Valor.obra_social_nro == body.obra_social_nro,
                Valor.convenio_id == body.convenio_id,
                Valor.nomenclador_id == item.nomenclador_id,
                Valor.estado == "activo",
            )
            anterior = (await db.execute(stmt)).scalar_one_or_none()
            if not anterior:
                errores.append({"nomenclador_id": item.nomenclador_id, "motivo": "Sin valor activo"})
                continue

            stmt_comp = select(ValorComponente).where(
                ValorComponente.valor_id == anterior.id, ValorComponente.activo == True
            )
            comps = (await db.execute(stmt_comp)).scalars().all()

            anterior.estado = "cerrado"
            anterior.vigencia_hasta = fecha_corte
            await db.flush()

            nuevo = Valor(
                obra_social_nro=anterior.obra_social_nro,
                convenio_id=anterior.convenio_id,
                nomenclador_id=anterior.nomenclador_id,
                codigo=anterior.codigo,
                descripcion=anterior.descripcion,
                nivel=item.nuevo_nivel if item.nuevo_nivel is not None else anterior.nivel,
                vigencia_desde=body.vigencia_desde,
                vigencia_hasta=None,
                estado="activo",
                observacion=anterior.observacion,
            )
            db.add(nuevo)
            await db.flush()

            for c in comps:
                db.add(ValorComponente(
                    valor_id=nuevo.id,
                    concepto=c.concepto,
                    galeno_id=c.galeno_id,
                    cantidad=c.cantidad,
                    valor_unitario=item.nuevo_valor_unitario if c.galeno_id is None else c.valor_unitario,
                    opcional=c.opcional,
                    orden=c.orden,
                    observacion=c.observacion,
                ))
            await db.flush()

            await service.regenerar_historial_por_valores(
                nuevo.id, fecha_corte, db, motivo="valor_fijo_actualizado"
            )
            actualizados += 1
        except Exception as e:
            errores.append({"nomenclador_id": item.nomenclador_id, "motivo": str(e)})

    await db.commit()
    return ActualizacionMasivaResult(actualizados=actualizados, errores=errores)


@router.post("/revertir_ultima_actualizacion", response_model=ActualizacionMasivaResult)
async def revertir_ultima_actualizacion(body: RevertirActualizacionIn, db: AsyncSession = Depends(get_db)):
    """
    Elimina (soft) todos los valores con vigencia_desde = fecha_revertir para
    el scope dado y reactiva los valores anteriores.
    """
    stmt = select(Valor).where(
        Valor.obra_social_nro == body.obra_social_nro,
        Valor.convenio_id == body.convenio_id,
        Valor.vigencia_desde == body.vigencia_revertir,
        Valor.estado == "activo",
    )
    valores_a_revertir = (await db.execute(stmt)).scalars().all()

    actualizados = 0
    errores = []

    for v in valores_a_revertir:
        try:
            # Soft-delete del valor a revertir
            v.estado = "cerrado"
            v.vigencia_hasta = body.vigencia_revertir
            await db.flush()

            # Buscar y reactivar el valor inmediatamente anterior
            stmt_prev = select(Valor).where(
                Valor.obra_social_nro == body.obra_social_nro,
                Valor.convenio_id == body.convenio_id,
                Valor.nomenclador_id == v.nomenclador_id,
                Valor.estado == "cerrado",
                Valor.vigencia_hasta == body.vigencia_revertir - datetime.timedelta(days=1),
            )
            anterior = (await db.execute(stmt_prev)).scalar_one_or_none()
            if anterior:
                anterior.estado = "activo"
                anterior.vigencia_hasta = None
                await db.flush()
                # Regenerar historial con el valor anterior
                fecha_corte = body.vigencia_revertir - datetime.timedelta(days=1)
                # Cerrar la fila de historial que apuntaba al valor revertido
                await db.execute(
                    update(HistorialPrecioCodigo)
                    .where(
                        HistorialPrecioCodigo.valores_id == v.id,
                        HistorialPrecioCodigo.vigencia_hasta.is_(None),
                    )
                    .values(vigencia_hasta=fecha_corte)
                )
                await service.regenerar_historial_por_valores(
                    anterior.id, None, db, motivo="carga_inicial"
                )

            actualizados += 1
        except Exception as e:
            errores.append({"nomenclador_id": v.nomenclador_id, "motivo": str(e)})

    await db.commit()
    return ActualizacionMasivaResult(actualizados=actualizados, errores=errores)


# ─── Lookup de precio ─────────────────────────────────────────────────────────

@router.post("/lookup", response_model=LookupPrecioOut)
async def lookup_precio(body: LookupPrecioIn, db: AsyncSession = Depends(get_db)):
    nomenclador_id: Optional[int] = None

    if body.codigo_colegio:
        stmt = select(NomencladorCMC).where(
            NomencladorCMC.codigo == body.codigo_colegio, NomencladorCMC.activo == True
        )
        nom = (await db.execute(stmt)).scalar_one_or_none()
        if not nom:
            raise HTTPException(404, f"Código '{body.codigo_colegio}' no encontrado en el nomenclador")
        nomenclador_id = nom.id
    else:
        stmt = select(Homologador).where(
            Homologador.obra_social_nro == body.obra_social_nro,
            Homologador.codigo_origen == body.codigo_origen,
            Homologador.activo == True,
        )
        hom = (await db.execute(stmt)).scalar_one_or_none()
        if not hom:
            raise HTTPException(
                422, f"Código '{body.codigo_origen}' no homologado para OS {body.obra_social_nro}"
            )
        nomenclador_id = hom.nomenclador_id

    try:
        return await service.lookup_precio(
            nomenclador_id=nomenclador_id,
            obra_social_nro=body.obra_social_nro,
            fecha=body.fecha_practica,
            medico_id=body.medico_id,
            opcionales_activos=body.opcionales_activos,
            db=db,
        )
    except LookupError as e:
        raise HTTPException(e.status_code, e.message)


@router.get("/precio_a_fecha", response_model=LookupPrecioOut)
async def precio_a_fecha(
    codigo: str = Query(...),
    obra_social_nro: int = Query(...),
    fecha: datetime.date = Query(...),
    medico_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(NomencladorCMC).where(
        NomencladorCMC.codigo == codigo, NomencladorCMC.activo == True
    )
    nom = (await db.execute(stmt)).scalar_one_or_none()
    if not nom:
        raise HTTPException(404, f"Código '{codigo}' no encontrado")
    try:
        return await service.lookup_precio(
            nomenclador_id=nom.id,
            obra_social_nro=obra_social_nro,
            fecha=fecha,
            medico_id=medico_id,
            opcionales_activos=[],
            db=db,
        )
    except LookupError as e:
        raise HTTPException(e.status_code, e.message)


# ─── Historial ────────────────────────────────────────────────────────────────

@router.get("/historial", response_model=List[HistorialPrecioOut])
async def get_historial(
    nomenclador_id: Optional[int] = Query(None),
    obra_social_nro: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.obra_social_nro == obra_social_nro,
    )
    if nomenclador_id:
        stmt = stmt.where(HistorialPrecioCodigo.nomenclador_id == nomenclador_id)
    stmt = stmt.order_by(HistorialPrecioCodigo.vigencia_desde)
    result = await db.execute(stmt)
    return result.scalars().all()


# ─── Importar CSV de valores ──────────────────────────────────────────────────

@router.post("/importar_csv", response_model=ImportarCSVResult)
async def importar_valores_csv(
    file: UploadFile = File(...),
    obra_social_nro: int = Query(...),
    convenio_id: int = Query(...),
    vigencia_desde: datetime.date = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    CSV esperado (una fila por componente):
    codigo_colegio,descripcion,valor_unitario,nivel,concepto,galeno_codigo,cantidad

    - Si cantidad=0 y valor_unitario tiene valor → componente fijo.
    - Si cantidad>0 y galeno_codigo tiene valor → componente calculable (busca galeno por codigo).
    - Agrupa múltiples filas con el mismo codigo_colegio en un solo Valor.
    """
    if not await db.get(Convenio, convenio_id):
        raise HTTPException(404, "Convenio no encontrado")

    contenido = await file.read()
    texto = contenido.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(texto))

    # Agrupar filas por codigo_colegio
    grupos: dict = {}
    for i, row in enumerate(reader, start=2):
        codigo = row.get("codigo_colegio", "").strip()
        if not codigo:
            continue
        if codigo not in grupos:
            grupos[codigo] = {
                "descripcion": row.get("descripcion", "").strip() or None,
                "nivel": int(row["nivel"]) if row.get("nivel", "").strip() else None,
                "componentes": [],
                "fila_inicio": i,
            }
        grupos[codigo]["componentes"].append(row)

    procesados = 0
    errores = []

    for codigo, data in grupos.items():
        try:
            # Buscar nomenclador
            stmt_nom = select(NomencladorCMC).where(
                NomencladorCMC.codigo == codigo, NomencladorCMC.activo == True
            )
            nom = (await db.execute(stmt_nom)).scalar_one_or_none()
            if not nom:
                errores.append({"fila": data["fila_inicio"], "motivo": f"Código CMC '{codigo}' no encontrado"})
                continue

            # Cerrar valor activo previo si existe
            stmt_prev = select(Valor).where(
                Valor.obra_social_nro == obra_social_nro,
                Valor.convenio_id == convenio_id,
                Valor.nomenclador_id == nom.id,
                Valor.estado == "activo",
            )
            prev = (await db.execute(stmt_prev)).scalar_one_or_none()
            fecha_corte = vigencia_desde - datetime.timedelta(days=1)
            if prev:
                prev.estado = "cerrado"
                prev.vigencia_hasta = fecha_corte
                await db.flush()

            # Crear nuevo Valor
            nuevo = Valor(
                obra_social_nro=obra_social_nro,
                convenio_id=convenio_id,
                nomenclador_id=nom.id,
                codigo=nom.codigo,
                descripcion=data["descripcion"] or nom.descripcion,
                nivel=data["nivel"],
                vigencia_desde=vigencia_desde,
                vigencia_hasta=None,
                estado="activo",
            )
            db.add(nuevo)
            await db.flush()

            # Crear componentes
            for idx, row in enumerate(data["componentes"]):
                concepto = row.get("concepto", "").strip()
                galeno_codigo = row.get("galeno_codigo", "").strip()
                cantidad_str = row.get("cantidad", "0").strip()
                valor_unitario_str = row.get("valor_unitario", "").strip()

                cantidad = Decimal(cantidad_str) if cantidad_str else Decimal("0")
                valor_unitario = Decimal(valor_unitario_str) if valor_unitario_str else None

                galeno_id = None
                if galeno_codigo and cantidad > 0:
                    stmt_g = select(Galeno).where(
                        Galeno.obra_social_nro == obra_social_nro,
                        Galeno.convenio_id == convenio_id,
                        Galeno.codigo == galeno_codigo,
                        Galeno.vigencia_hasta.is_(None),
                        Galeno.activo == True,
                    )
                    galeno = (await db.execute(stmt_g)).scalar_one_or_none()
                    if galeno:
                        galeno_id = galeno.id
                    else:
                        errores.append({
                            "fila": data["fila_inicio"] + idx,
                            "motivo": f"Galeno '{galeno_codigo}' no encontrado vigente"
                        })
                        continue

                db.add(ValorComponente(
                    valor_id=nuevo.id,
                    concepto=concepto,
                    galeno_id=galeno_id,
                    cantidad=cantidad,
                    valor_unitario=valor_unitario,
                    opcional=False,
                    orden=idx,
                ))
            await db.flush()

            motivo = "valor_fijo_actualizado" if prev else "carga_inicial"
            await service.regenerar_historial_por_valores(nuevo.id, fecha_corte if prev else None, db, motivo=motivo)
            procesados += 1
        except Exception as e:
            errores.append({"fila": data.get("fila_inicio", "?"), "motivo": str(e)})

    await db.commit()
    return ImportarCSVResult(procesados=procesados, errores=errores)
