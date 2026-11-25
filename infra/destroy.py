import boto3
import json
import os

REGION = "us-east-1"
TABLAS = ["ticketflow-tickets", "ticketflow-auditoria", "ticketflow-tabla"]
LAMBDA_NAME = "ticketflow-api"

dynamodb = boto3.client("dynamodb", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
s3 = boto3.resource("s3", region_name=REGION)

def destruir():
    if os.path.exists("infra/state.json"):
        with open("infra/state.json", encoding="utf-8") as f:
            bucket_name = json.load(f).get("bucket")
        if bucket_name:
            print(f"[1/3] Vaciando y eliminando bucket S3: {bucket_name}...")
            try:
                bucket = s3.Bucket(bucket_name)
                bucket.objects.all().delete()
                bucket.delete()
            except Exception:
                pass
        os.remove("infra/state.json")

    print(f"[2/3] Eliminando funcion Lambda: {LAMBDA_NAME}...")
    try:
        lam.delete_function_url_config(FunctionName=LAMBDA_NAME)
    except Exception:
        pass
    try:
        lam.delete_function(FunctionName=LAMBDA_NAME)
    except Exception:
        pass

    print("[3/3] Eliminando tablas DynamoDB...")
    for t in TABLAS:
        try:
            dynamodb.delete_table(TableName=t)
            print(f" -> Eliminada: {t}")
        except Exception:
            pass

    print("Limpieza de recursos finalizada.")

if __name__ == "__main__":
    destruir()