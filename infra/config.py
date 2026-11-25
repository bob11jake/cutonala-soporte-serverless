"""Nombres y rutas compartidos por deploy.py, destroy.py, status.py y las pruebas."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
STATE_FILE = ROOT / "infra" / "state.json"

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
ROLE_NAME = os.environ.get("TICKETFLOW_ROLE", "LabRole")  # rol preexistente en AWS Academy
PREFIJO = "ticketflow"

TICKETS_TABLE = f"{PREFIJO}-tickets"
AUDIT_TABLE = f"{PREFIJO}-auditoria"
USERS_TABLE = f"{PREFIJO}-usuarios"
RESTORE_TABLE = f"{PREFIJO}-tickets-restaurada"  # la crea la prueba de resiliencia de PITR
TABLAS = [TICKETS_TABLE, AUDIT_TABLE, USERS_TABLE]

API_LAMBDA = f"{PREFIJO}-api"
SLA_LAMBDA = f"{PREFIJO}-sla-monitor"
SLA_RULE = f"{PREFIJO}-sla-cada-15-min"
TOPIC_NAME = f"{PREFIJO}-alertas"
DASHBOARD = "CUTonala-Soporte"
ALARMAS = [f"{API_LAMBDA}-errores", f"{API_LAMBDA}-throttles", f"{API_LAMBDA}-latencia", f"{SLA_LAMBDA}-errores"]
LOG_RETENCION_DIAS = 14

# Usuarios iniciales del personal. Las contrasenas se guardan con hash PBKDF2
# en DynamoDB; aqui solo existen para poder hacer la demostracion.
USUARIOS_INICIALES = [
    {"email": "admin@ticketflow.edu", "password": "Admin123!", "nombre": "admin", "rol": "ADMIN"},
    {"email": "soporte@ticketflow.edu", "password": "Soporte123!", "nombre": "tecnico", "rol": "TECNICO"},
]


def leer_estado():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def guardar_estado(estado):
    STATE_FILE.write_text(json.dumps(estado, indent=2), encoding="utf-8")


def contexto_ssl():
    """Contexto SSL que funciona tambien con el Python de python.org en macOS."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()
