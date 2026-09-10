# Fase 1 Base de la aplicación y datos

## Objetivo

Crear un proyecto local reproducible y un modelo que permita mezclar redes sin perder la identidad, procedencia o significado de las observaciones. Al terminar habrá una API mínima, un frontend mínimo y PostgreSQL, todavía sin pretender una red meteorológica completa.

Dependencia: fase 0 documentada, incluidas sus limitaciones. Se puede trabajar con fixtures si faltan claves, pero debe indicarse en la interfaz de desarrollo y en el estado de la fase.

## Trabajo

1. Crear `backend/`, `frontend/`, `deploy/containers/`, `config/` y `tests/` según el diseño. Compartir el paquete de dominio entre API y worker, con puntos de entrada diferentes.
2. Fijar versiones soportadas de Python, Node, PostgreSQL y dependencias, con archivos de bloqueo. Incluir un `compose.yaml` de desarrollo compatible con el proveedor de Compose que se documente; comprobar su invocación con Podman en el entorno de prueba.
3. Configurar FastAPI, validación de entrada y salida, SQLAlchemy y Alembic. Crear `/health/live` y `/health/ready`; el segundo comprueba base de datos y esquema. Un fallo externo de una red no debe derribar la API.
4. Crear las entidades descritas en el resumen: proveedores, estaciones, orígenes, historial de ubicación, observaciones, últimos valores, resúmenes, exclusiones, trabajos, auditoría y acceso administrador. Las tablas de agregados podrán completarse en fase 5, pero su contrato debe quedar fijado.
5. Establecer unicidad de `(provider_id, external_id)` y una clave de observación que incluya origen, producto, instante y periodo. Definir representación de periodos ausentes para que la unicidad siga funcionando con nulos.
6. Separar estado de moderación (`active`, `excluded`, `review`) de frescura (`fresh`, `stale`, `unknown`) y estado del origen (`enabled`, `paused`, `unavailable`). La falta de datos no equivale a exclusión.
7. Implementar normalizadores comunes, estructuras de calidad, conversión de unidades y clasificación provincial. Guardar en una observación las métricas y metadatos que permitan recuperar su significado: presión de estación o relativa, lluvia de intervalo o contador, y hora del dato.
8. Definir un servicio único de elegibilidad que filtre exclusiones. Las futuras rutas de mapa, series, rankings y exportaciones usarán ese mismo servicio.
9. Crear interfaz de adaptador con `capabilities`, `discover`, `fetch_current` y `fetch_history`. Un método no soportado devuelve un resultado explícito, no una lista vacía indistinguible de “sin datos”.
10. Preparar integración continua para instalación, formato, análisis estático, migraciones y pruebas pertinentes. No requiere secretos meteorológicos ni conectividad con sus APIs.

## Contratos mínimos

Una observación normalizada debe conservar `source_id`, `product`, `observed_at`, `fetched_at`, `period_start`, `period_end`, `period_basis`, valores/unidades, banderas de calidad, `payload_hash` y versión del normalizador. Las métricas sin sensor son nulas. El hash no sustituye a la clave semántica: dos valores iguales en momentos distintos pueden ser observaciones diferentes.

La API de lectura inicial será versionada bajo `/api/v1`. Definir OpenAPI para lista de estaciones, detalle, última observación, series y resúmenes. Especificar paginación, error, fecha UTC y límites de rangos antes de construir el frontend completo. Las rutas de fases posteriores pueden seguir pendientes; no devolver éxito ficticio desde endpoints vacíos.

## Entregables

- Backend y frontend mínimos, migración inicial y configuración de desarrollo.
- `.env.example` con nombres y valores no secretos; validación de configuración al arrancar.
- Documentación de arranque con los comandos que realmente se hayan probado.
- Contratos de proveedor y API, datos de demostración separados de producción.
- CI y actualización del estado.

## Comprobación de salida

- [ ] Un checkout limpio permite levantar desarrollo con el procedimiento documentado.
- [ ] La migración funciona sobre PostgreSQL vacío y se detecta un esquema incompatible.
- [ ] La base rechaza un origen duplicado y una observación duplicada, incluidos sus casos con nulos.
- [ ] Cero, nulo y dato inválido producen resultados diferentes.
- [ ] La clasificación provincial excluye puntos fuera del ámbito y trata los límites de forma determinista.
- [ ] Una estación excluida no aparece en la primera ruta de lectura.
- [ ] El navegador y los logs no contienen secretos de prueba.

## Prompt para Codex

```text
Lee AGENTS.md, el resumen global, las decisiones, el estado y
docs/fases/FASE_01_BASE_Y_DATOS.md. Implementa solo la fase 1.
Construye una base reproducible con React, FastAPI y PostgreSQL,
migraciones, contratos de proveedores y el modelo indicado. No inventes
datos reales ni metas la ingestión en el proceso web. Prueba unicidad,
nulos, periodos y exclusión con PostgreSQL real. Documenta los comandos
que has verificado y actualiza el estado sin dar por hechas otras fases.
```
