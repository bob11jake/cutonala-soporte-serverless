"""Fixtures de pytest: AWS simulado con moto (no requiere cuenta ni credenciales)."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.update({
    "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1", "JWT_SECRET": "secreto-de-pruebas",
    "EVIDENCE_BUCKET": "evidencias-test",
})
os.environ.pop("AWS_PROFILE", None)
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "infra")]


@pytest.fixture(scope="session")
def aws():
    from moto import mock_aws
    with mock_aws():
        import boto3
        topic = boto3.client("sns").create_topic(Name="alertas-test")["TopicArn"]
        os.environ["ALERTS_TOPIC_ARN"] = topic
        boto3.client("s3").create_bucket(Bucket="evidencias-test")
        import deploy
        deploy.crear_tablas()
        deploy.crear_usuarios_y_contadores()
        yield


@pytest.fixture(scope="session")
def api(aws):
    import lambda_function

    class Cliente:
        def __call__(self, metodo, ruta="/", body=None, token=None, qs=None, raw=None):
            headers = {"authorization": f"Bearer {token}"} if token else {}
            evento = {
                "requestContext": {"http": {"method": metodo}}, "rawPath": ruta, "headers": headers,
                "body": raw if raw is not None else (json.dumps(body) if body is not None else None),
                "queryStringParameters": qs,
            }
            r = lambda_function.lambda_handler(evento, None)
            return r["statusCode"], json.loads(r["body"])

        def login(self, email="admin@ticketflow.edu", password="Admin123!"):
            return self("POST", "/auth/login", {"email": email, "password": password})[1]["token"]

        def crear(self, **kw):
            body = {"solicitante": "ana", "titulo": "Fuga en bano", "descripcion": "Gotea el lavabo",
                    "categoria": "AGUA", "prioridad": "MEDIA", **kw}
            c, data = self("POST", "/tickets", body)
            assert c == 201, data
            return data["ticket"]

    return Cliente()
