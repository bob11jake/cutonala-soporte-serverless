import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

CATEGORIAS_VALIDAS = {"ELECTRICIDAD", "AGUA", "SEGURIDAD", "INFRAESTRUCTURA", "CONECTIVIDAD"}
PRIORIDADES_VALIDAS = {"BAJA", "MEDIA", "ALTA", "CRITICA"}
ESTADOS_VALIDOS = {"ABIERTO", "EN_PROGRESO", "EN_REVISION", "RESUELTO"}
ESTADOS_ACTIVOS = ("ABIERTO", "EN_PROGRESO", "EN_REVISION")
ROLES_STAFF = {"ADMIN", "TECNICO"}

CATEGORIA_DEFAULT = "INFRAESTRUCTURA"
PRIORIDAD_DEFAULT = "MEDIA"

# Flujo de trabajo permitido: un ticket solo puede avanzar al siguiente estado
# o regresar uno (p. ej. una revision rechazada vuelve a EN_PROGRESO).
TRANSICIONES = {
    "ABIERTO": {"EN_PROGRESO"},
    "EN_PROGRESO": {"EN_REVISION", "ABIERTO"},
    "EN_REVISION": {"RESUELTO", "EN_PROGRESO"},
    "RESUELTO": {"EN_PROGRESO"},
}

SLA_HORAS = {
    "CRITICA": 2,
    "ALTA": 8,
    "MEDIA": 24,
    "BAJA": 72,
}

LIMITES = {
    "titulo": (4, 120),
    "descripcion": (5, 2000),
    "nombre": (2, 60),
    "comentario": (2, 1000),
    "nota": (0, 500),
}

TIPOS_EVIDENCIA = {"image/jpeg", "image/png", "image/webp"}
MAX_EVIDENCIA_BYTES = 5 * 1024 * 1024
MAX_EVIDENCIAS_POR_TICKET = 5


class ErrorValidacion(Exception):
    """Error de datos de entrada: se responde con HTTP 400."""


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super().default(obj)


def respuesta(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, cls=DecimalEncoder, ensure_ascii=True),
    }


def texto(valor, campo):
    """Normaliza un campo de texto y valida su longitud."""
    minimo, maximo = LIMITES[campo]
    limpio = " ".join(str(valor or "").split())
    if len(limpio) < minimo or len(limpio) > maximo:
        raise ErrorValidacion(f"El campo '{campo}' debe tener entre {minimo} y {maximo} caracteres")
    return limpio


def ahora_iso():
    return datetime.now(timezone.utc).isoformat()


def calcular_sla_limite(prioridad, desde=None):
    horas = SLA_HORAS.get(prioridad, 24)
    base = desde or datetime.now(timezone.utc)
    return (base + timedelta(hours=horas)).isoformat()


def evaluar_estado_sla(ticket, ahora=None):
    if ticket.get("estado") == "RESUELTO":
        return "CUMPLIDO"
    limite_str = ticket.get("sla_limite")
    if not limite_str:
        return "EN_TIEMPO"
    limite = datetime.fromisoformat(limite_str)
    ahora = ahora or datetime.now(timezone.utc)
    restante = (limite - ahora).total_seconds()
    if restante < 0:
        return "VENCIDO"
    # "Por vencer" cuando queda menos del 25% de la ventana del SLA (max. 2 h).
    ventana = SLA_HORAS.get(ticket.get("prioridad"), 24) * 3600
    if restante < min(ventana * 0.25, 7200):
        return "POR_VENCER"
    return "EN_TIEMPO"
