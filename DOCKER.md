# Ejecutar FinanZen con Docker y Kafka

Desde back/, con Docker Desktop/Engine y Compose disponibles, y front/ como
repositorio hermano:

~~~powershell
docker compose up -d --build --wait --wait-timeout 240
~~~

Panel: **http://127.0.0.1:8080**. No se necesita Python o Node en el equipo para
arrancar los contenedores. Se construyen las imágenes desde el código local.

## Servicios y datos

| Servicio | Responsabilidad | Persistencia propia |
| --- | --- | --- |
| web | Nginx, panel y enrutamiento HTTP | Imagen con HTML/CSS/JS |
| financial-records | Movimientos e idempotencia HTTP | financial-data |
| budget-service | Presupuestos, proyección financiera y reportes | budget-data |
| notification-service | Bandeja, preferencias y entrega in-app | notification-data |
| device-service | Registro, adaptadores y telemetría | telemetry-data |
| kafka | Event Bus Apache Kafka 4.1.2 en KRaft | kafka-data |
| kafka-permissions | Inicialización del propietario del volumen de Kafka | Termina con código 0 |
| kafka-init | Creación idempotente de topics y DLQs | Termina con código 0 |

Las cuatro APIs se ejecutan en procesos/contenedores independientes. No usan
app.suite ni el despachador local. Cada una ve únicamente su volumen /data y
consume eventos por Kafka; no consulta las bases de otros servicios. Los dos
contenedores de inicialización deben quedar Exited (0); es su estado esperado.

~~~mermaid
flowchart LR
    U[Panel] --> G[Nginx]
    G --> F[Financial Records]
    G --> B[Budget]
    G --> N[Notification]
    G --> D[Device Integration]
    F --> FO[(Financial DB + Outbox)]
    FO --> K[Apache Kafka]
    K --> B
    B --> BO[(Budget DB + Inbox / Outbox)]
    BO --> K
    K --> N
    N --> NO[(Notification DB + Inbox / Outbox)]
    NO --> K
    D --> DO[(Telemetry DB + Outbox)]
    DO --> K
    K --> Q[DLQs]
~~~

Los volúmenes SQLite conservan datos al reiniciar o recrear contenedores.
docker compose stop detiene el stack y docker compose start lo reanuda.
docker compose down elimina contenedores y red, conservando volúmenes.
La configuración no elimina ni reinicializa bases existentes.

## Swagger, estado y eventos

| Servicio | Swagger | Estado de Outbox y trabajadores |
| --- | --- | --- |
| Financial | /services/financial/docs | /services/financial/operations/events |
| Budget | /services/budgets/docs | /services/budgets/operations/events |
| Notification | /services/notifications/docs | /services/notifications/operations/events |
| Devices | /services/devices/docs | /services/devices/operations/events |

Las rutas son relativas a http://127.0.0.1:8080. Cada servicio tiene /health
y /openapi.json bajo el mismo prefijo. /docs redirige al Swagger financiero.
El panel usa /api/v1/ y Nginx dirige cada operación al servicio correspondiente.
Nginx vuelve a resolver los nombres internos cuando se recrean contenedores.

El puerto Kafka externo es 127.0.0.1:9092; dentro de Docker se usa kafka:19092.
WEB_PORT y KAFKA_PORT permiten cambiar esos puertos mediante .env de Compose
o variables del shell. FRONTEND_CONTEXT permite cambiar la ruta de front/.
Las APIs no publican sus puertos al host.

## Publicación, confirmaciones y recuperación

1. Financial Records guarda movimiento y TransactionRecorded en la misma
   transacción SQL. Device Integration hace lo mismo con lectura y
   ConsumptionReadingNormalized.
2. El trabajador de cada servicio publica su propio Outbox. Solo después de
   recibir el callback de entrega confirmada de Kafka lo marca como entregado.
3. Budget confirma Inbox, proyección y eventos BudgetThresholdReached en una
   transacción. Después confirma el offset en Kafka.
4. Notification confirma Inbox y notificación persistida antes de confirmar
   el offset; su trabajador de entrega procesa la bandeja por separado.
5. Una entrega confirmada emite NotificationDelivered. Cinco intentos fallidos
   de entrega emiten NotificationFailed a finanzen.notification-delivery.dlq.
6. Los fallos de conexión al broker conservan los eventos en Outbox y se
   reintentan sin descartarlos, con una espera máxima de 30 segundos.
7. Un mensaje que el consumidor no puede procesar se reintenta cinco veces.
   El contador se conserva en su base. Se publica en la DLQ y se espera su
   confirmación antes de avanzar el offset original.

La semántica es **al menos una entrega**. Si un proceso cae entre guardar y
confirmar, puede recibir otra vez el evento: el Inbox y las restricciones de
unicidad evitan repetir sus efectos. No se afirma una transacción distribuida
exactly-once entre Kafka y SQL.

## Topics

| Topic | Publicador | Consumidor |
| --- | --- | --- |
| finanzen.transactions.v1 | Financial Outbox | Grupo finanzen-budgets-v1 |
| finanzen.budget-thresholds.v1 | Budget Outbox | Grupo finanzen-notifications-v1 |
| finanzen.telemetry.v1 | Device Outbox | Grupo finanzen-notifications-v1 |
| finanzen.notification-results.v1 | Notification Outbox | Auditoría/verificación |
| finanzen.notification-delivery.dlq | Notification Outbox | Recuperación de entregas |
| finanzen.transactions.v1.budgets.dlq | Budget consumer | Operador |
| finanzen.budget-thresholds.v1.notifications.dlq | Notification consumer | Operador |
| finanzen.telemetry.v1.notifications.dlq | Notification consumer | Operador |

Se crean tres particiones por topic y una réplica para el broker único de
esta demostración. Los eventos se particionan por usuario. La retención de
Kafka es de siete días; las bases de cada servicio se conservan en sus volúmenes.

## Verificar y obtener evidencia real

~~~powershell
docker compose ps -a
docker compose logs --tail 100 financial-records budget-service notification-service device-service
docker compose exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:19092 --list
docker compose exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:19092 --all-groups --describe
docker compose --profile test run --build --rm tests
~~~

La imagen de pruebas ejecuta el código en Linux. Las tres pruebas PostgreSQL
requieren una TEST_DATABASE_URL de pruebas; este stack usa bases SQLite separadas.

Para pruebas del protocolo Kafka y fallos controlados desde el host, instalar las
dependencias en un entorno virtual y ejecutar:

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-kafka.txt
.\.venv\Scripts\python.exe scripts/verify_docker.py --faults
~~~

El verificador crea una identidad sintética y comprueba el flujo financiero,
reenvío HTTP y Kafka, IoT, DLQ, caída del broker, caída de Notification Service,
persistencia tras reinicios y recuperación de entrega. --faults interrumpe
brevemente solo estos contenedores y restaura el funcionamiento al finalizar.
Sin --faults verifica el flujo y la DLQ sin detener servicios.

Para simular el fallo de entrega utiliza
FINANZEN_NOTIFICATION_SIMULATE_FAILURE=1 solamente en el contenedor Notification.
Tras probar cinco intentos y NotificationFailed, restaura 0, reinicia ese
contenedor y solicita el reintento de la misma notificación mediante su API.
No modifica una configuración global del sistema.

La evidencia se guarda en reports/docker/verification.json con resultados,
posiciones Kafka, cuerpos sintéticos e inventario de contenedores. La colección
Postman admite tanto Docker como la suite local:

~~~powershell
npm.cmd exec --yes --package=newman -- newman run postman/FinanZen-Sprints-2-3.postman_collection.json --env-var baseUrl=http://127.0.0.1:8080 --delay-request 150 --reporters cli,json --reporter-json-export reports/newman-kafka.json
~~~

Para el navegador, desde front/:

~~~powershell
..\back\.venv\Scripts\python.exe scripts/test_browser.py --base-url http://127.0.0.1:8080
~~~

Los resultados y capturas bajo reports/ son evidencia de ejecución local; no se
incorporan automáticamente al informe o al tablero del equipo.

## Reprocesar un mensaje de la DLQ

Después de corregir el consumidor o el contrato, seleccionar la posición exacta
de una DLQ de consumidor. El script publica otra vez en su topic original y
conserva event_id:

~~~powershell
.\.venv\Scripts\python.exe scripts/replay_kafka_dlq.py --topic finanzen.transactions.v1.budgets.dlq --partition 0 --offset 0
~~~

Para un cuerpo inválido se puede pasar --corrected-event ruta/evento.json. El
archivo debe incluir el mismo event_id del mensaje original. La posición de la
DLQ no se elimina: queda como auditoría. El comando falla si esa posición ya no
está disponible y no sustituye silenciosamente otro mensaje.

Las notificaciones de finanzen.notification-delivery.dlq se recuperan con
POST /api/v1/notifications/{id}/retry o el botón Reintentar del panel, después de
restablecer el proveedor. No se reinsertan como nuevos movimientos financieros.

## Alcance de esta entrega

Kafka es un broker real, con datos y offsets persistentes, integrado con los cuatro
servicios. Es una ejecución distribuida local de un broker y una instancia por
servicio. Los volúmenes SQLite y la configuración de replicación 1 están orientados
a esta demostración, sin alta disponibilidad o escalado horizontal.

X-User-Id continúa como identidad demo. Las notificaciones son in-app; no se
declaran correo, SMS o push. El tráfico del stack es local y Kafka usa PLAINTEXT.
El código de main incluye este incremento y la ejecución completa verificada es
Docker local. El servicio financiero aislado de Render no constituye un
despliegue del stack distribuido.

Referencias de implementación: [Kafka Docker](https://kafka.apache.org/41/getting-started/docker/),
[cliente Python](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html),
[orden de arranque de Compose](https://docs.docker.com/compose/how-tos/startup-order/).
