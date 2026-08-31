import csv
import datetime
import io
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.uploads import leer_texto
from app.db.database import get_db
from app.db.models.catalogs import ObrasSociales
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
    GenerarValoresNNIn,
    GenerarValoresNNResult,
    HistorialPrecioOut,
    ImportarCSVResult,
    LookupPrecioIn,
    LookupPrecioOut,
    Origen,
    ReplicarEstructuraIn,
    ReplicarObrasSocialesIn,
    RevertirActualizacionIn,
    ValorCerrarYCrearIn,
    ValorComponenteOut,
    ValorCreate,
    ValorOut,
    ValorUpdate,
    validar_reglas_origen,
)
from app.modules.nomenclador.service import LookupError, NivelInconsistenteError

router = APIRouter()

_UNIDADES_MAP: dict[str, str] = {
    "Honorarios": "unidades_honorarios",
    "Ayudante":   "unidades_ayudante",
    "Gastos":     "unidades_gastos",
}

# Conceptos generados en 0 para un valor "por presupuesto"
_CONCEPTOS_PRESUPUESTO = ("Honorarios", "Gastos", "Ayudante")


def _componentes_presupuesto_cero() -> list[dict]:
    """Los 3 componentes estándar como fijos en 0, para un Valor por presupuesto.

    El precio_total resultante es 0; el monto real lo informa la OS al facturar.
    """
    return [
        {
            "concepto": concepto,
            "galeno_id": None,
            "cantidad": Decimal("0"),
            "valor_unitario": Decimal("0"),            "orden": orden,
            "observacion": None,
        }
        for orden, concepto in enumerate(_CONCEPTOS_PRESUPUESTO)
    ]


# ─── helpers ─────────────────────────────────────────────────────────────────

async def _valores_out(db: AsyncSession, valores: list[Valor]) -> list[ValorOut]:
    """ValorOut con `descripcion_efectiva` resuelta.

    `Valor.descripcion` es el override de la OS y normalmente viene NULL (hereda del
    catálogo), así que el ABM necesita el texto ya resuelto para mostrar. Se traen las
    descripciones del catálogo en un solo query, no una por fila.
    """
    if not valores:
        return []
    ids = {v.nomenclador_id for v in valores}
    catalogo = {
        nid: desc
        for nid, desc in (await db.execute(
            select(NomencladorCMC.id, NomencladorCMC.descripcion)
            .where(NomencladorCMC.id.in_(ids))
        )).all()
    }
    salida = []
    for v in valores:
        out = ValorOut.model_validate(v)
        out.descripcion_efectiva = (
            v.descripcion if (v.descripcion or "").strip() else (catalogo.get(v.nomenclador_id) or "")
        )
        salida.append(out)
    return salida


async def _valor_out(db: AsyncSession, valor: Valor) -> ValorOut:
    return (await _valores_out(db, [valor]))[0]


def _cond_variante_valor(origen: str, especialidad_id: Optional[int]):
    cond = Valor.origen == origen
    if especialidad_id is None:
        return cond & Valor.especialidad_id_colegio.is_(None)
    return cond & (Valor.especialidad_id_colegio == especialidad_id)


async def _buscar_valor_activo(
    db: AsyncSession,
    obra_social_nro: int,
    nomenclador_id: int,
    origen: str,
    especialidad_id: Optional[int],
) -> Optional[Valor]:
    """Valor activo de una variante específica (origen + especialidad; None = sin especialidad)."""
    stmt = select(Valor).where(
        Valor.obra_social_nro == obra_social_nro,
        Valor.nomenclador_id == nomenclador_id,
        _cond_variante_valor(origen, especialidad_id),
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


def _completar_tres_componentes(componentes: list[dict]) -> list[dict]:
    """Garantiza que el Valor tenga siempre los 3 conceptos (Honorarios, Gastos,
    Ayudante). Los faltantes se crean en 0 respetando la modalidad del grupo:
    - galeno (algún componente con galeno_id): el faltante usa ese galeno con cantidad 0.
    - fijo: el faltante va con valor_unitario 0.
    El subtotal de un faltante es 0, así que no altera el precio. Asume que la lista
    ya pasó `_validar_homogeneidad_datos` (modalidad homogénea, sin repetidos)."""
    presentes = {c["concepto"] for c in componentes}
    galeno_relleno = next((c["galeno_id"] for c in componentes if c.get("galeno_id")), None)
    completos = list(componentes)
    for orden, concepto in enumerate(_CONCEPTOS_PRESUPUESTO):  # Honorarios, Gastos, Ayudante
        if concepto in presentes:
            continue
        completos.append({
            "concepto": concepto,
            "galeno_id": galeno_relleno,
            "cantidad": Decimal("0"),
            "valor_unitario": None if galeno_relleno is not None else Decimal("0"),            "orden": 90 + orden,
        })
    return completos


def _resolver_cantidad(
    nom: NomencladorCMC, galeno: Optional[Galeno], concepto: str, cantidad: Decimal
) -> Decimal:
    """
    Pre-fill de unidades para un componente calculable con cantidad=0.
    Precedencia: cantidad explícita > unidad-plantilla del galeno (por OS) >
    unidades por defecto del código (nomenclador nacional). Lanza 422 si ninguna existe.
    """
    if cantidad and cantidad > 0:
        return cantidad
    attr = _UNIDADES_MAP[concepto]
    galeno_default = getattr(galeno, attr, None) if galeno is not None else None
    if galeno_default:
        return galeno_default
    default = getattr(nom, attr, None)
    if not default:
        raise HTTPException(
            422,
            f"El componente '{concepto}' requiere cantidad explícita, o que el galeno "
            f"o el código tengan '{attr}' configurado",
        )
    return default


async def _crear_valor_con_componentes(
    db: AsyncSession,
    obra_social_nro: int,
    nomenclador_id: int,
    origen: str,
    vigencia_desde: datetime.date,
    componentes_in: list,
    descripcion: Optional[str],
    nivel: Optional[int],
    complejidad: Optional[str],
    especialidad_id_colegio: Optional[int],
    observacion: Optional[str],
    por_presupuesto: bool = False,
    cantidad_ayudantes: Optional[int] = None,
    categoria: Optional[str] = None,
    requiere_autorizacion: Optional[bool] = None,
) -> Valor:
    nom = await db.get(NomencladorCMC, nomenclador_id)
    if not nom:
        raise HTTPException(404, "Código de nomenclador no encontrado")

    valor = Valor(
        obra_social_nro=obra_social_nro,
        nomenclador_id=nomenclador_id,
        origen=origen,
        codigo=nom.codigo,
        # Sin copia con avidez: NULL = hereda del catálogo (service.descripcion_efectiva).
        # Copiarla haría que corregir el catálogo nunca propague.
        descripcion=descripcion or None,
        nivel=nivel,
        complejidad=complejidad,
        categoria=categoria,
        requiere_autorizacion=requiere_autorizacion,
        especialidad_id_colegio=especialidad_id_colegio,
        por_presupuesto=por_presupuesto,
        cantidad_ayudantes=cantidad_ayudantes,
        vigencia_desde=vigencia_desde,
        vigencia_hasta=None,
        estado="activo",
        observacion=observacion,
    )
    db.add(valor)
    await db.flush()

    # Por presupuesto: se ignora la ecuación entrante y se guardan los 3 conceptos en 0
    if por_presupuesto:
        for datos in _componentes_presupuesto_cero():
            db.add(ValorComponente(valor_id=valor.id, **datos))
        await db.flush()
        return valor

    presentes: set[str] = set()
    galeno_relleno: Optional[int] = None
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
            cantidad = _resolver_cantidad(nom, galeno, comp_in.concepto, cantidad)
            if galeno_relleno is None:
                galeno_relleno = comp_in.galeno_id

        comp = ValorComponente(
            valor_id=valor.id,
            concepto=comp_in.concepto,
            galeno_id=comp_in.galeno_id,
            cantidad=cantidad,
            valor_unitario=comp_in.valor_unitario,            orden=comp_in.orden,
            observacion=comp_in.observacion,
        )
        db.add(comp)
        presentes.add(comp_in.concepto)

    # Todo Valor lleva siempre los 3 conceptos; los faltantes en 0 según la modalidad.
    for orden, concepto in enumerate(_CONCEPTOS_PRESUPUESTO):  # Honorarios, Gastos, Ayudante
        if concepto in presentes:
            continue
        db.add(ValorComponente(
            valor_id=valor.id,
            concepto=concepto,
            galeno_id=galeno_relleno,
            cantidad=Decimal("0"),
            valor_unitario=None if galeno_relleno is not None else Decimal("0"),            orden=90 + orden,
        ))
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
        origen=origen.origen,
        codigo=origen.codigo,
        descripcion=origen.descripcion,
        nivel=nivel if nivel is not None else origen.nivel,
        complejidad=origen.complejidad,
        categoria=origen.categoria,
        requiere_autorizacion=origen.requiere_autorizacion,
        especialidad_id_colegio=origen.especialidad_id_colegio,
        cantidad_ayudantes=origen.cantidad_ayudantes,
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
            "valor_unitario": c.valor_unitario,            "orden": c.orden,
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
    return await _valores_out(db, list(result.scalars().all()))


@router.get("/por_vigencia")
async def contar_valores_por_vigencia(
    obra_social_nro: int = Query(...),
    vigencia_desde: datetime.date = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Cantidad de valores cargados para una OS en una vigencia_desde exacta.
    Se usa para impedir cargar dos veces los mismos precios en la misma vigencia."""
    stmt = (
        select(func.count())
        .select_from(Valor)
        .where(
            Valor.obra_social_nro == obra_social_nro,
            Valor.vigencia_desde == vigencia_desde,
        )
    )
    cantidad = (await db.execute(stmt)).scalar_one()
    return {
        "obra_social_nro": obra_social_nro,
        "vigencia_desde": vigencia_desde,
        "cantidad": cantidad,
    }


@router.get("/codigos_por_vigencia")
async def codigos_por_vigencia(
    obra_social_nro: int = Query(...),
    vigencia_desde: datetime.date = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Códigos ya cargados para una OS en una vigencia_desde exacta.
    Permite cargar una misma vigencia por partes (varias hojas) sin repetir códigos:
    el importador omite los que ya están cargados en esa vigencia."""
    stmt = (
        select(Valor.codigo)
        .where(
            Valor.obra_social_nro == obra_social_nro,
            Valor.vigencia_desde == vigencia_desde,
        )
        .distinct()
    )
    codigos = (await db.execute(stmt)).scalars().all()
    return {
        "obra_social_nro": obra_social_nro,
        "vigencia_desde": vigencia_desde,
        "codigos": list(codigos),
    }


@router.get("/vigencias")
async def listar_vigencias_cargadas(
    obra_social_nro: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Vigencias (vigencia_desde) con valores cargados para una OS, con su cantidad.
    Alimenta el selector del modal de eliminación."""
    stmt = (
        select(Valor.vigencia_desde, func.count().label("cantidad"))
        .where(Valor.obra_social_nro == obra_social_nro)
        .group_by(Valor.vigencia_desde)
        .order_by(Valor.vigencia_desde.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [{"vigencia_desde": r[0], "cantidad": r[1]} for r in rows]


@router.delete("/por_vigencia")
async def eliminar_valores_por_vigencia(
    obra_social_nro: int = Query(...),
    vigencia_desde: datetime.date = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Elimina TODOS los valores (con sus componentes e historial de precios) de una
    obra social cargados con una vigencia_desde exacta, y **deja a cada código
    cotizando el precio que tenía antes de esa carga**. Pensado para revertir una
    carga equivocada antes de volver a cargar.

    Es la versión dura de `/revertir_ultima_actualizacion`: aquella cierra, ésta
    borra. Lo que ambas comparten —y lo que esta ruta no hacía— es que deshacer una
    carga no es sólo sacarla: es devolver el estado anterior.

    Dos cosas que corrige respecto de la versión previa, las dos aprendidas de un
    incidente real en la obra social 62 (agosto 2026):

    1. **Sólo borra el historial de los valores que elimina.** Antes la condición
       era `valores_id IN (ids) OR (obra_social_nro = X AND vigencia_desde = Y)`, y
       esa segunda mitad no miraba `ids`: se llevaba puestas todas las filas de
       historial de la obra social con esa vigencia, **incluidas las de valores que
       no se estaban eliminando**. El arrastre de una rotación de galeno escribe
       filas con exactamente esa `vigencia_desde`: rotar los galenos de la OS 62 al
       2026-08-01 generó 1.907 filas NN, y un DELETE de una importación NNE del día
       siguiente se las llevó. Los valores NN quedaron activos y sin ninguna fila de
       historial abierta, o sea sin cotizar, durante 26 días.

    2. **Reactiva el valor anterior de cada variante.** Borrar la carga nueva no
       reabre lo que esa carga había cerrado, así que la variante quedaba sin precio
       vigente. Ahora se busca el valor inmediatamente anterior de la misma variante,
       se lo reactiva y se le regenera el historial con motivo `reversion`, igual que
       en `/revertir_ultima_actualizacion`. Sus componentes se repuntan al galeno
       vigente, porque una rotación posterior pudo haber cerrado el que usaban.

    Si una variante no tenía valor anterior, queda sin precio — que es lo correcto:
    antes de esa carga tampoco lo tenía.
    """
    valores = (
        await db.execute(
            select(Valor).where(
                Valor.obra_social_nro == obra_social_nro,
                Valor.vigencia_desde == vigencia_desde,
            )
        )
    ).scalars().all()

    if not valores:
        return {"eliminados": 0, "reactivados": 0, "errores": []}

    ids = [v.id for v in valores]

    # Se resuelve TODO lo que hay que reactivar antes de borrar nada: después del
    # DELETE ya no se puede saber qué variante era cada valor eliminado.
    reactivaciones: list[tuple[Valor, list[tuple[ValorComponente, int]]]] = []
    errores: list[dict] = []
    for v in valores:
        try:
            anterior = (
                await db.execute(
                    select(Valor)
                    .where(
                        Valor.obra_social_nro == obra_social_nro,
                        Valor.nomenclador_id == v.nomenclador_id,
                        _cond_variante_valor(v.origen, v.especialidad_id_colegio),
                        Valor.estado == "cerrado",
                        Valor.id.not_in(ids),
                        Valor.vigencia_desde < vigencia_desde,
                    )
                    .order_by(Valor.vigencia_desde.desc())
                    .limit(1)
                )
            ).scalars().first()
            if anterior is None:
                continue

            # Repuntes de galeno resueltos antes de mutar: si alguno no se puede
            # resolver, esta variante no se reactiva y se informa, pero el borrado
            # del resto sigue adelante.
            repuntes: list[tuple[ValorComponente, int]] = []
            for comp in await _componentes_activos(db, anterior.id):
                if comp.galeno_id is None:
                    continue
                galeno = await db.get(Galeno, comp.galeno_id)
                if galeno is None:
                    raise ValueError(f"Componente {comp.id} apunta a un galeno inexistente")
                if galeno.vigencia_hasta is None and galeno.activo:
                    continue
                vigente = await service.buscar_galeno_vigente(
                    db, anterior.obra_social_nro, galeno.codigo, galeno.nivel
                )
                if not vigente:
                    raise ValueError(
                        f"No hay galeno vigente '{galeno.codigo}' nivel {galeno.nivel} "
                        f"para reactivar el valor anterior"
                    )
                repuntes.append((comp, vigente.id))
            reactivaciones.append((anterior, repuntes))
        except Exception as e:  # noqa: BLE001 — se informa por variante, no corta
            errores.append({"nomenclador_id": v.nomenclador_id, "motivo": str(e)})

    # El borrado, acotado a los valores de esta carga y nada más.
    await db.execute(
        delete(HistorialPrecioCodigo).where(HistorialPrecioCodigo.valores_id.in_(ids))
    )
    await db.execute(delete(ValorComponente).where(ValorComponente.valor_id.in_(ids)))
    await db.execute(delete(Valor).where(Valor.id.in_(ids)))
    await db.flush()

    for anterior, repuntes in reactivaciones:
        anterior.estado = "activo"
        anterior.vigencia_hasta = None
        for comp, galeno_vigente_id in repuntes:
            comp.galeno_id = galeno_vigente_id
    await db.flush()

    for anterior, _ in reactivaciones:
        # Sin fecha_corte: la fila que había que cerrar era la de la carga borrada,
        # y ya no existe.
        await service.regenerar_historial_por_valores(
            anterior.id, None, db, motivo="reversion"
        )

    await db.commit()
    return {
        "eliminados": len(ids),
        "reactivados": len(reactivaciones),
        "errores": errores,
    }


@router.post("/", response_model=ValorOut, status_code=201)
async def create_valor(body: ValorCreate, db: AsyncSession = Depends(get_db)):
    existente = await _buscar_valor_activo(
        db, body.obra_social_nro, body.nomenclador_id, body.origen.value,
        body.especialidad_id_colegio,
    )
    if existente:
        variante = (
            f"especialidad {body.especialidad_id_colegio}"
            if body.especialidad_id_colegio is not None else "sin especialidad"
        )
        raise HTTPException(
            409,
            f"Ya existe un valor activo (id {existente.id}) para este código, OS, "
            f"origen {body.origen.value} ({variante}) — use POST /valores_nm/{existente.id}/actualizar",
        )

    valor = await _crear_valor_con_componentes(
        db=db,
        obra_social_nro=body.obra_social_nro,
        nomenclador_id=body.nomenclador_id,
        origen=body.origen.value,
        vigencia_desde=body.vigencia_desde,
        componentes_in=body.componentes,
        descripcion=body.descripcion,
        nivel=body.nivel,
        complejidad=body.complejidad,
        especialidad_id_colegio=body.especialidad_id_colegio,
        observacion=body.observacion,
        por_presupuesto=body.por_presupuesto,
        cantidad_ayudantes=body.cantidad_ayudantes,
        categoria=body.categoria,
        requiere_autorizacion=body.requiere_autorizacion,
    )

    await service.regenerar_historial_por_valores(valor.id, None, db, motivo="carga_inicial")

    await db.commit()
    await db.refresh(valor)
    return await _valor_out(db, valor)


@router.post("/generar_nn_por_rangos", response_model=GenerarValoresNNResult)
async def generar_nn_por_rangos(
    body: GenerarValoresNNIn, db: AsyncSession = Depends(get_db)
):
    """
    Siembra los valores NN de una OS: por cada código numérico de nm_nomenclador en
    1..419999 con al menos una unidad no nula, crea un Valor NN con 3 componentes
    calculables (Honorarios/Ayudante contra el galeno de honorarios del rango, Gastos
    contra el galeno de gastos del rango). Si el código ya tiene un NN activo, lo cierra
    y recrea. Partial-success: los códigos sin galeno vigente van a `errores`.
    """
    os = (await db.execute(
        select(ObrasSociales).where(ObrasSociales.NRO_OBRASOCIAL == body.obra_social_nro)
    )).scalar_one_or_none()
    if not os:
        raise HTTPException(404, f"Obra social {body.obra_social_nro} no encontrada")

    try:
        resultado = await service.generar_valores_nn_por_rangos(
            body.obra_social_nro, body.vigencia_desde, db
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(409, str(e))

    # Si no se creó/recreó nada y todo cayó en errores por falta de galenos, avisar claro
    if (
        resultado["total_candidatos"] > 0
        and resultado["creados"] == 0
        and resultado["recreados"] == 0
        and resultado["errores"]
    ):
        await db.rollback()
        raise HTTPException(
            404,
            f"La OS {body.obra_social_nro} no tiene los galenos base cargados "
            f"(galeno_quirurgico/practica/radiologico, gasto_*). Cargá los galenos primero.",
        )

    await db.commit()
    return GenerarValoresNNResult(**resultado)


@router.get("/{id}", response_model=ValorOut)
async def get_valor(id: int, db: AsyncSession = Depends(get_db)):
    obj = await db.get(Valor, id)
    if not obj:
        raise HTTPException(404, "Valor no encontrado")
    return await _valor_out(db, obj)


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
    return await _valor_out(db, obj)


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

    # El origen se conserva del valor que se cierra; revalidar las reglas del origen
    # contra la nueva ecuación (NN exige galeno y no admite por_presupuesto).
    es_galeno = (
        None if body.por_presupuesto
        else any(c.galeno_id is not None for c in body.componentes)
    )
    try:
        validar_reglas_origen(
            anterior.origen, anterior.especialidad_id_colegio,
            body.por_presupuesto, es_galeno,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))

    fecha_corte = body.vigencia_desde - datetime.timedelta(days=1)
    _cerrar_valor(anterior, fecha_corte)
    await db.flush()

    nuevo = await _crear_valor_con_componentes(
        db=db,
        obra_social_nro=anterior.obra_social_nro,
        nomenclador_id=anterior.nomenclador_id,
        origen=anterior.origen,
        vigencia_desde=body.vigencia_desde,
        componentes_in=body.componentes,
        descripcion=body.descripcion or anterior.descripcion,
        nivel=body.nivel if body.nivel is not None else anterior.nivel,
        complejidad=body.complejidad if body.complejidad is not None else anterior.complejidad,
        especialidad_id_colegio=anterior.especialidad_id_colegio,
        observacion=body.observacion or anterior.observacion,
        por_presupuesto=body.por_presupuesto,
        cantidad_ayudantes=(
            body.cantidad_ayudantes if body.cantidad_ayudantes is not None
            else anterior.cantidad_ayudantes
        ),
        categoria=body.categoria if body.categoria is not None else anterior.categoria,
        requiere_autorizacion=(
            body.requiere_autorizacion if body.requiere_autorizacion is not None
            else anterior.requiere_autorizacion
        ),
    )

    await service.regenerar_historial_por_valores(
        nuevo.id, fecha_corte, db, motivo="valores_estructura"
    )

    await db.commit()
    await db.refresh(nuevo)
    return await _valor_out(db, nuevo)


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
                    "valor_unitario": c.valor_unitario,                    "orden": c.orden,
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
                origen.origen, origen.especialidad_id_colegio,
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
                origen=origen.origen,
                codigo=nom_dest.codigo,
                descripcion=nom_dest.descripcion,
                nivel=nivel_destino,
                complejidad=None,
                especialidad_id_colegio=origen.especialidad_id_colegio,
                cantidad_ayudantes=origen.cantidad_ayudantes,
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


# ─── Replicación cross-OS ─────────────────────────────────────────────────────

@router.post("/replicar_a_obras_sociales", response_model=ActualizacionMasivaResult)
async def replicar_a_obras_sociales(
    body: ReplicarObrasSocialesIn, db: AsyncSession = Depends(get_db)
):
    """
    Replica la config de un Valor (mismo código CMC, origen, especialidad, nivel y
    ecuación/unidades) hacia varias obras sociales. El precio NO se copia entre OS:
    - galeno → cada OS usa su propio galeno vigente (codigo+nivel); si no lo tiene,
      ese destino queda en `errores`.
    - fijo   → con incluir_precio_fijo se copia el valor_unitario; si no, va a `errores`.
    """
    origen = await db.get(Valor, body.origen_valor_id)
    if not origen:
        raise HTTPException(404, "Valor origen no encontrado")

    comps_origen = await _componentes_activos(db, origen.id)
    if not comps_origen:
        raise HTTPException(422, "El valor origen no tiene componentes activos")

    nom = await db.get(NomencladorCMC, origen.nomenclador_id)

    # Resolver obras sociales destino (siempre se excluye la OS del origen)
    if body.modo == "todas":
        habilitadas = (
            await db.execute(
                select(ObrasSociales.NRO_OBRASOCIAL).where(ObrasSociales.MARCA == "S")
            )
        ).scalars().all()
        exclusiones = set(body.obras_sociales) | {origen.obra_social_nro}
        destinos_os = [os for os in habilitadas if os not in exclusiones]
    else:
        destinos_os = [os for os in body.obras_sociales if os != origen.obra_social_nro]

    fecha_corte = body.vigencia_desde - datetime.timedelta(days=1)
    actualizados = 0
    errores = []

    for os_dest in destinos_os:
        try:
            componentes_destino = []
            error_os = None
            for c in comps_origen:
                galeno_id = None
                cantidad = c.cantidad
                valor_unitario = c.valor_unitario
                if c.galeno_id is not None:
                    galeno_origen = await db.get(Galeno, c.galeno_id)
                    if galeno_origen is None:
                        error_os = f"Componente {c.id} del origen apunta a un galeno inexistente"
                        break
                    galeno_dest = await service.buscar_galeno_vigente(
                        db, os_dest, galeno_origen.codigo, galeno_origen.nivel
                    )
                    if not galeno_dest:
                        error_os = (
                            f"Galeno '{galeno_origen.codigo}' nivel {galeno_origen.nivel} "
                            f"no vigente en OS {os_dest}; cargá su precio primero"
                        )
                        break
                    galeno_id = galeno_dest.id
                    if body.usar_unidades_nomenclador and nom is not None:
                        attr = _UNIDADES_MAP[c.concepto]
                        default = getattr(nom, attr, None)
                        if not default:
                            error_os = (
                                f"El código no tiene '{attr}' para re-resolver la "
                                f"cantidad del componente '{c.concepto}'"
                            )
                            break
                        cantidad = default
                elif not body.incluir_precio_fijo:
                    error_os = (
                        "Modalidad fija sin incluir_precio_fijo: no hay precio para "
                        "replicar (cargá el valor por OS)"
                    )
                    break

                componentes_destino.append({
                    "concepto": c.concepto,
                    "galeno_id": galeno_id,
                    "cantidad": cantidad,
                    "valor_unitario": valor_unitario,                    "orden": c.orden,
                    "observacion": c.observacion,
                })

            if error_os is None:
                error_os = _validar_homogeneidad_datos(componentes_destino)
            if error_os:
                errores.append({"obra_social_nro": os_dest, "motivo": error_os})
                continue

            previo = await _buscar_valor_activo(
                db, os_dest, origen.nomenclador_id, origen.origen,
                origen.especialidad_id_colegio,
            )
            if previo:
                _cerrar_valor(previo, fecha_corte)
                await db.flush()

            nuevo = Valor(
                obra_social_nro=os_dest,
                nomenclador_id=origen.nomenclador_id,
                origen=origen.origen,
                codigo=origen.codigo,
                descripcion=origen.descripcion,
                nivel=origen.nivel,
                complejidad=None,
                especialidad_id_colegio=origen.especialidad_id_colegio,
                cantidad_ayudantes=origen.cantidad_ayudantes,
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
            errores.append({"obra_social_nro": os_dest, "motivo": str(e)})

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
        Valor.origen == body.origen.value,
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
            "valor_unitario": nuevo_vu,            "orden": c.orden,
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
                db, body.obra_social_nro, item.nomenclador_id, body.origen.value, None
            )
            if not anterior:
                errores.append({"nomenclador_id": item.nomenclador_id,
                                "motivo": f"Sin valor activo (origen {body.origen.value}, sin especialidad)"})
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
                    "valor_unitario": item.nuevo_valor_unitario,                    "orden": c.orden,
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
                    _cond_variante_valor(v.origen, v.especialidad_id_colegio),
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
        # Resuelve contra la OS del pedido: si esa obra social tiene una fila propia
        # para el código, gana sobre la compartida del Colegio.
        nom = await service.resolver_nomenclador(
            db, body.codigo_colegio, body.obra_social_nro
        )
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
            db=db,
            via=body.via,
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
    codigo_colegio,origen,descripcion,valor_unitario,nivel,especialidad,concepto,galeno_codigo,cantidad,presupuesto

    - `origen` (obligatorio): NE | NNE | NN. Fija la prioridad de la variante.
    - `especialidad` (opcional): solo válida para origen NE; vacío = sin especialidad.
    - `presupuesto` (opcional, truthy: 1/true/si/x): el grupo es "por presupuesto";
      se ignora la ecuación de las filas y se guardan los 3 conceptos (H/G/A) en 0.
      No válido para origen NN (siempre calculado).
    - Cada (codigo_colegio, origen, especialidad) agrupa sus filas en un solo Valor.
    - Modalidad homogénea por grupo: todas las filas con galeno_codigo o ninguna.
    - Si galeno_codigo tiene valor → el galeno se resuelve por (OS, galeno_codigo,
      nivel del valor) con fallback al galeno sin nivel.
    - Si cantidad=0 con galeno → unidades por defecto del código (0 si no tiene).
    - Todo Valor se completa siempre a 3 conceptos (Honorarios/Gastos/Ayudante); los
      faltantes se crean en 0 respetando la modalidad. No existen componentes opcionales.
    - Si algún componente del grupo falla, NO se crea el Valor (todo o nada).
    """
    texto = await leer_texto(file)
    reader = csv.DictReader(io.StringIO(texto))

    origenes_validos = {o.value for o in Origen}

    # Agrupar filas por (codigo_colegio, origen, especialidad)
    grupos: dict = {}
    for i, row in enumerate(reader, start=2):
        codigo = row.get("codigo_colegio", "").strip()
        if not codigo:
            continue
        origen = row.get("origen", "").strip().upper()
        esp_str = row.get("especialidad", "").strip()
        especialidad = int(esp_str) if esp_str else None
        presupuesto = row.get("presupuesto", "").strip().lower() in {"1", "true", "si", "sí", "x"}
        clave = (codigo, origen, especialidad)
        if clave not in grupos:
            grupos[clave] = {
                "origen": origen,
                "descripcion": row.get("descripcion", "").strip() or None,
                "nivel": int(row["nivel"]) if row.get("nivel", "").strip() else None,
                "especialidad": especialidad,
                "por_presupuesto": presupuesto,
                "componentes": [],
                "fila_inicio": i,
            }
        # Si cualquier fila del grupo marca presupuesto, el grupo es por presupuesto
        grupos[clave]["por_presupuesto"] = grupos[clave]["por_presupuesto"] or presupuesto
        grupos[clave]["componentes"].append(row)

    procesados = 0
    errores = []

    for (codigo, origen, especialidad), data in grupos.items():
        try:
            if origen not in origenes_validos:
                errores.append({"fila": data["fila_inicio"], "codigo": codigo,
                                "motivo": f"origen inválido '{origen}' (esperado: {', '.join(sorted(origenes_validos))})"})
                continue

            stmt_nom = select(NomencladorCMC).where(
                NomencladorCMC.codigo == codigo, NomencladorCMC.activo == True
            )
            nom = (await db.execute(stmt_nom)).scalar_one_or_none()
            if not nom:
                errores.append({"fila": data["fila_inicio"], "codigo": codigo, "motivo": f"Código CMC '{codigo}' no encontrado"})
                continue

            nivel_valor = data["nivel"]
            por_presupuesto = data["por_presupuesto"]

            componentes_resueltos = []
            error_grupo = None

            # Por presupuesto: se ignora la ecuación del CSV; los 3 conceptos van en 0
            filas = [] if por_presupuesto else data["componentes"]
            for idx, row in enumerate(filas):
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
                        # Pre-fill: unidad-plantilla del galeno y, si no tiene, unidades
                        # del nomenclador. Si ninguna existe, el componente queda en 0
                        # (subtotal 0) — válido: todo Valor lleva los 3 conceptos aunque
                        # alguno sea 0 (a diferencia del alta manual, el CSV no lanza 422).
                        attr = _UNIDADES_MAP.get(concepto)
                        cantidad = (
                            (attr and getattr(galeno, attr, None))
                            or (attr and getattr(nom, attr, None))
                            or Decimal("0")
                        )

                componentes_resueltos.append({
                    "concepto": concepto,
                    "galeno_id": galeno_id,
                    "cantidad": cantidad,
                    "valor_unitario": valor_unitario,                    "orden": idx,
                })

            if por_presupuesto:
                componentes_resueltos = _componentes_presupuesto_cero()
            elif error_grupo is None:
                motivo_invalido = _validar_homogeneidad_datos(componentes_resueltos)
                if motivo_invalido:
                    error_grupo = {"fila": data["fila_inicio"], "motivo": motivo_invalido}
                else:
                    # Todo Valor lleva siempre los 3 conceptos (Honorarios/Gastos/Ayudante);
                    # los faltantes se crean en 0 respetando la modalidad del grupo.
                    componentes_resueltos = _completar_tres_componentes(componentes_resueltos)

            # Reglas del origen (especialidad solo NE; NN exige galeno y no presupuesto)
            if error_grupo is None:
                es_galeno = (
                    None if por_presupuesto
                    else any(c.get("galeno_id") is not None for c in componentes_resueltos)
                )
                try:
                    validar_reglas_origen(origen, especialidad, por_presupuesto, es_galeno)
                except ValueError as e:
                    error_grupo = {"fila": data["fila_inicio"], "motivo": str(e)}
            if error_grupo:
                error_grupo["codigo"] = codigo
                errores.append(error_grupo)
                continue

            # Cerrar valor activo previo de la misma variante si existe
            prev = await _buscar_valor_activo(db, obra_social_nro, nom.id, origen, especialidad)
            fecha_corte = vigencia_desde - datetime.timedelta(days=1)
            if prev:
                _cerrar_valor(prev, fecha_corte)
                await db.flush()

            nuevo = Valor(
                obra_social_nro=obra_social_nro,
                nomenclador_id=nom.id,
                origen=origen,
                codigo=nom.codigo,
                descripcion=data["descripcion"] or None,  # NULL = hereda del catálogo
                nivel=nivel_valor,
                complejidad=prev.complejidad if prev else None,
                especialidad_id_colegio=especialidad,
                cantidad_ayudantes=prev.cantidad_ayudantes if prev else None,
                por_presupuesto=por_presupuesto,
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
            errores.append({"fila": data.get("fila_inicio", "?"), "codigo": codigo, "motivo": str(e)})

    await db.commit()
    return ImportarCSVResult(procesados=procesados, errores=errores)
