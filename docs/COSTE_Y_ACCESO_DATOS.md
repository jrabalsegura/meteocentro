# Coste y acceso a los datos

Revisión: 9 de septiembre de 2026. El usuario no dispone de clave de Wunderground y prefiere AEMET y Meteoclimatic si su coste resulta elevado. La recomendación para la primera versión es utilizar esas dos redes y aplazar Wunderground. Esta revisión actualiza el alcance del plan; no acredita integraciones ejecutadas.

## Qué clave utilizar

| Fuente | Acceso y precio publicado | Decisión para el proyecto |
| --- | --- | --- |
| AEMET OpenData | Servicio gratuito; requiere una API key. | Solicitar esta clave e integrar sus estaciones oficiales. |
| Meteoclimatic | Feeds XML/RSS públicos documentados, sin tarifa de consulta publicada ni clave de pago descrita para esos feeds. | Integrar tras comprobar cobertura y condiciones de conservación y publicación. |
| The Weather Company / Wunderground | Standard: 500 USD/mes, modalidad anual y un millón de llamadas al mes. Equivale a 6.000 USD/año al precio anunciado. | Aplazado por coste; no contratar para la primera versión. |

Fuentes: [AEMET, preguntas frecuentes](https://opendata.aemet.es/centrodedescargas/docs/FAQs130917.pdf), [Meteoclimatic, XML](https://www.meteoclimatic.net/index/wp/xml_es.html) y [tarifas de The Weather Company](https://www.weathercompany.com/weather-data-apis/weather-data-apis-packages-pricing/). El proveedor también anuncia una prueba gratuita de 30 días limitada a clientes empresariales elegibles y una opción Enterprise de presupuesto personalizado. No se ha obtenido una oferta individual más barata.

Para AEMET, entrar en [OpenData](https://opendata.aemet.es/centrodedescargas/inicio), solicitar una API key y completar el proceso por correo. Configurar la clave en el entorno del servidor cuando se desarrolle la integración. No introducirla en Git ni en documentos.

## La clave personal de Wunderground no resuelve este caso

La [ayuda oficial sobre claves PWS](https://support.weather.com/s/article/Understand-and-Manage-Your-Personal-Weather-Station-PWS-API-Keys-WeatherUnderground?language=en_US), comprobada en navegador, limita su disponibilidad a usuarios que aportan datos desde sus estaciones a Wunderground. Publica un máximo de 1.500 llamadas diarias y 30 por minuto. Crear una cuenta sin aportar datos no basta. Esa página no publica un precio para adquirir una clave personal sin cumplir el requisito.

Con una consulta individual cada diez minutos, cada estación consumiría 144 llamadas diarias. Diez estaciones consumirían 1.440, dejando solo 60 para otros usos. Es un cálculo orientativo, sin reintentos ni históricos; no acredita autorización de agregación o redistribución.

Otro escenario de dimensionamiento, sin afirmar que esa sea la cobertura real: 300 estaciones, consultadas individualmente cada diez minutos durante 30 días, consumirían 1.296.000 llamadas. Habría que reducir frecuencia, estaciones o contratar capacidad adicional frente al millón anunciado.

El [paquete Standard documentado](https://developer.weather.com/docs/standard-weather-data-package) incluye productos PWS actuales y ventanas históricas recientes de uno o siete días según resolución. No se debe presupuestar un archivo de años de todas las estaciones con esa información. Si se reactiva la ampliación, confirmar en una oferta concreta los productos PWS, descubrimiento, límites y derechos de almacenamiento y publicación.

## Qué conserva la primera versión

El diseño mantiene el mapa de las cuatro provincias, las fichas y gráficos, la exclusión manual persistente y el proceso en segundo plano para recopilar observaciones y descubrir estaciones accesibles. La cobertura dependerá de AEMET y Meteoclimatic; no se afirma cuántas estaciones aportarán hasta comprobar sus catálogos.

Los históricos detallados se construirán con los datos recopilados desde la puesta en marcha. Se importarán datos anteriores cuando exista un producto accesible y autorizado; el [histórico documentado por Meteoclimatic](https://wiki.meteoclimatic.net/wiki/Datos_hist%C3%B3ricos) describe resúmenes diarios y no permite prometer curvas intradiarias antiguas.

Meteoclimatic publica [condiciones de uso](https://www.meteoclimatic.net/index/wp/cc_es.html) que requieren revisar el uso concreto de la app en la fase 0. Acceso público no equivale a autorización ilimitada. Los costes del servidor, dominio y cartografía se presupuestarán aparte; no son tarifas de estas fuentes meteorológicas.

En el estado del proyecto, Wunderground se registrará como `deferred_cost`. Sus tareas permanecen como diseño opcional y no son requisitos para completar la primera versión.
