"""Anular en el listado de carga: el médico (cabecera) se lleva a todo su equipo con
`con_equipo=true`; un ayudante o el pediatra se anulan solos. Y editar una cabecera de carga
vieja (con `grupo_equipo_id` NULL pero con integrantes apuntándole) trae igual su equipo.

Corre contra la base de desarrollo con datos 100% sintéticos (mismo patrón que
`test_facturacion_pediatra.py`, de donde se toman las fixtures)."""
from app.core.security import create_access_token
from tests.test_facturacion_pediatra import (  # noqa: F401  (fixtures)
    COD_CESAREA, COD_OBRA_TEST, COD_PEDIATRA, NRO_AYUDANTE, NRO_CIRUJANO, NRO_PEDIATRA,
    _conn, _item, _post, cliente, headers, medicos_sinteticos,
)


def _estado(ids):
    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute(
                "SELECT id_detalle_prestaciones, estado FROM detalle_facturacion "
                "WHERE id_detalle_prestaciones IN (" + ",".join(["%s"] * len(ids)) + ")",
                ids,
            )
            return dict(cur.fetchall())
    finally:
        con.close()


def _equipo(cliente, headers):
    r = _post(cliente, headers, [
        _item(NRO_CIRUJANO, COD_CESAREA, honorarios=100),
        _item(NRO_AYUDANTE, COD_CESAREA, ayudante=10),
    ])
    assert r.status_code == 201, r.text
    cabeza_id, ayudante_id = r.json()["ids"]
    r2 = _post(cliente, headers, [
        _item(NRO_PEDIATRA, COD_PEDIATRA, honorarios=50, rol="pediatra", grupo_equipo_id=cabeza_id),
    ])
    assert r2.status_code == 201, r2.text
    return cabeza_id, ayudante_id, r2.json()["ids"][0]


def test_anular_al_medico_con_equipo_anula_todo_el_grupo(cliente, headers, medicos_sinteticos):
    cabeza, ayudante, pediatra = _equipo(cliente, headers)
    r = cliente.delete(f"/api/facturacion/prestaciones/{cabeza}?con_equipo=true&motivo=duplicado", headers=headers)
    assert r.status_code == 204, r.text
    assert _estado([cabeza, ayudante, pediatra]) == {cabeza: "X", ayudante: "X", pediatra: "X"}


def test_anular_al_ayudante_deja_el_resto_del_equipo(cliente, headers, medicos_sinteticos):
    cabeza, ayudante, pediatra = _equipo(cliente, headers)
    r = cliente.delete(f"/api/facturacion/prestaciones/{ayudante}?con_equipo=false&motivo=duplicado", headers=headers)
    assert r.status_code == 204, r.text
    assert _estado([cabeza, ayudante, pediatra]) == {cabeza: "A", ayudante: "X", pediatra: "A"}


def test_anular_al_pediatra_con_la_bandera_tampoco_toca_al_resto(cliente, headers, medicos_sinteticos):
    cabeza, ayudante, pediatra = _equipo(cliente, headers)
    r = cliente.delete(f"/api/facturacion/prestaciones/{pediatra}?con_equipo=true&motivo=duplicado", headers=headers)
    assert r.status_code == 204, r.text
    assert _estado([cabeza, ayudante, pediatra]) == {cabeza: "A", ayudante: "A", pediatra: "X"}


def test_sin_la_bandera_anular_a_la_cabecera_no_arrastra_al_equipo(cliente, headers, medicos_sinteticos):
    cabeza, ayudante, pediatra = _equipo(cliente, headers)
    r = cliente.delete(f"/api/facturacion/prestaciones/{cabeza}?motivo=duplicado", headers=headers)
    assert r.status_code == 204, r.text
    assert _estado([cabeza, ayudante, pediatra]) == {cabeza: "X", ayudante: "A", pediatra: "A"}


def test_cabecera_sin_grupo_cargado_trae_igual_a_su_equipo_y_lo_anula(cliente, headers, medicos_sinteticos):
    cabeza, ayudante, pediatra = _equipo(cliente, headers)
    # Carga vieja: los integrantes apuntan a la cabecera, que quedó con el grupo en NULL.
    con = _conn()
    try:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE detalle_facturacion SET grupo_equipo_id = NULL WHERE id_detalle_prestaciones = %s",
                (cabeza,),
            )
    finally:
        con.close()

    r = cliente.get(f"/api/facturacion/prestaciones/{cabeza}?motivo=duplicado", headers=headers)
    assert sorted(m["id"] for m in r.json()["grupo"]) == sorted([ayudante, pediatra])

    # Y desde un integrante, la cabecera también viene en su grupo.
    r = cliente.get(f"/api/facturacion/prestaciones/{ayudante}", headers=headers)
    assert cabeza in [m["id"] for m in r.json()["grupo"]]

    # El listado por grupo incluye a la cabecera.
    token = create_access_token(
        sub="1", uid=1, scopes=["facturacion:leer", "medico:leer"], role="admin",
    )
    r = cliente.get(
        f"/api/facturacion/prestaciones?grupo_equipo_id={cabeza}&limit=20",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sorted(m["id"] for m in r.json()) == sorted([cabeza, ayudante, pediatra])

    r = cliente.delete(f"/api/facturacion/prestaciones/{cabeza}?con_equipo=true&motivo=duplicado", headers=headers)
    assert r.status_code == 204, r.text
    assert _estado([cabeza, ayudante, pediatra]) == {cabeza: "X", ayudante: "X", pediatra: "X"}
