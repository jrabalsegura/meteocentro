# Contrato observado de AEMET OpenData

Consulta real el 14-9-2026 con una clave local ya existente. Se verificaron por separado observación actual, inventario y un día de climatología. La integración de ingesta continua pertenece a la fase 2.

La [especificación OpenData](https://opendata.aemet.es/dist/) documenta las tres rutas. Cada llamada produjo primero un JSON con `estado`, `descripcion`, `datos` y `metadatos`; las dos últimas son URL temporales descargadas a continuación. Se recibió HTTP 200 en ambas etapas. El diagnóstico valida el host antes de seguir la URL y nunca la imprime. La respuesta de datos requirió aceptar ISO-8859-1 además de UTF-8. Los metadatos describen campos y periodos; hay que conservar su versión o procedencia al normalizar.

| Producto | Campos observados | Regla de significado |
| --- | --- | --- |
| Observación | `idema`, `lat`, `lon`, `fint`, `ta`, `hr`, `vv`, `prec`, `pacutp`, `pres`, `pres_nmar`, entre otros. Muchos opcionales son nulos. | `fint` es UTC. `ta` es instantánea en °C; `vv` media de los 10 min previos en m/s. `prec` y `pacutp` son **dos sensores alternativos** de precipitación de los 60 min previos, en mm: no sumarlos. `pres` es presión de estación; `pres_nmar` es reducida al mar, y no deben mezclarse. Los periodos dependen del campo, no del intervalo de consulta. |
| Inventario | `indicativo`, `nombre`, `provincia`, `latitud`, `longitud`, `altitud`, `indsinop`. | Las coordenadas están en texto sexagesimal con hemisferio, distinto de las coordenadas decimales de observación. El inventario climatológico no es el catálogo completo de estaciones que emiten ahora. |
| Diario | `indicativo`, `fecha`, `tmax`, `tmin`, `tmed`, `prec`, `racha`, `velmedia`, `presMax`, `presMin`, etc. | La muestra de `2462` fue un único resumen de 4-9-2026. Los metadatos de este producto definen `prec` como precipitación diaria **de 07 a 07**, que no se etiquetará como día civil de Madrid sin expresar la ventana. Las horas de extremos y la cobertura necesitan interpretación propia. |

En la consulta de observación de las 10:29 UTC había 10.039 filas y 855 IDs únicos; 49 IDs estaban dentro de las cuatro provincias por coordenadas. El inventario devolvió 926 registros; 55 tienen coordenadas dentro de los polígonos. Los totales son instantáneas de productos distintos, no cobertura estable ni equivalencia de estaciones. `prec` nulo apareció en 278 de las 10.039 filas, y `pres` nulo en 6.397: nulo no equivale a 0. Se detectaron tres coordenadas no interpretables en el inventario; esos registros deben ir a revisión si interesan. La comparación de nombre de provincia con geometría puede discrepar, y prevalecerá la geometría verificada.

La [nota legal de AEMET](https://www.aemet.es/es/nota_legal) permite reutilización comercial y no comercial con atribución, mantenimiento del significado, fecha de actualización y metadatos aplicables. La clave local respondió correctamente. Su campo JWT `exp`, inspeccionado sin revelar el token, indica **31-10-2026 11:22:47 UTC**; la validez efectiva puede cambiar antes y debe vigilarse. La [FAQ de OpenData](https://opendata.aemet.es/centrodedescargas/faqs) publica 40 consultas API/minuto de límite general y advierte restricciones adicionales por producto.

No se probaron meses o años de importación, antigüedad máxima, disponibilidad sostenida ni un archivo horario antiguo. Tampoco se guardó ninguna respuesta real en Git. Los IDs de muestra y su clasificación figuran en la [matriz](MATRIZ_ACCESO.md).

## Integración de fase 2 — 16-9-2026

Se han vuelto a resolver los dos productos y sus metadatos reales. Las reglas anteriores siguen siendo válidas. La cabecera `api_key` fue aceptada para el sobre inicial; las descargas de datos y metadatos no recibieron esa cabecera. El inventario conserva coordenadas sexagesimales y la observación usa decimales. No se han identificado nuevos centinelas numéricos autorizados: los nulos se conservan y los valores no interpretables se rechazan o marcan, sin asignarles un significado supuesto.

La instantánea actual tuvo 10.479 registros y el inventario 926. En el ámbito hubo 49 IDs actuales y 55 de inventario, con una unión de 59. En los intervalos entre registros consecutivos de la respuesta nacional se observaron 9.633 separaciones de una hora y dos de dos horas; esto respalda una cadencia horaria observada y también demuestra huecos. No es una promesa de cobertura o frescura por estación.

Los hashes canónicos de `campos` usados por el normalizador fueron `56ea843b6f034efdd31a10ea4c05598cd41a9e22322434dd7fd91558cae09285` para observación y `7d3fcd3b878506991baa20cdb9906f0d8976104d8ede9618f44bc2cb1741d281` para inventario. Son referencias de metadatos, no claves. El worker guarda las definiciones y su hash en PostgreSQL, acepta ISO-8859-1 y verifica las unidades y los periodos consumidos; no guarda las URL temporales.

La prueba real y el contraste de las cuatro provincias figuran en [ESTADO.md](../ESTADO.md). La política de cuotas, pausas, recuperación y comandos está en [OPERACION_FASE_2.md](../OPERACION_FASE_2.md). El acceso climatológico diario permanece verificado por fase 0, sin importador nuevo en fase 2.

## Importación de fase 5 — 19-9-2026

El producto diario se importa ahora por ventanas reanudables de hasta 30 días, exclusivamente a resúmenes del proveedor. El piloto real de `2462`, del 1 al 30 de agosto de 2026, recibió treinta fechas en tres GET; guardó 180 resúmenes por métrica y ninguna observación intradiaria. Los metadatos reales tienen hash `9361b3e7c94732382081bd214bed104d0c857da855fd949215a517ee27b11f63`.

Se preservan `Ip`, `Acum`, nulos, cobertura desconocida y horas reportadas en UTC. Precipitación conserva el día pluviométrico 07–07 UTC. Los otros campos mantienen la fecha publicada con límites diarios sin acreditar para todas las estaciones: no se presume día civil ni se asignan fechas exactas a horas ambiguas. La presión extrema se refiere al nivel de la estación. Reglas completas, contratos y limitaciones en [HISTORICOS_FASE_5.md](../HISTORICOS_FASE_5.md). El archivo horario anterior sigue sin acceso acreditado; este piloto no demuestra años de cobertura ni disponibilidad sostenida.
