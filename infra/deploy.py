import boto3
import json
import time
import zipfile
import io
import os

REGION = "us-east-1"
ACCOUNT_ID = "998684510664"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/LabRole"

TICKETS_TABLE = "ticketflow-tickets"
AUDIT_TABLE = "ticketflow-auditoria"
LAMBDA_NAME = "ticketflow-api"

dynamodb = boto3.client("dynamodb", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)

def crear_tablas():
    print("[1/4] Provisionando tablas DynamoDB e Indices Secundarios (GSI)...")
    try:
        dynamodb.create_table(
            TableName=TICKETS_TABLE,
            KeySchema=[{"AttributeName": "ticket_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "ticket_id", "AttributeType": "S"},
                {"AttributeName": "estado", "AttributeType": "S"},
                {"AttributeName": "categoria", "AttributeType": "S"},
                {"AttributeName": "fecha_creacion", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "EstadoIndex",
                    "KeySchema": [
                        {"AttributeName": "estado", "KeyType": "HASH"},
                        {"AttributeName": "fecha_creacion", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "CategoriaIndex",
                    "KeySchema": [
                        {"AttributeName": "categoria", "KeyType": "HASH"},
                        {"AttributeName": "fecha_creacion", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.get_waiter("table_exists").wait(TableName=TICKETS_TABLE)
        print(f" -> Tabla '{TICKETS_TABLE}' con 2 GSIs activa.")
    except dynamodb.exceptions.ResourceInUseException:
        print(f" -> Tabla '{TICKETS_TABLE}' ya existe.")

    try:
        dynamodb.create_table(
            TableName=AUDIT_TABLE,
            KeySchema=[{"AttributeName": "audit_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "audit_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.get_waiter("table_exists").wait(TableName=AUDIT_TABLE)
        print(f" -> Tabla '{AUDIT_TABLE}' activa.")
    except dynamodb.exceptions.ResourceInUseException:
        print(f" -> Tabla '{AUDIT_TABLE}' ya existe.")

def empaquetar_backend():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for archivo in os.listdir("backend"):
            if archivo.endswith(".py"):
                zf.write(f"backend/{archivo}", arcname=archivo)
    buf.seek(0)
    return buf.read()

def desplegar_lambda():
    print("[2/4] Empaquetando modulos Python y desplegando Lambda...")
    zip_bytes = empaquetar_backend()
    env_vars = {
        "TICKETS_TABLE": TICKETS_TABLE,
        "AUDIT_TABLE": AUDIT_TABLE,
        "JWT_SECRET": "ticketflow-enterprise-secret-2026",
    }
    try:
        lam.create_function(
            FunctionName=LAMBDA_NAME,
            Runtime="python3.12",
            Role=ROLE_ARN,
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": zip_bytes},
            Timeout=15,
            MemorySize=256,
            Environment={"Variables": env_vars},
        )
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=zip_bytes)
        lam.get_waiter("function_updated_v2").wait(FunctionName=LAMBDA_NAME)
        lam.update_function_configuration(FunctionName=LAMBDA_NAME, Environment={"Variables": env_vars})

    lam.get_waiter("function_active_v2").wait(FunctionName=LAMBDA_NAME)

    cors_cfg = {
        "AllowOrigins": ["*"],
        "AllowMethods": ["GET", "POST", "PUT", "DELETE"],
        "AllowHeaders": ["content-type", "authorization"],
    }
    try:
        url_cfg = lam.create_function_url_config(FunctionName=LAMBDA_NAME, AuthType="NONE", Cors=cors_cfg)
    except lam.exceptions.ResourceConflictException:
        url_cfg = lam.update_function_url_config(FunctionName=LAMBDA_NAME, AuthType="NONE", Cors=cors_cfg)

    for stmt_id, action, extra in [
        ("PublicURLAccess", "lambda:InvokeFunctionUrl", {"FunctionUrlAuthType": "NONE"}),
        ("PublicInvokeAccess", "lambda:InvokeFunction", {"InvokedViaFunctionUrl": True}),
    ]:
        try:
            lam.add_permission(
                FunctionName=LAMBDA_NAME,
                StatementId=stmt_id,
                Action=action,
                Principal="*",
                **extra,
            )
        except lam.exceptions.ResourceConflictException:
            pass

    print(f" -> API URL: {url_cfg['FunctionUrl']}")
    return url_cfg["FunctionUrl"]

def desplegar_s3(api_url):
    bucket_name = f"ticketflow-enterprise-{int(time.time())}"
    if os.path.exists("infra/state.json"):
        with open("infra/state.json", encoding="utf-8") as f:
            prev = json.load(f).get("bucket")
            if prev:
                bucket_name = prev

    print(f"[3/4] Publicando Frontend modular en S3 ({bucket_name})...")
    try:
        s3.create_bucket(Bucket=bucket_name)
    except Exception:
        pass

    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": False,
            "IgnorePublicAcls": False,
            "BlockPublicPolicy": False,
            "RestrictPublicBuckets": False,
        },
    )
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": "*",
            "Action": "s3:GetObject",
            "Resource": f"arn:aws:s3:::{bucket_name}/*",
        }],
    }
    s3.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))
    s3.put_bucket_website(
        Bucket=bucket_name,
        WebsiteConfiguration={"IndexDocument": {"Suffix": "index.html"}},
    )

    with open("frontend/config.js", "w", encoding="utf-8") as f:
        f.write(f'const API_URL = "{api_url}";\n')

    tipos = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}
    for archivo in os.listdir("frontend"):
        ext = os.path.splitext(archivo)[1]
        s3.upload_file(
            f"frontend/{archivo}",
            bucket_name,
            archivo,
            ExtraArgs={"ContentType": tipos.get(ext, "text/plain")},
        )

    web_url = f"http://{bucket_name}.s3-website-{REGION}.amazonaws.com"
    with open("infra/state.json", "w", encoding="utf-8") as f:
        json.dump({"bucket": bucket_name, "api_url": api_url, "web_url": web_url}, f, indent=2)

    print(f"\n[4/4] DESPLIEGUE COMPLETADO. Abre la plataforma en:\n{web_url}")

if __name__ == "__main__":
    crear_tablas()
    url = desplegar_lambda()
    desplegar_s3(url)