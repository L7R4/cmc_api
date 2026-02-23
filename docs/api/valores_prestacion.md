# Valores Prestación — Referencia de API

## Endpoint

```
GET /api/catalogs/valores/boletin
```

Retorna la tabla de aranceles por código de prestación y obra social. Cuando existen múltiples registros para la misma combinación `(nro_obrasocial, codigos)` se devuelve únicamente el primero (menor `id`).

---

## Query params

| Parámetro         | Tipo     | Requerido | Default | Descripción                                   |
|-------------------|----------|-----------|---------|-----------------------------------------------|
| `nro_obra_social` | `int`    | No        | —       | Filtra por número de obra social              |
| `codigo`          | `string` | No        | —       | Filtra por código de prestación               |
| `page`            | `int`    | No        | `1`     | Número de página (≥ 1)                        |
| `size`            | `int`    | No        | `50`    | Registros por página (1–500)                  |

Todos los parámetros son combinables. La tabla tiene ~19 000 registros, por eso **siempre se pagina**.

---

## Response

`200 OK` — Array de objetos `ValorPrestacionOut`.

### Estructura de cada objeto

| Campo          | Tipo              | Nullable | Descripción                                                        |
|----------------|-------------------|----------|--------------------------------------------------------------------|
| `id`           | `number` (int)    | No       | PK interna del registro                                            |
| `codigos`      | `string`          | No       | Código de prestación (máx. 8 caracteres)                           |
| `nro_obrasocial` | `number` (int)  | No       | Número de obra social                                              |
| `obra_social`  | `string \| null`  | Sí       | Nombre de la obra social (puede ser `null` si no tiene registro)   |
| `honorarios_a` | `number` (float)  | No       | Honorarios categoría A                                             |
| `honorarios_b` | `number` (float)  | No       | Honorarios categoría B                                             |
| `honorarios_c` | `number` (float)  | No       | Honorarios categoría C                                             |
| `gastos`       | `number` (float)  | No       | Gastos                                                             |
| `ayudante_a`   | `number` (float)  | No       | Ayudante categoría A                                               |
| `ayudante_b`   | `number` (float)  | No       | Ayudante categoría B                                               |
| `ayudante_c`   | `number` (float)  | No       | Ayudante categoría C                                               |
| `c_p_h_s`      | `string` (1 char) | No       | Indicador de tipo (`C`, `P`, `H` o `S`)                           |
| `fecha_cambio` | `string \| null`  | Sí       | Fecha de última actualización (`YYYY-MM-DD`), puede ser `null`     |

> Los campos decimales (`honorarios_*`, `gastos`, `ayudante_*`) se serializan como `float`.

---

## Ejemplos

### Todos los valores

```
GET /api/catalogs/valores/boletin
```

### Filtrar por obra social

```
GET /api/catalogs/valores/boletin?nro_obra_social=15
```

### Filtrar por código de prestación

```
GET /api/catalogs/valores/boletin?codigo=030101
```

### Filtrar por ambos

```
GET /api/catalogs/valores/boletin?nro_obra_social=15&codigo=030101
```

### Paginación

```
GET /api/catalogs/valores/boletin?page=2&size=100
```

### Response de ejemplo

```json
[
  {
    "id": 42,
    "codigos": "030101",
    "nro_obrasocial": 15,
    "obra_social": "OSDE",
    "honorarios_a": 1250.50,
    "honorarios_b": 1100.00,
    "honorarios_c": 950.75,
    "gastos": 200.00,
    "ayudante_a": 375.15,
    "ayudante_b": 330.00,
    "ayudante_c": 285.23,
    "c_p_h_s": "C",
    "fecha_cambio": "2025-01-15"
  }
]
```

---

## Notas

- La respuesta está ordenada por `nro_obrasocial` ASC, luego `codigos` ASC.
- La tabla contiene ~19 000 registros. La paginación es obligatoria para evitar respuestas muy grandes (`size` máximo: 500).
- Si `obra_social` es `null` significa que el `nro_obrasocial` no tiene entrada en el catálogo de obras sociales.
- Existen ~191 combinaciones con duplicados en la tabla; el endpoint siempre devuelve el registro con menor `id` para cada par `(nro_obrasocial, codigos)`.
- El campo `c_p_h_s` identifica el tipo de prestación según la clasificación interna del sistema.
