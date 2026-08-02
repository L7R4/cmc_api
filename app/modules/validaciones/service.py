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

Alcance actual: obras sociales de **carga manual** (Boreal 285, Omint 243) y
Sancor (411) en línea. Las demás todavía no están implementadas — ver
`OBRAS_MANUALES` y `obra_manual_o_error()`.
"""
import datetime
import os
import shutil
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.files import UPLOAD_ROOT
from app.common.money import quantize_money
from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.db.models.nomenclador_cmc import NomencladorCMC
from app.modules.facturacion.service import (
    ORIGEN_MEDICO,
    _cleanup_factura_si_vacia,
    _ensure_factura_abierta,
    _gate_carga,
    _get_factura,
    asegurar_periodo_medico_vigente,
    calcular_importe_total,
    derivar_tipo,
    get_periodo_medico,
    resolver_precio,
    tpo_funcion_derivado,
)
from app.modules.validaciones import sancor

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

# Obras sociales cuyo validador consultamos en línea. Por ahora sólo Sancor.
SANCOR_OS = 411
OBRAS_ONLINE = {SANCOR_OS: "Sancor Salud"}


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


async def _descripciones(db: AsyncSession, codigos: list[str]) -> dict[str, str]:
    """Descripción del nomenclador para un lote de códigos. `detalle_facturacion`
    guarda el código, no el texto: se resuelve al listar para no desnormalizar."""
    if not codigos:
        return {}
    filas = (
        await db.execute(
            select(NomencladorCMC.codigo, NomencladorCMC.descripcion).where(
                NomencladorCMC.codigo.in_(set(codigos))
            )
        )
    ).all()
    return {c: (d or "") for c, d in filas}


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
        "orden": f.orden_path,
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
    descripciones = await _descripciones(db, [f.cod_nom or "" for f in filas])
    return [_to_dict(f, descripciones.get(f.cod_nom or "", "")) for f in filas]


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

    stmt = select(NomencladorCMC.codigo, NomencladorCMC.descripcion)
    termino = (q or "").strip()
    if termino:
        like = f"%{termino}%"
        stmt = stmt.where(
            NomencladorCMC.codigo.like(like) | NomencladorCMC.descripcion.like(like)
        )
    stmt = stmt.order_by(NomencladorCMC.codigo).limit(limite)
    filas = (await db.execute(stmt)).all()

    salida: list[dict] = []
    for codigo, descripcion in filas:
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

    fila = DetalleFacturacionCMC(
        periodo=periodo,
        cod_obr=str(obra_social_id),
        cod_med=str(medico.NRO_SOCIO),
        cod_nom=codigo,
        nro_orden="0",  # placeholder — la columna es NOT NULL; se espeja el PK abajo
        tipo=await derivar_tipo(db, codigo, bool(medico.es_organizacion)),
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

    if (archivo.content_type or "") not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(422, "La orden tiene que ser un PDF.")

    destino_dir = os.path.join(UPLOAD_ROOT, "validaciones", str(nro_socio))
    os.makedirs(destino_dir, exist_ok=True)

    base = os.path.basename(archivo.filename or "orden.pdf")
    seguro = "".join(c for c in base if c.isalnum() or c in "._-").strip() or "orden.pdf"
    nombre = f"{int(datetime.datetime.now().timestamp())}_{seguro}"[:120]
    destino = os.path.join(destino_dir, nombre)

    def _escribir() -> None:
        archivo.file.seek(0)
        with open(destino, "wb") as f:
            shutil.copyfileobj(archivo.file, f, length=1024 * 1024)

    await run_in_threadpool(_escribir)

    fila.orden_path = destino.replace("\\", "/")
    await db.commit()
    await db.refresh(fila)
    descripciones = await _descripciones(db, [fila.cod_nom or ""])
    return _to_dict(fila, descripciones.get(fila.cod_nom or "", ""))


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

    facturaba = fila.estado == DETALLE_ACTIVO
    fila.validacion_anulada = True
    fila.estado = DETALLE_FUERA_DE_FACTURA
    await db.flush()
    if facturaba:
        # Si era la última prestación abierta del período, se elimina la cabecera
        # abierta (mismo invariante que `anular_prestacion` de facturación).
        await _cleanup_factura_si_vacia(db, fila.cod_obr, fila.periodo)
    await db.commit()
