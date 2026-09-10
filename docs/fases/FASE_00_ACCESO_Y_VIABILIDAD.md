# Fase 0 Acceso a datos y viabilidad

## Objetivo

Determinar qué podemos obtener, conservar y mostrar de AEMET y Meteoclimatic antes de construir integraciones sobre supuestos. Wunderground está aplazado por coste y falta de clave; registrar ese estado sin bloquear el trabajo ni exigir sus pruebas reales. Esta fase produce contratos y evidencia; no necesita el frontend.

Leer [el resumen global](../00_RESUMEN_GLOBAL.md), [las decisiones](../DECISIONES.md) y [las fuentes](../FUENTES.md). No se requieren fases previas. Las credenciales se introducen en el entorno local o en el gestor de secretos que se use, nunca en el chat ni en Git.

## Trabajo

1. Crear una matriz por proveedor y producto: catálogo, observaciones, resúmenes e histórico. Registrar endpoint documentado, autenticación, límites, resolución, cobertura temporal, unidades, convenciones horarias, almacenamiento permitido y publicación permitida. Usar estados `verified`, `pending_access`, `pending_terms`, `unsupported` y `deferred_cost`.
2. Comprobar AEMET con una clave del proyecto. Probar la respuesta de observaciones y el inventario climatológico como productos diferentes. Verificar el mecanismo de respuesta que entrega las URLs de datos y metadatos. Registrar esquemas y campos, sin incluir la clave en logs ni guardar URLs con secretos.
3. Obtener una muestra de Meteoclimatic por ámbito, empezando por XML y usando RSS cuando sea necesario y esté documentado. Comprobar si incluye coordenadas, hora de la estación y unidades. No confundir `pubDate` del documento con la hora de cada estación. Los prefijos geográficos efectivos se verifican; no se inventan a partir del nombre de la provincia.
4. Registrar Wunderground como `deferred_cost`. Solo si se solicita reactivar su ampliación, comprobar por separado observación actual, histórico y proximidad. Un 200 en un producto no acredita los demás. Una clave de subida de una estación no se trata como clave de lectura de The Weather Company.
5. Elegir una muestra pequeña de estaciones reales de las cuatro provincias, repartida entre las redes accesibles. Objetivo orientativo: dos estaciones por provincia y red si existen y el acceso lo permite. Guardar IDs reales solo tras verificarlos; registrar cobertura faltante sin inventar estaciones.
6. Descargar los polígonos provinciales desde una fuente oficial del IGN/CNIG, conservar procedencia y licencia y preparar una versión simplificada para la app. La clasificación se hará en WGS84, conservando el criterio para puntos en el límite.
7. Comprobar qué cartografía base puede usarse: fondo topográfico y fondo claro, sus atribuciones y sus límites. La biblioteca de mapas no incluye por sí misma un servicio de teselas.
8. Estimar llamadas por día con la muestra y la cadencia propuesta. Incluir reintentos, descubrimiento e importación. Documentar permisos y posibles costes antes de ampliar.

## Entregables a crear

- `docs/integraciones/MATRIZ_ACCESO.md`, con fecha y evidencia por capacidad.
- `docs/integraciones/AEMET.md` y `METEOCLIMATIC.md`, con contratos observados y ambigüedades. Una nota de Wunderground puede remitir al estudio de coste; no se exige implementar ni probar su adaptador.
- `config/provinces.geojson` y su archivo de procedencia.
- Muestras mínimas sanitizadas o fixtures sintéticos fieles al contrato; las muestras reales solo se versionan si pueden redistribuirse. Identificar claramente cada clase de fixture.
- Un pequeño comando de diagnóstico que muestre códigos de estado, cobertura y campos sin revelar secretos. Su ejecución real debe ser explícita y limitada.
- Actualización de `docs/ESTADO.md`.

No se exige obtener todas las credenciales para redactar el resto del sistema. Las partes inaccesibles quedan señaladas con la capacidad concreta que falta. Sí se exige acceso verificado para activar una fuente real.

## Comprobación de salida

- [ ] Existe una matriz con resultado independiente para cada producto, sin sustituirlo por un único “proveedor OK”.
- [ ] Se conocen hora, periodo, unidades, nulos y significado de lluvia de cada muestra utilizable.
- [ ] Se han contrastado las coordenadas de la muestra con las cuatro provincias.
- [ ] Hay al menos una fuente realmente accesible para el siguiente incremento funcional, o un bloqueo explícito si ninguna lo está.
- [ ] Se han identificado las diferencias entre acceso técnico y permiso de conservación/publicación.
- [ ] El presupuesto estimado cabe en los límites observados o documentados.

La fase puede cerrar su investigación con bloqueos documentados. Eso no significa que las dos integraciones objetivo estén aprobadas ni que su desarrollo real esté completo. Wunderground aplazado no constituye un bloqueo de la primera versión.

## Prompt para Codex

```text
Lee AGENTS.md, docs/00_RESUMEN_GLOBAL.md, docs/DECISIONES.md,
docs/ESTADO.md y docs/fases/FASE_00_ACCESO_Y_VIABILIDAD.md.
Ejecuta la fase 0. Inspecciona el repositorio y la documentación oficial.
Trabaja sobre AEMET y Meteoclimatic; registra WU como aplazado por coste.
Verifica solo los accesos para los que haya credenciales configuradas y
autorización de uso. No adivines IDs, cuotas, unidades ni derechos.
Entrega matriz por producto, contratos, muestra provincial, presupuesto
de llamadas y fixtures identificados. Si falta acceso, completa las tareas
independientes y registra exactamente lo pendiente. No construyas aún
la interfaz ni despliegues. Actualiza docs/ESTADO.md con evidencia real.
```
