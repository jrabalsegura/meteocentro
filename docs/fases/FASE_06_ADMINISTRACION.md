# Fase 6 Administración y exclusión de estaciones

## Objetivo

Dar al propietario de la app control directo sobre las estaciones que aparecen y sobre el estado de sus fuentes. La acción principal es eliminar de la app una estación cuyos datos se consideren erróneos, sin que el descubrimiento automático la recupere.

Dependencias: las reglas de exclusión existen desde fase 1 y las respetan las fases 2 a 5. Aquí se añade su interfaz completa y se prueba el recorrido de extremo a extremo.

## Acceso

Crear un administrador mediante un comando del servidor que solicite contraseña de forma segura. Almacenar un hash adecuado, usar sesiones revocables con caducidad y cookies `HttpOnly`, `Secure` en producción y `SameSite`. No incluir registro abierto ni contraseña por defecto.

Proteger operaciones de escritura con autorización en la API y defensa CSRF apropiada para sesiones por cookie, comprobar origen y limitar intentos de acceso. Una ruta oculta en el menú no es protección. El modo de lectura privada también debe cubrir API, CSV y archivos generados.

## Pantallas

| Pantalla | Acciones y datos |
| --- | --- |
| Estaciones | Buscar, ver estado, revisar metadatos, eliminar y restaurar |
| Revisión | Coordenadas dudosas, traslados y coincidencias entre redes |
| Excluidas | Motivo, fecha, orígenes afectados y acceso a restauración |
| Fuentes | Estado, capacidades, última ejecución y retraso real de observaciones |
| Descubrimiento | Último resultado, cobertura, nuevos IDs y ejecución manual limitada |
| Trabajos | Estado, próxima ejecución, error sanitizado y reintento permitido |

Las claves permanecen fuera del panel y de sus respuestas. La UI puede indicar “configurada” o “falta credencial”, nunca devolver su valor. Los mensajes de error deben ser accionables sin mostrar URLs firmadas, cabeceras o trazas con secretos.

## Eliminación y restauración

1. En la ficha y la tabla de gestión, mostrar **Eliminar de la app**.
2. La confirmación identifica nombre y redes vinculadas y explica que desaparecerá de las consultas y dejará de recogerse individualmente. Motivo opcional.
3. Ejecutar una transacción que active la exclusión y registre auditoría, versión de catálogo e invalidación de trabajos. Coordinarla con la comprobación de escritura del worker para cerrar la carrera con una descarga en curso.
4. Filtrar la estación en cada consulta a partir de ese momento; no depender solo de borrar un marcador o de un filtro del cliente. Invalidar cachés del servidor y evitar servir resultados antiguos de rutas públicas. Los navegadores ya abiertos se actualizan en el siguiente refresco previsto.
5. Mantener una lista privada de excluidas. Una restauración explícita reactiva elegibilidad e ingestión, conserva la auditoría y muestra que el periodo excluido puede tener huecos.

La vinculación de orígenes requiere revisión. Al unir uno nuevo a una estación excluida se mantiene la exclusión efectiva. Si solo se pausa un origen y otro sigue activo, el panel debe explicar qué dato se verá. Dividir una vinculación errónea conserva el historial y no restaura silenciosamente un origen con exclusión propia.

Las exclusiones y la auditoría forman parte de las copias de seguridad. El sistema nunca envía órdenes de baja a los proveedores.

## Gestión de descubrimiento

Permitir ejecutar **Buscar nuevas estaciones** y ver su progreso mediante un trabajo, no una petición HTTP larga. Evitar duplicar un trabajo ya activo y respetar la misma cuota que el proceso programado. La respuesta informa nuevos, revisados, excluidos y cobertura incompleta. Permitir añadir un ID de proveedor para verificarlo y, si es válido, incorporarlo.

No permitir introducir URLs arbitrarias que el servidor vaya a descargar. Usar proveedor conocido e ID validado; los endpoints se construyen desde la configuración permitida de cada adaptador.

## Comprobación de salida

- [ ] Un visitante no puede listar datos privados ni realizar ninguna mutación administrativa.
- [ ] Una estación excluida desaparece de mapa, lista, ficha directa, buscador, rankings, gráficos y CSV.
- [ ] Tras descubrirla otra vez y reiniciar los contenedores sigue excluida.
- [ ] Una descarga ya iniciada no vuelve a publicar observaciones de esa estación.
- [ ] Restaurar requiere una acción explícita y deja un registro de auditoría.
- [ ] Excluir una estación vinculada cubre todas sus redes; pausar una sola red tiene el efecto indicado.
- [ ] Una exportación o caché generada antes de la exclusión no permite acceder a datos excluidos por una nueva petición pública.
- [ ] Los intentos de login, la sesión caducada y las defensas de escritura se verifican.

## Prompt para Codex

```text
Implementa docs/fases/FASE_06_ADMINISTRACION.md después de leer
AGENTS.md, resumen y estado. Crea gestión privada y el recorrido
Eliminar de la app / Restaurar. Asegura autorización en backend,
exclusión transaccional, invalidación de cachés, cancelación de trabajos
y persistencia frente a redescubrimiento. Verifica todas las rutas de
lectura y la carrera con un worker en curso. No borres estaciones en
servicios externos. Documenta el resultado y actualiza docs/ESTADO.md.
```
