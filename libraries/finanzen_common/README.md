# finanzen-common 0.1.0

Biblioteca Python independiente de FastAPI y de la base de datos. Implementa
Strategy mediante `FingerprintStrategy`, `CanonicalJsonSha256` y
`FingerprintService`. El backend consume la biblioteca para detectar cambios
de contenido cuando se reutiliza una clave idempotente.

```powershell
python -m pip install ./libraries/finanzen_common
```

```python
from finanzen_common import FingerprintService

fingerprint = FingerprintService().fingerprint({"amount": "1000.00"})
```

El contrato conserva `json.dumps(sort_keys=True, separators=(",", ":"))`
y SHA-256. No transforma montos, fechas ni valores Unicode: recibe el payload
JSON que el backend ya normaliza con Pydantic. Mantiene las huellas guardadas
antes de extraer la biblioteca. Cambiar la estrategia en producción requiere
una migración explícita; la inyección se demuestra en pruebas.

Distribución reproducible: `python -m build libraries/finanzen_common`.
Las pruebas de compatibilidad y sustitución están en `tests/test_common.py`.
Este paquete se instala desde el repositorio; no se afirma haberlo publicado
en PyPI. El paquete no implementa los módulos de eventos/errores propuestos
en el diseño original.
