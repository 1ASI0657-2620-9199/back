# FinanZen — Back

Repositorio independiente del backend de FinanZen: **Financial Records Service**
con Python, FastAPI y Pydantic. Utiliza SQLite local o PostgreSQL mediante
`DATABASE_URL` para conservar datos fuera del servidor cloud.

[Todos los repositorios](https://github.com/orgs/1ASI0657-2620-9199/repositories) ·
[Reporte](https://github.com/1ASI0657-2620-9199/report) ·
[Frontend](https://github.com/1ASI0657-2620-9199/front)

## Estructura

```text
back/
├── app/
│   ├── main.py
│   ├── models.py
│   ├── database.py        Conexiones SQLite/PostgreSQL y transacciones
│   └── repository.py
├── data/                 SQLite generado al arrancar
├── tests/test_api.py
├── requirements.txt
└── .github/workflows/validacion.yml
```

## Ejecutar la API

Requiere Python 3.10 o superior. Desde un directorio de trabajo:

```powershell
git clone https://github.com/1ASI0657-2620-9199/back.git
cd back
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

En Linux/macOS, usar `.venv/bin/python`.

- Swagger: http://127.0.0.1:8000/docs
- OpenAPI: http://127.0.0.1:8000/openapi.json
- Estado: http://127.0.0.1:8000/health

La API funciona sin clonar otros repositorios. SQLite y su esquema se crean al
arrancar; las bases de ejecución y entornos virtuales no se versionan.

## Ejecutar junto al frontend

Clonar `front` junto a `back`, en el mismo directorio padre:

```powershell
git clone https://github.com/1ASI0657-2620-9199/front.git
```

```text
directorio-de-trabajo/
├── back/                 Repositorio Git independiente
└── front/                Repositorio Git independiente
```

Reiniciar el backend y abrir http://127.0.0.1:8000. La ruta predeterminada apunta
a la carpeta vecina `front`. Para otra ubicación o base de datos:

```powershell
$env:FINANZEN_FRONTEND_PATH = 'C:\ruta\a\front'
$env:FINANZEN_DB_PATH = 'C:\ruta\a\financial_records.db'
```

Estas variables son opcionales. La base predeterminada es
`data/financial_records.db`, relativa al directorio de ejecución del servidor.

`DATABASE_URL` selecciona PostgreSQL para despliegue. La configuración gratuita
preparada utiliza Neon como almacenamiento y Render para la API.

## API disponible

| Método | Ruta | Función |
| --- | --- | --- |
| GET | `/health` | Estado operativo |
| POST | `/api/v1/transactions` | Registrar ingresos o gastos |
| GET | `/api/v1/transactions` | Consultar movimientos del usuario |
| GET | `/api/v1/summary?month=2026-10&currency=PEN` | Resumen mensual |

Los endpoints financieros requieren `X-User-Id`. El POST también requiere
`Idempotency-Key`, de 8 a 120 caracteres. Un movimiento nuevo devuelve HTTP 201;
su repetición devuelve el mismo ID con HTTP 200; reutilizar la clave con otro
cuerpo devuelve HTTP 409.

`X-User-Id` simula identidad; no autentica usuarios. Presupuestos recomendados,
alertas, IA, educación, IoT y auditoría permanecen pendientes.

## Deployment

[Configuración gratuita Render + Neon](DEPLOYMENT.md) y [Blueprint](render.yaml).
La publicación requiere conectar las cuentas y configurar `DATABASE_URL` como
variable privada. La aplicación no usa SQLite efímero cuando arranca en Render.

## Pruebas

Dentro de este repositorio, con las dependencias instaladas:

```powershell
python -m pytest -q
```

Las cinco pruebas usan bases temporales y cubren idempotencia/conflictos,
resumen y filtrado por usuario, frontend configurable, API sin frontend y
conservación de movimientos/idempotencia al recrear la aplicación con el mismo
archivo SQLite seleccionado mediante `FINANZEN_DB_PATH`.
El workflow repite la suite sin depender de los demás repositorios.

`tests/test_postgres.py` añade tres pruebas de integración PostgreSQL real,
incluyendo persistencia e idempotencia concurrente. Se ejecutan cuando existe
`TEST_DATABASE_URL`; el workflow proporciona esa base de pruebas. Sin esa
variable se omiten las tres y se ejecutan las cinco pruebas SQLite.

[Evidencia de validación](https://github.com/1ASI0657-2620-9199/report/blob/main/evidencias/validacion.md)
