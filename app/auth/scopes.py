"""Catálogo de permisos como código — fuente única de los códigos RBAC.

Antes de este archivo los scopes eran strings literales tipeados en cada
`routes.py`, sin nada que los atara a la tabla `permissions`. Eso produjo tres
defectos reales, documentados en `docs/api/AUDITORIA_SEGURIDAD.md` (F3):

  * `medicos:eliminar` referenciado en el código y **ausente** de la tabla, con
    lo que `DELETE /api/medicos/{id}` era inalcanzable para todos, admin
    incluido — y el síntoma era un 403, indistinguible de un permiso faltante
    legítimo.
  * el typo `solcitudes:ver` en la tabla (sin la "i").
  * 21 de los 30 permisos definidos sin ningún endpoint que los use.

Con el enum, un código mal escrito es un `AttributeError` **al importar**: la
app no arranca. `Scope` hereda de `StrEnum`, así que `Scope.PAGO_LEER ==
"pago:leer"` es `True` y nada aguas abajo (JWT, `require_scope`,
`get_effective_permission_codes`) necesita cambiar.

Convención: `<recurso>:<accion>`, recurso en singular y sin acentos, acción de
un conjunto cerrado — `leer` / `crear` / `editar` / `eliminar`, más acciones
específicas para operaciones irreversibles o con efecto financiero (`cerrar`,
`reabrir`, `emitir`, `anular`, `aplicar`).

**Los permisos no se heredan.** `pago:editar` no implica `pago:leer`. La
herencia se expresa en `ROLES`, no en el motor: es más verboso, pero elimina la
pregunta "¿qué incluye este permiso?" que no se podía responder mirando
`medicos:ver_solo_perfil`.

Ver `docs/api/RBAC_PROPUESTA.md` §2-§5 para el razonamiento completo.
"""
from enum import StrEnum


class Scope(StrEnum):
    # ── Financiero: pagos ─────────────────────────────────────────────────────
    PAGO_LEER = "pago:leer"
    PAGO_CREAR = "pago:crear"
    PAGO_EDITAR = "pago:editar"
    PAGO_ELIMINAR = "pago:eliminar"
    PAGO_CERRAR = "pago:cerrar"
    # Separado de PAGO_CERRAR a propósito: reabrir un pago cerrado es el
    # movimiento que permite alterar cifras ya conciliadas.
    PAGO_REABRIR = "pago:reabrir"

    # ── Financiero: recibos ───────────────────────────────────────────────────
    RECIBO_LEER = "recibo:leer"
    RECIBO_EMITIR = "recibo:emitir"
    RECIBO_ANULAR = "recibo:anular"

    # ── Financiero: liquidaciones ─────────────────────────────────────────────
    LIQUIDACION_LEER = "liquidacion:leer"
    LIQUIDACION_CREAR = "liquidacion:crear"
    LIQUIDACION_ELIMINAR = "liquidacion:eliminar"
    # La contracara de LIQUIDACION_LEER para el socio: su propia liquidación en
    # "Mi perfil". **Todavía no gatea ningún endpoint** — la API no tiene hoy un
    # "mi liquidación"; se da de alta para que el front lo tenga disponible
    # cuando exista, igual que se hizo con PANEL_INGRESAR. Cuando ese endpoint
    # se escriba, va a la matriz como tupla junto a LIQUIDACION_LEER y el
    # handler acota con ownership, no la matriz.
    LIQUIDACION_LEER_PROPIO = "liquidacion:leer_propio"

    # ── Financiero: deducciones y descuentos ──────────────────────────────────
    DEDUCCION_LEER = "deduccion:leer"
    DEDUCCION_CREAR = "deduccion:crear"
    DEDUCCION_EDITAR = "deduccion:editar"
    DEDUCCION_ELIMINAR = "deduccion:eliminar"
    DEDUCCION_APLICAR = "deduccion:aplicar"

    DESCUENTO_LEER = "descuento:leer"
    DESCUENTO_CREAR = "descuento:crear"
    DESCUENTO_EDITAR = "descuento:editar"
    DESCUENTO_ELIMINAR = "descuento:eliminar"

    # Panel de cobranzas: deuda agregada por concepto, solo lectura. Separado
    # de DEDUCCION_LEER a propósito — un perfil de cobranzas no necesita el
    # ABM de deducciones ni tocar la máquina de estados (aplicar, editar,
    # eliminar), solo consultar quién debe, cuánto y de qué.
    COBRANZA_LEER = "cobranza:leer"

    # ── Financiero: lotes de ajuste ───────────────────────────────────────────
    LOTE_LEER = "lote:leer"
    LOTE_CREAR = "lote:crear"
    LOTE_EDITAR = "lote:editar"
    LOTE_ELIMINAR = "lote:eliminar"
    LOTE_REFACTURAR = "lote:refacturar"

    # ── Facturación ───────────────────────────────────────────────────────────
    FACTURACION_LEER = "facturacion:leer"
    FACTURACION_CARGAR = "facturacion:cargar"
    FACTURACION_CERRAR = "facturacion:cerrar"
    # Mover el puntero de período afecta a todos los médicos a la vez.
    FACTURACION_PERIODO = "facturacion:periodo"
    FACTURACION_COMPLEMENTAR = "facturacion:complementar"
    # La contracara de FACTURACION_LEER para el socio. Antes el rol `medico`
    # llevaba `facturacion:leer` a secas y lo que lo acotaba a lo propio era
    # `ownership.filtro_socio()`, o sea el mismo scope significaba dos cosas
    # distintas según quién lo tuviera. Ahora el alcance se lee en el token.
    #
    # **Solo va en rutas con control de propiedad efectivo.** `GET /facturas`,
    # `/prestaciones/{id}` y `/prestaciones/recientes` NO lo llevan: no reciben
    # el usuario, así que no filtran por socio, y dárselo al médico ahí sería
    # abrirle la facturación de todos los colegas.
    FACTURACION_LEER_PROPIO = "facturacion:leer_propio"

    # El permiso base del médico prestador.
    VALIDACION_CARGAR = "validacion:cargar"

    # ── Reportes y estadísticas ───────────────────────────────────────────────
    # Separado de FACTURACION_LEER a propósito: el rol `medico` tiene
    # `facturacion:leer` acotado a lo propio por ownership.py, pero
    # /api/reportes/* cruza la facturación de TODOS los colegiados (quién
    # facturó más, con qué códigos, contra qué obra social) y no hay ningún
    # `medico_id` que ownership pueda acotar. Reusar `facturacion:leer` habría
    # abierto el ranking de colegas a los 4.500 socios.
    REPORTE_LEER = "reporte:leer"

    # ── Nomenclador y tarifario ───────────────────────────────────────────────
    NOMENCLADOR_LEER = "nomenclador:leer"
    NOMENCLADOR_EDITAR = "nomenclador:editar"
    NOMENCLADOR_ELIMINAR = "nomenclador:eliminar"
    # Separado: un error acá reescribe el tarifario completo.
    NOMENCLADOR_MASIVO = "nomenclador:masivo"

    # ── Médicos y padrón ──────────────────────────────────────────────────────
    MEDICO_LEER = "medico:leer"
    # Lo mismo que MEDICO_LEER pero **solo sobre uno mismo**. Es el permiso del
    # rol `medico`: le abre su legajo, sus especialidades, sus adjuntos, su deuda
    # y su padrón, y nada más. Quien impone el "y nada más" es `medico_objetivo()`
    # de app/auth/ownership.py, que sin MEDICO_LEER ignora el `medico_id` pedido
    # y devuelve el del token. Ver A4.
    MEDICO_LEER_PROPIO = "medico:leer_propio"
    # `medico:leer_sensible` se unificó en MEDICO_LEER (2026-08-15). Nunca llegó
    # a hacer nada: su único punto de aplicación era `puede_ver_sensible()` de
    # app/auth/ownership.py, que **no tenía llamadores** — documento, CUIT, CBU,
    # domicilio y teléfono particular ya salían en el response para cualquiera
    # con `medico:leer`. Sostener dos niveles en el catálogo mientras la API
    # devolvía uno solo era peor que tener uno: daba por cubierto un control que
    # no existía. Si en algún momento hace falta esconder esos campos, el lugar
    # es el serializer, y ahí se decide si vuelve a hacer falta un scope aparte.
    MEDICO_CREAR = "medico:crear"
    MEDICO_EDITAR = "medico:editar"
    # Solo el CBU: es el campo contra el que se pagan las liquidaciones.
    MEDICO_EDITAR_BANCARIO = "medico:editar_bancario"
    MEDICO_ELIMINAR = "medico:eliminar"
    MEDICO_DOCUMENTO = "medico:documento"

    PADRON_LEER = "padron:leer"
    PADRON_EDITAR = "padron:editar"

    # ── Catálogos y contenido ─────────────────────────────────────────────────
    CATALOGO_LEER = "catalogo:leer"
    CATALOGO_EDITAR = "catalogo:editar"

    CONTENIDO_LEER = "contenido:leer"
    CONTENIDO_EDITAR = "contenido:editar"

    SOLICITUD_LEER = "solicitud:leer"
    SOLICITUD_RESOLVER = "solicitud:resolver"

    AVISO_GESTIONAR = "aviso:gestionar"
    BENEFICIO_GESTIONAR = "beneficio:gestionar"

    # ── Sistema ───────────────────────────────────────────────────────────────
    RBAC_GESTIONAR = "rbac:gestionar"
    AUDITORIA_LEER = "auditoria:leer"
    AUDITORIA_PURGAR = "auditoria:purgar"
    EXPORT_GENERAR = "export:generar"

    # TEMPORAL — habilita a un médico puntual a quedarse en el panel nuevo
    # después del login en vez de ir al legacy. No gatea ningún endpoint: la
    # API ya autoriza por rol `medico`. Se otorga nominalmente vía
    # `UserPermission.allow`, nunca por rol. Borrar al cerrar la prueba.
    PANEL_INGRESAR = "panel:ingresar"

    # No gatea ningún endpoint de esta API: **lo consume el front legacy**, que
    # con `cmc_has('system_new:access')` decide si muestra el link "INGRESAR
    # NUEVO SISTEMA" (legacy_cmc_php/src/{principal,principal3,
    # calcular_valores_colegio}.php). Estuvo listado como obsoleto por error —
    # ningún `require_scope` lo nombraba, y de ahí la conclusión equivocada de
    # que nadie lo usaba. Borrarlo le saca la puerta de entrada al panel nuevo
    # a los 19 admins de producción. Va por rol (`admin`), a diferencia de
    # PANEL_INGRESAR.
    SYSTEM_NEW_ACCESS = "system_new:access"


# ── Las cuatro operaciones más peligrosas ────────────────────────────────────
# Reescribir el tarifario completo, cambiar el CBU contra el que se paga,
# reabrir un pago ya conciliado y borrar el rastro de auditoría.
#
# **Hasta el 2026-08-16 no se otorgaban por rol**: iban solo por
# `UserPermission.allow`, para que quedara constancia nominal de quién podía
# hacer cada una. Se cambió por decisión del Colegio: el rol tiene que traer sus
# permisos por defecto al asignarlo, y el ajuste fino se hace después sacando o
# agregando permisos a la persona. El mecanismo de ajuste ya existe y no cambió
# — `UserPermission.allow=False` **deniega** aunque el rol lo conceda, que es
# como se le quita el CBU a alguien puntual sin sacarle el rol entero.
#
# El frozenset se conserva porque lo importa la migración `z9a0b1c2d3e4`, y
# porque sigue siendo la lista de lo que conviene mirar dos veces en una
# auditoría. Ya no es una invariante: `test_permisos_criticos_no_van_por_rol` se
# eliminó junto con la regla.
CRITICOS: frozenset[Scope] = frozenset({
    Scope.MEDICO_EDITAR_BANCARIO,
    Scope.NOMENCLADOR_MASIVO,
    Scope.PAGO_REABRIR,
    Scope.AUDITORIA_PURGAR,
})


# ── Composición de roles ─────────────────────────────────────────────────────
# Desde el 2026-08-16 esto es la **línea de base fija** de cada rol, definida por
# el Colegio, y la migración `a6b7c8d9e0f1` hace que `role_permission` sea
# exactamente igual a este dict en las dos bases.
#
# Sigue sin ser la autoridad en runtime: la autorización se resuelve contra MySQL
# en `get_effective_permission_codes()`, y la pantalla de administración puede
# mover `role_permission` en caliente. Por eso tampoco ahora hay un test de
# igualdad — un test así se rompería la primera vez que alguien usa la pantalla,
# que es justamente para lo que está. Lo que sí sigue habiendo es la invariante
# de `RBAC_GESTIONAR`. Ver `docs/api/RBAC_PROPUESTA.md` §5.1 y §5.2.
#
# El ajuste por persona va por `user_permission`, que se resuelve encima del rol:
# `allow=True` concede lo que el rol no da, `allow=False` deniega lo que el rol
# sí da.

_LECTURA_COMUN = {
    Scope.CATALOGO_LEER,
    Scope.CONTENIDO_LEER,
    Scope.NOMENCLADOR_LEER,
}

# `contenido:leer` va en **todos** los roles, por decisión del Colegio: las
# noticias y la publicidad del portal son para cualquiera que entre al sistema,
# no un módulo de un área. Si aparece un rol nuevo, tiene que llevarlo.
# `test_contenido_leer_en_todos_los_roles` lo verifica.

ROLES: dict[str, set[Scope]] = {
    # El rol de base, el más numeroso (~4.500 colegiados). Todo lo que ve está
    # acotado a sus propios datos por el control de propiedad de
    # app/auth/ownership.py, NO por el scope: `facturacion:leer` acá significa
    # "ver la propia facturación", y quien impone eso es socio_objetivo().
    #
    # NO lleva MEDICO_LEER, y eso es deliberado: MEDICO_LEER es el scope
    # administrativo que `ownership.py` usa como llave para operar sobre OTRO
    # médico. Dárselo al rol `medico` anularía el control de propiedad de todos
    # los módulos de una sola vez — cada colegiado podría pasar el `medico_id`
    # de cualquier otro. Lo que sí lleva es MEDICO_LEER_PROPIO, que abre los
    # mismos endpoints acotados a su propia fila.
    "medico": {
        Scope.MEDICO_LEER_PROPIO,
        # Los dos "propio" reemplazan al `facturacion:leer` a secas que llevaba
        # antes: el alcance ahora se lee en el token y no depende de que el
        # handler se acuerde de llamar a ownership.
        Scope.FACTURACION_LEER_PROPIO,
        Scope.LIQUIDACION_LEER_PROPIO,
        # Es el permiso del prestador: le abre /api/validaciones/* completo y la
        # carga del portal (POST /api/facturacion/medico/prestaciones). Sustituye
        # a FACTURACION_CARGAR, que además le habilitaba editar y borrar
        # prestaciones del circuito administrativo.
        Scope.VALIDACION_CARGAR,
        *_LECTURA_COMUN,
    },

    # Carga administrativa. Lleva FACTURACION_PERIODO (mueve el puntero de todos
    # los médicos) y el mantenimiento del nomenclador y los catálogos.
    "facturador": {
        Scope.MEDICO_LEER,
        Scope.MEDICO_EDITAR,
        Scope.MEDICO_EDITAR_BANCARIO,
        Scope.MEDICO_DOCUMENTO,
        Scope.FACTURACION_LEER,
        Scope.FACTURACION_CARGAR,
        Scope.FACTURACION_CERRAR,
        Scope.FACTURACION_PERIODO,
        Scope.FACTURACION_COMPLEMENTAR,
        # Carga validaciones en nombre de un médico (selector de socio en
        # Validaciones). Ya tenía MEDICO_LEER, que es lo que exige
        # `ownership.socio_objetivo` para pedir la matrícula de otro.
        Scope.VALIDACION_CARGAR,
        Scope.NOMENCLADOR_LEER,
        Scope.NOMENCLADOR_EDITAR,
        Scope.NOMENCLADOR_ELIMINAR,
        Scope.NOMENCLADOR_MASIVO,
        Scope.CATALOGO_LEER,
        Scope.CATALOGO_EDITAR,
        Scope.EXPORT_GENERAR,
        # Ve el padrón por obra social, no lo edita: `padron:editar` sigue
        # siendo de `admin`.
        Scope.PADRON_LEER,
        Scope.CONTENIDO_LEER,
    },

    # Arma, sella y revierte la liquidación: lleva PAGO_CERRAR, PAGO_REABRIR y
    # RECIBO_ANULAR. Sin PAGO_ELIMINAR, que queda solo en `admin`.
    "liquidador": {
        Scope.LIQUIDACION_LEER,
        Scope.LIQUIDACION_CREAR,
        Scope.LIQUIDACION_ELIMINAR,
        Scope.LOTE_LEER,
        Scope.LOTE_CREAR,
        Scope.LOTE_EDITAR,
        Scope.LOTE_ELIMINAR,
        Scope.LOTE_REFACTURAR,
        Scope.DEDUCCION_LEER,
        Scope.DEDUCCION_CREAR,
        Scope.DEDUCCION_EDITAR,
        Scope.DEDUCCION_ELIMINAR,
        Scope.DEDUCCION_APLICAR,
        Scope.DESCUENTO_LEER,
        Scope.DESCUENTO_CREAR,
        Scope.DESCUENTO_EDITAR,
        Scope.DESCUENTO_ELIMINAR,
        Scope.COBRANZA_LEER,
        Scope.PAGO_LEER,
        Scope.PAGO_CREAR,
        Scope.PAGO_EDITAR,
        Scope.PAGO_CERRAR,
        Scope.PAGO_REABRIR,
        Scope.RECIBO_LEER,
        Scope.RECIBO_EMITIR,
        Scope.RECIBO_ANULAR,
        Scope.MEDICO_LEER,
        Scope.MEDICO_EDITAR,
        Scope.MEDICO_EDITAR_BANCARIO,
        Scope.MEDICO_DOCUMENTO,
        # No estaba en la lista del Colegio y se agrega igual: sin esto se
        # rompe el botón "Exportar" de Deducciones, porque
        # `GET /api/deducciones/export` pide `deduccion:leer` **y**
        # `export:generar`. Decir "el liquidador gestiona deducciones" y no
        # dejarlo exportarlas no era la intención.
        Scope.EXPORT_GENERAR,
        Scope.PADRON_LEER,
        Scope.CONTENIDO_LEER,
    },

    # El rol `contador` se eliminó (A14). Era el único fuera de `admin` que
    # tenía `rbac:gestionar` en producción: cualquiera con ese rol podía
    # otorgarse —o dar a otro— cualquier permiso del sistema, de forma
    # persistente. Escalada a administrador total con una sola fila de
    # `role_permission`.
    #
    # No se "arregló" quitándole ese permiso porque el rol tampoco tenía un
    # dueño claro: 26 permisos que se solapaban con `liquidador` y con `admin`,
    # y ningún usuario que lo necesitara entero. Quien tenga que hacer control
    # financiero va con `liquidador` más los permisos nominales que le falten
    # vía `UserPermission.allow`, que además deja constancia de quién puede qué.
    # El `DELETE` del rol y de sus asignaciones va en la migración
    # `s3s10n3sr3v0`; el test `test_el_rol_contador_no_existe` impide que
    # vuelva desde la pantalla de administración.

    # Reemplaza el `web:editor` actual, que no lo usa ningún endpoint.
    "editor_web": {
        Scope.CONTENIDO_LEER,
        Scope.CONTENIDO_EDITAR,
        Scope.AVISO_GESTIONAR,
        Scope.BENEFICIO_GESTIONAR,
        Scope.CATALOGO_LEER,
    },

    # "Administrativo: todo". Desde el 2026-08-16 incluye también los cuatro de
    # CRITICOS, que antes quedaban afuera para otorgarse nominalmente.
    #
    # Siguen afuera los dos "propio", que no son permisos administrativos sino
    # la versión acotada de otros que `admin` ya tiene enteros: dárselos no
    # agregaría ningún acceso y sí ensuciaría el token con scopes que en un
    # admin no significan nada.
    #
    # PANEL_INGRESAR también queda afuera: es la bandera temporal de la prueba
    # controlada del panel nuevo, y si `admin` la llevara, los administradores
    # dejarían de ir al legacy tras el login, que es donde hacen su trabajo real.
    "admin": set(Scope) - {
        Scope.PANEL_INGRESAR,
        Scope.MEDICO_LEER_PROPIO,
        Scope.FACTURACION_LEER_PROPIO,
        Scope.LIQUIDACION_LEER_PROPIO,
    },
}


def codigos_de_rol(rol: str) -> list[str]:
    """Códigos del rol, ordenados. Para el seed de la migración."""
    return sorted(s.value for s in ROLES.get(rol, ()))


# Los 28 códigos viejos que quedan huérfanos tras la migración — verificados
# contra `SELECT code FROM permissions` de la base de desarrollo (30 filas, de
# las cuales solo `rbac:gestionar` y `auditoria:purgar` sobreviven con el mismo
# nombre).
#
# La Etapa 3 los borra, una vez que ningún token en circulación los lleve
# (esperar > ACCESS_MINUTES tras el despliegue). Se listan acá para que el
# `DELETE` de la migración de limpieza no se escriba de memoria.
CODIGOS_OBSOLETOS: frozenset[str] = frozenset({
    "auditoria:ver",                # → auditoria:leer
    "avisos:gestionar",             # → aviso:gestionar
    "beneficios:gestionar",         # → beneficio:gestionar
    "contabilidad:asientos",
    "contabilidad:ver",
    "debitos:gestionar",
    "deducciones:editar",           # → deduccion:editar
    "deducciones:generar",          # → deduccion:aplicar
    "facturacion_ioscor:leer",
    "facturas:abrir",
    "facturas:cerrar",              # → facturacion:cerrar
    "facturas:refacturar",          # → lote:refacturar
    "facturas:ver",                 # → facturacion:leer
    "liquidacion:procesar",
    "liquidacion:ver",              # → liquidacion:leer
    "liquidaciones:gestionar",
    "liquidaciones:leer",
    "medicos:agregar",              # → medico:crear
    "medicos:editar_perfil",        # → medico:editar
    "medicos:editar_solo_perfil",
    "medicos:leer",                 # → medico:leer
    "medicos:ver_perfil",
    "medicos:ver_solo_perfil",
    "medico:leer_sensible",         # unificado en medico:leer (2026-08-15)
    "solcitudes:ver",               # typo original, sin la "i"
    "solicitudes:gestionar",        # → solicitud:resolver
    "solicitudes_cambio:gestionar",  # → solicitud:resolver
    "web:editor",
})

# `system_new:access` estuvo en esta lista y **no correspondía**: no lo usa
# ningún endpoint, pero sí el front legacy. Ver `Scope.SYSTEM_NEW_ACCESS`. El
# criterio para dar de baja un código pasa a ser "no lo nombra ni la API ni el
# legacy ni el panel", no sólo "no lo nombra la API".

# Roles que se borran de la base. `contador` es A14: ver la nota en ROLES.
# El test `test_el_rol_contador_no_existe` verifica que no vuelva.
ROLES_OBSOLETOS: frozenset[str] = frozenset({"contador"})

# Descripciones para el seed. Sin esto la tabla queda con `description` en NULL
# y la pantalla de administración muestra códigos pelados.
DESCRIPCIONES: dict[Scope, str] = {
    Scope.PAGO_LEER: "Ver pagos, informes y vista previa",
    Scope.PAGO_CREAR: "Crear pagos",
    Scope.PAGO_EDITAR: "Editar un pago existente",
    Scope.PAGO_ELIMINAR: "Eliminar un pago",
    Scope.PAGO_CERRAR: "Cerrar un pago",
    Scope.PAGO_REABRIR: "Reabrir un pago cerrado (altera cifras conciliadas)",
    Scope.RECIBO_LEER: "Ver recibos",
    Scope.RECIBO_EMITIR: "Emitir y regenerar recibos",
    Scope.RECIBO_ANULAR: "Anular o borrar recibos emitidos",
    Scope.LIQUIDACION_LEER: "Ver liquidaciones por obra social",
    Scope.LIQUIDACION_CREAR: "Crear liquidaciones por obra social",
    Scope.LIQUIDACION_ELIMINAR: "Eliminar liquidaciones por obra social",
    Scope.LIQUIDACION_LEER_PROPIO: "Ver únicamente la liquidación propia",
    Scope.DEDUCCION_LEER: "Ver deducciones, export y top de deudores",
    Scope.DEDUCCION_CREAR: "Crear deducciones",
    Scope.DEDUCCION_EDITAR: "Editar deducciones",
    Scope.DEDUCCION_ELIMINAR: "Eliminar deducciones y deshacer generaciones",
    Scope.DEDUCCION_APLICAR: "Generar en masa, aplicar y refrescar deducciones",
    Scope.DESCUENTO_LEER: "Ver el catálogo de descuentos y sus socios",
    Scope.DESCUENTO_CREAR: "Crear descuentos y asignar socios",
    Scope.DESCUENTO_EDITAR: "Editar descuentos y sus asignaciones",
    Scope.DESCUENTO_ELIMINAR: "Eliminar descuentos y desasignar socios",
    Scope.COBRANZA_LEER: "Ver el panel de cobranzas y la deuda por concepto",
    Scope.LOTE_LEER: "Ver lotes de ajuste",
    Scope.LOTE_CREAR: "Crear lotes de ajuste e ítems",
    Scope.LOTE_EDITAR: "Editar lotes de ajuste y su estado",
    Scope.LOTE_ELIMINAR: "Eliminar lotes de ajuste e ítems",
    Scope.LOTE_REFACTURAR: "Crear lotes de refacturación y sin factura",
    Scope.FACTURACION_LEER: "Ver facturación, prestaciones y facturas",
    Scope.FACTURACION_CARGAR: "Cargar, editar y borrar prestaciones",
    Scope.FACTURACION_CERRAR: "Cerrar el período de facturación",
    Scope.FACTURACION_PERIODO: "Mover el puntero de período (afecta a todos los médicos)",
    Scope.FACTURACION_COMPLEMENTAR: "Emitir facturas y prestaciones complementarias",
    Scope.FACTURACION_LEER_PROPIO: "Ver únicamente la facturación y prestaciones propias",
    Scope.VALIDACION_CARGAR: "Validar y cargar prestaciones contra obras sociales",
    Scope.REPORTE_LEER: "Ver reportes y estadísticas de facturación de todo el Colegio",
    Scope.NOMENCLADOR_LEER: "Ver nomenclador, galenos, valores y homologador",
    Scope.NOMENCLADOR_EDITAR: "Crear y editar en nomenclador, galenos y valores",
    Scope.NOMENCLADOR_ELIMINAR: "Eliminar del nomenclador, galenos y valores",
    Scope.NOMENCLADOR_MASIVO: "Actualización y borrado masivo del tarifario",
    Scope.MEDICO_LEER: "Ver el padrón de médicos sin datos sensibles",
    Scope.MEDICO_LEER_PROPIO: "Ver únicamente el propio legajo, padrón y adjuntos",
    Scope.MEDICO_CREAR: "Alta administrativa de médicos",
    Scope.MEDICO_EDITAR: "Editar datos de un médico",
    Scope.MEDICO_EDITAR_BANCARIO: "Modificar el CBU de un médico",
    Scope.MEDICO_ELIMINAR: "Baja de un médico",
    Scope.MEDICO_DOCUMENTO: "Alta y baja de documentos adjuntos de médicos",
    Scope.PADRON_LEER: "Ver padrones y asignaciones por obra social",
    Scope.PADRON_EDITAR: "Editar padrones, conceptos y especialidades asignadas",
    Scope.CATALOGO_LEER: "Ver obras sociales, especialidades, períodos y valores",
    Scope.CATALOGO_EDITAR: "Editar obras sociales, períodos, valores y observaciones",
    Scope.CONTENIDO_LEER: "Ver noticias y publicidad",
    Scope.CONTENIDO_EDITAR: "Editar noticias y publicidad",
    Scope.SOLICITUD_LEER: "Ver solicitudes de registro y de cambio de datos",
    Scope.SOLICITUD_RESOLVER: "Aprobar o rechazar solicitudes",
    Scope.AVISO_GESTIONAR: "Gestionar avisos push",
    Scope.BENEFICIO_GESTIONAR: "Gestionar beneficios",
    Scope.RBAC_GESTIONAR: "Administrar roles y permisos",
    Scope.AUDITORIA_LEER: "Consultar el registro de auditoría",
    Scope.AUDITORIA_PURGAR: "Purgar el registro de auditoría",
    Scope.EXPORT_GENERAR: "Generar exportaciones a Excel",
    Scope.PANEL_INGRESAR: "Ingreso al panel nuevo (prueba controlada, temporal)",
    Scope.SYSTEM_NEW_ACCESS: "Ingreso al nuevo sistema (link en el menú del legacy)",
}
