"""Las columnas de la planilla de importación de prestaciones.

Es la única fuente del formato: de acá salen la plantilla que se descarga
(`plantilla.py`) y los nombres que el front busca al leer el archivo. Cambiar una
columna es cambiarla acá y regenerar la plantilla.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Columna:
    clave: str           # nombre en la planilla y clave del JSON que manda el front (en minúsculas)
    obligatoria: bool
    ancho: int
    formato: str         # formato de celda de Excel
    descripcion: str
    ejemplo: str


TIPOS = ("CONSULTA", "PRACTICA", "HONORARIOS INDIVIDUALES", "SANATORIO")
VIAS = ("T", "L")
CALCULOS = ("A", "M")
ROLES = ("MEDICO", "AYUDANTE", "PEDIATRA")

COLUMNAS: tuple[Columna, ...] = (
    Columna("TIPO", True, 24, "@",
            "CONSULTA · PRACTICA · HONORARIOS INDIVIDUALES · SANATORIO. SANATORIO: la clínica factura y el médico "
            "ejecuta. HONORARIOS INDIVIDUALES: el médico factura y la clínica es sólo el lugar. CONSULTA y PRACTICA "
            "no llevan clínica; el sistema controla que coincidan con la categoría del código.", "CONSULTA"),
    Columna("NRO_SOCIO", True, 11, "0",
            "NRO_SOCIO del médico que hizo la prestación y cobra (en SANATORIO también: es el ejecutor).", "1084"),
    Columna("MATRICULA", False, 11, "0",
            "Matrícula del médico. Sólo control: si no es la del NRO_SOCIO, la fila queda en «Elegir socio».", "1827"),
    Columna("CLINICA", False, 10, "0",
            "NRO_SOCIO de la clínica. Obligatorio en SANATORIO; opcional en HONORARIOS INDIVIDUALES (es el lugar).", "9882"),
    Columna("FECHA", False, 12, "DD/MM/YYYY",
            "Fecha de la práctica (dd/mm/aaaa). Vacía: se cotiza con la fecha de hoy y la fila queda sin fecha.", "08/09/2026"),
    Columna("AUTORIZACION", False, 14, "@",
            "Número de autorización de la obra social (hasta 30 caracteres).", "14337840"),
    Columna("CODIGO", True, 10, "@",
            "Código del nomenclador, como texto (respeta ceros a la izquierda).", "420101"),
    Columna("VIA", False, 6, "@",
            "Vía quirúrgica: T tradicional · L laparoscópica. Vacía: T.", "T"),
    Columna("CANTIDAD", True, 9, "0",
            "Cantidad (entero ≥ 1). El importe se multiplica por cantidad × sesión.", "1"),
    Columna("SESION", False, 8, "0", "Sesión (entero ≥ 1). Vacía: 1.", "1"),
    Columna("PORCENTAJE", False, 11, "0", "Porcentaje a facturar (entero ≥ 1). Vacío: 100.", "100"),
    Columna("NRO_AFILIADO", False, 15, "@",
            "DNI o número de afiliado del paciente (como texto). Si está en el padrón, el nombre sale de ahí.",
            "29895801011"),
    Columna("AFILIADO", False, 32, "@",
            "Apellido y nombre del paciente. Se usa si el NRO_AFILIADO no está en el padrón o no se informa.",
            "RODRIGUEZ, MARI MARIA SOLEDAD"),
    Columna("CALCULO", False, 8, "@",
            "A = automático (el sistema cotiza con el valor vigente; los importes > 0 sólo marcan qué concepto se "
            "factura) · M = manual (se guardan los importes de la fila tal cual). Vacío: A.", "M"),
    Columna("HONORARIOS", False, 13, "#,##0.00", "Honorarios por unidad (sin multiplicar por cantidad).", "24000"),
    Columna("GASTOS", False, 13, "#,##0.00", "Gastos por unidad.", "0"),
    Columna("AYUDANTE", False, 13, "#,##0.00",
            "Importe de ayudantía por unidad. Sólo en la fila del ayudante (con honorarios y gastos en 0).", "0"),
    Columna("COSEGURO", False, 11, "#,##0.00",
            "Lo que paga el afiliado por unidad; se descuenta del total. Vacío: el sugerido por el valor (0 en manual).",
            "0"),
    Columna("EQUIPO", False, 9, "@",
            "Clave libre para agrupar un equipo quirúrgico: las filas con la misma clave se cargan juntas. "
            "Vacía: prestación individual.", "E1"),
    Columna("ROL", False, 10, "@",
            "Rol dentro del equipo: MEDICO (cirujano, cabeza) · AYUDANTE · PEDIATRA. Vacío: MEDICO.", "AYUDANTE"),
    Columna("COMENTARIOS", False, 40, "@",
            "Texto libre. No se guarda: se muestra en la previsualización para ubicar la fila.",
            "Agregada desde factura 1107"),
)

HOJA = "Prestaciones"
CLAVES = tuple(c.clave for c in COLUMNAS)
