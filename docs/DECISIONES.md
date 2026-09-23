# Decisiones del proyecto

Fecha inicial: 9 de septiembre de 2026. Registrar aquí cambios de criterio para que distintas sesiones de Codex no tomen decisiones contradictorias.

## Requisitos del usuario

- Inspiración en el estilo y las características meteorológicas de Suremet, especialmente mapa e históricos por estación.
- Vista inicial solicitada el 21-9-2026: Madrid ciudad, centro `[-3.70765, 40.42437]` (longitud, latitud), zoom `10.69`, tomada del encuadre que el usuario tenía abierto. Se usa al abrir sin `view` válido; una vista explícita en la URL prevalece. Mantener esta preferencia en fases posteriores y conservar el botón «Ver las cuatro provincias» para el encuadre regional. No impone filtros de provincia ni de red.
- Provincias de Madrid, Ávila, Segovia y Guadalajara completas.
- Petición inicial de AEMET, Meteoclimatic y Wunderground. Actualización: el usuario no tiene clave WU y prefiere AEMET y Meteoclimatic si WU resulta caro; la revisión de coste conduce a proponer WU como ampliación aplazada.
- Posibilidad de eliminar estaciones que el administrador considere erróneas.
- Descubrimiento periódico de nuevas estaciones y recogida automática de observaciones.
- Despliegue en contenedores Podman en el servidor habitual mediante SSH.
- Decisión del 23-9-2026: web personal de consulta sin sistema de copias de seguridad. Se retiran comandos, copias previas a actualizar, timer, retención, destino externo y ensayo de restauración. Se conserva el volumen PostgreSQL y la comprobación de datos/exclusiones tras migraciones y reinicios; no se borran históricos ni artefactos locales existentes.
- Se solicitaron las fases 0–7; el usuario ha indicado expresamente que la fase 7 se prepare sin desplegar.
- Las coordenadas públicas de Meteoclimatic mostradas a minutos son suficientemente precisas para situar aproximadamente una estación en el mapa; no se exige precisión a segundos. El 21-9-2026 el usuario pide mostrar también las estaciones próximas a un límite provincial: la provincia se asigna según el punto publicado y puede ser aproximada; la incertidumbre de minutos deja de bloquear su publicación.

## Propuestas de trabajo

| Decisión | Propuesta | Motivo |
| --- | --- | --- |
| Nombre | Meteocentro | Describe el ámbito; es provisional |
| Repositorio | `meteocentro`, privado en la cuenta autenticada | Preparar el desarrollo sin publicar información del proyecto |
| Cliente | React, TypeScript, MapLibre y ECharts | Mapa con valores y gráficos de estación |
| Servidor | FastAPI y PostgreSQL | Adaptadores en Python y archivo propio |
| Background | Un worker separado con trabajos persistidos | Sigue funcionando sin usuarios y reanuda tras reinicios |
| Eliminación | Exclusión reversible con identidad conservada | Evita la reaparición durante el descubrimiento |
| Nuevas estaciones | Incorporación automática de casos válidos; revisión de dudas | Reducir mantenimiento sin aceptar ubicaciones o duplicados inciertos |
| Exposición inicial | Despliegue privado; administración siempre privada | La lectura pública se decide antes de publicar |
| Coste de APIs | No contratar ningún servicio por defecto | El acceso de pago requiere elegir un presupuesto |
| Fuentes de la primera versión | AEMET y Meteoclimatic; WU aplazado | Evitar el coste recurrente de WU y avanzar sin su clave |
| Histórico inicial | Probar primero 30 días; ampliar después según cobertura y cuota | Validar la importación antes de una descarga masiva |
| Runtime | Podman rootless y Quadlet | Encaje con el flujo operativo; validar soporte en el host |
| Publicación de versiones | CI de comprobación y despliegue lanzado manualmente | Mantener control sobre cuándo se cambia el servidor |

Estas son decisiones técnicas propuestas, no preferencias adicionales atribuidas al usuario. Se pueden ajustar sin cambiar los requisitos principales.

## Preguntas que deben resolverse antes de su dependencia

| Cuestión | Cuándo hace falta | Qué sucede mientras tanto |
| --- | --- | --- |
| ¿Está disponible la clave AEMET? | Resuelto en fase 0: clave local de Radar App probada el 14-9-2026 | No copiarla a Git; prever renovación antes de su expiración JWT del 31-10-2026, sujeta a validez real |
| ¿Hay acceso WU adecuado a un coste aceptable? | Solo si se reactiva la ampliación | No implementar ni activar WU; conservar extensibilidad |
| ¿Qué uso e importación permite Meteoclimatic para esta app? | Uso local privado no comercial resuelto con licencia publicada; revisar antes de exposición externa o importar archivo diario | Conservar originales y atribución; no presumir autorización de derivados públicos |
| ¿Se quiere reconsiderar WU con otro acuerdo de acceso? | Fuera de la primera versión | Mantenerlo aplazado y usar la revisión de coste como referencia |
| ¿Consulta pública o solo personal? | Publicación en fase 7 | Configuración privada inicial |
| ¿Qué dominio y puerto libre se usarán? | Configurar el vhost | Usar parámetros sin tocar Nginx existente |
| ¿Cuántos años anteriores interesa importar? | Ampliar el piloto de histórico | Piloto de 30 días, reanudable y ampliable |

El usuario podrá cambiar la retención, pero el proyecto no debe activar una purga que reduzca el archivo existente sin dejar claros sus efectos.

La revisión de precios está en [COSTE_Y_ACCESO_DATOS.md](COSTE_Y_ACCESO_DATOS.md). Se conserva la arquitectura de proveedores para que esta decisión sea reversible sin rehacer el mapa ni los históricos.

## Decisiones menores de fase 2 — 16 de septiembre de 2026

- Cola y cuotas compartidas en PostgreSQL; liderazgo durante cada transacción de programación, propietario con arrendamiento y confirmación separada de los lotes guardados. No se añade otro servicio de colas.
- Contar todos los GET HTTP, no solo el sobre API: 400 diarios, 20 por minuto y reserva de 220 para observaciones. Son límites locales conservadores, ajustables; no hay gasto contratado.
- Prioridad de `prec` y alternativa `pacutp`, conservando ambos valores en calidad. No sumar sensores ni calcular diarios en esta fase.
- Metadatos versionados por hash y refrescados cada 24 horas; pausar si cambian las unidades o periodos consumidos. Campos meteorológicos adicionales e importación climatológica quedan para incrementos posteriores.
- Catálogo por unión de IDs, con capacidad histórica explícita y sin reactivar exclusiones. Un desplazamiento superior a 0,002 grados exige revisión; el inventario no sustituye automáticamente la posición actual.
- Perfil local `ingestion` explícito en Compose. La clave AEMET solo se entrega al worker. Una aprobación expresa en esta tarea permite usar temporalmente la clave de Radar App para el piloto limitado, sin copiarla a configuración ni a Git.

## Decisiones menores de fase 3 — 16 de septiembre de 2026

- Revisión posterior de esta misma fase: Meteoclimatic se habilita localmente para archivo privado no comercial bajo su aviso legal y CC BY-NC-ND 3.0, conservando originales, atribución y licencia. La referencia registra esa interpretación del uso publicado, no un permiso individual. El ejemplo genérico sigue desactivado; antes de publicación externa se revisará el producto concreto.
- Un XML nacional cada 15 minutos combina observaciones y detección; la revisión diaria reutiliza el lote. Se mantiene el patrón nacional ya comprobado y el filtro de prefijos verificados, con clasificación final por polígonos. No se añade RSS. Un trabajo de catálogo separado recoge únicamente coordenadas a minutos y altitud de las fichas públicas, comprueba robots, cachea 30 días y usa cuotas comunes (300 GET/día, 20/minuto, reserva de 110 para actualidad).
- Sin coordenadas, revisión. La entrada manual conserva evidencia y precisión; a minutos se exige que el rectángulo de incertidumbre completo quede en una provincia. No se inventan posiciones a partir de municipios.
- Presión relativa al mar verificada en documentación primaria. Contador y extremos diarios se exponen con tipos y nombres separados, y `provider_day_timezone_unknown`: la fuente admite UTC y día civil sin identificar la opción en el XML. No se inventan medianoches, intervalos ni horas de los extremos; no se suman contadores. Los originales quedan en calidad. Esto resuelve el contrato de instantáneas reportadas, sin prometer diarios cerrados homogéneos.
- Tres ausencias en revisiones diarias señalan el origen sin borrarlo. Candidatos a duplicado: 250 m entre redes, o 1 km con precisión a minutos/nombre coincidente (umbral inicial de 3 km reducido por petición del usuario el 21-9-2026); la vinculación siempre es explícita y conserva exclusiones y series por origen.
- El alta manual acredita registro, no emisión actual. La API muestra estados de proveedor; una desactivación oculta esa red y conserva el archivo. Una pausa técnica permite seguir consultando el archivo autorizado.

## Aclaración y configuración AEMET — 16 de septiembre de 2026

- El usuario reitera que las coordenadas Meteoclimatic a minutos son suficientes. No se solicitará mayor precisión como condición para incorporar la red; el catálogo ya obtiene esas ubicaciones de las fichas públicas conforme al contrato revisado.
- El usuario autoriza expresamente copiar la clave AEMET desde Radar App a la configuración privada de Meteocentro, sin mostrarla. Se copia únicamente `AEMET_API_KEY` al `.env` local, ignorado por Git y con permisos 0600; se preservan las demás variables y la configuración de Radar App. Esta decisión amplía el permiso anterior, que se limitaba a usarla en memoria.

## Decisiones menores de fase 4 — 19 de septiembre de 2026

- Cartografía WMTS IGN topográfica y clara, sin claves ni gasto; límites provinciales ya versionados. MapLibre 6.10.0, cargado aparte y bloqueado; la versión inicial ensayada se descartó tras la auditoría de dependencias.
- Proyección por lote sin caché pública: mapa y tabla comparten población filtrada, no solo la parte visible. Hasta 2.000 resultados en UI y límite de contrato de 5.000, truncamiento visible y extremos desactivados si la población es parcial.
- Preferencia por valor reciente utilizable, después AEMET/Meteoclimatic; origen individual visible. Se mantienen 90/45 minutos de frescura. Extremos con tipos, unidades y periodos idénticos; las instantáneas explican su rango de horas.
- Municipios solo cuando consten en metadatos; también se busca texto en el nombre. No se incorpora geocodificación ni se inventan localidades. No hay migración de esquema.
- La lluvia diaria y extremos Meteoclimatic de horario desconocido siguen separados como reportados en ficha. Racha del intervalo e intensidad sin datos no se simulan. Fase 5 completará diarios, gráficos e históricos.
- Pruebas de interfaz offline y chequeo cartográfico real separado; población sintética aislada, no publicada. Sin despliegue ni activación de servicios permanentes. Detalles en `USO_Y_VALIDACION_FASE_4.md`.

## Decisiones menores de fase 5 — 19 de septiembre de 2026

- Series por origen y canal semántico; reducción horaria/diaria con extremos conservados. Versión `local-v1`, medias temporales limitadas a una cadencia, umbral inicial de comparación 90 %, corte horario común para el día provisional y nulos visibles. Reglas y límites en [HISTORICOS_FASE_5.md](HISTORICOS_FASE_5.md).
- AEMET diario independiente de observaciones: lluvia 07–07 UTC; las otras métricas conservan la fecha publicada con límites diarios no acreditados. No se presupone cobertura temporal ni se fabrican horas de extremos. Meteoclimatic diario remoto y CSV de derivados siguen pendientes del permiso/acceso concreto; WU continúa aplazado.
- Piloto de 30 días para un origen, máximo de 20 GET históricos/día por defecto y menor prioridad que actualidad. Las solicitudes solapadas comparten ventanas ya encoladas; reapertura explícita para revisar correcciones. No hay importaciones masivas ni panel administrativo nuevo.
- Se mantiene el diseño de archivo prolongado: particionado mensual real con PK/FKs temporales, tres meses futuros y partición de reserva. Migración bajo ventana de mantenimiento. Retención solo simulada, sin mecanismo de purga habilitable.
- ECharts 6.1.0 fijado en el bloqueo de dependencias, carga diferida y renderizador SVG. El intervalo personalizado se introduce explícitamente en UTC con fin excluido; las horas se muestran en Madrid con CET/CEST. La política externa de publicación sigue en fase 7.

## Decisiones menores de fase 6 — 21 de septiembre de 2026

- Lectura privada por defecto con origen explícito; creación y recuperación de administrador por CLI interactiva. scrypt de la biblioteca estándar, sesiones opacas revocables de doce horas y límites de login en PostgreSQL. Sin nuevas dependencias ni contraseñas automáticas.
- Exclusión representada por la lista persistente, sin destruir el estado de revisión. Auditoría, versión y cancelación de trabajos individuales atómicas; lotes compartidos mantienen su calendario y descartan registros excluidos. Restaurar conserva las exclusiones de orígenes y nunca reutiliza el token del worker cancelado.
- Sin caché de respuestas o archivos CSV reutilizables. Consultas siempre bajo elegibilidad; no-store también en errores y documentación privada. Comprobación de sesión al volver a la pestaña y cada sesenta segundos; panel de trabajos cada quince segundos.
- Búsqueda manual por trabajos existentes o verificación acotada de ID, una solicitud por red cada quince minutos y cuotas comunes. Las pausas técnicas y de permisos se resuelven en el servidor; el botón de reintento respeta su espera y no las evita.
- Pruebas en PostgreSQL y servicios web aislados; sin migrar la base en uso, crear cuentas reales ni desplegar. La aplicación de la versión y los ensayos Podman/Quadlet corresponden al siguiente paso operativo. Detalles en [ADMINISTRACION_FASE_6.md](ADMINISTRACION_FASE_6.md).

## Decisiones de fase 7 — 21 de septiembre de 2026

- Preparación y validación local únicamente; sin SSH, publicación ni cambios en servicios existentes. El usuario pide conservar el flujo de sus otros despliegues. Se han leído las guías locales de finantialApp (Podman rootful/Quadlet/Nginx) y nueva_web_julio (separación local/producción y secretos).
- Plantillas rootless por defecto conforme al resumen; opción rootful explícita para seguir el precedente de finanzas si el inventario del host lo aconseja. No se presupone que aquel inventario siga vigente.
- Aclaración expresa del usuario: Meteoclimatic ya funciona junto con AEMET. El ejemplo de producción conserva **ambas redes activas para uso privado no comercial**, no cambia el `.env` local. Lectura privada obligatoria en el procedimiento inicial; publicación pública y diario/CSV Meteoclimatic siguen fuera del alcance aprobado.
- Bases OCI y acciones CI fijadas por digest/SHA; versión de app por commit completo y digest de registro o ID local. Construcción sin publicación en push/PR; publicación GHCR solo al marcarla en una ejecución manual, sin SSH ni despliegue automático.
- Volumen PostgreSQL nombrado, fuera del checkout, gestión de permisos por imagen oficial/Podman. Cambio de volumen o imagen DB en una actualización ordinaria rechazado; adopción inicial de volumen existente explícita por nombre.
- Migración 0006 solo para latido persistente; monitorización en CLI/panel con edad del dato y retrasos, sin mensajería. Ningún reinicio se dispara porque un proveedor repita meteorología antigua.
- Actualizada el 23-9-2026: se retira el diseño inicial de copias. Las actualizaciones siguen siendo manuales, con parada de escritores y una sola migración. Ante un fallo se conserva DB y se detienen las aplicaciones; una reversión depende de la compatibilidad del esquema, sin recuperación de datos a un instante anterior.

## Ajustes de consulta — 23 de septiembre de 2026

- Al buscar por nombre, municipio o ID, centrar suavemente el mapa en el primer resultado del orden actual del listado, con zoom moderado 12. Esperar la respuesta de esa búsqueda y aplicar el encuadre una sola vez; refrescos, cambios de variable, ordenación y limpieza del buscador conservan la vista. Una búsqueda realizada desde la tabla se encuadra al volver al mapa. Al abrir un enlace, su `view` válido prevalece; una búsqueda sin vista explícita se encuadra al recibir resultados. Sin coincidencias o sin coordenadas en la primera, conservar el encuadre. Mantener el filtro de antigüedad y las reglas de agrupación existentes.
- Separar visualmente marcadores próximos o coincidentes cuando haya espacio libre alrededor, a cualquier zoom. Agrupar cuando la densidad impida leerlos sin colisiones; el zoom no es requisito previo para separarlos. El desplazamiento es solo de pantalla, con una línea hacia la coordenada publicada; no modifica posiciones, identidades ni históricos. Las coincidencias de coordenadas a minutos no prueban duplicidad. Se conserva la revisión de catálogo entre redes (250 m, ampliados a 1 km por precisión/nombre) y la excepción para identidades próximas a una exclusión.
- Por petición del usuario, retirar escala y nota superpuestas al mapa; conservar la leyenda exterior y atribuciones.
- Mostrar mínima, máxima y precipitación en la ficha inicial, además del valor seleccionado y humedad/viento. Distinguir diarios reportados por Meteoclimatic y resumen parcial del archivo civil de Madrid, con procedencia y cobertura. No presentar registros de ayer como datos de hoy.
- Confirmación expresa: la tabla aplica provincia, red, estado, búsqueda y variable del mapa, pero mantiene todas las coincidencias aunque estén fuera del encuadre.
- El usuario solicita desplegar estos cambios mediante GitHub en la instalación existente. Despliegue manual de commit comprobado; no habilitar publicación automática por push.
- Nueva petición del usuario: ocultar por defecto en el mapa las estaciones con más de una hora sin actualizar, conservándolas señaladas en el listado. Casilla reversible en filtros, persistida como `hide_old=0` al desmarcarla. Se usa la edad de la observación seleccionada (`age_seconds` de la API), nunca la recogida; 3.600 s exactos siguen visibles. Sin dato utilizable también se oculta el marcador. Se evalúa de nuevo en cada refresco de 60 s. Los filtros comunes, los umbrales operativos por proveedor, los extremos y los históricos conservan su contrato; la excepción de población afecta solo al mapa de actualidad. Despliegue en GitHub y `ssh remote` expresamente solicitado.
