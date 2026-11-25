"""Carga datos de demostracion y ejecuta una verificacion rapida (smoke test) de la API.

Uso:
    python tests/seed_and_resilience.py              # BORRA los tickets existentes y carga la demo
    python tests/seed_and_resilience.py --conservar  # agrega la demo sin borrar nada

Para las pruebas de resiliencia completas (fallas reales en AWS) usa tests/chaos.py.
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import cargar_estado, req  # noqa: E402
from config import AUDIT_TABLE, REGION, TICKETS_TABLE  # noqa: E402

API = cargar_estado()["api_url"]

TICKETS_DEMO = [
    ("Corte de energia electrica en Edificio B", "ELECTRICIDAD", "CRITICA",
     "Falla total de luz en salones de planta alta y pasillo central desde el inicio del turno matutino", "EN_PROGRESO"),
    ("Fuga de agua potable en sanitarios de Biblioteca", "AGUA", "ALTA",
     "Tuberia rota debajo de los lavabos provoca encharcamiento en el acceso principal", "EN_REVISION"),
    ("Reporte de agresion verbal en explanada", "SEGURIDAD", "CRITICA",
     "Se solicita intervencion inmediata de vigilancia universitaria junto a la cafeteria central", "RESUELTO"),
    ("Luminarias fundidas en estacionamiento sur", "ELECTRICIDAD", "MEDIA",
     "Cuatro postes de alumbrado exterior permanecen apagados durante el horario nocturno", "ABIERTO"),
    ("Filtracion de agua en plafon del Aula 14", "INFRAESTRUCTURA", "ALTA",
     "Humedad acumulada en el techo cerca del pizarron afecta las clases del modulo", "EN_PROGRESO"),
    ("Sin suministro de agua en bebederos del Edificio D", "AGUA", "MEDIA",
     "Baja presion en toda la red hidraulica del edificio de laboratorios", "ABIERTO"),
    ("Proyector descompuesto en Auditorio Central", "INFRAESTRUCTURA", "BAJA",
     "El proyector enciende pero no detecta la entrada HDMI de las laptops", "RESUELTO"),
    ("Puerta de emergencia trabada en Edificio C", "SEGURIDAD", "ALTA",
     "La barra antipanico de la salida norte no abre completamente", "ABIERTO"),
]
FLUJO = ["ABIERTO", "EN_PROGRESO", "EN_REVISION", "RESUELTO"]


def limpiar():
    print("=== 0. ELIMINANDO TICKETS Y AUDITORIA ANTERIORES ===")
    ddb = boto3.resource("dynamodb", region_name=REGION)
    for nombre, llave in [(TICKETS_TABLE, "ticket_id"), (AUDIT_TABLE, "audit_id")]:
        tabla, total, inicio = ddb.Table(nombre), 0, None
        while True:
            kwargs = {"ProjectionExpression": llave, **({"ExclusiveStartKey": inicio} if inicio else {})}
            res = tabla.scan(**kwargs)
            with tabla.batch_writer() as batch:
                for item in res.get("Items", []):
                    batch.delete_item(Key={llave: item[llave]})
            total += len(res.get("Items", []))
            inicio = res.get("LastEvaluatedKey")
            if not inicio:
                break
        print(f" -> {total} registros eliminados de {nombre}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--conservar", action="store_true", help="no borrar los datos existentes")
    args = parser.parse_args()
    if not args.conservar:
        limpiar()

    print("\n=== 1. AUTENTICACION (token de ADMIN y TECNICO) ===")
    code, auth, _ = req(API + "/auth/login", "POST", {"email": "admin@ticketflow.edu", "password": "Admin123!"})
    if code != 200:
        sys.exit(f"Login fallo (HTTP {code}): {auth}. Vuelve a ejecutar python infra/deploy.py")
    admin = auth["token"]
    _, auth_tec, _ = req(API + "/auth/login", "POST", {"email": "soporte@ticketflow.edu", "password": "Soporte123!"})
    tecnico = auth_tec["token"]
    print(f"Login admin -> HTTP {code} | Rol: {auth['usuario']['rol']}")

    print("\n=== 2. POBLANDO INCIDENCIAS UNIVERSITARIAS ===")
    for titulo, categoria, prioridad, descripcion, destino in TICKETS_DEMO:
        c, data, _ = req(API + "/tickets", "POST", {
            "solicitante": "Comunidad CUTonala", "titulo": titulo, "categoria": categoria,
            "prioridad": prioridad, "descripcion": descripcion})
        tid = data["ticket"]["ticket_id"]
        for estado in FLUJO[1:FLUJO.index(destino) + 1]:
            req(API + "/tickets", "PUT", {"ticket_id": tid, "estado": estado, "nota": "Atendido por soporte"}, tecnico)
        if destino != "ABIERTO":
            req(API + "/tickets/comentarios", "POST", {"ticket_id": tid, "texto": "Personal asignado al reporte"}, token=tecnico)
        print(f"POST [{categoria:<15}] {titulo[:45]:<45} -> HTTP {c} -> {destino}")

    print("\n=== 3. SEGURIDAD: cambio de estado sin token (esperado 403) ===")
    c, err, _ = req(API + "/tickets", "PUT", {"ticket_id": "12345678", "estado": "RESUELTO"})
    print(f"HTTP {c}: {err}")

    print("\n=== 4. VALIDACION: datos invalidos (esperado 400) ===")
    c, err, _ = req(API + "/tickets", "POST", {"solicitante": "alumno", "titulo": "ab", "categoria": "INVALIDA"}, token=admin)
    print(f"HTTP {c}: {err}")

    print("\n=== 5. CONCURRENCIA: 80 peticiones simultaneas ===")
    inicio = time.time()
    with ThreadPoolExecutor(max_workers=20) as pool:
        codigos = [r[0] for r in pool.map(lambda _: req(API + "/tickets"), range(80))]
    print(f"80 peticiones en {time.time() - inicio:.2f}s | Distribucion HTTP: { {c: codigos.count(c) for c in set(codigos)} }")

    print("\n=== 6. METRICAS GLOBALES ===")
    _, m, _ = req(API + "/metricas", token=admin)
    print(m)
    print("\nListo. Para las pruebas de fallas reales: python tests/chaos.py")


if __name__ == "__main__":
    main()
