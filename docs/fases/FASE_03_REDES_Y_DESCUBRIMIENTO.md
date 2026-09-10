# Fase 3 Redes de aficionados y descubrimiento

## Objetivo

Añadir Meteoclimatic al motor de AEMET y mantener actualizado el catálogo de las cuatro provincias. Wunderground queda como ampliación opcional aplazada; se conserva abajo su diseño para una posible incorporación futura, sin exigir su implementación para cerrar esta fase.

Dependencias: fases 1 y 2; contrato y condiciones de Meteoclimatic verificados en fase 0. La app debe seguir operativa si una de las redes habilitadas falla.

## Meteoclimatic

1. Implementar el lector del formato XML documentado y el complemento RSS que justifique el contrato. Usar un parser sin entidades externas y controlar tamaño y codificación. Tratar unidades, coma decimal, sensores ausentes y centinelas según las muestras.
2. Consultar por ámbitos provinciales verificados o agrupaciones permitidas. El mismo lote puede actualizar observaciones y detectar IDs nuevos; no volver a descargarlo innecesariamente para dos trabajos.
3. Resolver metadatos y coordenadas mediante una fuente permitida. El nombre de municipio no basta para inventar una posición exacta. Sin ubicación válida, pasar a revisión.
4. Distinguir valores actuales y extremos diarios del feed. Preservar su periodo y hora; un feed consultado dos veces con la misma hora de estación es la misma observación.
5. Conservar los indicadores de calidad de Meteoclimatic sin reinterpretarlos como certificación propia. La ausencia de un sello no implica datos inválidos.

## Ampliación opcional aplazada de Wunderground

No ejecutar este apartado en la primera versión. Solo se reactiva mediante una petición posterior del usuario y acceso adecuado a coste aceptable, según [el estudio de coste](../COSTE_Y_ACCESO_DATOS.md).

1. Implementar observación actual por `stationId` usando el producto PWS documentado y la clave del proyecto; normalizar el bloque de unidades elegido.
2. Implementar descubrimiento solo si la cuenta tiene acceso al producto de proximidad. Partir de una malla de puntos dentro del ámbito, con solapamiento razonable y filtrado final por polígonos. Limitar la refinación de celdas y el total de llamadas por ejecución.
3. Registrar puntos consultados, resultados, celdas con límite de resultados alcanzado, fecha y presupuesto restante. No tratar una consulta de diez vecinos como inventario completo de una provincia.
4. Deduplicar por ID externo antes de pedir observaciones. Una malla más densa mejora la muestra, pero no acredita exhaustividad. Mostrar “cobertura de descubrimiento parcial” cuando corresponda.
5. Si el catálogo no está disponible, permitir una lista de IDs verificados e incorporación manual. Conservar `discover=unsupported` o `pending_access` en la matriz; no fingir una búsqueda automática realizada.

## Reglas comunes de catálogo

Cada detección actualiza `first_seen_at`, `last_seen_at`, metadatos y capacidades sin sobrescribir decisiones manuales. Una estación nueva se incorpora si cumple el ámbito, tiene acceso habilitado y metadatos válidos, y no coincide con una exclusión. Los casos de identidad o posición dudosa van a `review`.

La ausencia temporal en un feed no borra la estación ni su histórico. Se registra frescura por separado y se revisa el origen tras ausencias repetidas. Un cambio significativo de coordenadas crea historial y aviso, porque puede representar un traslado del equipo.

Para coincidencias entre redes, proponer parejas por proximidad y metadatos; la vinculación necesita evidencia suficiente o revisión del administrador. Mantener las series por origen. Tras vincular, el mapa muestra un punto canónico con fuente elegida de forma explícita y sin sumar lluvia de las dos fuentes. El cambio de fuente aparece en la interfaz.

**La exclusión es prioritaria.** Antes de insertar una estación se consulta su ID en la lista de exclusión. Al vincular un nuevo origen a una estación excluida, hereda su exclusión efectiva. Una estación reaparecida con un ID distinto no se puede identificar con certeza solo por distancia: se marca para revisión si parece una coincidencia.

## Frecuencias y presupuesto

Meteoclimatic: propuesta inicial de recogida cada 10 minutos y revisión diaria de catálogo. La programación WU permanece desactivada. Si se reactiva su ampliación, la propuesta es 10–15 minutos para IDs activos y exploración semanal, siempre según acceso real.

Ejemplo de presupuesto: 300 IDs WU consultados individualmente cada 10 minutos requieren 43.200 llamadas diarias solo para observaciones, antes de histórico y reintentos. Ese cálculo es de diseño, no una cuota disponible. Si supera el acceso contratado, reducir cadencia o número de estaciones de forma visible; no distribuir peticiones entre claves para eludir límites.

## Entregables y comprobación

- Adaptador Meteoclimatic, catálogo unificado, trabajos de descubrimiento y alta manual por ID. WU no es un entregable obligatorio.
- Resumen por ejecución: nuevos, actualizados, fuera de ámbito, excluidos, duplicados potenciales y pendientes.
- [ ] Repetir un descubrimiento no duplica estaciones.
- [ ] Una estación excluida no se reactiva ni vuelve a programarse.
- [ ] Un origen con API denegada queda aislado y muestra su limitación.
- [ ] Una caída del feed no borra el catálogo.
- [ ] No se programa ni consulta WU en la primera versión. Si se reactiva, su presupuesto no se supera, incluso con reintentos.
- [ ] Los registros de unidades y lluvia coinciden con muestras verificadas.
- [ ] Hay prueba real separada para cada red habilitada; cualquier red pendiente mantiene su estado pendiente.

## Prompt para Codex

```text
Lee AGENTS.md, resumen, matriz de acceso, contratos y estado.
Implementa docs/fases/FASE_03_REDES_Y_DESCUBRIMIENTO.md reutilizando
el worker. Añade Meteoclimatic con su acceso permitido. Mantén WU
aplazado, sin implementarlo ni exigirlo para cerrar la fase. Prueba descubrimiento
idempotente, geografía, cuotas, duplicados y exclusiones persistentes.
Mantén operativa la app con un proveedor deshabilitado. Registra las
pruebas reales y lo que siga pendiente; no declares completa una red
validada únicamente con fixtures. Actualiza docs/ESTADO.md.
```
