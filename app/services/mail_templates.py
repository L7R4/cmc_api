# app/services/mail_templates.py
from __future__ import annotations
from html import escape
from datetime import date, datetime
from typing import Optional, Tuple

BRAND = {
    "name": "Colegio Médico",
    "primary": "#1D4ED8",
    "success": "#16A34A",
    "danger": "#EF4444",
    "gray900": "#0F172A",
    "gray700": "#334155",
    "gray500": "#64748B",
    "gray100": "#F1F5F9",
    "bg": "#ffffff",
}

def _esc(s: Optional[str]) -> str:
    return escape(s or "")

def _fmt_date(d: Optional[datetime | date | str]) -> str:
    """Devuelve DD de <mes> de YYYY (es-AR). Acepta str ISO, date o datetime."""
    if d is None or d == "":
        return "-"
    if isinstance(d, str):
        try:
            # intenta parseo ISO
            d = datetime.fromisoformat(d.replace("Z", "+00:00"))
        except Exception:
            return "-"
    if isinstance(d, datetime):
        d = d.date()
    if not isinstance(d, date):
        return "-"
    meses = [
        "enero","febrero","marzo","abril","mayo","junio",
        "julio","agosto","septiembre","octubre","noviembre","diciembre"
    ]
    return f"{d.day:02d} de {meses[d.month-1]} de {d.year}"

def _wrap_base(status_badge: str, preheader: str, body_inner_html: str) -> str:
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Estado de solicitud</title>
<style>@media (max-width:600px){{.container{{padding:20px!important}}.card{{padding:20px!important}}.btn{{display:block!important;width:100%!important;text-align:center!important}}}}</style>
</head>
<body style="margin:0;background:{BRAND['gray100']};font-family:Arial,Helvetica,sans-serif;line-height:1.5;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;height:0;">{_esc(preheader)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td align="center" style="padding:32px 16px;">
<table class="container" role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:620px;background:{BRAND['bg']};padding:28px;border-radius:16px;box-shadow:0 8px 24px rgba(15,23,42,0.08);">
<tr><td style="text-align:center;padding-bottom:8px;">
  <div style="font-size:20px;font-weight:700;color:{BRAND['gray900']};">{_esc(BRAND['name'])}</div>
  <div style="font-size:12px;color:{BRAND['gray500']};margin-top:2px;">Sistema de Solicitudes</div>
</td></tr>
<tr><td class="card" style="padding:24px;border:1px solid #E2E8F0;border-radius:12px;">
  <div style="text-align:center;margin-bottom:16px;">{status_badge}</div>
  {body_inner_html}
</td></tr>
<tr><td style="text-align:center;padding-top:16px;color:{BRAND['gray500']};font-size:12px;">
  Este es un mensaje automático. No respondas a este correo.<br/>© {datetime.utcnow().year} {_esc(BRAND['name'])}. Todos los derechos reservados.
</td></tr>
</table>
</td></tr></table>
</body></html>"""

def build_approval_email(
    *,
    name: Optional[str],
    member_type: Optional[str],
    join_date: Optional[datetime | date | str],
    observations: Optional[str],
) -> Tuple[str, str]:
    safe_name = _esc(name or "solicitante")
    safe_member = _esc(member_type or "-")
    safe_join = _esc(_fmt_date(join_date))
    safe_obs_html = (
        f'<tr><td style="padding:10px 0;border-bottom:1px solid #E2E8F0;"><p style="margin:0;color:{BRAND["gray700"]}"><strong>Observaciones:</strong> {_esc(observations)}</p></td></tr>'
        if observations else ""
    )

    badge = f'<span style="display:inline-block;background:{BRAND["success"]};color:#fff;font-weight:700;font-size:12px;padding:6px 10px;border-radius:999px;letter-spacing:.3px;">APROBADA</span>'
    preheader = "Tu solicitud fue aprobada. Revisá la documentación requerida y próximos pasos."

    body = f"""
  <h1 style="margin:0 0 8px 0;font-size:22px;color:{BRAND['gray900']};text-align:center;">¡Tu solicitud fue aprobada!</h1>
  <p style="margin:0 0 16px 0;color:{BRAND['gray700']};text-align:center;">Hola <strong>{safe_name}</strong>, tu alta fue aprobada. Abajo vas a ver el resumen de tu registro y los próximos pasos.</p>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:12px 0 8px 0;">
    <tr><td style="padding:10px 0;border-bottom:1px solid #E2E8F0;"><strong style="color:{BRAND['gray900']}">Tipo de Socio:</strong><div style="color:{BRAND['gray700']}">{safe_member}</div></td></tr>
    <tr><td style="padding:10px 0;border-bottom:1px solid #E2E8F0;"><strong style="color:{BRAND['gray900']}">Fecha de Ingreso:</strong><div style="color:{BRAND['gray700']}">{safe_join}</div></td></tr>
    {safe_obs_html}
  </table>
  <div style="background:#F8FAFC;border:1px dashed #E2E8F0;border-radius:10px;padding:14px;margin:16px 0;">
    <p style="margin:0 0 8px 0;color:{BRAND['gray700']}">Por favor acercá la siguiente documentación a nuestras oficinas:</p>
    <ul style="margin:0 0 0 18px;color:{BRAND['gray700']};">
      <li>DNI original</li>
      <li>Título de Médico</li>
      <li>Título de Especialista (si corresponde)</li>
    </ul>
    <p style="margin:2px 0 8px 0;color:{BRAND['gray700']}">Y recuerde que deberá abonar el monto de inscripción ($40.000) y la cuota societaria ($8.000) para completar su registro.</p>
    <p style="margin:10px 0 0 0;color:{BRAND['gray500']};font-size:13px;">Dirección: Carlos Pellegrini 1785, Corrientes Capital · Horario: Lunes a Viernes 7–15hs</p>
  </div>
  <div style="text-align:center;margin-top:18px;">
    <a class="btn" href="https://colegiomedicocorrientes.com/" style="display:inline-block;background:{BRAND['primary']};color:#fff;text-decoration:none;font-weight:700;font-size:14px;padding:12px 18px;border-radius:10px;">Ver mi estado en el portal</a>
    <div style="font-size:12px;color:{BRAND['gray500']};margin-top:8px;">Si el botón no funciona, copiá y pegá el siguiente enlace: colegiomedicocorrientes.com</div>
  </div>
    """

    html = _wrap_base(badge, preheader, body)

    text = f"""¡Tu solicitud fue aprobada!

Hola {name or "solicitante"}, tu alta fue aprobada. Resumen:
- Tipo de Socio: {member_type or "-"}
- Fecha de Ingreso: { _fmt_date(join_date) }
{f"- Observaciones: {observations}" if observations else ""}

Próximos pasos: acercá DNI original, Título y (si corresponde) Título de Especialista.
Dirección: Carlos Pellegrini 1785, Corrientes Capital — Horario: Lunes a Viernes 7–15hs

Portal: https://colegiomedicocorrientes.com/

— {BRAND['name']}
"""
    return html, text

def build_rejection_email(
    *,
    name: Optional[str],
    reason: Optional[str],
) -> Tuple[str, str]:
    safe_name = _esc(name or "solicitante")
    safe_reason = _esc(reason or "Sin detalle.")
    badge = f'<span style="display:inline-block;background:{BRAND["danger"]};color:#fff;font-weight:700;font-size:12px;padding:6px 10px;border-radius:999px;letter-spacing:.3px;">RECHAZADA</span>'
    preheader = "Tu solicitud fue rechazada. Te contamos el motivo y los pasos a seguir."

    body = f"""
  <h1 style="margin:0 0 8px 0;font-size:22px;color:{BRAND['gray900']};text-align:center;">Tu solicitud fue rechazada</h1>
  <p style="margin:0 0 16px 0;color:{BRAND['gray700']};text-align:center;">Hola <strong>{safe_name}</strong>, lamentablemente tu solicitud fue rechazada.</p>
  <div style="background:#FEF2F2;border:1px solid #FEE2E2;border-radius:10px;padding:14px;margin:12px 0;">
    <p style="margin:0;color:{BRAND['gray700']}"><strong>Motivo:</strong> {safe_reason}</p>
  </div>
  <p style="margin:16px 0 0 0;color:{BRAND['gray700']}">Podés responder a este correo o comunicarte con Mesa de Ayuda para volver a intentar tu registro.</p>
    """

    html = _wrap_base(badge, preheader, body)

    text = f"""Tu solicitud fue rechazada.

Hola {name or "solicitante"}, lamentablemente tu solicitud fue rechazada.

Motivo: {reason or "Sin detalle."}

Podés responder a este correo o comunicarte con Mesa de Ayuda para volver a intentar tu registro.

— {BRAND['name']}
"""
    return html, text
