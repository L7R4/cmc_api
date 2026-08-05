"""Tests de la cadena de toma de control de cuentas (§2.4 de la auditoría).

Eran dos piezas que se combinaban: la contraseña inicial era el DNI, y el login
aceptaba además la matrícula provincial como contraseña de forma permanente. La
matrícula tiene 4-5 dígitos y es dato de registro público; el DNI lo conoce
cualquiera que haya tramitado algo con el médico. Ninguna de las dos caducaba.

Estos tests no verifican que "el arreglo esté aplicado" —eso ya lo dice el
diff—; verifican que **no se pueda reintroducir sin que el build lo diga**. El
fallback de matrícula concretamente era un `bool` con default `True`: volver a
ponerlo es un carácter de diferencia.
"""
import inspect

import pytest

from app.core import passwords
from app.core.passwords import (
    PASSWORD_INICIAL,
    hash_password,
    hash_password_inicial,
    verify_and_upgrade,
    verify_password,
)


def test_no_hay_fallback_de_matricula():
    """A2: `verify_and_upgrade` no puede volver a mirar `MATRICULA_PROV`.

    Se chequea el código fuente y no el comportamiento porque el fallback era un
    parámetro con default: un test de comportamiento que pasara `False`
    explícitamente lo habría dado por bueno igual.
    """
    fuente = inspect.getsource(verify_and_upgrade)
    assert "MATRICULA" not in fuente.upper(), (
        "verify_and_upgrade volvió a aceptar la matrícula como contraseña. "
        "Es información de registro público de 4-5 dígitos: ver §2.4 de "
        "docs/api/AUDITORIA_SEGURIDAD.md"
    )

    firma = inspect.signature(verify_and_upgrade)
    assert "allow_first_time_by_matricula" not in firma.parameters, (
        "Volvió el parámetro del fallback de matrícula."
    )


def test_la_contrasena_inicial_no_es_un_dato_del_medico():
    """A3: no puede volver a derivarse del DNI, del CUIT ni de la matrícula.

    Una constante fija es igual de adivinable, pero solo vale hasta el primer
    login porque viaja con `must_change_password`. Un dato personal, en cambio,
    sigue siendo el mismo dentro de dos años.
    """
    firma = inspect.signature(hash_password_inicial)
    assert not firma.parameters, (
        "hash_password_inicial() recibe argumentos: la contraseña inicial "
        "volvió a depender de algún dato del médico."
    )
    assert isinstance(PASSWORD_INICIAL, str) and PASSWORD_INICIAL


def test_la_contrasena_inicial_verifica():
    """Sanity check del hash: el alta tiene que poder loguearse una vez."""
    assert verify_password(PASSWORD_INICIAL, hash_password_inicial())
    assert not verify_password("otra-cosa", hash_password_inicial())


@pytest.mark.asyncio
async def test_una_password_equivocada_no_entra():
    """Sin fallbacks: si el hash no coincide, no hay segunda oportunidad."""

    class _MedicoFalso:
        ID = 1
        NRO_SOCIO = 999
        MATRICULA_PROV = 4321
        hashed_password = hash_password("la-correcta")

    class _DbFalsa:
        def add(self, _obj): raise AssertionError("no debería escribir nada")
        async def commit(self): raise AssertionError("no debería commitear nada")

    medico = _MedicoFalso()
    db = _DbFalsa()

    assert await verify_and_upgrade(db, medico, "la-correcta")
    # La matrícula, que antes entraba por la puerta de atrás.
    assert not await verify_and_upgrade(db, medico, "4321")
    assert not await verify_and_upgrade(db, medico, "")


def test_el_alta_marca_must_change_password():
    """Las dos altas —pública y administrativa— tienen que marcar el flag.

    Sin el flag, `PASSWORD_INICIAL` deja de ser "una credencial de un solo uso"
    y pasa a ser "la contraseña de todos", que es peor que el DNI.
    """
    from app.modules.medicos import service

    fuente = inspect.getsource(service)
    altas = fuente.count("hash_password_inicial()")
    marcas = fuente.count("must_change_password = True")
    assert altas >= 2, "Faltan altas usando la contraseña inicial"
    assert marcas >= altas, (
        f"{altas} altas con contraseña inicial y solo {marcas} marcan "
        "must_change_password"
    )


def test_passwords_no_expone_helpers_con_fallback():
    """Nada en el módulo debe aceptar credenciales alternativas."""
    sospechosos = [
        nombre for nombre in dir(passwords)
        if "matricula" in nombre.lower() or "fallback" in nombre.lower()
    ]
    assert not sospechosos, sospechosos
