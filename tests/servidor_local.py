"""Ejecuta toda la aplicacion en tu computadora SIN cuenta de AWS.

Levanta un AWS simulado (moto) en http://localhost:5001, crea las mismas tablas,
bucket y topic que infra/deploy.py, y sirve el frontend + la API en
http://localhost:8000. Util para desarrollar o practicar la demo sin gastar
creditos del laboratorio.

Uso:  pip install -r requirements-dev.txt
      python tests/servidor_local.py
"""
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
PUERTO_WEB = int(os.environ.get("PUERTO", "8000"))
PUERTO_AWS = 5001

os.environ.update({
    "AWS_ACCESS_KEY_ID": "local", "AWS_SECRET_ACCESS_KEY": "local", "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1", "AWS_ENDPOINT_URL": f"http://localhost:{PUERTO_AWS}",
    "JWT_SECRET": "secreto-local", "EVIDENCE_BUCKET": "evidencias-local",
    "S3_ADDRESSING": "path",
})
for var in ("AWS_PROFILE", "AWS_SESSION_TOKEN", "AWS_CA_BUNDLE"):
    os.environ.pop(var, None)
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "infra")]


def preparar_aws():
    from moto.server import ThreadedMotoServer
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    ThreadedMotoServer(port=PUERTO_AWS, verbose=False).start()
    import boto3
    os.environ["ALERTS_TOPIC_ARN"] = boto3.client("sns").create_topic(Name="ticketflow-alertas")["TopicArn"]
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="evidencias-local")
    s3.put_bucket_cors(Bucket="evidencias-local", CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["*"], "AllowedMethods": ["GET", "POST"], "AllowedHeaders": ["*"]}]})
    import deploy
    deploy.crear_tablas()
    deploy.crear_usuarios_y_contadores()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "frontend"), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def _api(self):
        import lambda_function
        url = urlparse(self.path)
        largo = int(self.headers.get("Content-Length") or 0)
        evento = {
            "requestContext": {"http": {"method": self.command}},
            "rawPath": url.path[len("/api"):] or "/",
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "queryStringParameters": {k: v[0] for k, v in parse_qs(url.query).items()} or None,
            "body": self.rfile.read(largo).decode("utf-8") if largo else None,
        }
        res = lambda_function.lambda_handler(evento, None)
        cuerpo = res["body"].encode("utf-8")
        self.send_response(res["statusCode"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _es_api(self):
        ruta = urlparse(self.path).path
        return ruta == "/api" or ruta.startswith("/api/")

    def do_GET(self):
        if self._es_api():
            return self._api()
        if self.path.startswith("/config.js"):
            cuerpo = f'const API_URL = "http://localhost:{PUERTO_WEB}/api";\n'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.end_headers()
            return self.wfile.write(cuerpo)
        return super().do_GET()

    do_POST = do_PUT = do_DELETE = _api


if __name__ == "__main__":
    preparar_aws()
    print(f"CUTonala Soporte en modo local: http://localhost:{PUERTO_WEB}")
    print("Personal: admin@ticketflow.edu / Admin123!  |  soporte@ticketflow.edu / Soporte123!")
    print("Ctrl+C para detener.")
    ThreadingHTTPServer(("localhost", PUERTO_WEB), Handler).serve_forever()
