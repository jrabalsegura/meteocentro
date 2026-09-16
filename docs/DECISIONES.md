# Decisiones del proyecto

Fecha inicial: 9 de septiembre de 2026. Registrar aquí cambios de criterio para que distintas sesiones de Codex no tomen decisiones contradictorias.

## Requisitos del usuario

- Inspiración en el estilo y las características meteorológicas de Suremet, especialmente mapa e históricos por estación.
- Provincias de Madrid, Ávila, Segovia y Guadalajara completas.
- Petición inicial de AEMET, Meteoclimatic y Wunderground. Actualización: el usuario no tiene clave WU y prefiere AEMET y Meteoclimatic si WU resulta caro; la revisión de coste conduce a proponer WU como ampliación aplazada.
- Posibilidad de eliminar estaciones que el administrador considere erróneas.
- Descubrimiento periódico de nuevas estaciones y recogida automática de observaciones.
- Despliegue en contenedores Podman en el servidor habitual mediante SSH.
- Se solicitaron las fases 0, 1, 2 y 3; el despliegue remoto sigue sin solicitarse.
- Las coordenadas públicas de Meteoclimatic mostradas a minutos son suficientemente precisas para situar aproximadamente una estación en el mapa; no se exige precisión a segundos. Los casos cercanos a un límite provincial siguen requiriendo revisión.

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
- Tres ausencias en revisiones diarias señalan el origen sin borrarlo. Candidatos a duplicado: 250 m entre redes, o 3 km con precisión a minutos/nombre coincidente; la vinculación siempre es explícita y conserva exclusiones y series por origen.
- El alta manual acredita registro, no emisión actual. La API muestra estados de proveedor; una desactivación oculta esa red y conserva el archivo. Una pausa técnica permite seguir consultando el archivo autorizado.

## Aclaración y configuración AEMET — 16 de septiembre de 2026

- El usuario reitera que las coordenadas Meteoclimatic a minutos son suficientes. No se solicitará mayor precisión como condición para incorporar la red; el catálogo ya obtiene esas ubicaciones de las fichas públicas conforme al contrato revisado.
- El usuario autoriza expresamente copiar la clave AEMET desde Radar App a la configuración privada de Meteocentro, sin mostrarla. Se copia únicamente `AEMET_API_KEY` al `.env` local, ignorado por Git y con permisos 0600; se preservan las demás variables y la configuración de Radar App. Esta decisión amplía el permiso anterior, que se limitaba a usarla en memoria.
