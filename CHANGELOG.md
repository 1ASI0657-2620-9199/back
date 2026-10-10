# Changelog

## 0.1.1 — 2026-10-10

- Extrae `finanzen-common` 0.1.0 como paquete instalable con Strategy para
  huellas de solicitudes, conservando la compatibilidad con claves existentes.
- Añade cuatro pruebas de biblioteca y seis escenarios Gherkin ejecutables
  con Behave, vinculados al recorte de US01 y al resumen de PF-02.
- CI ejecuta 12 casos pytest con PostgreSQL 17, los seis escenarios locales
  y la construcción wheel/sdist; publica los artefactos de validación.
- Mantiene el contrato de transacciones y el almacenamiento PostgreSQL en Neon.

La etiqueta de release del backend es `v0.1.1`; la biblioteca tiene su propia
versión `0.1.0`. No se declara una publicación del paquete en PyPI ni una
revisión por otro integrante cuando no exista.
