"""Utilidades compartidas por los scripts que hablan con la API desplegada."""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "infra"))
from config import contexto_ssl, leer_estado  # noqa: E402

_SSL = contexto_ssl()


def cargar_estado():
    estado = leer_estado()
    if not estado.get("api_url"):
        sys.exit("No existe infra/state.json. Ejecuta primero: python infra/deploy.py")
    return estado


def req(url, metodo="GET", payload=None, token=None, raw=None, timeout=15):
    """Hace una peticion HTTP. Devuelve (status, cuerpo_json_o_texto, milisegundos)."""
    data = raw if raw is not None else (json.dumps(payload).encode("utf-8") if payload is not None else None)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(url, data=data, headers=headers, method=metodo)
    inicio = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout, context=_SSL if url.startswith("https") else None) as resp:
            cuerpo, status = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        cuerpo, status = e.read(), e.code
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, str(e), round((time.time() - inicio) * 1000)
    ms = round((time.time() - inicio) * 1000)
    try:
        return status, json.loads(cuerpo.decode("utf-8")), ms
    except ValueError:
        return status, cuerpo.decode("utf-8", "replace")[:200], ms
