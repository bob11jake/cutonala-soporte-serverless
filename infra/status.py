"""Muestra el estado de todos los recursos desplegados y prueba el endpoint /health."""
import json
import sys
import urllib.request
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    ALARMAS, API_LAMBDA, REGION, SLA_LAMBDA, SLA_RULE, TABLAS, TICKETS_TABLE, contexto_ssl, leer_estado,
)

session = boto3.session.Session(region_name=REGION)
ddb = session.client("dynamodb")
lam = session.client("lambda")
s3 = session.client("s3")
cw = session.client("cloudwatch")
events = session.client("events")


def linea(servicio, texto):
    print(f"[{servicio:<10}] {texto}")


print("=== ESTADO DE LA INFRAESTRUCTURA CUTONALA SOPORTE ===")
estado = leer_estado()

for tabla in TABLAS:
    try:
        d = ddb.describe_table(TableName=tabla)["Table"]
        gsis = [g["IndexName"] for g in d.get("GlobalSecondaryIndexes", [])]
        linea("DynamoDB", f"{tabla}: {d['TableStatus']} | ~{d['ItemCount']} items | GSIs: {gsis or '-'}")
    except ClientError:
        linea("DynamoDB", f"{tabla}: NO ENCONTRADA")
try:
    pitr = ddb.describe_continuous_backups(TableName=TICKETS_TABLE)["ContinuousBackupsDescription"]
    linea("DynamoDB", f"PITR en {TICKETS_TABLE}: {pitr['PointInTimeRecoveryDescription']['PointInTimeRecoveryStatus']}")
except ClientError:
    pass

for fn in (API_LAMBDA, SLA_LAMBDA):
    try:
        c = lam.get_function_configuration(FunctionName=fn)
        try:
            conc = lam.get_function_concurrency(FunctionName=fn).get("ReservedConcurrentExecutions")
        except ClientError:
            conc = None
        extra = f" | CONCURRENCIA RESERVADA={conc}" if conc is not None else ""
        linea("Lambda", f"{fn}: {c['State']} | {c['Runtime']} | {c['MemorySize']} MB{extra}")
    except ClientError:
        linea("Lambda", f"{fn}: NO ENCONTRADA")

try:
    r = events.describe_rule(Name=SLA_RULE)
    linea("EventBridge", f"{SLA_RULE}: {r['State']} ({r['ScheduleExpression']})")
except ClientError:
    linea("EventBridge", f"{SLA_RULE}: NO ENCONTRADA")

try:
    alarmas = cw.describe_alarms(AlarmNames=ALARMAS)["MetricAlarms"]
    for a in alarmas:
        linea("CloudWatch", f"alarma {a['AlarmName']}: {a['StateValue']}")
except ClientError:
    pass

for clave in ("bucket", "evidence_bucket"):
    b = estado.get(clave)
    if b:
        try:
            s3.head_bucket(Bucket=b)
            linea("S3", f"{b}: OK")
        except ClientError:
            linea("S3", f"{b}: NO ENCONTRADO")

if estado.get("api_url"):
    try:
        with urllib.request.urlopen(estado["api_url"] + "/health", timeout=10, context=contexto_ssl()) as resp:
            linea("API", f"/health -> HTTP {resp.status} {json.loads(resp.read())}")
    except Exception as e:  # noqa: BLE001
        linea("API", f"/health -> FALLA: {e}")
    linea("Web", estado.get("web_url"))
    linea("API URL", estado.get("api_url"))
else:
    print("\nNo hay infra/state.json: ejecuta python infra/deploy.py")
