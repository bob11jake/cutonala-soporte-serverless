import json
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
import boto3

with open("infra/state.json", encoding="utf-8") as f:
    API_URL = json.load(f)["api_url"].rstrip("/")

def limpiar_tablas_anteriores():
    print("=== 0. ELIMINANDO TICKETS Y AUDITORIA ANTERIORES EN DYNAMODB ===")
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    for nombre_tabla, llave in [("ticketflow-tickets", "ticket_id"), ("ticketflow-auditoria", "audit_id")]:
        tabla = dynamodb.Table(nombre_tabla)
        scan = tabla.scan(ProjectionExpression=llave)
        items = scan.get("Items", [])
        with tabla.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={llave: item[llave]})
        print(f" -> {len(items)} registros eliminados de {nombre_tabla}")

def req(path="", metodo="GET", payload=None, token=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"{API_URL}{path}", data=data, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))

limpiar_tablas_anteriores()

print("\n=== 1. AUTENTICACION RBAC (OBTENCION DE TOKEN ADMIN) ===")
code, auth = req("/auth/login", "POST", {"email": "admin@ticketflow.edu", "password": "Admin123!"})
token_admin = auth["token"]
print(f"Login admin -> HTTP {code} | Nombre: {auth['usuario']['nombre']} | Rol: {auth['usuario']['rol']}")

tickets_universidad = [
    {
        "titulo": "Corte de energia electrica en Edificio B",
        "categoria": "ELECTRICIDAD",
        "prioridad": "CRITICA",
        "descripcion": "Falla total de luz en salones de planta alta y pasillo central desde el inicio del turno matutino"
    },
    {
        "titulo": "Fuga de agua potable en sanitarios de Biblioteca",
        "categoria": "AGUA",
        "prioridad": "ALTA",
        "descripcion": "Tuberia rota debajo de los lavabos provoca encharcamiento en el acceso principal"
    },
    {
        "titulo": "Reporte de agresion verbal y conato de violencia en explanada",
        "categoria": "SEGURIDAD",
        "prioridad": "CRITICA",
        "descripcion": "Se solicita intervencion inmediata de vigilancia universitaria junto a la cafeteria central"
    },
    {
        "titulo": "Luminarias fundidas en estacionamiento sur del campus",
        "categoria": "ELECTRICIDAD",
        "prioridad": "MEDIA",
        "descripcion": "Cuatro postes de alumbrado exterior permanecen apagados durante el horario nocturno"
    },
    {
        "titulo": "Filtracion de agua y desprendimiento en plafon del Aula 14",
        "categoria": "INFRAESTRUCTURA",
        "prioridad": "ALTA",
        "descripcion": "Humedad acumulada en el techo cerca del pizarron afecta las clases del modulo"
    },
    {
        "titulo": "Sin suministro de agua en bebederos del Edificio D",
        "categoria": "AGUA",
        "prioridad": "MEDIA",
        "descripcion": "Baja presion en toda la red hidraulica del edificio de laboratorios"
    },
]

print("\n=== 2. POBLANDO INCIDENCIAS UNIVERSITARIAS (SIN ENIE NI COMAS) ===")
for t in tickets_universidad:
    c, _ = req("", "POST", t, token=token_admin)
    print(f"POST [{t['categoria']}] '{t['titulo']}' -> HTTP {c}")

c_guest, _ = req("", "POST", {
    "solicitante": "alumno",
    "titulo": "Caida de red inalambrica en laboratorio de computo 2",
    "categoria": "CONECTIVIDAD",
    "prioridad": "MEDIA",
    "descripcion": "Los equipos de escritorio no obtienen direccion IP para entregar practicas"
})
print(f"POST Alumno sin contrasena -> HTTP {c_guest}")

print("\n=== 3. PRUEBA DE SEGURIDAD RBAC: CAMBIO DE ESTADO SIN TOKEN (HTTP 403) ===")
c_unauth, err_unauth = req("", "PUT", {"ticket_id": "12345678", "estado": "RESUELTO"})
print(f"Bloqueo de operacion privilegiada -> HTTP {c_unauth}: {err_unauth}")

print("\n=== 4. PRUEBA DE VALIDACION: PAYLOAD CORRUPTO (HTTP 400) ===")
c_bad, err_bad = req("", "POST", {"solicitante": "alumno", "titulo": "ab", "categoria": "INVALIDA"}, token=token_admin)
print(f"Rechazo de datos malformados -> HTTP {c_bad}: {err_bad}")

print("\n=== 5. PRUEBA DE CONCURRENCIA: 80 PETICIONES SIMULTANEAS ===")
inicio = time.time()
with ThreadPoolExecutor(max_workers=20) as pool:
    codigos = list(pool.map(lambda _: req("")[0], range(80)))
duracion = round(time.time() - inicio, 2)
resumen = {c: codigos.count(c) for c in set(codigos)}
print(f"80 peticiones resueltas en {duracion}s | Distribucion HTTP: {resumen}")