# Fase 4: mapa y estaciones

Implementación local del 19 de septiembre de 2026. No implica publicación, despliegue ni recogida permanente. Se conserva el alcance AEMET/Meteoclimatic y el archivo privado de fase 3.

## Uso

- `/` abre el mapa sobre Madrid ciudad, centrado en longitud `-3.70765`, latitud `40.42437` y zoom `10.69`, según la vista elegida por el usuario el 21-9-2026. Un parámetro `view` válido conserva su centro/zoom; si falta o es inválido se usa Madrid. «Ver las cuatro provincias» ajusta el mapa a la unión de los polígonos provinciales. Los botones cambian temperatura, humedad, velocidad del viento y precipitación del intervalo. La posición no se reinicia al cambiar variable ni al refrescar. El encuadre inicial no aplica filtros de red o provincia.
- Cada número es una observación, con escala de color y unidad visibles sobre el mapa y explicación del periodo debajo. Los marcadores próximos o coincidentes se separan automáticamente cuando hay espacio libre, a cualquier zoom, con una línea hacia su ubicación publicada. Solo se desplaza su representación visual. Se prueban disposiciones en filas y columnas, con separación de 72 × 44 px, respeto a otros marcadores/grupos y un desplazamiento máximo de 96 px por estación; los márgenes evitan sacar números del mapa o invadir la franja de controles superior.
- Si no caben, «N est.» representa un grupo de **N estaciones**, nunca una temperatura media. Al pulsarlo se amplía; para coordenadas idénticas o zoom 17–18 se ofrece directamente «Ver N est.» con nombre, valor, fuente, ID y hora de cada estación. La lista permite abrir sus fichas y se retira al cambiar datos/filtros/variable/encuadre para no conservar estaciones excluidas ni lecturas anteriores. La cercanía visual no fusiona estaciones ni sustituye los criterios de revisión del catálogo.
- Seleccionar un número abre el resumen: nombre, provincia, altitud disponible, valor, hora, antigüedad, periodo y origen. «Ver ficha completa» abre `/estaciones/:UUID`. Cada variable conserva su propia fuente; el selector permite inspeccionar otro origen elegible. Las presiones de estación y al nivel del mar tienen tarjetas distintas.
- La tabla de `/estaciones` y la tabla bajo el mapa comparten la misma consulta y filtros. Se puede ordenar por nombre, valor y hora, paginar de 50 en 50, abrir una ficha o seleccionar una estación en el mapa. Los nulos se muestran como «—», siempre al final al ordenar.
- Desde el ajuste del 23-9-2026, la casilla «Ocultar en el mapa estaciones con más de 1 h sin actualizar» viene marcada. Solo representa observaciones de la variable elegida con edad entre 0 y 3.600 s, inclusive; también oculta estaciones sin esa lectura. Las filas permanecen en el listado, con marca ámbar y «Más de 1 h sin actualizar» si superan la hora, y aviso «Oculta en el mapa» cuando corresponde. Desmarcarla permite ver todos los marcadores y se conserva al recargar mediante `hide_old=0` en la URL. No se envía ese parámetro a la API ni se limita el archivo histórico. Cada refresco ordinario vuelve a evaluar la edad, aunque el proveedor haya vuelto a recoger la misma observación. Los contadores muestran por separado la población del listado y la del mapa; «Estado según fuente» y los extremos mantienen los umbrales operativos de cada red.
- Provincia, red, frescura, variable, búsqueda, origen seleccionado, estación y centro/zoom viajan en la URL. Volver desde la ficha o recargar conserva ese contexto. El mapa representa la población filtrada de las cuatro provincias; moverlo **no limita silenciosamente** la tabla al rectángulo visible.
- Buscar por nombre, municipio o ID centra el mapa en la primera coincidencia del orden actual del listado con zoom moderado (12), sin abrir la ficha ni quitar el foco del buscador. El encuadre se aplica al recibir los resultados; si se busca desde la tabla, al volver al mapa. Refrescar, cambiar de variable o borrar la búsqueda conserva la vista. Los enlaces con `view` válido mantienen su encuadre al abrirlos. Sin resultados o sin coordenadas en la primera coincidencia, el mapa no se mueve. El filtro de una hora sigue vigente: para ver una coincidencia antigua, desmarcarlo.
- En móvil, los filtros arrancan plegados. La ficha ocupa como máximo el 48 % de la altura de la ventana, se desplaza por dentro y permite seguir usando el mapa. Escape cierra la selección y devuelve el foco a los filtros. Marcadores, grupos, controles, enlaces y ordenación son operables por teclado. La tabla tiene desplazamiento horizontal propio.
- Se consulta la API propia cada 60 segundos solo con la pestaña visible; al volver se refresca. La fecha de consulta y la de recogida no sustituyen la hora de observación. Una exclusión deja de aparecer en la siguiente lectura del servidor; una pantalla abierta se actualiza como máximo en el siguiente ciclo visible. No hay WebSocket ni invalidación instantánea de pantallas ya abiertas.
- Si falla la cartografía o WebGL, siguen disponibles tabla, filtros y fichas. Si falla la API, se retiran los resultados anteriores de la consulta y se ofrece reintento. Se distinguen selección vacía, fuente pendiente/pausada, ausencia de dato y observación desactualizada.

## Contrato público

`GET /api/v1/map` admite:

| Parámetro | Contrato |
| --- | --- |
| `metric` | `temperature`, `humidity`, `wind_speed`, `rain`; reservados para datos compatibles disponibles: `wind_gust`, `rain_rate` |
| `province` | Repetible: `05`, `19`, `28`, `40` |
| `network` | Repetible: `aemet`, `meteoclimatic` |
| `freshness` | `all`, `fresh`, `stale`, `unknown`, `historical_only` |
| `bbox` | Opcional: oeste,sur,este,norte en WGS84; finito, ordenado y dentro de los límites geográficos |
| `q` | Hasta 120 caracteres; nombre, municipio verificado si consta en metadatos del origen, o ID externo. `%` y `_` se tratan literalmente |
| `limit` | 1–5.000; por defecto 2.000 |

Devuelve estaciones únicas, orígenes elegibles, lectura seleccionada con procedencia/semántica, contadores y extremos. La proyección utiliza **una consulta SQL**, sin consultas por marcador ni HTTP a proveedores. El límite duro de exploración es 5.000 estaciones; `truncated` y `total_is_lower_bound` advierten cuando el total es incompleto. No se anuncian extremos de poblaciones truncadas. La interfaz pide hasta 2.000 y solicita acotar los filtros al superar ese límite.

`GET /api/v1/stations/:UUID/current` devuelve las últimas lecturas por métrica y origen con la misma elegibilidad, en una consulta. Una estación excluida, en revisión o sin orígenes publicables devuelve 404. Las exclusiones de identidad y de origen también se aplican. Todas las respuestas `/api/v1/*` llevan `Cache-Control: no-store`. No se añade una caché pública ni una migración.

La localidad no existe como campo independiente en los catálogos actuales: se busca también dentro del nombre y se admite `source_metadata.municipality` cuando haya evidencia. La ficha muestra «no consta» cuando falta; no se geocodifica ni deduce una localidad. La búsqueda explícita por ese metadato tiene prueba PostgreSQL.

## Tiempo, unidades y comparabilidad

- AEMET: reciente hasta **5.400 s (90 min)**, por su cadencia horaria observada en fase 2. Meteoclimatic: **2.700 s (45 min)**, tres intervalos de recogida de 15 min; tolerancia operativa, no garantía de cadencia de todas sus estaciones. Se usan los umbrales persistidos del proveedor, con estos valores por defecto. Una hora futura no se considera reciente.
- Preferencia por métrica: dato utilizable reciente, después utilizable desactualizado; dentro de cada categoría, AEMET antes de Meteoclimatic, después hora más reciente e identidad estable. Se indica el origen alternativo. La selección del mapa fija en el resumen el origen que aportaba su número. La ficha conserva todas las fuentes consultables.
- Viento en km/h, convertido desde m/s multiplicando por 3,6. AEMET aporta media de los diez minutos anteriores; Meteoclimatic, valor instantáneo. Se conservan esas diferencias. Dirección: grados de **procedencia** desde el norte en sentido horario; la flecha de la ficha apunta al destino (ángulo + 180°), solo con dirección de la misma observación/origen.
- La lluvia del mapa es el incremento AEMET de los sesenta minutos anteriores. El contador diario Meteoclimatic y sus extremos diarios se muestran aparte en la ficha con `provider_day_timezone_unknown`: no se inventan medianoches ni se comparan con intervalos horarios. No se suman contadores.
- La racha del intervalo y la intensidad aún no tienen datos integrados; no hay controles que simulen esas capacidades ni se sustituye racha por máxima diaria. Las mínimas y máximas diarias sin periodo validado no se ofrecen en el mapa. Gráficos, diarios cerrados e históricos quedan en fase 5.
- Los extremos usan solo datos utilizables recientes de una misma unidad, tipo, convención e inicio/fin del periodo. Con periodos distintos se explica la ausencia de comparabilidad. Para instantáneas se muestra el rango real de horas de observación: no se anuncian mediciones simultáneas ni récords diarios. Se muestra cobertura sobre la población filtrada. Flags de plausibilidad impiden usar el valor en la proyección.
- Las horas visibles usan `Europe/Madrid`, con fecha y abreviatura de zona (CET/CEST). El detalle también conserva la hora original UTC, producto, recogida, antigüedad y umbral. Los ceros no se confunden con ausencia.

## Cartografía y dependencias

MapLibre GL JS **6.10.0**, bloqueado en `package-lock.json`. La revisión de npm detectó un aviso de saneamiento HTML en la versión inicialmente ensayada; se actualizó antes de la validación final y `npm audit` quedó sin vulnerabilidades. Los textos de marcadores se escriben con `textContent`, los de fichas con React.

Fondos [WMTS oficiales del IGN](https://www.ign.es/web/es/ign/portal/ide-area-nodo-ide-ign): `mapa-raster` / `MTN` y `ign-base` / `IGNBaseTodo`, matriz `GoogleMapsCompatible`, tesela JPEG 256 px. `GetCapabilities` de MTN y teselas reales comprobados. Atribución IGN/SCNE siempre visible, más procedencia de los límites versionados. [Documentación de MapLibre](https://maplibre.org/maplibre-gl-js/docs/).

Opcionalmente se pueden configurar `VITE_TOPO_TILES_URL` y `VITE_LIGHT_TILES_URL` al construir el frontend (plantillas `{z}`, `{x}`, `{y}`). Se conserva la atribución IGN para los fondos predeterminados: cambiar de proveedor requiere ajustar su atribución/condiciones en `WeatherMap.tsx`, no solo la URL. No introducir claves meteorológicas en ninguna variable `VITE_*`. No hay descarga masiva, servicio cartográfico de pago ni geolocalización.

## Validación efectivamente ejecutada

Entorno: macOS 26.6.2, Apple M3, 24 GiB RAM, Chrome 153.0.8010.50 sin interfaz, ventanas 1.440 × 1.000 y 390 × 844; Node 26.3.0 local (avisa frente al Node 24.21.0 fijado para CI/Compose), Vite 7.3.6, PostgreSQL 17 local temporal en `127.0.0.1:55436`, base exclusiva `meteocentro_phase4_test`. No se alteraron las bases de desarrollo ni las de los pilotos. Al terminar se detuvieron API, Vite y PostgreSQL temporales; la base de pruebas quedó sin estaciones tras la suite final.

| Comprobación | Resultado |
| --- | --- |
| Suite previa + nuevos casos de fase 4 | 111 pruebas correctas en la ejecución completa final, incluidas 23 de fase 4 |
| Restricciones/exclusiones | Estación, origen, identidad, proveedor deshabilitado, revisión y commit desde otra sesión: mapa, búsqueda, listado, ficha y últimos valores dejan de exponerlos |
| Semántica | Cero/nulo, instante antiguo con recogida reciente, futuro, valores inválidos, preferencia/fallback, viento ×3,6, lluvia de distinto intervalo, truncamiento y parámetros inválidos |
| SQL | Una sentencia de mapa con múltiples estaciones; sin N+1 |
| Playwright offline | 7 recorridos correctos: navegación/URL/ordenación, 390 px/teclado, error API/cartografía/exclusión/recuperación, 2.000 puntos, pausa/recuperación de pestaña, origen retirado y conservación del foco al refrescar |
| API con 2.000 estaciones sintéticas | 6 lecturas: 217,7 / 220,6 / 194,6 / 201,8 / 215,2 / 198,5 ms; mediana de las cinco posteriores: **201,8 ms**; respuesta JSON 2.009.894 bytes sin compresión |
| Navegador con DTO y teselas simulados | Datos útiles **488 ms**; cambio de variable **439 ms**; 41 símbolos agrupados, **0 solapamientos**; dos tareas principales de 50 y 52 ms durante la carga/cambio |
| Navegador + API PostgreSQL + teselas IGN reales | Datos útiles **933 ms**; cambio **466 ms**; 24 teselas MTN y 15 IGNBaseTodo HTTP 200 entre escritorio y móvil; cero errores JavaScript; sin desbordamiento de página a 390 px; panel inferior 405,1 px en ventana de 844 px |
| Controles finales | Ruff, formato, `git diff --check` y Alembic `check` correctos; sin nombres de claves ni URL PostgreSQL en el bundle |
| Compilación | `npm run build` correcto; MapLibre se carga separado, 388 kB gzip; Vite avisa de chunk >500 kB sin comprimir |

Los tiempos de navegador se miden desde la navegación hasta el primer marcador visible con los datos, y desde pulsar otra variable hasta pintar sus marcadores. Son mediciones locales de desarrollo, **sin ralentización de CPU/red**, con el sistema de archivos y recursos potencialmente calientes. El objetivo de <3 s se cumple en estas muestras; no es un percentil, garantía de primera visita, ni ensayo de producción/red móvil. La carga total de teselas se verifica aparte: no se presenta el primer marcador como la finalización de toda la cartografía. El script de API usa TestClient y PostgreSQL real; el recorrido manual de navegador sí atraviesa Vite → HTTP API → PostgreSQL.

Las 2.000 estaciones tienen nombres `SINTÉTICA ...` y producto `synthetic_phase4`; no equivalen a inventario real ni se insertan en desarrollo. Capturas y contadores locales están en `runtime/phase4/` y `frontend/test-results/`, ignorados por Git.

### Contraste de fuente archivada

24 comparaciones numéricas y de hora entre normalizador → nuevo DTO de API y las respuestas AEMET del piloto existente; transacción de comprobación revertida. Ejemplos:

| Provincia | ID | Hora original UTC del 16-09-2026 | Temperatura |
| --- | --- | --- | --- |
| Ávila | 2430Y | 05:00 | 12,0 °C |
| Guadalajara | 3013 | 07:00 | 12,8 °C |
| Madrid | 3100B | 07:00 | 16,9 °C |
| Segovia | 2135A | 06:00 | 11,5 °C |

Se contrastaron temperatura, humedad, viento, ambas presiones y lluvia, incluidos nulos. Las muestras se consideran desactualizadas. **Es una reproducción offline de datos archivados**, no nueva integración en vivo; no se auditó otra fecha de recogida ni se gastó cuota meteorológica. Los pilotos de origen siguen siendo los de fases 2 y 3.

## Repetir las comprobaciones

Desde el repositorio, con una base **desechable** terminada en `_test` y `TEST_DATABASE_URL` en el entorno:

```sh
backend/.venv/bin/pytest tests -q
backend/.venv/bin/ruff check backend/src tests/test_phase4.py scripts/measure_phase4.py
backend/.venv/bin/python scripts/measure_phase4.py
```

El benchmark exige base sin estaciones y esquema migrado. `--keep` deja sus datos sintéticos únicamente para inspección; no ejecutarlo concurrentemente con pytest, que vacía la base de pruebas.

Desde `frontend`:

```sh
npm ci
npm run build
npm run test:e2e
```

Playwright usa Chrome instalado en macOS y Chromium instalado por el trabajo CI en Linux. Su configuración inicia Vite si no hay ya una vista previa en el puerto 5174. Las pruebas ordinarias simulan tanto API como teselas y no dependen de Internet. La suite de backend comprueba la exclusión real; la de navegador comprueba su reflejo visual con respuestas simuladas. El trabajo CI incorpora ambas suites, sin añadir despliegue automático. Sus resultados remotos se consultan en la pull request; los resultados documentados arriba son locales.

Comprobación manual con red cartográfica, separada de CI: poblar la base temporal con `measure_phase4.py --keep`, arrancar API local y Vite en 5174 y ejecutar `node frontend/scripts/check-map-local.mjs` desde la raíz. Consulta exclusivamente la API local para meteorología y los WMTS para cartografía. No ejecutar simultáneamente pruebas que borren esa base. Requiere un catálogo de prueba de 2.000 estaciones; no modificar la URL para usar un servicio remoto.

Siguiente fase: series y agregados de fase 5. No se han añadido radar, pronósticos, administración, exportaciones ni despliegue.
