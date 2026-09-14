# Estado del desarrollo

Última actualización: 14 de septiembre de 2026.

**Estado general:** investigación de fase 0 y base local de fase 1 ejecutadas. AEMET OpenData funciona con la clave local indicada por el usuario; el XML de Meteoclimatic responde, pero su uso transformado y publicación requieren aclarar condiciones. La API, PostgreSQL y el frontend mínimos arrancan localmente; no hay ingestión periódica ni despliegue remoto.

**Alcance vigente tras revisar costes:** primera versión con AEMET y Meteoclimatic; Wunderground aplazado como ampliación opcional. El usuario ha confirmado que no dispone de clave WU y prefiere prescindir de esa red si es cara. Se ha comprobado la tarifa pública y la elegibilidad/límite de las claves PWS; ver el documento de coste. No se ha contratado ningún servicio.

## Seguimiento de fases

| Fase | Estado | Evidencia pendiente para cerrar |
| --- | --- | --- |
| 0 | Investigación realizada; bloqueos documentados | Confirmar permiso de conservación, transformación y publicación de Meteoclimatic y coordenadas verificables para su red; ampliar piloto histórico cuando corresponda |
| 1 | Implementación y pruebas locales completadas con Docker Compose y PostgreSQL 17.11; comprobación Podman pendiente | Ejecutar el mismo Compose con Podman en un entorno que lo tenga instalado |
| 2 | Pendiente | Ingestión AEMET real y recuperación del worker |
| 3 | Pendiente | Ingestión Meteoclimatic real y descubrimiento sin reactivar exclusiones; WU fuera de alcance |
| 4 | Pendiente | Recorrido de mapa, tabla y ficha en escritorio y móvil |
| 5 | Pendiente | Series y agregados correctos con periodos y cobertura |
| 6 | Pendiente | Exclusión y restauración comprobadas de extremo a extremo |
| 7 | Pendiente | Instalación Podman, reinicio, actualización y restauración ensayados |

## Registro de fase 1 — 14 de septiembre de 2026

**Cambios realizados:** proyecto FastAPI/React con PostgreSQL, migración Alembic `0001_initial`, modelo de 15 tablas, normalización y clasificación provincial comunes, contrato extensible de adaptadores, servicio de elegibilidad para todas las rutas de lectura, puntos de entrada separados para API y worker, Compose local y CI de comprobación. La base y la pantalla arrancan vacías: no hay fixtures insertados en desarrollo ni datos meteorológicos publicados. La semántica está detallada en [CONTRATOS_FASE_1.md](CONTRATOS_FASE_1.md). Versiones elegidas: Python 3.13, Node 24.21.0, PostgreSQL 17.11 y uv 0.12.13; dependencias resueltas en `backend/uv.lock` y `frontend/package-lock.json`.

**Comandos y resultados reales:** `uv lock` y `uv sync --locked` instalaron el backend con Python 3.13. `npm ci` y `npm run build` compilaron el frontend; la compilación local usó Node 26 instalado en el equipo y mostró un aviso de versión, mientras que el servicio Compose usó Node 24.21.0. `docker compose config --quiet` y `docker compose up -d --build` terminaron correctamente. En el Compose nuevo, PostgreSQL y API quedaron saludables, la migración terminó con éxito, `GET http://127.0.0.1:5173/health/ready` devolvió `{"status":"ready","schema_revision":"0001_initial"}` y `GET /api/v1/stations` devolvió cero estaciones. OpenAPI sirvió 7 rutas. Se inspeccionó la pantalla en el navegador local, que mostró «0 estaciones publicables en la base local» y el aviso de que aún no hay datos meteorológicos.

**Pruebas:** se creó además una base PostgreSQL 17.11 temporal y vacía fuera de Compose para la migración y los tests. `pytest tests/test_phase1.py -q`: 10 correctas, con restricciones reales para identidad y observaciones duplicadas con periodos nulos/no nulos; cero, nulo, inválido, cambio horario, límite provincial, exclusión observada tras otro commit, rutas vacías y esquema incompatible. `alembic check`: sin diferencias entre modelos y migración. `python3 -m unittest discover -s tests -p test_phase0.py -v`: 5 correctas. `ruff check`, `ruff format --check` y `git diff --check`: correctos. `frontend/dist`, `.env`, dependencias locales y entorno virtual están ignorados por Git; el bundle no contiene nombres de variables de claves ni la URL de PostgreSQL. Estas pruebas no usan APIs meteorológicas externas.

**Limitaciones:** la integración AEMET real corresponde a la fase 2 y no se activó aquí; la clave del otro proyecto no se copió. Meteoclimatic continúa `pending_terms`. El usuario acepta las coordenadas públicas mostradas a minutos para la representación aproximada en el futuro mapa; se añadió una clasificación conservadora para las que se verifiquen manualmente, pero no se encontró un feed de coordenadas documentado en su mapa: ver [el contrato actualizado](integraciones/METEOCLIMATIC.md). No se automatizó la extracción de fichas ni se probó su reutilización. Podman no estaba instalado en este equipo, por lo que su invocación específica sigue sin verificar; sí se ejecutó el Compose completo con Docker. La CI quedó preparada, pero no hay una ejecución de GitHub Actions acreditada en esta fase.

**Siguiente paso:** validar Podman cuando haya un entorno de prueba disponible; después, fase 2 para un worker AEMET separado con límites de cuota, recuperación y pruebas reales acotadas. La fase 3 de Meteoclimatic depende de aclarar licencia, método autorizado de coordenadas y significado de sus contadores.

## Registro de fase 0 — 14 de septiembre de 2026

**Cambios realizados:** [matriz por producto](integraciones/MATRIZ_ACCESO.md), [contrato AEMET](integraciones/AEMET.md), [contrato Meteoclimatic](integraciones/METEOCLIMATIC.md), diagnósticos sin registro de secretos, polígonos IGN completos y de visualización con [procedencia](../config/provinces.provenance.json), fixtures sintéticos y pruebas. Wunderground permanece `deferred_cost`.

**Comandos y resultados reales:** `python3 scripts/fetch_provinces.py` descargó una geometría oficial para cada provincia y generó cuatro polígonos completos y una versión visual simplificada; requiere red y solo debe repetirse si se decide actualizar la versión. El diagnóstico `python3 scripts/diagnose_sources.py --aemet --meteoclimatic`, con `AEMET_API_KEY` en el entorno del proceso, obtuvo HTTP 200 para observaciones, inventario y XML. `--aemet-history` obtuvo HTTP 200 para un día de la estación `2462`; sin clave, `--aemet` devuelve `pending_access` sin llamar a la red. Las respuestas AEMET se descargaron mediante las URL temporales de `datos` y `metadatos`, sin guardarlas. Dos consultas `GetCapabilities` del IGN respondieron HTTP 200. `python3 -m unittest discover -s tests -v`: 5 pruebas, todas correctas. `python3 -m compileall -q scripts tests` y `ruff check scripts tests`: correctos. El entorno inicial no permitía DNS; las consultas oficiales se ejecutaron con la autorización de red correspondiente. No se probó Podman ni PostgreSQL porque pertenecen a fases posteriores.

**Fuentes reales y capacidades:** AEMET observación actual, inventario y un día climatológico: `verified` técnico. En la instantánea final de observación había 10.039 filas, 855 IDs únicos en España y 49 IDs únicos clasificados dentro de las cuatro provincias; en inventario, 926 IDs y 55 clasificados dentro. Meteoclimatic XML nacional: 1.540 estaciones, `verified` técnico; los prefijos de los cuatro ámbitos produjeron 89/22/10/22 estaciones respectivamente, todavía sin coordenadas en el XML. Estos conteos no son cobertura garantizada ni datos almacenados. RSS Meteoclimatic e importación diaria no se probaron. Ninguna fuente está integrada en la app.

**Pruebas con fixtures:** los ejemplos de AEMET y Meteoclimatic son [sintéticos](../tests/fixtures/SINTETICOS.md). Las pruebas distinguen 0 de nulo, ISO-8859-1, coordenadas sexagesimales, clasificación geográfica, límite compartido y hora de estación frente a hora del feed. No sustituyen pruebas de ingestión ni de base de datos.

**Limitaciones y decisiones:** Meteoclimatic enlaza CC BY-NC-ND 3.0; se marca `pending_terms` antes de conservar, transformar o publicar datos. La pertenencia provincial de sus IDs por prefijo es provisional hasta disponer de coordenadas verificadas. En AEMET, el inventario no equivale a emisión actual; se observaron tres coordenadas de inventario no interpretables. Los históricos horarios antiguos no están acreditados. La clave AEMET no se copió a este repositorio ni se publicó; su JWT indica expiración el 31-10-2026 11:22:47 UTC, sujeta a validez efectiva del proveedor. `GetCapabilities` de los dos fondos WMTS candidatos del IGN respondió HTTP 200, aún sin integración visual ni prueba de teselas. No se solicitaron gastos, publicación ni acciones remotas.

**Siguiente paso:** fase 1 con base reproducible y fixtures. Antes de activar Meteoclimatic en la fase 3, resolver por una vía autorizada sus condiciones concretas y las coordenadas. Planificar renovación de la clave AEMET antes de que caduque. La fase 0 queda documentada con esos bloqueos, no como aprobación general de ambas fuentes.

## Comprobaciones de planificación previas

- Revisión de páginas públicas y presentación del mapa de Suremet.
- Consulta de documentación primaria de AEMET, Meteoclimatic, The Weather Company, Podman y componentes propuestos.
- Identificación del límite de la búsqueda de proximidad WU y de diferencias entre históricos diarios e intradiarios.
- Redacción de documentos por fase con entregables, validación y prompts para Codex.

Las comprobaciones de planificación del 9 de septiembre por sí solas no acreditaban acceso; los resultados de acceso real están diferenciados arriba. Las frecuencias y políticas de retención siguen siendo propuestas que deberán medirse.

## Registro para completar en cada fase

```text
Fase y fecha:
Cambios realizados:
Comandos o recorridos verificados:
Resultados observados:
Fuentes reales probadas y capacidades:
Pruebas con fixtures:
Limitaciones o bloqueos:
Decisiones tomadas:
Commit o referencia de entrega:
Siguiente paso:
```
