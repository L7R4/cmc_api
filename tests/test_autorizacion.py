"""Tests que impiden que la cobertura de autorización se degrade otra vez.

La evidencia de la auditoría es que sin un control automático **la cobertura no
se recupera sola**: entre la auditoría original y su revisión se agregaron 54
endpoints, todos correctamente protegidos, y no se cerró ninguno de los 200 que
ya estaban abiertos. Estos tests convierten eso en un error de build.

Dos familias:

  * **Estructurales** (§5.2) — igualdad estricta, porque las dos puntas solo
    cambian por commit: el enum `Scope` y la tabla `permissions`, que no tiene
    ningún endpoint que la escriba (el módulo RBAC expone `GET /permissions`
    pero no un `POST`).
  * **Invariantes** (§5.2) — sobre `role_permission`, que **sí** muta en runtime
    desde la pantalla de administración. Por eso no se testea igualdad contra
    `ROLES` (daría falso positivo cada vez que alguien hace su trabajo), sino las
    reglas que no deben romperse nunca.
"""
import pytest
from sqlalchemy import text

from app.auth.authz import MAQUINA, SCOPES_POR_RUTA, SOLO_AUTENTICADO
from app.auth.public import PUBLIC_ROUTES
from app.auth.scopes import CRITICOS, DESCRIPCIONES, ROLES, ROLES_OBSOLETOS, Scope


# ─────────────────────────────────────────────────────────────────────────────
# Estructurales
# ─────────────────────────────────────────────────────────────────────────────

def _rutas_reales(app) -> set[tuple[str, str]]:
    rutas = set()
    for r in app.routes:
        if not hasattr(r, "dependant"):
            continue
        for metodo in r.methods - {"HEAD", "OPTIONS"}:
            rutas.add((metodo, r.path))
    return rutas


def test_todo_endpoint_declara_autorizacion(app):
    """Ninguna ruta sin decisión explícita de autorización.

    Este es el test que hace innecesario acordarse: un endpoint nuevo que no pase
    por `authz.py` ni por `public.py` rompe el build. En runtime además falla
    cerrado (403 con RBAC_ENFORCE), pero para eso ya tiene que estar desplegado.
    """
    declaradas = set(SCOPES_POR_RUTA) | PUBLIC_ROUTES
    sin_declarar = _rutas_reales(app) - declaradas

    assert not sin_declarar, (
        "Rutas sin autorización declarada. Agregalas a "
        "app/auth/authz.py::SCOPES_POR_RUTA o, si de verdad son públicas, a "
        f"app/auth/public.py::PUBLIC_ROUTES:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(sin_declarar))
    )


def test_no_hay_rutas_declaradas_de_mas(app):
    """La tabla no describe rutas que ya no existen.

    Una entrada huérfana no rompe nada en runtime, pero desactualiza la matriz y
    hace que el test de arriba deje de significar lo que dice.
    """
    huerfanas = (set(SCOPES_POR_RUTA) | PUBLIC_ROUTES) - _rutas_reales(app)
    assert not huerfanas, (
        "Entradas que no corresponden a ninguna ruta:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(huerfanas))
    )


def test_la_allowlist_publica_no_crecio():
    """Lo público solo crece con una justificación explícita en el diff.

    Trece rutas:

      * **8 de autenticación y alta** — login/refresh/logout web, SSO legacy,
        login/refresh del app, y el alta pública de registro con su adjunto.
      * **5 del portal**, agregadas el 2026-08-05 — noticias (listado, detalle y
        adjuntos), publicidad y obras sociales. El default cerrado de B1 las
        había dejado en 401 y eso rompió el sitio para los visitantes anónimos.

    Las cinco del portal **recortan la respuesta** para quien no manda token:
    noticias oculta borradores, publicidad oculta avisos inactivos, y obras
    sociales oculta CUIT, contactos y condiciones del convenio. Que estén acá
    significa "no exige token", no "devuelve todo" — lo verifica
    `test_los_endpoints_publicos_del_portal_recortan_la_respuesta`.
    """
    assert len(PUBLIC_ROUTES) <= 13, sorted(PUBLIC_ROUTES)


def test_los_endpoints_publicos_del_portal_recortan_la_respuesta():
    """Cada ruta pública del portal tiene que mirar quién pregunta.

    Es la mitad que hace que abrirlas no sea una filtración. Si alguien saca el
    `usuario_opcional` de uno de estos handlers, el endpoint sigue respondiendo
    200 —no se rompe nada visible— pero pasa a entregar borradores, avisos dados
    de baja o el CUIT y los contactos de cada obra social. Este test es lo único
    que lo detecta.
    """
    import inspect

    from app.modules.catalogs import routes_obras_sociales
    from app.modules.contenido import routes_noticias, routes_publicidad

    for modulo, handlers in (
        (routes_noticias, ("list_noticias", "obtener_noticia", "listar_documentos_noticia")),
        (routes_publicidad, ("listar_publicidades",)),
        (routes_obras_sociales, ("list_obras_sociales",)),
    ):
        for nombre in handlers:
            fuente = inspect.getsource(getattr(modulo, nombre))
            assert "usuario_opcional" in fuente, (
                f"{modulo.__name__}.{nombre} es público y ya no distingue al "
                "visitante anónimo del usuario autenticado"
            )


def test_scopes_del_catalogo_tienen_descripcion():
    """Sin esto la pantalla de administración muestra códigos pelados."""
    faltan = [s.value for s in Scope if s not in DESCRIPCIONES]
    assert not faltan, faltan


def test_las_alternativas_de_scope_son_del_catalogo():
    """Las entradas con tupla solo pueden contener `Scope`.

    La matriz acepta `(Scope.A, Scope.B)` con semántica "cualquiera alcanza".
    Es una puerta abierta a debilitar un endpoint sin que se note en el diff:
    una tupla larga es un endpoint casi sin autorización. El test no impide
    usarlas, pero sí que se cuelen strings sueltos o marcas mezcladas, y deja el
    número de alternativas a la vista.
    """
    malos = []
    for (metodo, ruta), req in SCOPES_POR_RUTA.items():
        if not isinstance(req, tuple):
            continue
        if not all(isinstance(s, Scope) for s in req):
            malos.append(f"{metodo} {ruta}: la tupla tiene algo que no es Scope")
        if len(req) > 2:
            malos.append(f"{metodo} {ruta}: {len(req)} scopes alternativos, revisar")
    assert not malos, "\n".join(malos)


def test_convencion_de_nombres():
    """`<recurso>:<accion>`, recurso en singular y acción del conjunto cerrado."""
    acciones_validas = {
        "leer", "crear", "editar", "eliminar",
        # específicas: operaciones irreversibles o con efecto financiero
        "cerrar", "reabrir", "emitir", "anular", "aplicar", "cargar",
        "refacturar", "periodo", "complementar", "masivo", "gestionar",
        "purgar", "generar", "resolver", "documento", "leer_sensible",
        "leer_propio", "editar_bancario",
        # `ingresar` es de panel:ingresar, la bandera temporal de la prueba
        # controlada del panel nuevo (ver Scope.PANEL_INGRESAR).
        "ingresar",
    }
    malos = []
    for s in Scope:
        if s.value.count(":") != 1:
            malos.append(f"{s.value}: debe tener exactamente un ':'")
            continue
        recurso, accion = s.value.split(":")
        if recurso != recurso.lower() or not recurso.isascii():
            malos.append(f"{s.value}: recurso en minúsculas y sin acentos")
        if recurso.endswith("s") and recurso not in ("acceso",):
            malos.append(f"{s.value}: recurso en singular ({recurso})")
        if accion not in acciones_validas:
            malos.append(f"{s.value}: acción '{accion}' fuera del conjunto cerrado")
    assert not malos, "\n".join(malos)


@pytest.mark.asyncio
async def test_catalogo_sincronizado_con_la_base(db):
    """Todo `Scope` existe en `permissions`.

    Es el test que habría detectado `medicos:eliminar` el día que se escribió:
    el código lo referenciaba, la tabla no lo tenía, y el resultado era un
    `DELETE /api/medicos/{id}` inalcanzable para todos — con un 403 como único
    síntoma, indistinguible de un permiso faltante legítimo.

    Se verifica en una sola dirección (código ⊆ base) mientras dure la Etapa 0:
    los 28 códigos viejos siguen en la tabla a propósito, para que los tokens en
    circulación no se rompan. La Etapa 3 los borra y ahí pasa a ser igualdad.
    """
    en_db = {c for (c,) in (await db.execute(text("SELECT code FROM permissions"))).all()}
    faltan = {s.value for s in Scope} - en_db
    assert not faltan, (
        "Scopes declarados en el código que no existen en `permissions` — "
        f"esos endpoints son inalcanzables para todos: {sorted(faltan)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Invariantes sobre role_permission (que sí muta en runtime)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_el_rol_contador_no_existe(db):
    """A14: `contador` se eliminó y no puede volver.

    Era el único rol fuera de `admin` con `rbac:gestionar` en producción:
    cualquiera con ese rol podía otorgarse —o dar a otro— cualquier permiso del
    sistema, de forma persistente. Escalada a administrador total con una sola
    fila de `role_permission`.

    El rol se borró entero en la migración `s3s10n3sr3v0`. La pantalla de
    administración no tiene endpoint para crear roles, así que reaparecer
    implicaría un INSERT manual — y este test lo detecta.
    """
    en_db = {
        n for (n,) in (await db.execute(text("SELECT name FROM roles"))).all()
    }
    revividos = en_db & ROLES_OBSOLETOS
    assert not revividos, (
        f"Roles dados de baja que volvieron a la tabla `roles`: {sorted(revividos)}"
    )


@pytest.mark.asyncio
async def test_solo_admin_administra_permisos(codigos_por_rol):
    """Ningún rol salvo `admin` puede otorgarse permisos a sí mismo.

    Es la invariante general de la que A14 fue el caso concreto: cualquier rol
    con `rbac:gestionar` es, en la práctica, administrador. Sigue vigente
    aunque `contador` ya no exista, porque el permiso se puede asignar a
    cualquier otro rol desde la pantalla de administración.
    """
    con_permiso = {
        rol for rol, codes in codigos_por_rol.items()
        if Scope.RBAC_GESTIONAR.value in codes
    }
    assert con_permiso <= {"admin"}, (
        f"Roles con rbac:gestionar además de admin: {sorted(con_permiso - {'admin'})}"
    )


@pytest.mark.asyncio
async def test_permisos_criticos_no_van_por_rol(codigos_por_rol):
    """Los cuatro peligrosos se otorgan nominalmente por usuario.

    Reescribir el tarifario completo, cambiar el CBU contra el que se paga,
    reabrir un pago ya conciliado y borrar el rastro de auditoría. Que vayan por
    `UserPermission.allow` deja constancia de quién puede hacer cada una.
    """
    malos = {
        (rol, code)
        for rol, codes in codigos_por_rol.items()
        for code in codes
        if code in {s.value for s in CRITICOS} and rol != "admin"
    }
    assert not malos, f"Permisos críticos otorgados por rol: {sorted(malos)}"


@pytest.mark.asyncio
async def test_el_rol_medico_no_puede_ver_a_otros_medicos(codigos_por_rol):
    """`medico:leer` es la llave que `ownership.py` usa para operar sobre OTRO.

    Si el rol base la tuviera, el control de propiedad quedaría anulado en todos
    los módulos a la vez: cada colegiado podría pasar el `medico_id` de
    cualquier otro y el helper lo dejaría pasar. Es una sola fila en
    `role_permission` de distancia, y el síntoma sería silencioso.
    """
    codigos = codigos_por_rol.get("medico", set())
    assert Scope.MEDICO_LEER.value not in codigos, (
        "El rol `medico` tiene medico:leer — eso anula el control de propiedad "
        "de app/auth/ownership.py en medicos, padrones, pagos y facturacion."
    )
    # Y lo que sí tiene que tener: el permiso acotado a lo propio (A4). Sin él
    # el colegiado no puede ni ver su propio legajo.
    assert Scope.MEDICO_LEER_PROPIO.value in codigos, (
        "El rol `medico` no tiene medico:leer_propio — su propio perfil, "
        "adjuntos y padrón le responden 403."
    )


@pytest.mark.asyncio
async def test_roles_del_codigo_existen_en_la_base(codigos_por_rol):
    """`ROLES` es el seed; los roles que nombra tienen que estar creados.

    No se verifica igualdad de permisos a propósito: `role_permission` muta desde
    la pantalla de administración y esa divergencia es legítima. Ver
    docs/api/RBAC_PROPUESTA.md §5.1.
    """
    faltan = set(ROLES) - set(codigos_por_rol)
    assert not faltan, f"Roles declarados en el código y ausentes en la base: {sorted(faltan)}"


# ─────────────────────────────────────────────────────────────────────────────
# Comportamiento
# ─────────────────────────────────────────────────────────────────────────────

def test_el_cron_llega_a_su_endpoint(app, monkeypatch):
    """El disparador del cron no lleva JWT y no debe pedirlo.

    Regresión real: al aplicar B1 (auth global) este endpoint quedó detrás de
    `enforce_authz`, que exige Bearer antes de que corra su propia validación de
    `X-Cron-Secret`. El cron diario que cierra los períodos vencidos venía
    respondiendo 401 desde entonces.
    """
    from fastapi.testclient import TestClient
    from app.core.config import settings

    monkeypatch.setattr(settings, "CRON_SECRET", "secreto-de-prueba")
    cliente = TestClient(app, raise_server_exceptions=False)

    r = cliente.post(
        "/api/facturacion/periodo-medico/cerrar-vencidos",
        headers={"X-Cron-Secret": "secreto-de-prueba"},
    )
    assert r.status_code != 401, (
        "El cron vuelve a estar bloqueado por la auth global; revisá la marca "
        "MAQUINA en app/auth/authz.py"
    )

    # Y el secreto sigue siendo obligatorio.
    r = cliente.post("/api/facturacion/periodo-medico/cerrar-vencidos")
    assert r.status_code == 401


def test_endpoint_sin_token_responde_401(app):
    from fastapi.testclient import TestClient

    cliente = TestClient(app, raise_server_exceptions=False)
    assert cliente.get("/api/medicos/1").status_code == 401
    assert cliente.get("/api/pagos/").status_code == 401
