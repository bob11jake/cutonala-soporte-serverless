"""Evidencias fotograficas en un bucket S3 privado mediante URLs prefirmadas.

El navegador sube la imagen directo a S3 (la Lambda nunca recibe el archivo),
asi el tamano de la peticion no esta limitado por Lambda y no se paga computo
por la transferencia.
"""
import os
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from models import MAX_EVIDENCIA_BYTES

EVIDENCE_BUCKET = os.environ.get("EVIDENCE_BUCKET", "")
URL_EXPIRA_SEG = 900

_s3 = boto3.client(
    "s3",
    # "virtual" (bucket.s3.amazonaws.com) en AWS; el modo local usa "path".
    config=Config(signature_version="s3v4", retries={"max_attempts": 3},
                  s3={"addressing_style": os.environ.get("S3_ADDRESSING", "virtual")}),
)

EXTENSIONES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def habilitado():
    return bool(EVIDENCE_BUCKET)


def generar_subida(ticket_id, content_type):
    key = f"evidencias/{ticket_id}/{uuid.uuid4().hex[:12]}.{EXTENSIONES[content_type]}"
    post = _s3.generate_presigned_post(
        Bucket=EVIDENCE_BUCKET,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, MAX_EVIDENCIA_BYTES],
        ],
        ExpiresIn=URL_EXPIRA_SEG,
    )
    return {"key": key, "url": post["url"], "fields": post["fields"]}


def existe(key):
    try:
        meta = _s3.head_object(Bucket=EVIDENCE_BUCKET, Key=key)
        return meta["ContentLength"]
    except ClientError:
        return None


def url_lectura(key):
    return _s3.generate_presigned_url(
        "get_object", Params={"Bucket": EVIDENCE_BUCKET, "Key": key}, ExpiresIn=3600
    )


def eliminar_de_ticket(ticket):
    keys = [e["key"] for e in ticket.get("evidencias", [])]
    if not keys or not habilitado():
        return
    try:
        _s3.delete_objects(Bucket=EVIDENCE_BUCKET, Delete={"Objects": [{"Key": k} for k in keys]})
    except ClientError:
        pass  # la regla de ciclo de vida del bucket limpia lo que quede
