# AEMET y worker local — fase 2

La API lee el archivo de PostgreSQL. Solo `python -m meteocentro.worker` consulta AEMET. No hay planificador en FastAPI ni llamadas de observaciones desde el navegador. Meteoclimatic continúa pendiente de términos y coordenadas; Wunderground sigue aplazado. No se incluye importación climatológica diaria, agregados, panel administrativo ni despliegue remoto.

## Arranque y diagnóstico

Con las dependencias de `backend/uv.lock` instaladas, configurar `DATABASE_URL` y `AEMET_API_KEY` en el entorno privado del proceso. La clave no es un argumento de línea de comandos y nunca debe llevar prefijo `VITE_`. Aplicar primero la migración:

```sh
backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
backend/.venv/bin/python -m meteocentro.worker
```

Desde otra terminal con el mismo `DATABASE_URL`:

```sh
backend/.venv/bin/python -m meteocentro.worker --status
backend/.venv/bin/python -m meteocentro.worker --once current
backend/.venv/bin/python -m meteocentro.worker --once inventory
```

Las consultas únicas usan la misma cola, exclusiones y cuota. Si el trabajo todavía no vence, otro proceso lo tiene arrendado o el proveedor está pausado, devuelven `not_due_or_paused`, sin HTTP, y código de salida 1. `--max-runs N` limita las ejecuciones reclamadas por ese proceso, sin adelantar horarios. `--status` solo lee PostgreSQL. `--resume` levanta una pausa después de corregir la clave o actualizar el normalizador; no borra consumos ni adelanta un `Retry-After` pendiente.

Compose mantiene el worker en el perfil explícito `ingestion`, para que arrancar la base o la interfaz no active consultas involuntarias:

```sh
docker compose --profile ingestion config --quiet
docker compose --profile ingestion up -d --build
# Estado operativo, sin imprimir configuración ni secretos:
docker compose --profile ingestion exec worker python -m meteocentro.worker --status
```

La clave solo se pasa al servicio `worker`, no a `api` ni a `web`. El perfil usa la misma imagen de backend, arranca tras migrar y tiene `restart: unless-stopped`. Detener con `docker compose --profile ingestion down` conserva el volumen; no utilizar `down -v` para una parada ordinaria. Podman/Quadlet y el servidor remoto pertenecen a fase 7.

## Programación, recuperación y presupuesto

| Parámetro de entorno | Valor inicial | Significado |
| --- | --- | --- |
| `AEMET_POLL_SECONDS` | 900 | Mínimo 15 minutos entre ciclos de observaciones completados |
| `AEMET_DAILY_HTTP_BUDGET` | 400 | Techo local de intentos HTTP por día UTC; no es una cuota diaria prometida por AEMET |
| `AEMET_CURRENT_RESERVE` | 220 | El inventario no puede consumir las últimas 220 peticiones del presupuesto |
| `AEMET_MINUTE_HTTP_BUDGET` | 20 | Ventana móvil de 60 segundos, hasta un máximo configurable de 40 |
| `AEMET_CONCURRENCY` | 1 | Ejecuciones simultáneas por proveedor entre procesos; admite 1–2 |
| `WORKER_LEASE_SECONDS` | 120 | Arrendamiento, renovado cada tercio de su duración |
| `WORKER_POLL_SECONDS` | 2 | Espera del bucle de cola; no frecuencia HTTP |

El inventario vence cada 24 horas. Los metadatos se recuperan al primer acceso de cada producto y se conservan hasta 24 horas; cada versión de sus campos tiene un hash. Un día sin incidencias, a 96 ciclos actuales, necesita aproximadamente **196 GET**: 192 de observación, dos de inventario y dos de metadatos. Reintentos y descargas fallidas también consumen cuota, incluso si el proceso muere después de reservar y antes de enviar. Un arranque puede descargar las últimas horas que ofrece el lote, sin inventar una resolución superior a la publicada.

`provider_runtime` serializa las reservas y conserva consumo, ventana móvil, pausa y espera. El presupuesto incluye sobres API, datos y metadatos, de forma más conservadora que contar solo la primera petición. Se comparte entre workers conectados a esta base; no contabiliza otros proyectos que utilicen la misma clave, por lo que un 429 del proveedor sigue prevaleciendo. La reserva protege las observaciones frente al catálogo; recuperar las horas todavía disponibles usa el mismo lote actual, sin una cola ilimitada de peticiones por estación.

Un bloqueo asesor de PostgreSQL da liderazgo durante cada transacción de programación. Hay una sola fila de trabajo por producto mediante `dedupe_key`; iniciar otra API o reiniciar workers no duplica calendarios. Cada reclamación asigna un UUID de propietario nuevo, incrementa `attempts` y crea un `ingestion_run`. La reclamación respeta prioridad, concurrencia y `SKIP LOCKED`.

La escritura se divide en transacciones de hasta 25 registros del ámbito. Cada una bloquea el trabajo y comprueba propietario, estado y vencimiento antes y después de escribir. Los datos confirmados sobreviven a la muerte del proceso; si falla antes del acuse final, el siguiente propietario vuelve a consultar la ventana disponible y repite las operaciones idempotentes. El propietario anterior no puede confirmar, reservar más llamadas ni escribir cuando pierde su arrendamiento. Las ejecuciones vencidas quedan `abandoned`.

El cursor de cada producto conserva el máximo confirmado por ID externo y el instante del ciclo completado. No se adelanta si falla PostgreSQL ni si el proceso cae antes del acuse. Si la siguiente respuesta ya no alcanza una ventana anterior, el resultado registra `outside_available_window`; una fuente ausente registra `source_absent_in_batch`. Son avisos de cobertura, no observaciones fabricadas ni prueba de que hubiera una medida esperada en cada hora. Los huecos intermedios siguen visibles en el archivo. No se promete recuperar horas anteriores a las que aún devuelve AEMET.

Ante un error transitorio hay tres reintentos con espera exponencial y variación aleatoria; agotados, se vuelve a la cadencia ordinaria. Un 429 conserva el mayor plazo entre `Retry-After` y la espera local, compartido por todos los trabajos. Un 401/403 o falta de clave pausa el proveedor hasta `--resume`. Un cambio incompatible de metadatos también pausa la fuente. Si PostgreSQL cae, el bucle espera y reintenta, sin confirmar el trabajo. SIGTERM/SIGINT deja terminar la operación acotada en curso; si el proceso se interrumpe antes, su arrendamiento vence y se recupera.

## Contrato de observaciones y catálogo

La respuesta inicial se resuelve en cada consulta; las URL temporales nunca se guardan. Solo se aceptan URL HTTPS del host exacto `opendata.aemet.es`, puerto normal y ruta `/opendata/`, sin credenciales de usuario ni redirecciones. La cabecera `api_key` solo viaja a la petición inicial. Hay timeout de conexión de 10 segundos, lectura de 30 segundos, plazo de descarga de 90 segundos y máximo de 16 MB descomprimidos. Se aceptan UTF-8 e ISO-8859-1. Los errores persistidos y la salida operativa contienen códigos fijos, sin cuerpos HTTP, URLs temporales, claves ni excepciones completas.

Los polígonos completos del IGN deciden la provincia. El catálogo es la unión de IDs actuales e inventario, conserva la identidad `(provider, external_id)` y acumula capacidades. Un origen exclusivo del inventario tiene `daily_history`, sin `current`, y la API indica `freshness: historical_only`. La capacidad señala el producto del catálogo; el importador de diarios sigue sin implementarse (`fetch_history` devuelve `unsupported`). La ausencia en un lote no elimina ni desactiva estaciones. Coordenadas inválidas se cuentan; una posición actual que difiera más de 0,002 grados respecto a la registrada deja la estación en revisión, sin mover silenciosamente su histórico. Es un umbral de revisión, no una fusión espacial.

Cada registro actual produce tres observaciones semánticas:

| Grupo | Métricas normalizadas | Periodo |
| --- | --- | --- |
| Instantáneo | Temperatura, humedad, presión de estación y presión al nivel del mar | `fint`, UTC, sin ventana agregada |
| Viento | Velocidad media `vv`, m/s | Diez minutos anteriores a `fint`, `interval_mean` |
| Precipitación | Una métrica `rain`, mm | Sesenta minutos anteriores a `fint`, `interval_total` |

`fint` sin sufijo se interpreta en UTC porque así lo define AEMET. Un nulo conserva ausencia; cero sigue siendo un valor. No se interpretan centinelas textuales no verificados: un número mal formado invalida el registro; un valor fuera de límites físicos queda nulo para uso y conserva su original y una marca de plausibilidad. `prec` tiene prioridad cuando es utilizable; `pacutp` se usa como alternativa. Ambos sensores y la elección quedan en `quality`, nunca se suman. Los periodos permiten detectar solapamientos; esta fase no calcula acumulados diarios. Los demás campos publicados por AEMET no se normalizan todavía.

La unicidad semántica de PostgreSQL impide duplicados. Las correcciones guardan métricas, calidad y hash anteriores en `observation_revisions`. Los últimos valores avanzan solo con un instante igual o posterior; si una corrección invalida el último valor, se busca el anterior utilizable. La edad de AEMET se evalúa respecto a `observed_at`, con cadencia horaria observada y tolerancia inicial de 90 minutos, nunca respecto a un HTTP 200. La cadencia es una observación del piloto, no una garantía del proveedor.

## Exclusión y observabilidad

La ingestión y un trigger de `exclusions` bloquean la misma fila de estación. Tras adquirir ese bloqueo se vuelve a consultar la exclusión en una sentencia nueva de PostgreSQL `READ COMMITTED`. Si la exclusión confirma primero, el trabajo descarta el origen; si la escritura confirma primero, la exclusión posterior oculta inmediatamente lo almacenado. Se aplica tanto a estación como a origen, también en redescubrimiento. No se reactivan estados de moderación ni se hacen cambios en redes externas. Todas las lecturas públicas siguen usando el servicio común de elegibilidad.

`--status` muestra los trabajos, cinco últimas ejecuciones, `last_polled_at`, `last_new_data_at`, `newest_observed_at`, edad y `data_state`. `last_polled_at` incluye consultas completadas de cualquiera de los dos productos; las ejecuciones identifican cuál. Cada resultado separa recibidos, válidos, fuera de ámbito, excluidos, inválidos, nuevos orígenes, observaciones insertadas/revisadas/idénticas, retraso y huecos. Los contadores de registros y observaciones son distintos porque cada registro actual contiene varios periodos. No hay todavía un panel web de operación.

## Pruebas ordinarias

Usar una base PostgreSQL 17 dedicada cuyo nombre termine en `_test`: las fixtures truncan sus tablas. Nunca apuntar las pruebas al archivo local o a producción.

```sh
# TEST_DATABASE_URL debe apuntar a esa base aislada.
backend/.venv/bin/pytest tests/test_phase1.py tests/test_phase2.py -q
python3 -m unittest discover -s tests -p test_phase0.py -v
backend/.venv/bin/ruff check backend/src tests/conftest.py tests/test_phase1.py tests/test_phase2.py
```

La suite ordinaria usa transporte HTTP simulado y PostgreSQL real. Las consultas reales se ejecutan separadamente con clave del entorno, horarios ordinarios y presupuesto reducido. Sus resultados y limitaciones se registran en [ESTADO.md](ESTADO.md).
