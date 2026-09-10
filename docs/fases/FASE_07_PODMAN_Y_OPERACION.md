# Fase 7 Despliegue Podman y operación

## Objetivo

Preparar y verificar una instalación mantenible en el servidor habitual accesible mediante `ssh remote`, usando Podman, Quadlet/systemd y el Nginx existente. La implementación de esta fase produce ficheros y procedimiento revisables; la ejecución remota se realiza cuando el usuario pida desplegar.

Dependencias: aplicación validada en fases anteriores, dominio elegido, acceso SSH disponible en el entorno que despliegue y condiciones de publicación resueltas. Una integración pendiente se mantiene deshabilitada y documentada en la versión entregada.

## Reconocimiento previo del servidor

Cuando se solicite el despliegue, comprobar de forma no destructiva sistema operativo, arquitectura, versiones de Podman y systemd, cgroups, modo rootless/rootful, usuario efectivo, puertos ocupados, memoria y espacio. Revisar solo la configuración relevante de Nginx y sus servicios existentes, evitando exponer secretos.

El contexto de trabajo previo apunta a Quadlet/systemd y a un Nginx ya configurado. No se ha inspeccionado este host en la preparación del plan; por tanto, usuario de ejecución, ruta final de datos y compatibilidad se verifican antes de instalar. No asumir que el Podman de una máquina de trabajo coincide con el del remoto.

## Topología

| Servicio | Imagen | Red y persistencia |
| --- | --- | --- |
| `meteocentro-web` | Build estático y Nginx sin privilegios | Único puerto publicado en loopback; proxy `/api` al backend |
| `meteocentro-api` | Backend de la aplicación | Red interna, sin puerto expuesto al exterior |
| `meteocentro-worker` | Misma imagen backend con otra orden | Red interna y salida a proveedores; sin puerto público |
| `meteocentro-db` | PostgreSQL con versión fijada | Volumen persistente; sin publicación de 5432 |

El Nginx del host termina HTTPS y dirige únicamente el nuevo vhost al puerto local elegido para `web`. Propuesta de puerto: `127.0.0.1:8088`, si está libre. El servidor web del contenedor sirve la interfaz y encamina `/api`; no necesita gestionar certificados públicos.

## Ficheros a crear

- `deploy/containers/Containerfile.backend` y `Containerfile.web`, con compilación reproducible y usuarios adecuados.
- `deploy/quadlet/meteocentro-{web,api,worker,db}.container`, red y volúmenes asociados. Definir nombres sin colisión con otros servicios.
- `deploy/nginx/meteocentro.conf.example`, con dominio y puerto parametrizados.
- `deploy/env/meteocentro.env.example`, sin claves reales.
- Scripts de preparación, despliegue de versión concreta, backup, restauración en entorno aislado y comprobación de servicio.
- `docs/OPERACION.md` con instalación, actualización, recuperación y diagnóstico.
- Flujo de GitHub Actions para comprobaciones y construcción. El despliegue será manual inicialmente, no en cada push.

Para rootless, colocar los Quadlet en `~/.config/containers/systemd/`, incluir su sección de instalación y usar los servicios generados por systemd. Los `.container` no van en el directorio de unidades de usuario tradicionales. La forma de arranque y habilitación se documentará conforme a la versión instalada y al manual oficial [S16].

Configurar `linger` para el usuario de servicio cuando sea necesario para seguir ejecutando tras cerrar sesión y arrancar después de reiniciar. Se realizará con los privilegios adecuados, sin alterar el usuario o las unidades de otras aplicaciones.

Los volúmenes se sitúan en una ruta persistente con espacio medido, fuera del checkout. Si el host aplica SELinux, usar etiquetas y permisos adecuados; no deshabilitarlo. Validar UID/GID del contenedor PostgreSQL y el montaje real, no hacer cambios recursivos sobre rutas compartidas.

## Actualización

La opción preferida es construir imágenes en CI y publicarlas en GHCR privado con etiqueta de commit y digest, si están disponibles los permisos necesarios. Para el servidor, usar credencial de lectura limitada al registro y acceso de lectura al repositorio cuando haga falta. No reutilizar credenciales corporativas de otros registros.

El procedimiento de actualización debe registrar la versión actual, comprobar configuración y espacio, hacer backup, descargar imágenes exactas, ejecutar migración una vez, arrancar servicios y verificar salud, interfaz e ingestión. Evitar `latest`. Si una migración rompe compatibilidad, diseñar su reversión o una estrategia gradual antes de desplegar.

Si CI o GHCR no están disponibles, documentar una construcción con Podman a partir de un commit exacto en el entorno acordado. Esta alternativa no convierte un despliegue remoto en una operación implícita al editar documentación.

## Operación diaria

Systemd mantiene API y worker activos y reinicia procesos fallidos con límites para evitar bucles. La base de datos tiene comprobación de disponibilidad; las aplicaciones reintentan su conexión de forma acotada. El worker informa latido, retraso, próximo trabajo y errores por fuente.

Preparar una comprobación que detecte falta de observaciones nuevas cuando deberían haber llegado, además de HTTP y contenedores vivos. Mostrar estas incidencias en el panel. No añadir envíos por email o mensajería sin configurar ese canal expresamente.

Backups propuestos: una copia diaria de PostgreSQL, retención inicial de siete diarias y cuatro semanales, y copia externa al servidor. Incluir catálogo, observaciones, exclusiones, auditoría y configuración recuperable; los secretos se respaldan por una vía protegida separada. Cifrar la copia externa y ensayar la restauración en una base aislada. Una copia en el mismo disco no cubre la pérdida del host.

Objetivos propuestos a validar: pérdida máxima de 24 horas si se depende del backup diario y recuperación en dos horas en el entorno ensayado. No son garantías hasta probar el procedimiento. Los logs tendrán rotación y las importaciones respetarán un límite de recursos para no agotar el servidor.

## Comprobación de salida

- [ ] Los cuatro servicios arrancan desde la configuración versionada y mantienen datos al recrearlos.
- [ ] Cerrar SSH no detiene el worker; el arranque tras reinicio se prueba en un entorno autorizado.
- [ ] PostgreSQL y API no son accesibles directamente desde el exterior.
- [ ] `nginx -t` valida la nueva configuración y los vhosts anteriores siguen funcionando.
- [ ] Se verifica HTTPS y el modo de lectura elegido; no se publica información de proveedores no autorizados.
- [ ] Una actualización de versión conserva datos y exclusiones; el procedimiento de reversión queda ensayado o con su limitación concreta documentada.
- [ ] Se restaura una copia en un entorno aislado y se comprueban observaciones y exclusiones.
- [ ] El diagnóstico detecta un worker detenido y un proveedor que responde sin datos nuevos.
- [ ] Se documentan consumo de memoria, almacenamiento y ritmo de crecimiento observado.

## Prompt para Codex

```text
Lee AGENTS.md, resumen, estado y
docs/fases/FASE_07_PODMAN_Y_OPERACION.md. Prepara Containerfiles,
Quadlet, Nginx, configuración, CI y procedimientos reproducibles de
despliegue, actualización, backup y restauración. Valida localmente lo
posible. Si además se te ha pedido desplegar, comprueba primero el host
con ssh remote y presenta los cambios concretos que afecten al servidor.
Conserva sus otros servicios y usa versiones inmutables. No declares
probado un reinicio o una restauración que no hayas ejecutado.
Actualiza docs/ESTADO.md con resultados y limitaciones.
```
