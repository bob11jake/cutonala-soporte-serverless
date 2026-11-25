"""Acceso a datos en DynamoDB.

Patrones usados:
- Indices secundarios (EstadoIndex, CategoriaIndex) para consultar sin Scan.
- Item de contadores (#STATS) actualizado en la misma transaccion que cada
  ticket, para que los KPIs no requieran recorrer toda la tabla.
- Escrituras condicionales para evitar carreras (dos tecnicos moviendo el
  mismo ticket a la vez) y operaciones sobre tickets inexistentes.
"""
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError

from models import ESTADOS_ACTIVOS

log = logging.getLogger()

# Reintentos con backoff exponencial y control adaptativo ante throttling.
_cfg = Config(retries={"max_attempts": 5, "mode": "adaptive"}, connect_timeout=3, read_timeout=5)
dynamodb = boto3.resource("dynamodb", config=_cfg)

TICKETS_TABLE = os.environ.get("TICKETS_TABLE", "ticketflow-tickets")
AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "ticketflow-auditoria")
USERS_TABLE = os.environ.get("USERS_TABLE", "ticketflow-usuarios")

tickets_tbl = dynamodb.Table(TICKETS_TABLE)
audit_tbl = dynamodb.Table(AUDIT_TABLE)
users_tbl = dynamodb.Table(USERS_TABLE)

STATS_ID = "#STATS"
AUDIT_TTL_DIAS = 365
MAX_INTENTOS_LOGIN = 5
BLOQUEO_SEG = 15 * 60


class NoEncontrado(Exception):
    pass


class Conflicto(Exception):
    pass


def _cliente():
    return tickets_tbl.meta.client


# --------------------------------------------------------------------------- salud

def verificar_salud():
    """Lectura minima (1 RCU) para comprobar que DynamoDB responde."""
    tickets_tbl.get_item(Key={"ticket_id": STATS_ID}, ProjectionExpression="ticket_id")
    return True


# ---------------------------------------------------------------------- auditoria

def registrar_auditoria(actor, rol, accion, detalle):
    """Best effort: si la bitacora falla no debe tumbar la operacion principal."""
    ahora = datetime.now(timezone.utc)
    try:
        audit_tbl.put_item(
            Item={
                "audit_id": uuid.uuid4().hex[:12],
                "tipo": "EVENTO",
                "timestamp": ahora.isoformat(),
                "actor": actor,
                "rol": rol,
                "accion": accion,
                "detalle": detalle,
                "expira": int(ahora.timestamp()) + AUDIT_TTL_DIAS * 86400,
            }
        )
    except ClientError as e:
        log.warning("auditoria_no_registrada %s %s", accion, e.response["Error"]["Code"])


def obtener_auditoria(limite=30):
    res = audit_tbl.query(
        IndexName="TimelineIndex",
        KeyConditionExpression=Key("tipo").eq("EVENTO"),
        ScanIndexForward=False,
        Limit=limite,
    )
    return res.get("Items", [])


# ------------------------------------------------------------------------ tickets

def _query_paginado(limite, **kwargs):
    items, inicio = [], None
    while len(items) < limite:
        if inicio:
            kwargs["ExclusiveStartKey"] = inicio
        res = tickets_tbl.query(Limit=limite - len(items), **kwargs)
        items.extend(res.get("Items", []))
        inicio = res.get("LastEvaluatedKey")
        if not inicio:
            break
    return items


def _por_estado(estado, limite):
    return _query_paginado(
        limite,
        IndexName="EstadoIndex",
        KeyConditionExpression=Key("estado").eq(estado),
        ScanIndexForward=False,
    )


def listar_tickets(estado=None, categoria=None, limite=100):
    """Nunca hace Scan: el costo de cada consulta queda acotado por 'limite'."""
    if estado:
        return _por_estado(estado, limite)
    if categoria:
        return _query_paginado(
            limite,
            IndexName="CategoriaIndex",
            KeyConditionExpression=Key("categoria").eq(categoria),
            ScanIndexForward=False,
        )
    items = []
    for est in ESTADOS_ACTIVOS:
        items.extend(_por_estado(est, limite))
    items.extend(_por_estado("RESUELTO", 50))  # solo los resueltos mas recientes
    return items


def obtener_ticket(ticket_id):
    item = tickets_tbl.get_item(Key={"ticket_id": ticket_id}).get("Item")
    if not item or ticket_id == STATS_ID:
        raise NoEncontrado(ticket_id)
    return item


def _update_stats(deltas):
    nombres, valores, partes = {}, {}, []
    for i, (campo, delta) in enumerate(deltas.items()):
        nombres[f"#k{i}"] = campo
        valores[f":v{i}"] = delta
        partes.append(f"#k{i} :v{i}")
    return {
        "Update": {
            "TableName": TICKETS_TABLE,
            "Key": {"ticket_id": STATS_ID},
            "UpdateExpression": "ADD " + ", ".join(partes),
            "ExpressionAttributeNames": nombres,
            "ExpressionAttributeValues": valores,
        }
    }


def _transaccion(operaciones):
    try:
        _cliente().transact_write_items(TransactItems=operaciones)
    except ClientError as e:
        if e.response["Error"]["Code"] == "TransactionCanceledException":
            raise Conflicto("El ticket fue modificado por otra persona; recarga e intenta de nuevo")
        raise


def crear_ticket(item):
    _transaccion([
        {
            "Put": {
                "TableName": TICKETS_TABLE,
                "Item": item,
                "ConditionExpression": "attribute_not_exists(ticket_id)",
            }
        },
        _update_stats({
            "total": 1,
            f"e_{item['estado']}": 1,
            f"c_{item['categoria']}": 1,
            f"p_{item['prioridad']}": 1,
        }),
    ])
    return item


def cambiar_estado(ticket_id, nuevo_estado, nota, autor):
    actual = obtener_ticket(ticket_id)
    anterior = actual["estado"]
    ahora = datetime.now(timezone.utc).isoformat()
    evento = f"Estado {anterior} -> {nuevo_estado}. {nota}".strip()
    expr = "SET estado = :e, historial = list_append(historial, :h), actualizado = :a"
    valores = {":e": nuevo_estado, ":h": [{"fecha": ahora, "autor": autor, "evento": evento}], ":a": ahora, ":old": anterior}
    if nuevo_estado == "RESUELTO":
        expr += ", fecha_resolucion = :a"
    _transaccion([
        {
            "Update": {
                "TableName": TICKETS_TABLE,
                "Key": {"ticket_id": ticket_id},
                "UpdateExpression": expr,
                "ConditionExpression": "estado = :old",
                "ExpressionAttributeValues": valores,
            }
        },
        _update_stats({f"e_{anterior}": -1, f"e_{nuevo_estado}": 1}),
    ])
    return anterior


def agregar_comentario_ticket(ticket_id, autor, rol, texto):
    comentario = {"fecha": datetime.now(timezone.utc).isoformat(), "autor": autor, "rol": rol, "texto": texto}
    try:
        tickets_tbl.update_item(
            Key={"ticket_id": ticket_id},
            UpdateExpression="SET comentarios = list_append(if_not_exists(comentarios, :empty), :c)",
            ExpressionAttributeValues={":empty": [], ":c": [comentario]},
            ConditionExpression="attribute_exists(estado)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise NoEncontrado(ticket_id)
        raise
    return comentario


def agregar_evidencia(ticket_id, evidencia, maximo):
    try:
        tickets_tbl.update_item(
            Key={"ticket_id": ticket_id},
            UpdateExpression="SET evidencias = list_append(if_not_exists(evidencias, :empty), :ev)",
            ConditionExpression="attribute_exists(estado) AND (attribute_not_exists(evidencias) OR size(evidencias) < :max)",
            ExpressionAttributeValues={":empty": [], ":ev": [evidencia], ":max": maximo},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise Conflicto("El ticket no existe o ya alcanzo el maximo de evidencias")
        raise


def eliminar_ticket(ticket_id):
    actual = obtener_ticket(ticket_id)
    _transaccion([
        {
            "Delete": {
                "TableName": TICKETS_TABLE,
                "Key": {"ticket_id": ticket_id},
                "ConditionExpression": "estado = :old",
                "ExpressionAttributeValues": {":old": actual["estado"]},
            }
        },
        _update_stats({
            "total": -1,
            f"e_{actual['estado']}": -1,
            f"c_{actual['categoria']}": -1,
            f"p_{actual['prioridad']}": -1,
        }),
    ])
    return actual


def marcar_sla_notificado(ticket_id):
    """Devuelve True solo la primera vez (evita notificaciones duplicadas)."""
    try:
        tickets_tbl.update_item(
            Key={"ticket_id": ticket_id},
            UpdateExpression="SET sla_notificado = :t",
            ConditionExpression="attribute_exists(estado) AND attribute_not_exists(sla_notificado)",
            ExpressionAttributeValues={":t": datetime.now(timezone.utc).isoformat()},
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


# ---------------------------------------------------------------------- contadores

def obtener_contadores():
    item = tickets_tbl.get_item(Key={"ticket_id": STATS_ID}).get("Item") or {}
    item.pop("ticket_id", None)
    return {k: max(int(v), 0) for k, v in item.items()}


def recalcular_contadores():
    """Reconstruye #STATS recorriendo la tabla una sola vez (uso administrativo)."""
    stats, inicio = {"total": 0}, None
    while True:
        kwargs = {"ExclusiveStartKey": inicio} if inicio else {}
        res = tickets_tbl.scan(ProjectionExpression="ticket_id, estado, categoria, prioridad", **kwargs)
        for it in res.get("Items", []):
            if it["ticket_id"] == STATS_ID:
                continue
            stats["total"] += 1
            for clave in (f"e_{it['estado']}", f"c_{it['categoria']}", f"p_{it['prioridad']}"):
                stats[clave] = stats.get(clave, 0) + 1
        inicio = res.get("LastEvaluatedKey")
        if not inicio:
            break
    tickets_tbl.put_item(Item={"ticket_id": STATS_ID, **stats})
    return stats


# ------------------------------------------------------------------------ usuarios

def obtener_usuario(email):
    return users_tbl.get_item(Key={"email": email}).get("Item")


def usuario_bloqueado(usuario):
    return int(usuario.get("bloqueado_hasta", 0)) > int(time.time())


def registrar_login_fallido(email):
    """Tras MAX_INTENTOS_LOGIN fallos seguidos la cuenta se bloquea 15 minutos."""
    res = users_tbl.update_item(
        Key={"email": email},
        UpdateExpression="ADD intentos_fallidos :uno",
        ConditionExpression="attribute_exists(email)",
        ExpressionAttributeValues={":uno": 1},
        ReturnValues="UPDATED_NEW",
    )
    if int(res["Attributes"]["intentos_fallidos"]) >= MAX_INTENTOS_LOGIN:
        users_tbl.update_item(
            Key={"email": email},
            UpdateExpression="SET bloqueado_hasta = :b, intentos_fallidos = :cero",
            ExpressionAttributeValues={":b": int(time.time()) + BLOQUEO_SEG, ":cero": 0},
        )


def reiniciar_intentos(email):
    users_tbl.update_item(
        Key={"email": email},
        UpdateExpression="SET intentos_fallidos = :cero, ultimo_login = :a REMOVE bloqueado_hasta",
        ExpressionAttributeValues={":cero": 0, ":a": datetime.now(timezone.utc).isoformat()},
    )
