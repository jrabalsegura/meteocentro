# Contrato de Meteoclimatic

Actualización: 16 de septiembre de 2026. La precisión pública **a minutos está aceptada por el usuario**. La integración de fase 3 cubre el archivo local privado no comercial; sus comprobaciones reales y cifras están en [ESTADO.md](../ESTADO.md).

## Acceso y uso adoptado

El [aviso legal](https://www.meteoclimatic.net/index/wp/legal_es.html) aplica la licencia también a la información meteorológica aportada por los usuarios, exceptuando los datos personales de registro. Remite a [sus condiciones Creative Commons](https://www.meteoclimatic.net/index/wp/cc_es.html) y a [CC BY-NC-ND 3.0](https://creativecommons.org/licenses/by-nc-nd/3.0/legalcode). La licencia permite reproducción y colecciones bajo sus condiciones, exige atribución y uso no comercial y restringe compartir adaptaciones.

**Decisión de aplicación de esa licencia al alcance actual:** usar el XML documentado y las coordenadas de las fichas públicas para un archivo privado local no comercial, conservar los valores reportados y su procedencia, y mostrar atribución y licencia. Las conversiones numéricas son representaciones internas de esos mismos datos; no se alteran los originales conservados. Esta es una interpretación del alcance publicado, no un permiso individual obtenido de Meteoclimatic. No se contactó al proveedor. No constituye autorización para comercialización o publicación de productos derivados: esas decisiones se revisarán antes de la exposición prevista en fase 7. La falta de un permiso adicional ya no se usa como bloqueo general para este uso local.

`METEOCLIMATIC_TERMS_REFERENCE` registra el aviso legal, la licencia y ese ámbito. Sigue siendo obligatoria para habilitar el worker y no equivale por sí misma a una autorización. `.env.example` queda desactivado para nuevas instalaciones; la configuración privada de este proyecto se ha activado para el alcance anterior, sin iniciar un servicio permanente.

## Productos y semántica

El [XML meteodata 0.1 documentado](https://www.meteoclimatic.net/index/wp/xml_es.html) se obtiene por HTTPS en `https://www.meteoclimatic.net/feed/xml/ES`, sin clave. Su patrón nacional sigue la [documentación RSS](https://wiki.meteoclimatic.net/wiki/RSS). Un lote sirve tanto para observaciones como para detectar identidades. Los prefijos `ESMAD28`, `ESCYL05`, `ESCYL40`, `ESCLM19` seleccionan candidatos; no sustituyen la clasificación geográfica.

Se usa `pubDate` de **cada estación**, con zona explícita, y se conserva también la recogida. UTF-8, ASCII, ISO-8859-1 e ISO-8859-15 están admitidos; Latin-9 está verificado en el feed real. Se prohíben DTD/entidades externas y se controlan tamaño, tiempo y unidades. La negociación acepta `application/xml` y `text/xml`: limitarla al primero produjo HTTP 406 en el diagnóstico inicial.

| Campo | Representación | Precaución |
| --- | --- | --- |
| `temperature.now`, `humidity.now` | Instantáneos, °C y % | Vacío/ausente es nulo; cero es válido |
| `wind.now`, `wind.azimuth` | Viento actual en m/s y dirección en grados | Conservar valor original en km/h; no atribuir ventana de promedio no indicada |
| `barometre.now` | `pressure_sea_level`, hPa, tipo `sea_level_pressure` | Es presión relativa reportada, no presión de estación ni certificación de calibración |
| `rain.total` | `rain_daily`, mm, tipo `daily_counter` | Foto del contador diario reportado; nunca sumar sucesivas consultas |
| `min/max` de temperatura, humedad, presión y máximo de viento | Nombres separados y tipos `daily_minimum` / `daily_maximum` | No sustituyen valores actuales ni acreditan hora de ocurrencia del extremo |
| `QOS` | Calidad original del proveedor | El valor cero no invalida una estación |

La referencia de presión se apoya en la tabla oficial de [barómetro](https://wiki.meteoclimatic.net/wiki/Bar%C3%B3metro_fuera_de_rango), que define BAR/DHBR/DLBR como presión relativa actual/máxima/mínima, y en la definición de [presión relativa](https://wiki.meteoclimatic.net/wiki/Presi%C3%B3n_atmosf%C3%A9rica_relativa). No se descargan plantillas de subida ni claves de terceros.

La [documentación de horarios](https://wiki.meteoclimatic.net/wiki/Los_horarios_%28UTC%2C_CET%2C_CEST%2C_Horario_Civil_etc%29) recomienda UTC, pero admite día civil. **El XML no identifica el horario diario configurado de cada estación.** Por eso los contadores/extremos llevan `period_basis=provider_day_timezone_unknown` en la métrica y no se inventan `period_start/end`. Son instantáneas a la hora de publicación, no intervalos medidos ni diarios cerrados comparables. Se conservan los campos originales en `quality.reported_fields`, `daily_period` y `pressure_reference=sea_level_relative`. Un reinicio del contador, incluido cero, se guarda sin sumar ni derivar diferencias. Los agregados de fase 5 deberán excluir estos contadores de cualquier suma de incrementos y no suponer medianoche UTC común.

Cada observación conserva autor público si el XML lo aporta, URL de ficha y licencia. API y pantalla muestran atribución. Se mantiene el original numérico al convertir unidades. Normalizador: `meteoclimatic-current-v2`.

## Coordenadas y catálogo público

Las fichas `https://www.meteoclimatic.net/perfil/{ID}` muestran latitud, longitud y altitud. Se leen exclusivamente esos metadatos, verificando que el encabezado corresponda al ID pedido. No se geocodifican nombres ni se consultan endpoints internos del mapa. No se recogen formularios, imágenes, datos de registro o contactos.

Se consultó [robots.txt](https://www.meteoclimatic.net/robots.txt): no excluía `/perfil/`. El worker lo comprueba y conserva 24 horas; una prohibición de fichas evita sus peticiones. La exclusión de `/feed/` para rastreadores no se usa para explorar páginas: el XML se consume únicamente como el producto máquina expresamente documentado por el proveedor.

El trabajo `catalog` consulta hasta 16 fichas pendientes por ejecución, con cuotas y exclusiones del worker común; cachea coordenadas 30 días y revisa errores de ficha tras 24 horas. Todas las llamadas, incluidos robots y fallos, consumen presupuesto persistente. HTTP 403/429 y problemas de transporte usan la pausa/reintento del worker. Una ficha inexistente o ilegible se aísla sin bloquear el resto. Las coordenadas manuales prevalecen; un cambio relevante de ubicación automática genera revisión e historial sin mover la posición canónica.

Se registra precisión `minute`, URL, fecha y método. Desde la petición expresa del usuario del 21-9-2026, la aceptación y provincia se calculan con el punto publicado sobre los polígonos IGN. La provincia puede ser aproximada junto al límite; se muestran esas estaciones conservando la precisión a minutos. Se retira el bloqueo anterior que exigía contener todo un rectángulo de ±1 minuto en una sola provincia. El prefijo no sustituye a las coordenadas ni sitúa automáticamente un punto fuera del ámbito.

## Fuera de alcance y validación

El [archivo histórico](https://wiki.meteoclimatic.net/wiki/Datos_hist%C3%B3ricos) contiene resúmenes diarios; su descarga automatizada y condiciones específicas siguen sin verificarse. No permite reconstruir curvas intradiarias anteriores a nuestro archivo. RSS no aporta una capacidad necesaria y no se implementa.

Las pruebas ordinarias usan fixtures inventados y PostgreSQL real. El piloto separado utiliza XML y fichas reales, con presupuesto acotado, sin insertar fixtures en su base ni publicar datos. Véanse [operación](../OPERACION_FASE_3.md) y [resultados efectivos](../ESTADO.md). La red no se declara validada por fixtures solamente; tampoco se presenta un piloto corto como garantía de cobertura exhaustiva o disponibilidad sostenida.
