# FinanZen — Deployment con datos persistentes

Configuración preparada el **10 de octubre de 2026** para Render.
Estado: **pendiente de conectar la cuenta y aceptar el costo; no publicado todavía**.

## Recursos preparados

| Recurso | Configuración |
| --- | --- |
| Código | `1ASI0657-2620-9199/back`, rama `main` |
| Runtime | Python 3.11.9, FastAPI, Uvicorn |
| Servicio | Web Service, plan `0.5c-512mb` (antes Starter), una instancia |
| Región | Virginia |
| Build | `python -m pip install -r requirements.txt` |
| Start | `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check | `/health` |
| Disco | 1 GB montado en `/var/data` |
| SQLite | `FINANZEN_DB_PATH=/var/data/financial_records.db` |
| Actualizaciones | `autoDeployTrigger: checksPass` |

El [Blueprint](render.yaml) define estos recursos. La base se inicializa al
arrancar el servicio, cuando el disco ya está montado. Solo el contenido bajo
`/var/data` se conserva entre reinicios y despliegues. El servicio es una única
instancia; no utiliza una base SQLite incluida en el repositorio.

El backend expone Swagger en `/docs` y OpenAPI en `/openapi.json`, y funciona
sin el repositorio del frontend. No se modifica la identidad de demostración
basada en `X-User-Id` ni se presentan estos endpoints como autenticación real.

## Costo antes de publicar

Precios consultados el 10 de octubre de 2026: USD 7/mes por `0.5c-512mb` y
USD 0.25/GB/mes por disco. La configuración de 1 GB tiene un costo base estimado
de **USD 7.25/mes**, sin impuestos ni consumos adicionales. El workspace Hobby
puede mantenerse sin cuota adicional. Confirmar el importe mostrado por Render
antes de crear recursos. [Precios oficiales](https://render.com/pricing).

No se aplica el Blueprint ni se crea un recurso facturable hasta que el dueño
de la cuenta acepte este costo. Render gratuito no permite discos persistentes.

## Publicación

Con la cuenta conectada y el costo aceptado:

1. Crear un Blueprint desde el repositorio `back` utilizando `render.yaml`.
2. Revisar plan, región, disco, comandos y variables; confirmar la creación.
3. Esperar que el build y el deploy finalicen y que el estado del servicio sea Live.
4. Registrar la URL HTTPS real asignada por Render, ID de deploy y commit publicado.
5. Verificar `/health`, `/docs` y `/openapi.json` en esa URL.
6. Ejecutar la colección de Postman contra la URL pública y conservar resultados.
7. Registrar un movimiento de muestra, reiniciar el servicio y consultar el mismo
   usuario para comprobar que el movimiento permanece en SQLite.

La persistencia en Render debe comprobarse después de publicar; una verificación
local de reinicios no demuestra por sí sola que el disco cloud esté montado.

## Evidencias

El reporte conservará configuración, logs de build/deploy, URL, commit,
respuestas reales y capturas de Swagger. Las capturas locales están identificadas
por su entorno y no sustituyen las evidencias del servicio público.

## Referencias

- [FastAPI en Render](https://render.com/docs/deploy-fastapi).
- [Discos persistentes y sus restricciones](https://render.com/docs/disks).
- [Blueprint y validación](https://render.com/docs/blueprint-spec).
- [Identificadores de planes](https://render.com/docs/compute-plans).
