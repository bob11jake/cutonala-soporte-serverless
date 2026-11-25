"""Pruebas de resiliencia ("chaos engineering") contra el despliegue REAL en AWS.

Cada escenario: 1) mide el estado normal, 2) inyecta una falla real en un
componente de AWS, 3) observa como responde el sistema, 4) restaura el
componente y 5) mide cuanto tarda en recuperarse. La restauracion siempre se
ejecuta (bloques finally), aunque la prueba falle o se interrumpa con Ctrl+C.

Uso:
    python tests/chaos.py                 # todos los escenarios (unos 3-4 minutos)
    python tests/chaos.py --solo 2 5      # solo los escenarios 2 y 5
    python tests/chaos.py --restaurar     # incluye la restauracion PITR real (~15-30 min extra)

Genera docs/resultados_resiliencia.md con la evidencia para la documentacion.
"""
import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import cargar_estado, req  # noqa: E402
from config import API_LAMBDA, REGION, RESTORE_TABLE, ROOT, TICKETS_TABLE, USERS_TABLE  # noqa: E402

estado = cargar_estado()
API = estado["api_url"]
WEB = estado["web_url"]
session = boto3.session.Session(region_name=REGION)
lam = session.client("lambda")
s3 = session.client("s3")
ddb = session.client("dynamodb")

resultados = []


def titulo(n, texto):
    print(f"\n{'=' * 72}\nESCENARIO {n}: {texto}\n{'=' * 72}")


def registrar(n, componente, falla, esperado, observado, recuperacion, ok):
    marca = "SUPERADA" if ok else "FALLIDA"
    print(f"  => {marca} | observado: {observado} | recuperacion: {recuperacion}")
    resultados.append((n, componente, falla, esperado, observado, recuperacion, marca))


def esperar_status(ruta, esperado, limite_seg=120, metodo="GET", payload=None):
    """Sondea la API hasta obtener el status esperado. Devuelve los segundos transcurridos."""
    inicio = time.time()
    while time.time() - inicio < limite_seg:
        status, _, _ = req(API + ruta, metodo, payload)
        if status == esperado:
            return round(time.time() - inicio, 1)
        time.sleep(2)
    return None


def env_actual():
    return lam.get_function_configuration(FunctionName=API_LAMBDA)["Environment"]["Variables"]


def cambiar_env(variables):
    lam.get_waiter("function_updated_v2").wait(FunctionName=API_LAMBDA)
    lam.update_function_configuration(FunctionName=API_LAMBDA, Environment={"Variables": variables})
    lam.get_waiter("function_updated_v2").wait(FunctionName=API_LAMBDA)


def login_admin():
    status, data, _ = req(API + "/auth/login", "POST", {"email": "admin@ticketflow.edu", "password": "Admin123!"})
    if status != 200:
        sys.exit(f"No se pudo iniciar sesion como admin (HTTP {status}): {data}")
    return data["token"]


# --------------------------------------------------------------------- escenarios

def e1_linea_base():
    titulo(1, "Linea base y carga concurrente (200 peticiones, 25 hilos)")
    status, data, ms = req(API + "/health")
    print(f"  /health -> HTTP {status} en {ms} ms: {data}")
    with ThreadPoolExecutor(max_workers=25) as pool:
        res = list(pool.map(lambda _: req(API + "/tickets"), range(200)))
    codigos = [r[0] for r in res]
    tiempos = sorted(r[2] for r in res)
    dist = {c: codigos.count(c) for c in sorted(set(codigos))}
    p50, p95 = statistics.median(tiempos), tiempos[int(len(tiempos) * 0.95) - 1]
    print(f"  Distribucion HTTP: {dist} | p50={p50:.0f} ms | p95={p95:.0f} ms | max={tiempos[-1]} ms")
    errores_5xx = sum(1 for c in codigos if c >= 500 or c == 0)
    registrar(1, "Lambda + DynamoDB", "200 peticiones simultaneas",
              "0 errores 5xx; Lambda escala sola", f"{dist}, p95 {p95:.0f} ms", "N/A", errores_5xx == 0)


def e2_lambda_sin_capacidad():
    titulo(2, "AWS Lambda deja de aceptar invocaciones (concurrencia reservada = 0)")
    try:
        lam.put_function_concurrency(FunctionName=API_LAMBDA, ReservedConcurrentExecutions=0)
        print("  Falla inyectada: la API no puede ejecutar ninguna instancia")
        time.sleep(5)
        status, data, _ = req(API + "/tickets")
        print(f"  GET /tickets durante la falla -> HTTP {status}: {str(data)[:120]}")
        print("  (El frontend reintenta con backoff y muestra 'Servicio Degradado'; la alarma de throttles avisa por SNS)")
    finally:
        lam.delete_function_concurrency(FunctionName=API_LAMBDA)
        print("  Restaurado: se elimino el limite de concurrencia")
    rec = esperar_status("/health", 200)
    registrar(2, "AWS Lambda", "Concurrencia en 0 (throttling total)",
              "Error controlado 429/503, sin perdida de datos", f"HTTP {status}",
              f"{rec} s" if rec is not None else "NO se recupero", status in (429, 500, 502, 503) and rec is not None)


def e3_dynamodb_no_disponible():
    titulo(3, "Amazon DynamoDB no disponible (la Lambda apunta a una tabla inexistente)")
    original = env_actual()
    token = login_admin()
    try:
        cambiar_env({**original, "TICKETS_TABLE": "ticketflow-tabla-inexistente"})
        print("  Falla inyectada: la tabla de tickets 'desaparecio' para la API")
        s_list, d_list, _ = req(API + "/tickets")
        s_health, _, _ = req(API + "/health")
        s_post, d_post, _ = req(API + "/tickets", "POST",
                                {"titulo": "Prueba durante falla", "descripcion": "No debe guardarse", "categoria": "AGUA"},
                                token=token)
        print(f"  GET /tickets -> HTTP {s_list}: {d_list}")
        print(f"  GET /health  -> HTTP {s_health}")
        print(f"  POST /tickets -> HTTP {s_post}: {d_post}")
    finally:
        cambiar_env(original)
        print("  Restaurado: configuracion original de la Lambda")
    rec = esperar_status("/health", 200)
    ok = s_list == 503 and s_health == 503 and s_post == 503 and isinstance(d_list, dict) and rec is not None
    registrar(3, "Amazon DynamoDB", "Tabla inaccesible",
              "HTTP 503 con JSON claro (sin stack trace); /health 503", f"GET {s_list}, POST {s_post}, health {s_health}",
              f"{rec} s" if rec is not None else "NO se recupero", ok)


def e4_sns_caido():
    titulo(4, "Amazon SNS no disponible al crear un ticket CRITICO")
    original = env_actual()
    token = login_admin()
    tid = None
    try:
        cambiar_env({**original, "ALERTS_TOPIC_ARN": original["ALERTS_TOPIC_ARN"] + "-inexistente"})
        print("  Falla inyectada: el topic de alertas no existe")
        status, data, _ = req(API + "/tickets", "POST", {
            "titulo": "Prueba de resiliencia SNS", "descripcion": "Ticket critico con SNS caido",
            "categoria": "SEGURIDAD", "prioridad": "CRITICA"}, token=token)
        tid = data.get("ticket", {}).get("ticket_id") if isinstance(data, dict) else None
        alerta = data.get("ticket", {}).get("alerta_enviada") if isinstance(data, dict) else None
        print(f"  POST ticket CRITICO -> HTTP {status} | alerta_enviada={alerta}")
    finally:
        cambiar_env(original)
        print("  Restaurado: topic SNS original")
        if tid:
            req(API + f"/tickets?ticket_id={tid}", "DELETE", token=token)
    registrar(4, "Amazon SNS", "Topic de alertas inexistente",
              "El ticket se crea (201); solo se pierde la notificacion", f"HTTP {status}, alerta_enviada={alerta}",
              "Inmediata", status == 201 and alerta is False)


def e5_frontend_borrado():
    titulo(5, "Amazon S3: se borra por accidente index.html del sitio web")
    bucket = estado["bucket"]
    s_antes, _, _ = req(WEB + "/index.html")
    s3.delete_object(Bucket=bucket, Key="index.html")
    print("  Falla inyectada: index.html eliminado del bucket")
    time.sleep(2)
    s_durante, _, _ = req(WEB + "/index.html")
    print(f"  GET index.html durante la falla -> HTTP {s_durante}")
    inicio = time.time()
    versiones = s3.list_object_versions(Bucket=bucket, Prefix="index.html")
    for marca in versiones.get("DeleteMarkers", []):
        if marca["Key"] == "index.html" and marca["IsLatest"]:
            s3.delete_object(Bucket=bucket, Key="index.html", VersionId=marca["VersionId"])
    print("  Restaurado: se elimino el 'delete marker' (el versionado conserva la version anterior)")
    s_despues = 0
    while time.time() - inicio < 30:
        s_despues, _, _ = req(WEB + "/index.html")
        if s_despues == 200:
            break
        time.sleep(1)
    rec = round(time.time() - inicio, 1)
    registrar(5, "Amazon S3 (sitio web)", "Borrado accidental de index.html",
              "Se recupera la version previa sin redeploy", f"antes {s_antes}, durante {s_durante}, despues {s_despues}",
              f"{rec} s", s_antes == 200 and s_durante != 200 and s_despues == 200)


def e6_ataques():
    titulo(6, "Entradas maliciosas y ataques de autenticacion")
    tabla = session.resource("dynamodb").Table(USERS_TABLE)
    sys.path.insert(0, str(ROOT / "backend"))
    from auth import hash_password
    email = "caos@ticketflow.edu"
    tabla.put_item(Item={"email": email, "nombre": "caos", "rol": "TECNICO",
                         "password_hash": hash_password("Correcta123!"), "intentos_fallidos": 0})
    try:
        casos = {
            "JSON malformado": req(API + "/tickets", "POST", raw=b"{esto no es json")[0],
            "Cuerpo de 1 MB": req(API + "/tickets", "POST", raw=b'{"titulo":"' + b"A" * 1_000_000 + b'"}')[0],
            "Tipos incorrectos": req(API + "/tickets", "POST", {"titulo": 123, "descripcion": None, "solicitante": []})[0],
            "Token falsificado": req(API + "/auditoria", token="eyJyb2wiOiJBRE1JTiJ9.firmafalsa")[0],
            "Sin token a ruta de admin": req(API + "/auditoria")[0],
            "Inyeccion en filtro": req(API + "/tickets?estado=ABIERTO%27%20OR%201=1")[0],
        }
        for _ in range(5):
            req(API + "/auth/login", "POST", {"email": email, "password": "incorrecta"})
        casos["6o login tras 5 fallos (bloqueo)"] = req(API + "/auth/login", "POST",
                                                       {"email": email, "password": "Correcta123!"})[0]
    finally:
        tabla.delete_item(Key={"email": email})
    for nombre, status in casos.items():
        print(f"  {nombre:<35} -> HTTP {status}")
    sin_5xx = all(0 < s < 500 for s in casos.values())
    registrar(6, "API (validacion y auth)", "Entradas invalidas, token falso, fuerza bruta",
              "4xx en todos los casos, nunca 5xx; bloqueo tras 5 fallos",
              ", ".join(f"{s}" for s in casos.values()), "N/A",
              sin_5xx and casos["6o login tras 5 fallos (bloqueo)"] == 429)


def e7_perdida_de_datos(restaurar):
    titulo(7, "Perdida de datos en DynamoDB: Point-in-Time Recovery")
    desc = ddb.describe_continuous_backups(TableName=TICKETS_TABLE)["ContinuousBackupsDescription"]
    pitr = desc["PointInTimeRecoveryDescription"]
    print(f"  PITR: {pitr['PointInTimeRecoveryStatus']} | restaurable desde {pitr.get('EarliestRestorableDateTime')} "
          f"hasta {pitr.get('LatestRestorableDateTime')}")
    if not restaurar:
        registrar(7, "Amazon DynamoDB (datos)", "Borrado o corrupcion de tickets",
                  "Restaurar a cualquier segundo de los ultimos 35 dias", f"PITR {pitr['PointInTimeRecoveryStatus']}",
                  "No ejecutada (usa --restaurar)", pitr["PointInTimeRecoveryStatus"] == "ENABLED")
        return
    origen = ddb.scan(TableName=TICKETS_TABLE, Select="COUNT")["Count"]
    print(f"  Restaurando {TICKETS_TABLE} -> {RESTORE_TABLE} (puede tardar 15-30 minutos)...")
    inicio = time.time()
    try:
        ddb.restore_table_to_point_in_time(SourceTableName=TICKETS_TABLE, TargetTableName=RESTORE_TABLE,
                                           UseLatestRestorableTime=True, BillingModeOverride="PAY_PER_REQUEST")
        ddb.get_waiter("table_exists").wait(TableName=RESTORE_TABLE, WaiterConfig={"Delay": 20, "MaxAttempts": 180})
        restaurados = ddb.scan(TableName=RESTORE_TABLE, Select="COUNT")["Count"]
    finally:
        try:
            ddb.delete_table(TableName=RESTORE_TABLE)
        except ddb.exceptions.ResourceNotFoundException:
            pass
    minutos = round((time.time() - inicio) / 60, 1)
    registrar(7, "Amazon DynamoDB (datos)", "Restauracion PITR completa",
              "La copia restaurada contiene los mismos items", f"origen {origen} items, restaurada {restaurados}",
              f"{minutos} min", origen == restaurados)


# --------------------------------------------------------------------------- main

def escribir_reporte():
    destino = ROOT / "docs" / "resultados_resiliencia.md"
    destino.parent.mkdir(exist_ok=True)
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lineas = [
        "# Resultados de las pruebas de resiliencia",
        "",
        f"Ejecutadas el {fecha} contra la cuenta {estado.get('account')} ({REGION}) con `python tests/chaos.py`.",
        "",
        "| # | Componente | Falla inyectada | Comportamiento esperado | Observado | Recuperacion | Resultado |",
        "|---|---|---|---|---|---|---|",
    ]
    lineas += ["| " + " | ".join(str(c) for c in fila) + " |" for fila in resultados]
    destino.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print(f"\nReporte guardado en {destino.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solo", nargs="*", type=int, help="numeros de escenario a ejecutar")
    parser.add_argument("--restaurar", action="store_true", help="ejecuta una restauracion PITR real (lenta)")
    args = parser.parse_args()

    escenarios = {1: e1_linea_base, 2: e2_lambda_sin_capacidad, 3: e3_dynamodb_no_disponible, 4: e4_sns_caido,
                  5: e5_frontend_borrado, 6: e6_ataques, 7: lambda: e7_perdida_de_datos(args.restaurar)}
    print(f"API: {API}\nWeb: {WEB}")
    for n in args.solo or sorted(escenarios):
        try:
            escenarios[n]()
        except Exception as e:  # noqa: BLE001  un escenario roto no detiene los demas
            print(f"  !! El escenario {n} no pudo completarse: {e}")
            registrar(n, "-", "-", "-", f"Error del script: {e}", "-", False)

    print(f"\n{'=' * 72}\nRESUMEN")
    for fila in resultados:
        print(f"  [{fila[6]}] {fila[0]}. {fila[1]} - {fila[2]}")
    escribir_reporte()
    sys.exit(0 if all(f[6] == "SUPERADA" for f in resultados) else 1)


if __name__ == "__main__":
    main()
