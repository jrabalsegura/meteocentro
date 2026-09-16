# Fixtures sintéticos

Los dos ejemplos de este directorio son inventados para probar estructura y casos límite. Sus IDs (`T000X` y `ESXXX0000000000000A`) imitan la forma de los identificadores observados, pero no se han obtenido de los proveedores; no acreditan una integración ni deben cargarse como históricos.

`aemet_metadata_sintetica.json` contiene descripciones abreviadas inventadas para las pruebas HTTP y de cambios de contrato de fase 2. Conserva las unidades y ventanas verificadas, pero no es una respuesta real. Las pruebas construyen también registros, errores HTTP y URLs temporales sintéticos; ninguna prueba ordinaria llama a AEMET.

Las pruebas de fase 3 derivan del XML sintético existente, sustituyen IDs y añaden sensores/codificaciones en memoria. Sus coordenadas y la referencia `fixture-only-permission` son exclusivas de tests: no acreditan autorización ni identidad de estaciones reales. Las respuestas del diagnóstico real del 16-9-2026 no se han guardado como fixtures.
