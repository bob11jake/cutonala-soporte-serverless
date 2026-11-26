"""Autenticacion: hash de contrasenas (PBKDF2) y tokens firmados con HMAC-SHA256.

Este modulo no depende de boto3 para que infra/deploy.py pueda reutilizar
hash_password() al crear los usuarios iniciales.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

ITERACIONES = 120_000
DURACION_TOKEN_SEG = 8 * 3600

# El secreto lo genera infra/deploy.py y lo inyecta como variable de entorno.
# Si faltara, se usa uno aleatorio por contenedor: los tokens dejan de ser
# validos entre contenedores, pero nunca se firma con un secreto conocido.
SECRET_KEY = (os.environ.get("JWT_SECRET") or secrets.token_hex(32)).encode("utf-8")


def hash_password(password, salt=None, iteraciones=ITERACIONES):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iteraciones)
    return f"pbkdf2_sha256${iteraciones}${salt}${digest.hex()}"


def verificar_password(password, almacenado):
    try:
        algoritmo, iteraciones, salt, _ = almacenado.split("$")
    except (ValueError, AttributeError):
        return False
    if algoritmo != "pbkdf2_sha256":
        return False
    calculado = hash_password(password, salt, int(iteraciones))
    return hmac.compare_digest(calculado, almacenado)


def _b64(data):
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _unb64(texto):
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def generar_token(email, nombre, rol, ahora=None):
    ahora = int(ahora or time.time())
    payload = {"email": email, "nombre": nombre, "rol": rol, "iat": ahora, "exp": ahora + DURACION_TOKEN_SEG}
    cuerpo = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    firma = hmac.new(SECRET_KEY, cuerpo.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{cuerpo}.{firma}"


def verificar_token(headers):
    """Devuelve el payload del token si es valido y no ha expirado; si no, None."""
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        return None
    partes = auth_header.split(" ", 1)[1].strip().split(".")
    if len(partes) != 2:
        return None
    cuerpo, firma_recibida = partes
    firma_esperada = hmac.new(SECRET_KEY, cuerpo.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(firma_recibida, firma_esperada):
        return None
    try:
        payload = json.loads(_unb64(cuerpo).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    return payload
