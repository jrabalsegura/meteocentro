# Fuentes y límites de la verificación

Consulta realizada el 9 de septiembre de 2026. Se utilizaron páginas de los propios proyectos, proveedores y organismos. La consulta de documentación acredita lo que publican esas páginas; no acredita acceso con una clave concreta ni disponibilidad continua de sus servicios.

## Referencia de producto

- **S1** [Suremet Red Meteo](https://suremet.es/). Página revisada y mapa inspeccionado en navegador: fondo topográfico, marcadores numéricos, controles de variables y resumen de estaciones. La paleta y organización descritas en el plan son una propuesta propia inspirada en esa referencia.
- **S2** [Suremet ficha de una estación](https://suremet.es/estacion.php?id=p04m093e01). Referencia de valores, metadatos y accesos rápidos por periodos; no se reutilizan sus observaciones en el proyecto.
- **S3** [Suremet histórico](https://suremet.es/historico.php). Selección de estación y fechas.
- **S4** [Suremet datos diarios](https://suremet.es/diario.php). Consulta de resúmenes por día.
- **S5** [Suremet efemérides](https://suremet.es/efemerides.php). Referencia del apartado de extremos históricos. El plan limita los récords a la cobertura de nuestro archivo.

## Proveedores meteorológicos

- **S6** [AEMET OpenData](https://opendata.aemet.es/centrodedescargas/inicio) y [acceso para desarrolladores](https://opendata.aemet.es/dist/index.html). Portal oficial y acceso a documentación. La interfaz dinámica de desarrolladores no proporcionó aquí un contrato completo legible; se verificará con sus metadatos en fase 0.
- **S7** [Catálogo de productos AEMET](https://opendata.aemet.es/centrodedescargas/productosAEMET) y [observaciones horarias](https://www.aemet.es/es/eltiempo/observacion/ultimosdatos). Fundamentan la distinción entre observación actual, inventario climatológico y productos diarios o mensuales. No se ha medido su cobertura por provincia.
- **S8** [Nota legal de AEMET](https://www.aemet.es/es/nota_legal). Referencia para condiciones de uso y atribución aplicables al producto que se integre.
- **S9** [Meteoclimatic formato XML meteodata](https://www.meteoclimatic.net/index/wp/xml_es.html). Describe datos de estación, publicación, sensores, unidades e indicadores de calidad. Se debe comprobar el endpoint efectivo y la presencia real de cada campo.
- **S10** [Meteoclimatic datos históricos](https://wiki.meteoclimatic.net/wiki/Datos_hist%C3%B3ricos). Describe el archivo diario de extremos y precipitación. No documenta por sí solo una API pública de curvas intradiarias antiguas.
- **S11** [Meteoclimatic Creative Commons](https://www.meteoclimatic.net/index/wp/cc_es.html) y [explicación en su FAQ](https://wiki.meteoclimatic.net/wiki/%C2%BFQue_significa_que_Meteoclimatic_est%C3%A9_acogido_a_Creative_Commons%3F). Sus textos mencionan restricciones de uso y transformación, y no bastan para afirmar una autorización general para esta app. Resolver el uso concreto de feeds, archivo y derivados en fase 0; no es una conclusión jurídica sobre todos los datos de la red.
- **S12** [The Weather Company Location Service Near](https://developer.weather.com/docs/openapi/location-service-near-3-0). Documenta búsqueda por proximidad y hasta diez resultados; remite los límites de llamadas al acuerdo de acceso. Sustenta que el descubrimiento por malla tendrá cobertura acotada, no exhaustividad garantizada.
- **S13** [The Weather Company PWS Current Observations](https://developer.weather.com/docs/openapi/pws-current-observations-2-0). Observación actual por identificador de estación con autenticación por API key.
- **S14** [The Weather Company PWS Historical](https://developer.weather.com/docs/openapi/pws-historical-2-0). Productos históricos con distintas resoluciones y formatos de consulta. La disponibilidad para la cuenta del usuario queda por verificar.
- **S15** [The Weather Company Getting Started](https://developer.weather.com/docs/getting-started). Base del servicio y obtención de acceso. No se ha contratado una suscripción; la revisión de tarifas públicas está en S26 y en el documento de coste.
- **S24** [Meteoclimatic RSS](https://wiki.meteoclimatic.net/wiki/RSS). Consultas por patrones geográficos o estación y formato de datos integrado; referencia complementaria al XML.
- **S25** [Meteoclimatic horarios](https://wiki.meteoclimatic.net/wiki/Los_horarios_(UTC,_CET,_CEST,_Horario_Civil_etc)). Justifica comprobar la convención temporal de cada producto y no atribuir automáticamente sus resúmenes al día civil de Madrid.
- **S26** [The Weather Company paquetes y precios](https://www.weathercompany.com/weather-data-apis/weather-data-apis-packages-pricing/) y [contenido del paquete Standard](https://developer.weather.com/docs/standard-weather-data-package). Precio publicado, periodo de contratación, volumen y productos; no constituyen una oferta personalizada ni garantizan un archivo histórico largo. Véase [coste y acceso](COSTE_Y_ACCESO_DATOS.md).
- **S27** [Weather Underground, claves personales PWS](https://support.weather.com/s/article/Understand-and-Manage-Your-Personal-Weather-Station-PWS-API-Keys-WeatherUnderground?language=en_US). Ayuda oficial revisada en navegador: requisito de aportar datos desde una estación y límites de llamadas. Distingue la clave de consulta de la clave de subida de una estación.
- **S28** [AEMET OpenData, preguntas frecuentes](https://opendata.aemet.es/centrodedescargas/docs/FAQs130917.pdf). Documento versión 1.4, fechado el 28 de julio de 2025. Confirma la gratuidad de OpenData y explica cómo solicitar su clave.

## Implementación y cartografía

- **S16** [Podman Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html) y [uso básico](https://docs.podman.io/en/latest/markdown/podman-quadlet-basic-usage.7.html). Referencia para rutas de unidades, arranque y funcionamiento con systemd. Validar las opciones contra la versión instalada.
- **S17** [MapLibre GL JS](https://maplibre.org/maplibre-gl-js/docs/). Biblioteca propuesta para el mapa; la selección de proveedor cartográfico se hace por separado.
- **S18** [Apache ECharts](https://echarts.apache.org/en/index.html). Biblioteca propuesta para gráficos.
- **S19** [FastAPI](https://fastapi.tiangolo.com/). Framework propuesto para la API.
- **S20** [React](https://react.dev/). Biblioteca propuesta para interfaz.
- **S21** [PostgreSQL particionado de tablas](https://www.postgresql.org/docs/current/ddl-partitioning.html). Referencia para organizar un archivo creciente; la decisión y las mediciones son propias del proyecto.
- **S22** [OpenStreetMap política de teselas](https://operations.osmfoundation.org/policies/tiles/). Referencia si se utiliza su servicio público estándar. Esta política no sustituye las condiciones de otros proveedores ni autoriza descargar mapas masivamente.
- **S23** [IGN y CNIG límites administrativos](https://centrodedescargas.cnig.es/CentroDescargas/limites-municipales-provinciales-autonomicos). Fuente propuesta para los polígonos de provincia. El dataset y su versión concreta se descargarán y registrarán en fase 0.

## Aspectos no comprobados todavía

No se han probado claves AEMET o WU, feeds provinciales contra contratos de producción, importaciones reales de históricos, cobertura completa de estaciones, rendimiento de la aplicación ni características del host remoto. Las frecuencias, dimensionamiento, tecnología y retención del resumen son propuestas de ingeniería. Las fases indican cómo convertirlas en decisiones verificadas.
