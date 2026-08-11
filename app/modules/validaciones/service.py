"""Lógica de las validaciones/cargas de prestaciones contra obras sociales.

Porta el comportamiento de los `grabar_prestacion_*.php` del sistema legacy,
pero **sin tablas propias ni tablas del sistema viejo**. Todo pasa por las dos
tablas del circuito nuevo:

- la prestación se guarda en `detalle_facturacion` con `origen_carga='medico'`
  (no en `guardar_atencion`), así entra derecho a facturación y liquidación sin
  ningún volcado posterior;
- el período sale del puntero `periodo_medico_actual` vía
  `facturacion.service.get_periodo_medico`, que resuelve el override por obra
  social y cae al global si esa O.S. no tiene uno (no de `periodos_doctor`);
- el cierre del período es el de facturación (`facturacion.estado_doctor` /
  `facturacion.estado`), no una marca propia del módulo;
- los precios y la habilitación del código salen del nomenclador nuevo (`nm_*`)
  vía `facturacion.service.resolver_precio`, no de `valor_prestacion` ni de
  `valor_nomenclador_nacional`.

Lo que la O.S. respondió vive en las columnas `validacion_*` de la misma fila.

**Una validación que la O.S. no autorizó no factura.** Las rechazadas y las
pendientes se graban con importe 0 y `estado='X'` — el mismo marcador que usa el
borrado de facturación—, así el prestador ve qué pasó pero la fila no entra a
ninguna factura ni liquidación (todo el circuito filtra `estado='A'`).

Alcance actual — las seis obras sociales integradas del panel: carga manual
(Boreal 285, Omint 243), en línea (Sancor 411, Nobis 402, OSPJN 151) y contra
padrón (OSPM 433, contra `clientes_ospm` — el mismo padrón que usa el legacy, no
una copia). Cualquier otra obra social cae en `obra_manual_o_error()`.
"""
import csv
import datetime
import os
import shutil
from decimal import Decimal
from typing import Optional, Sequence

from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import and_, func, or_, select, tuple_, delete
from sqlalchemy.ext.asyncio import AsyncSession

import uuid

from app.common.files import UPLOAD_ROOT, url_archivo
from app.common.money import quantize_money
from app.common.uploads import validate_upload
from app.db.models import ClientesOspm, DetalleFacturacionCMC, ListadoMedico
from app.db.models.padron_ospm import OSPM_ACTIVO, OSPM_INACTIVO
from app.db.models.nomenclador_cmc import NomencladorCMC, Valor

_SOLO_PDF = frozenset({".pdf"})
from app.modules.facturacion.service import (
    ORIGEN_MEDICO,
    _cleanup_factura_si_vacia,
    _ensure_factura_abierta,
    _gate_carga,
    _get_factura,
    _validar_autorizacion_medico,
    asegurar_periodo_medico_vigente,
    calcular_importe_total,
    derivar_tipo,
    get_periodo_medico,
    resolver_precio,
    tpo_funcion_derivado,
)
from app.modules.nomenclador import service as service_nm
from app.modules.validaciones import sancor, nobis, ospjn

CERO = Decimal("0.00")

# Estado que devolvió la obra social (columna `validacion_estado`).
#   autorizada → el validador de la OS la aprobó en línea
#   rechazada  → el validador la rechazó
#   pendiente  → requiere gestión del afiliado en la OS
#   cargada    → carga manual: la OS autorizó por fuera, acá se registró
ESTADOS_FACTURABLES = ("autorizada", "cargada")

# `detalle_facturacion.estado`: 'A' entra a la factura, 'X' no. Las validaciones
# no autorizadas nacen en 'X' para no tocar ningún filtro del resto del sistema.
DETALLE_ACTIVO = "A"
DETALLE_FUERA_DE_FACTURA = "X"

# Valores fijos con los que este módulo asienta en `detalle_facturacion`. Una
# validación es siempre una prestación simple del propio médico: sin equipo, sin
# ayudante, sin clínica, una sesión, al 100% y con el precio del nomenclador.
SESION_UNICA = 1
PORCENTAJE_COMPLETO = 100
CALCULO_AUTOMATICO = "A"  # `manual` = 'A': el monto lo puso el lookup, no el operador

# Obras sociales cuyo validador consultamos en línea.
SANCOR_OS = 411
NOBIS_OS = 402
OSPJN_OS = 151
OBRAS_ONLINE = {
    SANCOR_OS: "Sancor Salud",
    NOBIS_OS: "Nobis Salud",
    OSPJN_OS: "OSPJN · Poder Judicial",
}

# OSPM valida contra padrón propio, sin servicio externo: no entra en
# OBRAS_ONLINE (no hay a quién consultar) ni en OBRAS_MANUALES (el prestador no
# trae un número de autorización de afuera — lo resuelve el sistema).
OSPM_OS = 433

# Sólo los códigos de consulta (42*) tienen el tope de uno por afiliado y día.
# Es una regla del convenio de OSPM, replicada de grabar_prestacion_ospm_1.php.
OSPM_PREFIJO_CONSULTA = "42"


class ObraManual:
    """Parámetros con los que cada obra social de carga manual graba la fila."""

    def __init__(
        self,
        nro: int,
        nombre: str,
        *,
        descuenta_coseguro: bool,
        requiere_autorizacion: bool,
        requiere_nombre: bool,
        admite_orden: bool = False,
    ):
        self.nro = nro
        self.nombre = nombre
        # Boreal descuenta el coseguro del total; Omint no cobra coseguro.
        self.descuenta_coseguro = descuenta_coseguro
        self.requiere_autorizacion = requiere_autorizacion
        self.requiere_nombre = requiere_nombre
        self.admite_orden = admite_orden


OBRAS_MANUALES: dict[int, ObraManual] = {
    285: ObraManual(
        285,
        "Boreal Salud",
        descuenta_coseguro=True,
        requiere_autorizacion=True,
        requiere_nombre=True,
        admite_orden=True,
    ),
    243: ObraManual(
        243,
        "Omint",
        descuenta_coseguro=False,
        requiere_autorizacion=False,
        requiere_nombre=True,
    ),
}


def obra_manual_o_error(nro: int) -> ObraManual:
    obra = OBRAS_MANUALES.get(nro)
    if obra is None:
        implementadas = [f"{o.nombre} ({o.nro})" for o in OBRAS_MANUALES.values()]
        implementadas += [f"{n} ({k}, en línea)" for k, n in OBRAS_ONLINE.items()]
        implementadas.append(f"OSPM ({OSPM_OS}, contra padrón)")
        raise HTTPException(
            422,
            f"La obra social {nro} todavía no está implementada en el panel. "
            "Sólo están: " + ", ".join(implementadas),
        )
    return obra


def especialidades_de(medico: ListadoMedico) -> list[int]:
    """Las 6 columnas NRO_ESPECIALIDAD*, sin ceros ni repetidos."""
    crudas = [
        medico.NRO_ESPECIALIDAD,
        medico.NRO_ESPECIALIDAD2,
        medico.NRO_ESPECIALIDAD3,
        medico.NRO_ESPECIALIDAD4,
        medico.NRO_ESPECIALIDAD5,
        medico.NRO_ESPECIALIDAD6,
    ]
    vistas: list[int] = []
    for e in crudas:
        if e and int(e) not in vistas:
            vistas.append(int(e))
    return vistas


# ── Períodos ──────────────────────────────────────────────────────────────────

def partes_periodo(periodo: str) -> tuple[int, int]:
    """'YYYYMM' → (mes, anio)."""
    return (int(periodo[4:6]), int(periodo[0:4]))


async def periodo_actual(db: AsyncSession, obra_social_id: int) -> str:
    """Período en el que el médico está cargando para esa obra social.

    Sale del puntero `periodo_medico_actual`: primero el override de la O.S., y
    si no tiene, la fila global. NO es el mes calendario ni depende de la fecha
    de la prestación — es el mismo puntero con el que el médico carga desde
    facturación, para que todo caiga en el mismo período.

    `asegurar_periodo_medico_vigente` avanza el puntero si ya venció el
    `dia_corte` de la O.S., por si el cron de cierre no corrió.
    """
    cod_obra = str(obra_social_id)
    await asegurar_periodo_medico_vigente(db, cod_obra)
    return await get_periodo_medico(db, cod_obra)


async def periodo_cerrado(db: AsyncSession, obra_social_id: int, periodo: str) -> bool:
    """True si el médico ya no puede cargar en ese período de esa obra social.

    Mismo criterio que facturación (`_gate_carga`): la fase médico o la fase
    colegio de la cabecera está cerrada. Sin cabecera → abierto (se crea con la
    primera prestación).
    """
    try:
        await _gate_periodo(db, obra_social_id, periodo)
    except HTTPException:
        return True
    return False


async def _gate_periodo(db: AsyncSession, obra_social_id: int, periodo: str) -> None:
    """Corta con 409 si el período está cerrado para el médico. Se llama **antes**
    de consultar al validador de la O.S.: no tiene sentido consumir el token de la
    credencial del afiliado para una prestación que después no vamos a poder grabar.
    """
    _gate_carga(await _get_factura(db, str(obra_social_id), periodo), ORIGEN_MEDICO)


# ── Lecturas auxiliares ───────────────────────────────────────────────────────

async def get_medico(db: AsyncSession, nro_socio: int) -> ListadoMedico:
    medico = (
        await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == nro_socio))
    ).scalar_one_or_none()
    if medico is None:
        raise HTTPException(404, f"No existe el socio {nro_socio} en el registro del Colegio.")
    return medico


def _os_nro(cod_obr: Optional[str]) -> Optional[int]:
    """`detalle_facturacion.cod_obr` es varchar; el nomenclador usa int."""
    try:
        return int(cod_obr) if cod_obr else None
    except (TypeError, ValueError):
        return None


async def _descripciones(
    db: AsyncSession, filas: Sequence[DetalleFacturacionCMC]
) -> dict[int, str]:
    """Descripción de cada prestación, resuelta contra SU obra social.

    `detalle_facturacion` guarda el código, no el texto: se resuelve al listar para no
    desnormalizar. El código no es identidad global — el mismo número puede ser una
    práctica compartida del Colegio o una propia de esa OS —, así que la fila del
    catálogo sale de `nomenclador_id` y el texto preferido es el que la obra social
    pactó en su `nm_valores` (ver service_nm.descripcion_efectiva).

    Devuelve un dict por `id_detalle_prestaciones`, no por código: dos filas con el
    mismo `cod_nom` y distinta OS pueden tener descripciones distintas.
    """
    if not filas:
        return {}

    ids_catalogo = {f.nomenclador_id for f in filas if f.nomenclador_id}
    # Filas viejas sin backfill (código que ya no existe en el catálogo): se intenta
    # igual por código contra los compartidos.
    codigos_sueltos = {f.cod_nom for f in filas if not f.nomenclador_id and f.cod_nom}

    condiciones = []
    if ids_catalogo:
        condiciones.append(NomencladorCMC.id.in_(ids_catalogo))
    if codigos_sueltos:
        condiciones.append(
            and_(
                NomencladorCMC.codigo.in_(codigos_sueltos),
                NomencladorCMC.obra_social_nro.is_(None),
            )
        )

    por_id: dict[int, NomencladorCMC] = {}
    por_codigo: dict[str, NomencladorCMC] = {}
    if condiciones:
        for nom in (
            await db.execute(select(NomencladorCMC).where(or_(*condiciones)))
        ).scalars():
            por_id[nom.id] = nom
            if nom.obra_social_nro is None:
                por_codigo[nom.codigo] = nom

    # Descripción pactada por (OS, código) para las combinaciones que aparecen.
    pares = {
        (os_nro, f.nomenclador_id)
        for f in filas
        if f.nomenclador_id and (os_nro := _os_nro(f.cod_obr)) is not None
    }
    desc_os: dict[tuple[int, int], str] = {}
    if pares:
        filas_valor = (
            await db.execute(
                select(
                    Valor.obra_social_nro,
                    Valor.nomenclador_id,
                    func.max(Valor.descripcion),
                )
                .where(
                    tuple_(Valor.obra_social_nro, Valor.nomenclador_id).in_(pares),
                    Valor.estado == "activo",
                    Valor.descripcion.is_not(None),
                    Valor.descripcion != "",
                )
                .group_by(Valor.obra_social_nro, Valor.nomenclador_id)
            )
        ).all()
        desc_os = {(os_nro, nom_id): desc for os_nro, nom_id, desc in filas_valor}

    salida: dict[int, str] = {}
    for f in filas:
        nom = por_id.get(f.nomenclador_id) if f.nomenclador_id else por_codigo.get(f.cod_nom or "")
        os_nro = _os_nro(f.cod_obr)
        texto = desc_os.get((os_nro, f.nomenclador_id)) if f.nomenclador_id else None
        salida[f.id_detalle_prestaciones] = texto or (nom.descripcion if nom else "") or ""
    return salida


def _to_dict(f: DetalleFacturacionCMC, descripcion: str = "") -> dict:
    """Vista de la prestación para el panel del prestador."""
    mes, anio = partes_periodo(f.periodo)
    return {
        "id": f.id_detalle_prestaciones,
        "fecha": f.fecha_practica,
        "codigo": f.cod_nom or "",
        "descripcion": descripcion,
        "nro_afiliado": f.dni_p or "",
        "nombre_afiliado": f.nom_ape_p or "",
        "nro_validacion": f.autorizacion,
        "estado": f.validacion_estado or "cargada",
        "estado_detalle": f.validacion_detalle or "",
        "cantidad": f.cantidad or 1,
        "honorarios": quantize_money(f.honorarios or 0),
        "gastos": quantize_money(f.gastos or 0),
        "coseguro": quantize_money(f.coseguro or 0),
        "total": quantize_money(f.importe_total or 0),
        "mes": mes,
        "anio": anio,
        "obra_social": int(f.cod_obr),
        # La orden es del paciente: sale por el endpoint autorizado, nunca
        # por /uploads. Ver app/common/files.py::url_archivo.
        "orden": url_archivo(f.orden_path),
    }


def _filtro_validaciones(nro_socio: int, obra_social_id: int):
    """Filas de `detalle_facturacion` que salieron del módulo de validaciones para
    ese prestador y esa obra social, sin las que el prestador dio de baja."""
    M = DetalleFacturacionCMC
    return (
        M.cod_med == str(nro_socio),
        M.cod_obr == str(obra_social_id),
        M.validacion_estado.is_not(None),
        M.validacion_anulada.is_(False),
    )


# ── Listados ──────────────────────────────────────────────────────────────────

async def listar_prestaciones(
    db: AsyncSession, nro_socio: int, obra_social_id: int, mes: int, anio: int
) -> list[dict]:
    periodo = f"{anio}{mes:02d}"
    M = DetalleFacturacionCMC
    filas = list(
        (
            await db.execute(
                select(M)
                .where(*_filtro_validaciones(nro_socio, obra_social_id), M.periodo == periodo)
                .order_by(M.id_detalle_prestaciones.desc())
            )
        )
        .scalars()
        .all()
    )
    descripciones = await _descripciones(db, filas)
    return [_to_dict(f, descripciones.get(f.id_detalle_prestaciones, "")) for f in filas]


async def listar_periodos(
    db: AsyncSession, nro_socio: int, obra_social_id: int
) -> list[dict]:
    """Totales por período, el más reciente primero. Las rechazadas cuentan en
    `cantidad` pero suman 0 — que es justo lo que van a facturar."""
    M = DetalleFacturacionCMC
    rows = (
        await db.execute(
            select(
                M.periodo,
                func.count(M.id_detalle_prestaciones),
                func.coalesce(func.sum(M.importe_total), 0),
            )
            .where(*_filtro_validaciones(nro_socio, obra_social_id))
            .group_by(M.periodo)
            .order_by(M.periodo.desc())
        )
    ).all()

    salida = []
    for periodo, cantidad, total in rows:
        mes, anio = partes_periodo(periodo)
        salida.append(
            {
                "mes": mes,
                "anio": anio,
                "cantidad": cantidad,
                "total": quantize_money(total or 0),
                "cerrado": await periodo_cerrado(db, obra_social_id, periodo),
            }
        )
    return salida


async def buscar_codigos(
    db: AsyncSession, obra_social_id: int, nro_socio: int, q: str, limite: int = 20
) -> list[dict]:
    """Códigos del nomenclador nuevo con el valor que paga esa obra social.

    El precio sale del mismo lookup que usa facturación, así que lo que ve el
    prestador acá es lo que después se le va a liquidar.
    """
    medico = await get_medico(db, nro_socio)
    hoy = datetime.date.today()

    stmt = select(NomencladorCMC.codigo, NomencladorCMC.descripcion).where(
        # Códigos compartidos del Colegio + los propios de esta obra social.
        service_nm.filtro_pertenencia(obra_social_id)
    )
    termino = (q or "").strip()
    if termino:
        like = f"%{termino}%"
        stmt = stmt.where(
            NomencladorCMC.codigo.like(like) | NomencladorCMC.descripcion.like(like)
        )
    # La fila propia de la OS antes que la compartida; el dedupe de abajo se queda con
    # la primera que aparece de cada código.
    stmt = stmt.order_by(
        NomencladorCMC.codigo, NomencladorCMC.obra_social_key.desc()
    ).limit(limite * 2)
    filas = (await db.execute(stmt)).all()

    salida: list[dict] = []
    vistos: set[str] = set()
    for codigo, descripcion in filas:
        if codigo in vistos:
            continue
        vistos.add(codigo)
        if len(salida) >= limite:
            break
        try:
            precio = await resolver_precio(db, str(obra_social_id), medico, codigo, hoy)
        except HTTPException:
            continue
        salida.append(
            {
                "codigo": codigo,
                "descripcion": precio.descripcion or descripcion or "",
                "honorarios": precio.honorarios,
                "gastos": precio.gastos,
                "total": precio.honorarios + precio.gastos,
                "admitido": precio.admitido,
                "motivo": precio.motivo,
            }
        )
    return salida


# ── Escrituras ────────────────────────────────────────────────────────────────

async def _grabar_prestacion(
    db: AsyncSession,
    *,
    medico: ListadoMedico,
    obra_social_id: int,
    periodo: str,
    codigo: str,
    precio,
    cantidad: int,
    coseguro: Decimal,
    nro_afiliado: str,
    nombre_afiliado: str,
    nro_autorizacion: Optional[str],
    validacion_estado: str,
    validacion_detalle: str,
    validacion_respuesta: Optional[dict],
    fecha: datetime.date,
    usuario_carga: int,
) -> DetalleFacturacionCMC:
    """Graba la prestación validada en `detalle_facturacion`.

    Mapeo de campos (el resto de las columnas son legacy y quedan en NULL):

    | detalle_facturacion | de dónde sale |
    |---|---|
    | `origen_carga`      | siempre `'medico'` — la prestación es del médico, la tipee él o el Colegio en su nombre. Es lo que hace que la controle la fase médico del período |
    | `periodo`           | puntero `periodo_medico_actual` (override por O.S. → global) |
    | `cod_med` / `cod_obr` / `cod_nom` | NRO_SOCIO del prestador / NRO_OBRASOCIAL / código del nomenclador |
    | `dni_p` / `nom_ape_p` | nro y nombre de afiliado (`dni_p` es lo que liquidación y lotes ya leen como "nro de afiliado") |
    | `autorizacion`      | nro de autorización que devolvió (o que registró) la O.S. |
    | `honorarios` / `gastos` | lookup del nomenclador nuevo, igual que facturación |
    | `importe_total`     | (honorarios + gastos) × cantidad, menos el coseguro en las O.S. que lo descuentan. **0 si la O.S. no autorizó** |
    | `manual`            | 'A': el monto lo puso el lookup, no un operador |
    | `estado`            | 'A' si la O.S. autorizó; 'X' si no (no entra a ninguna factura) |
    | `usuario`           | NRO_SOCIO de **quien tipeó** la carga. Cuando el Colegio carga en nombre de un médico, `cod_med` es el médico y `usuario` el operador: ahí queda registrada la diferencia |
    | `validacion_*`      | qué respondió la O.S. y la traza cruda |

    Sin ayudante, sin clínica, sin equipo y al 100%: una validación es siempre
    una prestación simple del propio médico.
    """
    factura = validacion_estado in ESTADOS_FACTURABLES

    # Lo que la O.S. no autorizó vale 0: no se le puede facturar.
    honorarios = quantize_money(precio.honorarios) if factura else CERO
    gastos = quantize_money(precio.gastos) if factura else CERO
    coseguro = quantize_money(coseguro) if factura else CERO
    total = calcular_importe_total(honorarios, gastos, CERO, cantidad, SESION_UNICA)
    # Boreal: el afiliado ya pagó el coseguro de su bolsillo, así que a la obra
    # social se le factura el neto. Los conceptos quedan en el valor del
    # nomenclador (es lo que vale la práctica) y el descuento va sobre el total.
    total = quantize_money(total - coseguro)

    cabecera = await _get_factura(db, str(obra_social_id), periodo)
    version_destino = cabecera.version if cabecera is not None else 1

    # Fila del catálogo con la que se cotizó: el código puede ser propio de esta OS.
    nomenclador = await service_nm.resolver_nomenclador(db, codigo, obra_social_id)

    fila = DetalleFacturacionCMC(
        periodo=periodo,
        cod_obr=str(obra_social_id),
        cod_med=str(medico.NRO_SOCIO),
        cod_nom=codigo,
        nomenclador_id=nomenclador.id if nomenclador else None,
        nro_orden="0",  # placeholder — la columna es NOT NULL; se espeja el PK abajo
        tipo=await derivar_tipo(
            db, codigo, bool(medico.es_organizacion), str(obra_social_id)
        ),
        # tpo_funcion derivado SOLO para coexistencia (liquidación/lotes lo leen).
        tpo_funcion=tpo_funcion_derivado(honorarios, gastos, CERO),
        sesion=SESION_UNICA,
        cantidad=cantidad,
        honorarios=honorarios,
        gastos=gastos,
        ayudante=CERO,
        importe_total=total,
        coseguro=coseguro,
        manual=CALCULO_AUTOMATICO,
        dni_p=(nro_afiliado or "")[:20] or None,
        nom_ape_p=(nombre_afiliado or "")[:60] or None,
        fecha_practica=fecha,
        autorizacion=nro_autorizacion,
        porc=PORCENTAJE_COMPLETO,
        estado=DETALLE_ACTIVO if factura else DETALLE_FUERA_DE_FACTURA,
        origen_carga=ORIGEN_MEDICO,
        usuario=str(usuario_carga)[:15],
        version=version_destino,
        calculo_snapshot=precio.snapshot,
        validacion_estado=validacion_estado,
        validacion_detalle=validacion_detalle[:255],
        validacion_respuesta=validacion_respuesta,
    )
    db.add(fila)
    await db.flush()  # asigna id_detalle_prestaciones (PK)
    # `nro_orden` no es un contador propio (el PK alcanza como identificador
    # único) — se deja igual al PK sólo para no dejar NULL una columna NOT NULL
    # legacy que liquidación (`nro_orden_cmc`) y lotes todavía leen para mostrar.
    fila.nro_orden = str(fila.id_detalle_prestaciones)

    if factura:
        # Cabecera abierta para la OS+período; la crea si es la primera del período.
        # Las no autorizadas no abren factura: no hay nada que facturar.
        await _ensure_factura_abierta(
            db, str(obra_social_id), periodo, str(usuario_carga)[:15]
        )

    await db.commit()
    await db.refresh(fila)
    return fila


async def crear_prestacion_manual(
    db: AsyncSession,
    *,
    nro_socio: int,
    obra: ObraManual,
    codigo: str,
    nombre_afiliado: str,
    nro_afiliado: str,
    nro_autorizacion: str,
    coseguro: Decimal,
    cantidad: int,
    usuario_carga: int,
) -> dict:
    if obra.requiere_autorizacion and not nro_autorizacion.strip():
        raise HTTPException(422, f"{obra.nombre} necesita el número de autorización.")
    if obra.requiere_nombre and not nombre_afiliado.strip():
        raise HTTPException(422, "Falta el nombre del afiliado.")
    # Chequeo por CÓDIGO, independiente del de arriba: el de `obra` es un todo-o-nada
    # de la obra social, éste marca prácticas puntuales que necesitan autorización
    # previa. No se aplica en el flujo Sancor porque ahí la autorización la emite la
    # obra social dentro de la misma llamada.
    await _validar_autorizacion_medico(db, codigo, str(obra.nro), nro_autorizacion)

    medico = await get_medico(db, nro_socio)
    hoy = datetime.date.today()
    periodo = await periodo_actual(db, obra.nro)
    await _gate_periodo(db, obra.nro, periodo)

    precio = await resolver_precio(db, str(obra.nro), medico, codigo, hoy)
    if not precio.admitido:
        raise HTTPException(422, precio.motivo or "El código no está habilitado.")

    fila = await _grabar_prestacion(
        db,
        medico=medico,
        obra_social_id=obra.nro,
        periodo=periodo,
        codigo=codigo,
        precio=precio,
        cantidad=cantidad,
        coseguro=Decimal(coseguro or 0) if obra.descuenta_coseguro else CERO,
        nro_afiliado=nro_afiliado.strip(),
        nombre_afiliado=nombre_afiliado.strip().upper(),
        nro_autorizacion=nro_autorizacion.strip() or None,
        # Carga manual: la obra social autorizó por fuera del panel.
        validacion_estado="cargada",
        validacion_detalle="",
        validacion_respuesta=None,
        fecha=hoy,
        usuario_carga=usuario_carga,
    )
    return _to_dict(fila, precio.descripcion or "")


def _ospm_parsear_padron(contenido: bytes) -> list[dict]:
    """Parsea el CSV/TXT del padrón que manda OSPM.

    Formato heredado de `importar_padron_ospm.php`: columnas
    `AFILIADO, DU, CUIT, ACTIVO`, separador `;` o `,` (se detecta con la primera
    línea), encabezado opcional que arranca con `AYN` o `AFILIADO`, y texto en
    ISO-8859-1. Se intenta UTF-8 primero porque los archivos nuevos ya vienen
    así; si falla, latin-1, que nunca rompe.
    """
    try:
        texto = contenido.decode("utf-8")
    except UnicodeDecodeError:
        texto = contenido.decode("latin-1")

    lineas = [ln for ln in texto.splitlines() if ln.strip()]
    if not lineas:
        raise HTTPException(422, "El archivo del padrón está vacío.")

    delimitador = ";" if ";" in lineas[0] else ","
    filas: list[dict] = []
    vistos: set[str] = set()

    for i, linea in enumerate(csv.reader(lineas, delimiter=delimitador)):
        if not linea:
            continue
        primera = (linea[0] or "").strip().upper()
        if i == 0 and primera in ("AYN", "AFILIADO"):
            continue

        nombre = (linea[0] or "").strip() if len(linea) > 0 else ""
        documento = (linea[1] or "").strip() if len(linea) > 1 else ""
        cuit = (linea[2] or "").strip() if len(linea) > 2 else ""
        activo = (linea[3] or "").strip().upper() if len(linea) > 3 else ""

        if not documento:
            continue  # fila sin DNI: no se puede buscar por ella, no sirve
        if documento in vistos:
            continue  # el padrón trae repetidos; gana el primero
        vistos.add(documento)

        # Se mapea a las columnas del legacy (`clientes_ospm`), con SUS límites:
        # DU varchar(8), CUIT varchar(11), AFILIADO varchar(30). Todas NOT NULL.
        filas.append(
            {
                "DU": documento[:8],
                "CUIT": cuit[:11],
                "AFILIADO": (nombre[:30] or "SIN NOMBRE"),
                "ACTIVO": OSPM_ACTIVO if activo == "S" else OSPM_INACTIVO,
            }
        )

    if not filas:
        raise HTTPException(
            422, "No se encontró ninguna fila válida. Se espera AFILIADO, DU, CUIT, ACTIVO."
        )
    return filas


async def importar_padron_ospm(db: AsyncSession, archivo: UploadFile) -> dict:
    """Reemplaza el padrón de OSPM con el del archivo.

    A diferencia del legacy —que hace `TRUNCATE` y recién después parsea, así
    que un archivo malo deja el padrón vacío y nadie valida— acá se parsea
    **primero** y el borrado va en la misma transacción que la carga: si algo
    falla, el padrón anterior queda intacto.
    """
    contenido = await archivo.read()
    filas = _ospm_parsear_padron(contenido)

    await db.execute(delete(ClientesOspm))
    db.add_all([ClientesOspm(**f) for f in filas])
    await db.commit()

    activos = sum(1 for f in filas if f["ACTIVO"] == OSPM_ACTIVO)
    return {
        "importados": len(filas),
        "activos": activos,
        "inactivos": len(filas) - activos,
    }


async def _ospm_afiliado(db: AsyncSession, documento: str) -> ClientesOspm:
    """Busca al afiliado en el padrón de OSPM (`clientes_ospm`).

    Es la MISMA tabla que usa el legacy: el padrón es uno solo, así que el PHP
    viejo y la API validan siempre contra el mismo dato. La importación de este
    módulo la reemplaza entera, igual que `importar_padron_ospm.php`.
    """
    doc = (documento or "").strip()
    if not doc:
        raise HTTPException(422, "Falta el DNI del afiliado.")

    fila = (
        await db.execute(select(ClientesOspm).where(ClientesOspm.DU == doc))
    ).scalar_one_or_none()

    if fila is None:
        total = int((await db.execute(select(func.count(ClientesOspm.ID)))).scalar_one() or 0)
        if total == 0:
            # Distinguirlo importa: con el padrón vacío NADIE valida, y el
            # prestador no tiene forma de saber que el problema no es su DNI.
            raise HTTPException(
                422,
                "El padrón de OSPM todavía no fue importado. Avisá al Colegio "
                "para que cargue el padrón vigente.",
            )
        raise HTTPException(422, f"El DNI {doc} no figura en el padrón de OSPM.")

    return fila


async def _ospm_duplicado(
    db: AsyncSession,
    *,
    codigo: str,
    nro_afiliado: str,
    fecha: datetime.date,
) -> bool:
    """¿Ya hay una consulta cargada para ese afiliado, código y día?

    Por convenio OSPM admite una sola consulta (códigos 42*) por afiliado y
    fecha. Se mira sobre `detalle_facturacion` —no sobre `guardar_atencion`, que
    es del legacy— y se ignoran las anuladas/fuera de factura: si la anterior se
    dio de baja, el cupo del día vuelve a estar libre.
    """
    if not codigo.startswith(OSPM_PREFIJO_CONSULTA):
        return False

    existe = (
        await db.execute(
            select(DetalleFacturacionCMC.id_detalle_prestaciones)
            .where(
                DetalleFacturacionCMC.cod_obr == str(OSPM_OS),
                DetalleFacturacionCMC.cod_nom == codigo,
                DetalleFacturacionCMC.dni_p == nro_afiliado,
                DetalleFacturacionCMC.fecha_practica == fecha,
                DetalleFacturacionCMC.estado == DETALLE_ACTIVO,
            )
            .limit(1)
        )
    ).first()
    return existe is not None


async def validar_ospm(
    db: AsyncSession,
    *,
    nro_socio: int,
    codigo: str,
    documento: str,
    cantidad: int,
    usuario_carga: int,
) -> dict:
    """Valida contra el padrón de OSPM (433) y graba la prestación.

    OSPM no tiene servicio de autorización: se resuelve con un solo dato local,
    **el afiliado**, buscado en `padron_ospm` por DNI. Si no está, no se graba
    nada.

    De ahí salen dos desenlaces:

    | Afiliado | Resultado |
    |---|---|
    | activo | `pendiente` — el afiliado gestiona la autorización en la O.S. |
    | inactivo | `rechazada` |

    Que un código pueda saltearse la autorización es criterio por convenio y
    todavía no está resuelto acá, así que se toma siempre el caso restrictivo:
    mejor mandar a gestionar de más que dar por autorizado algo que la obra
    social después rechaza.

    Como en el resto del módulo, **siempre** queda una fila: las que no se
    autorizaron van con importe 0 y `estado='X'`, visibles para el prestador
    pero fuera de toda factura.
    """
    medico = await get_medico(db, nro_socio)
    hoy = datetime.date.today()
    periodo = await periodo_actual(db, OSPM_OS)
    await _gate_periodo(db, OSPM_OS, periodo)

    afiliado = await _ospm_afiliado(db, documento)
    doc = afiliado.documento

    if await _ospm_duplicado(db, codigo=codigo, nro_afiliado=doc, fecha=hoy):
        raise HTTPException(
            422,
            f"Por convenio, el afiliado {doc} y la prestación {codigo} no pueden "
            "cargarse más de una vez en la misma fecha.",
        )

    precio = await resolver_precio(db, str(OSPM_OS), medico, codigo, hoy)
    if not precio.admitido:
        raise HTTPException(422, precio.motivo or "El código no está habilitado.")

    if not afiliado.activo:
        estado, detalle = "rechazada", "El afiliado no figura activo en el padrón de OSPM."
    else:
        estado, detalle = "pendiente", "Gestionar autorización en la obra social."

    fila = await _grabar_prestacion(
        db,
        medico=medico,
        obra_social_id=OSPM_OS,
        periodo=periodo,
        codigo=codigo,
        precio=precio,
        cantidad=cantidad,
        coseguro=CERO,  # OSPM no cobra coseguro (el legacy lo fija en 0)
        nro_afiliado=doc,
        nombre_afiliado=afiliado.nombre,
        # Sin nº de autorización: la da la obra social cuando el afiliado la
        # gestiona, no el Colegio.
        nro_autorizacion=None,
        validacion_estado=estado,
        validacion_detalle=detalle,
        validacion_respuesta={
            "padron": {
                "documento": doc,
                "activo": afiliado.activo,
                "importado_at": afiliado.importado_at.isoformat(),
            },
        },
        fecha=hoy,
        usuario_carga=usuario_carga,
    )

    # No se asigna número de validación: ninguna prestación de OSPM queda
    # autorizada acá, la autorización la da la obra social.
    return _to_dict(fila, precio.descripcion or "")


async def validar_sancor(
    db: AsyncSession,
    *,
    nro_socio: int,
    codigo: str,
    nro_afiliado: str,
    barra_afiliado: str,
    token: str,
    cantidad: int,
    usuario_carga: int,
) -> dict:
    """Pide la autorización a Sancor y guarda el resultado, salga como salga.

    A diferencia del legacy —que si no reconoce la respuesta no graba nada ni
    avisa— acá **siempre** queda una fila: autorizada, rechazada o pendiente. Así
    el prestador ve qué pasó y soporte puede reconstruirlo con
    `validacion_respuesta`. Las que Sancor no autorizó quedan en importe 0 y
    `estado='X'`: se ven en el panel pero no entran a la factura.
    """
    if not token.strip():
        raise HTTPException(422, "Sancor exige el token de la credencial del afiliado.")
    if not nro_afiliado.strip():
        raise HTTPException(422, "Falta el número de afiliado.")

    medico = await get_medico(db, nro_socio)
    hoy = datetime.date.today()
    periodo = await periodo_actual(db, SANCOR_OS)
    # Antes de hablar con Sancor: una autorización consume el token del afiliado,
    # no la pedimos si después no vamos a poder grabar la prestación.
    await _gate_periodo(db, SANCOR_OS, periodo)

    if codigo in sancor.CODIGOS_NO_ADMITIDOS:
        raise HTTPException(
            422, f"El código {codigo} no está habilitado para Sancor con esta especialidad."
        )

    # El precio y lo que se guarda usan SIEMPRE el código del Colegio; a Sancor
    # se le manda el que ella espera para esa especialidad (ver SUSTITUCIONES).
    precio = await resolver_precio(db, str(SANCOR_OS), medico, codigo, hoy)
    if not precio.admitido:
        raise HTTPException(422, precio.motivo or "El código no está habilitado.")

    especialidades = especialidades_de(medico)
    codigo_envio, codigo_original = sancor.sustituir_codigo(codigo, especialidades)
    afiliado = f"{nro_afiliado}/{barra_afiliado}" if barra_afiliado else nro_afiliado

    async def _guardar(
        estado: str,
        detalle: str,
        autorizacion: Optional[str],
        nombre: str,
        traza: Optional[dict],
    ) -> dict:
        fila = await _grabar_prestacion(
            db,
            medico=medico,
            obra_social_id=SANCOR_OS,
            periodo=periodo,
            codigo=codigo,
            precio=precio,
            cantidad=cantidad,
            coseguro=CERO,  # Sancor no descuenta coseguro
            nro_afiliado=afiliado,
            nombre_afiliado=nombre,
            nro_autorizacion=autorizacion,
            validacion_estado=estado,
            validacion_detalle=detalle,
            validacion_respuesta=traza,
            fecha=hoy,
            usuario_carga=usuario_carga,
        )
        return _to_dict(fila, precio.descripcion or "")

    # Práctica que el afiliado tiene que gestionar en la obra social: no se
    # consulta el autorizador, se deja constancia y listo (igual que el legacy).
    if codigo in sancor.CODIGOS_GESTION_PRESENCIAL:
        return await _guardar(
            "pendiente",
            "El paciente debe tramitar esta práctica en oficinas de Sancor.",
            None,
            "",
            None,
        )

    try:
        res = await sancor.autorizar(
            nro_matricula=medico.MATRICULA_PROV,
            nro_afiliado=nro_afiliado.strip(),
            barra_afiliado=barra_afiliado.strip(),
            token=token.strip(),
            codigo_prestacion=codigo_envio,
            fecha=hoy,
        )
    except sancor.SancorError as e:
        # No se llegó a pedir la autorización: no inventamos una fila autorizada.
        raise HTTPException(502, str(e)) from e

    traza = {
        "modo": res.modo,
        "codigo_enviado": codigo_envio,
        "codigo_original": codigo_original,
        "mensaje_enviado": res.enviado,
        "respuesta": res.crudo,
    }

    return await _guardar(
        "autorizada" if res.autorizada else "rechazada",
        res.estado_detalle,
        res.nro_autorizacion,
        res.nombre_afiliado or "",
        traza,
    )


async def validar_nobis(
    db: AsyncSession,
    *,
    nro_socio: int,
    codigo: str,
    nro_afiliado: str,
    token: str,
    cantidad: int,
    usuario_carga: int,
) -> dict:
    """Pide la autorización a Nobis (WSGeCROS) y guarda el resultado.

    Nobis devuelve tres estados, y los tres se graban:

    | `<Estado>` | `validacion_estado` | ¿Factura? |
    |---|---|---|
    | `A-Autorizado` | `autorizada` | sí |
    | `P-Pendiente`  | `pendiente`  | no — importe 0, `estado='X'` |
    | `R-Rechazada`  | `rechazada`  | no — importe 0, `estado='X'` |

    El **pendiente es el caso normal** en Nobis, no una excepción: la orden real
    documentada en el legacy volvió `P-Pendiente` con su número. Queda esperando
    resolución de la obra social, así que no puede facturarse todavía.

    En `autorizacion` se guarda el **número de orden** (`Num`), que es lo que
    identifica la orden en Nobis; el código de autorización (`Cod`) queda en la
    traza, porque es lo que después pide la anulación.
    """
    if not nro_afiliado.strip():
        raise HTTPException(422, "Falta el número de afiliado.")
    # El legacy exige el token en la pantalla pero NUNCA lo manda al WS: sólo lo
    # guarda. Se mantiene el requisito para no cambiarle la regla al prestador.
    if not token.strip():
        raise HTTPException(422, "Nobis exige el token de la credencial del afiliado.")

    medico = await get_medico(db, nro_socio)
    hoy = datetime.date.today()
    periodo = await periodo_actual(db, NOBIS_OS)
    # Antes de hablar con Nobis: insertar una orden es un efecto real, no la
    # pedimos si después no vamos a poder grabar la prestación.
    await _gate_periodo(db, NOBIS_OS, periodo)

    precio = await resolver_precio(db, str(NOBIS_OS), medico, codigo, hoy)
    if not precio.admitido:
        raise HTTPException(422, precio.motivo or "El código no está habilitado.")

    try:
        res = await nobis.insertar_autorizacion(
            numero_afiliado=nro_afiliado.strip(),
            mat_prov=str(medico.MATRICULA_PROV or ""),
            codigo_practica=codigo,
            cantidad=cantidad,
            fecha_prescripcion=hoy,
            fecha_realizacion=hoy,
        )
    except nobis.NobisError as e:
        # No se llegó a crear la orden: no inventamos una fila autorizada.
        raise HTTPException(502, str(e)) from e

    if res.autorizada:
        estado = "autorizada"
    elif res.requiere_gestion:
        estado = "pendiente"
    else:
        estado = "rechazada"

    fila = await _grabar_prestacion(
        db,
        medico=medico,
        obra_social_id=NOBIS_OS,
        periodo=periodo,
        codigo=codigo,
        precio=precio,
        # El coseguro que informa Nobis lo paga el afiliado de su bolsillo; no
        # se descuenta de lo que se le factura a la obra social. Queda en la
        # traza para que el prestador sepa cuánto cobrarle al paciente.
        coseguro=CERO,
        cantidad=cantidad,
        nro_afiliado=nro_afiliado.strip(),
        nombre_afiliado=res.nombre_afiliado or "",
        nro_autorizacion=res.nro_orden,
        validacion_estado=estado,
        validacion_detalle=res.estado_detalle,
        validacion_respuesta={
            "modo": res.modo,
            "estado": res.estado,
            "nro_orden": res.nro_orden,
            # Lo pide AnularOrdenNroCod: sin esto no se puede dar de baja.
            "cod_autorizacion": res.cod_autorizacion,
            "coseguro_informado": res.coseguro,
            "token_ingresado": token.strip(),
            "mensaje_enviado": res.enviado,
            "respuesta": res.crudo,
        },
        fecha=hoy,
        usuario_carga=usuario_carga,
    )
    return _to_dict(fila, precio.descripcion or "")


async def validar_ospjn(
    db: AsyncSession,
    *,
    nro_socio: int,
    codigo: str,
    nro_afiliado: str,
    barra_afiliado: str,
    cantidad: int,
    usuario_carga: int,
) -> dict:
    """Valida el afiliado contra OSPJN y graba la prestación.

    OSPJN **valida al afiliado**, no autoriza una práctica: se le manda una
    *categoría* de prestación ('CON' consultas / 'OTR' el resto) y contesta si
    está en condiciones, con un `NroConsulta` que acredita la validación. Por
    eso no hay nada que anular después: eliminar la prestación es una baja local.

    | Respuesta | `validacion_estado` | ¿Factura? |
    |---|---|---|
    | `NroConsulta` distinto de 0 | `autorizada` | sí |
    | INACTIVO / SUSPENDIDO / no encontrado | `rechazada` | no — importe 0, `estado='X'` |

    A OSPJN se le manda la categoría; el precio y lo que se guarda usan
    **siempre el código del Colegio**.
    """
    if not nro_afiliado.strip():
        raise HTTPException(422, "Falta el número de afiliado.")

    medico = await get_medico(db, nro_socio)
    hoy = datetime.date.today()
    periodo = await periodo_actual(db, OSPJN_OS)
    await _gate_periodo(db, OSPJN_OS, periodo)

    precio = await resolver_precio(db, str(OSPJN_OS), medico, codigo, hoy)
    if not precio.admitido:
        raise HTTPException(422, precio.motivo or "El código no está habilitado.")

    # Se deriva del propio código (42* + 430202 = consulta; el resto 'OTR'), no de
    # una columna: la regla es una función del número.
    categoria = ospjn.categoria_de_codigo(codigo)

    try:
        res = await ospjn.validar_afiliado(
            numero_afiliado=nro_afiliado.strip(),
            barra_afiliado=barra_afiliado.strip(),
            categoria_prestacion=categoria,
            fecha=hoy,
        )
    except ospjn.OspjnError as e:
        # No se llegó a validar: no inventamos una fila autorizada.
        raise HTTPException(502, str(e)) from e

    afiliado = (
        f"{nro_afiliado.strip()}/{barra_afiliado.strip()}"
        if barra_afiliado.strip()
        else nro_afiliado.strip()
    )

    fila = await _grabar_prestacion(
        db,
        medico=medico,
        obra_social_id=OSPJN_OS,
        periodo=periodo,
        codigo=codigo,
        precio=precio,
        cantidad=cantidad,
        coseguro=CERO,  # OSPJN no descuenta coseguro
        nro_afiliado=afiliado,
        nombre_afiliado=res.nombre_afiliado or "",
        nro_autorizacion=res.nro_consulta,
        validacion_estado="autorizada" if res.validado else "rechazada",
        validacion_detalle=res.estado_detalle,
        validacion_respuesta={
            "modo": res.modo,
            "categoria_enviada": categoria,
            "estado": res.estado,
            "nro_consulta": res.nro_consulta,
            "nro_documento": res.nro_documento,
            "mensaje_enviado": res.enviado,
            "respuesta": res.crudo,
        },
        fecha=hoy,
        usuario_carga=usuario_carga,
    )
    return _to_dict(fila, precio.descripcion or "")


async def _prestacion_del_socio(
    db: AsyncSession, prestacion_id: int, nro_socio: int
) -> DetalleFacturacionCMC:
    fila = await db.get(DetalleFacturacionCMC, prestacion_id)
    if fila is None or fila.validacion_estado is None or fila.validacion_anulada:
        raise HTTPException(404, "La prestación no existe o ya fue eliminada.")
    if str(fila.cod_med) != str(nro_socio):
        raise HTTPException(403, "La prestación pertenece a otro prestador.")
    return fila


async def adjuntar_orden(
    db: AsyncSession, prestacion_id: int, nro_socio: int, archivo: UploadFile
) -> dict:
    """Guarda la orden/receta en PDF y deja la ruta en `orden_path`."""
    fila = await _prestacion_del_socio(db, prestacion_id, nro_socio)

    # El chequeo anterior era sobre `content_type`, que lo declara el cliente:
    # bastaba mandar el header correcto para guardar cualquier cosa como PDF.
    # `validate_upload` lo decide por magic bytes y además limita el tamaño.
    info = await validate_upload(archivo, _SOLO_PDF)

    destino_dir = os.path.join(UPLOAD_ROOT, "validaciones", str(nro_socio))
    os.makedirs(destino_dir, exist_ok=True)

    nombre = f"{uuid.uuid4().hex}{info.extension}"
    destino = os.path.join(destino_dir, nombre)

    def _escribir() -> None:
        with open(destino, "wb") as f:
            f.write(info.data)

    await run_in_threadpool(_escribir)

    fila.orden_path = destino.replace("\\", "/")
    await db.commit()
    await db.refresh(fila)
    descripciones = await _descripciones(db, [fila])
    return _to_dict(fila, descripciones.get(fila.id_detalle_prestaciones, ""))


async def eliminar_prestacion(db: AsyncSession, prestacion_id: int, nro_socio: int) -> None:
    """Baja lógica: la fila queda con `validacion_anulada=1` y `estado='X'` (el
    soft-delete de facturación), así deja de sumar en la factura y en la
    liquidación. No se borra: la traza de lo que la O.S. contestó se conserva.

    Si la prestación tenía una autorización de Sancor, primero se la anula allá
    (ZQA^Z04). Es lo que hace el legacy en `borra_atencion_colegio_sancor.php`:
    si sólo la borráramos acá, la autorización quedaría viva en la obra social.
    """
    fila = await _prestacion_del_socio(db, prestacion_id, nro_socio)
    obra_social_id = int(fila.cod_obr)
    if await periodo_cerrado(db, obra_social_id, fila.periodo):
        raise HTTPException(409, "El período ya está cerrado: no se puede eliminar.")

    if (
        obra_social_id == SANCOR_OS
        and fila.validacion_estado == "autorizada"
        and fila.autorizacion
    ):
        medico = await get_medico(db, int(fila.cod_med))
        try:
            res = await sancor.anular(
                nro_autorizacion=fila.autorizacion,
                nro_matricula=medico.MATRICULA_PROV,
            )
        except sancor.SancorError as e:
            # No la damos de baja acá si no pudimos anularla en Sancor: quedarían
            # descalzadas y el afiliado con el cupo consumido.
            raise HTTPException(
                502,
                f"No se pudo anular la autorización en Sancor, así que no se eliminó: {e}",
            ) from e

        traza = dict(fila.validacion_respuesta or {})
        traza["anulacion"] = {"modo": res.modo, "respuesta": res.crudo}
        fila.validacion_respuesta = traza
        fila.validacion_detalle = res.estado_detalle[:255]

    # Nobis: también hay que anular la orden allá. Ojo con la diferencia contra
    # Sancor — acá NO alcanza con las autorizadas: una orden en `P-Pendiente`
    # existe igual en Nobis y hay que darla de baja, si no queda viva.
    if obra_social_id == NOBIS_OS and fila.validacion_estado in ("autorizada", "pendiente"):
        traza = dict(fila.validacion_respuesta or {})
        # AnularOrdenNroCod exige pCodAut; el número de orden es opcional.
        cod_aut = (traza.get("cod_autorizacion") or "").strip()
        if cod_aut:
            try:
                res = await nobis.anular_orden(
                    cod_autorizacion=cod_aut,
                    nro_orden=fila.autorizacion or "",
                )
            except nobis.NobisError as e:
                raise HTTPException(
                    502,
                    f"No se pudo anular la orden en Nobis, así que no se eliminó: {e}",
                ) from e

            traza["anulacion"] = {"modo": res.modo, "respuesta": res.crudo}
            fila.validacion_respuesta = traza
            fila.validacion_detalle = res.estado_detalle[:255]
        else:
            # Sin cod_aut no hay forma de anularla en Nobis. Se da de baja acá
            # igual —si no, la prestación queda trabada para siempre— pero se
            # deja dicho, porque alguien va a tener que anularla a mano.
            traza["anulacion"] = {
                "pendiente_en_nobis": True,
                "motivo": "La orden no guardó cod_autorizacion: anular manualmente en Nobis.",
            }
            fila.validacion_respuesta = traza

    facturaba = fila.estado == DETALLE_ACTIVO
    fila.validacion_anulada = True
    fila.estado = DETALLE_FUERA_DE_FACTURA
    await db.flush()
    if facturaba:
        # Si era la última prestación abierta del período, se elimina la cabecera
        # abierta (mismo invariante que `anular_prestacion` de facturación).
        await _cleanup_factura_si_vacia(db, fila.cod_obr, fila.periodo)
    await db.commit()
