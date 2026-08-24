"""Lecturas del panel del prestador: listados de prestaciones/períodos y
búsqueda de códigos. Comparten filtro y armado de vista sin importar la obra
social — lo específico de cada una ya quedó grabado en `detalle_facturacion`.
"""
import datetime
from typing import Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import quantize_money
from app.db.models import DetalleFacturacionCMC
from app.db.models.nomenclador_cmc import NomencladorCMC, Valor
from app.modules.facturacion.service import resolver_precio
from app.modules.nomenclador import service as service_nm
from app.modules.validaciones.core.contrato import factura_en_cero
from app.modules.validaciones.core.grabado import to_dict
from app.modules.validaciones.core.medicos import get_medico
from app.modules.validaciones.core.periodos import partes_periodo, periodo_cerrado


# Cuántas filas del catálogo se leen por cada una que se devuelve en
# `buscar_codigos`. Cubre el dedupe (fila propia de la OS + compartida) y los
# códigos que el médico no puede facturar. Subirlo mejora los resultados de un
# médico con pocas habilitaciones a costa de más consultas de precio.
_FACTOR_BARRIDO = 5


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
    return [to_dict(f, descripciones.get(f.id_detalle_prestaciones, "")) for f in filas]


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
    """Códigos del nomenclador nuevo que **ese médico puede facturar** en esa
    obra social, con su valor.

    El precio sale del mismo lookup que usa facturación, así que lo que ve el
    prestador acá es lo que después se le va a liquidar.

    Los no admitidos —sin habilitación por especialidad, sin precio vigente— no
    se devuelven. Antes salían con `admitido=False` y un motivo, y el buscador
    los pintaba en gris: el prestador no los puede usar, así que verlos sólo
    servía para que intentara elegirlos. Como efecto secundario, el `limite`
    ahora rinde — una búsqueda corta podía gastar las 20 filas en códigos
    inservibles y esconder los que sí servían.

    **Los que cotizan $ 0 tampoco se devuelven**, con el mismo criterio: es lo
    que `Contexto.precio()` rechaza con 422 al cargar, así que ofrecerlos sería
    ofrecer algo que no se puede usar. Es el caso que dejaba pasar
    `CARGA_SIN_PRECIO=true`, y el que hacía que un médico con especialidad 33
    viera `420130` a $ 0,00 y lo cargara igual.
    """
    medico = await get_medico(db, nro_socio)
    hoy = datetime.date.today()

    # Con qué código se le habla a esta obra social. Import perezoso: `obras`
    # arma los seis validadores al importarse y `pipeline` es el que lo hace en
    # el arranque — traerlo acá arriba invertiría ese orden sin necesidad.
    from app.modules.validaciones import obras

    obra = obras.POR_NRO.get(obra_social_id)
    especialidad = int(medico.NRO_ESPECIALIDAD) if medico.NRO_ESPECIALIDAD else None

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
    #
    # Se leen más filas de las que se devuelven porque de acá se cae por dos
    # motivos: el dedupe y, ahora, la habilitación. El techo es lo que acota el
    # costo — `resolver_precio` es una consulta por fila —, y el `break` corta
    # apenas se juntan `limite` códigos usables, que es el caso normal.
    stmt = stmt.order_by(
        NomencladorCMC.codigo, NomencladorCMC.obra_social_key.desc()
    ).limit(limite * _FACTOR_BARRIDO)
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
        if not precio.admitido or factura_en_cero(precio):
            continue
        # Informativo: si la O.S. exige otro código, el prestador tiene que
        # saber que lo que se autoriza allá se llama distinto. El precio y lo
        # que se factura siguen siendo los de `codigo`.
        se_envia = None
        if obra is not None:
            homologado, _ = obra.homologar(codigo, especialidad)
            se_envia = homologado if homologado != codigo else None

        salida.append(
            {
                "codigo": codigo,
                "descripcion": precio.descripcion or descripcion or "",
                "honorarios": precio.honorarios,
                "gastos": precio.gastos,
                "total": precio.honorarios + precio.gastos,
                "admitido": True,
                "motivo": precio.motivo,
                "se_envia": se_envia,
            }
        )
    return salida
