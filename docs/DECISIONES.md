# Decisiones del proyecto

Fecha inicial: 9 de septiembre de 2026. Registrar aquí cambios de criterio para que distintas sesiones de Codex no tomen decisiones contradictorias.

## Requisitos del usuario

- Inspiración en el estilo y las características meteorológicas de Suremet, especialmente mapa e históricos por estación.
- Provincias de Madrid, Ávila, Segovia y Guadalajara completas.
- Petición inicial de AEMET, Meteoclimatic y Wunderground. Actualización: el usuario no tiene clave WU y prefiere AEMET y Meteoclimatic si WU resulta caro; la revisión de coste conduce a proponer WU como ampliación aplazada.
- Posibilidad de eliminar estaciones que el administrador considere erróneas.
- Descubrimiento periódico de nuevas estaciones y recogida automática de observaciones.
- Despliegue en contenedores Podman en el servidor habitual mediante SSH.
- Entrega inicial de documentación global, documentos por fase y repositorio de GitHub; no se ha solicitado implementar ni desplegar todavía.

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
| ¿Está disponible la clave AEMET? | Pruebas reales de fase 0 | Preparar contratos y continuar con las tareas independientes; no pedir claves por chat |
| ¿Hay acceso WU adecuado a un coste aceptable? | Solo si se reactiva la ampliación | No implementar ni activar WU; conservar extensibilidad |
| ¿Qué uso e importación permite Meteoclimatic para esta app? | Ingestión, conservación y publicación de esa fuente | Resolver las condiciones de los feeds y archivo por separado |
| ¿Se quiere reconsiderar WU con otro acuerdo de acceso? | Fuera de la primera versión | Mantenerlo aplazado y usar la revisión de coste como referencia |
| ¿Consulta pública o solo personal? | Publicación en fase 7 | Configuración privada inicial |
| ¿Qué dominio y puerto libre se usarán? | Configurar el vhost | Usar parámetros sin tocar Nginx existente |
| ¿Cuántos años anteriores interesa importar? | Ampliar el piloto de histórico | Piloto de 30 días, reanudable y ampliable |

El usuario podrá cambiar la retención, pero el proyecto no debe activar una purga que reduzca el archivo existente sin dejar claros sus efectos.

La revisión de precios está en [COSTE_Y_ACCESO_DATOS.md](COSTE_Y_ACCESO_DATOS.md). Se conserva la arquitectura de proveedores para que esta decisión sea reversible sin rehacer el mapa ni los históricos.
