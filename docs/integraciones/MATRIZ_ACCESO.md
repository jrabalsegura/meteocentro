# Matriz de acceso y viabilidad, fase 0

Comprobación del 14 de septiembre de 2026, 10:25–10:30 UTC. `verified` significa respuesta real del producto indicado, no ingestión ni permiso general para publicar cualquier derivado. Las consultas se hicieron con [el diagnóstico local](../../scripts/diagnose_sources.py), sin conservar respuestas brutas, URL temporales ni claves. La clave AEMET se leyó solo en memoria desde la configuración local de Radar App. No hay aplicación ni trabajo periódico activo.

| Fuente y producto | Vía y autenticación | Resultado real | Cobertura/periodo observado | Conservación y publicación | Estado |
| --- | --- | --- | --- | --- | --- |
| AEMET observación convencional | `GET /opendata/api/observacion/convencional/todas/`, API key; respuesta con URL temporal `datos` y `metadatos` | HTTP 200 en las dos etapas; 10.039 filas y 855 IDs únicos en una consulta | `fint` UTC, fin de la hora observada; 49 estaciones únicas dentro de nuestros cuatro polígonos en esa instantánea. No es un censo ni una cadencia garantizada. | [Nota legal AEMET](https://www.aemet.es/es/nota_legal): reutilización, con atribución, fecha y metadatos; no desnaturalizar. | `verified` |
| AEMET inventario climatológico | `GET /opendata/api/valores/climatologicos/inventarioestaciones/todasestaciones/`, misma clave y segunda URL | HTTP 200; 926 IDs, de los que 55 tienen coordenadas dentro de los cuatro polígonos. Tres registros del inventario tienen coordenadas no interpretables por el diagnóstico. | Metadatos de estación; no prueba emisión actual. | Misma nota legal; conservar procedencia. | `verified` |
| AEMET climatología diaria | `GET /opendata/api/valores/climatologicos/diarios/datos/fechaini/{inicio}/fechafin/{fin}/estacion/{id}`, misma clave | HTTP 200; una fila para `2462` el 4-9-2026, consultada en una ventana de un día. | Resumen por `fecha`, no curva intradiaria; `prec` documentada de 07 a 07. Antigüedad máxima sin comprobar. | Misma nota legal. | `verified` para ese día y estación |
| AEMET histórico intradiario antiguo | No se ha localizado ni probado un producto equivalente en el catálogo revisado. | Sin consulta. | No prometer curvas previas al inicio de nuestro archivo. | Por resolver si aparece producto. | `unsupported` en el diseño actual |
| Meteoclimatic XML actual y catálogo de IDs | `GET https://www.meteoclimatic.net/feed/xml/ES`, sin clave, patrón documentado | HTTP 200; `meteodata` 0.1, 1.540 estaciones. Prefijos observados para los cuatro ámbitos: 89 Madrid, 22 Ávila, 10 Segovia y 22 Guadalajara. | `pubDate` por estación, sensores y unidades. No trae coordenadas en el XML; la provincia por prefijo aún no está contrastada espacialmente. | El sitio enlaza [CC BY-NC-ND 3.0](https://creativecommons.org/licenses/by-nc-nd/3.0/) y [explica restricciones](https://www.meteoclimatic.net/index/wp/cc_es.html). Hay que aclarar si permiten archivo propio, transformación de unidades/agregados y publicación en Meteocentro. | `verified` acceso técnico; `pending_terms` para uso en la app |
| Meteoclimatic RSS | [Patrones documentados](https://wiki.meteoclimatic.net/wiki/RSS); no probado. | No consultado: XML es la vía preferida por su propia documentación. | Pendiente si XML resulta insuficiente. | Mismas restricciones. | `pending_access` |
| Meteoclimatic archivo diario | [Archivo documentado](https://wiki.meteoclimatic.net/wiki/Datos_hist%C3%B3ricos); sin API de descarga automatizada confirmada. | No se ha importado ni descargado. | Máximas, mínimas y precipitación diarias; no reconstruye 5 minutos antiguos. | Aclarar permiso de descarga, conservación y transformación. | `pending_access` + `pending_terms` |
| Wunderground | Ampliación aplazada. | No se probó ni utilizó clave. | No forma parte de esta versión. | Coste y acceso en [estudio existente](../COSTE_Y_ACCESO_DATOS.md). | `deferred_cost` |

La fuente AEMET cumple el requisito de tener al menos un acceso real para el siguiente incremento. Meteoclimatic no se activará como ingesta ni publicación hasta resolver condiciones y coordenadas; acceso HTTP 200 por sí solo no lo autoriza.

## Muestra geográfica mínima

Los siguientes IDs AEMET aparecen en la respuesta actual y caen dentro de los polígonos oficiales del IGN. Son muestras de identidad y coordenadas, no datos meteorológicos versionados ni garantía de continuidad.

| Provincia | AEMET actual | AEMET inventario | Meteoclimatic XML: muestra por prefijo verificado, ubicación espacial pendiente |
| --- | --- | --- | --- |
| Madrid `28` | `3100B`, `3104Y` | `3100B`, `3104Y` | `ESMAD2800000028802B`, `ESMAD2800000028806B` |
| Ávila `05` | `2430Y`, `2444` | `2430Y`, `2444` | `ESCYL0500000005004B`, `ESCYL0500000005229A` |
| Segovia `40` | `2135A`, `2140A` | `2135A`, `2140A` | `ESCYL4000000040196C`, `ESCYL4000000040410B` |
| Guadalajara `19` | `3013`, `3021Y` | `3013`, `3021Y` | `ESCLM1900000019225B`, `ESCLM1900000019208A` |

No se han versionado observaciones reales. Los [fixtures](../../tests/fixtures/SINTETICOS.md) son sintéticos y muestran solo el contrato. Los polígonos se conservan con [procedencia](../../config/provinces.provenance.json), geometría completa para clasificar y una versión reducida únicamente para visualizar.

## Presupuesto inicial de llamadas

Si se consulta AEMET por lote cada 15 minutos: 96 peticiones a la API por día para observaciones; cada petición supone además una descarga `datos` (192 GET HTTP diarios). El inventario diario añade 1 petición API y su descarga. Los metadatos se recuperarán al cambiar el contrato, no en cada ciclo. Con tres reintentos máximos de todas las consultas en un día excepcional, el techo teórico de estas dos tareas sería 388 peticiones API y 776 GET HTTP, antes de importar históricos. Un piloto diario de 30 días para una estación puede caber en una consulta de rango documentada, pero solo se ha probado un día; no presupuestar el volumen hasta medirlo. AEMET publica un límite general de [40 consultas API/minuto](https://opendata.aemet.es/centrodedescargas/faqs), con posibles restricciones por producto; no se ha medido cuota diaria ni disponibilidad sostenida.

Si se autorizase Meteoclimatic, un solo XML nacional cada 15 minutos implicaría 96 GET diarios. Su propia [guía de estaciones](https://www.meteoclimatic.net/index/weatherlink_es.html) indica actualización de 15 minutos, por lo que consultar cada 10 no promete más resolución. Por ahora el presupuesto operativo para esa fuente es **cero**, salvo diagnósticos manuales, al estar `pending_terms`.

Para el mapa se han localizado [WMTS de cartografía ráster y mapa base del IGN](https://www.ign.es/web/es/ign/portal/ide-area-nodo-ide-ign) como candidatos topográfico y claro. `GetCapabilities` respondió HTTP 200 en ambos servicios el 14-9-2026; el primero ofrecía `MTN`/`MTN_Fondo` y el segundo `IGNBase-gris`, `IGNBaseSimplificado` y otros. Falta verificar teselas concretas, atribución visible en la interfaz y capacidad reales en la fase 4. El servicio comunitario de [teselas OSM](https://operations.osmfoundation.org/policies/tiles/) tiene política propia y no se presupone como alojamiento ilimitado. La licencia de la geometría del IGN consta en [config/README.md](../../config/README.md).
