"""Entrada y salida del módulo de datos institucionales.

La regla que atraviesa todo el archivo: **ningún schema de salida tiene un campo
de contraseña**. `EmailOut` expone `tiene_password` (un booleano) y nada más. El
texto plano se devuelve por un schema aparte, `PasswordRevelada`, que sólo usa el
endpoint dedicado. Así, agregar una pantalla que liste casillas no puede filtrar
credenciales por descuido.
"""
import datetime
import re
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# CUIT y CBU se guardan sólo con dígitos: la gente los escribe con guiones,
# puntos y espacios, y compararlos o copiarlos después se vuelve un problema.
_NO_DIGITOS = re.compile(r"\D")


def _solo_digitos(valor: Optional[str]) -> Optional[str]:
    if valor is None:
        return None
    limpio = _NO_DIGITOS.sub("", valor)
    return limpio or None


def _vacio_es_none(valor: Optional[str]) -> Optional[str]:
    """Un campo que el usuario borró llega como `""` y tiene que guardarse NULL.

    Sin esto la base termina con una mezcla de NULL y cadenas vacías que
    significan lo mismo y obligan a chequear las dos en cada consulta.
    """
    if valor is None:
        return None
    limpio = valor.strip()
    return limpio or None


# ── Teléfonos ────────────────────────────────────────────────────────────────

class TelefonoIn(BaseModel):
    etiqueta: Optional[str] = Field(None, max_length=80)
    numero: str = Field(..., min_length=1, max_length=60)
    notas: Optional[str] = Field(None, max_length=200)

    _limpiar = field_validator("etiqueta", "notas")(_vacio_es_none)

    @field_validator("numero")
    @classmethod
    def _numero_no_vacio(cls, v: str) -> str:
        limpio = v.strip()
        if not limpio:
            raise ValueError("El número no puede estar vacío.")
        return limpio


class TelefonoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    etiqueta: Optional[str] = None
    numero: str
    notas: Optional[str] = None


# ── Casillas de correo ───────────────────────────────────────────────────────

class EmailIn(BaseModel):
    """Alta o edición de una casilla. **No lleva la contraseña**.

    La contraseña se carga y se cambia por `PUT /mails/{id}/password`, un
    endpoint aparte. Separarlos evita el caso clásico: editar la etiqueta con un
    formulario que manda el campo de contraseña vacío y borrar el secreto sin
    darse cuenta.
    """

    etiqueta: Optional[str] = Field(None, max_length=80)
    direccion: str = Field(..., min_length=3, max_length=200)
    servidor_entrante: Optional[str] = Field(None, max_length=200)
    servidor_saliente: Optional[str] = Field(None, max_length=200)
    notas: Optional[str] = Field(None, max_length=200)

    _limpiar = field_validator(
        "etiqueta", "servidor_entrante", "servidor_saliente", "notas"
    )(_vacio_es_none)

    @field_validator("direccion")
    @classmethod
    def _parece_un_mail(cls, v: str) -> str:
        limpio = v.strip().lower()
        # Validación mínima a propósito: la del RFC completo rechaza direcciones
        # válidas y acá el dato lo carga personal del Colegio mirando la casilla
        # real, no un formulario público.
        if "@" not in limpio or limpio.startswith("@") or limpio.endswith("@"):
            raise ValueError("La dirección de correo no es válida.")
        return limpio


class EmailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    etiqueta: Optional[str] = None
    direccion: str
    servidor_entrante: Optional[str] = None
    servidor_saliente: Optional[str] = None
    notas: Optional[str] = None
    #: Si hay una contraseña guardada. **Nunca** el valor.
    tiene_password: bool = False
    password_actualizada_en: Optional[datetime.datetime] = None


class PasswordIn(BaseModel):
    """La contraseña a guardar. `None` la borra."""

    password: Optional[str] = Field(None, max_length=200)


class PasswordRevelada(BaseModel):
    """La respuesta del endpoint de lectura. El único lugar con texto plano."""

    id: int
    direccion: str
    password: str


# ── Datos generales ──────────────────────────────────────────────────────────

class InstitucionIn(BaseModel):
    """Todo opcional: la pantalla se guarda a medias mientras se junta el dato."""

    razon_social: Optional[str] = Field(None, max_length=200)
    cuit: Optional[str] = Field(None, max_length=13)
    condicion_iva: Optional[str] = Field(None, max_length=60)
    ingresos_brutos: Optional[str] = Field(None, max_length=40)

    cbu: Optional[str] = Field(None, max_length=30)
    alias_cbu: Optional[str] = Field(None, max_length=60)
    banco: Optional[str] = Field(None, max_length=120)
    titular_cuenta: Optional[str] = Field(None, max_length=200)

    domicilio: Optional[str] = Field(None, max_length=200)
    localidad: Optional[str] = Field(None, max_length=120)
    provincia: Optional[str] = Field(None, max_length=120)
    codigo_postal: Optional[str] = Field(None, max_length=20)

    sitio_web: Optional[str] = Field(None, max_length=200)
    horario_atencion: Optional[str] = Field(None, max_length=200)
    notas: Optional[str] = None

    _limpiar = field_validator(
        "razon_social", "condicion_iva", "ingresos_brutos", "alias_cbu", "banco",
        "titular_cuenta", "domicilio", "localidad", "provincia", "codigo_postal",
        "sitio_web", "horario_atencion", "notas",
    )(_vacio_es_none)

    @field_validator("cuit")
    @classmethod
    def _cuit_valido(cls, v: Optional[str]) -> Optional[str]:
        """11 dígitos, o nada. No se verifica el dígito verificador.

        Verificarlo rechazaría un CUIT mal tipeado, sí, pero también dejaría
        trabada la pantalla si alguien tiene que cargar temporalmente un valor
        que la AFIP todavía no emitió. El largo alcanza para atajar el error
        real, que es pegar un CUIT con la matrícula adentro.
        """
        limpio = _solo_digitos(v)
        if limpio is None:
            return None
        if len(limpio) != 11:
            raise ValueError("El CUIT debe tener 11 dígitos.")
        return limpio

    @field_validator("cbu")
    @classmethod
    def _cbu_valido(cls, v: Optional[str]) -> Optional[str]:
        """22 dígitos, o nada.

        Acá el largo **sí** es una validación fuerte y no una formalidad: es la
        cuenta contra la que cobra el Colegio, y un CBU de 21 o 23 dígitos es
        siempre un error de carga.
        """
        limpio = _solo_digitos(v)
        if limpio is None:
            return None
        if len(limpio) != 22:
            raise ValueError("El CBU debe tener 22 dígitos.")
        return limpio


class InstitucionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    razon_social: Optional[str] = None
    cuit: Optional[str] = None
    condicion_iva: Optional[str] = None
    ingresos_brutos: Optional[str] = None

    cbu: Optional[str] = None
    alias_cbu: Optional[str] = None
    banco: Optional[str] = None
    titular_cuenta: Optional[str] = None

    domicilio: Optional[str] = None
    localidad: Optional[str] = None
    provincia: Optional[str] = None
    codigo_postal: Optional[str] = None

    sitio_web: Optional[str] = None
    horario_atencion: Optional[str] = None
    notas: Optional[str] = None

    actualizado_en: Optional[datetime.datetime] = None
    actualizado_por: Optional[int] = None

    telefonos: List[TelefonoOut] = []
    emails: List[EmailOut] = []

    #: Si el servidor puede guardar y leer contraseñas (hay `SECRETOS_KEY`).
    #: La pantalla lo usa para no ofrecer el campo cuando la función está
    #: apagada, en vez de dejar que el usuario escriba y reciba un 503.
    secretos_disponibles: bool = True

    #: Si **este** usuario está en la lista nominal que puede ver las
    #: contraseñas (`INSTITUCION_CLAVES_SOCIOS`). Es para que la pantalla no
    #: muestre un botón que la API va a rechazar; el control real está en el
    #: handler.
    puede_ver_claves: bool = False
