import base64
import hashlib
import hmac
import json
import os
import time

SECRET_KEY = os.environ.get("JWT_SECRET", "ticketflow-enterprise-secret-2026").encode("utf-8")

USUARIOS_DEMO = {
    "admin@ticketflow.edu": {"password": "Admin123!", "nombre": "admin", "rol": "ADMIN"},
    "soporte@ticketflow.edu": {"password": "Soporte123!", "nombre": "tecnico", "rol": "TECNICO"},
    "usuario@ticketflow.edu": {"password": "User123!", "nombre": "usuario", "rol": "SOLICITANTE"},
}

def generar_token(email, nombre, rol):
    payload = {
        "email": email,
        "nombre": nombre,
        "rol": rol,
        "exp": int(time.time()) + 28800,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("utf-8")
    firma = hmac.new(SECRET_KEY, b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{b64}.{firma}"

def verificar_token(headers):
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    partes = token.split(".")
    if len(partes) != 2:
        return None
    b64, firma_recibida = partes
    firma_esperada = hmac.new(SECRET_KEY, b64.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(firma_recibida, firma_esperada):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(b64.encode("utf-8")).decode("utf-8"))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None

def autenticar_credenciales(email, password):
    user = USUARIOS_DEMO.get(email.lower().strip())
    if not user or user["password"] != password:
        return None
    token = generar_token(email.lower().strip(), user["nombre"], user["rol"])
    return {"token": token, "usuario": {"email": email, "nombre": user["nombre"], "rol": user["rol"]}}