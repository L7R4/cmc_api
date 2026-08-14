"""Traducción de una fila de `detalle_facturacion` (sistema nuevo) a una fila de
`guardar_atencion` (sistema viejo).

Es una función pura: recibe lo que el pipeline ya tiene en la mano y devuelve el
objeto ORM sin tocar la base. Todo lo que consulta —el `C_P_H_S` del código— se
resuelve aparte y se le pasa como argumento, así el mapeo se puede leer y
verificar de una sola pasada.

## Las columnas que importan de verdad

De relevar los ~300 `SELECT ... FROM guardar_atencion` del PHP, el legacy filtra
y agrupa por: `EXISTE`, `MES_PERIODO`, `ANIO_PERIODO`, `NRO_OBRA_SOCIAL`,
`NRO_SOCIO`, `CODIGO_PRESTACION`, `CATEGORIA_A_B_C`, `CON_HONO_SANA`,
`NRO_CONSULTA <> 0`, `AUTO_MANUAL` y `FECHA_PRESTACION`. Y suma **una sola**
columna de plata: `SUM(VALOR_CIRUJIA)`, en 17 lugares distintos.

`ESTADODESCRIPCION`, `MENSAJE`, `PACIENTE`, `SANATORIO`, `NOMBRE_ARCHIVO` y
`TOKEN` no se filtran en ningún lado: son de pantalla. Por eso el mapeo se cuida
con las primeras y es deliberadamente simple con las segundas.
"""
import datetime
from decimal import Decimal

from app.db.models import DetalleFacturacionCMC, GuardarAtencion, ListadoMedico
from app.modules.validaciones.core.periodos import partes_periodo
from app.modules.validaciones.legacy.perfiles import PerfilLegacy

CERO = Decimal("0.00")

# `NRO_CONSULTA` es varchar y el legacy lo filtra con `<> 0`. Una prestación que
# la obra social no autorizó no tiene número de autorización: va '0' para que
# esos filtros la dejen afuera, que es exactamente lo que hace `estado='X'` del
# lado nuevo.
SIN_AUTORIZACION = "0"

# `RESULTADO` es la marca que usa el paso de período (`paso_proximo_periodo*.php`)
# para decidir qué filas arrastra al mes siguiente: mueve las que cumplen
# `RESULTADO='N'` / `RESULTADO<>'S'`.
#
# En la tabla real esta columna es un desastre — conviven 'N', 'S', '1', '0',
# '', '-', 'false' y hasta 'HC^SA', según qué PHP grabó la fila. No hay un
# criterio que recuperar. Se escribe siempre 'N', que es el único valor que
# satisface las dos condiciones del paso de período: una fila recién creada
# todavía no se procesó, así que tiene que arrastrarse como cualquier otra.
NO_PROCESADA = "N"

# `MENSAJE` es varchar(5) y el legacy lo deja en su default en todos los caminos
# salvo Nobis, que mete ahí un recorte del mensaje del servicio. No se filtra
# nunca.
MENSAJE_DEFECTO = "A"

# Relleno de `FECHASUSPENSION` (varchar(10), no es una fecha) en las obras
# sociales que no guardan la fecha ahí.
SIN_FECHA_SUSPENSION = "A"

# `CON_HONO_SANA` cuando el código no está en `codigo_descripcion`: 'C' es el
# default de la columna y lo que asume el PHP cuando no encuentra la fila.
TIPO_CODIGO_DEFECTO = "C"


def construir_fila(
    *,
    detalle: DetalleFacturacionCMC,
    medico: ListadoMedico,
    perfil: PerfilLegacy,
    con_hono_sana: str,
    hoy: datetime.date,
) -> GuardarAtencion:
    """Arma la fila de `guardar_atencion` equivalente a `detalle`.

    Las columnas que no se setean acá (ayudantes, cirujano, porcentajes,
    `CODIGO_PRESTACION_2/3`, `AYUDANTE_ACTUAL`…) quedan en el default de la
    tabla, que es exactamente lo que el legacy escribe en ellas: una validación
    es siempre una prestación simple del propio médico, sin equipo.

    Tres de esas columnas **no están declaradas en el modelo** `GuardarAtencion`
    aunque sí existen en la tabla: `AUTO_MANUAL`, `ABIERTO_CERRADO` y
    `CODICION_IVA`. Sus defaults de MySQL —`'N'`, `'A'` y `'Exento'`— son
    justamente los valores que escribe el legacy, así que omitirlas da el
    resultado correcto. Importa saberlo por `AUTO_MANUAL`: hay consultas que
    filtran `AUTO_MANUAL='N'`, o sea que no es cosmética, y acá depende del
    default de la tabla y no de este código. Si alguien completa el modelo,
    conviene setearla explícitamente.
    """
    mes, anio = partes_periodo(detalle.periodo)
    nombre_afiliado = (detalle.nom_ape_p or "")[:40]

    return GuardarAtencion(
        # ── Identificación ───────────────────────────────────────────────────
        NRO_SOCIO=_entero(detalle.cod_med),
        # Tal cual, sin traducir: los dos sistemas usan el mismo número para
        # todas las obras sociales (ver `perfiles.PerfilLegacy`).
        NRO_OBRA_SOCIAL=_entero(detalle.cod_obr),
        CODIGO_PRESTACION=(detalle.cod_nom or "")[:8],
        NRO_MATRICULA=medico.MATRICULA_PROV or 0,
        NOMBRE_PRESTADOR=(medico.NOMBRE or "")[:40],
        # `CATEGORIA_A_B_C` y `CON_HONO_SANA` son claves de agrupación de la
        # facturación del legacy: si salen mal, la prestación se factura en el
        # bloque equivocado.
        CATEGORIA_A_B_C=(medico.CATEGORIA or "A")[:1],
        CON_HONO_SANA=con_hono_sana[:1],
        NRO_ESPECIALIDAD=medico.NRO_ESPECIALIDAD or 0,
        # ── Afiliado ─────────────────────────────────────────────────────────
        # `dni_p` es el número de afiliado (con barra incluida cuando la O.S. la
        # usa: "2012871/00"). `BARRA_AFILIADO` queda en 0, igual que el legacy,
        # que tampoco la separa.
        NRO_AFILIADO=(detalle.dni_p or SIN_AUTORIZACION)[:20],
        NOMBRE_AFILIADO=nombre_afiliado,
        PACIENTE=(perfil.paciente if perfil.paciente is not None else nombre_afiliado)[:50],
        SANATORIO=perfil.sanatorio[:50],
        # ── Resultado de la validación ───────────────────────────────────────
        NRO_CONSULTA=(detalle.autorizacion or SIN_AUTORIZACION)[:16],
        ESTADODESCRIPCION=(detalle.validacion_detalle or "")[:100],
        MENSAJE=MENSAJE_DEFECTO,
        RESULTADO=NO_PROCESADA,
        # ── Período y fechas ─────────────────────────────────────────────────
        MES_PERIODO=mes,
        ANIO_PERIODO=anio,
        FECHA_PRESTACION=detalle.fecha_practica,
        FECHA_CARGA=hoy,
        # No hay cirugía, pero el legacy nunca dejó esta columna en NULL.
        FECHA_CIRUGIA=detalle.fecha_practica,
        FECHASUSPENSION=(
            detalle.fecha_practica.strftime("%Y/%m/%d")
            if perfil.fechasuspension_con_fecha and detalle.fecha_practica
            else SIN_FECHA_SUSPENSION
        ),
        # ── Importes ─────────────────────────────────────────────────────────
        # `VALOR_CIRUJIA` es la única columna que el legacy suma, y su fórmula
        # —(honorarios + gastos) × cantidad − coseguro— es exactamente la que
        # ya calculó el sistema nuevo en `importe_total`. Verificado contra
        # filas reales de las seis obras sociales.
        IMPORTE_COLEGIO=detalle.honorarios or CERO,
        GASTOS=detalle.gastos or CERO,
        COSEGURO=detalle.coseguro or CERO,
        VALOR_CIRUJIA=detalle.importe_total or CERO,
        CANTIDAD=detalle.cantidad or 1,
        CANT_TRATAMIENTO=1,
        # ── Marcas ───────────────────────────────────────────────────────────
        EXISTE="S",
        NOMBRE_ARCHIVO=perfil.nombre_archivo[:100],
        # Quién tipeó la carga, igual que `detalle_facturacion.usuario`.
        USUARIO_COLEGIO=_entero(detalle.usuario),
        # `TOKEN` es el token de la credencial del afiliado, que sólo Sancor usa
        # y que el sistema nuevo no persiste en ninguna columna propia: viaja
        # dentro del mensaje HL7 de la traza y no se puede recuperar sin
        # parsearlo. Queda en 0. No se filtra por esta columna en ningún lado
        # del legacy, así que no cambia ningún resultado.
        TOKEN=0,
    )


def _entero(valor) -> int:
    """`cod_med` y `usuario` son varchar del lado nuevo y enteros del viejo."""
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return 0
