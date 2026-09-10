# Fase 4 Mapa y consulta de estaciones

## Objetivo

Construir la experiencia principal inspirada en Suremet: abrir la web, entender qué ocurre en la región, elegir una variable y entrar en la estación que interesa. La pantalla debe ser útil en móvil y escritorio y consultar exclusivamente nuestra API para datos meteorológicos.

Dependencias: fase 2 y modelo estable de fase 1. Si alguna red de fase 3 sigue pendiente, se trabaja con las redes disponibles sin ocultar esa limitación. Las métricas diarias que dependan de fase 5 se mostrarán solo con un periodo ya validado.

## Diseño

La portada será el mapa, con cabecera compacta, selector de temperatura/humedad/viento/lluvia, filtros de provincia y fuente, y una leyenda visible. La referencia de Suremet es su densidad útil de información: mapa topográfico, números coloreados y acceso directo a fichas. Usar identidad propia y tonos azul y violeta en los controles; reservar escalas meteorológicas específicas para los datos.

La vista inicial ajusta el mapa a la unión de las cuatro provincias. No hace falta centrarlo en una capital ni pedir geolocalización. El usuario puede cambiar entre fondo topográfico y claro. Las atribuciones del fondo y de las redes permanecen visibles.

En áreas densas, mostrar valores legibles con control de colisiones. Si se agrupan puntos, el símbolo de grupo indica número de estaciones y se distingue de un dato de temperatura. Al ampliar se recuperan los puntos individuales; la agrupación no calcula un valor meteorológico ficticio.

## Trabajo

1. Implementar las rutas `/`, `/estaciones` y `/estaciones/:id`, más enlaces a datos diarios e históricos cuando estén listos. El ID de URL es estable aunque cambie el nombre.
2. Crear API de mapa con `bbox`, provincias, redes, métrica y filtro de frescura; devolver GeoJSON o un DTO equivalente. Limitar resultados, validar parámetros y evitar peticiones por cada marcador.
3. Pintar marcador numérico y leyenda de unidades/periodo. Para viento, distinguir velocidad media y racha; la flecha muestra dirección según convención documentada. No mostrar una racha diaria como velocidad actual.
4. Implementar un popup con nombre, municipio, altitud disponible, fuente, valor, hora y acceso a ficha. Con varias redes vinculadas, indicar cuál aporta el dato y permitir consultar las otras fuentes.
5. Crear tabla ordenable por variable, nombre y actualización, con buscador. Mapa y tabla comparten filtros y población de estaciones. Representar ausencia mediante un indicador claro, nunca cero.
6. Añadir contadores y extremos de la red. Solo compiten medidas utilizables de un mismo periodo y unidad. Mostrar cobertura o ausencia de comparabilidad cuando sea necesario.
7. Crear ficha actual con temperatura, humedad, viento, presión y precipitación disponibles, junto a sus unidades, periodo y última observación. Los paneles históricos se completarán en fase 5.
8. Conservar filtros y selección en la URL, y posición del mapa al cambiar variable o actualizar datos. Consultar la API cada 60 segundos mientras la pestaña está activa; pausar el refresco oculto y recuperar al volver.
9. Implementar estados vacíos, error de API, fuente pendiente, estación desactualizada y fondo cartográfico caído. La lista debe seguir sirviendo si no carga el mapa.
10. Añadir accesibilidad por teclado, contraste, foco visible, nombres de controles y alternativa tabular. En móvil, filtros plegables y ficha en panel inferior sin bloquear todo el mapa.

## Contrato de frescura

Cada valor muestra su `observed_at` y la antigüedad calculada. La fecha de recogida y el estado del proveedor son información adicional. Un HTTP 200 con observación vieja no renueva la frescura. Definir umbrales por producto basados en su cadencia y documentarlos; las estaciones AEMET horarias no deben juzgarse con un umbral pensado para PWS de cinco minutos.

La selección del dato de una estación con varios orígenes seguirá una preferencia explícita y elegibilidad. Si se permite recurrir a otro origen, se indica el cambio; no se mezclan variables o periodos sin conservar su procedencia individual.

## Entregables y comprobación

- Mapa, tabla y ficha conectados con la API, y guía breve de uso.
- [ ] Temperatura, humedad, viento y precipitación se pueden seleccionar cuando existen datos.
- [ ] Los filtros y la estación se conservan al volver de una ficha y al refrescar.
- [ ] Una estación excluida no aparece por mapa, tabla, búsqueda ni URL directa.
- [ ] Los valores desactualizados tienen hora y estado; no dominan los extremos actuales.
- [ ] Se inspeccionan los recorridos a 390 px y en escritorio sin desbordamientos.
- [ ] Se prueba la representación con 2.000 estaciones sintéticas identificadas como tales, para evaluar colisiones y fluidez; no es un objetivo de inventario real.
- [ ] No hay una llamada de proveedor por marcador ni secretos en el navegador.
- [ ] Se contrasta una muestra de valores con la API y la hora de su fuente.

Objetivo de rendimiento para medir, no resultado ya conseguido: el contenido útil del mapa aparece en menos de tres segundos en el entorno de prueba documentado y la interacción no se bloquea al cambiar de variable.

## Prompt para Codex

```text
Implementa la fase 4 después de leer AGENTS.md, resumen y estado.
Sigue docs/fases/FASE_04_MAPA_Y_ESTACIONES.md. Prioriza el mapa
topográfico con números coloreados y la navegación a fichas, con estilo
propio inspirado en Suremet. Usa la API del proyecto, conserva filtros
y explica hora, unidad, periodo y fuente. Verifica escritorio, móvil,
errores de cartografía y exclusiones. No añadas radar ni pronósticos.
Documenta las mediciones y actualiza docs/ESTADO.md.
```
