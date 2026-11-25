# CUTonala Soporte Técnico — Mesa de servicios serverless en AWS

Aplicación **web** para que la comunidad del Centro Universitario de Tonalá reporte incidencias del campus
(electricidad, agua, seguridad, infraestructura y red) y para que el personal técnico las atienda con
tiempos de respuesta (SLA), evidencias fotográficas, alertas y bitácora de auditoría.

Toda la infraestructura se crea con **un script de Python (boto3)**; no se usa la consola web de AWS.

## Arquitectura

```
Navegador ──HTTP──> Amazon S3 (sitio estático, versionado)
    │
    ├──HTTPS──> Lambda Function URL ──> AWS Lambda "ticketflow-api" ──> Amazon DynamoDB
    │                                        │                          (tickets, auditoría, usuarios)
    │                                        ├──> Amazon SNS (alertas por correo)
    │                                        └──> S3 evidencias (URLs prefirmadas)
    └──HTTPS (POST prefirmado)──> Amazon S3 evidencias (bucket privado)

Amazon EventBridge (cada 15 min) ──> AWS Lambda "ticketflow-sla-monitor" ──> DynamoDB + SNS
Amazon CloudWatch: logs (14 días), 4 alarmas → SNS, dashboard y métrica TicketsSLAVencidos
```

| Servicio | Uso |
|---|---|
| Amazon S3 | Sitio web estático (con versionado) y bucket privado de evidencias con expiración a 365 días |
| AWS Lambda | API REST (`ticketflow-api`) y monitor programado de SLA (`ticketflow-sla-monitor`) |
| Amazon DynamoDB | Tablas `tickets` (2 GSI + contadores transaccionales + PITR), `auditoria` (GSI + TTL) y `usuarios` |
| Amazon SNS | Alertas de tickets CRÍTICOS, SLA vencidos y alarmas de CloudWatch |
| Amazon EventBridge | Ejecuta la revisión de SLA cada 15 minutos |
| Amazon CloudWatch | Logs estructurados, alarmas, dashboard y métrica personalizada |

La documentación completa (guía de despliegue, diagrama, justificación, costos y pruebas de resiliencia)
está en [`docs/Documentacion_CUTonala_Soporte.pdf`](docs/Documentacion_CUTonala_Soporte.pdf).

## Requisitos

- Python 3.10 o superior y Git.
- Cuenta de **AWS Academy Learner Lab** (usa el rol existente `LabRole`) o cualquier cuenta de AWS con un rol
  para Lambda (indícalo con la variable `TICKETFLOW_ROLE`).

## Despliegue paso a paso

**1. Clonar el repositorio**

```bash
git clone https://github.com/bob11jake/cutonala-soporte-serverless.git
cd cutonala-soporte-serverless
```

**2. Crear el entorno virtual e instalar dependencias**

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> Si PowerShell bloquea la activación: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
> En PyCharm: *Settings → Project → Python Interpreter → Add Interpreter → Existing* y elige `.venv`.

**3. Configurar credenciales de AWS Academy**

Inicia el laboratorio (círculo verde), abre **AWS Details → AWS CLI: Show** y copia el bloque completo
(`[default]` y las tres claves) en el archivo de credenciales:

- macOS / Linux: `~/.aws/credentials` (`mkdir -p ~/.aws && open -e ~/.aws/credentials`)
- Windows: `%USERPROFILE%\.aws\credentials` (`notepad $HOME\.aws\credentials`)

Las credenciales caducan cuando termina la sesión del laboratorio; si aparece `ExpiredToken`, cópialas otra vez.
**No hay que editar el Account ID**: el script lo obtiene automáticamente.

**4. Desplegar**

```bash
python infra/deploy.py --email tu_correo@ejemplo.com
```

El correo es opcional; recibirás un mensaje de AWS para **confirmar la suscripción** a las alertas.
Al terminar se imprime la URL de la aplicación. El script es idempotente: puedes volver a ejecutarlo para
actualizar el código.

**5. Cargar datos de demostración y verificar**

```bash
python tests/seed_and_resilience.py     # borra los tickets actuales y carga 8 de ejemplo
python infra/status.py                  # estado de todos los recursos y prueba de /health
```

Usuarios del personal: `admin@ticketflow.edu / Admin123!` y `soporte@ticketflow.edu / Soporte123!`.
La comunidad entra solo con su nombre.

**6. Pruebas de resiliencia (fallas reales en AWS)**

```bash
python tests/chaos.py                   # 7 escenarios, ~4 minutos; genera docs/resultados_resiliencia.md
python tests/chaos.py --restaurar       # incluye una restauración PITR real (15-30 min extra)
```

**7. Eliminar todo al terminar**

```bash
python infra/destroy.py
```

## Desarrollo sin AWS

```bash
pip install -r requirements-dev.txt
python -m pytest -q                     # 22 pruebas del backend con AWS simulado (moto)
python tests/servidor_local.py          # app completa en http://localhost:8000 sin cuenta de AWS
```

## Problemas comunes

| Síntoma | Solución |
|---|---|
| `zsh: command not found: pip` | Activa el entorno: `source .venv/bin/activate` |
| `ExpiredToken` / `InvalidClientTokenId` | La sesión del laboratorio terminó: copia de nuevo las credenciales |
| `CERTIFICATE_VERIFY_FAILED` (macOS) | `pip install -r requirements.txt` (instala `certifi`) o ejecuta `Install Certificates.command` de tu Python |
| La página muestra "Sin Conexión" | Ejecuta `python infra/status.py`; si la API no responde, vuelve a correr `deploy.py` |

## Estructura

```
backend/     Lambda: lambda_function.py (API), sla_monitor.py, auth.py, repository.py, storage.py, notifier.py, models.py
frontend/    index.html, app.js, api.js, charts.js, styles.css  (config.js se genera al desplegar)
infra/       deploy.py, destroy.py, status.py, config.py
tests/       test_backend.py (pytest), chaos.py, seed_and_resilience.py, servidor_local.py
docs/        documentación PDF, presentación y fuentes
```
