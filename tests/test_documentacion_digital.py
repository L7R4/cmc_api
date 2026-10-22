"""Documentación digital de las prestaciones: quién la exige, el guardado de los archivos, la
auditoría, el cierre, los totales de la factura y el PDF de la documentación.

Servicio sin commit real (`commit` → `flush`, el fixture `db` deshace al final), sobre una obra
social sintética y el período 209912, que no existen en facturación. Los archivos van a una carpeta
temporal.
"""
import datetime
import io
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from fpdf import FPDF
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import select

from app.db.models import CodigoObraSocial, DetalleFacturacionCMC, NomencladorCMC, ObrasSociales
from app.modules.facturacion import documentacion, service
from app.modules.facturacion.export import documentacion as pdf_doc
from app.modules.facturacion.export.datos import FilaExport
from app.modules.facturacion.export.schemas import ExportOpciones
from app.modules.nomenclador import alta_os
from app.modules.nomenclador.schemas import AccionCodigosIn

OS = 29996  # no existe en el catálogo real de obras sociales
PERIODO = "209912"
D = Decimal


@pytest.fixture
def s(db, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "commit", db.flush)

    async def _no_rollback():
        return None

    monkeypatch.setattr(db, "rollback", _no_rollback)
    monkeypatch.setattr(documentacion, "UPLOAD_ROOT", str(tmp_path / "uploads"))
    return db


def _pdf_bytes(paginas=1) -> bytes:
    pdf = FPDF()
    for i in range(paginas):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, f"pagina {i + 1}")
    return bytes(pdf.output())


def _jpg_bytes() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (300, 200), "red").save(b, format="JPEG")
    return b.getvalue()


def _png_bytes() -> bytes:
    b = io.BytesIO()
    Image.new("RGBA", (200, 300), (0, 0, 255, 128)).save(b, format="PNG")
    return b.getvalue()


def _upload(contenido: bytes, nombre: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(contenido), filename=nombre)


async def _os(db, habilitada=True) -> ObrasSociales:
    os_ = ObrasSociales(NRO_OBRASOCIAL=OS, OBRA_SOCIAL="PRUEBA DOCUMENTACION", cuit="0", documentacion_digital=habilitada)
    db.add(os_)
    await db.flush()
    return os_


async def _par(db, *, exige=True) -> CodigoObraSocial:
    nom = (await db.execute(select(NomencladorCMC).where(NomencladorCMC.activo.is_(True)).limit(1))).scalars().first()
    par = CodigoObraSocial(
        obra_social_nro=OS, nomenclador_id=nom.id, codigo=nom.codigo, estado="activo", requiere_documentacion=exige,
    )
    db.add(par)
    await db.flush()
    return par


def _prestacion(codigo: str, *, revisado=False, cod_med="1084", importe="100") -> DetalleFacturacionCMC:
    return DetalleFacturacionCMC(
        periodo=PERIODO, version=1, estado="A", revisado=revisado,
        cod_obr=str(OS), cod_med=cod_med, cod_nom=codigo, cantidad=1, sesion=1, porc=100,
        nro_orden=0, tpo_funcion="M", manual="N", ayudante=D("0"),
        honorarios=D(importe), gastos=D("0"), coseguro=D("0"),
        importe_total=D(importe), dni_p="1", nom_ape_p="PRUEBA",
        fecha_practica=datetime.date(2026, 9, 1), usuario="1084",
    )


async def _escenario(db, *, habilitada=True, exige=True):
    await _os(db, habilitada)
    par = await _par(db, exige=exige)
    p = _prestacion(par.codigo)
    db.add(p)
    await db.flush()
    return par, p


# ── Quién la exige ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_la_exige_solo_si_la_obra_social_y_el_codigo_estan_marcados(s):
    par, _ = await _escenario(s)
    assert await documentacion.requiere_documentacion(s, OS, par.codigo) is True

    par.requiere_documentacion = False  # la obra social sí, el código no
    await s.flush()
    assert await documentacion.requiere_documentacion(s, OS, par.codigo) is False

    par.requiere_documentacion = True
    (await s.execute(select(ObrasSociales).where(ObrasSociales.NRO_OBRASOCIAL == OS))).scalar_one().documentacion_digital = False
    await s.flush()  # el código sí, la obra social no
    assert await documentacion.requiere_documentacion(s, OS, par.codigo) is False
    assert await documentacion.codigos_que_requieren(s, OS, [par.codigo]) == set()


# ── Guardado ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agregar_guarda_los_archivos_en_orden_y_con_su_origen(s, tmp_path):
    _, p = await _escenario(s)
    docs = await documentacion.agregar(
        s, p, [_upload(_pdf_bytes(), "orden.pdf"), _upload(_jpg_bytes(), "bono.jpg"), _upload(_png_bytes(), "t.png")],
        "colegio", "7",
    )
    assert [d.posicion for d in docs] == [0, 1, 2]
    assert [d.content_type for d in docs] == ["application/pdf", "image/jpeg", "image/png"]
    assert all(d.origen == "colegio" and d.subido_por == "7" for d in docs)
    for d in docs:  # cada uno quedó en el disco, bajo la carpeta del médico dueño
        assert d.path.endswith((".pdf", ".jpg", ".png")) and "/validaciones/1084/" in d.path
        assert (tmp_path.parent / d.path).exists() or __import__("os").path.exists(d.path)

    mas = await documentacion.agregar(s, p, [_upload(_pdf_bytes(), "otro.pdf")], "medico", "1084")
    assert mas[0].posicion == 3  # sigue a los anteriores


@pytest.mark.asyncio
async def test_rechaza_lo_que_no_es_pdf_ni_imagen_por_su_contenido(s):
    _, p = await _escenario(s)
    with pytest.raises(HTTPException) as e:
        await documentacion.agregar(s, p, [_upload(b"MZ\x90\x00 no soy un pdf", "orden.pdf")], "colegio", "7")
    assert e.value.status_code == 415
    with pytest.raises(HTTPException) as e:  # un Excel con nombre de imagen tampoco
        await documentacion.agregar(s, p, [_upload(b"PK\x03\x04 zip", "foto.jpg")], "colegio", "7")
    assert e.value.status_code == 415
    assert await documentacion.listar(s, p.id_detalle_prestaciones) == []


@pytest.mark.asyncio
async def test_agregar_sin_archivos_y_el_tope_por_prestacion(s):
    _, p = await _escenario(s)
    with pytest.raises(HTTPException) as e:
        await documentacion.agregar(s, p, [], "colegio", "7")
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        await documentacion.agregar(
            s, p, [_upload(_pdf_bytes(), f"{i}.pdf") for i in range(documentacion.MAX_POR_PRESTACION + 1)], "colegio", "7",
        )
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_reordenar_pide_todos_los_ids_y_reemplazar_conserva_el_lugar(s):
    _, p = await _escenario(s)
    a, b, c = await documentacion.agregar(
        s, p, [_upload(_pdf_bytes(), "a.pdf"), _upload(_pdf_bytes(), "b.pdf"), _upload(_pdf_bytes(), "c.pdf")], "colegio", "7",
    )
    with pytest.raises(HTTPException) as e:
        await documentacion.reordenar(s, p.id_detalle_prestaciones, [a.id, b.id])
    assert e.value.status_code == 422

    docs = await documentacion.reordenar(s, p.id_detalle_prestaciones, [c.id, a.id, b.id])
    assert [d.nombre_original for d in docs] == ["c.pdf", "a.pdf", "b.pdf"]

    cambiado = await documentacion.reemplazar(s, p, a.id, _upload(_jpg_bytes(), "nuevo.jpg"), "colegio", "7")
    assert cambiado.nombre_original == "nuevo.jpg" and cambiado.posicion == 1


# ── Baja: el médico no puede dejarla sin documentos ──────────────────────────


@pytest.mark.asyncio
async def test_el_medico_no_deja_sin_documentos_una_prestacion_que_los_exige(s):
    _, p = await _escenario(s)
    (d,) = await documentacion.agregar(s, p, [_upload(_pdf_bytes(), "o.pdf")], "medico", "1084")
    with pytest.raises(HTTPException) as e:
        await documentacion.eliminar(s, p, d.id, es_medico=True)
    assert e.value.status_code == 409
    assert len(await documentacion.listar(s, p.id_detalle_prestaciones)) == 1


@pytest.mark.asyncio
async def test_el_colegio_si_puede_y_la_prestacion_vuelve_a_sin_auditar(s):
    _, p = await _escenario(s)
    p.revisado = True
    (d,) = await documentacion.agregar(s, p, [_upload(_pdf_bytes(), "o.pdf")], "colegio", "7")
    await documentacion.eliminar(s, p, d.id, es_medico=False)
    assert await documentacion.listar(s, p.id_detalle_prestaciones) == []
    assert p.revisado is False
    assert await documentacion.estado(s, p) == documentacion.FALTA


@pytest.mark.asyncio
async def test_si_no_la_exige_se_puede_quitar_todo(s):
    _, p = await _escenario(s, exige=False)
    (d,) = await documentacion.agregar(s, p, [_upload(_pdf_bytes(), "o.pdf")], "medico", "1084")
    await documentacion.eliminar(s, p, d.id, es_medico=True)  # no exige: sin restricción
    assert await documentacion.estado(s, p) == documentacion.NO_APLICA


# ── Auditoría y cierre ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_se_audita_una_prestacion_que_exige_documentos_y_no_los_tiene(s):
    _, p = await _escenario(s)
    with pytest.raises(HTTPException) as e:
        await service.marcar_revisado(s, [p.id_detalle_prestaciones], [])
    assert e.value.status_code == 409 and e.value.detail["ids"] == [p.id_detalle_prestaciones]

    await documentacion.agregar(s, p, [_upload(_pdf_bytes(), "o.pdf")], "colegio", "7")
    (fila,) = await service.marcar_revisado(s, [p.id_detalle_prestaciones], [])
    assert fila.revisado is True


@pytest.mark.asyncio
async def test_una_prestacion_que_no_exige_se_audita_sin_documentos(s):
    _, p = await _escenario(s, exige=False)
    (fila,) = await service.marcar_revisado(s, [p.id_detalle_prestaciones], [])
    assert fila.revisado is True


@pytest.mark.asyncio
async def test_no_se_cierra_con_prestaciones_sin_auditar_si_la_obra_social_tiene_documentacion(s):
    _, p = await _escenario(s)
    await service._ensure_factura_abierta(s, str(OS), PERIODO, "7")  # la cabecera se crea al cargar
    with pytest.raises(HTTPException) as e:
        await service.cerrar_periodo(s, str(OS), PERIODO, "7")
    assert e.value.status_code == 409 and e.value.detail["tipo"] == "sin_auditar" and e.value.detail["cantidad"] == 1

    await documentacion.agregar(s, p, [_upload(_pdf_bytes(), "o.pdf")], "colegio", "7")
    await service.marcar_revisado(s, [p.id_detalle_prestaciones], [])
    cierre = await service.cerrar_periodo(s, str(OS), PERIODO, "7")
    assert cierre["cantidad"] == 1


@pytest.mark.asyncio
async def test_sin_documentacion_digital_se_cierra_sin_auditar_como_siempre(s):
    await _os(s, habilitada=False)
    par = await _par(s, exige=False)
    s.add(_prestacion(par.codigo))
    await s.flush()
    await service._ensure_factura_abierta(s, str(OS), PERIODO, "7")
    cierre = await service.cerrar_periodo(s, str(OS), PERIODO, "7")
    assert cierre["cantidad"] == 1


# ── Totales de la factura ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_total_a_facturar_suma_lo_auditado_y_la_proyeccion_todo(s):
    await _os(s)
    par = await _par(s)
    s.add_all([
        _prestacion(par.codigo, revisado=True, importe="100"),
        _prestacion(par.codigo, revisado=False, importe="40"),
    ])
    await s.flush()
    cab = await service._ensure_factura_abierta(s, str(OS), PERIODO, "7")
    d = await service.obtener_factura_detalle(s, cab.id_prestaciones)

    assert d["documentacion_digital"] is True
    assert d["total_auditado"] == D("100") and d["total_proyectado"] == D("140") and d["total_importe"] == D("140")
    assert d["sin_auditar"] == 1 and d["requieren_documentos"] == 2 and d["con_documentos"] == 0
    assert [p["requiere_documentacion"] for p in d["prestadores"][0]["prestaciones"]] == [True, True]


@pytest.mark.asyncio
async def test_sin_documentacion_digital_el_total_no_cambia(s):
    await _os(s, habilitada=False)
    par = await _par(s, exige=False)
    s.add_all([_prestacion(par.codigo, revisado=True, importe="100"), _prestacion(par.codigo, importe="40")])
    await s.flush()
    cab = await service._ensure_factura_abierta(s, str(OS), PERIODO, "7")
    d = await service.obtener_factura_detalle(s, cab.id_prestaciones)
    assert d["documentacion_digital"] is False
    assert d["total_auditado"] == d["total_importe"] == D("140")
    assert d["sin_auditar"] == 0 and d["requieren_documentos"] == 0


# ── Códigos por obra social: rango y acciones en bloque ──────────────────────


@pytest.mark.asyncio
async def test_el_rango_de_codigos_incluye_los_extremos_y_acota_los_conteos(s):
    nums = [
        int(c) for (c,) in (await s.execute(
            select(NomencladorCMC.codigo).where(NomencladorCMC.activo.is_(True)).order_by(NomencladorCMC.codigo)
        )).all() if c.isdigit()
    ]
    nums = sorted(set(nums))
    assert len(nums) > 20
    desde, hasta = nums[5], nums[15]

    sin_rango = await alta_os.listar_por_os(s, OS, size=200)
    con_rango = await alta_os.listar_por_os(s, OS, codigo_desde=desde, codigo_hasta=hasta, size=200)
    codigos = [int(i.codigo) for i in con_rango.items]
    assert codigos and min(codigos) == desde and max(codigos) == hasta
    assert all(desde <= c <= hasta for c in codigos)
    assert con_rango.total == len([n for n in nums if desde <= n <= hasta]) <= sin_rango.total
    assert sum(con_rango.conteos.values()) == con_rango.total  # los conteos también quedan acotados

    solo_desde = await alta_os.listar_por_os(s, OS, codigo_desde=nums[-3], size=200)
    assert [int(i.codigo) for i in solo_desde.items] == nums[-3:]


@pytest.mark.asyncio
async def test_acciones_en_bloque_con_resultado_por_codigo(s):
    await _os(s)
    noms = (await s.execute(select(NomencladorCMC).where(NomencladorCMC.activo.is_(True)).limit(3))).scalars().all()
    a, b, sin_alta = noms
    for n in (a, b):
        s.add(CodigoObraSocial(obra_social_nro=OS, nomenclador_id=n.id, codigo=n.codigo, estado="activo"))
    await s.flush()
    ids = [a.id, b.id, sin_alta.id]

    r = await alta_os.aplicar_accion(s, AccionCodigosIn(obra_social_nro=OS, nomenclador_ids=ids, accion="activar_documentacion"))
    assert r.aplicados == 2
    assert {x.nomenclador_id: x.estado for x in r.resultados} == {a.id: "aplicado", b.id: "aplicado", sin_alta.id: "no_aplica"}

    r = await alta_os.aplicar_accion(s, AccionCodigosIn(obra_social_nro=OS, nomenclador_ids=ids, accion="activar_documentacion"))
    assert r.aplicados == 0  # ya la llevaban

    r = await alta_os.aplicar_accion(s, AccionCodigosIn(obra_social_nro=OS, nomenclador_ids=[a.id], accion="suspender"))
    assert r.aplicados == 1
    r = await alta_os.aplicar_accion(s, AccionCodigosIn(obra_social_nro=OS, nomenclador_ids=[a.id, b.id], accion="reactivar"))
    assert [x.estado for x in r.resultados] == ["aplicado", "no_aplica"]
    r = await alta_os.aplicar_accion(s, AccionCodigosIn(obra_social_nro=OS, nomenclador_ids=[a.id], accion="desactivar_documentacion"))
    assert r.aplicados == 1
    listado = await alta_os.listar_por_os(s, OS, codigo_desde=int(a.codigo) if a.codigo.isdigit() else None, size=200)
    assert listado.documentacion_digital is True


@pytest.mark.asyncio
async def test_activar_documentacion_en_un_codigo_exige_la_obra_social_habilitada(s):
    await _os(s, habilitada=False)
    par = await _par(s, exige=False)
    from app.modules.nomenclador.schemas import CodigoObraSocialUpdate

    with pytest.raises(HTTPException) as e:
        await alta_os.actualizar_par(s, OS, par.nomenclador_id, CodigoObraSocialUpdate(requiere_documentacion=True))
    assert e.value.status_code == 409

    r = await alta_os.aplicar_accion(
        s, AccionCodigosIn(obra_social_nro=OS, nomenclador_ids=[par.nomenclador_id], accion="activar_documentacion"),
    )
    assert r.aplicados == 0 and "habilitada" in r.resultados[0].motivo


# ── PDF de la documentación ──────────────────────────────────────────────────


def _fila(id_, cod, nombre, matricula, *, requiere=True, docs=1, revisado=True, fecha=1) -> FilaExport:
    return FilaExport(
        id=id_, cod_medico=cod, prestador_nombre=nombre, cod_obr="29996", obra_social_nombre=None,
        matricula=matricula, autorizacion=None, fecha_practica=datetime.date(2026, 9, fecha),
        codigo="420101", nro_afiliado=None, afiliado="X", cantidad=1, sesion=1, porcentaje=100,
        honorarios=D("100"), gastos=D("0"), ayudante=D("0"), coseguro=D("0"), subtotal=D("100"),
        tipo="Consulta", tipo_prestador=None, diagnostico=None, via=None, especialidad_nombre=None,
        estado_validacion=None, revisado=revisado, grupo_equipo_id=None,
        requiere_documentacion=requiere, documentos=docs,
    )


def test_los_pendientes_son_lo_que_exige_y_no_tiene_o_no_esta_auditado():
    filas = [
        _fila(1, "10", "A", 1),
        _fila(2, "10", "A", 1, docs=0),
        _fila(3, "10", "A", 1, revisado=False),
        _fila(4, "10", "A", 1, requiere=False, docs=0, revisado=False),  # no exige: no cuenta
    ]
    p = pdf_doc.pendientes_de(filas)
    assert (p.sin_documentos, p.sin_auditar) == (1, 1) and bool(p) is True
    assert not pdf_doc.pendientes_de([filas[0], filas[3]])


def test_los_medicos_van_de_la_a_a_la_z_y_cada_uno_con_su_orden_del_detalle():
    filas = [
        _fila(1, "30", "ZAPATA, JUAN", 300, fecha=2),
        _fila(2, "10", "ÁLVAREZ, ANA", 100, fecha=9),
        _fila(3, "10", "ÁLVAREZ, ANA", 100, fecha=3),
        _fila(4, "20", "MOLINA, LUIS", 200, fecha=1, docs=0, requiere=False),  # sin documentos: no entra
    ]
    ordenadas = pdf_doc.filas_en_orden_del_detalle(filas, ExportOpciones(orden="fecha_practica", direccion="asc", agrupacion="plana"))
    assert [f.id for f in ordenadas if f.id in (2, 3)] == [3, 2]  # por fecha ascendente, como en el detalle
    docs = {i: [SimpleNamespace(path=f"/x/{i}.pdf", nombre_original=f"{i}.pdf", content_type="application/pdf")] for i in (1, 2, 3)}
    grupos = pdf_doc.agrupar_por_medico(ordenadas, docs)

    assert [g.titulo for g in grupos] == ["100 - ÁLVAREZ, ANA · Socio 10", "300 - ZAPATA, JUAN · Socio 30"]
    # Dentro del médico, las prestaciones siguen el orden del detalle (por fecha ascendente: la 3 antes que la 2).
    assert [a.nombre for a in grupos[0].archivos] == ["3.pdf", "2.pdf"]


def test_el_pdf_une_pdfs_y_fotos_con_el_titulo_de_cada_medico(tmp_path):
    def guardar(nombre, datos):
        ruta = tmp_path / nombre
        ruta.write_bytes(datos)
        return str(ruta)

    grupos = [
        pdf_doc.GrupoMedico("100 - ÁLVAREZ, ANA · Socio 10", [
            pdf_doc.Archivo(guardar("a.pdf", _pdf_bytes(2)), "a.pdf", "application/pdf"),
            pdf_doc.Archivo(guardar("b.jpg", _jpg_bytes()), "b.jpg", "image/jpeg"),
        ]),
        pdf_doc.GrupoMedico("300 - ZAPATA, JUAN · Socio 30", [
            pdf_doc.Archivo(guardar("c.png", _png_bytes()), "c.png", "image/png"),
        ]),
    ]
    lector = PdfReader(io.BytesIO(pdf_doc.build_pdf_documentacion(grupos)))
    assert len(lector.pages) == 4  # 2 + 1 + 1
    assert "ÁLVAREZ, ANA" in lector.pages[0].extract_text() or "LVAREZ, ANA" in lector.pages[0].extract_text()
    assert "pagina 1" in lector.pages[0].extract_text()  # el documento sigue ahí, debajo de la franja
    assert "pagina" in lector.pages[1].extract_text() and "Socio" not in lector.pages[1].extract_text()
    assert "Socio 30" in lector.pages[3].extract_text()  # arranca el segundo médico


def test_un_archivo_que_falta_se_avisa_y_no_se_omite(tmp_path):
    grupos = [pdf_doc.GrupoMedico("1 - A · Socio 1", [pdf_doc.Archivo(str(tmp_path / "no_esta.pdf"), "no_esta.pdf", "application/pdf")])]
    with pytest.raises(pdf_doc.DocumentoIlegible):
        pdf_doc.build_pdf_documentacion(grupos)


# ── Lado del médico: validar con documentación ───────────────────────────────


async def _medico(db):
    from app.db.models import ListadoMedico

    return (await db.execute(
        select(ListadoMedico).where(
            ListadoMedico.estado_cuenta == "activo", ListadoMedico.es_organizacion == 0, ListadoMedico.MATRICULA_PROV > 0,
        ).limit(1)
    )).scalars().first()


@pytest.mark.asyncio
async def test_validar_sin_documentos_se_rechaza_antes_de_consultar_a_la_obra_social(s, monkeypatch):
    """Si el código la exige y no vienen archivos: 422, y la obra social ni se entera."""
    from app.modules.validaciones.core import pipeline
    from app.modules.validaciones.schemas import PrestacionCreate

    par, _ = await _escenario(s)
    medico = await _medico(s)

    async def _validar(*a, **k):
        raise AssertionError("no tiene que llegar a la obra social")

    obra = SimpleNamespace(nro=OS, entrada=None, verificar_prestador=lambda m: None, validar=_validar)

    async def _get_medico(db, nro):
        return medico

    async def _periodo(db, nro):
        return PERIODO

    async def _gate(*a, **k):
        return None

    monkeypatch.setattr(pipeline.obras, "obtener_o_error", lambda nro: obra)
    monkeypatch.setattr(pipeline, "parsear_entrada", lambda entrada, payload: SimpleNamespace(codigo=payload.codigo, cantidad=1))
    monkeypatch.setattr(pipeline, "get_medico", _get_medico)
    monkeypatch.setattr(pipeline, "periodo_actual", _periodo)
    monkeypatch.setattr(pipeline, "gate_periodo", _gate)

    payload = PrestacionCreate(obra_social=OS, codigo=par.codigo, nro_afiliado="1", nombre_afiliado="X")
    with pytest.raises(HTTPException) as e:
        await pipeline.crear_prestacion(s, payload=payload, nro_socio=int(medico.NRO_SOCIO), usuario_carga=1)
    assert e.value.status_code == 422 and "documentación" in e.value.detail

    # Con un archivo que no es ni PDF ni imagen tampoco: se verifica antes de hablar con la obra social.
    with pytest.raises(HTTPException) as e:
        await pipeline.crear_prestacion(
            s, payload=payload, nro_socio=int(medico.NRO_SOCIO), usuario_carga=1,
            archivos=[_upload(b"MZ no soy un pdf", "orden.pdf")],
        )
    assert e.value.status_code == 415


@pytest.mark.asyncio
async def test_la_prestacion_se_graba_junto_con_sus_documentos(s, tmp_path):
    from app.modules.validaciones.core.grabado import grabar_prestacion

    par, _ = await _escenario(s)
    medico = await _medico(s)
    infos = await documentacion.validar_archivos([_upload(_pdf_bytes(), "orden.pdf"), _upload(_jpg_bytes(), "bono.jpg")])
    escritos = await documentacion.escribir(infos, medico.NRO_SOCIO)

    fila = await grabar_prestacion(
        s, medico=medico, obra_social_id=OS, periodo=PERIODO, codigo=par.codigo,
        precio=SimpleNamespace(honorarios=D("100"), gastos=D("0"), snapshot=None),
        cantidad=1, coseguro=D("0"), nro_afiliado="1", nombre_afiliado="X", nro_autorizacion="A1",
        validacion_estado="cargada", validacion_detalle="", validacion_respuesta=None,
        fecha=datetime.date(2026, 9, 1), usuario_carga=int(medico.NRO_SOCIO), documentos=escritos,
    )
    docs = await documentacion.listar(s, fila.id_detalle_prestaciones)
    assert [d.nombre_original for d in docs] == ["orden.pdf", "bono.jpg"]
    assert all(d.origen == "medico" for d in docs)
    assert await documentacion.estado(s, fila) == documentacion.COMPLETA


@pytest.mark.asyncio
async def test_el_listado_del_medico_trae_documentos_y_estado(s):
    from app.modules.validaciones.core.documentos import enriquecer

    _, con = await _escenario(s)
    sin = _prestacion(con.cod_nom)
    s.add(sin)
    await s.flush()
    await documentacion.agregar(s, con, [_upload(_pdf_bytes(), "o.pdf")], "medico", "1084")
    dicts = [{"orden": None}, {"orden": None}]
    await enriquecer(s, [con, sin], dicts)

    assert dicts[0]["documentacion"] == "completa" and dicts[0]["documentos"][0]["nombre"] == "o.pdf"
    assert dicts[0]["orden"] == dicts[0]["documentos"][0]["url"]
    assert dicts[1]["documentacion"] == "falta" and dicts[1]["documentos"] == []


# ── Equipo de una cirugía ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_equipo_no_exige_documentos_aparte_la_exige_su_cabeza(s):
    par, cabeza = await _escenario(s)
    ayudante = _prestacion(par.codigo, cod_med="2210")
    ayudante.grupo_equipo_id = cabeza.id_detalle_prestaciones
    s.add(ayudante)
    await s.flush()

    exigen = await documentacion.exigen_en_bloque(s, [cabeza, ayudante])
    assert exigen == {cabeza.id_detalle_prestaciones}  # el ayudante no
    # Y por eso se puede auditar sin documentos propios.
    (fila,) = await service.marcar_revisado(s, [ayudante.id_detalle_prestaciones], [])
    assert fila.revisado is True
