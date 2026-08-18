"""Control de propiedad (anti-IDOR): sobre qué médico se opera.

Un scope es un permiso **sobre un tipo de recurso, no sobre una fila**.
`facturacion:leer` dice "podés leer facturación"; no dice *cuál*. Con 4.538
filas en `listado_medico` y el rol `medico` llevando ese scope, otorgarlo sin
control de propiedad significa que cada colegiado puede leer la facturación de
los otros 4.537. Por eso M1 (scopes) no subsume a M2 (propiedad): agregar
`require_scope` no cambia nada cuando el scope lo tienen todos.

El patrón nació en `app/modules/validaciones/routes.py` y se generaliza acá.
Tres decisiones que conviene no perder al usarlo:

  1. **El default es el token, no el parámetro.** Si el front no manda nada, se
     opera sobre uno mismo. Quien no sabe que el parámetro existe nunca ve datos
     ajenos.
  2. **Pedir otro médico no está prohibido: está escalonado.** El personal del
     Colegio que carga en nombre de un médico lo necesita, y lo habilita el
     scope administrativo.
  3. **La identidad sale del token firmado**, nunca del body ni del path.

## Por qué son dos funciones y no una

La base usa **dos identificadores distintos** para un médico y no son
intercambiables:

  * `NRO_SOCIO` — la credencial pública, el `sub` del JWT. La usan
    `validaciones`, `facturacion` y `DetalleLiquidacion.medico_id` (legacy).
  * `ListadoMedico.ID` — la PK interna, el claim `uid`. La usan `medicos`,
    `padrones`, `Ajuste`, `Deduccion` y `PagoMedico`.

`app/modules/pagos/service.py` convive con las dos y lo deja anotado: la línea
275 aclara que `medico_db_id` es la PK, la 294 que `DetalleLiquidacion.medico_id`
es el NRO_SOCIO. Un helper único que asumiera una sola convención no fallaría
ruidosamente: compararía dos enteros que no representan lo mismo y devolvería un
200 con datos de otro médico, o un 403 a quien sí es el dueño. El nombre de la
función obliga a decidir cuál corresponde.

Ver `docs/api/RBAC_PROPUESTA.md` §6.
"""
from typing import Optional

from fastapi import HTTPException

from app.auth.scopes import Scope


def _objetivo(
    propio: Optional[int],
    pedido: Optional[int],
    scopes: list[str] | None,
    scope_admin: Scope,
    etiqueta: str,
) -> int:
    if propio is None:
        # Token viejo sin el claim que corresponde. No se puede decidir
        # propiedad, así que se rechaza en vez de dejar pasar: el cliente
        # renueva con /auth/refresh y sigue. Ver la nota de transición en
        # app/auth/deps.py::get_current_user.
        raise HTTPException(401, "token_sin_identidad")

    if pedido is None or int(pedido) == int(propio):
        return int(propio)

    if scope_admin not in (scopes or []):
        raise HTTPException(403, f"No podés operar sobre {etiqueta} de otro médico.")

    return int(pedido)


def socio_objetivo(
    user: dict,
    pedido: Optional[int] = None,
    *,
    scope_admin: Scope = Scope.MEDICO_LEER,
) -> int:
    """Opera por **NRO_SOCIO**.

    Para `validaciones`, `facturacion` y todo lo legacy que indexa por número de
    socio (incluido `DetalleLiquidacion.medico_id`).
    """
    return _objetivo(
        user.get("nro_socio"), pedido, user.get("scopes"), scope_admin, "la matrícula"
    )


def medico_objetivo(
    user: dict,
    pedido: Optional[int] = None,
    *,
    scope_admin: Scope = Scope.MEDICO_LEER,
) -> int:
    """Opera por **ListadoMedico.ID** (la PK interna).

    Para `medicos`, `padrones` y las tablas nuevas de `pagos` (`Ajuste`,
    `Deduccion`, `PagoMedico`).

    `scope_admin` se sube cuando el dato es más sensible que el padrón:

        # leer para trabajar → alcanza el scope de lectura
        objetivo = medico_objetivo(user, medico_id)

        # tocar datos bancarios → permiso nominal
        objetivo = medico_objetivo(user, medico_id,
                                   scope_admin=Scope.MEDICO_EDITAR_BANCARIO)
    """
    return _objetivo(
        user.get("uid"), pedido, user.get("scopes"), scope_admin, "el legajo"
    )


def filtro_socio(
    user: dict,
    pedido: Optional[int] = None,
    *,
    scope_admin: Scope = Scope.MEDICO_LEER,
) -> Optional[int]:
    """Variante para **listados con filtro opcional** por médico.

    `socio_objetivo` no sirve acá: devuelve el socio propio cuando no se pide
    ninguno, y eso en un listado significaría que un administrativo que no filtra
    deja de ver el padrón entero y pasa a verse solo a sí mismo.

    La semántica correcta depende del scope:

      * con `scope_admin` → se respeta lo pedido, y `None` significa **sin
        filtro** (ve todos los médicos);
      * sin `scope_admin`  → se fuerza el propio, y pedir otro es 403.

    Devuelve el NRO_SOCIO por el que filtrar, o `None` para no filtrar.
    """
    if scope_admin in (user.get("scopes") or []):
        return int(pedido) if pedido is not None else None
    return socio_objetivo(user, pedido, scope_admin=scope_admin)


# `puede_ver_sensible()` se eliminó junto con `medico:leer_sensible`
# (2026-08-15). Describía un control que no existía: la función no tenía ni un
# llamador, así que documento, CUIT, CBU, domicilio y teléfono particular ya
# salían en el response para cualquiera con `medico:leer`. Si vuelve a hacer
# falta esconderlos, el lugar es el serializer del módulo de médicos — no un
# helper que nadie invoca. Ver la nota en `Scope.MEDICO_CREAR`.
