# Meteocentro

Plan de desarrollo de una aplicación meteorológica para Madrid, Ávila, Segovia y Guadalajara, inspirada en el mapa y los históricos de Suremet. La primera versión se centra en AEMET y Meteoclimatic. Weather Underground queda como ampliación opcional tras revisar su coste y acceso.

**Este repositorio contiene la especificación y las fases para desarrollar con Codex. La aplicación todavía no está implementada.** Fecha del plan: 9 de septiembre de 2026. Nombre provisional: Meteocentro.

Actualización de alcance: el usuario no dispone de clave Wunderground y prefiere usar AEMET y Meteoclimatic si su incorporación resulta cara. La recomendación tras revisar las tarifas es desarrollar primero esas dos redes. Ver [coste y acceso a datos](docs/COSTE_Y_ACCESO_DATOS.md).

## Por dónde empezar

1. Leer [el resumen global](docs/00_RESUMEN_GLOBAL.md).
2. Abrir el repositorio en Codex y comenzar por [la fase 0](docs/fases/FASE_00_ACCESO_Y_VIABILIDAD.md).
3. Ejecutar una fase cada vez con el prompt incluido al final de su documento.
4. Registrar resultados y bloqueos en [ESTADO.md](docs/ESTADO.md). Una fase con datos de prueba no equivale a una integración real verificada.

## Documentos por fase

| Fase | Resultado | Documento |
| --- | --- | --- |
| 0 | Accesos y alcance real de las fuentes comprobados | [Acceso y viabilidad](docs/fases/FASE_00_ACCESO_Y_VIABILIDAD.md) |
| 1 | Proyecto base y modelo de datos | [Base de la aplicación](docs/fases/FASE_01_BASE_Y_DATOS.md) |
| 2 | AEMET y recogida automática funcionando | [AEMET y proceso de fondo](docs/fases/FASE_02_AEMET_Y_WORKER.md) |
| 3 | Meteoclimatic y descubrimiento periódico; WU como ampliación | [Redes y descubrimiento](docs/fases/FASE_03_REDES_Y_DESCUBRIMIENTO.md) |
| 4 | Mapa, tabla de estaciones y ficha actual | [Mapa y consulta](docs/fases/FASE_04_MAPA_Y_ESTACIONES.md) |
| 5 | Gráficos, diarios, históricos y efemérides | [Históricos](docs/fases/FASE_05_HISTORICOS.md) |
| 6 | Gestión privada y exclusión persistente de estaciones | [Administración](docs/fases/FASE_06_ADMINISTRACION.md) |
| 7 | Despliegue Podman, actualizaciones y recuperación | [Despliegue y operación](docs/fases/FASE_07_PODMAN_Y_OPERACION.md) |

[FUENTES.md](docs/FUENTES.md) recoge la documentación consultada. [DECISIONES.md](docs/DECISIONES.md) distingue requisitos del usuario, propuestas técnicas y cuestiones pendientes.

## Primera instrucción para Codex

```text
Lee AGENTS.md, docs/00_RESUMEN_GLOBAL.md, docs/DECISIONES.md,
docs/ESTADO.md y docs/fases/FASE_00_ACCESO_Y_VIABILIDAD.md.
Ejecuta la fase 0 y registra las comprobaciones y bloqueos reales.
No presentes fixtures como datos reales. Prioriza AEMET y Meteoclimatic;
Wunderground está aplazado y no bloquea esta primera versión.
Continúa las tareas independientes si falta una credencial y especifica
qué integración queda pendiente. No inicies otra fase ni despliegues.
```

## Comportamientos esenciales

- El mapa y los históricos consultan nuestra base de datos.
- El worker recopila datos aunque no haya usuarios conectados.
- Un trabajo separado descubre nuevas estaciones y actualiza su catálogo.
- Eliminar una estación de la app la excluye del mapa, consultas y recogida individual; el descubrimiento no puede reactivarla.
- Los históricos anteriores al arranque se importan solo cuando la fuente los ofrece y el acceso permite conservarlos y mostrarlos.
- Las claves se configuran fuera de Git y del navegador.

El destino previsto es el servidor habitual mediante `ssh remote`, con Podman y Quadlet/systemd. Este plan no ha cambiado la configuración de ese servidor.
