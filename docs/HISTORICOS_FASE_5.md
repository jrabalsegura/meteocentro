# Históricos: contrato, operación y validación de fase 5

## Qué se puede consultar

La ficha enlaza a `/historicos?station=UUID`. Hay periodos de 24, 48 y 72 horas, 7 y 14 días, fechas personalizadas, tabla, gráficos, diarios, meses, años y efemérides. `/diarios` muestra tabla, mapa de mínimos/máximos/totales y extremos de poblaciones comparables. Un gráfico con fechas anteriores al archivo muestra ausencia, nunca una reconstrucción de curvas desde diarios. El CSV AEMET conserva unidades canónicas; la pantalla convierte el viento a km/h.

Todas las lecturas aplican las exclusiones de estación, origen e identidad, el estado del proveedor y `Cache-Control: no-store`. Los orígenes de una estación vinculada se seleccionan explícitamente: el refresco conserva esa selección. Un origen retirado no se sustituye silenciosamente. Los canales separan producto, métrica, unidad, tipo, convención, duración de intervalo y versión del normalizador. Los emplazamientos se muestran y cortan la continuidad de la serie.

| Ruta GET | Parámetros y límites |
| --- | --- |
| `/api/v1/stations/{id}/series` | `from`, `to`, `metric`, `source`, `resolution=raw\|hour\|day`, `limit` 1–10.000, `offset` 0–100.000. Detalle hasta 31 días; agregados hasta 366 días. `next_offset` explícito. |
| `/api/v1/stations/{id}/daily` | Mismo intervalo/origen/métrica; `method=all\|local\|provider`; `resolution=day\|month\|year`. Hasta 366 días en diario y 3.660 en meses/años. Páginas de 1–1.000. |
| `/api/v1/stations/{id}/records` | `metric`, `source`. Efemérides por canal y método, fechas, días disponibles, unidad y cobertura. |
| `/api/v1/stations/{id}/export.csv` | `from`, `to`, `metric`, `source`, `resolution=raw\|day`. Hasta 31/366 días y 10.000 filas; falla con 422 si excede el límite. |
| `/api/v1/daily` | `day=AAAA-MM-DD`, `metric=temperature\|humidity\|rain\|wind_gust`; `province`, `network` opcionales. Hasta 5.000 resúmenes; si se trunca, desactiva extremos. |

Los intervalos son `[from,to)`, con zona obligatoria. Las fechas personalizadas de la pantalla indican **UTC y fin excluido**; el diario civil se calcula en Madrid. `source` puede omitirse solo cuando existe un único origen elegible. Una fuente no elegible devuelve 404; orígenes ambiguos, métrica/resolución/intervalos inválidos, 422. `/records` son efemérides; la tabla de registros usa la página de `/series`.

`availability` presenta primera/última fecha por métrica y resolución y días pendientes de regenerar. Una fecha inicial no garantiza continuidad. Los nulos, porcentajes de cobertura y banderas de calidad siguen visibles. El detalle muestra la calidad original y el instante de recogida; los agregados presentan el método y la procedencia. Las series largas llevan mínimos y máximos, además de medias: no se reduce un pico a una media. El gráfico corta los huecos, no suaviza ni conecta nulos, y permite zoom.

## Métodos y periodos

`local-v1` calcula días civiles `Europe/Madrid` con límites UTC: 23, 24 o 25 horas. Todos los instantes se convierten a UTC antes de restar, incluso cuando PostgreSQL devuelve `ZoneInfo('Europe/Madrid')`. Las medias instantáneas ponderan el tiempo mediante mantenimiento hacia delante hasta la siguiente muestra, **limitado a una cadencia nativa**. Un nulo interrumpe ese mantenimiento; no se extiende una lectura durante un hueco largo. Sin cadencia acreditada no se generan estos agregados. Un intervalo medio conserva su duración: diez minutos medidos cada hora no acreditan una hora completa de viento.

La dirección usa suma vectorial ponderada por duración y velocidad simultánea; calma, falta de velocidad o vector resultante nulo no generan una dirección ficticia. Las rachas son máximos, y los tipos de presión permanecen separados. Se excluyen inválidos y valores físicamente imposibles; las banderas plausibles se conservan sin borrar el extremo.

La cobertura es la duración de intervalos válidos y no solapados dividida por la ventana. Los contadores con límites conocidos solo aportan diferencias consecutivas no negativas del mismo periodo. Base cero exige evidencia explícita `counter_base_zero`; un descenso inesperado, base desconocida o hueco queda señalado. Los incrementos solapados se descartan del cálculo, sin elegir un sensor arbitrario. Un total que cruza un límite no se reparte proporcionalmente. Los móviles no se suman. Los contadores diarios de Meteoclimatic siguen con ventana desconocida: no producen lluvia civil comparable. La intensidad reportada es independiente, nunca inferida silenciosamente de un contador.

La comparación usa `HISTORY_COVERAGE_THRESHOLD` (0,90 inicialmente), unidad, tipo, producto, convención, normalizador y **el mismo corte temporal**. El día en curso se corta a la última hora UTC cerrada y se marca provisional. No se suman resultados de poblaciones distintas. Un diario pendiente de regeneración no participa en extremos. Meses y años consultan diarios del mismo canal; conservan extremos y totales parciales y ponderan medias solo con segundos de cobertura conocidos. No se promedian aritméticamente direcciones ni medias externas de método desconocido.

## Diarios externos AEMET

El importador guarda `method=provider`, separado del cálculo local, con originales, hashes de registro y metadatos, fecha de recogida y `aemet-daily-v1`. No inserta en `observations` ni modifica `latest_observations`. Una corrección conserva la versión anterior en `daily_summary_revisions`; meses, años y efemérides la leen directamente, sin caché antigua.

La lluvia mantiene `AEMET_07_07_UTC`, desde las 07 UTC de la fecha publicada hasta las 07 del día siguiente. Los metadatos se contrastaron el 19-9-2026; el sentido del día pluviométrico está respaldado por el [informe oficial de AEMET sobre Erla](https://www.aemet.es/documentos/es/conocermas/recursos_en_linea/publicaciones_y_estudios/estudios/Informe_Erla-9jun2014.pdf) y el [calendario meteorológico de AEMET](https://www.aemet.es/documentos_d/conocermas/recursos_en_linea/calendarios/cm-2018.pdf). `Ip` conserva el significado de inferior a 0,1 mm y `Acum` el de acumulado de periodo desconocido; ninguno se convierte en cero numérico.

Para las otras métricas, los metadatos identifican una **fecha del proveedor**, pero no acreditan todos los límites diarios de todas las estaciones históricas. Se usa `AEMET_provider_date`, con anclas de indexación de fecha a fecha siguiente, `window_boundaries_verified=false` y el aviso visible «límites diarios no acreditados». Esas anclas no afirman una ventana física 00–24 UTC/Madrid. Las horas reportadas permanecen en UTC como originales, sin inventar su fecha exacta. No entran en comparaciones civiles.

La cobertura temporal externa no viene acreditada: permanece nula. Los récords reportados por AEMET se muestran aparte con cobertura desconocida; las efemérides calculadas exigen el umbral y un día cerrado. La media externa se preserva, sin presumir que sea una media temporal. `presmin`/`presmax` son presión al nivel de referencia de la estación.

Meteoclimatic aporta series de lo recogido localmente desde su incorporación. Su importación diaria anterior y la exportación de derivados quedan **pendientes de acceso/condiciones del producto**, no implementadas ni simuladas como completas. CSV responde 403 para esa red. Wunderground sigue aplazado.

## Importación reanudable

Usar una configuración local con `DATABASE_URL` y la clave AEMET ya autorizada. Estos comandos no despliegan servicios ni encolan automáticamente un archivo masivo:

```sh
backend/.venv/bin/python -m meteocentro.history_maintenance \
  --source UUID_ORIGEN_AEMET --from 2026-08-01 --to 2026-08-31
backend/.venv/bin/python -m meteocentro.worker --provider aemet --once history
backend/.venv/bin/python -m meteocentro.worker --provider aemet --status
```

Cada solicitud admite hasta 30 días ya pasados. Encolar ventanas solapadas, incluso tras completarlas, solo añade los tramos no solicitados: un bloqueo de estación serializa esa operación. Los trabajos usan la cola persistente existente, prioridad 1 frente a 100 de actualidad y 20 de catálogo. `HISTORY_DAILY_HTTP_BUDGET=20` cuenta todos los GET, incluidos sobre, metadatos y datos, además del presupuesto global y su reserva para actualidad. La API pública no importa ni programa.

Las ventanas se confirman con los diarios en una transacción, antes del acuse de la cola. Un reinicio posterior a ese commit usa el cursor confirmado y no repite HTTP. Un fallo previo se puede repetir idempotentemente. La exclusión se comprueba antes de descargar y bajo bloqueo de estación antes de escribir. 204/404 se registran como ventana `unavailable`, sin crear datos o reintentar indefinidamente; un 200 vacío conserva cero filas como resultado consultado. Errores transitorios y cuota reanudan según la cola; falta de clave/metadatos incompatibles conservan la pausa explícita.

Para revisar correcciones del proveedor se reabre expresamente una ventana completada:

```sh
backend/.venv/bin/python -m meteocentro.history_maintenance --refresh-job UUID_TRABAJO
```

`jobs.cursor` e `ingestion_runs.result` conservan fechas, confirmación, filas recibidas, contadores y disponibilidad. No hay panel de administración nuevo en esta fase. Ampliar años de importación sigue requiriendo decidir el alcance.

## Agregación, almacenamiento y retención

La migración `0004_history` transforma observaciones en particiones mensuales y preserva los IDs y valores existentes. **Reescribe la tabla bajo bloqueo**: requiere una ventana de mantenimiento cuando se aplique a una base en uso. La clave primaria y las referencias de últimos valores/revisiones incorporan `observed_at`; la identidad semántica conserva `NULLS NOT DISTINCT`. Hay índices `(source_id,observed_at)` y `(source_id,metric,period_start)` en agregados. Alembic ignora únicamente las tablas y FKs hijas creadas por PostgreSQL, no el esquema lógico.

Un trigger transaccional marca el día afectado y sus vecinos para regenerar correcciones/lecturas tardías, incluyendo las contribuciones de frontera de los productos integrados. La migración también encola los días ya archivados. El worker consume esas marcas, publica hora y día en una transacción y refresca cada hora el día actual, con revisión nocturna de los dos anteriores. Nunca se ejecuta dentro de la API. El bloqueo de estación precede al de la marca para evitar el orden inverso al de la ingesta. Los agregados antiguos permanecen señalados hasta regenerarse.

Se crean particiones para el mes actual y tres futuros. Una partición `DEFAULT` conserva llegadas fuera del calendario. Si ya contiene filas de un mes que faltaba, el mantenimiento conserva esas filas y no intenta moverlas/borrarlas automáticamente: sigue siendo consultable; su redistribución necesita una operación posterior controlada. No se retiran particiones. El downgrade se niega a borrar semántica si ya hay resúmenes/canales nuevos.

```sh
backend/.venv/bin/python -m meteocentro.history_maintenance --rebuild
backend/.venv/bin/python -m meteocentro.history_maintenance --retention-preview
```

La simulación usa `DETAIL_RETENTION_MONTHS=24`, informa filas/tamaño por proveedor y resúmenes ausentes o pendientes. **No existe purga activable** en esta fase. Tener un diario no acredita conservar cada sensor/periodo: antes de implementar borrado habrá que verificar todos los agregados, los permisos y los efectos concretos.

## Reproducción de pruebas

Las pruebas ordinarias usan PostgreSQL dedicado con nombre terminado en `_test` y HTTP sintético:

```sh
backend/.venv/bin/alembic -c backend/alembic.ini upgrade head
backend/.venv/bin/alembic -c backend/alembic.ini check
backend/.venv/bin/pytest -q
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Para rendimiento, configurar `TEST_DATABASE_URL` hacia **otra base de pruebas vacía**, ya migrada:

```sh
backend/.venv/bin/python scripts/measure_phase5.py --sources 20 --keep
```

Genera un año de muestras sintéticas de temperatura cada diez minutos para veinte orígenes y agregados sintéticos regulares. Prueba las consultas de producción; el cálculo meteorológico real se verifica en `tests/test_phase5.py`. Los resultados/planes quedan en `runtime/phase5/annual-benchmark.json`. El ancho medido de una fila de un único sensor no estima el archivo multimétrica real ni garantiza el rendimiento a 52,6 millones de filas.

Para la comprobación visual contra esa base, arrancar API local en 8005 y Vite en 5175 con `VITE_API_PROXY_TARGET=http://127.0.0.1:8005`, luego ejecutar `node scripts/check-history-local.mjs` desde `frontend`. Comprueba 365 diarios, gráfico en escritorio/móvil y mapa diario de veinte orígenes. Bloquea teselas externas para aislar la prueba. La cartografía real ya se validó en fase 4. Capturas y evidencias se conservan fuera de Git en `runtime/phase5/`.
