"""Autorización por endpoint: qué scope exige cada ruta.

## Por qué una tabla central y no un decorador por endpoint

`RBAC_PROPUESTA.md` §5 proponía `dependencies=[Depends(require_scope(...))]` en
cada ruta. Para 301 endpoints repartidos en 30 archivos, y **sin suite de tests
previa que atrape un error de dedo**, eso es 301 oportunidades de equivocarse en
silencio. Una tabla tiene tres ventajas concretas para este retrofit:

  1. **Es revisable de una sentada.** La matriz de autorización completa entra en
     una pantalla, ordenada por módulo. Un decorador suelto en la línea 1.403 de
     `medicos/routes.py` no se compara con nada.
  2. **El test de cobertura es trivial y exhaustivo**: toda ruta de `app.routes`
     tiene que estar acá o en `PUBLIC_ROUTES`. Un endpoint nuevo sin entrada
     **rompe el test** — y en `RBAC_ENFORCE=True` además responde 403, porque la
     tabla falla cerrada.
  3. **No hay dos mecanismos.** Los 44 `require_scope` que había quedaron
     migrados acá; sus códigos cambiaban igual por el renombre del catálogo
     (`medicos:leer` → `medico:leer`, `auditoria:ver` → `auditoria:leer`), así
     que la tabla no agregó trabajo: lo reemplazó.

El costo es que el scope no se ve al lado del handler. Lo compensa el test: no se
puede agregar un endpoint sin tocar este archivo.

`require_scope()` sigue existiendo en `app/auth/deps.py` y es válido para casos
puntuales, pero la tabla es la fuente de verdad y el test la exige.

## Modo observación

Con `RBAC_ENFORCE=False` (el default) **no se rechaza nada**: cuando a un usuario
le falta el scope se emite un `WARNING` con `rid`, socio, método, ruta y scope
faltante, y la request sigue. Eso permite una o dos semanas de tráfico real para
descubrir qué rol necesita qué, sin romper pantallas del front.

Pasar a `RBAC_ENFORCE=True` activa el 403. Ver `docs/api/RBAC_PROPUESTA.md` §7.
"""
import logging
from typing import Final

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import bearer, get_current_user
from app.auth.public import is_public
from app.auth.scopes import Scope
from app.auth.sessions import access_revocado
from app.core.config import settings
from app.db.database import get_db

log = logging.getLogger(__name__)


class _Marca(str):
    """Marcador para rutas que no se cierran con un scope del catálogo."""
    __slots__ = ()


# Basta con estar autenticado: la restricción real es de propiedad y la impone
# el handler (app/auth/ownership.py). Ej.: registrar el propio dispositivo.
SOLO_AUTENTICADO: Final = _Marca("<solo-autenticado>")

# El endpoint trae su propia autenticación de máquina y NO lleva JWT.
# Hoy solo el disparador del cron, que valida `X-Cron-Secret` y falla cerrado.
MAQUINA: Final = _Marca("<auth-de-maquina>")


class _Todos(tuple):
    """Tupla con semántica **Y**: hacen falta todos los scopes, no cualquiera.

    Una tupla pelada en la matriz significa "cualquiera de estos alcanza", que
    es lo correcto para `(medico:leer, medico:leer_propio)` — dos permisos sobre
    el mismo endpoint que se distinguen por el alcance. Pero para los exports
    hace falta lo contrario: `export:generar` **se suma** al permiso de lectura
    del dominio, no lo reemplaza. Sin esto, poner `export:generar` a secas en
    `GET /api/deducciones/export` dejaba que alguien volcara el histórico
    completo de deducciones sin tener `deduccion:leer`.
    """
    __slots__ = ()


def TODOS(*scopes: Scope) -> _Todos:
    """`TODOS(A, B)` — el usuario tiene que tener A **y** B."""
    return _Todos(scopes)


# ─────────────────────────────────────────────────────────────────────────────
# Matriz de autorización — (método, template de ruta) → scope
#
# El template es `route.path`, con los parámetros sin resolver, igual que en
# PUBLIC_ROUTES. Las 8 rutas públicas NO van acá: viven en app/auth/public.py.
#
# El valor puede ser una **tupla de scopes**, que se lee como "cualquiera de
# estos alcanza". Se usa solo para el par `(medico:leer, medico:leer_propio)`:
# son dos permisos sobre el mismo endpoint que se distinguen por el alcance, no
# por la acción, y esa distinción no la puede hacer la matriz porque depende del
# `medico_id` que venga en la request. La matriz deja pasar a los dos y el
# handler llama a `medico_objetivo()`, que le devuelve al de `leer_propio` su
# propio id y le rechaza cualquier otro con 403. Ver A4 y app/auth/ownership.py.
# ─────────────────────────────────────────────────────────────────────────────

# Lectura del legajo de un médico: la administrativa (cualquier médico) y la
# propia (solo el del token). Quién puede ver a quién lo decide el handler.
LECTURA_MEDICO: Final = (Scope.MEDICO_LEER, Scope.MEDICO_LEER_PROPIO)
LECTURA_PADRON: Final = (Scope.PADRON_LEER, Scope.MEDICO_LEER_PROPIO)
LECTURA_DOCUMENTOS: Final = (Scope.MEDICO_DOCUMENTO, Scope.MEDICO_LEER_PROPIO)

# Facturación: la administrativa (cualquier médico) y la propia (solo el del
# token). **Solo se usa en rutas donde el handler realmente acota**, o sea las
# que llaman a `socio_objetivo()` / `filtro_socio()`, más los punteros de
# período, que no exponen datos de nadie. Poner esta tupla en una ruta que no
# filtra le abre al socio la facturación de todos los colegas.
LECTURA_FACTURACION: Final = (Scope.FACTURACION_LEER, Scope.FACTURACION_LEER_PROPIO)

SCOPES_POR_RUTA: dict[tuple[str, str], Scope | tuple[Scope, ...] | _Marca] = {
    # ── Sistema: RBAC ────────────────────────────────────────────────────────
    ("GET", "/api/admin/rbac/permissions"): Scope.RBAC_GESTIONAR,
    ("GET", "/api/admin/rbac/roles"): Scope.RBAC_GESTIONAR,
    ("GET", "/api/admin/rbac/roles/{role_name}/permissions"): Scope.RBAC_GESTIONAR,
    ("POST", "/api/admin/rbac/roles/{role_name}/permissions/{perm_code}"): Scope.RBAC_GESTIONAR,
    ("DELETE", "/api/admin/rbac/roles/{role_name}/permissions/{perm_code}"): Scope.RBAC_GESTIONAR,
    ("GET", "/api/admin/rbac/users/{user_id}/permissions/effective"): Scope.RBAC_GESTIONAR,
    ("GET", "/api/admin/rbac/users/{user_id}/permissions/overrides"): Scope.RBAC_GESTIONAR,
    ("POST", "/api/admin/rbac/users/{user_id}/permissions/{perm_code}"): Scope.RBAC_GESTIONAR,
    ("DELETE", "/api/admin/rbac/users/{user_id}/permissions/{perm_code}"): Scope.RBAC_GESTIONAR,
    ("GET", "/api/admin/rbac/users/{user_id}/roles"): Scope.RBAC_GESTIONAR,
    ("POST", "/api/admin/rbac/users/{user_id}/roles/{role_name}"): Scope.RBAC_GESTIONAR,
    ("DELETE", "/api/admin/rbac/users/{user_id}/roles/{role_name}"): Scope.RBAC_GESTIONAR,

    # ── Sistema: auditoría ───────────────────────────────────────────────────
    ("GET", "/api/auditoria/"): Scope.AUDITORIA_LEER,
    ("GET", "/api/auditoria/{audit_id}"): Scope.AUDITORIA_LEER,
    ("DELETE", "/api/auditoria/purge"): Scope.AUDITORIA_PURGAR,

    # ── Sistema: exports ─────────────────────────────────────────────────────
    # Este no lee nada de la base: recibe el JSON que el front ya tiene en
    # pantalla y devuelve el .xlsx. `export:generar` a secas es todo lo que
    # corresponde — no hay dominio que proteger además del formateo.
    ("POST", "/api/exports/exportar_excel_for_liquidacion"): Scope.EXPORT_GENERAR,

    # ── Adjuntos ─────────────────────────────────────────────────────────────
    # Un solo endpoint para todo `uploads/`, y el scope depende del
    # subdirectorio: `medicos/` exige medico:documento, `validaciones/`
    # medico:leer, `obras_sociales/` catalogo:leer. Eso no se puede expresar en
    # esta tabla, que indexa por ruta y no por parámetro, así que la decisión la
    # toma el handler — igual que el control de propiedad. Ver
    # app/modules/archivos/routes.py::_autorizar, que falla cerrado ante un
    # subdirectorio no declarado.
    ("GET", "/api/archivos/{ruta:path}"): SOLO_AUTENTICADO,

    # ── Avisos push ──────────────────────────────────────────────────────────
    ("GET", "/api/avisos/"): Scope.AVISO_GESTIONAR,
    ("POST", "/api/avisos/"): Scope.AVISO_GESTIONAR,
    ("GET", "/api/avisos/tipos"): Scope.AVISO_GESTIONAR,
    ("GET", "/api/avisos/{aviso_id}"): Scope.AVISO_GESTIONAR,
    ("PATCH", "/api/avisos/{aviso_id}"): Scope.AVISO_GESTIONAR,
    ("DELETE", "/api/avisos/{aviso_id}"): Scope.AVISO_GESTIONAR,
    ("POST", "/api/avisos/{aviso_id}/enviar"): Scope.AVISO_GESTIONAR,

    # ── Beneficios ───────────────────────────────────────────────────────────
    ("GET", "/api/beneficios/"): Scope.BENEFICIO_GESTIONAR,
    ("POST", "/api/beneficios/"): Scope.BENEFICIO_GESTIONAR,
    ("GET", "/api/beneficios/categorias"): Scope.BENEFICIO_GESTIONAR,
    ("GET", "/api/beneficios/{beneficio_id}"): Scope.BENEFICIO_GESTIONAR,
    ("PATCH", "/api/beneficios/{beneficio_id}"): Scope.BENEFICIO_GESTIONAR,
    ("DELETE", "/api/beneficios/{beneficio_id}"): Scope.BENEFICIO_GESTIONAR,
    # La vitrina del socio, no la administración: devuelve sólo los convenios
    # vigentes y no acepta filtros. `beneficio:gestionar` es del editor web y el
    # rol `medico` no lo tiene — exigirlo acá dejaba la pantalla en 403.
    ("GET", "/api/beneficios/vigentes"): SOLO_AUTENTICADO,

    # ── Catálogos: boletín / observaciones ───────────────────────────────────
    ("GET", "/api/boletin/observaciones"): Scope.CATALOGO_LEER,
    ("PUT", "/api/boletin/observaciones/{nro_obrasocial}"): Scope.CATALOGO_EDITAR,
    ("DELETE", "/api/boletin/observaciones/{nro_obrasocial}"): Scope.CATALOGO_EDITAR,
    ("GET", "/api/boletin/observaciones/plantillas"): Scope.CATALOGO_LEER,
    ("POST", "/api/boletin/observaciones/plantillas"): Scope.CATALOGO_EDITAR,
    ("DELETE", "/api/boletin/observaciones/plantillas/{id}"): Scope.CATALOGO_EDITAR,

    # ── Catálogos: obras sociales ────────────────────────────────────────────
    ("GET", "/api/obras_social/"): Scope.CATALOGO_LEER,
    ("POST", "/api/obras_social/"): Scope.CATALOGO_EDITAR,
    ("GET", "/api/obras_social/{id}"): Scope.CATALOGO_LEER,
    ("PATCH", "/api/obras_social/{id}"): Scope.CATALOGO_EDITAR,
    ("DELETE", "/api/obras_social/{id}"): Scope.CATALOGO_EDITAR,
    ("POST", "/api/obras_social/{id}/documentos"): Scope.CATALOGO_EDITAR,

    # Pagos y deuda de la obra social (pestaña «Pagos» del perfil): router
    # deshabilitado (ver app/api/routes.py), así que estas rutas no existen hoy.
    # Reactivar el router exige descomentar estas seis entradas también, o
    # test_no_hay_rutas_declaradas_de_mas() falla por entradas huérfanas.
    # ("GET", "/api/obras_social/{obra_id}/pagos"): Scope.CATALOGO_LEER,
    # ("POST", "/api/obras_social/{obra_id}/pagos"): Scope.CATALOGO_EDITAR,
    # ("PUT", "/api/obras_social/{obra_id}/pagos/{pago_id}"): Scope.CATALOGO_EDITAR,
    # ("DELETE", "/api/obras_social/{obra_id}/pagos/{pago_id}"): Scope.CATALOGO_EDITAR,
    # ("POST", "/api/obras_social/{obra_id}/pagos/{pago_id}/factura"): Scope.CATALOGO_EDITAR,
    # ("DELETE", "/api/obras_social/{obra_id}/pagos/{pago_id}/factura"): Scope.CATALOGO_EDITAR,
    ("DELETE", "/api/obras_social/{id}/documentos/{doc_id}"): Scope.CATALOGO_EDITAR,

    # ── Catálogos: especialidades y períodos ─────────────────────────────────
    ("GET", "/api/especialidades/"): Scope.CATALOGO_LEER,
    ("POST", "/api/especialidades/"): Scope.CATALOGO_EDITAR,
    ("PATCH", "/api/especialidades/{id}"): Scope.CATALOGO_EDITAR,
    ("GET", "/api/periodos/disponibles"): Scope.CATALOGO_LEER,
    ("GET", "/api/periodos/disponibles_lotes_ajustes"): Scope.CATALOGO_LEER,

    # ── Catálogos: valores boletín y éticos ──────────────────────────────────
    ("GET", "/api/valores/boletin"): Scope.CATALOGO_LEER,
    ("POST", "/api/valores/boletin"): Scope.CATALOGO_EDITAR,
    ("DELETE", "/api/valores/boletin/{id}"): Scope.CATALOGO_EDITAR,
    ("GET", "/api/valores/galenos"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/valores/nomenclado-swiss"): Scope.NOMENCLADOR_LEER,
    ("PATCH", "/api/valores/nomenclado-swiss/{id}"): Scope.NOMENCLADOR_EDITAR,
    ("DELETE", "/api/valores/nomenclado-swiss/{id}"): Scope.NOMENCLADOR_ELIMINAR,

    ("GET", "/api/valores-eticos/"): Scope.CATALOGO_LEER,
    ("POST", "/api/valores-eticos/"): Scope.CATALOGO_EDITAR,
    ("GET", "/api/valores-eticos/ultimo"): Scope.CATALOGO_LEER,
    ("DELETE", "/api/valores-eticos/{id}"): Scope.CATALOGO_EDITAR,

    # ── Contenido: noticias ──────────────────────────────────────────────────
    ("GET", "/api/noticias/"): Scope.CONTENIDO_LEER,
    ("POST", "/api/noticias/"): Scope.CONTENIDO_EDITAR,
    ("GET", "/api/noticias/{id}"): Scope.CONTENIDO_LEER,
    ("PUT", "/api/noticias/{id}"): Scope.CONTENIDO_EDITAR,
    ("DELETE", "/api/noticias/{id}"): Scope.CONTENIDO_EDITAR,
    ("GET", "/api/noticias/{id}/documentos"): Scope.CONTENIDO_LEER,
    ("DELETE", "/api/noticias/{noticia_id}/documentos/{doc_id}"): Scope.CONTENIDO_EDITAR,

    # ── Contenido: planillas de consulta ─────────────────────────────────────
    # Las lee el médico (CONTENIDO_LEER está en _LECTURA_COMUN) y las publica
    # quien administra contenido, igual que las noticias.
    ("GET", "/api/planillas/"): Scope.CONTENIDO_LEER,
    ("POST", "/api/planillas/"): Scope.CONTENIDO_EDITAR,
    ("PATCH", "/api/planillas/{planilla_id}"): Scope.CONTENIDO_EDITAR,
    ("DELETE", "/api/planillas/{planilla_id}"): Scope.CONTENIDO_EDITAR,

    # ── Datos del propio Colegio ─────────────────────────────────────────────
    # Se reusan los scopes de catálogo en vez de crear `institucion:*`: es el
    # permiso del personal administrativo, que es quien mantiene el CUIT, el
    # CBU y los contactos de la institución. No hay scope propio a propósito —
    # un código nuevo obliga a sincronizar `permissions` y `role_permission` en
    # las dos bases, y hasta que eso corra el endpoint queda inalcanzable para
    # todos.
    ("GET", "/api/institucion/"): Scope.CATALOGO_LEER,
    ("PUT", "/api/institucion/"): Scope.CATALOGO_EDITAR,
    ("GET", "/api/institucion/telefonos"): Scope.CATALOGO_LEER,
    ("POST", "/api/institucion/telefonos"): Scope.CATALOGO_EDITAR,
    ("PUT", "/api/institucion/telefonos/{telefono_id}"): Scope.CATALOGO_EDITAR,
    ("DELETE", "/api/institucion/telefonos/{telefono_id}"): Scope.CATALOGO_EDITAR,
    ("POST", "/api/institucion/mails"): Scope.CATALOGO_EDITAR,
    ("PUT", "/api/institucion/mails/{mail_id}"): Scope.CATALOGO_EDITAR,
    ("DELETE", "/api/institucion/mails/{mail_id}"): Scope.CATALOGO_EDITAR,

    # Las contraseñas de las casillas van aparte y **más arriba**: hoy
    # `rbac:gestionar` lo tiene sólo `admin`, que es el alcance pedido ("eso
    # sólo lo verá el personal autorizado"). Es un permiso prestado, no uno
    # propio: si algún día hace falta separar "administra permisos" de "puede
    # ver las claves del correo", se cambia acá y en ningún otro lado — los
    # handlers de `app/modules/institucion/routes.py` no miran scopes.
    #
    # `revelar` es POST y no GET para que `app/middleware/audit.py` lo registre:
    # el middleware sólo audita métodos mutantes.
    ("PUT", "/api/institucion/mails/{mail_id}/password"): Scope.RBAC_GESTIONAR,
    ("POST", "/api/institucion/mails/{mail_id}/password/revelar"): Scope.RBAC_GESTIONAR,

    # ── Calendarios: feriados, cumpleaños y tareas del mes ───────────────────
    ("GET", "/api/agenda/"): Scope.CATALOGO_LEER,
    ("GET", "/api/agenda/mes"): Scope.CATALOGO_LEER,
    # El listado del personal del Colegio para el selector de responsable. Va
    # con `medico:leer` —el scope administrativo para ver datos de otros— y no
    # con `catalogo:leer`, que el rol `medico` sí tiene: un socio no tiene por
    # qué obtener la nómina del personal.
    ("GET", "/api/agenda/responsables"): Scope.MEDICO_LEER,
    ("POST", "/api/agenda/"): Scope.CATALOGO_EDITAR,
    ("PUT", "/api/agenda/{evento_id}"): Scope.CATALOGO_EDITAR,
    ("DELETE", "/api/agenda/{evento_id}"): Scope.CATALOGO_EDITAR,

    # ── Contenido: publicidad ────────────────────────────────────────────────
    ("GET", "/api/publicidad-medicos/"): Scope.CONTENIDO_LEER,
    ("POST", "/api/publicidad-medicos/"): Scope.CONTENIDO_EDITAR,
    ("GET", "/api/publicidad-medicos/{id}"): Scope.CONTENIDO_LEER,
    ("PUT", "/api/publicidad-medicos/{id}"): Scope.CONTENIDO_EDITAR,
    ("DELETE", "/api/publicidad-medicos/{id}"): Scope.CONTENIDO_EDITAR,
    ("GET", "/api/publicidad-medicos/medicos/buscar"): Scope.MEDICO_LEER,

    # ── Solicitudes de registro ──────────────────────────────────────────────
    ("GET", "/api/solicitudes/"): Scope.SOLICITUD_LEER,
    ("GET", "/api/solicitudes/stats/counts"): Scope.SOLICITUD_LEER,
    ("GET", "/api/solicitudes/stats/monthly"): Scope.SOLICITUD_LEER,
    ("GET", "/api/solicitudes/{sid}"): Scope.SOLICITUD_LEER,
    ("POST", "/api/solicitudes/{sid}/approve"): Scope.SOLICITUD_RESOLVER,
    ("POST", "/api/solicitudes/{sid}/reject"): Scope.SOLICITUD_RESOLVER,

    # ── Solicitudes de cambio de datos ───────────────────────────────────────
    ("GET", "/api/solicitudes-cambio/"): Scope.SOLICITUD_LEER,
    ("GET", "/api/solicitudes-cambio/campos"): Scope.SOLICITUD_LEER,
    ("GET", "/api/solicitudes-cambio/{solicitud_id}"): Scope.SOLICITUD_LEER,
    ("POST", "/api/solicitudes-cambio/{solicitud_id}/approve"): Scope.SOLICITUD_RESOLVER,
    ("POST", "/api/solicitudes-cambio/{solicitud_id}/reject"): Scope.SOLICITUD_RESOLVER,
    # `router_socio` del mismo módulo: el médico abre y consulta SUS reclamos
    # desde "Mi perfil". No llevan scope porque el filtro es el propio token —
    # los cuatro handlers salen de `user.NRO_SOCIO` / `user.ID` y no aceptan un
    # id ajeno por parámetro. Es el mismo criterio que
    # ("POST", "/api/mobile/solicitudes-cambio"), que es su gemelo en el BFF.
    ("POST", "/api/solicitudes-cambio/"): SOLO_AUTENTICADO,
    ("POST", "/api/solicitudes-cambio/formulario"): SOLO_AUTENTICADO,
    ("GET", "/api/solicitudes-cambio/campos-editables"): SOLO_AUTENTICADO,
    ("GET", "/api/solicitudes-cambio/mias"): SOLO_AUTENTICADO,

    # ── Médicos ──────────────────────────────────────────────────────────────
    ("GET", "/api/medicos"): Scope.MEDICO_LEER,
    ("GET", "/api/medicos/all"): Scope.MEDICO_LEER,
    ("GET", "/api/medicos/count"): Scope.MEDICO_LEER,
    ("GET", "/api/medicos/{medico_id}"): LECTURA_MEDICO,
    ("PUT", "/api/medicos/{medico_id}"): Scope.MEDICO_EDITAR,
    ("DELETE", "/api/medicos/{medico_id}"): Scope.MEDICO_ELIMINAR,
    ("POST", "/api/medicos/admin/register"): Scope.MEDICO_CREAR,
    ("POST", "/api/medicos/admin/save-continue"): Scope.MEDICO_CREAR,
    ("POST", "/api/medicos/admin/register/{medico_id}/document"): Scope.MEDICO_DOCUMENTO,
    ("PATCH", "/api/medicos/{medico_id}/attach"): Scope.MEDICO_EDITAR,
    ("PATCH", "/api/medicos/{medico_id}/existe"): Scope.MEDICO_EDITAR,
    # Marca un socio como organización (clínica/sanatorio/servicio). Es editar
    # un campo del legajo, mismo scope que el resto de la edición.
    ("PATCH", "/api/medicos/{medico_id}/organizacion"): Scope.MEDICO_EDITAR,
    ("POST", "/api/medicos/{medico_id}/reset-password"): Scope.MEDICO_EDITAR,
    ("GET", "/api/medicos/{medico_id}/conceptos"): LECTURA_PADRON,
    ("GET", "/api/medicos/{medico_id}/deuda"): LECTURA_MEDICO,
    ("POST", "/api/medicos/{medico_id}/deudas_manual"): Scope.MEDICO_EDITAR,
    # Los adjuntos son escaneos de DNI, título y constancia de CBU: la lectura
    # va con el mismo permiso que el alta, no con MEDICO_LEER. El médico ve los
    # propios con MEDICO_LEER_PROPIO; el handler no le deja pedir otro id.
    ("GET", "/api/medicos/{medico_id}/documentos"): LECTURA_DOCUMENTOS,
    ("POST", "/api/medicos/{medico_id}/documentos"): Scope.MEDICO_DOCUMENTO,
    ("DELETE", "/api/medicos/{medico_id}/documentos/{doc_id}"): Scope.MEDICO_DOCUMENTO,
    ("GET", "/api/medicos/{medico_id}/especialidades"): LECTURA_MEDICO,
    ("POST", "/api/medicos/{medico_id}/especialidades"): Scope.MEDICO_EDITAR,
    ("PATCH", "/api/medicos/{medico_id}/especialidades/{id_colegio}"): Scope.MEDICO_EDITAR,
    ("DELETE", "/api/medicos/{medico_id}/especialidades/{id_colegio}"): Scope.MEDICO_EDITAR,
    # Control de calidad del padrón: sólo lectura, señala legajos con problemas.
    # Traían `require_scope("medicos:leer")` hardcodeado — un código del catálogo
    # viejo que ya no existe en `permissions`, o sea 403 para todos, admin
    # incluido. El scope correcto es el del padrón administrativo.
    ("GET", "/api/medicos/auditoria/resumen"): Scope.MEDICO_LEER,
    ("GET", "/api/medicos/auditoria/{chequeo_id}"): Scope.MEDICO_LEER,

    # ── Reportes y estadísticas ──────────────────────────────────────────────
    # Todo el módulo cruza la facturación de todos los colegiados. Traía
    # `require_scope("facturas:ver")` a nivel router — otro código muerto del
    # catálogo viejo. Ver la nota de Scope.REPORTE_LEER sobre por qué no reusa
    # `facturacion:leer`.
    ("GET", "/api/reportes/resumen"): Scope.REPORTE_LEER,
    ("GET", "/api/reportes/codigos"): Scope.REPORTE_LEER,
    ("GET", "/api/reportes/codigos/{codigo}/medicos"): Scope.REPORTE_LEER,
    ("GET", "/api/reportes/medicos"): Scope.REPORTE_LEER,
    ("GET", "/api/reportes/medicos/{nro_socio}/codigos"): Scope.REPORTE_LEER,
    ("GET", "/api/reportes/obras-sociales"): Scope.REPORTE_LEER,
    ("GET", "/api/reportes/prestaciones"): Scope.REPORTE_LEER,
    ("GET", "/api/reportes/evolucion"): Scope.REPORTE_LEER,

    # ── Padrones ─────────────────────────────────────────────────────────────
    ("GET", "/api/padrones/catalogo"): Scope.CATALOGO_LEER,
    ("GET", "/api/padrones/obras-sociales/{nro_os}/medicos"): Scope.PADRON_LEER,
    ("GET", "/api/padrones/obras-sociales/{nro_os}/medicos/export"): Scope.PADRON_LEER,
    ("GET", "/api/padrones/{nro_socio}"): LECTURA_PADRON,
    ("GET", "/api/padrones/{medico_id}/asignaciones"): LECTURA_PADRON,
    ("POST", "/api/padrones/{medico_id}/asignaciones/concepto"): Scope.PADRON_EDITAR,
    ("DELETE", "/api/padrones/{medico_id}/asignaciones/concepto/{nro_concepto}"): Scope.PADRON_EDITAR,
    ("POST", "/api/padrones/{medico_id}/asignaciones/especialidad"): Scope.PADRON_EDITAR,
    ("DELETE", "/api/padrones/{medico_id}/asignaciones/especialidad/{esp_id}"): Scope.PADRON_EDITAR,
    ("POST", "/api/padrones/{medico_id}/obras-sociales/{nro_os}"): Scope.PADRON_EDITAR,
    ("DELETE", "/api/padrones/{medico_id}/obras-sociales/{nro_os}"): Scope.PADRON_EDITAR,

    # ── Pagos ────────────────────────────────────────────────────────────────
    ("GET", "/api/pagos/"): Scope.PAGO_LEER,
    ("POST", "/api/pagos/"): Scope.PAGO_CREAR,
    ("GET", "/api/pagos/{pago_id}"): Scope.PAGO_LEER,
    ("PUT", "/api/pagos/{pago_id}"): Scope.PAGO_EDITAR,
    ("DELETE", "/api/pagos/{pago_id}"): Scope.PAGO_ELIMINAR,
    ("POST", "/api/pagos/{pago_id}/cerrar"): Scope.PAGO_CERRAR,
    ("POST", "/api/pagos/{pago_id}/reabrir"): Scope.PAGO_REABRIR,
    ("GET", "/api/pagos/{pago_id}/informe/{tipo}"): Scope.PAGO_LEER,
    ("GET", "/api/pagos/{pago_id}/pago_medico_actualizado"): Scope.PAGO_LEER,
    ("GET", "/api/pagos/{pago_id}/vista_previa"): Scope.PAGO_LEER,
    ("GET", "/api/pagos/{pago_id}/recibos"): Scope.RECIBO_LEER,
    ("DELETE", "/api/pagos/{pago_id}/recibos"): Scope.RECIBO_ANULAR,
    ("POST", "/api/pagos/{pago_id}/recibos/generar"): Scope.RECIBO_EMITIR,
    ("POST", "/api/pagos/{pago_id}/recibos/emitir_todos"): Scope.RECIBO_EMITIR,
    ("PATCH", "/api/pagos/{pago_id}/recibos/estado"): Scope.RECIBO_EMITIR,

    # ── Liquidación ──────────────────────────────────────────────────────────
    ("GET", "/api/liquidacion/liquidaciones_por_os/"): Scope.LIQUIDACION_LEER,
    ("POST", "/api/liquidacion/liquidaciones_por_os/crear"): Scope.LIQUIDACION_CREAR,
    ("GET", "/api/liquidacion/liquidaciones_por_os/{liquidacion_id}"): Scope.LIQUIDACION_LEER,
    ("DELETE", "/api/liquidacion/liquidaciones_por_os/{liquidacion_id}"): Scope.LIQUIDACION_ELIMINAR,
    ("GET", "/api/liquidacion/liquidaciones_por_os/{liquidacion_id}/detalles"): Scope.LIQUIDACION_LEER,
    ("GET", "/api/liquidacion/liquidaciones_por_os/{liquidacion_id}/detalles_vista"): Scope.LIQUIDACION_LEER,
    ("GET", "/api/liquidacion/recibos/{recibo_id}"): Scope.RECIBO_LEER,
    ("GET", "/api/liquidacion/recibos/{recibo_id}/detalle"): Scope.RECIBO_LEER,
    ("PUT", "/api/liquidacion/recibos/{recibo_id}/anular"): Scope.RECIBO_ANULAR,

    # ── Lotes de ajuste ──────────────────────────────────────────────────────
    ("GET", "/api/lotes/snaps/lista"): Scope.LOTE_LEER,
    ("GET", "/api/lotes/snaps/buscar_atenciones"): Scope.LOTE_LEER,
    ("GET", "/api/lotes/snaps/por_os_periodo"): Scope.LOTE_LEER,
    ("GET", "/api/lotes/snaps/{lote_id}"): Scope.LOTE_LEER,
    ("POST", "/api/lotes/snaps/obtener_o_crear"): Scope.LOTE_CREAR,
    ("POST", "/api/lotes/snaps/{lote_id}/items"): Scope.LOTE_CREAR,
    ("PATCH", "/api/lotes/snaps/{lote_id}/estado"): Scope.LOTE_EDITAR,
    ("PUT", "/api/lotes/snaps/{lote_id}/items/{ajuste_id}"): Scope.LOTE_EDITAR,
    ("DELETE", "/api/lotes/snaps/{lote_id}"): Scope.LOTE_ELIMINAR,
    ("DELETE", "/api/lotes/snaps/{lote_id}/items/{ajuste_id}"): Scope.LOTE_ELIMINAR,
    ("POST", "/api/lotes/snaps/crear_refacturacion"): Scope.LOTE_REFACTURAR,
    ("POST", "/api/lotes/snaps/crear_sin_factura"): Scope.LOTE_REFACTURAR,

    # ── Deducciones ──────────────────────────────────────────────────────────
    ("GET", "/api/deducciones/"): Scope.DEDUCCION_LEER,
    ("POST", "/api/deducciones/"): Scope.DEDUCCION_CREAR,
    # Sí lee de la base, y sin paginación: es el histórico completo de
    # deducciones en una sola respuesta. Por eso va con los dos permisos —
    # `deduccion:leer` por los datos, `export:generar` por el volcado.
    ("GET", "/api/deducciones/export"): TODOS(Scope.DEDUCCION_LEER, Scope.EXPORT_GENERAR),
    ("GET", "/api/deducciones/top-deudores"): Scope.DEDUCCION_LEER,
    ("GET", "/api/deducciones/por_pago/{pago_id}"): Scope.DEDUCCION_LEER,
    ("GET", "/api/deducciones/por_pago/{pago_id}/aplicadas"): Scope.DEDUCCION_LEER,
    ("GET", "/api/deducciones/{id}"): Scope.DEDUCCION_LEER,
    ("PATCH", "/api/deducciones/{id}"): Scope.DEDUCCION_EDITAR,
    ("DELETE", "/api/deducciones/{id}"): Scope.DEDUCCION_ELIMINAR,
    ("POST", "/api/deducciones/{id}/pagar"): Scope.DEDUCCION_APLICAR,
    ("POST", "/api/deducciones/{pago_id}/colegio/bulk_generar_descuento/{desc_id}"): Scope.DEDUCCION_APLICAR,
    ("POST", "/api/deducciones/{pago_id}/deducciones/refrescar"): Scope.DEDUCCION_APLICAR,
    ("DELETE", "/api/deducciones/{pago_id}/colegio/deshacer"): Scope.DEDUCCION_ELIMINAR,
    ("DELETE", "/api/deducciones/{pago_id}/colegio/deshacer/{desc_id}"): Scope.DEDUCCION_ELIMINAR,
    # Asignación de socios a descuentos: es catálogo de descuentos, no deducción.
    ("GET", "/api/deducciones/socios"): Scope.DESCUENTO_LEER,
    ("POST", "/api/deducciones/socios"): Scope.DESCUENTO_CREAR,
    ("GET", "/api/deducciones/socios/{socio_descuento_id}"): Scope.DESCUENTO_LEER,
    ("PATCH", "/api/deducciones/socios/{socio_descuento_id}"): Scope.DESCUENTO_EDITAR,
    ("PATCH", "/api/deducciones/socios/{socio_descuento_id}/pagador"): Scope.DESCUENTO_EDITAR,
    ("DELETE", "/api/deducciones/socios/{socio_descuento_id}"): Scope.DESCUENTO_ELIMINAR,

    # ── Descuentos ───────────────────────────────────────────────────────────
    ("GET", "/api/descuentos"): Scope.DESCUENTO_LEER,
    ("POST", "/api/descuentos"): Scope.DESCUENTO_CREAR,
    ("GET", "/api/descuentos/by_nro/{nro_colegio}"): Scope.DESCUENTO_LEER,
    ("GET", "/api/descuentos/{desc_id}"): Scope.DESCUENTO_LEER,
    ("PATCH", "/api/descuentos/{desc_id}"): Scope.DESCUENTO_EDITAR,
    ("DELETE", "/api/descuentos/{desc_id}"): Scope.DESCUENTO_ELIMINAR,

    # ── Cobranzas (panel de deuda por concepto, solo lectura) ────────────────
    # Rutas estáticas antes que las dinámicas por la misma razón que en
    # Deducciones: /export y /por_concepto tienen que matchear antes que
    # cualquier ruta con {id}.
    ("GET", "/api/cobranzas/resumen"): Scope.COBRANZA_LEER,
    ("GET", "/api/cobranzas/por_concepto"): Scope.COBRANZA_LEER,
    ("GET", "/api/cobranzas/por_socio"): Scope.COBRANZA_LEER,
    ("GET", "/api/cobranzas/export"): TODOS(Scope.COBRANZA_LEER, Scope.EXPORT_GENERAR),
    ("GET", "/api/cobranzas/por_concepto/{descuento_id}/medicos"): Scope.COBRANZA_LEER,
    ("GET", "/api/cobranzas/medicos/{medico_id}"): Scope.COBRANZA_LEER,

    # ── Facturación ──────────────────────────────────────────────────────────
    ("GET", "/api/facturacion/afiliados"): Scope.FACTURACION_LEER,
    ("POST", "/api/facturacion/afiliados"): Scope.FACTURACION_CARGAR,
    ("GET", "/api/facturacion/afiliados/{dni:path}"): Scope.FACTURACION_LEER,
    ("DELETE", "/api/facturacion/afiliados/{dni:path}"): Scope.FACTURACION_CARGAR,
    ("GET", "/api/facturacion/clinicas"): Scope.FACTURACION_LEER,
    ("GET", "/api/facturacion/medicos"): Scope.FACTURACION_LEER,
    ("GET", "/api/facturacion/medicos/todos"): Scope.FACTURACION_LEER,
    ("GET", "/api/facturacion/obras-sociales"): Scope.CATALOGO_LEER,
    ("GET", "/api/facturacion/obras-sociales/todas"): Scope.CATALOGO_LEER,
    ("GET", "/api/facturacion/nomenclador"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/facturacion/nomenclador/precio"): Scope.NOMENCLADOR_LEER,
    # `socio_objetivo()`: el {nro_socio} del path se valida contra el del token.
    ("GET", "/api/facturacion/medico/{nro_socio}/codigos-habilitados"): LECTURA_FACTURACION,
    # Carga del **médico** desde el portal (actor=ORIGEN_MEDICO), no del
    # circuito administrativo. Va con el permiso del prestador: el rol
    # `medico` ya no lleva `facturacion:cargar`, que además habilitaba editar
    # y borrar prestaciones de cualquiera.
    ("POST", "/api/facturacion/medico/prestaciones"): Scope.VALIDACION_CARGAR,
    ("GET", "/api/facturacion/facturas"): Scope.FACTURACION_LEER,
    ("GET", "/api/facturacion/facturas/{id}/detalle"): Scope.FACTURACION_LEER,
    ("POST", "/api/facturacion/facturas/complemento"): Scope.FACTURACION_COMPLEMENTAR,
    ("POST", "/api/facturacion/prestaciones-complementaria"): Scope.FACTURACION_COMPLEMENTAR,
    # `filtro_socio()`: sin `medico:leer` el filtro se fuerza al socio del token.
    ("GET", "/api/facturacion/prestaciones"): LECTURA_FACTURACION,
    ("POST", "/api/facturacion/prestaciones"): Scope.FACTURACION_CARGAR,
    ("GET", "/api/facturacion/prestaciones/recientes"): Scope.FACTURACION_LEER,
    ("PATCH", "/api/facturacion/prestaciones/revisado"): Scope.FACTURACION_CARGAR,
    ("GET", "/api/facturacion/prestaciones/{id}"): Scope.FACTURACION_LEER,
    # Ficha completa de una prestación (pantalla de consulta, solo lectura).
    # `facturacion:leer` a secas y NO `LECTURA_FACTURACION`: la ficha es MÁS sensible
    # que `/prestaciones/{id}` — expone `usuario`, `calculo_snapshot` y el audit_log
    # con IPs y bodies. Mismo criterio de acceso administrativo que ese endpoint.
    ("GET", "/api/facturacion/prestaciones/{id}/ficha"): Scope.FACTURACION_LEER,
    ("PATCH", "/api/facturacion/prestaciones/{id}"): Scope.FACTURACION_CARGAR,
    ("DELETE", "/api/facturacion/prestaciones/{id}"): Scope.FACTURACION_CARGAR,
    ("POST", "/api/facturacion/cierre"): Scope.FACTURACION_CERRAR,
    ("POST", "/api/facturacion/cierre-doctor"): Scope.FACTURACION_CERRAR,
    ("GET", "/api/facturacion/cierre/preview"): Scope.FACTURACION_LEER,
    ("GET", "/api/facturacion/periodo-activo"): LECTURA_FACTURACION,
    ("GET", "/api/facturacion/periodo-colegio"): LECTURA_FACTURACION,
    ("GET", "/api/facturacion/periodo-medico"): LECTURA_FACTURACION,
    ("GET", "/api/facturacion/periodo-medico/punteros"): Scope.FACTURACION_LEER,
    ("POST", "/api/facturacion/periodo-medico/avanzar"): Scope.FACTURACION_PERIODO,
    ("POST", "/api/facturacion/periodo-medico/set"): Scope.FACTURACION_PERIODO,
    ("POST", "/api/facturacion/prestaciones/mover-periodo"): Scope.FACTURACION_PERIODO,
    # Lo dispara el cron con X-Cron-Secret, sin JWT. Ver la nota en MAQUINA.
    ("POST", "/api/facturacion/periodo-medico/cerrar-vencidos"): MAQUINA,

    # ── Validaciones (el permiso base del prestador) ─────────────────────────
    ("GET", "/api/validaciones/prestador"): Scope.VALIDACION_CARGAR,
    ("GET", "/api/validaciones/codigos"): Scope.VALIDACION_CARGAR,
    ("GET", "/api/validaciones/periodos"): Scope.VALIDACION_CARGAR,
    ("GET", "/api/validaciones/periodo-actual"): Scope.VALIDACION_CARGAR,
    ("GET", "/api/validaciones/prestaciones"): Scope.VALIDACION_CARGAR,
    ("POST", "/api/validaciones/prestaciones"): Scope.VALIDACION_CARGAR,
    ("DELETE", "/api/validaciones/prestaciones/{prestacion_id}"): Scope.VALIDACION_CARGAR,
    ("POST", "/api/validaciones/prestaciones/{prestacion_id}/orden"): Scope.VALIDACION_CARGAR,
    ("GET", "/api/validaciones/sancor/estado"): Scope.VALIDACION_CARGAR,
    ("GET", "/api/validaciones/nobis/afiliado"): Scope.VALIDACION_CARGAR,
    # Importar el padrón de OSPM: operación del Colegio, no del prestador —
    # mismo scope que el resto de las mutaciones de padrón.
    ("POST", "/api/validaciones/ospm/padron"): Scope.PADRON_EDITAR,

    # ── Nomenclador ──────────────────────────────────────────────────────────
    ("GET", "/api/nomenclador/"): Scope.NOMENCLADOR_LEER,
    ("POST", "/api/nomenclador/"): Scope.NOMENCLADOR_EDITAR,
    ("GET", "/api/nomenclador/codigos"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/nomenclador/especialidades"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/nomenclador/{id}"): Scope.NOMENCLADOR_LEER,
    ("PUT", "/api/nomenclador/{id}"): Scope.NOMENCLADOR_EDITAR,
    ("DELETE", "/api/nomenclador/{id}"): Scope.NOMENCLADOR_ELIMINAR,
    ("PATCH", "/api/nomenclador/{id}/activar"): Scope.NOMENCLADOR_EDITAR,
    # Separa un código compartido en una fila propia de una OS: crea catálogo y
    # repunta valores/historial/prestaciones — es una edición estructural.
    ("POST", "/api/nomenclador/{id}/desacoplar/{obra_social_nro}"): Scope.NOMENCLADOR_EDITAR,
    ("GET", "/api/nomenclador/{id}/especialidades"): Scope.NOMENCLADOR_LEER,
    ("POST", "/api/nomenclador/{id}/especialidades"): Scope.NOMENCLADOR_EDITAR,
    ("DELETE", "/api/nomenclador/{id}/especialidades/{esp_id}"): Scope.NOMENCLADOR_ELIMINAR,
    ("PATCH", "/api/nomenclador/{id}/especialidades/{esp_id}/activar"): Scope.NOMENCLADOR_EDITAR,
    ("GET", "/api/nomenclador/{id}/habilitaciones_medico"): Scope.NOMENCLADOR_LEER,
    ("POST", "/api/nomenclador/{id}/habilitaciones_medico"): Scope.NOMENCLADOR_EDITAR,
    ("PUT", "/api/nomenclador/{id}/habilitaciones_medico/{hab_id}"): Scope.NOMENCLADOR_EDITAR,
    ("DELETE", "/api/nomenclador/{id}/habilitaciones_medico/{hab_id}"): Scope.NOMENCLADOR_ELIMINAR,
    ("PATCH", "/api/nomenclador/{id}/habilitaciones_medico/{hab_id}/activar"): Scope.NOMENCLADOR_EDITAR,

    # ── Homologador ──────────────────────────────────────────────────────────
    ("GET", "/api/homologador/"): Scope.NOMENCLADOR_LEER,
    ("POST", "/api/homologador/"): Scope.NOMENCLADOR_EDITAR,
    ("POST", "/api/homologador/validar"): Scope.NOMENCLADOR_LEER,
    ("POST", "/api/homologador/importar_csv"): Scope.NOMENCLADOR_MASIVO,
    ("GET", "/api/homologador/{id}"): Scope.NOMENCLADOR_LEER,
    ("PUT", "/api/homologador/{id}"): Scope.NOMENCLADOR_EDITAR,
    ("DELETE", "/api/homologador/{id}"): Scope.NOMENCLADOR_ELIMINAR,

    # ── Galenos ──────────────────────────────────────────────────────────────
    ("GET", "/api/galenos/"): Scope.NOMENCLADOR_LEER,
    ("POST", "/api/galenos/"): Scope.NOMENCLADOR_EDITAR,
    ("GET", "/api/galenos/plantillas"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/galenos/plantillas/{grupo}"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/galenos/historial/{obra_social_nro}/{codigo}"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/galenos/{id}"): Scope.NOMENCLADOR_LEER,
    ("PUT", "/api/galenos/{id}"): Scope.NOMENCLADOR_EDITAR,
    ("DELETE", "/api/galenos/{id}"): Scope.NOMENCLADOR_ELIMINAR,
    ("POST", "/api/galenos/crear_niveles"): Scope.NOMENCLADOR_EDITAR,
    ("POST", "/api/galenos/{id}/actualizar_precio"): Scope.NOMENCLADOR_EDITAR,
    ("POST", "/api/galenos/{id}/actualizar_unidades"): Scope.NOMENCLADOR_EDITAR,
    ("POST", "/api/galenos/actualizar_precio_masivo"): Scope.NOMENCLADOR_MASIVO,
    ("POST", "/api/galenos/importar_de_obra_social"): Scope.NOMENCLADOR_MASIVO,
    ("POST", "/api/galenos/importar_lote"): Scope.NOMENCLADOR_MASIVO,

    # ── Valores del nomenclador ──────────────────────────────────────────────
    ("GET", "/api/valores_nm/"): Scope.NOMENCLADOR_LEER,
    ("POST", "/api/valores_nm/"): Scope.NOMENCLADOR_EDITAR,
    ("GET", "/api/valores_nm/vigencias"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/valores_nm/historial"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/valores_nm/codigos_por_vigencia"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/valores_nm/por_vigencia"): Scope.NOMENCLADOR_LEER,
    ("POST", "/api/valores_nm/lookup"): Scope.NOMENCLADOR_LEER,
    # Respaldo documental de cada vigencia de valores. Mismos scopes que los
    # valores que respalda: quien puede cargar precios puede adjuntar la nota.
    ("GET", "/api/valores_nm/documentos"): Scope.NOMENCLADOR_LEER,
    # Qué obras sociales actualizaron valores, por mes de vigencia.
    ("GET", "/api/valores_nm/actualizaciones"): Scope.NOMENCLADOR_LEER,
    ("POST", "/api/valores_nm/documentos"): Scope.NOMENCLADOR_EDITAR,
    ("DELETE", "/api/valores_nm/documentos/{doc_id}"): Scope.NOMENCLADOR_EDITAR,
    ("GET", "/api/valores_nm/{id}"): Scope.NOMENCLADOR_LEER,
    ("PUT", "/api/valores_nm/{id}"): Scope.NOMENCLADOR_EDITAR,
    ("DELETE", "/api/valores_nm/{id}"): Scope.NOMENCLADOR_ELIMINAR,
    ("POST", "/api/valores_nm/{id}/actualizar"): Scope.NOMENCLADOR_EDITAR,
    ("GET", "/api/valores_nm/{id}/componentes"): Scope.NOMENCLADOR_LEER,
    # Todo lo que toca muchas filas de una: reescribe el tarifario.
    ("POST", "/api/valores_nm/actualizar_por_codigos"): Scope.NOMENCLADOR_MASIVO,
    ("POST", "/api/valores_nm/actualizar_porcentaje"): Scope.NOMENCLADOR_MASIVO,
    ("POST", "/api/valores_nm/generar_nn_por_rangos"): Scope.NOMENCLADOR_MASIVO,
    ("POST", "/api/valores_nm/importar_csv"): Scope.NOMENCLADOR_MASIVO,
    ("POST", "/api/valores_nm/replicar_a_obras_sociales"): Scope.NOMENCLADOR_MASIVO,
    ("POST", "/api/valores_nm/replicar_estructura"): Scope.NOMENCLADOR_MASIVO,
    ("POST", "/api/valores_nm/revertir_ultima_actualizacion"): Scope.NOMENCLADOR_MASIVO,
    ("DELETE", "/api/valores_nm/por_vigencia"): Scope.NOMENCLADOR_MASIVO,

    # ── Reportes del nomenclador ─────────────────────────────────────────────
    ("GET", "/api/reportes_nm/boletin"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/reportes_nm/evolucion_precios"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/reportes_nm/ranking_valores"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/reportes_nm/tabla_valores"): Scope.NOMENCLADOR_LEER,

    # ── App móvil ────────────────────────────────────────────────────────────
    # Todo lo del app opera sobre el médico del token; la identidad se deriva
    # del JWT, nunca del body (ver A4 en la auditoría). Los scopes acá son los
    # del rol `medico`, y el recorte a "lo propio" lo hace el propio módulo.
    # Perfil, credencial y padrones propios: el handler filtra por `user.ID` /
    # `user.NRO_SOCIO` y no acepta ningún parámetro que elija otro médico, así
    # que exigir MEDICO_LEER (el scope administrativo de ownership.py) los
    # dejaría inaccesibles justamente para su único destinatario.
    ("GET", "/api/mobile/perfil"): SOLO_AUTENTICADO,
    ("GET", "/api/mobile/credencial"): SOLO_AUTENTICADO,
    ("GET", "/api/mobile/padrones"): SOLO_AUTENTICADO,
    ("GET", "/api/mobile/obras-sociales"): Scope.CATALOGO_LEER,
    ("GET", "/api/mobile/nomenclador"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/mobile/tabla-valores"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/mobile/boletin/consulta"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/mobile/boletin/galenos"): Scope.NOMENCLADOR_LEER,
    ("GET", "/api/mobile/avisos"): Scope.CONTENIDO_LEER,
    ("GET", "/api/mobile/beneficios"): Scope.CONTENIDO_LEER,
    # Alta/baja del propio dispositivo y alta de la propia solicitud de cambio:
    # no hay scope que aplique, la restricción es de propiedad.
    ("POST", "/api/mobile/dispositivos"): SOLO_AUTENTICADO,
    ("DELETE", "/api/mobile/dispositivos"): SOLO_AUTENTICADO,
    ("POST", "/api/mobile/solicitudes-cambio"): SOLO_AUTENTICADO,
    # Cierra la propia sesión. Exige access token a propósito: sin eso, un
    # refresh encontrado alcanzaría para desloguear a su dueño.
    ("POST", "/api/mobile/auth/logout"): SOLO_AUTENTICADO,

    # ── Auth ─────────────────────────────────────────────────────────────────
    # Operan siempre sobre el usuario del token; no hay nada que autorizar más
    # allá de tenerlo.
    ("GET", "/auth/me"): SOLO_AUTENTICADO,
    ("POST", "/auth/change-password"): SOLO_AUTENTICADO,
    ("GET", "/auth/legacy/sso-link"): SOLO_AUTENTICADO,
}


def scope_requerido(metodo: str, ruta: str) -> Scope | tuple[Scope, ...] | _Marca | None:
    """Scope de la ruta, o None si no está declarada."""
    return SCOPES_POR_RUTA.get((metodo.upper(), ruta))


def _alcanza(requerido: Scope | tuple[Scope, ...], scopes: list[str]) -> bool:
    """Un scope, todos los de un `TODOS(...)`, o cualquiera de una tupla pelada."""
    # `_Todos` hereda de tuple: el orden de los `isinstance` importa.
    if isinstance(requerido, _Todos):
        return all(s in scopes for s in requerido)
    if isinstance(requerido, tuple):
        return any(s in scopes for s in requerido)
    return requerido in scopes


def _nombre(requerido: Scope | tuple[Scope, ...]) -> str:
    if isinstance(requerido, _Todos):
        return " y ".join(s.value for s in requerido)
    if isinstance(requerido, tuple):
        return " o ".join(s.value for s in requerido)
    return str(requerido)


async def enforce_authz(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    """Dependencia global: autenticación (B1) + autorización (M1) + revocación (A7/A12).

    Reemplaza a `enforce_auth`, que solo hacía la primera mitad. Se registra una
    sola vez en `app/main.py` y cubre las 301 rutas.

    `db` es la **misma sesión** que recibe el handler: FastAPI cachea las
    dependencias por request, así que `Depends(get_db)` se resuelve una sola vez.
    No agrega una conexión; agrega una consulta.
    """
    route = request.scope.get("route")
    ruta = getattr(route, "path", None)

    # Sin ruta resuelta no hay decisión posible por allowlist. Starlette va a
    # responder 404/405 igual; no es una vía de bypass porque no hay handler.
    if ruta is None:
        return None

    metodo = request.method.upper()

    if is_public(metodo, ruta):
        return None

    requerido = scope_requerido(metodo, ruta)

    # Auth de máquina: el endpoint valida su propio secreto y falla cerrado.
    if requerido is MAQUINA:
        return None

    # A partir de acá hace falta token sí o sí.
    user = get_current_user(creds)

    # Corte de sesiones: el token firma bien y no venció, pero el usuario cambió
    # su contraseña o le movieron los permisos después de que se emitiera.
    # Mismo `detail` que un token vencido para que el front ya sepa qué hacer:
    # intenta /auth/refresh, ahí también falla, y cae al login. Ver A7 y A12.
    if await access_revocado(db, user.get("uid"), user.get("iat")):
        raise HTTPException(401, "token_revocado")

    if requerido is SOLO_AUTENTICADO:
        return user

    if requerido is None:
        # Ruta no declarada. Falla cerrado a propósito: agregar un endpoint sin
        # pasar por este archivo no debe quedar accesible por omisión. El test
        # `test_todo_endpoint_declara_autorizacion` lo detecta antes de llegar
        # acá; esto es la red de seguridad en runtime.
        log.error(
            "ruta sin autorización declarada: %s %s — agregala a "
            "SCOPES_POR_RUTA o a PUBLIC_ROUTES",
            metodo, ruta,
        )
        if settings.RBAC_ENFORCE:
            raise HTTPException(403, "Ruta sin autorización declarada")
        return user

    if _alcanza(requerido, user.get("scopes") or []):
        return user

    # Falta el scope.
    if not settings.RBAC_ENFORCE:
        # Modo observación: se registra y se deja pasar. Una o dos semanas de
        # esto dicen qué rol necesita qué, sin romper el front.
        log.warning(
            "RBAC observación: socio=%s le falta '%s' para %s %s",
            user.get("nro_socio"), _nombre(requerido), metodo, ruta,
        )
        return user

    raise HTTPException(403, f"Falta el permiso '{_nombre(requerido)}'")
