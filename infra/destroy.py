"""Elimina todos los recursos de CUTonala Soporte para no consumir creditos.

Uso:  python infra/destroy.py          (pide confirmacion)
      python infra/destroy.py --si     (sin confirmacion)
"""
import argparse
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    ALARMAS, API_LAMBDA, DASHBOARD, REGION, RESTORE_TABLE, SLA_LAMBDA, SLA_RULE, STATE_FILE, TABLAS,
    TOPIC_NAME, leer_estado,
)

session = boto3.session.Session(region_name=REGION)


def intentar(descripcion, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        print(f"   -> {descripcion}")
    except ClientError as e:
        codigo = e.response["Error"]["Code"]
        if codigo in ("ResourceNotFoundException", "NoSuchBucket", "NotFound", "ResourceNotFound"):
            print(f"   .. {descripcion}: no existia")
        else:
            print(f"   !! {descripcion}: {codigo}")


def vaciar_bucket(nombre):
    bucket = session.resource("s3").Bucket(nombre)
    bucket.object_versions.delete()  # borra tambien versiones anteriores y delete markers
    bucket.objects.all().delete()
    bucket.delete()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--si", action="store_true", help="no pedir confirmacion")
    args = parser.parse_args()

    estado = leer_estado()
    cuenta = estado.get("account") or session.client("sts").get_caller_identity()["Account"]
    if not args.si:
        r = input(f"Se eliminaran TODOS los recursos y datos de CUTonala en la cuenta {cuenta}. Escribe 'si': ")
        if r.strip().lower() != "si":
            print("Cancelado.")
            return

    lam = session.client("lambda")
    events = session.client("events")
    logs = session.client("logs")
    cw = session.client("cloudwatch")
    ddb = session.client("dynamodb")
    sns = session.client("sns")

    print("[1/6] EventBridge")
    intentar("Destino de la regla", events.remove_targets, Rule=SLA_RULE, Ids=["sla-monitor"])
    intentar(f"Regla {SLA_RULE}", events.delete_rule, Name=SLA_RULE)

    print("[2/6] Lambda y logs")
    intentar("Function URL", lam.delete_function_url_config, FunctionName=API_LAMBDA)
    for fn in (API_LAMBDA, SLA_LAMBDA):
        intentar(f"Lambda {fn}", lam.delete_function, FunctionName=fn)
        intentar(f"Log group de {fn}", logs.delete_log_group, logGroupName=f"/aws/lambda/{fn}")

    print("[3/6] CloudWatch")
    intentar(f"{len(ALARMAS)} alarmas", cw.delete_alarms, AlarmNames=ALARMAS)
    intentar(f"Dashboard {DASHBOARD}", cw.delete_dashboards, DashboardNames=[DASHBOARD])

    print("[4/6] SNS")
    intentar(f"Topic {TOPIC_NAME}", sns.delete_topic, TopicArn=f"arn:aws:sns:{REGION}:{cuenta}:{TOPIC_NAME}")

    print("[5/6] S3")
    buckets = {estado.get("bucket"), estado.get("evidence_bucket"),
               f"ticketflow-web-{cuenta}-{REGION}", f"ticketflow-evidencias-{cuenta}-{REGION}"} - {None}
    for b in sorted(buckets):
        intentar(f"Bucket {b}", vaciar_bucket, b)

    print("[6/6] DynamoDB")
    for t in TABLAS + [RESTORE_TABLE, "ticketflow-tabla"]:
        intentar(f"Tabla {t}", ddb.delete_table, TableName=t)

    if STATE_FILE.exists():
        STATE_FILE.unlink()
    print("\nLimpieza finalizada. Para volver a crear todo: python infra/deploy.py")


if __name__ == "__main__":
    main()
