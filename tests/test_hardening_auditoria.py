"""Auditoría de requests (S6) y SQL del control de calidad del padrón (S8).

Sin base: prueban funciones puras.
"""
import pytest

from app.middleware import audit
from app.modules.medicos import auditoria as padron


def test_redact_tapa_datos_personales_por_clave():
    out = audit._redact(
        {
            "dni_afiliado": "30111222",
            "nro_cuit": "20-30111222-5",
            "Telefono": "3794000000",
            "mail_contacto": "a@b.com",
            "cbu": "123",
            "datos": {"domicilio": "San Juan 123", "codigo": "420101"},
            "lista": [{"celular": "1"}],
        }
    )
    assert out["dni_afiliado"] == out["nro_cuit"] == out["Telefono"] == "***"
    assert out["mail_contacto"] == out["cbu"] == "***"
    assert out["datos"] == {"domicilio": "***", "codigo": "420101"}
    assert out["lista"] == [{"celular": "***"}]


def test_redact_tapa_emails_y_cuit_dentro_de_texto_libre():
    out = audit._redact({"obs": "avisar a juan@x.com, cuit 20-12345678-9, período 202610"})
    assert "juan@x.com" not in out["obs"]
    assert "20-12345678-9" not in out["obs"]
    assert "202610" in out["obs"]  # un período no es un CUIT


def test_body_no_json_tambien_se_tapa():
    body = audit._build_request_body(b"mail=a@b.com", "application/json")
    assert "a@b.com" not in body


def test_query_string_tapa_claves_sensibles():
    assert audit._redact_query("dni=1&periodo=202610&email=a@b.com") == "dni=***&periodo=202610&email=***"
    assert audit._redact_query("") is None


def test_rutas_personales_no_guardan_contenido():
    assert "/api/tareas".startswith(audit._SIN_CONTENIDO_PREFIXES)
    assert "/api/solicitudes-cambio/".startswith(audit._SIN_CONTENIDO_PREFIXES)


def test_chequeos_vigentes_son_validos():
    assert len(padron.CHEQUEOS) > 5  # se construyeron todos sin ValueError


@pytest.mark.parametrize(
    "where",
    ["1=1; DROP TABLE listado_medico", "1=1 -- x", "1=1 UNION SELECT 1", "x = '" + chr(92) + "'"],
)
def test_chequeo_rechaza_where_peligroso(where):
    with pytest.raises(ValueError):
        padron.Chequeo(id="x", titulo="t", descripcion="d", severidad="baja", where=where)


def test_chequeo_rechaza_group_by_que_no_es_columna():
    with pytest.raises(ValueError):
        padron.Chequeo(
            id="x", titulo="t", descripcion="d", severidad="baja",
            where="DOCUMENTO > 0", group_by="DOCUMENTO) OR (1=1",
        )
