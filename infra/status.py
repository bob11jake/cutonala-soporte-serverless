import boto3
import json
import os

REGION = "us-east-1"
dynamodb = boto3.client("dynamodb", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)

print("=== AUDITORIA DE INFRAESTRUCTURA AWS ===")
for tabla in ["ticketflow-tickets", "ticketflow-auditoria"]:
    try:
        desc = dynamodb.describe_table(TableName=tabla)["Table"]
        gsis = [g["IndexName"] for g in desc.get("GlobalSecondaryIndexes", [])]
        print(f"[DynamoDB] {tabla}: {desc['TableStatus']} | Items: {desc['ItemCount']} | GSIs: {gsis}")
    except Exception:
        print(f"[DynamoDB] {tabla}: NO ENCONTRADA")

try:
    fn = lam.get_function_configuration(FunctionName="ticketflow-api")
    print(f"[Lambda]   {fn['FunctionName']}: {fn['State']} | Runtime: {fn['Runtime']} | Memoria: {fn['MemorySize']}MB")
except Exception:
    print("[Lambda]   ticketflow-api: NO ENCONTRADA")

if os.path.exists("infra/state.json"):
    with open("infra/state.json", encoding="utf-8") as f:
        st = json.load(f)
    print(f"[S3 Web]   {st.get('web_url')}")
    print(f"[API URL]  {st.get('api_url')}")