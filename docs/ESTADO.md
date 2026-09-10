# Estado del desarrollo

Última actualización: 9 de septiembre de 2026.

**Estado general:** planificación inicial. No hay aplicación ejecutable, fuentes integradas, credenciales meteorológicas comprobadas ni despliegue remoto realizado.

**Alcance vigente tras revisar costes:** primera versión con AEMET y Meteoclimatic; Wunderground aplazado como ampliación opcional. El usuario ha confirmado que no dispone de clave WU y prefiere prescindir de esa red si es cara. Se ha comprobado la tarifa pública y la elegibilidad/límite de las claves PWS; ver el documento de coste. No se ha contratado ningún servicio.

## Seguimiento de fases

| Fase | Estado | Evidencia pendiente para cerrar |
| --- | --- | --- |
| 0 | Pendiente | Matriz de acceso y muestras verificadas de AEMET y Meteoclimatic |
| 1 | Pendiente | Arranque local y migración con PostgreSQL |
| 2 | Pendiente | Ingestión AEMET real y recuperación del worker |
| 3 | Pendiente | Ingestión Meteoclimatic real y descubrimiento sin reactivar exclusiones; WU fuera de alcance |
| 4 | Pendiente | Recorrido de mapa, tabla y ficha en escritorio y móvil |
| 5 | Pendiente | Series y agregados correctos con periodos y cobertura |
| 6 | Pendiente | Exclusión y restauración comprobadas de extremo a extremo |
| 7 | Pendiente | Instalación Podman, reinicio, actualización y restauración ensayados |

## Comprobaciones de planificación realizadas

- Revisión de páginas públicas y presentación del mapa de Suremet.
- Consulta de documentación primaria de AEMET, Meteoclimatic, The Weather Company, Podman y componentes propuestos.
- Identificación del límite de la búsqueda de proximidad WU y de diferencias entre históricos diarios e intradiarios.
- Redacción de documentos por fase con entregables, validación y prompts para Codex.

Esto no acredita que las APIs funcionen con una cuenta concreta. Las frecuencias, volúmenes y políticas de retención son propuestas que deberán medirse.

## Registro para completar en cada fase

```text
Fase y fecha:
Cambios realizados:
Comandos o recorridos verificados:
Resultados observados:
Fuentes reales probadas y capacidades:
Pruebas con fixtures:
Limitaciones o bloqueos:
Decisiones tomadas:
Commit o referencia de entrega:
Siguiente paso:
```
