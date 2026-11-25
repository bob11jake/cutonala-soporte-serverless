"""Pruebas unitarias y de integracion del backend contra AWS simulado (moto).

Ejecutar:  python -m pytest -q
"""
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest


# ------------------------------------------------------------------ autenticacion

def test_hash_password_es_verificable_y_con_sal():
    import auth
    h1, h2 = auth.hash_password("Clave123!"), auth.hash_password("Clave123!")
    assert h1 != h2  # sal distinta
    assert auth.verificar_password("Clave123!", h1)
    assert not auth.verificar_password("otra", h1)
    assert not auth.verificar_password("x", "basura")


def test_token_alterado_o_expirado_es_rechazado():
    import auth
    token = auth.generar_token("a@b.c", "a", "ADMIN")
    assert auth.verificar_token({"authorization": f"Bearer {token}"})["rol"] == "ADMIN"
    cuerpo, firma = token.split(".")
    falso = auth._b64(b'{"email":"x","nombre":"x","rol":"ADMIN","exp":9999999999}')
    assert auth.verificar_token({"authorization": f"Bearer {falso}.{firma}"}) is None
    viejo = auth.generar_token("a@b.c", "a", "ADMIN", ahora=time.time() - auth.DURACION_TOKEN_SEG - 10)
    assert auth.verificar_token({"authorization": f"Bearer {viejo}"}) is None
    assert auth.verificar_token({}) is None


def test_token_invalido_responde_401(api):
    assert api("GET", "/auditoria", token="basura.firma")[0] == 401
    assert api("POST", "/tickets", {"solicitante": "ana", "titulo": "Titulo ok", "descripcion": "Desc ok"},
               token="basura.firma")[0] == 401


def test_login_correcto_e_incorrecto(api):
    c, data = api("POST", "/auth/login", {"email": "soporte@ticketflow.edu", "password": "Soporte123!"})
    assert c == 200 and data["usuario"]["rol"] == "TECNICO"
    c, _ = api("POST", "/auth/login", {"email": "soporte@ticketflow.edu", "password": "mal"})
    assert c == 401
    c, _ = api("POST", "/auth/login", {"email": "nadie@x.com", "password": "mal"})
    assert c == 401


def test_bloqueo_por_fuerza_bruta(api):
    import boto3
    from auth import hash_password
    boto3.resource("dynamodb").Table("ticketflow-usuarios").put_item(Item={
        "email": "victima@ticketflow.edu", "nombre": "v", "rol": "TECNICO",
        "password_hash": hash_password("Correcta1!"), "intentos_fallidos": 0})
    for _ in range(5):
        assert api("POST", "/auth/login", {"email": "victima@ticketflow.edu", "password": "x"})[0] == 401
    # aun con la contrasena correcta queda bloqueada
    assert api("POST", "/auth/login", {"email": "victima@ticketflow.edu", "password": "Correcta1!"})[0] == 429


# ------------------------------------------------------------------------ tickets

def test_crear_ticket_anonimo_y_validaciones(api):
    t = api.crear()
    assert t["estado"] == "ABIERTO" and t["sla_estado"] == "EN_TIEMPO" and len(t["ticket_id"]) == 8
    # categoria por defecto valida (antes era SOFTWARE e invalida)
    c, data = api("POST", "/tickets", {"solicitante": "ana", "titulo": "Sin categoria", "descripcion": "texto valido"})
    assert c == 201 and data["ticket"]["categoria"] == "INFRAESTRUCTURA"
    assert api("POST", "/tickets", {"solicitante": "ana", "titulo": "ab", "descripcion": "x"})[0] == 400
    assert api("POST", "/tickets", {"titulo": "Titulo ok", "descripcion": "Descripcion ok"})[0] == 400  # sin nombre
    assert api("POST", "/tickets", {"solicitante": "ana", "titulo": "Titulo ok", "descripcion": "Descripcion ok",
                                    "categoria": "NOPE"})[0] == 400
    assert api("POST", "/tickets", raw="{no es json")[0] == 400
    assert api("POST", "/tickets", raw="[1,2]")[0] == 400


def test_html_se_guarda_como_texto(api):
    t = api.crear(titulo="<img src=x onerror=alert(1)>")
    assert t["titulo"] == "<img src=x onerror=alert(1)>"  # el frontend lo escapa al mostrarlo


def test_flujo_de_estados_y_rbac(api):
    t = api.crear()
    tid = t["ticket_id"]
    assert api("PUT", "/tickets", {"ticket_id": tid, "estado": "EN_PROGRESO"})[0] == 403  # sin token
    tecnico = api.login("soporte@ticketflow.edu", "Soporte123!")
    assert api("PUT", "/tickets", {"ticket_id": tid, "estado": "RESUELTO"}, tecnico)[0] == 409  # salto invalido
    for estado in ("EN_PROGRESO", "EN_REVISION", "RESUELTO"):
        c, data = api("PUT", "/tickets", {"ticket_id": tid, "estado": estado, "nota": "ok"}, tecnico)
        assert c == 200, data
    assert api("PUT", "/tickets", {"ticket_id": "ZZZZ9999", "estado": "EN_PROGRESO"}, tecnico)[0] == 404
    # solo ADMIN elimina
    assert api("DELETE", "/tickets", token=tecnico, qs={"ticket_id": tid})[0] == 403
    admin = api.login()
    assert api("DELETE", "/tickets", token=admin, qs={"ticket_id": tid})[0] == 200
    assert api("DELETE", "/tickets", token=admin, qs={"ticket_id": tid})[0] == 404


def test_contadores_consistentes(api):
    admin = api.login()
    antes = api("GET", "/metricas", token=admin)[1]["contadores"]
    t = api.crear(categoria="SEGURIDAD", prioridad="ALTA")
    api("PUT", "/tickets", {"ticket_id": t["ticket_id"], "estado": "EN_PROGRESO"}, admin)
    despues = api("GET", "/metricas", token=admin)[1]["contadores"]
    assert despues["total"] == antes["total"] + 1
    assert despues["c_SEGURIDAD"] == antes.get("c_SEGURIDAD", 0) + 1
    assert despues["e_EN_PROGRESO"] == antes.get("e_EN_PROGRESO", 0) + 1
    assert despues["e_ABIERTO"] == antes.get("e_ABIERTO", 0)
    assert api("GET", "/metricas")[0] == 403


def test_listado_y_filtros(api):
    api.crear(categoria="CONECTIVIDAD")
    c, data = api("GET", "/tickets")
    assert c == 200 and data["tickets"]
    assert all(t["ticket_id"] != "#STATS" for t in data["tickets"])
    fechas = [t["fecha_creacion"] for t in data["tickets"]]
    assert fechas == sorted(fechas, reverse=True)
    c, data = api("GET", "/", qs={"categoria": "CONECTIVIDAD"})
    assert c == 200 and all(t["categoria"] == "CONECTIVIDAD" for t in data["tickets"])
    assert api("GET", "/", qs={"estado": "XYZ"})[0] == 400


def test_comentarios(api):
    t = api.crear()
    assert api("POST", "/tickets/comentarios", {"ticket_id": t["ticket_id"], "autor": "luis", "texto": "Sigue igual"})[0] == 201
    assert api("POST", "/tickets/comentarios", {"ticket_id": "NOEXISTE", "autor": "luis", "texto": "hola"})[0] == 404
    assert api("POST", "/tickets/comentarios", {"ticket_id": "#STATS", "autor": "luis", "texto": "hola"})[0] == 400


def test_auditoria_ordenada_y_restringida(api):
    admin = api.login()
    api.crear(titulo="Ultimo evento auditado")
    c, data = api("GET", "/auditoria", token=admin)
    assert c == 200
    marcas = [a["timestamp"] for a in data["auditoria"]]
    assert marcas == sorted(marcas, reverse=True)
    assert data["auditoria"][0]["accion"] == "CREAR_TICKET"
    assert api("GET", "/auditoria")[0] == 403


def test_rutas_desconocidas(api):
    assert api("GET", "/no-existe")[0] == 404
    assert api("PATCH", "/tickets")[0] == 405


# ---------------------------------------------------------------------- evidencias

def test_evidencias_flujo_completo(api):
    import boto3
    t = api.crear()
    tid = t["ticket_id"]
    assert api("POST", "/tickets/evidencia", {"ticket_id": tid, "content_type": "application/pdf"})[0] == 400
    c, sub = api("POST", "/tickets/evidencia", {"ticket_id": tid, "content_type": "image/png"})
    assert c == 200 and sub["key"].startswith(f"evidencias/{tid}/") and "policy" in {k.lower() for k in sub["fields"]}
    # confirmar antes de subir -> 404
    assert api("POST", "/tickets/evidencia/confirmar", {"ticket_id": tid, "key": sub["key"], "autor": "ana"})[0] == 404
    boto3.client("s3").put_object(Bucket="evidencias-test", Key=sub["key"], Body=b"\x89PNG....")
    c, data = api("POST", "/tickets/evidencia/confirmar", {"ticket_id": tid, "key": sub["key"], "autor": "ana"})
    assert c == 201 and "X-Amz-Signature" in data["url"]
    # no se puede registrar la evidencia de otro ticket
    otro = api.crear()
    assert api("POST", "/tickets/evidencia/confirmar", {"ticket_id": otro["ticket_id"], "key": sub["key"], "autor": "ana"})[0] == 400
    # al eliminar el ticket se borra la imagen
    api("DELETE", "/tickets", token=api.login(), qs={"ticket_id": tid})
    assert boto3.client("s3").list_objects_v2(Bucket="evidencias-test", Prefix=f"evidencias/{tid}/").get("KeyCount", 0) == 0


# ------------------------------------------------------------------------ SLA

def test_estado_sla():
    from models import evaluar_estado_sla
    ahora = datetime.now(timezone.utc)
    t = {"estado": "ABIERTO", "prioridad": "CRITICA", "sla_limite": (ahora + timedelta(hours=2)).isoformat()}
    assert evaluar_estado_sla(t) == "EN_TIEMPO"  # recien creado no aparece "por vencer"
    t["sla_limite"] = (ahora + timedelta(minutes=20)).isoformat()
    assert evaluar_estado_sla(t) == "POR_VENCER"
    t["sla_limite"] = (ahora - timedelta(minutes=1)).isoformat()
    assert evaluar_estado_sla(t) == "VENCIDO"
    t["estado"] = "RESUELTO"
    assert evaluar_estado_sla(t) == "CUMPLIDO"


def test_monitor_sla_notifica_una_sola_vez(api):
    import boto3
    import sla_monitor
    t = api.crear(prioridad="ALTA")
    vencido = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    boto3.resource("dynamodb").Table("ticketflow-tickets").update_item(
        Key={"ticket_id": t["ticket_id"]}, UpdateExpression="SET sla_limite = :v", ExpressionAttributeValues={":v": vencido})
    r1 = sla_monitor.lambda_handler({}, None)
    r2 = sla_monitor.lambda_handler({}, None)
    assert r1["nuevos_notificados"] >= 1 and r1["sns"] is True
    assert r2["nuevos_notificados"] == 0 and r2["vencidos"] >= 1


# ------------------------------------------------------------------ resiliencia

def test_dynamodb_caido_responde_503_controlado(api):
    import repository
    original = repository.tickets_tbl
    repository.tickets_tbl = repository.dynamodb.Table("tabla-que-no-existe")
    try:
        c, data = api("GET", "/tickets")
        assert c == 503 and data["detalle"] == "ResourceNotFoundException"
        assert api("GET", "/health")[0] == 503
    finally:
        repository.tickets_tbl = original
    assert api("GET", "/health")[0] == 200


def test_sns_caido_no_impide_crear_ticket_critico(api):
    import notifier
    from botocore.exceptions import ClientError
    error = ClientError({"Error": {"Code": "InternalError", "Message": "x"}}, "Publish")
    with mock.patch.object(notifier._sns, "publish", side_effect=error):
        t = api.crear(prioridad="CRITICA")
    assert t["alerta_enviada"] is False


def test_auditoria_caida_no_impide_operar(api):
    import repository
    original = repository.audit_tbl
    repository.audit_tbl = repository.dynamodb.Table("auditoria-inexistente")
    try:
        api.crear()  # aun sin bitacora, el ticket se crea
    finally:
        repository.audit_tbl = original


def test_error_inesperado_responde_500_sin_detalles(api):
    import lambda_function
    with mock.patch.object(lambda_function.repo, "listar_tickets", side_effect=RuntimeError("bug")):
        c, data = api("GET", "/tickets")
    assert c == 500 and "bug" not in str(data)


def test_concurrencia_mismo_ticket_un_solo_ganador(api):
    """Dos tecnicos mueven el mismo ticket a la vez: la escritura condicional deja pasar solo a uno."""
    import repository
    t = api.crear()
    repository.cambiar_estado(t["ticket_id"], "EN_PROGRESO", "", "tec1")
    real = repository.obtener_ticket
    with mock.patch.object(repository, "obtener_ticket", side_effect=lambda tid: {**real(tid), "estado": "ABIERTO"}):
        with pytest.raises(repository.Conflicto):
            repository.cambiar_estado(t["ticket_id"], "EN_PROGRESO", "", "tec2")


def test_migracion_de_despliegue_anterior(aws):
    """Un despliegue viejo (auditoria sin indice) se actualiza solo al volver a desplegar."""
    import boto3
    import deploy
    ddb = boto3.client("dynamodb")
    ddb.delete_table(TableName="ticketflow-auditoria")
    ddb.create_table(TableName="ticketflow-auditoria", BillingMode="PAY_PER_REQUEST",
                     KeySchema=[{"AttributeName": "audit_id", "KeyType": "HASH"}],
                     AttributeDefinitions=[{"AttributeName": "audit_id", "AttributeType": "S"}])
    tabla = boto3.resource("dynamodb").Table("ticketflow-auditoria")
    tabla.put_item(Item={"audit_id": "a1", "timestamp": "2026-01-01"})
    deploy.crear_tablas()
    idx = ddb.describe_table(TableName="ticketflow-auditoria")["Table"]["GlobalSecondaryIndexes"]
    assert [g["IndexName"] for g in idx] == ["TimelineIndex"]
    assert tabla.get_item(Key={"audit_id": "a1"})["Item"]["tipo"] == "EVENTO"
