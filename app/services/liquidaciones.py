# app/services/liquidaciones.py
from __future__ import annotations
from typing import Dict, Any, List, Optional, Set, Tuple
from decimal import Decimal
import re, datetime
from sqlalchemy import select, or_, and_, exists
from sqlalchemy.ext.asyncio import AsyncSession

# modelos legados mapeados por sqlacodegen (ajusta nombres si difieren)
from app.db.models import GuardarAtencion, ObrasSociales, ListadoMedico, DetalleLiquidacion


# ==============================
# Helpers (nivel módulo)
# ==============================

_PERIODO_RX = re.compile(r"^\s*(\d{4})[-/](\d{1,2})\s*$")

def normalizar_periodo(periodo: str) -> Optional[str]:
    """
    Acepta 'YYYY-MM' o 'YYYY/M' y devuelve 'YYYY-MM'. Retorna None si es inválido.
    """
    if not isinstance(periodo, str):
        return None
    match = _PERIODO_RX.match(periodo)
    if not match:
        return None
    anio, mes = int(match.group(1)), int(match.group(2))
    if anio < 1900 or anio > 3000 or not (1 <= mes <= 12):
        return None
    return f"{anio:04d}-{mes:02d}"

def separar_anio_mes(periodo_normalizado: str) -> Tuple[int, int]:
    """
    'YYYY-MM' -> (YYYY, MM)
    """
    anio_str, mes_str = periodo_normalizado.split("-")
    return int(anio_str), int(mes_str)

def periodo_desde_fecha(fecha: Optional[datetime.date | str]) -> Optional[str]:
    """
    date|str -> 'YYYY-MM' | None
    """
    if not fecha:
        return None
    if isinstance(fecha, datetime.date):
        return f"{fecha.year:04d}-{fecha.month:02d}"
    if isinstance(fecha, str) and len(fecha) >= 7:
        return fecha[:7]
    return None

def to_int_id(value: Any) -> Optional[int]:
    try:
        i = int(value)
        return i if i > 1 else None
    except (TypeError, ValueError):
        return None

def to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def agregar_prestacion_agrupada(
    agrupado_por_medico: Dict[int, Dict[str, Any]],
    user_id: Optional[int],
    user_nombre: Optional[str],
    obra_social_nombre: str,
    periodo_anio_mes: str,
    id_atencion: int,
    codigo_prestacion: str,
    fecha_periodo_derivada: Optional[str],
    bruto: float,
    debitos: float = 0.0,
    descuentos: float = 0.0,
    totales_resumen: Optional[Dict[str, float]] = None,  # si lo pasás, también actualiza el global
) -> Dict[int, Dict[str, Any]]:
    """
    Inserta la prestación en la estructura medico -> obra_social -> periodo
    y actualiza totales de periodo, de la obra social y del médico.
    Si se recibe `totales_resumen`, también actualiza los totales globales.
    """
    if user_id is None:
        return agrupado_por_medico  # sin id no agrupamos

    clave_medico = user_id
    nombre_final = (user_nombre or "").strip() or f"Médico {user_id}"

    # bucket del médico (incluye totales del médico)
    grupo_medico = agrupado_por_medico.setdefault(
        clave_medico,
        {
            "medico_id": user_id,
            "medico_nombre": nombre_final,
            "totales_medico": {"bruto": 0.0, "descuentos": 0.0, "debitos": 0.0, "neto": 0.0},
            "obras_sociales": {}
        }
    )

    # bucket de la obra social dentro del médico (incluye totales de la OS)
    grupo_obra_social = grupo_medico["obras_sociales"].setdefault(
        obra_social_nombre,
        {"totales_obra_social": {"bruto": 0.0, "debitos": 0.0, "neto": 0.0}, "periodos": {}}
    )

    # bucket del periodo dentro de la OS
    periodos = grupo_obra_social["periodos"]
    grupo_periodo = periodos.setdefault(
        periodo_anio_mes,
        {"periodo": periodo_anio_mes, "totales": {"bruto": 0.0, "descuentos": 0.0, "debitos": 0.0 ,"neto": 0.0}, "prestaciones": []}
    )


    # agregar prestación (actor)
    grupo_periodo["prestaciones"].append({
        "id_atencion": id_atencion,
        "codigo_prestacion": codigo_prestacion,
        "fecha": fecha_periodo_derivada,
        "bruto": bruto,
    })

    # totales por periodo
    t_per = grupo_periodo["totales"]
    t_per["bruto"] += bruto
    t_per["descuentos"] += descuentos
    t_per["debitos"] += debitos

    neto = bruto - (descuentos + debitos)
    t_per["neto"] += neto

    # totales por obra social (del médico)
    t_os = grupo_obra_social["totales_obra_social"]
    t_os["bruto"] += bruto
    t_os["debitos"] += debitos

    neto = bruto - debitos
    t_os["neto"] += neto

    # totales del médico
    t_med = grupo_medico["totales_medico"]
    t_med["bruto"] += bruto
    t_med["descuentos"] += descuentos
    t_med["debitos"] += debitos
    neto = bruto - (descuentos + debitos)
    t_med["neto"] += neto

    # totales globales (opcional)
    if totales_resumen is not None:
        totales_resumen["bruto"] += bruto
        totales_resumen["descuentos"] += descuentos
        totales_resumen["debitos"] += debitos
        totales_resumen["neto"] += neto

    return agrupado_por_medico

# =====================================================
# Servicio principal: periodos por obra social (async)
# =====================================================

async def generar_preview(
    db: AsyncSession,
    obra_sociales_con_periodos: Dict[int, List[str]],  # {300: ['2025-04','2025-07'], 412: ['2025-06','2025-07']}
) -> Dict[str, Any]:

    # 1) Validaciones y normalización de periodos por OS =========================
    if not obra_sociales_con_periodos:
        return {"status": "error", "message": "Debe indicar al menos una obra social con periodos."}

    periodos_por_obra_social_normalizados: Dict[int, List[str]] = {}
    for obra_social_id, lista_periodos in obra_sociales_con_periodos.items():
        if not lista_periodos:
            return {"status": "error", "message": f"Verifica que todas las obras sociales tengan periodos."}
        normalizados = [normalizar_periodo(p) for p in lista_periodos]
        if any(p is None for p in normalizados):
            return {"status": "error", "message": f"Periodos inválidos para la OS {obra_social_id}; use YYYY-MM."}
        # eliminar duplicados preservando orden
        periodos_por_obra_social_normalizados[int(obra_social_id)] = list(dict.fromkeys(normalizados))

    # 2) Resolver nombres de las obras sociales solicitadas =====================
    codigos_solicitados: Set[int] = set(periodos_por_obra_social_normalizados.keys())
    consulta_obras_sociales = (
        select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
        .where(ObrasSociales.NRO_OBRASOCIAL.in_(codigos_solicitados))
    )
    resultado_obras_sociales = await db.execute(consulta_obras_sociales)
    obras_sociales_en_bd = resultado_obras_sociales.all()

    mapa_codigo_a_nombre: Dict[int, str] = {codigo: nombre for (codigo, nombre) in obras_sociales_en_bd}
    if not mapa_codigo_a_nombre:
        return {"status": "error", "message": "No se encontraron obras sociales válidas."}

    codigos_encontrados = set(mapa_codigo_a_nombre.keys())
    codigos_desconocidos = sorted(codigos_solicitados - codigos_encontrados)
    if codigos_desconocidos:
        return {"status": "error", "message": f"Códigos de obra social inexistentes: {codigos_desconocidos}"}

    # 3) WHERE compuesto: (OS=X AND (periodo_1 OR periodo_2 ...)) OR (OS=Y AND ...) + NOT EXISTS
    condiciones_por_obra_social = []
    for obra_social_id, periodos_normalizados in periodos_por_obra_social_normalizados.items():
        anios_meses = [separar_anio_mes(p) for p in periodos_normalizados]
        condicion_periodos = or_(*[
            and_(GuardarAtencion.ANIO_PERIODO == anio, GuardarAtencion.MES_PERIODO == mes)
            for anio, mes in anios_meses
        ])
        condiciones_por_obra_social.append(
            and_(GuardarAtencion.NRO_OBRA_SOCIAL == obra_social_id, condicion_periodos)
        )

    condicion_compuesta = or_(*condiciones_por_obra_social)
    existe_liquidacion_detalle = exists().where(
        DetalleLiquidacion.prestacion_id == GuardarAtencion.ID
    )

    consulta_prestaciones = (
        select(
            GuardarAtencion.ID.label("id_atencion"),
            GuardarAtencion.NRO_SOCIO.label("medico_id"),
            GuardarAtencion.NOMBRE_PRESTADOR.label("medico_nombre"),
            GuardarAtencion.NRO_OBRA_SOCIAL.label("obra_social_id"),
            GuardarAtencion.CODIGO_PRESTACION.label("codigo_prestacion"),
            GuardarAtencion.FECHA_PRESTACION.label("fecha_prestacion"),
            GuardarAtencion.ANIO_PERIODO.label("anio_periodo"),
            GuardarAtencion.MES_PERIODO.label("mes_periodo"),
            GuardarAtencion.VALOR_CIRUJIA.label("valor_cirugia"),
            GuardarAtencion.GASTOS.label("gastos"),
            GuardarAtencion.CANTIDAD.label("cantidad"),
            GuardarAtencion.CANT_TRATAMIENTO.label("cantidad_tratamiento"),
            GuardarAtencion.AYUDANTE.label("nro_socio_ayudante"),
            GuardarAtencion.NOMBRE_AYUDANTE.label("nombre_ayudante"),
            GuardarAtencion.AYUDANTE_2.label("nro_socio_ayudante_2"),
            GuardarAtencion.NOMBRE_AYUDANTE_2.label("nombre_ayudante_2"),
            GuardarAtencion.VALOR_AYUDANTE.label("valor_ayudante"),
            GuardarAtencion.VALOR_AYUDANTE_2.label("valor_ayudante_2"),
            GuardarAtencion.NRO_CONSULTA.label("nro_consulta"),
        )
        .where(
            condicion_compuesta,
            ~existe_liquidacion_detalle,  # excluir ya liquidadas
        )
    )

    prestaciones_rows = (await db.execute(consulta_prestaciones)).mappings().all()

    # 5) Post-proceso y agrupación (sin omitidas, sin retenciones/ajustes) ======
    prestacion_actor_vista: set[Tuple[int, int]] = set()  # (id_atencion, actor_id)
    agrupado_por_medico: Dict[int, Dict[str, Any]] = {}
    totales_resumen = {"bruto": 0.0, "descuentos": 0.0,"debitos": 0.0, "neto": 0.0}
    total_incluidas = 0

    for row in prestaciones_rows:
        if row.get("nro_consulta") in ["0", "1"] or row.get("nro_consulta") is None:
            continue

        id_atencion = to_int_id(row.get("id_atencion"))
        if id_atencion is None:
            continue

        periodo_anio_mes = f'{int(row["anio_periodo"]):04d}-{int(row["mes_periodo"]):02d}'
        fecha_periodo_derivada = row.get("fecha_prestacion")

        obra_social_id = row.get("obra_social_id")
        if obra_social_id is None:
            continue
        obra_social_nombre = mapa_codigo_a_nombre.get(obra_social_id, str(obra_social_id))

        cantidad = int(row.get("cantidad") or 1)
        cantidad_tratamiento = int(row.get("cantidad_tratamiento") or 1)
        factor = cantidad * cantidad_tratamiento

        codigo_prestacion = row["codigo_prestacion"]

        # ----------------- Actor: Médico principal -----------------
        actor_id = to_int_id(row.get("medico_id"))
        actor_nombre = (row.get("medico_nombre") or "").strip().title() or None

        if actor_id is not None:
            clave = (id_atencion, actor_id)
            if clave not in prestacion_actor_vista:
                prestacion_actor_vista.add(clave)

                valor_cirugia = to_decimal(row.get("valor_cirugia"))
                bruto_actor = float(valor_cirugia * Decimal(factor))

                agrupado_por_medico = agregar_prestacion_agrupada(
                    agrupado_por_medico,
                    user_id=actor_id,
                    user_nombre=actor_nombre,
                    obra_social_nombre=obra_social_nombre,
                    periodo_anio_mes=periodo_anio_mes,
                    id_atencion=id_atencion,
                    codigo_prestacion=codigo_prestacion,
                    fecha_periodo_derivada=fecha_periodo_derivada,
                    bruto=bruto_actor,
                    descuentos=0.0,
                    debitos=0.0,
                    totales_resumen=totales_resumen,
                )
                total_incluidas += 1

        # ----------------- Actor: Ayudante 1 -----------------
        actor_id = to_int_id(row.get("nro_socio_ayudante"))
        actor_nombre = (row.get("nombre_ayudante") or "").strip() or None
        if actor_id is not None:
            clave = (id_atencion, actor_id)
            if clave not in prestacion_actor_vista:
                prestacion_actor_vista.add(clave)

                valor_ayudante = to_decimal(row.get("valor_ayudante"))
                bruto_actor = float(valor_ayudante)  # <-- si debe multiplicar por factor: float(valor_ayudante * Decimal(factor))

                agrupado_por_medico = agregar_prestacion_agrupada(
                    agrupado_por_medico,
                    user_id=actor_id,
                    user_nombre=actor_nombre,
                    obra_social_nombre=obra_social_nombre,
                    periodo_anio_mes=periodo_anio_mes,
                    id_atencion=id_atencion,
                    codigo_prestacion=codigo_prestacion,
                    fecha_periodo_derivada=fecha_periodo_derivada,
                    bruto=bruto_actor,
                    descuentos=0.0,
                    totales_resumen=totales_resumen,
                )
                total_incluidas += 1

        # ----------------- Actor: Ayudante 2 -----------------
        actor_id = to_int_id(row.get("nro_socio_ayudante_2"))
        actor_nombre = (row.get("nombre_ayudante_2") or "").strip() or None
        if actor_id is not None:
            clave = (id_atencion, actor_id)
            if clave not in prestacion_actor_vista:
                prestacion_actor_vista.add(clave)

                valor_ayudante_2 = to_decimal(row.get("valor_ayudante_2"))
                bruto_actor = float(valor_ayudante_2)  # <-- si debe multiplicar por factor: float(valor_ayudante_2 * Decimal(factor))

                agrupado_por_medico = agregar_prestacion_agrupada(
                    agrupado_por_medico,
                    user_id=actor_id,
                    user_nombre=actor_nombre,
                    obra_social_nombre=obra_social_nombre,
                    periodo_anio_mes=periodo_anio_mes,
                    id_atencion=id_atencion,
                    codigo_prestacion=codigo_prestacion,
                    fecha_periodo_derivada=fecha_periodo_derivada,
                    bruto=bruto_actor,
                    descuentos=0.0,
                    totales_resumen=totales_resumen,
                )
                total_incluidas += 1
        


    # 6) Transformar agrupado a la estructura solicitada ========================
    por_medico_salida: List[Dict[str, Any]] = []
    for _, datos_medico in agrupado_por_medico.items():
        obras_sociales_salida = []
        for nombre_os, datos_os in datos_medico["obras_sociales"].items():
            periodos_list = [v for _, v in sorted(datos_os["periodos"].items())]
            obras_sociales_salida.append({
                "obra_social": nombre_os,
                "periodos": periodos_list
            })
        por_medico_salida.append({
            "medico_id": datos_medico["medico_id"],
            "medico_nombre": datos_medico["medico_nombre"],
            "obras_sociales": obras_sociales_salida
        })


    # Unión de todos los periodos normalizados (para el bloque "solicitud")
    union_periodos_normalizados = sorted({
        periodo for lista in periodos_por_obra_social_normalizados.values() for periodo in lista
    })
    return {
        "status": "ok",
        "solicitud": {
            "obra_sociales": [mapa_codigo_a_nombre[c] for c in sorted(mapa_codigo_a_nombre.keys())],
            "periodos_normalizados": union_periodos_normalizados
        },
        "resumen": {
            "total_prestaciones_incluidas": total_incluidas,
            "total_bruto": round(totales_resumen["bruto"], 2),
            "total_descuentos": round(totales_resumen["descuentos"], 2),
            "total_neto": round(totales_resumen["neto"], 2),
        },
        "por_medico": por_medico_salida
    }




# async def generar_preview(
#     db: AsyncSession,
#     obra_sociales_con_periodos: Dict[int, List[str]],  # {300: ['2025-04','2025-07'], 412: ['2025-06','2025-07']}
# ) -> Dict[str, Any]:

#     # 1) Validaciones y normalización de periodos por OS =========================
#     if not obra_sociales_con_periodos:
#         return {"status": "error", "message": "Debe indicar al menos una obra social con periodos."}

#     periodos_por_obra_social_normalizados: Dict[int, List[str]] = {}
#     for obra_social_id, lista_periodos in obra_sociales_con_periodos.items():
#         if not lista_periodos:
#             return {"status": "error", "message": f"Verifica que todas las obras sociales tengan periodos."}
#         normalizados = [normalizar_periodo(p) for p in lista_periodos]
#         if any(p is None for p in normalizados):
#             return {"status": "error", "message": f"Periodos inválidos para la OS {obra_social_id}; use YYYY-MM."}
#         # eliminar duplicados preservando orden
#         periodos_por_obra_social_normalizados[int(obra_social_id)] = list(dict.fromkeys(normalizados))

#     # 2) Resolver nombres de las obras sociales solicitadas =====================
#     codigos_solicitados: Set[int] = set(periodos_por_obra_social_normalizados.keys())
#     consulta_obras_sociales = (
#         select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
#         .where(ObrasSociales.NRO_OBRASOCIAL.in_(codigos_solicitados))
#     )
#     resultado_obras_sociales = await db.execute(consulta_obras_sociales)
#     obras_sociales_en_bd = resultado_obras_sociales.all()

#     mapa_codigo_a_nombre: Dict[int, str] = {codigo: nombre for (codigo, nombre) in obras_sociales_en_bd}
#     if not mapa_codigo_a_nombre:
#         return {"status": "error", "message": "No se encontraron obras sociales válidas."}

#     codigos_encontrados = set(mapa_codigo_a_nombre.keys())
#     codigos_desconocidos = sorted(codigos_solicitados - codigos_encontrados)
#     if codigos_desconocidos:
#         return {"status": "error", "message": f"Códigos de obra social inexistentes: {codigos_desconocidos}"}

#     # 3) WHERE compuesto: (OS=X AND (periodo_1 OR periodo_2 ...)) OR (OS=Y AND ...) + NOT EXISTS
#     condiciones_por_obra_social = []
#     for obra_social_id, periodos_normalizados in periodos_por_obra_social_normalizados.items():
#         anios_meses = [separar_anio_mes(p) for p in periodos_normalizados]
#         condicion_periodos = or_(*[
#             and_(GuardarAtencion.ANIO_PERIODO == anio, GuardarAtencion.MES_PERIODO == mes)
#             for anio, mes in anios_meses
#         ])
#         condiciones_por_obra_social.append(
#             and_(GuardarAtencion.NRO_OBRA_SOCIAL == obra_social_id, condicion_periodos)
#         )

#     condicion_compuesta = or_(*condiciones_por_obra_social)
#     existe_liquidacion_detalle = exists().where(
#         DetalleLiquidacion.prestacion_id == GuardarAtencion.ID
#     )

#     consulta_prestaciones = (
#         select(
#             GuardarAtencion.ID.label("id_atencion"),
#             GuardarAtencion.NRO_SOCIO.label("medico_id"),
#             GuardarAtencion.NRO_OBRA_SOCIAL.label("obra_social_id"),
#             GuardarAtencion.CODIGO_PRESTACION.label("codigo_prestacion"),
#             GuardarAtencion.FECHA_PRESTACION.label("fecha_prestacion"),
#             GuardarAtencion.ANIO_PERIODO.label("anio_periodo"),
#             GuardarAtencion.MES_PERIODO.label("mes_periodo"),
#             GuardarAtencion.VALOR_CIRUJIA.label("valor_cirugia"),
#             GuardarAtencion.GASTOS.label("gastos"),
#             GuardarAtencion.CANTIDAD.label("cantidad"),
#             GuardarAtencion.CANT_TRATAMIENTO.label("cantidad_tratamiento"),
#         )
#         .where(
#             condicion_compuesta,
#             ~existe_liquidacion_detalle,  # excluir ya liquidadas
#         )
#     )

#     prestaciones_rows = (await db.execute(consulta_prestaciones)).mappings().all()

#     # 4) Nombres de médicos (lookup) ============================================
#     medico_ids: Set[int] = {int(r["medico_id"]) for r in prestaciones_rows if r["medico_id"] is not None}
#     mapa_medicos: Dict[int, str] = {}
#     if medico_ids:
#         consulta_medicos = (
#             select(ListadoMedico.NRO_SOCIO, ListadoMedico.NOMBRE)
#             .where(ListadoMedico.NRO_SOCIO.in_(medico_ids))
#         )
#         resultado_medicos = await db.execute(consulta_medicos)
#         mapa_medicos = {row[0]: row[1] for row in resultado_medicos.all()}

#     # 5) Post-proceso y agrupación (sin omitidas, sin retenciones/ajustes) ======
#     atenciones_vistas: Set[int] = set()
#     agrupado_por_medico: Dict[int, Dict[str, Any]] = {}
#     totales_resumen = {"bruto": 0.0, "descuentos": 0.0, "neto": 0.0}
#     total_incluidas = 0

#     for row in prestaciones_rows:
#         id_atencion = int(row["id_atencion"])
#         if id_atencion in atenciones_vistas:
#             continue  # deduplicación silenciosa
#         atenciones_vistas.add(id_atencion)

#         periodo_anio_mes = f'{int(row["anio_periodo"]):04d}-{int(row["mes_periodo"]):02d}'
#         fecha_periodo_derivada = periodo_desde_fecha(row["fecha_prestacion"])

#         cantidad = int(row["cantidad"] or 1)
#         cantidad_tratamiento = int(row["cantidad_tratamiento"] or 1)
#         bruto = float(Decimal(row["valor_cirugia"] or 0) + Decimal(row["gastos"] or 0)) * (cantidad_tratamiento * cantidad)
#         descuentos = 0.0
#         neto = bruto - descuentos

#         medico_id = int(row["medico_id"])
#         obra_social_id = int(row["obra_social_id"])
#         obra_social_nombre = mapa_codigo_a_nombre.get(obra_social_id, str(obra_social_id))

#         grupo_medico = agrupado_por_medico.setdefault(
#             medico_id,
#             {
#                 "medico_id": medico_id,
#                 "medico_nombre": mapa_medicos.get(medico_id, f"Médico {medico_id}"),
#                 "obras_sociales": {}
#             }
#         )

#         grupo_obra_social = grupo_medico["obras_sociales"].setdefault(obra_social_nombre, {})
#         grupo_periodo = grupo_obra_social.setdefault(
#             periodo_anio_mes,
#             {
#                 "periodo": periodo_anio_mes,
#                 "totales": {"bruto": 0.0, "descuentos": 0.0, "neto": 0.0},
#                 "prestaciones": []
#             }
#         )

#         grupo_periodo["prestaciones"].append({
#             "id_atencion": id_atencion,
#             "codigo_prestacion": row["codigo_prestacion"],
#             "fecha": fecha_periodo_derivada,
#             "bruto": bruto,
#             "descuentos": descuentos,
#             "neto": neto,
#         })

#         totales_periodo = grupo_periodo["totales"]
#         totales_periodo["bruto"] += bruto
#         totales_periodo["descuentos"] += descuentos
#         totales_periodo["neto"] += neto

#         totales_resumen["bruto"] += bruto
#         totales_resumen["descuentos"] += descuentos
#         totales_resumen["neto"] += neto
#         total_incluidas += 1

#     # 6) Transformar agrupado a la estructura solicitada ========================
#     por_medico_salida: List[Dict[str, Any]] = []
#     for medico_id, datos_medico in agrupado_por_medico.items():
#         obras_sociales_salida = [
#             {"obra_social": nombre_os, "periodos": list(mapa_periodos.values())}
#             for nombre_os, mapa_periodos in datos_medico["obras_sociales"].items()
#         ]
#         por_medico_salida.append({
#             "medico_id": datos_medico["medico_id"],
#             "medico_nombre": datos_medico["medico_nombre"],
#             "obras_sociales": obras_sociales_salida
#         })

#     # Unión de todos los periodos normalizados (para el bloque "solicitud")
#     union_periodos_normalizados = sorted({
#         periodo for lista in periodos_por_obra_social_normalizados.values() for periodo in lista
#     })

#     return {
#         "status": "ok",
#         "solicitud": {
#             "obra_sociales": [mapa_codigo_a_nombre[c] for c in sorted(mapa_codigo_a_nombre.keys())],
#             "periodos_normalizados": union_periodos_normalizados
#         },
#         "resumen": {
#             "total_prestaciones_incluidas": total_incluidas,
#             "total_bruto": round(totales_resumen["bruto"], 2),
#             "total_descuentos": round(totales_resumen["descuentos"], 2),
#             "total_neto": round(totales_resumen["neto"], 2),
#         },
#         "por_medico": por_medico_salida
#     }

