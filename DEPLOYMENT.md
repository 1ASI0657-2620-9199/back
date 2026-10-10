# FinanZen — Deployment gratuito con datos persistentes

Publicado y verificado el **10 de octubre de 2026** con **Render Free + Neon Free**.
Estado: **Live**, con datos PostgreSQL conservados después de reiniciar Render.

- API: https://finanzen-financial-records.onrender.com
- Swagger: https://finanzen-financial-records.onrender.com/docs
- Health: https://finanzen-financial-records.onrender.com/health
- OpenAPI: https://finanzen-financial-records.onrender.com/openapi.json
- [Capturas, grabación y registros reales](https://github.com/1ASI0657-2620-9199/report/blob/main/evidencias/deployment/publico/README.md).

Servicio Render: `srv-db4vl25ckfvc738e4hp0`.
Deploy inicial: `dep-db4vl2tckfvc738e4j60`, commit `525ac3f`, estado `live`.
Neon: proyecto `dawn-bird-07958545`, PostgreSQL 17 en `aws-us-east-1`.

## Recursos desplegados

| Recurso | Configuración |
| --- | --- |
| Código | `1ASI0657-2620-9199/back`, rama `main` |
| Runtime | Python 3.11.9, FastAPI, Uvicorn |
| Servicio API | Render Web Service, plan `free`, una instancia, Virginia |
| Persistencia cloud | PostgreSQL 17 en Neon Free, AWS US East 1, compute 0.25 CU |
| Build | `python -m pip install -r requirements.txt` |
| Start | `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `/health` |
| Variable privada | `DATABASE_URL`, configurada durante la creación por la API de Render |
| Actualizaciones | `autoDeployTrigger: checksPass` |

El servicio se creó por la API oficial de Render aplicando la configuración del
[Blueprint](render.yaml), que define únicamente el servicio gratuito. No define
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

Se verificaron y utilizaron los planes **Free**, sin crear servicios de pago. Según los
límites consultados el 10 de octubre de 2026, Neon incluye 1 GB de almacenamiento
y 100 CU-horas al mes por proyecto. Render Free puede dormir tras 15 minutos
sin tráfico; las primeras solicitudes pueden tardar mientras se reactiva.
El almacenamiento de Neon es independiente de esos reinicios de Render.
La gratuidad está sujeta a las cuotas y condiciones de cada proveedor.

## Reproducir la publicación

La publicación realizada utilizó Neon CLI con autorización del usuario y la API
oficial de Render. Para reproducirla en otra cuenta:

1. Crear un proyecto Neon Free y obtener su connection string PostgreSQL.
2. Crear Render Web Service desde `back/main` con la configuración del Blueprint,
   verificando `plan: free`; usar el Dashboard, API o una integración disponible.
3. Introducir la conexión en `DATABASE_URL` como variable privada; no incluirla
   en commits, capturas o archivos del reporte.
4. Esperar que build/deploy terminen y el servicio esté Live.
5. Registrar la URL HTTPS real, ID de deploy y commit publicado.
6. Verificar `/health`, `/docs`, `/openapi.json` y la colección Postman en esa URL.
7. Registrar un movimiento, reiniciar/redeployar el servicio y consultar el mismo
   usuario para demostrar persistencia.

Las bases SQLite originales se conservan localmente; no se importaron a Neon.
La verificación cloud utiliza exclusivamente movimientos sintéticos de demostración.

## Validación

Contra la URL pública, `/health`, `/docs` y `/openapi.json` devolvieron HTTP 200.
Newman ejecutó ocho peticiones y once aserciones sin fallos. Después de reiniciar
Render y comprobar el nuevo arranque en sus logs, dos movimientos conservaron
IDs, saldo PEN 92.66 y repetición idempotente HTTP 200.

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
