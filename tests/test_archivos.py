"""Entrega autorizada de adjuntos (S6).

Los 525 archivos de `uploads/medicos/` son escaneos de DNI, títulos y
constancias de CBU. Se servían por dos caminos sin autenticación: el
`handle /uploads/*` de Caddy y —sin que nadie lo hubiera documentado— el
`app.mount("/uploads", StaticFiles(...))` de `app/main.py`, que además evadía
`enforce_authz` porque un `Mount` es un sub-ASGI-app.

Estos tests fijan las tres cosas que no pueden volver: que el árbol entero no se
publique, que no se pueda salir de `uploads/` con `..`, y que un subdirectorio
nuevo no quede accesible por omisión.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.scopes import Scope


@pytest.fixture
def cliente(app):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def adjunto_de_prueba():
    """Crea `uploads/medicos/999999/prueba.txt` y lo borra al terminar."""
    destino = Path("uploads/medicos/999999/prueba.txt")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("SECRETO", encoding="utf-8")
    yield destino
    destino.unlink(missing_ok=True)
    destino.parent.rmdir()


def test_el_arbol_de_uploads_ya_no_se_publica(cliente, adjunto_de_prueba):
    """La regresión más importante: `/uploads/medicos/...` no puede dar 200.

    Antes daba 200 con el contenido, sin token, y ni siquiera pasaba por
    `enforce_authz`. Se verifica sobre el path viejo, que es el que un cliente
    o un atacante ya tiene guardado.
    """
    r = cliente.get("/uploads/medicos/999999/prueba.txt")
    assert r.status_code != 200, (
        "El mount público de /uploads volvió: el árbol entero de adjuntos está "
        "accesible sin autenticación. Ver app/main.py y S6."
    )
    assert "SECRETO" not in r.text


def test_el_endpoint_nuevo_exige_token(cliente, adjunto_de_prueba):
    r = cliente.get("/api/archivos/medicos/999999/prueba.txt")
    assert r.status_code == 401, r.text


def test_los_directorios_publicos_siguen_publicos(app):
    """Noticias y publicidad los tiene que ver un visitante anónimo.

    Si se protegieran, el portal quedaría sin imágenes. Van montados uno por uno
    a propósito.
    """
    montados = {r.path for r in app.routes if r.__class__.__name__ == "Mount"}
    assert "/uploads/web_noticias" in montados
    assert "/uploads/medicos_publicidad" in montados
    # Y el padre NO, que es el que publicaba todo.
    assert "/uploads" not in montados


@pytest.mark.parametrize("ruta", [
    "../.env",
    "medicos/../../.env",
    "medicos/999999/../../../app/core/config.py",
    "/etc/passwd",
])
def test_no_se_puede_salir_de_uploads(cliente, ruta):
    """Path traversal: nunca 200, con o sin token.

    Starlette ya decodifica el porcentaje antes de que llegue el parámetro, así
    que buscar la cadena ".." no alcanzaría; el handler resuelve el path real y
    exige que quede debajo de la raíz.
    """
    r = cliente.get(f"/api/archivos/{ruta}")
    assert r.status_code != 200, f"traversal con {ruta!r} devolvió 200"


def test_las_reglas_cubren_todos_los_subdirectorios_reales():
    """Todo directorio bajo `uploads/` tiene que tener una regla explícita.

    `_autorizar` falla cerrado, así que un directorio nuevo sin regla responde
    403 y no filtra nada — pero el síntoma sería "no me deja descargar" en una
    pantalla que antes andaba, y este test lo dice antes y con el nombre del
    directorio.
    """
    from app.modules.archivos.routes import PUBLICOS

    con_regla = {
        "medicos", "validaciones", "obras_sociales",
        "boletin_valores_eticos", "facturas", "planillas",
    } | set(PUBLICOS)

    raiz = Path("uploads")
    if not raiz.is_dir():
        pytest.skip("no hay uploads/ en este entorno")

    reales = {d.name for d in raiz.iterdir() if d.is_dir()}
    sin_regla = reales - con_regla
    assert not sin_regla, (
        "Subdirectorios de uploads/ sin regla de autorización — agregalos a "
        f"app/modules/archivos/routes.py::_autorizar: {sorted(sin_regla)}"
    )


def test_las_noticias_y_la_publicidad_siguen_saliendo_publicas():
    """El contenido del portal NO se puede mover al endpoint autorizado.

    Un visitante anónimo tiene que poder ver las noticias: son la home del
    Colegio. Si `url_archivo` empezara a devolver `/api/archivos/...` para
    `web_noticias`, el portal quedaría sin imágenes para todo el que no esté
    logueado — y el síntoma sería "se ven cuadrados rotos", no un error.

    En producción son 130 archivos en `web_noticias/` y 33 en
    `medicos_publicidad/`.
    """
    from app.common.files import url_archivo

    assert url_archivo("/uploads/web_noticias/foto.jpg") == "/uploads/web_noticias/foto.jpg"
    assert url_archivo("uploads/web_noticias/foto.jpg") == "/uploads/web_noticias/foto.jpg"
    assert url_archivo("/uploads/medicos_publicidad/ad.mp4") == "/uploads/medicos_publicidad/ad.mp4"


def test_los_adjuntos_con_dueno_salen_por_el_endpoint_autorizado():
    """Y todo lo demás sí tiene que ir por `/api/archivos`.

    Es la otra mitad: si un módulo nuevo guardara en `uploads/loquesea/` y
    `url_archivo` lo dejara público, volvería S6 por la puerta de atrás.
    """
    from app.common.files import url_archivo

    for ruta in (
        "uploads/medicos/2514/abc.pdf",
        "uploads/validaciones/3200/orden.pdf",
        "uploads/obras_sociales/5/convenio.pdf",
        "uploads/facturas/9922/comprobante.pdf",
        "uploads/boletin_valores_eticos/x.pdf",
        "uploads/un_modulo_futuro/x.pdf",
    ):
        assert url_archivo(ruta).startswith("/api/archivos/"), ruta

    # Tolera las dos convenciones que conviven en la base.
    assert url_archivo("/uploads/medicos/1/a.pdf") == url_archivo("uploads/medicos/1/a.pdf")
    # Y no rompe con vacíos, que es lo que devuelve un `attach_*` sin cargar.
    assert url_archivo(None) is None
    assert url_archivo("") is None


def test_cada_subdirectorio_usa_el_identificador_correcto():
    """`medicos/` va por PK interna y `validaciones/` por NRO_SOCIO.

    No son intercambiables y confundirlos no falla ruidosamente: compararía dos
    enteros que no representan lo mismo y devolvería el archivo de otro médico.
    Se verifica sobre el código porque el bug sería silencioso en runtime.
    """
    import inspect

    from app.modules.archivos import routes

    fuente = inspect.getsource(routes._autorizar)

    bloque_medicos = fuente.split('== "medicos"')[1].split('== "validaciones"')[0]
    assert "medico_objetivo" in bloque_medicos, (
        "uploads/medicos/ se indexa por ListadoMedico.ID: va con medico_objetivo"
    )
    assert Scope.MEDICO_DOCUMENTO.name in bloque_medicos

    bloque_validaciones = fuente.split('== "validaciones"')[1].split('== "obras_sociales"')[0]
    assert "socio_objetivo" in bloque_validaciones, (
        "uploads/validaciones/ se indexa por NRO_SOCIO: va con socio_objetivo"
    )
    assert Scope.MEDICO_LEER.name in bloque_validaciones
