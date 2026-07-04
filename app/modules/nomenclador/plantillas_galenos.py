"""Plantillas de galeno para el alta ("Nuevo galeno").

Conjunto CANONICO y estatico (independiente de la base): son las estructuras
reutilizables (nombre + slug + niveles con sus unidades-plantilla de referencia)
que ofrece el modal para crear galenos. El precio no se incluye (queda "0.00"):
lo carga el operador al instanciar el galeno para una obra social.

Antes se derivaban de los galenos ya cargados, pero eso fallaba en entornos cuya
base todavia no tiene galenos (lista vacia) — y es circular: se necesitan las
plantillas para poder crear galenos. Por eso viven aca, en codigo.
"""

PLANTILLAS_GALENOS = [
    {
        "grupo": "galeno_cirugia_adultos",
        "codigo": "galeno_cirugia_adultos",
        "nombre": "Galeno Cirugía Adultos",
        "niveles": [
            {
                "nivel": 1,
                "valor_unitario": "0.00",
                "unidades_honorarios": "3.00",
                "unidades_ayudante": "0.75",
                "unidades_gastos": None
            },
            {
                "nivel": 2,
                "valor_unitario": "0.00",
                "unidades_honorarios": "10.00",
                "unidades_ayudante": "2.50",
                "unidades_gastos": None
            },
            {
                "nivel": 3,
                "valor_unitario": "0.00",
                "unidades_honorarios": "20.00",
                "unidades_ayudante": "5.00",
                "unidades_gastos": None
            },
            {
                "nivel": 4,
                "valor_unitario": "0.00",
                "unidades_honorarios": "30.00",
                "unidades_ayudante": "7.50",
                "unidades_gastos": None
            },
            {
                "nivel": 5,
                "valor_unitario": "0.00",
                "unidades_honorarios": "45.00",
                "unidades_ayudante": "11.25",
                "unidades_gastos": None
            },
            {
                "nivel": 6,
                "valor_unitario": "0.00",
                "unidades_honorarios": "65.00",
                "unidades_ayudante": "16.25",
                "unidades_gastos": None
            },
            {
                "nivel": 7,
                "valor_unitario": "0.00",
                "unidades_honorarios": "90.00",
                "unidades_ayudante": "22.50",
                "unidades_gastos": None
            },
            {
                "nivel": 8,
                "valor_unitario": "0.00",
                "unidades_honorarios": "120.00",
                "unidades_ayudante": "30.00",
                "unidades_gastos": None
            },
            {
                "nivel": 9,
                "valor_unitario": "0.00",
                "unidades_honorarios": "150.00",
                "unidades_ayudante": "37.50",
                "unidades_gastos": None
            },
            {
                "nivel": 10,
                "valor_unitario": "0.00",
                "unidades_honorarios": "170.00",
                "unidades_ayudante": "42.50",
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "galeno_cirugia_infantil",
        "codigo": "galeno_cirugia_infantil",
        "nombre": "Galeno Cirugía Infantil",
        "niveles": [
            {
                "nivel": 1,
                "valor_unitario": "0.00",
                "unidades_honorarios": "60.00",
                "unidades_ayudante": None,
                "unidades_gastos": None
            },
            {
                "nivel": 2,
                "valor_unitario": "0.00",
                "unidades_honorarios": "160.00",
                "unidades_ayudante": "40.00",
                "unidades_gastos": None
            },
            {
                "nivel": 3,
                "valor_unitario": "0.00",
                "unidades_honorarios": "250.00",
                "unidades_ayudante": "62.50",
                "unidades_gastos": None
            },
            {
                "nivel": 4,
                "valor_unitario": "0.00",
                "unidades_honorarios": "500.00",
                "unidades_ayudante": "125.00",
                "unidades_gastos": None
            },
            {
                "nivel": 5,
                "valor_unitario": "0.00",
                "unidades_honorarios": "800.00",
                "unidades_ayudante": "200.00",
                "unidades_gastos": None
            },
            {
                "nivel": 6,
                "valor_unitario": "0.00",
                "unidades_honorarios": "1000.00",
                "unidades_ayudante": "250.00",
                "unidades_gastos": None
            },
            {
                "nivel": 7,
                "valor_unitario": "0.00",
                "unidades_honorarios": "1900.00",
                "unidades_ayudante": "400.00",
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "galeno_consulta",
        "codigo": "galeno_consulta",
        "nombre": "Galeno Consulta",
        "niveles": [
            {
                "nivel": None,
                "valor_unitario": "0.00",
                "unidades_honorarios": None,
                "unidades_ayudante": None,
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "galeno_ginecologia",
        "codigo": "galeno_ginecologia",
        "nombre": "Galeno Ginecología",
        "niveles": [
            {
                "nivel": 1,
                "valor_unitario": "0.00",
                "unidades_honorarios": "180.00",
                "unidades_ayudante": None,
                "unidades_gastos": None
            },
            {
                "nivel": 2,
                "valor_unitario": "0.00",
                "unidades_honorarios": "350.00",
                "unidades_ayudante": "87.50",
                "unidades_gastos": None
            },
            {
                "nivel": 3,
                "valor_unitario": "0.00",
                "unidades_honorarios": "600.00",
                "unidades_ayudante": "150.00",
                "unidades_gastos": None
            },
            {
                "nivel": 4,
                "valor_unitario": "0.00",
                "unidades_honorarios": "900.00",
                "unidades_ayudante": "225.00",
                "unidades_gastos": None
            },
            {
                "nivel": 5,
                "valor_unitario": "0.00",
                "unidades_honorarios": "1200.00",
                "unidades_ayudante": "300.00",
                "unidades_gastos": None
            },
            {
                "nivel": 6,
                "valor_unitario": "0.00",
                "unidades_honorarios": "1800.00",
                "unidades_ayudante": "450.00",
                "unidades_gastos": None
            },
            {
                "nivel": 7,
                "valor_unitario": "0.00",
                "unidades_honorarios": "2250.00",
                "unidades_ayudante": "562.50",
                "unidades_gastos": None
            },
            {
                "nivel": 8,
                "valor_unitario": "0.00",
                "unidades_honorarios": "2700.00",
                "unidades_ayudante": "675.00",
                "unidades_gastos": None
            },
            {
                "nivel": 9,
                "valor_unitario": "0.00",
                "unidades_honorarios": "3250.00",
                "unidades_ayudante": "812.50",
                "unidades_gastos": None
            },
            {
                "nivel": 10,
                "valor_unitario": "0.00",
                "unidades_honorarios": "4000.00",
                "unidades_ayudante": "1000.00",
                "unidades_gastos": None
            },
            {
                "nivel": 11,
                "valor_unitario": "0.00",
                "unidades_honorarios": "4500.00",
                "unidades_ayudante": "1125.00",
                "unidades_gastos": None
            },
            {
                "nivel": 12,
                "valor_unitario": "0.00",
                "unidades_honorarios": "6250.00",
                "unidades_ayudante": "1562.50",
                "unidades_gastos": None
            },
            {
                "nivel": 13,
                "valor_unitario": "0.00",
                "unidades_honorarios": "9400.00",
                "unidades_ayudante": "2350.00",
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "galeno_practica",
        "codigo": "galeno_practica",
        "nombre": "Galeno Practica",
        "niveles": [
            {
                "nivel": None,
                "valor_unitario": "0.00",
                "unidades_honorarios": None,
                "unidades_ayudante": None,
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "galeno_quirurgico",
        "codigo": "galeno_quirurgico",
        "nombre": "Galeno Quirurgico",
        "niveles": [
            {
                "nivel": None,
                "valor_unitario": "0.00",
                "unidades_honorarios": None,
                "unidades_ayudante": None,
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "galeno_radiologico",
        "codigo": "galeno_radiologico",
        "nombre": "Galeno Radiologico",
        "niveles": [
            {
                "nivel": None,
                "valor_unitario": "0.00",
                "unidades_honorarios": None,
                "unidades_ayudante": None,
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "galeno_tac",
        "codigo": "galeno_tac",
        "nombre": "Galeno TAC",
        "niveles": [
            {
                "nivel": None,
                "valor_unitario": "0.00",
                "unidades_honorarios": None,
                "unidades_ayudante": None,
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "gasto_tac",
        "codigo": "gasto_tac",
        "nombre": "Gasto TAC",
        "niveles": [
            {
                "nivel": None,
                "valor_unitario": "0.00",
                "unidades_honorarios": None,
                "unidades_ayudante": None,
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "gasto_bioquimico",
        "codigo": "gasto_bioquimico",
        "nombre": "Gastos Bioquimicos",
        "niveles": [
            {
                "nivel": None,
                "valor_unitario": "0.00",
                "unidades_honorarios": None,
                "unidades_ayudante": None,
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "gasto_quirurgico",
        "codigo": "gasto_quirurgico",
        "nombre": "Gastos Quirurgicos",
        "niveles": [
            {
                "nivel": None,
                "valor_unitario": "0.00",
                "unidades_honorarios": None,
                "unidades_ayudante": None,
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "gasto_radiologico",
        "codigo": "gasto_radiologico",
        "nombre": "Gastos Radiologico",
        "niveles": [
            {
                "nivel": None,
                "valor_unitario": "0.00",
                "unidades_honorarios": None,
                "unidades_ayudante": None,
                "unidades_gastos": None
            }
        ]
    },
    {
        "grupo": "gasto_otros",
        "codigo": "gasto_otros",
        "nombre": "Otros Gastos",
        "niveles": [
            {
                "nivel": None,
                "valor_unitario": "0.00",
                "unidades_honorarios": None,
                "unidades_ayudante": None,
                "unidades_gastos": None
            }
        ]
    }
]
