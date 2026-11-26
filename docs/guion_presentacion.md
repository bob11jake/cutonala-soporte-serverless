# Guion de la explicación técnica (3-5 minutos)

Repartición sugerida para 3 integrantes. Los tiempos son aproximados.

| Diapositiva | Quién | Tiempo | Qué decir |
|---|---|---|---|
| 1. Portada | Integrante 1 | 10 s | Nombre del proyecto y del equipo. |
| 2. Problema y solución | Integrante 1 | 30 s | Hoy las fallas del campus se reportan por mensajes, sin registro ni tiempos. Creamos una aplicación web: la comunidad reporta con su nombre y una foto, el personal atiende con tiempos de respuesta según la prioridad, de 2 a 72 horas. |
| 3. Arquitectura | Integrante 1 | 40 s | Recorrer el diagrama de izquierda a derecha: el navegador carga el sitio desde S3, llama a la API en Lambda, que guarda en DynamoDB; SNS manda correos; EventBridge revisa los SLA cada 15 minutos; CloudWatch vigila todo. Todo lo crea un solo script de Python. |
| 4. Flujo de datos | Integrante 2 | 45 s | Contar el caso del corte de luz crítico, paso por paso (1 a 6). Recalcar: la foto no pasa por la Lambda, va directo a S3 con una URL firmada; el ticket y el contador se guardan en una sola transacción. |
| 5. Función de cada servicio | Integrante 2 | 30 s | Una frase por servicio. |
| 6. Diseño de datos | Integrante 2 | 30 s | Por qué no usamos Scan: índices por estado y categoría y contadores precalculados; escrituras condicionales para cuando dos técnicos trabajan el mismo ticket. |
| 7. Infraestructura como código | Integrante 3 | 20 s | Un comando crea todo, es idempotente, y otro lo borra; no usamos la consola. |
| 8. Pruebas de resistencia | Integrante 3 | 40 s | El script rompe componentes reales: deja la Lambda sin capacidad, desconecta DynamoDB, tumba SNS, borra el index.html. Decir qué responde el sistema y cuánto tardó en recuperarse (usar los números de docs/resultados_resiliencia.md). |
| 9. Costos | Integrante 3 | 25 s | ≈ 8.40 USD al mes / 101 al año sin capa gratuita, contra ≈ 645 con servidores. Lo más caro es el dashboard. |
| 10. Por qué estos servicios | Integrante 3 | 20 s | Dos ventajas y dos desventajas. |
| 11. Conclusiones | Integrante 1 | 15 s | Las tres conclusiones y abrir a preguntas. |

## Preguntas probables

- **¿Por qué no API Gateway?** La Function URL da HTTPS y CORS gratis; API Gateway agregaría límites por cliente y WAF, que serían el siguiente paso si hubiera abuso.
- **¿Por qué no RDS?** Cuesta aunque nadie lo use y requiere VPC; nuestras consultas son por estado o categoría, que DynamoDB resuelve con índices.
- **¿Qué pasa si se cae toda la región?** No está cubierto; requeriría DynamoDB Global Tables y una segunda región (duplica el costo).
- **¿Por qué no Cognito?** Está restringido en Learner Lab; implementamos hash PBKDF2, tokens firmados y bloqueo por fuerza bruta. Cognito es la mejora para producción.
- **¿Cómo evitan avisos duplicados de SLA?** Escritura condicional: el ticket se marca como notificado solo si no lo estaba.
- **¿Qué es la concurrencia reservada en 0?** Un límite de instancias simultáneas; en 0 la Lambda rechaza todas las peticiones, que es como simular que el servicio no está disponible.
