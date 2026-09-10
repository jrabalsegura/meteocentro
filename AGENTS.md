# Instrucciones de desarrollo de Meteocentro

## Objetivo

Desarrollar la especificación de `docs/00_RESUMEN_GLOBAL.md` por fases. La prioridad es un mapa meteorológico útil y unos históricos fiables de las cuatro provincias. La documentación está en español; el código puede usar identificadores en inglés.

Alcance vigente: AEMET y Meteoclimatic para la primera versión. Wunderground es una ampliación opcional aplazada por coste y falta de clave. No dedicar su implementación ni sus pruebas reales a una fase ordinaria salvo que se solicite reactivarlo; conservar el contrato extensible de proveedores. Consultar `docs/COSTE_Y_ACCESO_DATOS.md`.

## Forma de trabajo

- Leer el resumen, las decisiones, el estado y la fase solicitada antes de editar.
- Inspeccionar primero el repositorio real. Los directorios y comandos de las fases son entregables futuros, no una afirmación de que ya existan.
- Implementar solo la fase solicitada y las dependencias mínimas necesarias. Hacer incrementos revisables; no reescribir trabajo ajeno.
- Actualizar `docs/ESTADO.md` con cambios, pruebas efectivamente ejecutadas, resultados, limitaciones y siguiente paso.
- Diferenciar siempre contrato implementado, pruebas con fixtures e integración real. Una fuente sin acceso debe figurar como pendiente, no como terminada.
- Mantener versiones y dependencias bloqueadas; elegir versiones soportadas y compatibles cuando se implemente la fase 1.
- Si un detalle menor no está fijado, decidirlo y documentarlo. Consultar si afecta al alcance, a un gasto recurrente, a la publicación o a la pérdida de datos.

## Invariantes

1. Identidad externa única por `(provider, external_id)`; los IDs internos son estables.
2. Conservar procedencia, instante de observación, instante de recogida y periodo de medida.
3. Un valor ausente no equivale a cero. No fabricar históricos ni rellenar huecos silenciosamente.
4. No sumar contadores acumulativos de lluvia como si fueran incrementos.
5. No mezclar presión de estación con presión reducida al nivel del mar.
6. La exclusión manual prevalece sobre descubrimiento, importación, caché y trabajos en curso. Nunca borrar o restaurar estaciones en las redes externas.
7. El navegador no contiene claves de proveedores ni consulta sus APIs de observaciones directamente.
8. El worker funciona separado de la API; no programar la ingestión dentro de cada proceso web.
9. Solo integrar accesos documentados y autorizados. No extraer claves de webs ajenas ni saltar límites o barreras. Documentar cualquier limitación concreta de la fuente.
10. Todas las rutas públicas, incluidos CSV y widgets si se implementan, aplican las mismas reglas de exclusión y publicación.

## Validación

Priorizar pruebas de errores con consecuencias: duplicados, lluvia, cambio de hora, exclusión concurrente, falta de acceso y recuperación. Usar PostgreSQL real para verificar sus restricciones y concurrencia. Las pruebas automáticas ordinarias no deben depender de la red externa. Las comprobaciones reales se ejecutan separadamente con credenciales del entorno y cuotas limitadas.

## Despliegue

Usar Podman y Quadlet/systemd según la fase 7. No publicar, ejecutar acciones remotas ni modificar otros servicios al implementar una fase que no lo pida. Cuando se solicite desplegar, preparar y validar primero los cambios concretos. Comprobar el estado del servidor y conservar sus vhosts, certificados, volúmenes y puertos existentes.

No añadir claves, bases de datos, respuestas con secretos, ficheros de entorno reales ni copias de seguridad a Git. No activar despliegues automáticos desde cada push sin que el usuario haya elegido esa política.
