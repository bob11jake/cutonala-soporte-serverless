"""API REST de CUTonala Soporte (AWS Lambda + Function URL).

Rutas:
  GET    /health                        estado del servicio y de DynamoDB
  POST   /auth/login                    login de personal (ADMIN / TECNICO)
  GET    /tickets  (o /)                lista de tickets (?estado= | ?categoria=)
  POST   /tickets  (o /)                crear ticket (anonimo con nombre o con token)
  PUT    /tickets  (o /)                cambiar estado (ADMIN / TECNICO)
  DELETE /tickets  (o /)?ticket_id=     eliminar (ADMIN)
  GET    /metricas                      contadores globales (ADMIN / TECNICO)
  GET    /auditoria                     bitacora (ADMIN / TECNICO)
  POST   /tickets/comentarios           agregar comentario
  POST   /tickets/evidencia             pedir URL prefirmada para subir una foto
  POST   /tickets/evidencia/confirmar   registrar la foto ya subida en el ticket
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError

from models import (
    CATEGORIA_DEFAULT,
    CATEGORIAS_VALIDAS,
    ESTADOS_VALIDOS,
    MAX_EVIDENCIAS_POR_TICKET,
    PRIORIDAD_DEFAULT,
    PRIORIDADES_VALIDAS,
    ROLES_STAFF,
    TIPOS_EVIDENCIA,
    TRANSICIONES,
    ErrorValidacion,
    calcular_sla_limite,
    evaluar_estado_sla,
    respuesta,
    texto,
)
import auth
import notifier
import repository as repo
import storage

log = logging.getLogger()
log.setLevel(logging.INFO)

RUTAS_TICKETS = {"", "/", "/tickets"}


class NoAutorizado(Exception):
    pass


def _body(event):
    crudo = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        crudo = base64.b64decode(crudo).decode("utf-8")
    data = json.loads(crudo)
    if not isinstance(data, dict):
        raise ErrorValidacion("El cuerpo debe ser un objeto JSON")
    return data


def _requiere_staff(user, solo_admin=False):
    roles = {"ADMIN"} if solo_admin else ROLES_STAFF
    if not user or user.get("rol") not in roles:
        raise NoAutorizado("Solo el rol ADMIN puede realizar esta accion" if solo_admin
                           else "Acceso restringido a personal tecnico o administrador")


def _ticket_id(valor):
    tid = str(valor or "").strip().upper()
    if not (4 <= len(tid) <= 12) or not tid.isalnum():
        raise ErrorValidacion("ticket_id invalido")
    return tid


def _actor(user, body, campo):
    if user:
        return user["nombre"], user["rol"]
    return texto(body.get(campo), "nombre"), "SOLICITANTE"


def _presentar(t):
    t["sla_estado"] = evaluar_estado_sla(t)
    if storage.habilitado():
        for ev in t.get("evidencias", []):
            ev["url"] = storage.url_lectura(ev["key"])
    return t


# --------------------------------------------------------------------- handlers

def login(event, user):
    body = _body(event)
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    usuario = repo.obtener_usuario(email) if email else None
    if usuario and repo.usuario_bloqueado(usuario):
        return respuesta(429, {"error": "Cuenta bloqueada temporalmente por intentos fallidos. Intenta en 15 minutos"})
    if not usuario or not auth.verificar_password(password, usuario.get("password_hash")):
        if usuario:
            repo.registrar_login_fallido(email)
            repo.registrar_auditoria(email, usuario["rol"], "LOGIN_FALLIDO", "Contrasena incorrecta")
        return respuesta(401, {"error": "Correo o contrasena incorrectos"})
    repo.reiniciar_intentos(email)
    token = auth.generar_token(email, usuario["nombre"], usuario["rol"])
    repo.registrar_auditoria(usuario["nombre"], usuario["rol"], "LOGIN", f"Inicio de sesion ({email})")
    return respuesta(200, {"token": token, "usuario": {"email": email, "nombre": usuario["nombre"], "rol": usuario["rol"]}})


def salud(event, user):
    inicio = time.time()
    repo.verificar_salud()
    return respuesta(200, {
        "status": "ok",
        "dynamodb": "ok",
        "latencia_ms": round((time.time() - inicio) * 1000),
        "evidencias": storage.habilitado(),
        "alertas": bool(notifier.TOPIC_ARN),
    })


def listar(event, user):
    params = event.get("queryStringParameters") or {}
    estado = (params.get("estado") or "").upper() or None
    categoria = (params.get("categoria") or "").upper() or None
    if estado and estado not in ESTADOS_VALIDOS:
        raise ErrorValidacion("Estado de filtro invalido")
    if categoria and categoria not in CATEGORIAS_VALIDAS:
        raise ErrorValidacion("Categoria de filtro invalida")
    items = [_presentar(t) for t in repo.listar_tickets(estado=estado, categoria=categoria)]
    items.sort(key=lambda x: x.get("fecha_creacion", ""), reverse=True)
    return respuesta(200, {"tickets": items})


def crear(event, user):
    body = _body(event)
    solicitante, rol = _actor(user, body, "solicitante")
    titulo = texto(body.get("titulo"), "titulo")
    descripcion = texto(body.get("descripcion"), "descripcion")
    categoria = str(body.get("categoria") or CATEGORIA_DEFAULT).upper()
    prioridad = str(body.get("prioridad") or PRIORIDAD_DEFAULT).upper()
    if categoria not in CATEGORIAS_VALIDAS:
        raise ErrorValidacion(f"Categoria invalida. Opciones: {sorted(CATEGORIAS_VALIDAS)}")
    if prioridad not in PRIORIDADES_VALIDAS:
        raise ErrorValidacion(f"Prioridad invalida. Opciones: {sorted(PRIORIDADES_VALIDAS)}")

    ahora = datetime.now(timezone.utc)
    item = {
        "ticket_id": uuid.uuid4().hex[:8].upper(),
        "titulo": titulo,
        "descripcion": descripcion,
        "categoria": categoria,
        "prioridad": prioridad,
        "estado": "ABIERTO",
        "solicitante": solicitante,
        "fecha_creacion": ahora.isoformat(),
        "sla_limite": calcular_sla_limite(prioridad, ahora),
        "comentarios": [],
        "evidencias": [],
        "historial": [{"fecha": ahora.isoformat(), "autor": solicitante, "evento": "Ticket creado"}],
    }
    repo.crear_ticket(item)
    repo.registrar_auditoria(solicitante, rol, "CREAR_TICKET", f"Ticket #{item['ticket_id']} ({prioridad})")
    if prioridad == "CRITICA":
        item["alerta_enviada"] = notifier.alerta_ticket_critico(item)
    return respuesta(201, {"mensaje": "Ticket registrado", "ticket": _presentar(item)})


def cambiar_estado(event, user):
    _requiere_staff(user)
    body = _body(event)
    ticket_id = _ticket_id(body.get("ticket_id"))
    nuevo = str(body.get("estado", "")).upper()
    nota = texto(body.get("nota", ""), "nota")
    if nuevo not in ESTADOS_VALIDOS:
        raise ErrorValidacion("Estado invalido")
    actual = repo.obtener_ticket(ticket_id)["estado"]
    if nuevo not in TRANSICIONES[actual]:
        return respuesta(409, {"error": f"Transicion no permitida: {actual} -> {nuevo}"})
    repo.cambiar_estado(ticket_id, nuevo, nota, user["nombre"])
    repo.registrar_auditoria(user["nombre"], user["rol"], "ACTUALIZAR_ESTADO", f"#{ticket_id} {actual} -> {nuevo}")
    return respuesta(200, {"mensaje": "Estado actualizado", "anterior": actual, "estado": nuevo})


def eliminar(event, user):
    _requiere_staff(user, solo_admin=True)
    params = event.get("queryStringParameters") or {}
    ticket_id = _ticket_id(params.get("ticket_id"))
    ticket = repo.eliminar_ticket(ticket_id)
    storage.eliminar_de_ticket(ticket)
    repo.registrar_auditoria(user["nombre"], user["rol"], "ELIMINAR_TICKET", f"Elimino ticket #{ticket_id}")
    return respuesta(200, {"mensaje": "Ticket eliminado"})


def comentar(event, user):
    body = _body(event)
    autor, rol = _actor(user, body, "autor")
    ticket_id = _ticket_id(body.get("ticket_id"))
    comentario = repo.agregar_comentario_ticket(ticket_id, autor, rol, texto(body.get("texto"), "comentario"))
    repo.registrar_auditoria(autor, rol, "COMENTARIO", f"Comento en ticket #{ticket_id}")
    return respuesta(201, {"mensaje": "Comentario agregado", "comentario": comentario})


def pedir_subida_evidencia(event, user):
    if not storage.habilitado():
        return respuesta(503, {"error": "El almacenamiento de evidencias no esta configurado"})
    body = _body(event)
    ticket_id = _ticket_id(body.get("ticket_id"))
    content_type = str(body.get("content_type", "")).lower()
    if content_type not in TIPOS_EVIDENCIA:
        raise ErrorValidacion("Solo se permiten imagenes JPG, PNG o WEBP")
    ticket = repo.obtener_ticket(ticket_id)
    if len(ticket.get("evidencias", [])) >= MAX_EVIDENCIAS_POR_TICKET:
        return respuesta(409, {"error": f"Maximo {MAX_EVIDENCIAS_POR_TICKET} evidencias por ticket"})
    return respuesta(200, storage.generar_subida(ticket_id, content_type))


def confirmar_evidencia(event, user):
    if not storage.habilitado():
        return respuesta(503, {"error": "El almacenamiento de evidencias no esta configurado"})
    body = _body(event)
    ticket_id = _ticket_id(body.get("ticket_id"))
    key = str(body.get("key", ""))
    if not key.startswith(f"evidencias/{ticket_id}/"):
        raise ErrorValidacion("La evidencia no corresponde a este ticket")
    tamano = storage.existe(key)
    if not tamano:
        return respuesta(404, {"error": "La imagen no se encontro en S3; vuelve a subirla"})
    autor, rol = _actor(user, body, "autor")
    repo.agregar_evidencia(ticket_id, {"key": key, "autor": autor, "bytes": tamano,
                                       "fecha": datetime.now(timezone.utc).isoformat()}, MAX_EVIDENCIAS_POR_TICKET)
    repo.registrar_auditoria(autor, rol, "EVIDENCIA", f"Adjunto imagen a ticket #{ticket_id}")
    return respuesta(201, {"mensaje": "Evidencia registrada", "url": storage.url_lectura(key)})


def metricas(event, user):
    _requiere_staff(user)
    return respuesta(200, {"contadores": repo.obtener_contadores()})


def auditoria(event, user):
    _requiere_staff(user)
    return respuesta(200, {"auditoria": repo.obtener_auditoria(30)})


RUTAS = {
    ("GET", "/health"): salud,
    ("POST", "/auth/login"): login,
    ("GET", "/auditoria"): auditoria,
    ("GET", "/metricas"): metricas,
    ("POST", "/tickets/comentarios"): comentar,
    ("POST", "/tickets/evidencia"): pedir_subida_evidencia,
    ("POST", "/tickets/evidencia/confirmar"): confirmar_evidencia,
}
for _ruta in RUTAS_TICKETS:
    RUTAS[("GET", _ruta)] = listar
    RUTAS[("POST", _ruta)] = crear
    RUTAS[("PUT", _ruta)] = cambiar_estado
    RUTAS[("DELETE", _ruta)] = eliminar


def lambda_handler(event, context):
    inicio = time.time()
    metodo = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    ruta = (event.get("rawPath") or "/").rstrip("/") or "/"
    headers = event.get("headers") or {}

    try:
        handler = RUTAS.get((metodo, ruta))
        if not handler:
            res = respuesta(404 if not any(r == ruta for _, r in RUTAS) else 405, {"error": "Ruta o metodo no soportado"})
        else:
            user = auth.verificar_token(headers)
            tiene_token = bool(headers.get("authorization") or headers.get("Authorization"))
            if tiene_token and not user and handler is not login:
                res = respuesta(401, {"error": "Sesion expirada o token invalido"})
            else:
                res = handler(event, user)
    except json.JSONDecodeError:
        res = respuesta(400, {"error": "Cuerpo JSON malformado"})
    except ErrorValidacion as e:
        res = respuesta(400, {"error": str(e)})
    except NoAutorizado as e:
        res = respuesta(403, {"error": str(e)})
    except repo.NoEncontrado:
        res = respuesta(404, {"error": "El ticket especificado no existe"})
    except repo.Conflicto as e:
        res = respuesta(409, {"error": str(e)})
    except ClientError as e:
        codigo = e.response["Error"]["Code"]
        log.error("dynamodb_error %s %s %s", metodo, ruta, codigo)
        res = respuesta(503, {"error": "Servicio de datos no disponible temporalmente", "detalle": codigo})
    except BotoCoreError as e:
        log.error("aws_error %s %s %s", metodo, ruta, type(e).__name__)
        res = respuesta(503, {"error": "Servicio de datos no disponible temporalmente", "detalle": type(e).__name__})
    except Exception:
        log.exception("error_no_controlado %s %s", metodo, ruta)
        res = respuesta(500, {"error": "Error interno", "request_id": getattr(context, "aws_request_id", None)})

    # Log estructurado: consultable con CloudWatch Logs Insights.
    print(json.dumps({
        "tipo": "request", "metodo": metodo, "ruta": ruta, "status": res["statusCode"],
        "duracion_ms": round((time.time() - inicio) * 1000),
    }))
    return res
