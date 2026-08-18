"""Obras sociales de carga manual: el prestador ya obtuvo la autorización por
fuera del panel (portal propio de la O.S., teléfono, etc.) y acá sólo registra
el resultado. Boreal y Omint comparten esta implementación — difieren en tres
banderas de convenio, no en el flujo.
"""
from decimal import Decimal

from fastapi import HTTPException

from app.modules.facturacion.service import _validar_autorizacion_medico
from app.modules.validaciones.core.contrato import CERO, Contexto, ResultadoValidacion, ValidadorOS
from app.modules.validaciones.schemas import EntradaManual


class ValidadorManual(ValidadorOS):
    def __init__(
        self,
        nro: int,
        nombre: str,
        *,
        descuenta_coseguro: bool,
        requiere_autorizacion: bool,
        requiere_nombre: bool,
        admite_orden: bool = False,
    ):
        super().__init__(nro=nro, nombre=nombre, entrada=EntradaManual, modalidad="manual")
        # Boreal descuenta el coseguro del total; Omint no cobra coseguro.
        self.descuenta_coseguro = descuenta_coseguro
        self.requiere_autorizacion = requiere_autorizacion
        self.requiere_nombre = requiere_nombre
        # Hoy no se aplica en ningún lado (`adjuntar_orden` no distingue por
        # O.S.) — se conserva para no perder la intención de Boreal. Cablearlo
        # es un cambio de comportamiento aparte, no de este refactor.
        self.admite_orden = admite_orden

    async def validar(self, ctx: Contexto, entrada: EntradaManual) -> ResultadoValidacion:
        if self.requiere_autorizacion and not entrada.nro_validacion.strip():
            raise HTTPException(422, f"{self.nombre} necesita el número de autorización.")
        if self.requiere_nombre and not entrada.nombre_afiliado.strip():
            raise HTTPException(422, "Falta el nombre del afiliado.")
        # Chequeo por CÓDIGO, independiente del de arriba: el de `self` es un
        # todo-o-nada de la obra social, éste marca prácticas puntuales que
        # necesitan autorización previa.
        await _validar_autorizacion_medico(
            ctx.db, entrada.codigo, str(self.nro), entrada.nro_validacion
        )

        precio = await ctx.precio(entrada.codigo)
        return ResultadoValidacion(
            estado="cargada",
            detalle="",
            codigo=entrada.codigo,
            precio=precio,
            nro_afiliado=entrada.nro_afiliado.strip(),
            nombre_afiliado=entrada.nombre_afiliado.strip().upper(),
            nro_autorizacion=entrada.nro_validacion.strip() or None,
            coseguro=Decimal(entrada.coseguro or 0) if self.descuenta_coseguro else CERO,
        )
