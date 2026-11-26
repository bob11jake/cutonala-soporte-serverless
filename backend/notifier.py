"""Notificaciones por Amazon SNS.

Degradacion elegante: si SNS falla, la operacion principal (crear el ticket)
se completa igual y solo se registra el problema en CloudWatch Logs.
"""
import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger()

TOPIC_ARN = os.environ.get("ALERTS_TOPIC_ARN", "")
_sns = boto3.client("sns", config=Config(retries={"max_attempts": 2}, connect_timeout=2, read_timeout=3))


def publicar(asunto, mensaje):
    if not TOPIC_ARN:
        return False
    try:
        _sns.publish(TopicArn=TOPIC_ARN, Subject=asunto[:100], Message=mensaje)
        return True
    except (ClientError, BotoCoreError) as e:
        log.warning("notificacion_fallida %s", e)
        return False


def alerta_ticket_critico(ticket):
    return publicar(
        f"[CUTonala] Ticket CRITICO #{ticket['ticket_id']}",
        (
            f"Se registro un ticket de prioridad CRITICA.\n\n"
            f"ID: {ticket['ticket_id']}\n"
            f"Titulo: {ticket['titulo']}\n"
            f"Categoria: {ticket['categoria']}\n"
            f"Reportado por: {ticket['solicitante']}\n"
            f"Limite SLA: {ticket['sla_limite']}\n\n"
            f"{ticket['descripcion']}"
        ),
    )


def alerta_sla_vencidos(tickets):
    lineas = [f"- #{t['ticket_id']} [{t['prioridad']}] {t['titulo']} (limite {t['sla_limite']})" for t in tickets]
    return publicar(
        f"[CUTonala] {len(tickets)} ticket(s) con SLA vencido",
        "Los siguientes tickets superaron su tiempo de atencion:\n\n" + "\n".join(lineas),
    )
