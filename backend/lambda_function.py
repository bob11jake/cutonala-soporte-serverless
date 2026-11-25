import json
import uuid
from datetime import datetime, timezone
from botocore.exceptions import ClientError

from models import (
    CATEGORIAS_VALIDAS,
    ESTADOS_VALIDOS,
    PRIORIDADES_VALIDAS,
    calcular_sla_limite,
    evaluar_estado_sla,
    respuesta,
)
from auth import autenticar_credenciales, verificar_token
import repository as repo

def lambda_handler(event, context):
    http_ctx = event.get("requestContext", {}).get("http", {})
    metodo = http_ctx.get("method", "GET")
    ruta = event.get("rawPath", "/")
    headers = event.get("headers") or {}
    params = event.get("queryStringParameters") or {}

    try:
        # 1. AUTENTICACION ADMIN / TECNICO
        if ruta == "/auth/login" and metodo == "POST":
            body = json.loads(event.get("body") or "{}")
            auth_res = autenticar_credenciales(body.get("email", ""), body.get("password", ""))
            if not auth_res:
                return respuesta(401, {"error": "Correo o contrasena incorrectos"})
            u = auth_res["usuario"]
            repo.registrar_auditoria(u["nombre"], u["rol"], "LOGIN", f"Inicio de sesion ({u['email']})")
            return respuesta(200, auth_res)

        # 2. BITACORA DE AUDITORIA (SOLO ADMIN / TECNICO)
        if ruta == "/auditoria" and metodo == "GET":
            user = verificar_token(headers)
            if not user or user["rol"] not in {"ADMIN", "TECNICO"}:
                return respuesta(403, {"error": "Acceso restringido a personal tecnico o administrador"})
            logs = repo.obtener_auditoria(30)
            return respuesta(200, {"auditoria": logs})

        # 3. COMENTARIOS EN TICKETS
        if ruta == "/tickets/comentarios" and metodo == "POST":
            user = verificar_token(headers)
            body = json.loads(event.get("body") or "{}")
            ticket_id = str(body.get("ticket_id", "")).strip()
            texto = str(body.get("texto", "")).strip()
            autor = user["nombre"] if user else str(body.get("autor", "")).strip()
            rol = user["rol"] if user else "SOLICITANTE"

            if len(autor) < 2:
                return respuesta(400, {"error": "Debes indicar tu nombre para comentar"})
            if not ticket_id or len(texto) < 2:
                return respuesta(400, {"error": "ticket_id y comentario valido son requeridos"})

            comentario = repo.agregar_comentario_ticket(ticket_id, autor, rol, texto)
            repo.registrar_auditoria(autor, rol, "COMENTARIO", f"Comento en ticket #{ticket_id}")
            return respuesta(201, {"mensaje": "Comentario agregado", "comentario": comentario})

        # 4. LISTAR TICKETS
        if metodo == "GET":
            estado = params.get("estado")
            categoria = params.get("categoria")
            if estado and estado not in ESTADOS_VALIDOS:
                return respuesta(400, {"error": "Estado de filtro invalido"})
            if categoria and categoria not in CATEGORIAS_VALIDAS:
                return respuesta(400, {"error": "Categoria de filtro invalida"})

            items = repo.listar_tickets(estado=estado, categoria=categoria)
            for it in items:
                it["sla_estado"] = evaluar_estado_sla(it)
            items.sort(key=lambda x: x.get("fecha_creacion", ""), reverse=True)
            return respuesta(200, {"tickets": items})

        # 5. CREAR TICKET
        if metodo == "POST":
            user = verificar_token(headers)
            body = json.loads(event.get("body") or "{}")
            solicitante = user["nombre"] if user else str(body.get("solicitante", "")).strip()
            rol_actor = user["rol"] if user else "SOLICITANTE"

            titulo = str(body.get("titulo", "")).strip()
            descripcion = str(body.get("descripcion", "")).strip()
            categoria = str(body.get("categoria", "SOFTWARE")).upper()
            prioridad = str(body.get("prioridad", "MEDIA")).upper()

            if len(solicitante) < 2:
                return respuesta(400, {"error": "El nombre del solicitante es obligatorio"})
            if len(titulo) < 4 or len(descripcion) < 5:
                return respuesta(400, {"error": "Titulo (min. 4 chars) y descripcion (min. 5 chars) obligatorios"})
            if categoria not in CATEGORIAS_VALIDAS:
                return respuesta(400, {"error": f"Categoria invalida. Opciones: {list(CATEGORIAS_VALIDAS)}"})
            if prioridad not in PRIORIDADES_VALIDAS:
                return respuesta(400, {"error": f"Prioridad invalida. Opciones: {list(PRIORIDADES_VALIDAS)}"})

            ahora = datetime.now(timezone.utc).isoformat()
            ticket_id = str(uuid.uuid4())[:8].upper()
            item = {
                "ticket_id": ticket_id,
                "titulo": titulo,
                "descripcion": descripcion,
                "categoria": categoria,
                "prioridad": prioridad,
                "estado": "ABIERTO",
                "solicitante": solicitante,
                "fecha_creacion": ahora,
                "sla_limite": calcular_sla_limite(prioridad),
                "comentarios": [],
                "historial": [{"fecha": ahora, "autor": solicitante, "evento": "Ticket creado"}],
            }
            repo.crear_ticket(item)
            repo.registrar_auditoria(solicitante, rol_actor, "CREAR_TICKET", f"Ticket #{ticket_id} ({prioridad})")
            item["sla_estado"] = "EN_TIEMPO"
            return respuesta(201, {"mensaje": "Ticket registrado", "ticket": item})

        # 6. ACTUALIZAR ESTADO (SOLO TECNICO O ADMIN)
        if metodo == "PUT":
            user = verificar_token(headers)
            if not user or user["rol"] not in {"ADMIN", "TECNICO"}:
                return respuesta(403, {"error": "Solo tecnicos o administradores pueden cambiar estados"})
            body = json.loads(event.get("body") or "{}")
            ticket_id = str(body.get("ticket_id", "")).strip()
            nuevo_estado = str(body.get("estado", "")).upper()
            nota = str(body.get("nota", "")).strip()

            if not ticket_id or nuevo_estado not in ESTADOS_VALIDOS:
                return respuesta(400, {"error": "ticket_id y estado valido son requeridos"})

            repo.actualizar_estado_ticket(ticket_id, nuevo_estado, nota, user["nombre"])
            repo.registrar_auditoria(user["nombre"], user["rol"], "ACTUALIZAR_ESTADO", f"#{ticket_id} -> {nuevo_estado}")
            return respuesta(200, {"mensaje": "Estado actualizado"})

        # 7. ELIMINAR TICKET (SOLO ADMIN)
        if metodo == "DELETE":
            user = verificar_token(headers)
            if not user or user["rol"] != "ADMIN":
                return respuesta(403, {"error": "Solo el rol ADMIN puede eliminar registros"})
            ticket_id = params.get("ticket_id", "").strip()
            if not ticket_id:
                return respuesta(400, {"error": "Falta parametro ticket_id"})
            repo.eliminar_ticket(ticket_id)
            repo.registrar_auditoria(user["nombre"], user["rol"], "ELIMINAR_TICKET", f"Elimino ticket #{ticket_id}")
            return respuesta(200, {"mensaje": "Ticket eliminado"})

        return respuesta(405, {"error": "Metodo no permitido"})

    except json.JSONDecodeError:
        return respuesta(400, {"error": "Cuerpo JSON malformado"})
    except ClientError as e:
        codigo = e.response["Error"]["Code"]
        if codigo == "ConditionalCheckFailedException":
            return respuesta(404, {"error": "El ticket especificado no existe"})
        return respuesta(503, {"error": "Fallo de disponibilidad en DynamoDB", "detalle": codigo})