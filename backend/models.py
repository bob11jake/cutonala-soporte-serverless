import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

CATEGORIAS_VALIDAS = {"ELECTRICIDAD", "AGUA", "SEGURIDAD", "INFRAESTRUCTURA", "CONECTIVIDAD"}
PRIORIDADES_VALIDAS = {"BAJA", "MEDIA", "ALTA", "CRITICA"}
ESTADOS_VALIDOS = {"ABIERTO", "EN_PROGRESO", "EN_REVISION", "RESUELTO"}

SLA_HORAS = {
    "CRITICA": 2,
    "ALTA": 8,
    "MEDIA": 24,
    "BAJA": 72,
}

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

def calcular_sla_limite(prioridad):
    horas = SLA_HORAS.get(prioridad, 24)
    limite = datetime.now(timezone.utc) + timedelta(hours=horas)
    return limite.isoformat()

def evaluar_estado_sla(ticket):
    if ticket.get("estado") == "RESUELTO":
        return "CUMPLIDO"
    limite_str = ticket.get("sla_limite")
    if not limite_str:
        return "EN_TIEMPO"
    limite = datetime.fromisoformat(limite_str)
    ahora = datetime.now(timezone.utc)
    restante = (limite - ahora).total_seconds()
    if restante < 0:
        return "VENCIDO"
    if restante < 3600 * 2:
        return "POR_VENCER"
    return "EN_TIEMPO"