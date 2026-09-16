# Contratos de la fase 1

## API de lectura

La especificación OpenAPI se sirve en `/api/v1/openapi.json` y su interfaz en `/api/v1/docs`. Todas las rutas de estaciones consultan PostgreSQL y pasan por el servicio común de elegibilidad. En una base vacía devuelven colecciones vacías; no generan estaciones ni observaciones ficticias.

| Ruta | Parámetros | Límite y resultado |
| --- | --- | --- |
| `GET /api/v1/stations` | `province` (`05`, `19`, `28`, `40`), `limit`, `offset` | 1–100, `offset` 0–10.000; orden por nombre e ID; `total` |
| `GET /api/v1/stations/{id}` | UUID | Ficha y orígenes elegibles; 404 si está excluida o no existe |
| `GET /api/v1/stations/{id}/latest` | UUID | Última observación por métrica y origen; 404 si la estación no es elegible |
| `GET /api/v1/stations/{id}/observations` | `start`, `end`, `limit`, `offset` | Intervalo `[start,end)` máximo de 31 días; 1–1000, `offset` 0–100.000; `next_offset` |
| `GET /api/v1/stations/{id}/daily-summaries` | `start`, `end`, `limit`, `offset` | Intervalo `[start,end)` máximo de 366 días; 1–1000, `offset` 0–100.000; `next_offset` |

`start` y `end` deben llevar zona horaria; el servidor los convierte a UTC y rechaza intervalos invertidos, vacíos o excesivos con HTTP 422. Las fechas emitidas por PostgreSQL son instantes con zona horaria y se interpretan en UTC. FastAPI devuelve HTTP 422 para parámetros mal formados. Los errores de dominio tienen `detail.code`: `station_not_found`, `timezone_required`, `invalid_range`, `schema_mismatch` o `database_unavailable`. `/health/live` no depende de redes externas. `/health/ready` comprueba conexión y versión Alembic; un esquema distinto recibe HTTP 503. La frescura de una estación (`fresh`, `stale`, `unknown`) se calcula solo a partir de orígenes elegibles y, provisionalmente, considera 60 minutos como umbral; no modifica su estado de moderación.

## Modelo y semántica

`station_sources` preserva una identidad única por `(provider_id, external_id)` y mantiene estable el UUID interno. `observations` usa `(source_id, product, observed_at, period_start, period_end, period_basis)` como identidad semántica. La restricción PostgreSQL 17 `NULLS NOT DISTINCT` impide duplicados cuando el periodo es nulo. Los instantes deben contener zona horaria. `period_start` y `period_end` son ambos nulos o ambos presentes y ordenados; `period_basis` identifica el tipo de periodo medido. `fetched_at`, `payload_hash` y `normalizer_version` preservan procedencia. Los valores nulos de sensores se conservan como nulos; cero es una medida válida. Una observación que revise otra conserva el valor anterior en `observation_revisions` cuando la lógica de ingesta de fases posteriores se implemente.

Cada métrica del contrato Python `NormalizedObservation` lleva valor canónico, unidad, tipo semántico, valor/unidad original y calidad. `daily_counter` no se suma como lluvia de intervalo. Desde fase 3, `daily_minimum` y `daily_maximum` distinguen extremos reportados de valores actuales. Meteoclimatic registra `period_basis=provider_day_timezone_unknown` por métrica diaria: es una instantánea de contador/extremo sin límites diarios conocidos, no un intervalo de lluvia ni un diario cerrado. La presión de estación y la reducida al mar son tipos distintos. Las tablas de últimos valores, resúmenes y agregados fijan el esquema, pero su cálculo y actualización quedan para las fases 2 y 5.

Los adaptadores comparten `Capabilities` y métodos `discover`, `fetch_current`, `fetch_history`. Un método no disponible devuelve `unsupported`, distinguible de una respuesta `ok` vacía y de `pending_access` o `pending_terms`. El punto de entrada del worker es independiente de la API; en fase 1 todavía no programa trabajos.

La clasificación usa polígonos completos del IGN para Madrid, Ávila, Segovia y Guadalajara. Se aplica `covers`; si un punto cae exactamente en un límite compartido, se escoge el código provincial menor para obtener un resultado determinista. Para coordenadas mostradas solo a minutos, `classify_minute_precision_location` exige que el rectángulo de incertidumbre de ±1 minuto esté contenido entero en una provincia; si no, devuelve `None` y requiere revisión. No se consulta automáticamente ninguna ficha externa.

## Evolución en fase 2

La migración `0002_aemet_worker` añade programación, arrendamientos, reservas de cuota y versiones de metadatos. Se han implementado la ingestión idempotente, revisiones y últimos valores. `freshness` admite ahora `historical_only` para orígenes presentes únicamente en el inventario; AEMET usa una tolerancia de 90 minutos sobre la hora observada, coherente con la cadencia horaria vista. Se añade `interval_mean` al contrato de métricas para el viento medio de diez minutos. El resto de rutas y límites se conserva. Detalles y comandos en [OPERACION_FASE_2.md](OPERACION_FASE_2.md).

## Evolución en fase 3

La migración `0003_network_catalog` añade detección, revisión, candidatos a duplicado y exclusión por identidad previa al alta. La elegibilidad común también exige proveedor `verified` o `paused`: las redes deshabilitadas o pendientes de términos no publican su archivo. Se conserva el contrato de todas las rutas de estaciones y se añade `GET /api/v1/providers` con código/nombre/estado, limitación técnica y última consulta, sin claves ni referencias privadas de autorización. El worker comparte programación y presupuestos entre procesos, aislados por proveedor. El nuevo contrato y los campos Meteoclimatic aún sin semántica verificada se detallan en [OPERACION_FASE_3.md](OPERACION_FASE_3.md).
