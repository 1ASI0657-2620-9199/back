# FinanZen — APIs, Docker y Kafka

Esta rama incorpora los Sprints 2 y 3: Financial Records,
Budget, Notification y Device Integration, más el frontend en el repositorio
hermano [front](https://github.com/1ASI0657-2620-9199/front).

El flujo funcional es registro financiero → TransactionRecorded → presupuesto →
BudgetThresholdReached → notificación dentro de la aplicación. Las lecturas IoT
se normalizan y almacenan separadas de los movimientos financieros.

## Ejecutar con Docker y Kafka

Con Docker Desktop/Engine y Compose disponibles, desde back/:

~~~powershell
docker compose up -d --build --wait --wait-timeout 240
~~~

Panel: **http://127.0.0.1:8080**. Se ejecutan cuatro APIs independientes, Nginx
y Apache Kafka, con cinco volúmenes separados. [Guía de ejecución, DLQs,
pruebas y recuperación](DOCKER.md). El modo local sin Docker también se conserva
para desarrollo rápido y pruebas.

Para una presentación, `scripts/iniciar_demostracion.cmd` inicia el stack y abre
el navegador. Con el stack activo, `python scripts/preparar_demostracion.py`
crea una cuenta nueva para el recorrido en vivo; añadir `--completa` crea otra
con el escenario terminado como respaldo. Los comandos imprimen la identidad
para elegirla en **Cuenta demo**. El manual PDF y su guion están en
[report/entrega](https://github.com/1ASI0657-2620-9199/report/tree/main/entrega).

## Ejecutar API y panel juntos

Requiere Python 3.11 y tener back/ y front/ dentro de la misma carpeta.
Desde back/, en PowerShell:

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/run_local.py
~~~

- Panel: http://127.0.0.1:8000
- Swagger integrado: http://127.0.0.1:8000/docs
- OpenAPI: http://127.0.0.1:8000/openapi.json
- Estado del despachador: http://127.0.0.1:8000/health

No es necesario activar el entorno virtual ni cambiar la política de ejecución
de PowerShell. Se pueden usar otros puertos con --port 8010. El servidor escucha
solo en 127.0.0.1 para esta etapa local.

La configuración usa variables del shell; .env.example es una referencia y
no se carga automáticamente.

## Aplicaciones y persistencia

| Aplicación ASGI | Base SQLite local | Swagger en la ejecución integrada |
| --- | --- | --- |
| app.main:app | financial_records.db | /services/financial/docs |
| services.budgets.main:app | budgets.db | /services/budgets/docs |
| services.notifications.main:app | notifications.db | /services/notifications/docs |
| services.devices.main:app | telemetry.db | /services/devices/docs |

Los cuatro archivos se crean en data/, o en FINANZEN_DATA_DIR. Cada repositorio
accede exclusivamente a su base. Budget mantiene una proyección propia de los
movimientos que recibe, sin consultar la base de Financial Records. El registro
de dispositivos y sus lecturas pertenecen al Telemetry Store de Device Integration.

Las aplicaciones también pueden arrancar con uvicorn por separado. El
despachador de esta entrega se ejecuta con app.suite:app; iniciar solamente
app.main:app conserva la API financiera pero no procesa los otros servicios.

Para PostgreSQL existen conexiones independientes DATABASE_URL,
BUDGET_DATABASE_URL, NOTIFICATION_DATABASE_URL y TELEMETRY_DATABASE_URL. Cada
una debe señalar una base propia. La validación actual del nuevo incremento es
local con SQLite; la integración PostgreSQL de Financial Records permanece en CI.

## Funciones del incremento

- Presupuestos por categoría, mes y moneda, con edición de límites y umbral.
- Gasto acumulado, disponible, porcentaje y estados dentro/cerca/fuera del plan.
- Recomendación usando ingresos de los tres meses previos, reserva del 20 %
  y distribución según gastos históricos. Sin ingresos previos responde 422.
- Reporte semanal bajo demanda: totales, categorías y sugerencias por reglas.
- Alertas persistentes de presupuesto y consumo, preferencias y marcado de lectura.
- Retry con espera exponencial y estado fallido tras cinco intentos; reintento
  manual de notificaciones desde la API y el panel.
- Dispositivos simulados o Meter V1, conexión, revocación, configuración de tarifa
  y límite de consumo.
- Lecturas por intervalo, normalización Wh/kWh, deduplicación, historial y
  estimación de costo con la tarifa vigente al recibir la lectura.
- Contratos Swagger/OpenAPI por aplicación e integración en un origen común.

## Eventos y límites de esta etapa

La escritura del movimiento y su evento Outbox usa la misma transacción. Budget
confirma proyección, recibo Inbox y nuevos eventos de umbral en una transacción.
Device Integration confirma lectura y evento juntos. Los consumidores detectan
repeticiones y rechazan un identificador de evento reutilizado con otro contenido.

app/runtime.py entrega los eventos en segundo plano cada 400 ms, conservando
eventos pendientes al reiniciar. Hay consistencia eventual: el presupuesto y la
alerta pueden tardar un instante en reflejar un gasto.

La suite local anterior es una composición en un proceso y usa el despachador
local. **Docker Compose ejecuta las aplicaciones en contenedores independientes
y entrega sus eventos mediante Kafka real**, con confirmaciones de publicación,
offsets manuales, consumidores idempotentes y DLQs persistentes. Consultar
[DOCKER.md](DOCKER.md) para distinguir ambas modalidades y sus pruebas.

La entrega de notificaciones actual es in-app. No se afirma envío de correo,
SMS o push. Los reportes semanales se consultan bajo demanda; no hay programador
de envíos automáticos. Las recomendaciones usan reglas explícitas, sin IA.

Solo Device Integration aplica Ports & Adapters, por sus dos formatos externos
reales. Los demás servicios usan capas de API, modelos y repositorio.

X-User-Id es una identidad de demostración, no autenticación. El ingreso HTTP
interno de eventos está deshabilitado salvo que se configure
FINANZEN_INTERNAL_EVENT_KEY y se envíe X-Event-Key. El frontend no usa esa clave.

## Pruebas

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=reports/pytest.xml
.\.venv\Scripts\python.exe -m behave features
.\.venv\Scripts\python.exe scripts/export_openapi.py
~~~

Las tres pruebas PostgreSQL requieren TEST_DATABASE_URL de pruebas y se omiten
si no está configurada. Las pruebas del nuevo flujo incluyen rollback de Outbox,
reentrega concurrente, recuperación de fallas, separación de usuarios y datos,
presupuestos históricos, contratos IoT y recuperación de notificaciones.

Con el servidor local en ejecución, importar
[la colección Postman](postman/FinanZen-Sprints-2-3.postman_collection.json).
Para ejecutar las 24 peticiones y sus 44 aserciones con Newman:

~~~powershell
npm.cmd exec --yes --package=newman -- newman run postman/FinanZen-Sprints-2-3.postman_collection.json --delay-request 150 --reporters cli,json --reporter-json-export reports/newman.json
~~~

En Linux/macOS usar npm en lugar de npm.cmd. En Collection Runner de Postman
establecer 150 ms de demora. La colección crea una identidad sintética nueva y
espera la consistencia eventual antes de comprobar presupuesto y alertas.

El workflow Docker inicia el stack y ejecuta las pruebas de recuperación con
Kafka. Usa la misma rama del repositorio front; FRONTEND_REF permite seleccionar
otra referencia. Las dos ramas de integración deben estar disponibles antes de
ejecutar las validaciones cruzadas. Los resultados remotos se consultan en GitHub Actions.

Las pruebas reales de navegador y sus capturas se ejecutan desde front/; consultar
su README. Los resultados se guardan bajo reports/, fuera de Git.

## Recuperación de eventos locales

Un evento Outbox que falla cinco veces conserva su cuerpo y pasa a estado
dead_letter. /health muestra los contadores. Después de corregir el consumidor:

~~~powershell
.\.venv\Scripts\python.exe scripts/replay_outbox.py --service financial --event-id "transaction:ID-DEL-MOVIMIENTO"
~~~

El script solo modifica ese evento en una base local existente y conserva su ID.
Los consumidores idempotentes permiten reentregar un evento ya procesado sin
duplicar efectos. Las notificaciones fallidas tienen su propio endpoint
POST /api/v1/notifications/{id}/retry.

## Compatibilidad y alcance

Se preservan rutas, encabezados y hashes de idempotencia del Sprint 1. Al arrancar,
los movimientos históricos sin evento reciben un Outbox de identificador estable.
Las fechas financieras y de consumo se agrupan en UTC; una fecha financiera
antigua sin zona horaria se interpreta como UTC.

La documentación de [deployment del Sprint 1](DEPLOYMENT.md) corresponde a la
versión pública anterior. El incremento de main se valida completo con Docker
y Kafka; no se presenta el servicio financiero aislado de Render como un
despliegue de los cuatro servicios. El informe, Trello y las contribuciones del
equipo requieren sus propias evidencias.
