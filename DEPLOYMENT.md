# FinanZen — Deployment gratuito con datos persistentes

Configuración preparada el **10 de octubre de 2026** para **Render Free + Neon Free**.
Estado: **publicación pendiente de acceso a las herramientas cloud; todavía no hay URL pública verificada**.
El usuario informó que conectó Render y Neon; el catálogo confirma ambos plugins
instalados. Esta sesión no dispone de sus herramientas para crear o inspeccionar
recursos. Continuar desde un chat nuevo compatible con esas integraciones.

## Recursos preparados

| Recurso | Configuración |
| --- | --- |
| Código | `1ASI0657-2620-9199/back`, rama `main` |
| Runtime | Python 3.11.9, FastAPI, Uvicorn |
| Servicio API | Render Web Service, plan `free`, una instancia, Virginia |
| Persistencia cloud | PostgreSQL en Neon Free, preferentemente región cercana a la API |
| Build | `python -m pip install -r requirements.txt` |
| Start | `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `/health` |
| Variable privada | `DATABASE_URL`, introducida durante la creación del Blueprint |
| Actualizaciones | `autoDeployTrigger: checksPass` |

El [Blueprint](render.yaml) crea únicamente el servicio gratuito. No define
un disco de pago ni una base Render Postgres que venza a los 30 días.
El esquema se inicializa en PostgreSQL al arrancar. Los movimientos quedan
fuera del filesystem efímero de Render, por lo que reiniciar la API no los elimina.

El backend admite dos modos:

- Sin `DATABASE_URL`: SQLite local, configurable mediante `FINANZEN_DB_PATH`.
- Con `DATABASE_URL`: PostgreSQL, conectado mediante pg8000 y TLS verificado.

En Render, la aplicación rechaza arrancar sin `DATABASE_URL` para evitar el uso
accidental de SQLite efímero. Solo la validación en localhost permite
`sslmode=disable`; las conexiones externas utilizan TLS.
Swagger (`/docs`) y OpenAPI (`/openapi.json`) funcionan sin frontend.
`X-User-Id` sigue siendo una identidad de demostración, no un login.

## Planes y límites gratuitos

Se utilizarán los planes **Free**, sin crear servicios de pago. Según los
límites consultados el 10 de octubre de 2026, Neon incluye 1 GB de almacenamiento
y 100 CU-horas al mes por proyecto. Render Free puede dormir tras 15 minutos
sin tráfico; las primeras solicitudes pueden tardar mientras se reactiva.
El almacenamiento de Neon es independiente de esos reinicios de Render.
La gratuidad está sujeta a las cuotas y condiciones de cada proveedor.
La opción de Render con disco se descartó por requerir pago.

## Publicación

La creación e integración desde el agente requieren las cuentas conectadas:

1. Crear un proyecto Neon Free y obtener su connection string PostgreSQL.
2. Crear un Blueprint de Render desde `back`, verificando `plan: free`.
3. Introducir la conexión en `DATABASE_URL` como variable privada; no incluirla
   en commits, capturas o archivos del reporte.
4. Esperar que build/deploy terminen y el servicio esté Live.
5. Registrar la URL HTTPS real, ID de deploy y commit publicado.
6. Verificar `/health`, `/docs`, `/openapi.json` y la colección Postman en esa URL.
7. Registrar un movimiento, reiniciar/redeployar el servicio y consultar el mismo
   usuario para demostrar persistencia.

No se inventa una URL mientras el servicio no existe. Las pruebas PostgreSQL
locales no demuestran que las cuentas cloud estén configuradas. Las bases SQLite
originales se conservan localmente; no se publican ni se importan automáticamente a Neon.

## Validación

Cinco pruebas SQLite y tres pruebas de integración PostgreSQL comprueban
contratos, idempotencia, aislamiento y persistencia al recrear la aplicación.
La integración incluye ocho POST concurrentes que deben producir un único
movimiento. El workflow utiliza PostgreSQL 17 real.
Para ejecutar la integración, configurar `TEST_DATABASE_URL` hacia una base
PostgreSQL de pruebas y ejecutar `python -m pytest -q`. Esta variable no
selecciona la base del servidor; `DATABASE_URL` sí lo hace.

## Referencias

- [FastAPI en Render](https://render.com/docs/deploy-fastapi).
- [Límites de Render Free](https://render.com/docs/free).
- [Blueprint y validación](https://render.com/docs/blueprint-spec).
- [Neon Free](https://neon.com/blog/neon-free-plan-1-gb-per-project).
- [Driver pg8000](https://pypi.org/project/pg8000/).
