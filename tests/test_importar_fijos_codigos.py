"""Importar valores fijos: códigos que llegan del Excel con NUL o sin el cero inicial."""
from app.modules.nomenclador.importar_fijos import _con_ceros, normalizar_codigo
from app.modules.nomenclador.schemas import ImportarFijosFilaIn


def test_fila_saca_los_nul_del_excel():
    f = ImportarFijosFilaIn(
        fila=9, codigo="02010501\x00", descripcion="Módulo de cirugía\x00", honorarios="1.234,50\x00",
    )
    assert f.codigo == "02010501"
    assert normalizar_codigo(f.codigo) == "02010501"
    assert f.descripcion == "Módulo de cirugía"
    assert f.honorarios == "1.234,50"


def test_fila_no_toca_numeros_ni_vacios():
    f = ImportarFijosFilaIn(fila=2, codigo=None, honorarios=5445688.82, gastos=None)
    assert f.codigo is None and f.honorarios == 5445688.82 and f.gastos is None


def test_con_ceros_ofrece_seis_y_ocho_digitos():
    assert _con_ceros("1021001") == ["01021001"]        # 7 dígitos: sólo 8 tiene sentido
    assert _con_ceros("20103") == ["020103", "00020103"]
    assert _con_ceros("020103") == ["00020103"]
    assert _con_ceros("01021001") == []
    assert _con_ceros("V-420101") == []
