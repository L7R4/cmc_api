"""PDF de la documentación digital de una factura.

Un solo PDF: los médicos de la A a la Z y, de cada uno, la línea «Matrícula - Nombre · Socio N»
arriba de su primera página y debajo sus documentos, uno detrás del otro, con las prestaciones en
el MISMO orden que el detalle (el orden de la vista, que llega en las mismas opciones que el
export del detalle: `armar` ya sabe ordenar y agrupar así).

Los PDF entran con todas sus páginas; las fotos (JPG/PNG) se ajustan a una página A4. Es la parte
pesada (hay que leer cada archivo): `build_pdf_documentacion` es síncrona y se corre en un hilo.

Sólo entra lo que está listo: la prestación tiene documentos y, si el código los exige, está
auditada. Lo que falta se informa aparte (`pendientes`) y el endpoint no genera el PDF mientras
haya algo pendiente.
"""
import io
import os
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from fpdf import FPDF
from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.errors import PyPdfError

from app.modules.facturacion.export import armado as armado_mod
from app.modules.facturacion.export.datos import FilaExport
from app.modules.facturacion.export.schemas import ExportOpciones

# Márgenes de la foto en una página A4, en mm.
_MARGEN_FOTO = 10
# La primera página de cada médico se achica un poco para dejar arriba la franja con su nombre.
_ESCALA_PRIMERA = 0.93
_ALTO_FRANJA_PT = 34


class DocumentoIlegible(ValueError):
    """Un archivo no se pudo leer (falta en disco, está dañado o está protegido)."""


@dataclass
class Archivo:
    path: str
    nombre: str
    content_type: str


@dataclass
class GrupoMedico:
    titulo: str
    archivos: list[Archivo] = field(default_factory=list)


@dataclass
class Pendientes:
    sin_documentos: int = 0
    sin_auditar: int = 0

    def __bool__(self) -> bool:
        return bool(self.sin_documentos or self.sin_auditar)


def _clave_nombre(nombre: Optional[str], cod: str) -> tuple:
    sin_marcas = unicodedata.normalize("NFD", nombre or "")
    return ("".join(c for c in sin_marcas if not unicodedata.combining(c)).casefold(), cod)


def pendientes_de(filas: list[FilaExport]) -> Pendientes:
    """Prestaciones que exigen documentación y todavía no la tienen / no están auditadas."""
    exigen = [f for f in filas if f.requiere_documentacion]
    return Pendientes(
        sin_documentos=sum(1 for f in exigen if f.documentos == 0),
        sin_auditar=sum(1 for f in exigen if not f.revisado),
    )


def filas_en_orden_del_detalle(filas: list[FilaExport], opciones: ExportOpciones) -> list[FilaExport]:
    """Las filas en el orden en que se ven: las secciones y grupos de `armar`, con el equipo de cada
    cirugía debajo de su cabeza."""
    armado = armado_mod.armar(filas, opciones)
    vistas: set[int] = set()
    salida: list[FilaExport] = []
    for seccion in armado.secciones:
        for grupo in seccion.grupos:
            for linea in grupo.lineas:
                for f in (linea.fila, *linea.hijos):
                    if f.id not in vistas:
                        vistas.add(f.id)
                        salida.append(f)
    return salida


def entran_al_pdf(f: FilaExport) -> bool:
    return f.documentos > 0 and (not f.requiere_documentacion or f.revisado)


def agrupar_por_medico(filas_ordenadas: list[FilaExport], docs_por_fila: dict[int, list]) -> list[GrupoMedico]:
    """Un grupo por médico (A-Z) con los archivos de sus prestaciones, en el orden recibido."""
    por_medico: dict[str, list[FilaExport]] = {}
    for f in filas_ordenadas:
        if entran_al_pdf(f):
            por_medico.setdefault(f.cod_medico, []).append(f)

    grupos: list[GrupoMedico] = []
    for cod, filas in sorted(por_medico.items(), key=lambda kv: _clave_nombre(kv[1][0].prestador_nombre, kv[0])):
        primera = filas[0]
        matricula = primera.matricula if primera.matricula else "s/m"
        titulo = f"{matricula} - {primera.prestador_nombre or 'SIN NOMBRE'} · Socio {cod}"
        g = GrupoMedico(titulo=titulo)
        for f in filas:
            for d in docs_por_fila.get(f.id, []):
                g.archivos.append(Archivo(path=d.path, nombre=d.nombre_original, content_type=d.content_type))
        if g.archivos:
            grupos.append(g)
    return grupos


# ── Armado del PDF ───────────────────────────────────────────────────────────
def _latin1(texto: str) -> str:
    return texto.encode("latin-1", "replace").decode("latin-1")


def _pagina_a4_de_foto(path: str) -> bytes:
    """Una página A4 con la foto ajustada (y derecha, según su orientación EXIF)."""
    try:
        with Image.open(path) as original:
            imagen = ImageOps.exif_transpose(original)
            if imagen.mode in ("RGBA", "LA", "P"):
                fondo = Image.new("RGB", imagen.size, "white")
                fondo.paste(imagen.convert("RGBA"), mask=imagen.convert("RGBA").split()[-1])
                imagen = fondo
            else:
                imagen = imagen.convert("RGB")
            ancho_px, alto_px = imagen.size
            pdf = FPDF(orientation="L" if ancho_px > alto_px else "P", unit="mm", format="A4")
            pdf.add_page()
            caja_w, caja_h = pdf.w - 2 * _MARGEN_FOTO, pdf.h - 2 * _MARGEN_FOTO
            escala = min(caja_w / ancho_px, caja_h / alto_px)
            w, h = ancho_px * escala, alto_px * escala
            pdf.image(imagen, x=(pdf.w - w) / 2, y=(pdf.h - h) / 2, w=w, h=h)
            return bytes(pdf.output())
    except OSError as e:
        raise DocumentoIlegible(str(e)) from e


def _franja(ancho: float, alto: float, titulo: str) -> bytes:
    """Una página transparente del tamaño dado con el título arriba, para superponer."""
    pdf = FPDF(unit="pt", format=(ancho, alto))
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_xy(28, 12)
    pdf.cell(ancho - 56, 14, _latin1(titulo), border=0)
    pdf.set_draw_color(17, 24, 39)
    pdf.set_line_width(1.2)
    pdf.line(28, 30, ancho - 28, 30)
    return bytes(pdf.output())


def _paginas_de(archivo: Archivo) -> list:
    if not os.path.isfile(archivo.path):
        raise DocumentoIlegible(f"No se encuentra el archivo «{archivo.nombre}».")
    if archivo.content_type == "application/pdf":
        try:
            lector = PdfReader(archivo.path)
            if lector.is_encrypted and not lector.decrypt(""):
                raise DocumentoIlegible(f"«{archivo.nombre}» está protegido con contraseña.")
            return list(lector.pages)
        except PyPdfError as e:
            raise DocumentoIlegible(f"«{archivo.nombre}» no se pudo leer: {e}") from e
    return list(PdfReader(io.BytesIO(_pagina_a4_de_foto(archivo.path))).pages)


def build_pdf_documentacion(grupos: list[GrupoMedico]) -> bytes:
    """El PDF final. Levanta `DocumentoIlegible` si algún archivo no se puede leer: es mejor
    avisar que entregar un PDF al que le falta una página sin que se note."""
    escritor = PdfWriter()
    for g in grupos:
        primera = True
        for archivo in g.archivos:
            for pagina in _paginas_de(archivo):
                if primera:
                    ancho, alto = float(pagina.mediabox.width), float(pagina.mediabox.height)
                    # Se achica hacia abajo y al centro; la franja del médico ocupa lo de arriba.
                    s = min(_ESCALA_PRIMERA, (alto - _ALTO_FRANJA_PT) / alto)
                    pagina.add_transformation(
                        Transformation().scale(s, s).translate(ancho * (1 - s) / 2, 0)
                    )
                    escritor.add_page(pagina)
                    franja = PdfReader(io.BytesIO(_franja(ancho, alto, g.titulo))).pages[0]
                    escritor.pages[-1].merge_page(franja)
                    primera = False
                else:
                    escritor.add_page(pagina)
    salida = io.BytesIO()
    escritor.write(salida)
    return salida.getvalue()
