import os
import uuid
from datetime import datetime, timezone
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
TICKETS_TABLE = os.environ.get("TICKETS_TABLE", "ticketflow-tickets")
AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "ticketflow-auditoria")

tickets_tbl = dynamodb.Table(TICKETS_TABLE)
audit_tbl = dynamodb.Table(AUDIT_TABLE)

def registrar_auditoria(actor, rol, accion, detalle):
    ahora = datetime.now(timezone.utc).isoformat()
    audit_tbl.put_item(
        Item={
            "audit_id": str(uuid.uuid4())[:10],
            "timestamp": ahora,
            "actor": actor,
            "rol": rol,
            "accion": accion,
            "detalle": detalle,
        }
    )

def listar_tickets(estado=None, categoria=None):
    if estado:
        res = tickets_tbl.query(
            IndexName="EstadoIndex",
            KeyConditionExpression=Key("estado").eq(estado),
            ScanIndexForward=False,
        )
    elif categoria:
        res = tickets_tbl.query(
            IndexName="CategoriaIndex",
            KeyConditionExpression=Key("categoria").eq(categoria),
            ScanIndexForward=False,
        )
    else:
        res = tickets_tbl.scan()
    return res.get("Items", [])

def crear_ticket(item):
    tickets_tbl.put_item(Item=item)
    return item

def actualizar_estado_ticket(ticket_id, nuevo_estado, nota, autor):
    ahora = datetime.now(timezone.utc).isoformat()
    tickets_tbl.update_item(
        Key={"ticket_id": ticket_id},
        UpdateExpression="SET estado = :e, historial = list_append(historial, :h)",
        ExpressionAttributeValues={
            ":e": nuevo_estado,
            ":h": [{"fecha": ahora, "autor": autor, "evento": f"Estado -> {nuevo_estado}. {nota}".strip()}],
        },
        ConditionExpression="attribute_exists(ticket_id)",
    )

def agregar_comentario_ticket(ticket_id, autor, rol, texto):
    ahora = datetime.now(timezone.utc).isoformat()
    comentario = {"fecha": ahora, "autor": autor, "rol": rol, "texto": texto}
    tickets_tbl.update_item(
        Key={"ticket_id": ticket_id},
        UpdateExpression="SET comentarios = list_append(if_not_exists(comentarios, :empty), :c)",
        ExpressionAttributeValues={
            ":empty": [],
            ":c": [comentario],
        },
        ConditionExpression="attribute_exists(ticket_id)",
    )
    return comentario

def eliminar_ticket(ticket_id):
    tickets_tbl.delete_item(
        Key={"ticket_id": ticket_id},
        ConditionExpression="attribute_exists(ticket_id)",
    )

def obtener_auditoria(limite=25):
    res = audit_tbl.scan(Limit=limite)
    items = res.get("Items", [])
    items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return items[:limite]