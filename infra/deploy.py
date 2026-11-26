"""Despliegue completo de CUTonala Soporte en AWS (infraestructura como codigo con boto3).

Uso (desde cualquier carpeta):
    python infra/deploy.py                      # despliega o actualiza todo
    python infra/deploy.py --email tu@correo    # ademas suscribe un correo a las alertas SNS
    python infra/deploy.py --solo-frontend      # solo vuelve a publicar el sitio web

Es idempotente: se puede ejecutar cuantas veces sea necesario; lo que ya existe
se actualiza en lugar de duplicarse.
"""
import argparse
import io
import json
import mimetypes
import os
import secrets
import sys
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from config import (  # noqa: E402
    ALARMAS, API_LAMBDA, AUDIT_TABLE, BACKEND_DIR, DASHBOARD, FRONTEND_DIR, LOG_RETENCION_DIAS,
    REGION, ROLE_NAME, SLA_LAMBDA, SLA_RULE, TICKETS_TABLE, TOPIC_NAME, USERS_TABLE,
    USUARIOS_INICIALES, guardar_estado, leer_estado,
)

# Los modulos del backend crean clientes boto3 sin region explicita.
os.environ.setdefault("AWS_DEFAULT_REGION", REGION)
sys.path.insert(0, str(BACKEND_DIR))
from auth import hash_password  # noqa: E402

session = boto3.session.Session(region_name=REGION)
sts = session.client("sts")
ddb = session.client("dynamodb")
ddb_res = session.resource("dynamodb")
lam = session.client("lambda")
s3 = session.client("s3")
sns = session.client("sns")
events = session.client("events")
logs = session.client("logs")
cw = session.client("cloudwatch")

PASOS = 9


def paso(n, texto):
    print(f"\n[{n}/{PASOS}] {texto}")


def ok(texto):
    print(f"   -> {texto}")


def aviso(texto):
    print(f"   !! {texto}")


# --------------------------------------------------------------------- identidad

def identidad():
    ident = sts.get_caller_identity()
    cuenta = ident["Account"]
    ok(f"Cuenta AWS {cuenta} | region {REGION} | identidad {ident['Arn'].split('/')[-1]}")
    return cuenta, f"arn:aws:iam::{cuenta}:role/{ROLE_NAME}"


# -------------------------------------------------------------------------- SNS

def crear_topic(email):
    arn = sns.create_topic(Name=TOPIC_NAME)["TopicArn"]  # idempotente
    ok(f"Topic SNS: {arn}")
    if email:
        subs = sns.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"]
        if any(s["Endpoint"] == email for s in subs):
            ok(f"{email} ya estaba suscrito")
        else:
            sns.subscribe(TopicArn=arn, Protocol="email", Endpoint=email)
            ok(f"Suscripcion enviada a {email}: confirma el correo que te llego de AWS")
    return arn


# --------------------------------------------------------------------- DynamoDB

def _esperar_tabla(nombre):
    ddb.get_waiter("table_exists").wait(TableName=nombre)
    while True:
        t = ddb.describe_table(TableName=nombre)["Table"]
        if t["TableStatus"] == "ACTIVE" and all(
            g["IndexStatus"] == "ACTIVE" for g in t.get("GlobalSecondaryIndexes", [])
        ):
            return t
        time.sleep(5)


def _crear_tabla(nombre, **kwargs):
    try:
        ddb.create_table(TableName=nombre, BillingMode="PAY_PER_REQUEST", **kwargs)
        _esperar_tabla(nombre)
        ok(f"Tabla '{nombre}' creada")
    except ddb.exceptions.ResourceInUseException:
        ok(f"Tabla '{nombre}' ya existe")


def _gsi(nombre, hash_key, range_key):
    return {
        "IndexName": nombre,
        "KeySchema": [{"AttributeName": hash_key, "KeyType": "HASH"}, {"AttributeName": range_key, "KeyType": "RANGE"}],
        "Projection": {"ProjectionType": "ALL"},
    }


def crear_tablas():
    _crear_tabla(
        TICKETS_TABLE,
        KeySchema=[{"AttributeName": "ticket_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": n, "AttributeType": "S"} for n in ("ticket_id", "estado", "categoria", "fecha_creacion")
        ],
        GlobalSecondaryIndexes=[
            _gsi("EstadoIndex", "estado", "fecha_creacion"),
            _gsi("CategoriaIndex", "categoria", "fecha_creacion"),
        ],
    )
    _crear_tabla(
        AUDIT_TABLE,
        KeySchema=[{"AttributeName": "audit_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("audit_id", "tipo", "timestamp")],
        GlobalSecondaryIndexes=[_gsi("TimelineIndex", "tipo", "timestamp")],
    )
    _crear_tabla(
        USERS_TABLE,
        KeySchema=[{"AttributeName": "email", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "email", "AttributeType": "S"}],
    )

    # Migracion: despliegues anteriores crearon la auditoria sin TimelineIndex.
    audit = ddb.describe_table(TableName=AUDIT_TABLE)["Table"]
    if not any(g["IndexName"] == "TimelineIndex" for g in audit.get("GlobalSecondaryIndexes", [])):
        ok("Migrando tabla de auditoria: agregando TimelineIndex (puede tardar unos minutos)...")
        ddb.update_table(
            TableName=AUDIT_TABLE,
            AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in ("tipo", "timestamp")],
            GlobalSecondaryIndexUpdates=[{"Create": _gsi("TimelineIndex", "tipo", "timestamp")}],
        )
        _esperar_tabla(AUDIT_TABLE)
        tabla = ddb_res.Table(AUDIT_TABLE)
        for it in tabla.scan().get("Items", []):
            tabla.update_item(Key={"audit_id": it["audit_id"]}, UpdateExpression="SET tipo = :t",
                              ExpressionAttributeValues={":t": "EVENTO"})

    # Point-in-Time Recovery: permite restaurar la tabla a cualquier segundo de los ultimos 35 dias.
    try:
        ddb.update_continuous_backups(
            TableName=TICKETS_TABLE,
            PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
        )
        ok("Point-in-Time Recovery activo en la tabla de tickets")
    except ClientError as e:
        aviso(f"No se pudo activar PITR ({e.response['Error']['Code']}); se continua sin el")

    # TTL: los registros de auditoria se borran solos al cumplir un ano (sin costo de escritura).
    try:
        ddb.update_time_to_live(TableName=AUDIT_TABLE, TimeToLiveSpecification={"Enabled": True, "AttributeName": "expira"})
        ok("TTL de 365 dias activo en la bitacora")
    except ClientError as e:
        if "already enabled" not in str(e).lower():
            aviso(f"No se pudo activar TTL ({e.response['Error']['Code']})")


def crear_usuarios_y_contadores():
    usuarios = ddb_res.Table(USERS_TABLE)
    for u in USUARIOS_INICIALES:
        try:
            usuarios.put_item(
                Item={"email": u["email"], "nombre": u["nombre"], "rol": u["rol"],
                      "password_hash": hash_password(u["password"]), "intentos_fallidos": 0},
                ConditionExpression="attribute_not_exists(email)",  # no pisa contrasenas cambiadas
            )
            ok(f"Usuario {u['email']} ({u['rol']}) creado")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            ok(f"Usuario {u['email']} ya existe")

    tickets = ddb_res.Table(TICKETS_TABLE)
    if "Item" not in tickets.get_item(Key={"ticket_id": "#STATS"}):
        os.environ.setdefault("TICKETS_TABLE", TICKETS_TABLE)
        import repository
        stats = repository.recalcular_contadores()
        ok(f"Contadores inicializados ({stats['total']} tickets existentes)")


# --------------------------------------------------------------------------- S3

def _crear_bucket(nombre):
    try:
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=nombre)
        else:
            s3.create_bucket(Bucket=nombre, CreateBucketConfiguration={"LocationConstraint": REGION})
        ok(f"Bucket '{nombre}' creado")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou",):
            raise
        ok(f"Bucket '{nombre}' ya existe")


def crear_bucket_evidencias(cuenta):
    nombre = f"ticketflow-evidencias-{cuenta}-{REGION}"
    _crear_bucket(nombre)
    s3.put_public_access_block(
        Bucket=nombre,
        PublicAccessBlockConfiguration={k: True for k in (
            "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")},
    )
    s3.put_bucket_cors(Bucket=nombre, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["*"], "AllowedMethods": ["GET", "POST"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 3000,
    }]})
    s3.put_bucket_lifecycle_configuration(Bucket=nombre, LifecycleConfiguration={"Rules": [
        {"ID": "expirar-evidencias", "Status": "Enabled", "Filter": {"Prefix": "evidencias/"},
         "Expiration": {"Days": 365}, "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}},
    ]})
    ok("Bucket privado (sin acceso publico), CORS para subida directa y expiracion a 365 dias")
    return nombre


def publicar_frontend(bucket, api_url):
    _crear_bucket(bucket)
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={k: False for k in (
            "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")},
    )
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Sid": "LecturaPublicaSitio", "Effect": "Allow", "Principal": "*",
                       "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket}/*"}],
    }))
    s3.put_bucket_website(Bucket=bucket, WebsiteConfiguration={
        "IndexDocument": {"Suffix": "index.html"}, "ErrorDocument": {"Key": "index.html"}})

    (FRONTEND_DIR / "config.js").write_text(f'const API_URL = "{api_url}";\n', encoding="utf-8")
    subidos = 0
    for archivo in sorted(FRONTEND_DIR.iterdir()):
        if not archivo.is_file() or archivo.name.startswith("."):
            continue
        tipo = mimetypes.guess_type(archivo.name)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo == "application/javascript":
            tipo += "; charset=utf-8"
        s3.upload_file(str(archivo), bucket, archivo.name,
                       ExtraArgs={"ContentType": tipo, "CacheControl": "no-cache"})
        subidos += 1
    ok(f"{subidos} archivos publicados (versionado activo para recuperar borrados)")
    return f"http://{bucket}.s3-website-{REGION}.amazonaws.com"


# ----------------------------------------------------------------------- Lambda

def _zip_backend():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for archivo in sorted(BACKEND_DIR.glob("*.py")):
            zf.write(archivo, arcname=archivo.name)
    return buf.getvalue()


def _log_group(funcion):
    nombre = f"/aws/lambda/{funcion}"
    try:
        logs.create_log_group(logGroupName=nombre)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    logs.put_retention_policy(logGroupName=nombre, retentionInDays=LOG_RETENCION_DIAS)


def _desplegar_funcion(nombre, handler, role_arn, env, zip_bytes, timeout, memoria):
    _log_group(nombre)
    for intento in range(5):
        try:
            try:
                lam.create_function(
                    FunctionName=nombre, Runtime="python3.12", Role=role_arn, Handler=handler,
                    Code={"ZipFile": zip_bytes}, Timeout=timeout, MemorySize=memoria,
                    Environment={"Variables": env},
                    Description="CUTonala Soporte",
                )
                ok(f"Lambda '{nombre}' creada")
            except lam.exceptions.ResourceConflictException:
                lam.update_function_code(FunctionName=nombre, ZipFile=zip_bytes)
                lam.get_waiter("function_updated_v2").wait(FunctionName=nombre)
                lam.update_function_configuration(
                    FunctionName=nombre, Handler=handler, Timeout=timeout, MemorySize=memoria,
                    Environment={"Variables": env}, Role=role_arn,
                )
                ok(f"Lambda '{nombre}' actualizada")
            break
        except lam.exceptions.InvalidParameterValueException as e:
            if "role" in str(e).lower() and intento < 4:
                time.sleep(5)  # el rol aun se propaga
                continue
            raise
    lam.get_waiter("function_active_v2").wait(FunctionName=nombre)
    lam.get_waiter("function_updated_v2").wait(FunctionName=nombre)
    return lam.get_function(FunctionName=nombre)["Configuration"]["FunctionArn"]


def desplegar_lambdas(role_arn, env):
    zip_bytes = _zip_backend()
    ok(f"Paquete del backend: {len(zip_bytes) / 1024:.1f} KB")
    _desplegar_funcion(API_LAMBDA, "lambda_function.lambda_handler", role_arn, env, zip_bytes, 15, 256)
    sla_arn = _desplegar_funcion(SLA_LAMBDA, "sla_monitor.lambda_handler", role_arn, env, zip_bytes, 60, 256)

    cors = {"AllowOrigins": ["*"], "AllowMethods": ["GET", "POST", "PUT", "DELETE"],
            "AllowHeaders": ["content-type", "authorization"], "MaxAge": 3600}
    try:
        url = lam.create_function_url_config(FunctionName=API_LAMBDA, AuthType="NONE", Cors=cors)["FunctionUrl"]
    except lam.exceptions.ResourceConflictException:
        url = lam.update_function_url_config(FunctionName=API_LAMBDA, AuthType="NONE", Cors=cors)["FunctionUrl"]
    for sid, accion, extra in [
        ("PublicURLAccess", "lambda:InvokeFunctionUrl", {"FunctionUrlAuthType": "NONE"}),
        ("PublicInvokeAccess", "lambda:InvokeFunction", {"InvokedViaFunctionUrl": True}),
    ]:
        try:
            lam.add_permission(FunctionName=API_LAMBDA, StatementId=sid, Action=accion, Principal="*", **extra)
        except lam.exceptions.ResourceConflictException:
            pass
    ok(f"Function URL: {url}")
    return url.rstrip("/"), sla_arn


def programar_sla(sla_arn):
    regla = events.put_rule(Name=SLA_RULE, ScheduleExpression="rate(15 minutes)", State="ENABLED",
                            Description="Revisa tickets con SLA vencido")["RuleArn"]
    events.put_targets(Rule=SLA_RULE, Targets=[{"Id": "sla-monitor", "Arn": sla_arn}])
    try:
        lam.add_permission(FunctionName=SLA_LAMBDA, StatementId="EventBridgeInvoke",
                           Action="lambda:InvokeFunction", Principal="events.amazonaws.com", SourceArn=regla)
    except lam.exceptions.ResourceConflictException:
        pass
    ok(f"Regla EventBridge '{SLA_RULE}' -> {SLA_LAMBDA} cada 15 minutos")


# ------------------------------------------------------------------- CloudWatch

def crear_monitoreo(topic_arn):
    def alarma(nombre, funcion, metrica, estadistica, umbral, descripcion):
        cw.put_metric_alarm(
            AlarmName=nombre, AlarmDescription=descripcion, Namespace="AWS/Lambda", MetricName=metrica,
            Dimensions=[{"Name": "FunctionName", "Value": funcion}], Statistic=estadistica,
            Period=300, EvaluationPeriods=1, Threshold=umbral, ComparisonOperator="GreaterThanOrEqualToThreshold",
            TreatMissingData="notBreaching", AlarmActions=[topic_arn], OKActions=[topic_arn],
        )

    alarma(ALARMAS[0], API_LAMBDA, "Errors", "Sum", 5, "5 o mas errores de la API en 5 minutos")
    alarma(ALARMAS[1], API_LAMBDA, "Throttles", "Sum", 1, "La API esta rechazando peticiones por concurrencia")
    alarma(ALARMAS[2], API_LAMBDA, "Duration", "Average", 3000, "Latencia promedio de la API mayor a 3 s")
    alarma(ALARMAS[3], SLA_LAMBDA, "Errors", "Sum", 1, "Fallo la revision automatica de SLA")
    ok(f"{len(ALARMAS)} alarmas CloudWatch conectadas a SNS")

    def lam_metric(metrica, funcion=API_LAMBDA, stat="Sum"):
        return ["AWS/Lambda", metrica, "FunctionName", funcion, {"stat": stat}]

    widgets = [
        {"type": "metric", "x": 0, "y": 0, "width": 12, "height": 6, "properties": {
            "title": "API: invocaciones, errores y throttles", "region": REGION, "period": 300,
            "metrics": [lam_metric("Invocations"), lam_metric("Errors"), lam_metric("Throttles")]}},
        {"type": "metric", "x": 12, "y": 0, "width": 12, "height": 6, "properties": {
            "title": "API: latencia (ms)", "region": REGION, "period": 300,
            "metrics": [lam_metric("Duration", stat="Average"), lam_metric("Duration", stat="p99")]}},
        {"type": "metric", "x": 0, "y": 6, "width": 12, "height": 6, "properties": {
            "title": "DynamoDB: capacidad consumida (tickets)", "region": REGION, "period": 300,
            "metrics": [["AWS/DynamoDB", "ConsumedReadCapacityUnits", "TableName", TICKETS_TABLE, {"stat": "Sum"}],
                        ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", TICKETS_TABLE, {"stat": "Sum"}]]}},
        {"type": "metric", "x": 12, "y": 6, "width": 12, "height": 6, "properties": {
            "title": "Tickets con SLA vencido", "region": REGION, "period": 900,
            "metrics": [["CUTonalaSoporte", "TicketsSLAVencidos", {"stat": "Maximum"}]]}},
        {"type": "alarm", "x": 0, "y": 12, "width": 24, "height": 3, "properties": {
            "title": "Alarmas", "alarms": [f"arn:aws:cloudwatch:{REGION}:{topic_arn.split(':')[4]}:alarm:{a}" for a in ALARMAS]}},
    ]
    cw.put_dashboard(DashboardName=DASHBOARD, DashboardBody=json.dumps({"widgets": widgets}))
    ok(f"Dashboard '{DASHBOARD}' publicado")


# ------------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Despliega CUTonala Soporte en AWS")
    parser.add_argument("--email", help="correo que recibira las alertas (SNS)")
    parser.add_argument("--solo-frontend", action="store_true", help="solo vuelve a publicar el sitio web")
    args = parser.parse_args()

    estado = leer_estado()
    inicio = time.time()

    paso(1, "Verificando credenciales de AWS")
    try:
        cuenta, role_arn = identidad()
    except ClientError as e:
        print(f"\nERROR: las credenciales no son validas ({e.response['Error']['Code']}).")
        print("Copia de nuevo AWS Details -> AWS CLI en ~/.aws/credentials (caducan cada sesion del laboratorio).")
        sys.exit(1)
    if estado.get("account") and estado["account"] != cuenta:
        print(f"   !! infra/state.json pertenece a la cuenta {estado['account']}; se ignora y se crea uno nuevo.")
        estado = {}

    bucket_web = estado.get("bucket") or f"ticketflow-web-{cuenta}-{REGION}"

    if args.solo_frontend:
        if not estado.get("api_url"):
            sys.exit("Primero ejecuta el despliegue completo: python infra/deploy.py")
        paso(9, f"Publicando frontend en S3 ({bucket_web})")
        estado["web_url"] = publicar_frontend(bucket_web, estado["api_url"])
        guardar_estado(estado)
        print(f"\nListo: {estado['web_url']}")
        return

    paso(2, "Amazon SNS: topic de alertas")
    topic_arn = crear_topic(args.email)

    paso(3, "Amazon DynamoDB: tablas, indices, PITR y TTL")
    crear_tablas()

    paso(4, "Usuarios del personal (contrasenas con hash PBKDF2) y contadores")
    crear_usuarios_y_contadores()

    paso(5, "Amazon S3: bucket privado de evidencias")
    bucket_evid = crear_bucket_evidencias(cuenta)

    paso(6, "AWS Lambda: API y monitor de SLA")
    jwt_secret = estado.get("jwt_secret") or secrets.token_hex(32)
    env = {
        "TICKETS_TABLE": TICKETS_TABLE, "AUDIT_TABLE": AUDIT_TABLE, "USERS_TABLE": USERS_TABLE,
        "EVIDENCE_BUCKET": bucket_evid, "ALERTS_TOPIC_ARN": topic_arn, "JWT_SECRET": jwt_secret,
    }
    api_url, sla_arn = desplegar_lambdas(role_arn, env)

    paso(7, "Amazon EventBridge: revision automatica de SLA")
    programar_sla(sla_arn)

    paso(8, "Amazon CloudWatch: alarmas y dashboard")
    crear_monitoreo(topic_arn)

    paso(9, f"Publicando frontend en S3 ({bucket_web})")
    web_url = publicar_frontend(bucket_web, api_url)

    guardar_estado({
        "account": cuenta, "region": REGION, "bucket": bucket_web, "evidence_bucket": bucket_evid,
        "api_url": api_url, "web_url": web_url, "topic_arn": topic_arn, "jwt_secret": jwt_secret,
    })

    print("\n" + "=" * 70)
    print(f"DESPLIEGUE COMPLETADO en {time.time() - inicio:.0f} s")
    print(f"  Aplicacion web : {web_url}")
    print(f"  API            : {api_url}")
    print(f"  Dashboard      : https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#dashboards/dashboard/{DASHBOARD}")
    print("=" * 70)


if __name__ == "__main__":
    main()
