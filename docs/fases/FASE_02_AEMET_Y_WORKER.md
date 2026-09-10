# Fase 2 AEMET y proceso de fondo

## Objetivo

Empezar a almacenar observaciones reales de AEMET y demostrar que la recogida continúa sin usuarios conectados. Esta fase entrega el motor reutilizable por las otras redes.

Dependencias: fase 1 y contrato AEMET de fase 0. Si no hay clave, se puede validar el motor con fixtures; la integración real queda pendiente y no se declara terminada.

## Trabajo de integración

1. Implementar el cliente HTTP de AEMET según el contrato verificado. Resolver datos y metadatos sin fijar para siempre una URL temporal. Aplicar timeout de conexión y lectura, límite de tamaño de respuesta y saneamiento de logs.
2. Priorizar la descarga conjunta de observaciones sobre una petición por estación si el producto lo permite. Filtrar el ámbito después de obtener los registros; registrar por separado observaciones válidas, fuera de ámbito, excluidas e inválidas.
3. Construir el catálogo con la unión del inventario climatológico y los IDs presentes en observaciones. Una estación que solo dispone de climatología no se presenta como estación actual sin conexión; tendrá capacidad histórica.
4. Usar los metadatos para confirmar campos, unidades y periodos. La conversión de coordenadas del inventario y la interpretación de centinelas numéricos deben basarse en la respuesta real. La presión y precipitación no se asignan por parecido del nombre.
5. Guardar con operaciones idempotentes y actualizar el último dato solo cuando corresponda a un instante igual o posterior; una importación antigua no puede desplazar la última observación actual.

## Trabajo del worker

El worker es un proceso propio. Se propone un bucle sencillo con planificación guardada en PostgreSQL, trabajos con `next_run_at`, `status`, `attempts`, `lease_until`, `owner_token`, `cursor` e identificador de deduplicación. No hace falta incorporar Redis ni Celery para esta escala inicial.

- Una sola instancia programadora activa, mediante bloqueo de liderazgo; no duplicar trabajos al reiniciar la API.
- Un trabajo reclamado tiene plazo y latido. Si el proceso muere, puede recuperarse al vencer el plazo. Al escribir resultados se comprueba que sigue teniendo el arrendamiento y que no se ha excluido el origen.
- La ejecución es al menos una vez; la unicidad de datos y trabajos impide duplicados observables.
- Prioridad: datos actuales, recuperación de huecos, descubrimiento e importación antigua. La recuperación no puede consumir todo el presupuesto de datos actuales.
- Concurrencia pequeña y configurable por proveedor. Para 429 respetar `Retry-After` y el presupuesto; para fallos transitorios, reintentos con espera creciente y variación aleatoria. Un 401/403 pausa el acceso y muestra el motivo; no reintenta indefinidamente.
- Al reiniciar, leer el último punto confirmado y recuperar solo las ventanas disponibles. Un hueco imposible de recuperar permanece identificado.
- Si PostgreSQL falla, no confirmar el trabajo ni adelantar su cursor. El apagado libera o deja vencer los trabajos sin perder registros confirmados.

Frecuencia inicial propuesta para AEMET: 15 minutos. La frescura se calcula con la hora del dato y la cadencia real del producto, no con la hora del último HTTP 200. El panel operativo deberá distinguir “proveedor consultado” de “nuevas observaciones recibidas”.

## Entregables

- Adaptador AEMET, ejecutable del worker y operaciones de consulta única y descubrimiento único para diagnóstico.
- Historial de ejecuciones con contadores, retraso y error sanitizado.
- Últimas observaciones consultables desde la API.
- Comandos de operación local y explicación de las frecuencias efectivas.

## Comprobación de salida

- [ ] Sin navegador abierto se ejecutan al menos tres ciclos del worker y se guardan las observaciones nuevas que la fuente publique.
- [ ] Reprocesar una respuesta no crea filas repetidas ni modifica indebidamente el último dato.
- [ ] Un fallo después de guardar datos y antes de confirmar el trabajo se recupera de forma idempotente.
- [ ] Una caída de proveedor no rompe las consultas del archivo existente.
- [ ] 429, credencial inválida y timeout producen los estados esperados.
- [ ] Reiniciar worker o base de datos recupera la actividad sin duplicar programación.
- [ ] Una exclusión añadida mientras una descarga está en curso impide la escritura/publicación de ese origen.
- [ ] Una muestra real del ámbito se contrasta con AEMET indicando hora y resolución, cuando exista acceso.

## Prompt para Codex

```text
Implementa la fase 2 tras leer AGENTS.md, resumen, contratos y estado.
Usa docs/fases/FASE_02_AEMET_Y_WORKER.md. Crea la ingestión AEMET
y un worker independiente, persistente y reanudable. Usa el contrato
verificado de la fuente. Demuestra idempotencia, recuperación, control
de cuotas y exclusión concurrente. Haz una prueba real limitada si hay
clave disponible; si no, deja explícito el bloqueo. No construyas aún
las otras redes ni despliegues. Actualiza docs/ESTADO.md.
```
