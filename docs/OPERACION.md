# Operación de Meteocentro — fase 7

Revisada el 23 de septiembre de 2026 tras retirar las copias de seguridad por petición del usuario. **No se ha conectado a `remote`, instalado unidades ni desplegado.** Los resultados efectivamente ejecutados están en [ESTADO.md](ESTADO.md); este procedimiento no acredita por sí solo un reinicio del host.

Se sigue el precedente local de `finantialApp/docs/DEPLOY.md` y `docs/OPERATIONS.md`: imagen OCI, Quadlet, datos separados del checkout, puerto en loopback, Nginx existente y validación antes de recargar. Aquel documento registra un despliegue **rootful** y Podman 4.9.3 en agosto de 2026; no es un inventario actual del servidor. Meteocentro prepara **rootless** por defecto y permite `--mode rootful` para seguir ese precedente. No se usan las etiquetas mutables `current`/`rollback` de aquel proyecto: las imágenes se fijan por digest o ID completo.

## 1. Qué se instalará y qué decidir antes

| Recurso | Propuesta, pendiente del inventario futuro |
| --- | --- |
| Contenedores/unidades | `meteocentro-db`, `meteocentro-api`, `meteocentro-worker`, `meteocentro-web` |
| Red | `meteocentro`, privada entre contenedores con salida para proveedores |
| Datos | Volumen nombrado `meteocentro-db-data`, dentro del almacenamiento persistente de Podman, fuera del checkout |
| Entrada | Solo `127.0.0.1:8088` → web:8080; API y PostgreSQL sin publicación |
| Lectura | Privada, cuenta creada por CLI, cookies Secure en HTTPS |
| Proveedores | AEMET **y Meteoclimatic activos**, como la instalación privada local; clave solo en worker |
| Nginx | Un nuevo vhost parametrizado; certificados existentes o emisión individual para el dominio nuevo |
| Actualización | Manual, parada de escritores, migración única, imágenes exactas y comprobación HTTP |
| Persistencia | PostgreSQL conserva los datos en su volumen; sin copias, timers ni destinos externos |

Confirmar dominio, modo de Podman y usuario de servicio, arquitectura (`amd64`/`arm64`), puerto libre y espacio disponible. Las rutas de los ejemplos son propuestas; no se han creado en el servidor. La configuración preparada mantiene uso privado no comercial de Meteoclimatic, atribución y originales. No habilita su archivo diario remoto, CSV ni publicación pública. Wunderground continúa aplazado. La instalación nueva crea una base vacía en el servidor y no traslada ni borra el archivo local (apartado 7). Sin copias, el proyecto no ofrece recuperación de datos si se pierde el volumen. Cambiar `PRIVATE_READ` requiere decidir la exposición y revisar las condiciones del producto que se publique.

## 2. Validación previa en el Mac, sin tocar los contenedores actuales

Desde el repositorio:

```bash
cd /Users/jraba/Desktop/meteocentro
python3 -m unittest discover -s tests -p test_deploy.py
python3 deploy/scripts/build.py --engine docker --platform linux/arm64 \
  --allow-dirty --output runtime/phase7-local-images.json
python3 deploy/scripts/rehearse.py --engine docker \
  --images runtime/phase7-local-images.json \
  --output runtime/phase7-local-ensayo --backend-tests
```

Elegir rutas nuevas si ya existen. `--allow-dirty` identifica una compilación de trabajo: `apply` y `publish` rechazan estas imágenes. Para un Mac Intel usar `linux/amd64`. La arquitectura de producción se elige después del inventario, independientemente de la del Mac.

El ensayo crea nombres aleatorios `meteocentro-phase7-*`, una red propia, puertos efímeros en loopback y volúmenes sintéticos. Usa ambas redes desactivadas **solo en ese ensayo**, sin credenciales ni HTTP a proveedores. Migra 0005→0006 con observación/exclusión/auditoría sintéticas, arranca los cuatro contenedores, comprueba acceso privado, recrea API, reinicia PostgreSQL, verifica el diagnóstico y compara conteos/huellas para comprobar que migraciones y reinicios conservan datos y exclusiones. Al terminar elimina únicamente los contenedores/red del ensayo; conserva sus volúmenes y las evidencias en `runtime/`. Los nombres concretos quedan en la salida. No ejecutar limpiezas globales de Docker/Podman.

### Abrir la versión local en el navegador

- **Instalación habitual, con datos reales:** [http://localhost:5173](http://localhost:5173). En la comprobación del 21-09-2026 su API mantiene el esquema `0004_history`; AEMET y Meteoclimatic están activos. Esa instalación aún requiere actualizar API/worker, migrar y crear el administrador para usar las fases 6–7 completas. No se hace como efecto secundario del ensayo.
- **Vista previa de fase 7, aislada y sintética:** ejecutar el ensayo con `--keep-running`. Solo conserva los cuatro contenedores si todas las comprobaciones solicitadas pasan. Por ejemplo, con las imágenes ya construidas y el puerto 8088 libre:

```bash
python3 deploy/scripts/rehearse.py --engine docker \
  --images runtime/phase7-local-images.json \
  --output runtime/phase7-local-revalidado --backend-tests \
  --keep-running --web-port 8088
```

Abrir **[http://127.0.0.1:8088](http://127.0.0.1:8088)**, con ese origen exacto. Cuenta sintética: `fixture`; contraseña: `Phase7 synthetic password only!`. En `/gestion` se pueden revisar fuentes, diagnóstico y exclusiones. El mapa empieza vacío porque la única estación sintética se ha excluido para comprobar su conservación durante la migración y el reinicio. Sus datos se conservan en el archivo privado. No representa el catálogo real. Los proveedores están desactivados **solo aquí**; la recogida de la instalación habitual sigue activa con su configuración.

El directorio de salida debe ser nuevo en cada ejecución. Si 8088 está ocupado, elegir otro puerto o quitar `--web-port` para obtener uno libre; la salida y `result.json` indican la URL exacta y el resultado de las pruebas. No es necesario reconstruir imágenes tras corregir únicamente pruebas o el script del ensayo; sí tras cambiar código de la aplicación, dependencias o Containerfiles.

Para retirar exclusivamente esa vista previa, conservando sus volúmenes sintéticos:

```bash
sh runtime/phase7-local-revalidado/stop-preview.sh
```

Si falla una comprobación, incluso con `--keep-running`, se retiran los contenedores/red del ensayo. `result.json` conserva el estado `failed`, las comprobaciones terminadas y el resultado de pytest cuando se ejecuta. Unas comprobaciones de persistencia correctas previas a un fallo de pruebas no convierten el ensayo completo en correcto. El error de milisegundos observado en el Mac se corrigió usando el reloj PostgreSQL en las pruebas de cadencia y reanudación, igual que en la cola real; no añadiendo esperas ni cambiando los intervalos de ingestión.

## 3. Inventario futuro del host — solo cuando se vaya a desplegar

Primero entrar y examinar, sin instalar:

```bash
ssh remote
uname -srmo
id
podman --version
systemctl --version | head -1
podman info --format 'rootless={{.Host.Security.Rootless}} cgroups={{.Host.CgroupsVersion}} graphRoot={{.Store.GraphRoot}}'
ss -ltn
free -h
df -h
podman ps --format '{{.Names}} {{.Image}} {{.Ports}}'
sudo podman ps --format '{{.Names}} {{.Image}} {{.Ports}}'
systemctl --user list-units --type=service --state=running --no-pager
sudo systemctl list-units --type=service --state=running --no-pager
test -x /usr/lib/systemd/system-generators/podman-system-generator
sudo nginx -t
sudo ls -l /etc/nginx/sites-enabled
```

El script [inventory.sh](../deploy/scripts/inventory.sh) agrupa parte de esas lecturas **en la máquina donde se ejecuta**; no conecta por SSH. Revisar localmente solo los vhosts, certificados, propietarios y servicios pertinentes; no publicar `nginx -T`, archivos de entorno o `podman inspect` completo en logs compartidos. Comprobar DNS A/AAAA, puertos 80/443/8088 y los dominios actuales antes y después del cambio. Guardar un inventario de nombres, respuestas HTTP y rutas para comparación.

Antes de ejecutar los pasos siguientes, dejar revisados los cambios concretos: usuario/modo, rutas, los seis ficheros Quadlet, nueva red/volumen, puerto elegido, vhost y certificado. No detener finanzas, radar, webs ni otras unidades. No ejecutar una actualización global de paquetes o desactivar SELinux como parte de este procedimiento. Si faltan dependencias, instalar únicamente las necesarias tras revisar compatibilidad e impacto.

Requisitos: Linux con cgroups v2, generador Quadlet compatible (sintaxis base 4.9.3), systemd, Python ≥3.11, Podman, Nginx existente y espacio para imágenes, datos y crecimiento. El generador debe aceptar los ficheros antes de instalarlos. La validación del generador no prueba el runtime rootless, redes, cgroups o reinicio.

## 4. Usuario, rutas y acceso al registro

Ruta recomendada rootless, ejecutada **como el usuario de servicio elegido**, sin mezclar `sudo podman`:

```bash
install -d -m 0700 "$HOME/meteocentro" "$HOME/meteocentro/releases" \
  "$HOME/meteocentro/state"
```

Para un usuario nuevo, el administrador configura home, rangos subordinados UID/GID y acceso según la distribución. Comprobar que su sesión `systemctl --user` funciona. Activar `linger` únicamente para ese usuario:

```bash
sudo loginctl enable-linger USUARIO_METEOCENTRO
loginctl show-user USUARIO_METEOCENTRO -p Linger
```

Con rootless, usar `~/.config/containers/systemd/` y `systemctl --user`. Con rootful, ejecutar Podman y `ops.py apply` con `sudo`, usar `/etc/containers/systemd/`, `/etc/meteocentro`, `/var/lib/meteocentro/releases`, `/var/lib/meteocentro/state`; `WantedBy` se renderiza como `multi-user.target`. No definir `User=` en `[Service]` para intentar convertir Quadlet rootful en rootless.

El volumen nombrado se crea mediante `.volume`, no depende del checkout y no se borra al recrear contenedores. Medir el filesystem de Podman; consultar el montaje del volumen cuando ya exista:

```bash
podman info --format '{{.Store.GraphRoot}}'
# Después del primer apply:
podman volume inspect meteocentro-db-data --format '{{.Mountpoint}}'
```

El entrypoint oficial inicializa el volumen con su UID/GID; no hacer `chown -R` sobre almacenamiento compartido. Confirmar en la imagen elegida `podman run --rm IMAGEN_POSTGRES id postgres` y el montaje real. Los volúmenes nombrados reciben el tratamiento SELinux de Podman; si se adapta a bind mounts, usar una ruta exclusiva con etiqueta adecuada (`:Z`) y verificar UID mapeado con `podman unshare`. No relabelar directorios de otras aplicaciones.

## 5. Obtener una versión inmutable

Guardar los cambios revisados, incluidos los de retirada de copias, en un commit accesible desde el servidor y usar su **SHA completo**, no `git pull main` durante la instalación. Abrir o fusionar la pull request de esta fase no ejecuta un despliegue; la publicación GHCR también requiere seleccionar explícitamente esa opción manual.

### Opción A: CI y GHCR privado

La CI ejecuta backend, frontend, compilación, generador Quadlet y ensayo de migración/persistencia. En GitHub → Actions → CI → Run workflow, seleccionar el commit/rama revisado y marcar `publish` **solo si se quiere publicar imágenes**. No hay SSH ni despliegue en ese workflow. Verificar visibilidad privada de los paquetes GHCR y sus permisos antes del primer uso. El artefacto contiene `ghcr-images.json`, con etiquetas de commit y digests efectivos; el workflow actual construye `linux/amd64`. Un host ARM64 requiere la construcción local de la opción B o ampliar explícitamente la matriz.

Para descargarlas, usar en el usuario elegido credencial GHCR de **solo lectura de paquetes**, introducida por stdin. Proteger el archivo persistente de autenticación de Podman y apuntar `REGISTRY_AUTH_FILE` a él; el archivo transitorio bajo `/run` no sobrevive al reinicio. El token no va al repositorio, la configuración del contenedor ni a un argumento de build. No usar credenciales de otros registros o proyectos.

Descargar el artefacto, copiar el código del mismo SHA a una carpeta de versión y colocar el manifiesto como `images.json`. Comparar su `commit` con `git rev-parse HEAD`.

### Opción B: construcción en el servidor, sin publicar imágenes

Ejecutar dentro de `ssh remote`, con el usuario de servicio. El repositorio es privado: usar acceso de lectura ya configurado, sin poner tokens en la URL. Este ejemplo es para la primera instalación; si el checkout existe, revisarlo y reutilizarlo sin sobrescribir cambios. Detenerse ante cualquier error.

```bash
git clone https://github.com/jrabalsegura/meteocentro.git "$HOME/meteocentro/source"
cd "$HOME/meteocentro/source"
git fetch origin
release_sha=SHA_COMPLETO_REVISADO
git checkout --detach "$release_sha"
test -z "$(git status --porcelain)"
case "$(uname -m)" in
  x86_64) release_platform=linux/amd64 ;;
  aarch64|arm64) release_platform=linux/arm64 ;;
  *) echo "Arquitectura no contemplada"; exit 1 ;;
esac
python3 deploy/scripts/build.py --engine podman --platform "$release_platform" \
  --output "$HOME/meteocentro/releases/$release_sha-images.json"
```

En rootful usar `sudo python3 ... --engine podman` y las rutas rootful. `build.py` rechaza un árbol modificado; usa `uv.lock`, `package-lock.json` y bases por digest. El manifiesto local guarda IDs `sha256:…` completos. Los IDs locales pertenecen al almacén de ese host/usuario: para transportarlos, usar `podman save/load` y comprobar ID/arquitectura, o GHCR por digest. No reutilizar un ID del Mac en un host que no contiene esa imagen. Las etiquetas sirven para localizar la compilación, no para arrancar producción.

## 6. Preparar y revisar los archivos, sin arrancar

Desde el checkout exacto, como usuario de servicio rootless:

```bash
release_sha=SHA_COMPLETO_REVISADO
release_dir="$HOME/meteocentro/releases/$release_sha"
python3 deploy/scripts/ops.py prepare \
  --images "$HOME/meteocentro/releases/$release_sha-images.json" \
  --output "$release_dir" --mode rootless \
  --domain DOMINIO_ELEGIDO --port 8088 \
  --config-dir "$HOME/.config/meteocentro"
python3 deploy/scripts/ops.py init-config --release "$release_dir"
```

Con GHCR, usar la ruta a `ghcr-images.json` en `--images`. Para rootful renderizar `--mode rootful --config-dir /etc/meteocentro`, con salida en `/var/lib/meteocentro/releases/$release_sha`; crear la configuración con `sudo`. Si el certificado existe en otra ruta, pasar `--tls-cert` y `--tls-key`. `prepare` solo escribe archivos nuevos y se niega a sobrescribir su directorio. `init-config` se ejecuta **una sola vez**: nunca sobre una configuración existente.

`init-config` genera contraseña aleatoria de PostgreSQL y cuatro archivos 0600:

- `db.env`: nombre, usuario y contraseña para PostgreSQL.
- `database.env`: URL correspondiente, para API, worker y migración.
- `meteocentro.env`: origen HTTPS exacto, lectura privada y sesión.
- `worker.env`: AEMET/Meteoclimatic, cuotas, licencia y clave AEMET.

Editar `worker.env` con editor privado, poner la clave AEMET válida y comprobar permisos sin imprimir su contenido. La clave anterior tiene caducidad registrada el 31-10-2026; comprobar validez y renovar cuando corresponda. No copiar datos/secretos de prueba. Cambiar contraseña de PostgreSQL exige primero `ALTER ROLE` por canal privado y actualizar ambos archivos: editar `POSTGRES_PASSWORD` no modifica una base ya inicializada.

Revisar `release.json`, Quadlet y `nginx.conf` (estos no incluyen secretos). Validar:

```bash
QUADLET_UNIT_DIRS="$release_dir/quadlet" \
  /usr/lib/systemd/system-generators/podman-system-generator --user --dryrun
```

En rootful omitir `--user`. Deben generarse los cuatro servicios y las dependencias de red/volumen sin claves incompatibles. Confirmar además que `ss -ltn` no muestra el puerto propuesto ocupado y que no existen unidades/red/volumen homónimos ajenos. Las aplicaciones esperan PostgreSQL de forma acotada; la revisión del esquema debe coincidir. Las migraciones no corren dentro de cada API.

## 7. Datos de la primera instalación

La ruta ordinaria crea `meteocentro-db-data` vacío y empieza a recoger observaciones en el servidor. La base local permanece intacta; los históricos que solo estén allí no aparecerán automáticamente en el remoto. Retirar las copias de seguridad no implica borrar datos existentes.

Si se quiere trasladar el archivo local, concretar ese traslado antes de ejecutar `apply`; no se realiza como efecto secundario del despliegue. Se conserva `--adopt-volume NOMBRE_EXACTO` para una primera instalación sobre un volumen existente previamente revisado, con el mismo usuario/contraseña PostgreSQL y versión mayor compatible. El nombre debe coincidir con `VolumeName=` del Quadlet renderizado. Una actualización ordinaria rechaza cambiar el volumen. No montar nunca el mismo volumen en dos PostgreSQL simultáneos.

## 8. Instalar la versión y crear administrador

Tras revisar el inventario y los archivos concretos, en rootless:

```bash
python3 deploy/scripts/ops.py apply --release "$release_dir" \
  --state "$HOME/meteocentro/state"
```

Para rootful: `sudo python3 ...`, con rutas rootful. El script valida modo, configuración, cgroups, generador, reserva de espacio y colisiones; descarga imágenes exactas antes de detener nada, guarda un intento, instala únicamente sus Quadlet, arranca DB, ejecuta la migración una vez y arranca API/worker/web. En actualizaciones detiene primero estos tres servicios antes de migrar; no genera copias. No escribe en Nginx ni obtiene certificados. No hay despliegue automático por push.

Para administrar los servicios:

```bash
systemctl --user status meteocentro-db meteocentro-api meteocentro-worker meteocentro-web
podman exec -it meteocentro-api python -m meteocentro.admin_cli --help
podman exec -it meteocentro-api python -m meteocentro.admin_cli create NOMBRE_ADMIN
python3 deploy/scripts/ops.py smoke http://127.0.0.1:8088
podman exec meteocentro-api python -m meteocentro.operations
```

La CLI pide contraseña sin eco; ver sintaxis exacta en `--help`. No poner contraseñas en argumentos. `smoke` prueba HTTP/SPA y que API, CSV y administración sin sesión devuelven 401. La ingestión puede figurar degradada hasta la primera publicación: revisar último dato real, no solo éxito HTTP. Las claves nunca se envían a API/web.

**No ejecutar `systemctl enable meteocentro-api.service`**: las unidades generadas se habilitan mediante `[Install]` del Quadlet. `daemon-reload` y `start` son suficientes; para rootless, `linger` permite mantenerlas al cerrar sesión. El comportamiento al cerrar SSH y reiniciar el host se debe ensayar posteriormente en una ventana autorizada.

## 9. Añadir exclusivamente el nuevo vhost y HTTPS

Mantener los certificados y vhosts existentes. La plantilla HTTPS requiere certificado ya disponible. Si el dominio es nuevo, crear primero un vhost **solo de ese dominio** en puerto 80, con `/.well-known/acme-challenge/` apuntando a `/var/lib/meteocentro-acme` y el resto devolviendo 503. Validar DNS A/AAAA, `nginx -t` y recargar. Obtener su certificado con el mecanismo habitual; por ejemplo `certbot certonly --webroot -w /var/lib/meteocentro-acme -d DOMINIO_ELEGIDO`. No pedir que Certbot reconfigure vhosts ajenos. Dar un nombre propio al vhost HTTP temporal y retirar solo su enlace al instalar el definitivo, para evitar dos vhosts del mismo dominio en puerto 80.

Cuando exista el certificado:

```bash
# Verificar primero que el nombre no pertenece a un site previo.
test ! -e /etc/nginx/sites-available/meteocentro
sudo install -m 0644 "$release_dir/nginx.conf" /etc/nginx/sites-available/meteocentro
sudo ln -s /etc/nginx/sites-available/meteocentro /etc/nginx/sites-enabled/meteocentro
sudo nginx -t && sudo systemctl reload nginx
python3 deploy/scripts/ops.py smoke https://DOMINIO_ELEGIDO
```

Si `nginx -t` falla, **no recargar**; retirar solo el enlace nuevo y corregir. Si ya existe un vhost Meteocentro con certificado, revisar el diff y adaptar únicamente el vhost propio. No abrir 8088, 8000 o 5432 en el firewall. Comprobar desde otro equipo que solo HTTPS llega a la app, y que los vhosts previos responden como en el inventario. Verificar login, cookie Secure/HttpOnly/SameSite, mapa Madrid, ambas fuentes, históricos, exclusiones y CSV privado en el dominio real. Confirmar renovación del certificado con el mecanismo existente.

## 10. Actualizar y resolver fallos

1. Seleccionar nuevo SHA/manifiesto exacto; construir/descargar sin cambiar la versión en uso.
2. Leer migraciones y recursos. Para 0006 solo se añade latido; los cambios antiguos de particionado requieren su mantenimiento específico. Comprobar espacio en el almacenamiento Podman para imágenes, crecimiento y migraciones; el script solo exige una reserva mínima de 5 GiB en el filesystem de `--state`.
3. Renderizar en **otro** directorio con igual modo, config, puerto e imagen DB. Revisar diff. `apply` rechaza cambiar esos campos en una actualización ordinaria.
4. Ejecutar `apply` con el mismo `--state`. Registra la versión anterior, detiene escritores y migra una vez. Conservar el directorio `attempt-*` y su estado. El enlace `state/current` cambia solo al pasar HTTP; `state/previous` apunta a la versión anterior. Estos enlaces son archivos operativos, no etiquetas de imagen.
5. Revisar `operations`, fuentes y datos nuevos después de su cadencia; comprobar HTTPS/login/exclusión/históricos. No reinstalar el vhost en una actualización ordinaria.

Si una etapa falla, el script detiene API/web/worker y conserva DB y el intento fallido. No cambia automáticamente a una imagen antigua contra un esquema nuevo. Inspeccionar localmente logs y estado. Un primer intento fallido deja unidades instaladas: reanudar de forma explícita tras comprobar el motivo; no borrar datos para forzar la comprobación de colisiones.

**Reversión con esquema compatible:** detener API/web/worker y reinstalar solo Quadlet de `state/previous/quadlet`, `daemon-reload`, arrancar y comprobar. La API verifica revisión exacta: incluso una migración aditiva exige que la imagen anterior y `alembic_version` coincidan. En 0006→0005, con las tres aplicaciones detenidas, puede ejecutarse `alembic downgrade 0005_administration` desde la imagen nueva; solo elimina latido. Verificar el downgrade ensayado en el estado antes de usarlo. No generalizar a otras migraciones.

**Esquema incompatible o incidente:** mantener API/web/worker detenidos, conservar el volumen y revisar logs y `alembic_version`. Corregir hacia delante con una versión compatible o aplicar solo una reversión de esquema cuya conservación de datos esté verificada. Cambiar imágenes no recupera datos ni revierte una migración. No hay recuperación automática a un estado anterior de la base.

## 11. Diagnóstico, recursos y prueba de reinicio futura

```bash
systemctl --user status meteocentro-db meteocentro-api meteocentro-worker meteocentro-web
journalctl --user -u meteocentro-worker -n 80 --no-pager
podman logs --tail=80 meteocentro-worker
podman exec meteocentro-api python -m meteocentro.operations
podman exec meteocentro-worker python -m meteocentro.worker --status
podman stats --no-stream meteocentro-db meteocentro-api meteocentro-worker meteocentro-web
podman exec meteocentro-db psql -U meteocentro -d meteocentro \
  -c 'SELECT pg_database_size(current_database());'
```

`operations` devuelve código 0 saludable, 1 degradado, 2 configuración/DB no disponible. Es lectura local, sin descargar meteorología. El panel privado → Fuentes muestra latido, siguiente trabajo, retrasos y estado de cada proveedor. El latido cada 20 s acredita el proceso; tras 90 s sin él se avisa. Trabajos vencidos y edad del dato detectan bloqueos de ingesta aunque el proceso respire. `responding_without_fresh_data` distingue un HTTP correcto que repite datos antiguos; no afirma que el proveedor deba publicar a cada consulta. AEMET usa 5.400 s y Meteoclimatic 2.700 s de antigüedad por defecto. Ningún diagnóstico envía email/mensajería ni reinicia por falta de datos meteorológicos.

Systemd reinicia fallos con pausa y máximo de cinco arranques en cinco minutos; tras corregir una causa persistente, `systemctl --user reset-failed NOMBRE` y `start`. DB/API/web tienen healthcheck; web re-resuelve la dirección de API al recrearla. Los logs de contenedor usan `k8s-file` con 20 MB por servicio; los del servicio permanecen sujetos a la política existente de journald del host. No modificar esa política global sin revisar otros servicios. Límites iniciales: DB 1 GiB, API 512 MiB, worker 768 MiB/1 CPU, web 128 MiB; no son consumo observado. Medir antes de aceptar capacidad y ajustar solo los Quadlet propios.

Tomar `pg_database_size`, número de observaciones y tamaño del volumen en dos fechas separadas por al menos 24 h; registrar el delta real y espacio libre. El pequeño ensayo sintético no estima crecimiento de producción. No hay purga automática del histórico.

Para probar posteriormente persistencia: registrar observaciones y una exclusión sintética identificada; reiniciar exclusivamente los cuatro servicios, comprobar login/404 del excluido y archivo; cerrar SSH, volver y comprobar latido/ingesta. El **reinicio del host** afecta a otros servicios y se programa aparte: registrar vhosts/servicios, reiniciar solo en la ventana acordada y repetir inventario, HTTPS, latido y próxima publicación. No se ha realizado ese ensayo en esta preparación.

## Referencias técnicas

- [Quadlet 4.9.3: rutas, generación y sección Install](https://docs.podman.io/en/v4.9.3/markdown/podman-systemd.unit.5.html).
- [Manual actual de Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html).
- [Nginx: comprobación y recarga de configuración](https://nginx.org/en/docs/beginners_guide.html).
- [Nginx: proxy y cabeceras](https://nginx.org/en/docs/http/ngx_http_proxy_module.html).

Estos documentos respaldan la configuración; las evidencias locales y los límites concretos de esta entrega figuran en [ESTADO.md](ESTADO.md).

### Reanudar un primer intento fallido

`apply` no adopta automáticamente unidades preexistentes si aún no existe `state/current`. Para recuperar, inspeccionar `state/attempt-*/status.json`, escoger el intento exacto y resolver primero su error. Las tres aplicaciones deben estar detenidas. Validar el generador sobre `attempt-FECHA/release/quadlet`; arrancar DB y ejecutar la imagen exacta con `python -m meteocentro.start migrate`, usando red `meteocentro`, `meteocentro.env` y `database.env`. Arrancar API/worker/web y ejecutar el smoke del paso 8. Solo si pasa, crear `state/current` apuntando a ese `attempt-FECHA/release` y dejar el resultado registrado. Si no se puede explicar el estado del esquema, mantener las aplicaciones detenidas hasta diagnosticarlo; no eliminar unidades/volúmenes para ocultar el fallo.
