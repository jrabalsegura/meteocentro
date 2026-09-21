# Redes y catálogo local — fase 3

La fase añade el lector Meteoclimatic y reutiliza la cola, los arrendamientos, las cuotas y las transacciones de la fase 2. **Meteoclimatic está habilitado en la configuración privada local para uso privado no comercial bajo la licencia publicada**, según [el contrato revisado](integraciones/METEOCLIMATIC.md). El ejemplo genérico se entrega desactivado; la exposición externa se revisará en fase 7. Wunderground no tiene adaptador, configuración ni trabajos; no es requisito de cierre.

## Configuración y arranque

Aplicar `backend/.venv/bin/alembic -c backend/alembic.ini upgrade head` con `DATABASE_URL` privado antes de arrancar API y worker. La migración es `0003_network_catalog`; añade fechas y metadatos de catálogo, exclusiones por identidad externa y sugerencias de duplicado. En registros preexistentes las fechas se inicializan con la creación de la estación: no se reconstruye una fecha de detección anterior que no estaba registrada.

La API solo lee PostgreSQL. `python -m meteocentro.worker` atiende AEMET y Meteoclimatic por turnos; una pausa, denegación o cuota agotada afecta únicamente a su proveedor. El perfil Compose `ingestion` sigue siendo explícito. Los permisos y claves se pasan al worker, nunca al frontend.

**Primer arranque con base vacía:** el primer XML descubre identidades, pero no incluye coordenadas. El catálogo verifica después las fichas en lotes; una estación ya ubicada puede aparecer en el mapa todavía sin observaciones. Los datos se incorporan en la siguiente consulta ordinaria del XML (cada quince minutos), una vez resuelta su ubicación. La web refresca cada sesenta segundos. Si siguen todas sin datos después de ese ciclo, revisar `--status` y los registros del worker; no atribuir indefinidamente el problema al arranque ni forzar consultas fuera de la cadencia.

| Variable | Valor inicial | Efecto |
| --- | --- | --- |
| `AEMET_ENABLED` | `true` | Permite programar AEMET; sigue necesitando su clave |
| `METEOCLIMATIC_ENABLED` | `false` en ejemplo; `true` local | Activación explícita |
| `METEOCLIMATIC_TERMS_REFERENCE` | Vacía | Sin una referencia a licencia o permiso aplicable, mantiene `pending_terms` incluso si se habilita el interruptor |
| `METEOCLIMATIC_POLL_SECONDS` | `900` | Mínimo 15 minutos; no implica una resolución garantizada |
| `METEOCLIMATIC_DAILY_HTTP_BUDGET` | `300` | Techo local diario UTC, incluidos fallos y reintentos; no cuota ofrecida por la red |
| `METEOCLIMATIC_MINUTE_HTTP_BUDGET` | `20` | Ventana móvil de 60 segundos; concurrencia de esa red fijada a uno |

La referencia registra el [aviso legal publicado](https://www.meteoclimatic.net/index/wp/legal_es.html), CC BY-NC-ND 3.0 y el uso local privado no comercial. No representa un permiso especial del proveedor ni debe inventarse. Se conservan originales, procedencia y atribución. El alcance de publicación de derivados se revisará antes de exponerlos. `METEOCLIMATIC_CURRENT_RESERVE=110` reserva presupuesto diario para observaciones: las fichas no pueden consumirlo. `--resume` no evita esta condición ni borra cuota o `Retry-After`.

```sh
backend/.venv/bin/python -m meteocentro.worker --status
backend/.venv/bin/python -m meteocentro.worker --provider meteoclimatic --status
backend/.venv/bin/python -m meteocentro.worker --provider aemet --once current
# Con la licencia aplicable registrada y Meteoclimatic habilitado:
backend/.venv/bin/python -m meteocentro.worker --provider meteoclimatic --once current
backend/.venv/bin/python -m meteocentro.worker --provider meteoclimatic --once catalog
backend/.venv/bin/python -m meteocentro.worker --provider meteoclimatic --resume
```

`--status` no crea proveedores ni consulta la red. Sin `--provider`, el estado devuelve ambas redes; `--once` y `--resume` conservan AEMET como selección predeterminada. Una operación no vencida o deshabilitada termina sin HTTP. Deshabilitar una red en configuración y reiniciar el worker impide reclamar y confirmar trabajos; sus filas e históricos se conservan. La API oculta redes `disabled` o pendientes de permisos. Una pausa técnica mantiene consultable el archivo autorizado, con frescura calculada por hora de observación. `GET /api/v1/providers` y la pantalla inicial muestran el estado, sin claves ni referencias privadas de autorización.

## XML y semántica

Se consulta el [XML nacional documentado](https://www.meteoclimatic.net/index/wp/xml_es.html) `https://www.meteoclimatic.net/feed/xml/ES`. Un único lote actualiza observaciones e identidades; no se descarga otra vez para catálogo. Se filtran los prefijos verificados `ESMAD28`, `ESCYL05`, `ESCYL40`, `ESCLM19`, y la aceptación final exige coordenadas dentro de los polígonos completos del IGN. Un prefijo nunca asigna la provincia por sí solo.

El lector prohíbe DTD y entidades externas, limita la respuesta descomprimida a 8 MB y acota tiempo de conexión, lectura y descarga. No sigue redirecciones. Acepta UTF-8, ASCII, ISO-8859-1 e ISO-8859-15; esta última se verificó en el feed real. La negociación admite `application/xml` y `text/xml`. El esquema o las unidades incompatibles pausan únicamente esta red; un XML mal formado se trata como fallo transitorio. No se necesita RSS: la [documentación del proveedor](https://wiki.meteoclimatic.net/wiki/RSS) prefiere XML para procesar datos y las coordenadas proceden de las fichas públicas. No se ha implementado ni probado RSS.

Se usa `pubDate` de estación con zona explícita. Dos horas locales iguales durante el cambio horario pueden representar instantes distintos. Se normalizan temperatura, humedad, viento actual y dirección; se convierte `kmh` a m/s conservando original y unidad. Vacío y sensor ausente son nulos, cero es válido. Números fuera de rango conservan original y marca de plausibilidad; centinelas textuales no acreditados invalidan el registro, sin inventar valores. `QOS` se conserva como calidad del proveedor; el cero no invalida la estación.

La presión relativa se normaliza como `pressure_sea_level`. Lluvia `rain.total` y extremos diarios se distinguen mediante `daily_counter`, `daily_minimum` y `daily_maximum`, nombres separados y `period_basis=provider_day_timezone_unknown` en cada métrica. La fuente admite UTC y día civil, sin indicar la opción por estación en el XML; no se asignan límites diarios ni hora del extremo. La foto del contador no se suma como incremento ni se convierte en un diario cerrado. Se conservan todos los sensores originales en `quality.reported_fields`, `QOS`, autor público, enlace y licencia. No hay importación histórica ni agregados en esta fase. La justificación semántica y sus fuentes están en [el contrato](integraciones/METEOCLIMATIC.md).

## Descubrimiento, coordenadas y exclusiones

El catálogo común conserva UUID, `first_seen_at`, `last_seen_at`, nombre reportado, capacidades y revisión por origen. El nombre y posición canónicos no se sobrescriben automáticamente. El descubrimiento repetido actualiza la misma identidad. Los registros XML idénticos repetidos se colapsan; los contradictorios con el mismo ID y hora se descartan y contabilizan.

Los IDs sin coordenadas pasan a `review`; el trabajo `catalog` obtiene las coordenadas a minutos y altitud de sus fichas públicas y resuelve automáticamente solo esa falta inicial. Consulta hasta 16 fichas por trabajo, una ejecución cada minuto mientras se prepara el catálogo, caché de 30 días y reintento de errores de ficha a las 24 horas. Comprueba `robots.txt` con caché diaria. Todos los GET usan cuota compartida; no vuelve a descargar el XML. Las posiciones manuales y exclusiones prevalecen. Hay además un comando **local**, sin HTTP ni ruta pública de escritura, para registrar un ID verificado y opcionalmente coordenadas procedentes de una fuente autorizada. Sustituir los marcadores por valores acreditados:

```sh
backend/.venv/bin/python -m meteocentro.catalog_cli add \
  --provider meteoclimatic --id ID_VERIFICADO
backend/.venv/bin/python -m meteocentro.catalog_cli add \
  --provider meteoclimatic --id ID_VERIFICADO --name NOMBRE \
  --latitude LATITUD --longitude LONGITUD --precision minute \
  --evidence REFERENCIA_DE_LA_FUENTE_Y_PERMISO
```

El alta manual no acredita emisión actual: registra `manual_registration`, y el lote observado acredita `current`. `minute` conserva la precisión aproximada: por petición del usuario del 21-9-2026, la provincia se asigna según el punto publicado y la cercanía al límite no bloquea la estación. `exact` requiere coordenadas realmente verificadas con mayor precisión. No se geocodifican municipios ni se accede a endpoints internos del mapa. Las fichas públicas son la fuente documentada de esta implementación, con identidad, precisión y fecha verificadas. Las altas sin posición pueden resolverse con el segundo comando; otras decisiones de moderación no se revierten. Cada operación manual queda auditada.

La revisión diaria de catálogo aprovecha el lote descargado: tras tres revisiones diarias consecutivas sin un origen señala `missing_in_feed`; no borra ni oculta el archivo. Una caída HTTP no cuenta como ausencia. Repetir el mismo lote confirmado no incrementa revisiones diarias. La reaparición limpia únicamente esa marca de ausencia. La frescura permanece separada.

Un cambio de posición superior a 0,002 grados mantiene la posición canónica, registra su instantánea en `station_location_history`, la propuesta en metadatos y un evento de auditoría, y exige revisión. La instantánea no declara confirmado un traslado ni cierra artificialmente su vigencia.

Se proponen duplicados entre redes hasta 250 m, ampliando a 1 km cuando interviene precisión a minutos o nombre coincidente. El usuario redujo este umbral ampliado de 3 km a 1 km el 21-9-2026. Son umbrales conservadores de revisión, no pruebas de identidad. También se revisa un ID nuevo próximo a un origen excluido de la misma red. Las parejas son únicas y el origen nuevo queda en revisión; no se fusionan automáticamente estaciones ni series. Una vinculación explícita conserva procedencia y necesita evidencia:

```sh
backend/.venv/bin/python -m meteocentro.catalog_cli link \
  --source UUID_ORIGEN --station UUID_ESTACION --evidence REFERENCIA_VERIFICADA
```

La estación canónica elegida conserva nombre y posición. La exclusión del destino se hereda de forma efectiva; sacar un origen de una estación excluida conserva la exclusión de ese origen. La elección de una fuente para un único marcador y su señalización visual corresponden al mapa de fase 4; la API aún entrega series separadas y no suma lluvia entre orígenes.

Para aplicar expresamente el umbral reducido a candidatos antiguos, hay un comando local sin HTTP. Por defecto simula la operación y revierte la transacción; `--apply` confirma. Las parejas pendientes fuera de 1 km quedan como `outside_radius`, conservadas con auditoría. Solo se libera una estación cuya única revisión era esa coincidencia, con ubicación acreditada dentro del ámbito y sin exclusión, traslado propuesto, otras parejas ni fuentes vinculadas.

```sh
docker compose run --rm --no-deps worker python -m meteocentro.catalog_cli \
  recheck-duplicates --evidence 'Cambio autorizado del umbral a 1 km'
# Añadir --apply tras revisar el resultado para confirmarlo.
```

El mismo procedimiento permite resolver las revisiones antiguas `uncertain_boundary` con el criterio de provincia aproximada, conservando las demás protecciones. No descarga observaciones; llegan en la siguiente consulta ordinaria del XML.

```sh
docker compose run --rm --no-deps worker python -m meteocentro.catalog_cli \
  recheck-boundaries --evidence 'Aceptación de provincia aproximada junto al límite'
# Añadir --apply para confirmar la revisión auditada.
```

`identity_exclusions` permite vetar `(provider_id, external_id)` antes de descubrirlo. Su trigger toma el mismo bloqueo de identidad que el catálogo. Las exclusiones existentes de estación/origen se vuelven a consultar tras tomar el bloqueo de estación. La misma elegibilidad se aplica a ficha, lista, últimos valores y series/diarios; el alta manual tampoco elude exclusiones. No se cambia nada en las redes externas.

## Pruebas

La suite ordinaria usa PostgreSQL real dedicado, cuyo nombre termina en `_test`, y transportes HTTP sintéticos:

```sh
backend/.venv/bin/pytest tests/test_phase1.py tests/test_phase2.py tests/test_phase3.py -q
python3 -m unittest discover -s tests -p test_phase0.py -v
```

Diagnóstico explícito y separado, sin escritura en base ni salida de datos brutos:

```sh
backend/.venv/bin/python -m meteocentro.probe_sources --meteoclimatic --aemet
```

Como máximo hace un GET del XML y seis GET AEMET (dos productos con datos y metadatos), sin reintentos. Sin `AEMET_API_KEY` en el entorno no consulta AEMET. Este diagnóstico no utiliza el permiso de ingesta ni lo concede, y no es una tarea del worker. El resultado real, las peticiones de desarrollo y los pendientes se registran en [ESTADO.md](ESTADO.md).
