# Resultados de las pruebas de resiliencia

Ejecutadas el 2026-11-25 contra AWS (us-east-1) con `python tests/chaos.py`.

| # | Componente | Falla inyectada | Comportamiento esperado | Observado | Recuperacion | Resultado |
|---|---|---|---|---|---|---|
| 1 | Lambda + DynamoDB | 200 peticiones simultaneas | 0 errores 5xx; Lambda escala sola | {200: 200}, p50 448 ms, p95 550 ms, max 930 ms | N/A | SUPERADA |
| 2 | AWS Lambda | Concurrencia en 0 (throttling total) | Error controlado 429/503, sin perdida de datos | HTTP 429 | 0.3 s | SUPERADA |
| 3 | Amazon DynamoDB | Tabla inaccesible | HTTP 503 con JSON claro (sin stack trace); /health 503 | GET 503, POST 503, health 503 | 1.2 s | SUPERADA |
| 4 | Amazon SNS | Topic de alertas inexistente | El ticket se crea (201); solo se pierde la notificacion | HTTP 201, alerta_enviada=False | Inmediata | SUPERADA |
| 5 | Amazon S3 (sitio web) | Borrado accidental de index.html | Se recupera la version previa sin redeploy | antes 200, durante 404, despues 200 | 0.5 s | SUPERADA |
| 6 | API (validacion y auth) | Entradas invalidas, token falso, fuerza bruta | 4xx en todos los casos, nunca 5xx; bloqueo tras 5 fallos | 400, 400, 400, 401, 403, 400, 429 | N/A | SUPERADA |
| 7 | Amazon DynamoDB (datos) | Borrado o corrupcion de tickets | Restaurar a cualquier segundo de los ultimos 35 dias | PITR ENABLED | No ejecutada (usa --restaurar) | SUPERADA |
