import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DECIMAL, JSON, Boolean, Date, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DetalleFacturacionCMC(Base):
    """Tabla detalle_facturacion (co-propiedad con CMC).

    Lectura para las filas importadas de CMC; lectura **y escritura** para las
    prestaciones que carga el Colegio desde el módulo `facturacion`.
    """
    __tablename__ = "detalle_facturacion"

    id_detalle_prestaciones: Mapped[int] = mapped_column(Integer, primary_key=True)
    periodo: Mapped[str] = mapped_column(String(6), nullable=False)
    cod_med: Mapped[str] = mapped_column(String(20), nullable=False)
    categoria: Mapped[Optional[str]] = mapped_column(String(1))
    nro_orden: Mapped[Optional[str]] = mapped_column(String(30))
    cod_obr: Mapped[Optional[str]] = mapped_column(String(10))
    cod_nom: Mapped[Optional[str]] = mapped_column(String(20))
    tpo_funcion: Mapped[Optional[str]] = mapped_column(String(5))
    sesion: Mapped[Optional[int]] = mapped_column(Integer)
    cantidad: Mapped[Optional[int]] = mapped_column(Integer)
    honorarios: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2))
    gastos: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2))
    ayudante: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2))
    importe_total: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2))
    manual: Mapped[Optional[str]] = mapped_column(String(1))
    # NRO_SOCIO del médico que EJECUTÓ la prestación cuando `cod_med` (el payee) apunta
    # a una clínica (listado_medico.es_organizacion=1). NULL cuando el payee ya es el
    # propio médico. Determina el precio (por la especialidad del ejecutor) pero NO cobra
    # — a quien se le paga es siempre `cod_med`. Sin FK real, igual que `cod_med`.
    cod_med_ejecutor: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    dni_p: Mapped[Optional[str]] = mapped_column(String(15))
    nom_ape_p: Mapped[Optional[str]] = mapped_column(String(100))
    tpo_serv: Mapped[Optional[str]] = mapped_column(String(1))
    cod_clinica: Mapped[Optional[int]] = mapped_column(Integer)
    fecha_practica: Mapped[Optional[datetime.date]] = mapped_column(Date)
    tipo_orden: Mapped[Optional[str]] = mapped_column(String(1))
    porc: Mapped[Optional[int]] = mapped_column(Integer)
    cod_med_indica: Mapped[Optional[str]] = mapped_column(String(20))
    codigo_oms: Mapped[Optional[str]] = mapped_column(String(20))
    diag: Mapped[Optional[str]] = mapped_column(Text)
    nro_vias: Mapped[Optional[int]] = mapped_column(Integer)
    fin_semana: Mapped[Optional[str]] = mapped_column(String(1))
    nocturno: Mapped[Optional[str]] = mapped_column(String(1))
    feriado: Mapped[Optional[str]] = mapped_column(String(2))
    urgencia: Mapped[Optional[str]] = mapped_column(String(1))
    estado: Mapped[Optional[str]] = mapped_column(String(2))
    usuario: Mapped[Optional[str]] = mapped_column(String(30))
    id_especialidad: Mapped[Optional[int]] = mapped_column(Integer)
    # Desglose del cálculo del lookup (modo automático). NULL en CMC y modo manual.
    calculo_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Categorización única (reemplaza el uso de tpo_serv/tipo_orden en la API):
    # Consulta | Practica | Honorarios individuales | Sanatorio. Derivada: Sanatorio si
    # hay clínica, si no la categoria del nomenclador. Las columnas viejas coexisten.
    tipo: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # Vínculo ayudante/gastos → fila del médico (cabeza del equipo). NULL si factura solo.
    grupo_equipo_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Quién cargó la prestación: 'medico' (portal del médico) o 'colegio'. Gatea qué
    # fase de la cabecera controla su edición. Histórico → 'colegio'.
    origen_carga: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="colegio"
    )
    # Checkbox de auditoría del colegio sobre la prestación cargada. No participa
    # del cálculo ni de los gates de edición/cierre — es solo un marcador manual.
    revisado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Número de autorización de la obra social para la prestación. Nullable — no todas
    # las OS/prestaciones lo requieren; se carga/edita como cualquier otro campo simple.
    autorizacion: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    # Versión de la factura a la que pertenece la prestación dentro del par (cod_obr,
    # periodo). 1 = factura original; 2+ = facturas complementarias (prestaciones que
    # llegaron por excepción luego de cerrar/enviar el período — se reenvían aparte a la
    # OS con nota de crédito externa). Histórico → 1.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # ── Validación contra la obra social (módulo `validaciones`) ──────────────
    # Las prestaciones que el médico valida contra el portal/servicio de la O.S.
    # se guardan en esta misma tabla (origen_carga='medico'). Estas columnas son
    # lo que el circuito de facturación no tenía dónde guardar. NULL/0 en toda
    # fila que no venga de una validación (carga del Colegio, importación CMC).
    #
    # Estado que devolvió la O.S.: autorizada · rechazada · pendiente · cargada
    # ('cargada' = O.S. de carga manual, autorizó por fuera del panel). NULL = la
    # fila no pasó por el módulo de validaciones.
    validacion_estado: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)
    # Texto crudo que devolvió la O.S., sin normalizar. Para soporte.
    validacion_detalle: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Traza de la conversación con el validador (mensaje enviado, respuesta cruda,
    # modo). Imprescindible cuando la O.S. discute una autorización. NULL en las
    # obras sociales de carga manual.
    validacion_respuesta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Baja lógica desde el panel del prestador. La fila además pasa a `estado='X'`;
    # este flag distingue "el médico la borró" de "la O.S. la rechazó" (que también
    # queda en 'X' para no entrar nunca a la facturación).
    validacion_anulada: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # Lo que el afiliado paga de su bolsillo. Sólo lo usan las O.S. que lo
    # descuentan del total (Boreal); ya viene descontado de `importe_total`.
    coseguro: Mapped[Decimal] = mapped_column(
        DECIMAL(14, 2), nullable=False, default=0, server_default="0"
    )
    # Orden/receta en PDF que adjunta el prestador (ruta relativa en uploads/).
    orden_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)


class FacturacionCMC(Base):
    """Tabla facturacion importada desde CMC (lotes de facturación cerrados). Solo lectura."""
    __tablename__ = "facturacion"

    id_prestaciones: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_cliente: Mapped[Optional[int]] = mapped_column(Integer)
    tipo_factura: Mapped[Optional[str]] = mapped_column(String(10))
    nro_factura: Mapped[Optional[str]] = mapped_column(String(20))
    tipo_factura_2: Mapped[Optional[str]] = mapped_column(String(10))
    nro_factura_2: Mapped[Optional[str]] = mapped_column(String(20))
    tipo_factura_3: Mapped[Optional[str]] = mapped_column(String(10))
    nro_factura_3: Mapped[Optional[str]] = mapped_column(String(20))
    periodo: Mapped[Optional[str]] = mapped_column(String(6))
    cod_obr: Mapped[Optional[str]] = mapped_column(String(10))
    fecha: Mapped[Optional[datetime.date]] = mapped_column(Date)
    fecha_envio: Mapped[Optional[datetime.date]] = mapped_column(Date)
    fecha_recep: Mapped[Optional[datetime.date]] = mapped_column(Date)
    importe: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2))
    afip: Mapped[Optional[str]] = mapped_column(String(1))
    usuario: Mapped[Optional[str]] = mapped_column(String(30))
    # Fase COLEGIO del período: 'A' abierta / 'C' cerrada (liquidable). Los históricos
    # CMC usaban 'L'/'LC' (aceptados como cerrados por compatibilidad).
    estado: Mapped[Optional[str]] = mapped_column(String(2))
    # Fase MÉDICO del período: 'A' abierta (los médicos aún cargan) / 'C' cerrada.
    # La misma cabecera transita médico → colegio. Histórico → 'C'.
    estado_doctor: Mapped[str] = mapped_column(
        String(1), nullable=False, server_default="C"
    )
    created: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    # Path relativo (servido por /uploads) del comprobante de la factura, subido al
    # cerrar el período. Nullable — no todas las facturas tienen documento adjunto.
    documento_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # Versión de la factura dentro del par (cod_obr, periodo). 1 = original; 2+ =
    # complementarias. A lo sumo una versión está abierta a la vez y es la de mayor
    # número. Ver `abrir_complemento` en el servicio. Histórico → 1.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class PeriodoMedicoActual(Base):
    """Puntero del período abierto para carga de médicos. Reemplaza la tabla legacy
    `periodos_doctor` (deprecada). Es global con override por obra social:
    la fila con `obra_social_id = NULL` es el período global por defecto; las filas
    con `obra_social_id` (NRO_OBRASOCIAL) son overrides puntuales (ej. OS 151)."""
    __tablename__ = "periodo_medico_actual"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    obra_social_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    periodo: Mapped[str] = mapped_column(String(6), nullable=False)  # YYYYMM
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Afiliado(Base):
    """Padrón de afiliados/pacientes. DNI único; el nombre se desnormaliza en
    `detalle_facturacion.nom_ape_p` al cargar una prestación."""
    __tablename__ = "afiliado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dni: Mapped[str] = mapped_column(String(15), nullable=False, unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    usuario: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
