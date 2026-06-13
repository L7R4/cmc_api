import csv
import datetime
import io
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models.nomenclador_cmc import (
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
    HistorialPrecioOut,
    ImportarCSVResult,
    LookupPrecioIn,
    LookupPrecioOut,
    ReplicarEstructuraIn,
    RevertirActualizacionIn,
    ValorCerrarYCrearIn,
    ValorComponenteOut,
    ValorCreate,
    ValorOut,
    ValorUpdate,
)
from app.modules.nomenclador.service import LookupError, NivelInconsistenteError

router = APIRouter()

_UNIDADES_MAP: dict[str, str] = {
    "Honorarios": "unidades_honorarios",
    "Ayudante":   "unidades_ayudante",
    "Gastos":     "unidades_gastos",
}


# ─── helpers ─────────────────────────────────────────────────────────────────

def _cond_variante_valor(especialidad_id: Optional[int]):
    if especialidad_id is None:
        return Valor.especialidad_id_colegio.is_(None)
    return Valor.especialidad_id_colegio == especialidad_id


async def _buscar_valor_activo(
    db: AsyncSession,
    obra_social_nro: int,
    nomenclador_id: int,
    especialidad_id: Optional[int],
) -> Optional[Valor]:
    """Valor activo de una variante específica (especialidad_id None = base)."""
    stmt = select(Valor).where(
        Valor.obra_social_nro == obra_social_nro,
        Valor.nomenclador_id == nomenclador_id,
        _cond_variante_valor(especialidad_id),
        Valor.estado == "activo",
    )
    return (await db.execute(stmt)).scalars().first()


def _validar_homogeneidad_datos(componentes: list[dict]) -> Optional[str]:
    """
    Versión para componentes ya resueltos como dicts (CSV / replicación).
    Devuelve el mensaje de error o None si la ecuación es válida.
    """
    if not componentes:
        return "El valor requiere al menos un componente"
    con_galeno = [c for c in componentes if c.get("galeno_id") is not None]
    if con_galeno and len(con_galeno) != len(componentes):
        return (
            "Modalidad mixta no permitida: todos los componentes deben ser "
            "calculables (galeno) o todos fijos"
        )
    conceptos = [c["concepto"] for c in componentes]
    repetidos = {c for c in conceptos if conceptos.count(c) > 1}
    if repetidos:
        return f"Concepto repetido: {', '.join(sorted(repetidos))} (máximo uno por concepto)"
    return None


def _resolver_cantidad(
    nom: NomencladorCMC, concepto: str, cantidad: Decimal
) -> Decimal:
    """
    Pre-fill de unidades: si cantidad=0 para un componente calculable, usa las
    unidades por defecto del código (nomenclador nacional). Lanza 422 si no hay.
    """
    if cantidad and cantidad > 0:
        return cantidad
    attr = _UNIDADES_MAP[concepto]
    default = getattr(nom, attr, None)
    if not default:
        raise HTTPException(
            422,
            f"El componente '{concepto}' requiere cantidad explícita o "
            f"que el código tenga '{attr}' configurado",
        )
    return default


async def _crear_valor_con_componentes(
    db: AsyncSession,
    obra_social_nro: int,
    nomenclador_id: int,
    vigencia_desde: datetime.date,
    componentes_in: list,
    descripcion: Optional[str],
    nivel: Optional[int],
    complejidad: Optional[str],
    especialidad_id_colegio: Optional[int],
    observacion: Optional[str],
) -> Valor:
    nom = await db.get(NomencladorCMC, nomenclador_id)
    if not nom:
        raise HTTPException(404, "Código de nomenclador no encontrado")

    valor = Valor(
        obra_social_nro=obra_social_nro,
        nomenclador_id=nomenclador_id,
        codigo=nom.codigo,
        descripcion=descripcion or nom.descripcion,
        nivel=nivel,
        complejidad=complejidad,
        especialidad_id_colegio=especialidad_id_colegio,
        vigencia_desde=vigencia_desde,
        vigencia_hasta=None,
        estado="activo",
        observacion=observacion,
    )
    db.add(valor)
    await db.flush()

    for comp_in in componentes_in:
        cantidad = comp_in.cantidad
        if comp_in.galeno_id is not None:
            galeno = await db.get(Galeno, comp_in.galeno_id)
            if not galeno:
                raise HTTPException(404, f"Galeno {comp_in.galeno_id} no encontrado")
            if not galeno.activo:
                raise HTTPException(
                    409,
                    f"El galeno {comp_in.galeno_id} ('{galeno.nombre}') está inactivo — "
                    f"usá el galeno vigente para '{galeno.codigo}'"
                )
            try:
                service.validar_nivel_galeno(galeno, nivel)
            except NivelInconsistenteError as e:
                raise HTTPException(422, e.message)
            cantidad = _resolver_cantidad(nom, comp_in.concepto, cantidad)

        comp = ValorComponente(
            valor_id=valor.id,
            concepto=comp_in.concepto,
            galeno_id=comp_in.galeno_id,
            cantidad=cantidad,
            valor_unitario=comp_in.valor_unitario,
            opcional=comp_in.opcional,
            orden=comp_in.orden,
            observacion=comp_in.observacion,
        )
        db.add(comp)
    await db.flush()
    return valor


async def _clonar_valor(
    db: AsyncSession,
    origen: Valor,
    vigencia_desde: datetime.date,
    nivel: Optional[int] = None,
    transform_componente=None,
) -> Valor:
    """
    Crea un nuevo Valor copiando metadatos (incluida la variante) y componentes
    del origen. `transform_componente(comp) -> dict` permite ajustar cada
    componente clonado.
    """
    stmt_comp = select(ValorComponente).where(
        ValorComponente.valor_id == origen.id, ValorComponente.activo == True
    )
    comps = (await db.execute(stmt_comp)).scalars().all()

    nuevo = Valor(
        obra_social_nro=origen.obra_social_nro,
        nomenclador_id=origen.nomenclador_id,
        codigo=origen.codigo,
        descripcion=origen.descripcion,
        nivel=nivel if nivel is not None else origen.nivel,
        complejidad=origen.complejidad,
        especialidad_id_colegio=origen.especialidad_id_colegio,
        vigencia_desde=vigencia_desde,
        vigencia_hasta=None,
        estado="activo",
        observacion=origen.observacion,
    )
    db.add(nuevo)
    await db.flush()

    for c in comps:
        datos = {
            "concepto": c.concepto,
            "galeno_id": c.galeno_id,
            "cantidad": c.cantidad,
            "valor_unitario": c.valor_unitario,
            "opcional": c.opcional,
            "orden": c.orden,
            "observacion": c.observacion,
        }
        if transform_componente:
            datos = transform_componente(c) or datos
        db.add(ValorComponente(valor_id=nuevo.id, **datos))
    await db.flush()
    return nuevo


def _cerrar_valor(valor: Valor, fecha_corte: datetime.date) -> None:
    valor.estado = "cerrado"
    valor.vigencia_hasta = fecha_corte


async def _componentes_activos(db: AsyncSession, valor_id: int) -> list:
    stmt = select(ValorComponente).where(
        ValorComponente.valor_id == valor_id, ValorComponente.activo == True
    ).order_by(ValorComponente.orden)
    return (await db.execute(stmt)).scalars().all()


# ─── CRUD Valores ─────────────────────────────────────────────────────────────

@router.get("/", response_model=List[ValorOut])
async def list_valores(
    obra_social_nro: Optional[int] = Query(None),
    codigo: Optional[str] = Query(None),
    nivel: Optional[int] = Query(None),
    especialidad_id_colegio: Optional[int] = Query(None),
    estado: Optional[str] = Query(None),
    vigente_a: Optional[datetime.date] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Valor)
    if obra_social_nro:
        stmt = stmt.where(Valor.obra_social_nro == obra_social_nro)
    if codigo:
        stmt = stmt.where(Valor.codigo == codigo)
    if nivel is not None:
        stmt = stmt.where(Valor.nivel == nivel)
    if especialidad_id_colegio is not None:
        stmt = stmt.where(Valor.especialidad_id_colegio == especialidad_id_colegio)
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
    existente = await _buscar_valor_activo(
        db, body.obra_social_nro, body.nomenclador_id, body.especialidad_id_colegio
    )
    if existente:
        variante = (
            f"variante especialidad {body.especialidad_id_colegio}"
            if body.especialidad_id_colegio is not None else "variante base"
        )
        raise HTTPException(
            409,
            f"Ya existe un valor activo (id {existente.id}) para este código, OS y "
            f"{variante} — use POST /valores_nm/{existente.id}/actualizar",
        )

    valor = await _crear_valor_con_componentes(
        db=db,
        obra_social_nro=body.obra_social_nro,
        nomenclador_id=body.nomenclador_id,
        vigencia_desde=body.vigencia_desde,
        componentes_in=body.componentes,
        descripcion=body.descripcion,
        nivel=body.nivel,
        complejidad=body.complejidad,
        especialidad_id_colegio=body.especialidad_id_colegio,
        observacion=body.observacion,
    )

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
    cambios = body.model_dump(exclude_none=True)
    if "nivel" in cambios and cambios["nivel"] != obj.nivel:
        # No permitir desincronizar el nivel respecto de galenos nivelados ya vinculados
        for comp in await _componentes_activos(db, obj.id):
            if comp.galeno_id is None:
                continue
            galeno = await db.get(Galeno, comp.galeno_id)
            if galeno and galeno.nivel is not None and galeno.nivel != cambios["nivel"]:
                raise HTTPException(
                    422,
                    f"No se puede cambiar a nivel {cambios['nivel']}: el componente "
                    f"{comp.id} usa el galeno '{galeno.codigo}' nivel {galeno.nivel}. "
                    f"Use /actualizar con la nueva ecuación.",
                )
    for field, value in cambios.items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.post("/{id}/actualizar", response_model=ValorOut)
async def actualizar_valor(
    id: int, body: ValorCerrarYCrearIn, db: AsyncSession = Depends(get_db)
):
    """
    Cierra el valor actual y crea uno nuevo con la nueva fórmula de componentes.
    La variante (especialidad_id_colegio) se conserva: para crear otra variante
    del mismo código usar POST /valores_nm/.
    """
    anterior = await db.get(Valor, id)
    if not anterior:
        raise HTTPException(404, "Valor no encontrado")
    if anterior.estado == "cerrado":
        raise HTTPException(409, "El valor ya está cerrado")

    fecha_corte = body.vigencia_desde - datetime.timedelta(days=1)
    _cerrar_valor(anterior, fecha_corte)
    await db.flush()

    nuevo = await _crear_valor_con_componentes(
        db=db,
        obra_social_nro=anterior.obra_social_nro,
        nomenclador_id=anterior.nomenclador_id,
        vigencia_desde=body.vigencia_desde,
        componentes_in=body.componentes,
        descripcion=body.descripcion or anterior.descripcion,
        nivel=body.nivel if body.nivel is not None else anterior.nivel,
        complejidad=body.complejidad if body.complejidad is not None else anterior.complejidad,
        especialidad_id_colegio=anterior.especialidad_id_colegio,
        observacion=body.observacion or anterior.observacion,
    )

    await service.regenerar_historial_por_valores(
        nuevo.id, fecha_corte, db, motivo="valores_estructura"
    )

    await db.commit()
    await db.refresh(nuevo)
    return nuevo


@router.delete("/{id}", status_code=204)
async def delete_valor(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Valor, id)
    if not obj:
        raise HTTPException(404, "Valor no encontrado")
    # Baja con efecto inmediato: vigencia_hasta = ayer hace que el lookup de hoy
    # ya no devuelva esta variante
    ayer = datetime.date.today() - datetime.timedelta(days=1)
    _cerrar_valor(obj, ayer)
    await service.cerrar_historial_de_valor(obj.id, ayer, db)
    await db.commit()


@router.get("/{id}/componentes", response_model=List[ValorComponenteOut])
async def list_componentes(id: int, db: AsyncSession = Depends(get_db)):
    if not await db.get(Valor, id):
        raise HTTPException(404, "Valor no encontrado")
    return await _componentes_activos(db, id)


# ─── Replicación de estructura ────────────────────────────────────────────────

@router.post("/replicar_estructura", response_model=ActualizacionMasivaResult)
async def replicar_estructura(body: ReplicarEstructuraIn, db: AsyncSession = Depends(get_db)):
    """
    Replica la ecuación de componentes de un Valor origen en N códigos destino
    de la misma OS y la MISMA VARIANTE (se copia especialidad_id_colegio del
    origen). Por cada destino: cierra el valor activo previo de esa variante,
    crea el nuevo con los componentes clonados y regenera historial.

    - Componentes con galeno nivelado se remapean al galeno del nivel destino.
    - Con usar_unidades_nomenclador=True (default) la cantidad de cada componente
      calculable se re-resuelve desde las unidades del código destino.
    """
    origen = await db.get(Valor, body.origen_valor_id)
    if not origen:
        raise HTTPException(404, "Valor origen no encontrado")

    comps_origen = await _componentes_activos(db, origen.id)
    if not comps_origen:
        raise HTTPException(422, "El valor origen no tiene componentes activos")

    fecha_corte = body.vigencia_desde - datetime.timedelta(days=1)
    actualizados = 0
    errores = []

    for destino in body.destinos:
        try:
            nom_dest = await db.get(NomencladorCMC, destino.nomenclador_id)
            if not nom_dest:
                errores.append({"nomenclador_id": destino.nomenclador_id,
                                "motivo": "Código destino no encontrado"})
                continue

            nivel_destino = destino.nivel if destino.nivel is not None else origen.nivel

            # Armar componentes destino: remapear galenos nivelados + re-resolver unidades
            componentes_destino = []
            error_comp = None
            for c in comps_origen:
                galeno_id = c.galeno_id
                cantidad = c.cantidad
                if c.galeno_id is not None:
                    galeno = await db.get(Galeno, c.galeno_id)
                    if galeno and galeno.nivel is not None and galeno.nivel != nivel_destino:
                        remapeado = await service.buscar_galeno_vigente(
                            db, origen.obra_social_nro, galeno.codigo, nivel_destino
                        )
                        if not remapeado:
                            error_comp = (
                                f"No hay galeno vigente '{galeno.codigo}' "
                                f"nivel {nivel_destino} para remapear"
                            )
                            break
                        galeno_id = remapeado.id
                    if body.usar_unidades_nomenclador:
                        attr = _UNIDADES_MAP[c.concepto]
                        default = getattr(nom_dest, attr, None)
                        if not default:
                            error_comp = (
                                f"El código destino no tiene '{attr}' para "
                                f"re-resolver la cantidad del componente '{c.concepto}'"
                            )
                            break
                        cantidad = default
                componentes_destino.append({
                    "concepto": c.concepto,
                    "galeno_id": galeno_id,
                    "cantidad": cantidad,
                    "valor_unitario": c.valor_unitario,
                    "opcional": c.opcional,
                    "orden": c.orden,
                    "observacion": c.observacion,
                })

            if error_comp is None:
                error_comp = _validar_homogeneidad_datos(componentes_destino)
            if error_comp:
                errores.append({"nomenclador_id": destino.nomenclador_id, "motivo": error_comp})
                continue

            # Cerrar valor activo previo del destino (misma variante que el origen)
            previo = await _buscar_valor_activo(
                db, origen.obra_social_nro, destino.nomenclador_id,
                origen.especialidad_id_colegio,
            )
            if previo:
                if previo.id == origen.id:
                    errores.append({"nomenclador_id": destino.nomenclador_id,
                                    "motivo": "El destino es el mismo valor origen"})
                    continue
                _cerrar_valor(previo, fecha_corte)
                await db.flush()

            nuevo = Valor(
                obra_social_nro=origen.obra_social_nro,
                nomenclador_id=destino.nomenclador_id,
                codigo=nom_dest.codigo,
                descripcion=nom_dest.descripcion,
                nivel=nivel_destino,
                complejidad=None,
                especialidad_id_colegio=origen.especialidad_id_colegio,
                vigencia_desde=body.vigencia_desde,
                vigencia_hasta=None,
                estado="activo",
            )
            db.add(nuevo)
            await db.flush()
            for datos in componentes_destino:
                db.add(ValorComponente(valor_id=nuevo.id, **datos))
            await db.flush()

            await service.regenerar_historial_por_valores(
                nuevo.id, fecha_corte if previo else None, db, motivo="replicacion"
            )
            actualizados += 1
        except Exception as e:
            errores.append({"nomenclador_id": destino.nomenclador_id, "motivo": str(e)})

    await db.commit()
    return ActualizacionMasivaResult(actualizados=actualizados, errores=errores)


# ─── Actualizaciones masivas ──────────────────────────────────────────────────

@router.post("/actualizar_porcentaje", response_model=ActualizacionMasivaResult)
async def actualizar_porcentaje(body: ActualizarPorcentajeIn, db: AsyncSession = Depends(get_db)):
    """
    Aplica un porcentaje lineal a los valores de modalidad FIJA del scope
    (todas las variantes). Los valores calculables (galeno × unidades) quedan
    en `omitidos`: su precio se actualiza vía /galenos/actualizar_precio[_masivo].
    """
    stmt = select(Valor).where(
        Valor.obra_social_nro == body.obra_social_nro,
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
    omitidos = 0
    errores = []

    def _ajustar_fijo(c: ValorComponente) -> dict:
        nuevo_vu = c.valor_unitario
        if c.valor_unitario is not None:
            nuevo_vu = (c.valor_unitario * factor).quantize(Decimal("0.01"))
        return {
            "concepto": c.concepto,
            "galeno_id": c.galeno_id,
            "cantidad": c.cantidad,
            "valor_unitario": nuevo_vu,
            "opcional": c.opcional,
            "orden": c.orden,
            "observacion": c.observacion,
        }

    for v in valores:
        try:
            comps = await _componentes_activos(db, v.id)
            if not comps or service.modalidad_de(comps) == "galeno":
                omitidos += 1
                continue

            _cerrar_valor(v, fecha_corte)
            await db.flush()
            nuevo = await _clonar_valor(
                db, v, body.vigencia_desde, transform_componente=_ajustar_fijo
            )
            await service.regenerar_historial_por_valores(
                nuevo.id, fecha_corte, db, motivo="valor_fijo_actualizado"
            )
            actualizados += 1
        except Exception as e:
            errores.append({"nomenclador_id": v.nomenclador_id, "motivo": str(e)})

    await db.commit()
    return ActualizacionMasivaResult(actualizados=actualizados, errores=errores, omitidos=omitidos)


@router.post("/actualizar_por_codigos", response_model=ActualizacionMasivaResult)
async def actualizar_por_codigos(body: ActualizarPorCodigosIn, db: AsyncSession = Depends(get_db)):
    """
    Actualiza valores específicos con nuevos precios fijos unitarios.
    Opera sobre la VARIANTE BASE de cada código (para variantes por especialidad
    usar /valores_nm/{id}/actualizar). Rechaza valores calculables.
    """
    actualizados = 0
    errores = []
    fecha_corte = body.vigencia_desde - datetime.timedelta(days=1)

    for item in body.items:
        try:
            anterior = await _buscar_valor_activo(
                db, body.obra_social_nro, item.nomenclador_id, None
            )
            if not anterior:
                errores.append({"nomenclador_id": item.nomenclador_id,
                                "motivo": "Sin valor activo (variante base)"})
                continue

            comps = await _componentes_activos(db, anterior.id)
            if comps and service.modalidad_de(comps) == "galeno":
                errores.append({
                    "nomenclador_id": item.nomenclador_id,
                    "motivo": "Valor calculable por galeno; actualice el galeno, no el valor",
                })
                continue

            def _set_precio_fijo(c: ValorComponente) -> dict:
                return {
                    "concepto": c.concepto,
                    "galeno_id": c.galeno_id,
                    "cantidad": c.cantidad,
                    "valor_unitario": item.nuevo_valor_unitario,
                    "opcional": c.opcional,
                    "orden": c.orden,
                    "observacion": c.observacion,
                }

            _cerrar_valor(anterior, fecha_corte)
            await db.flush()
            nuevo = await _clonar_valor(
                db, anterior, body.vigencia_desde,
                nivel=item.nuevo_nivel,
                transform_componente=_set_precio_fijo,
            )
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
    el scope dado y reactiva los valores anteriores de la misma variante.
    Los componentes del valor reactivado se repuntan al galeno vigente
    (las rotaciones de galeno posteriores no se deshacen).
    """
    stmt = select(Valor).where(
        Valor.obra_social_nro == body.obra_social_nro,
        Valor.vigencia_desde == body.vigencia_revertir,
        Valor.estado == "activo",
    )
    valores_a_revertir = (await db.execute(stmt)).scalars().all()

    actualizados = 0
    errores = []
    fecha_corte = body.vigencia_revertir - datetime.timedelta(days=1)

    for v in valores_a_revertir:
        try:
            # Buscar el valor inmediatamente anterior de la MISMA variante
            stmt_prev = (
                select(Valor)
                .where(
                    Valor.obra_social_nro == body.obra_social_nro,
                    Valor.nomenclador_id == v.nomenclador_id,
                    _cond_variante_valor(v.especialidad_id_colegio),
                    Valor.estado == "cerrado",
                    Valor.id != v.id,
                    Valor.vigencia_desde < body.vigencia_revertir,
                )
                .order_by(Valor.vigencia_desde.desc())
                .limit(1)
            )
            anterior = (await db.execute(stmt_prev)).scalars().first()

            # Resolver los repuntes de galeno ANTES de mutar nada (todo o nada)
            repuntes: list[tuple[ValorComponente, int]] = []
            if anterior:
                for comp in await _componentes_activos(db, anterior.id):
                    if comp.galeno_id is None:
                        continue
                    galeno = await db.get(Galeno, comp.galeno_id)
                    if galeno is None:
                        raise ValueError(f"Componente {comp.id} apunta a un galeno inexistente")
                    if galeno.vigencia_hasta is None and galeno.activo:
                        continue  # ya vigente
                    vigente = await service.buscar_galeno_vigente(
                        db, anterior.obra_social_nro, galeno.codigo, galeno.nivel
                    )
                    if not vigente:
                        raise ValueError(
                            f"No hay galeno vigente '{galeno.codigo}' "
                            f"nivel {galeno.nivel} para reactivar el valor anterior"
                        )
                    repuntes.append((comp, vigente.id))

            _cerrar_valor(v, body.vigencia_revertir)
            await db.flush()

            if anterior:
                anterior.estado = "activo"
                anterior.vigencia_hasta = None
                for comp, galeno_vigente_id in repuntes:
                    comp.galeno_id = galeno_vigente_id
                await db.flush()
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
                    anterior.id, None, db, motivo="reversion"
                )
            else:
                # No hay valor anterior: la variante queda sin precio desde la reversión
                await service.cerrar_historial_de_valor(v.id, fecha_corte, db)

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


# ─── Historial ────────────────────────────────────────────────────────────────

@router.get("/historial", response_model=List[HistorialPrecioOut])
async def get_historial(
    nomenclador_id: Optional[int] = Query(None),
    obra_social_nro: int = Query(...),
    especialidad_id_colegio: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(HistorialPrecioCodigo).where(
        HistorialPrecioCodigo.obra_social_nro == obra_social_nro,
    )
    if nomenclador_id:
        stmt = stmt.where(HistorialPrecioCodigo.nomenclador_id == nomenclador_id)
    if especialidad_id_colegio is not None:
        stmt = stmt.where(
            HistorialPrecioCodigo.especialidad_id_colegio == especialidad_id_colegio
        )
    stmt = stmt.order_by(HistorialPrecioCodigo.vigencia_desde)
    result = await db.execute(stmt)
    return result.scalars().all()


# ─── Importar CSV de valores ──────────────────────────────────────────────────

@router.post("/importar_csv", response_model=ImportarCSVResult)
async def importar_valores_csv(
    file: UploadFile = File(...),
    obra_social_nro: int = Query(...),
    vigencia_desde: datetime.date = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    CSV esperado (una fila por componente):
    codigo_colegio,descripcion,valor_unitario,nivel,especialidad,concepto,galeno_codigo,cantidad

    - `especialidad` (opcional): variante del valor; vacío = variante base.
    - Cada (codigo_colegio, especialidad) agrupa sus filas en un solo Valor.
    - Modalidad homogénea por grupo: todas las filas con galeno_codigo o ninguna.
    - Si galeno_codigo tiene valor → el galeno se resuelve por (OS, galeno_codigo,
      nivel del valor) con fallback al galeno sin nivel.
    - Si cantidad=0 con galeno → unidades por defecto del código.
    - Si algún componente del grupo falla, NO se crea el Valor (todo o nada).
    """
    contenido = await file.read()
    texto = contenido.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(texto))

    # Agrupar filas por (codigo_colegio, especialidad)
    grupos: dict = {}
    for i, row in enumerate(reader, start=2):
        codigo = row.get("codigo_colegio", "").strip()
        if not codigo:
            continue
        esp_str = row.get("especialidad", "").strip()
        especialidad = int(esp_str) if esp_str else None
        clave = (codigo, especialidad)
        if clave not in grupos:
            grupos[clave] = {
                "descripcion": row.get("descripcion", "").strip() or None,
                "nivel": int(row["nivel"]) if row.get("nivel", "").strip() else None,
                "especialidad": especialidad,
                "componentes": [],
                "fila_inicio": i,
            }
        grupos[clave]["componentes"].append(row)

    procesados = 0
    errores = []

    for (codigo, especialidad), data in grupos.items():
        try:
            stmt_nom = select(NomencladorCMC).where(
                NomencladorCMC.codigo == codigo, NomencladorCMC.activo == True
            )
            nom = (await db.execute(stmt_nom)).scalar_one_or_none()
            if not nom:
                errores.append({"fila": data["fila_inicio"], "motivo": f"Código CMC '{codigo}' no encontrado"})
                continue

            nivel_valor = data["nivel"]

            # Resolver TODOS los componentes antes de tocar nada (todo o nada)
            componentes_resueltos = []
            error_grupo = None
            for idx, row in enumerate(data["componentes"]):
                concepto = row.get("concepto", "").strip()
                galeno_codigo = row.get("galeno_codigo", "").strip()
                cantidad_str = row.get("cantidad", "0").strip()
                valor_unitario_str = row.get("valor_unitario", "").strip()

                cantidad = Decimal(cantidad_str) if cantidad_str else Decimal("0")
                valor_unitario = Decimal(valor_unitario_str) if valor_unitario_str else None

                galeno_id = None
                if galeno_codigo:
                    galeno = await service.buscar_galeno_vigente(
                        db, obra_social_nro, galeno_codigo, nivel_valor
                    )
                    if not galeno and nivel_valor is not None:
                        # Fallback: galeno sin nivel
                        galeno = await service.buscar_galeno_vigente(
                            db, obra_social_nro, galeno_codigo, None
                        )
                    if not galeno:
                        error_grupo = {
                            "fila": data["fila_inicio"] + idx,
                            "motivo": (
                                f"Galeno '{galeno_codigo}' no encontrado vigente "
                                f"(nivel {nivel_valor} ni sin nivel)"
                            ),
                        }
                        break
                    galeno_id = galeno.id
                    if cantidad == 0:
                        attr = _UNIDADES_MAP.get(concepto)
                        cantidad = (attr and getattr(nom, attr, None)) or Decimal("0")
                        if cantidad == 0:
                            error_grupo = {
                                "fila": data["fila_inicio"] + idx,
                                "motivo": (
                                    f"Componente '{concepto}' tiene galeno pero sin cantidad "
                                    f"y el código no tiene unidades por defecto para ese concepto"
                                ),
                            }
                            break

                componentes_resueltos.append({
                    "concepto": concepto,
                    "galeno_id": galeno_id,
                    "cantidad": cantidad,
                    "valor_unitario": valor_unitario,
                    "opcional": False,
                    "orden": idx,
                })

            if error_grupo is None:
                motivo_invalido = _validar_homogeneidad_datos(componentes_resueltos)
                if motivo_invalido:
                    error_grupo = {"fila": data["fila_inicio"], "motivo": motivo_invalido}
            if error_grupo:
                errores.append(error_grupo)
                continue

            # Cerrar valor activo previo de la misma variante si existe
            prev = await _buscar_valor_activo(db, obra_social_nro, nom.id, especialidad)
            fecha_corte = vigencia_desde - datetime.timedelta(days=1)
            if prev:
                _cerrar_valor(prev, fecha_corte)
                await db.flush()

            nuevo = Valor(
                obra_social_nro=obra_social_nro,
                nomenclador_id=nom.id,
                codigo=nom.codigo,
                descripcion=data["descripcion"] or nom.descripcion,
                nivel=nivel_valor,
                complejidad=prev.complejidad if prev else None,
                especialidad_id_colegio=especialidad,
                vigencia_desde=vigencia_desde,
                vigencia_hasta=None,
                estado="activo",
            )
            db.add(nuevo)
            await db.flush()
            for datos in componentes_resueltos:
                db.add(ValorComponente(valor_id=nuevo.id, **datos))
            await db.flush()

            motivo = "valor_fijo_actualizado" if prev else "carga_inicial"
            await service.regenerar_historial_por_valores(
                nuevo.id, fecha_corte if prev else None, db, motivo=motivo
            )
            procesados += 1
        except Exception as e:
            errores.append({"fila": data.get("fila_inicio", "?"), "motivo": str(e)})

    await db.commit()
    return ImportarCSVResult(procesados=procesados, errores=errores)
