"""Lambda programada (EventBridge, cada 15 min) que vigila los SLA.

Busca tickets activos cuyo limite ya paso, los marca como notificados (una
sola vez por ticket gracias a una escritura condicional) y envia un resumen
por SNS. Publica la metrica TicketsSLAVencidos en CloudWatch con el formato
Embedded Metric Format (una linea JSON en el log, sin llamadas extra a la API).
"""
import json
import time

from models import ESTADOS_ACTIVOS, evaluar_estado_sla
import notifier
import repository as repo


def lambda_handler(event, context):
    vencidos, nuevos = [], []
    for estado in ESTADOS_ACTIVOS:
        for t in repo.listar_tickets(estado=estado, limite=500):
            if evaluar_estado_sla(t) == "VENCIDO":
                vencidos.append(t)
                if repo.marcar_sla_notificado(t["ticket_id"]):
                    nuevos.append(t)

    notificado = notifier.alerta_sla_vencidos(nuevos) if nuevos else False
    for t in nuevos:
        repo.registrar_auditoria("sla-monitor", "SISTEMA", "SLA_VENCIDO", f"Ticket #{t['ticket_id']} supero su SLA")

    print(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "CUTonalaSoporte",
                "Dimensions": [[]],
                "Metrics": [{"Name": "TicketsSLAVencidos", "Unit": "Count"}],
            }],
        },
        "TicketsSLAVencidos": len(vencidos),
        "nuevos_notificados": len(nuevos),
    }))
    return {"vencidos": len(vencidos), "nuevos_notificados": len(nuevos), "sns": notificado}
