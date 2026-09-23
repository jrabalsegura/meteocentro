# Administración privada — fase 6

Implementada el 21 de septiembre de 2026. El acceso está en `/gestion`; la lectura es privada por defecto. La base existente necesita la migración `0005_administration` y una cuenta creada por el operador. No hay registro abierto, cuenta automática ni contraseña inicial.

## Preparación y acceso

Configurar `PRIVATE_READ=true`, `APP_ORIGIN` con el origen exacto usado por el navegador (esquema, host y puerto, sin barra final) y `SESSION_HOURS=12`. El ejemplo usa `http://localhost:5173`; si se abre mediante `127.0.0.1`, configurar ese origen. En producción, `ENVIRONMENT=production` exige HTTPS y activa la cookie `__Host-meteocentro`, `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/`, sin `Domain`. En desarrollo se permite HTTP local. La API nunca recibe la clave AEMET desde Compose; solo el worker la necesita.

Después de aplicar la migración y arrancar la imagen nueva de la API, crear la cuenta en una terminal interactiva:

```sh
docker compose exec api python -m meteocentro.admin_cli create propietario
```

La contraseña se introduce dos veces sin eco, requiere entre 15 y 1.024 caracteres y nunca se acepta como argumento de la línea de comandos. El comando rechaza entrada sin terminal. Para recuperación, `password propietario` cambia la contraseña y revoca sus sesiones; `revoke propietario` revoca todas sin cambiarla. Fuera de Compose se usa el mismo módulo con `DATABASE_URL` en el entorno. No sobrescribir un `.env` existente con el ejemplo.

Las sesiones son tokens aleatorios de 256 bits; PostgreSQL conserva únicamente su hash SHA-256, usuario, caducidad absoluta y revocación. La contraseña usa scrypt con sal aleatoria, N=131072, r=8 y p=1, conforme a la [guía de almacenamiento de contraseñas de OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html). No se añadieron dependencias: se utiliza `hashlib` de Python.

Cada escritura verifica la sesión, el usuario habilitado, el origen exacto, JSON y un token CSRF asociado a la sesión, comparado en tiempo constante. El login verifica origen y formato antes de procesar credenciales. `SameSite` es una defensa adicional; no sustituye estas comprobaciones. Referencia: [prevención CSRF de OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html). Los errores de validación no reflejan el cuerpo recibido ni la contraseña.

Los intentos se reservan en PostgreSQL antes del cálculo de contraseña: cinco por usuario, veinte por dirección y sesenta globales en quince minutos. Se incluyen intentos correctos y fallidos; reiniciar la API o abrir otro navegador no reinicia el límite. La API usa la dirección del transporte, sin interpretar por sí misma `X-Forwarded-For`. La confianza en el proxy y sus direcciones se configurará al desplegar; un proxy local puede agrupar usuarios en el mismo límite por IP. No se omiten los límites por usuario y global.

La lectura privada cubre todas las rutas `/api/v1`, incluidas series, CSV, documentación OpenAPI y archivo administrativo. Solo login, consulta mínima de sesión y salud son accesibles sin sesión. `PRIVATE_READ=false` permite leer los productos elegibles sin autenticarse, pero nunca permite acceder al panel o mutar datos. La decisión de exposición externa sigue pendiente de fase 7 y de los permisos de las fuentes.

## Uso del panel

- **Estaciones:** buscar por nombre o ID externo, abrir la ficha de gestión, revisar metadatos y orígenes, eliminar o restaurar. La ficha permite consultar observaciones y resúmenes conservados, incluso de estaciones excluidas.
- **Revisión:** posiciones pendientes o propuestas, aprobación con evidencia y precisión; posibles coincidencias entre redes, confirmación de que son distintas, vinculación a una estación elegida mediante búsqueda o separación de una vinculación errónea. No hay fusiones por proximidad. Una decisión explícita de estaciones distintas no se revierte durante el redescubrimiento.
- **Excluidas:** exclusiones de estación y de origen, con motivo, fecha, actor y redes afectadas. Restaurar una estación conserva las exclusiones propias de sus orígenes y las revisiones pendientes.
- **Fuentes:** estado, capacidades, consulta más reciente, edad real de observaciones, cuota consumida y bloqueo. Solo se comunica el estado de la credencial, nunca su valor. Las pausas por acceso o contrato se resuelven en el servidor mediante los comandos de operación existentes.
- **Descubrimiento:** solicitar una revisión o verificar un ID mediante un trabajo persistente. El panel muestra progreso y contadores de nuevos, revisados, excluidos y pendientes. La cobertura de un lote no acredita un censo completo. Las coordenadas Meteoclimatic recién descubiertas se resuelven en el trabajo ordinario de catálogo; mientras tanto permanecen en revisión.
- **Trabajos:** estado, intentos, próxima ejecución y último resultado. El reintento solo se permite cuando ha vencido la espera; no salta cuotas, pausas ni cancelaciones administrativas.
- **Auditoría:** cambios con fecha y usuario. Desde aquí también se cierran todas las sesiones del administrador; «Salir» revoca la sesión del navegador actual.

La confirmación **Eliminar de la app** muestra nombre y redes vinculadas, explica la retirada de consultas y la cancelación de recogidas individuales, y permite un motivo opcional. Se conserva el archivo. La acción nunca envía órdenes de baja a AEMET ni a Meteoclimatic. Restaurar es explícito y advierte de posibles huecos durante el periodo excluido.

Pausar solo un origen no retira otros orígenes elegibles de la estación. Su procedencia sigue visible. Vincular un origen a una estación excluida aplica inmediatamente esa exclusión y cancela su trabajo individual; moverlo fuera de una estación excluida conserva una exclusión propia heredada. Separar conserva los IDs de origen y las series. Si faltan coordenadas para la nueva estación, queda en revisión.

## Exclusión, concurrencia y cachés

La transacción administrativa incluye exclusión o revocación, auditoría con usuario, incremento de `catalog_version`, cancelación de trabajos individuales y finalización de sus ejecuciones en curso. La operación es idempotente. No se cambian los UUID de estaciones u orígenes ni se borran observaciones, resúmenes o revisiones.

Las operaciones de gestión y el encolado histórico se coordinan con un bloqueo asesor. El orden de bloqueo compatible con el worker es trabajo individual → catálogo → identidad externa → estación. Las verificaciones manuales de ID también se bloquean porque pueden adquirir una asociación a origen mientras están ejecutándose. El encolado comprueba otra vez la elegibilidad tras obtener el bloqueo. Las consultas y escritores conservan las comprobaciones comunes de exclusión de estación, origen e identidad externa.

Cancelar retira el propietario y arrendamiento del trabajo. Un importador que termina una descarga tras la exclusión pierde su autorización para confirmar resultados. Una restauración puede reanudar un trabajo cancelado por exclusión si su origen vuelve a ser elegible, pero **no devuelve el token al worker anterior**. Los lotes nacionales permanecen activos para las demás estaciones; descartan los registros excluidos bajo los mismos bloqueos de escritura. Si el escritor ya estaba confirmando datos, la exclusión espera su transacción y oculta también esos datos antes de responder.

La aplicación sigue sin caché HTTP ni archivos CSV reutilizables. Todas las respuestas API, incluidos errores, tienen `Cache-Control: no-store`, `Pragma: no-cache`, `Vary: Cookie` y `X-Content-Type-Options: nosniff`. Las lecturas exponen `X-Catalog-Version`; cada nueva consulta resuelve la elegibilidad en PostgreSQL. No se responde con un CSV antiguo ni con un 304 que reutilice datos anteriores. Los agregados conservados se filtran por elegibilidad, igual que el detalle. La versión cambia en las operaciones del panel; la corrección de las lecturas no depende de esa cabecera ni de una caché en memoria.

El mapa y las fichas abiertos refrescan a los sesenta segundos o al volver a la pestaña; el panel revisa trabajos cada quince segundos. Una sesión revocada o caducada desmonta la vista privada cuando se comprueba el acceso. Se conserva el centro inicial de Madrid y la prioridad de las vistas válidas de URL. Los datos que una persona ya descargó o una respuesta iniciada antes de la exclusión no pueden retirarse retroactivamente; las **nuevas peticiones** quedan protegidas por el backend.

## Descubrimiento seguro

La API solo admite `aemet` o `meteoclimatic` e IDs con el formato conocido; rechaza URLs, proveedores aplazados y campos adicionales. La verificación usa los adaptadores existentes y sus destinos permitidos, dentro del worker, con menor prioridad que actualidad. Para AEMET comprueba producto actual e inventario histórico; para Meteoclimatic comprueba presencia en el XML. No registra un ID inventado como verificado ni interpreta un ID ausente como estación eliminada.

Se reutilizan trabajos activos y claves de deduplicación. Las solicitudes manuales se limitan a una por proveedor cada quince minutos y consumen el mismo presupuesto persistente que la programación normal. La API no hace HTTP a los proveedores. Un error técnico usa un código seleccionado; el panel nunca devuelve sobres, cursores completos, robots almacenados, claves, cabeceras, URLs firmadas ni trazas.

## API y esquema

- `/api/v1/auth/session`, `/login`, `/logout`, `/revoke-all` bajo el prefijo de autenticación.
- `/api/v1/admin/stations`, `/{id}`, `/{id}/archive`, `/{id}/exclude`, `/{id}/restore`.
- `/api/v1/admin/sources/{id}/exclude`, `/restore`, `/review/location`, `/review/link`, `/review/split`.
- `/api/v1/admin/duplicates`, `/{id}/distinct`; `/providers`, `/discovery/{provider}`, `/jobs`, `/jobs/{id}/retry`, `/audit` bajo el prefijo administrativo.

Las escrituras son POST y requieren cabecera `X-CSRF-Token` y `Origin`. El contrato detallado está en OpenAPI autenticado. La migración `0005_administration` añade `login_throttles` y `catalog_version`; reutiliza usuarios, sesiones, exclusiones y auditoría existentes. Su downgrade elimina esos dos estados nuevos, por lo que no debe usarse como mecanismo para reiniciar límites en explotación.

El volumen PostgreSQL conserva catálogo, observaciones, exclusiones, auditoría, cuentas, sesiones y trabajos entre reinicios. La restauración de una estación desde el panel reactiva su elegibilidad y conserva el archivo; esta función sigue disponible. Las copias de seguridad de la base quedan fuera del proyecto por decisión del usuario del 23-9-2026; véase [OPERACION.md](OPERACION.md).

## Evidencia local y límites

- **176 pruebas** de backend correctas, **23 nuevas de fase 6**, sobre PostgreSQL 17 temporal. HTTP meteorológico sintético. La enumeración desde OpenAPI comprueba autorización en todas las rutas protegidas; también se verifican cookies de producción, contraseña, límites persistentes, caducidad, rotación, revocación, usuario deshabilitado y ausencia de contraseñas en errores.
- Lecturas antes y después de exclusión: mapa/búsqueda/extremos, lista, ficha, últimos valores, observaciones, resúmenes originales, series, diarios, efemérides, diarios de red y CSV. El mismo CSV solicitado con cabeceras condicionales devuelve 404 tras excluir. El archivo privado permanece accesible al administrador.
- Carreras reales con sesiones PostgreSQL distintas: exclusión durante descarga por estación y origen, importación cancelada y restaurada antes de terminar, escritor bloqueado tras una exclusión sin confirmar y exclusión esperando al escritor. La contención se observa en `pg_locks`. No se guardan resultados del importador que perdió su token.
- **14 recorridos Playwright** con HTTP sintético correctos, más **un recorrido separado con navegador, API y PostgreSQL reales**. Este último verifica login, confirmación, retirada de ficha/lista, archivo privado, restauración, auditoría y logout, con una estación y cuenta sintéticas. Se inspeccionaron capturas en escritorio y móvil de 390 px; sin desbordamiento horizontal.
- Reinicio real del PostgreSQL temporal: una exclusión confirmada siguió activa; un worker nuevo redescubrió el mismo ID con fixture y lo descartó, conservando una identidad y una observación anterior. La restauración explícita recuperó la lectura. No se reiniciaron contenedores de desarrollo ni servicios remotos.
- Alta de cuenta por CLI comprobada en una terminal real contra la base sintética: dos solicitudes sin eco y hash almacenado. Ninguna cuenta creada en la base de uso del propietario.
- Alembic upgrade/downgrade/upgrade y `check` correctos en la base temporal; Ruff, formato, compilación frontend, Compose `config --quiet` y `git diff --check` correctos. Persisten los avisos conocidos de tamaño de chunks y deprecaciones. Se actualizó CI, sin afirmar ejecución remota.

No se llamaron APIs meteorológicas reales, no se desplegó ni se aplicó la migración a la base local en uso. No se copiaron claves ni se alteraron las condiciones de publicación de Meteoclimatic. Wunderground continúa aplazado. Antes de usar esta versión en el Compose existente, hay que actualizar API/worker, aplicar la migración y crear la cuenta; mezclar esta web con una API anterior no proporciona el acceso nuevo. La preparación de producción y el reinicio de contenedores quedan en fase 7.

### Repetir las pruebas

La suite ordinaria usa `TEST_DATABASE_URL` terminado en `_test` y una base desechable; nunca apuntar al archivo local en uso. Ejecutar `backend/.venv/bin/pytest -q` y, en `frontend`, `npm run build` y `npm run test:e2e`. Las pruebas públicas anteriores usan explícitamente modo público; fase 6 comprueba también el modo privado por defecto.

El recorrido integrado `frontend/tests/admin-real.spec.ts` se omite en la suite de fixtures. Para ejecutarlo, preparar otra base vacía terminada en `_ui_test`, fijar `DATABASE_URL` y una contraseña sintética mediante `E2E_ADMIN_PASSWORD`, y ejecutar `backend/.venv/bin/python tests/seed_admin_ui.py`. Levantar API y Vite aislados con `PRIVATE_READ=true`, `APP_ORIGIN` igual al origen de esa web y `VITE_API_PROXY_TARGET` apuntando a esa API. Lanzar Playwright con `E2E_REAL_ADMIN=1`, la misma contraseña y `E2E_BASE_URL`. El sembrador rechaza otras bases y una fixture ya existente. Las evidencias locales quedan en `runtime/phase6-tests/`, ignorado por Git.
