from app.db.base import Base, AuditMixin

from app.db.models.legacy import (
    Avisos, Clinicas, CodigoDescripcion, CodigoNomenclador,
    Codigoprestacionswiss, Consulta, EspeCod, EspeCodSwiss,
    GuardarIoscor, GuardarRefacturacion, MedicoObraSocial,
    Nomenclador, NomencladorIoscor, Paciente,
    UnidadNomenclador, UnidadNomenclador10, UnidadNomenclador7,
    UnidadNomencladorInf, UsuarioColegio, ValidarUsuario,
    ValorFijo, ValorNomencladoFijo, ValorNomencladoSwiss,
    ValorNomencladorNacional, ValorPrestacion10,
    ValorPrestacion7, ValorPrestacionInf,
)

from app.db.models.medico import ListadoMedico, Documento
from app.db.models.liquidacion import (
    GuardarAtencion, LiquidacionResumen, Liquidacion,
    DetalleLiquidacion, LiquidacionMedico, Recibo, ReciboItem,
)
from app.db.models.financiero import (
    Debito_Credito, Descuentos, SocioDescuento,
    Deduccion, DeduccionSaldo, DeduccionAplicacion,
)
from app.db.models.catalogs import (
    Especialidad, ObrasSociales, Periodos, ValorPrestacion,
    PeriodosDoctor, ValoresBoletin, ValoresBoletinHistorial, ValoresObrasocial,
)
from app.db.models.rbac import Role, Permission, UserRole, RolePermission, UserPermission
from app.db.models.contenido import Noticia, DocumentoNoticias, PublicidadMedico
from app.db.models.solicitud import SolicitudRegistro

__all__ = [
    "Base", "AuditMixin",
    # legacy
    "Avisos", "Clinicas", "CodigoDescripcion", "CodigoNomenclador",
    "Codigoprestacionswiss", "Consulta", "EspeCod", "EspeCodSwiss",
    "GuardarIoscor", "GuardarRefacturacion", "MedicoObraSocial",
    "Nomenclador", "NomencladorIoscor", "Paciente",
    "UnidadNomenclador", "UnidadNomenclador10", "UnidadNomenclador7",
    "UnidadNomencladorInf", "UsuarioColegio", "ValidarUsuario",
    "ValorFijo", "ValorNomencladoFijo", "ValorNomencladoSwiss",
    "ValorNomencladorNacional", "ValorPrestacion10",
    "ValorPrestacion7", "ValorPrestacionInf",
    # medico
    "ListadoMedico", "Documento",
    # liquidacion
    "GuardarAtencion", "LiquidacionResumen", "Liquidacion",
    "DetalleLiquidacion", "LiquidacionMedico", "Recibo", "ReciboItem",
    # financiero
    "Debito_Credito", "Descuentos", "SocioDescuento",
    "Deduccion", "DeduccionSaldo", "DeduccionAplicacion",
    # catalogs
    "Especialidad", "ObrasSociales", "Periodos", "PeriodosDoctor",
    "ValoresBoletin", "ValoresBoletinHistorial", "ValoresObrasocial", "ValorPrestacion",
    # rbac
    "Role", "Permission", "UserRole", "RolePermission", "UserPermission",
    # contenido
    "Noticia", "DocumentoNoticias", "PublicidadMedico",
    # solicitud
    "SolicitudRegistro",
]
