# Meteocentro

Plan de desarrollo de una aplicación meteorológica para Madrid, Ávila, Segovia y Guadalajara, inspirada en el mapa y los históricos de Suremet. La primera versión se centra en AEMET y Meteoclimatic. Weather Underground queda como ampliación opcional tras revisar su coste y acceso.

**Fases 0 y 1:** investigación de acceso y base local ejecutable, sin ingestión meteorológica. Fecha del plan: 9 de septiembre de 2026; comprobaciones reales: 14 de septiembre de 2026. Nombre provisional: Meteocentro.

Actualización de alcance: el usuario no dispone de clave Wunderground y prefiere usar AEMET y Meteoclimatic si su incorporación resulta cara. La recomendación tras revisar las tarifas es desarrollar primero esas dos redes. Ver [coste y acceso a datos](docs/COSTE_Y_ACCESO_DATOS.md).

## Por dónde empezar

1. Leer [el resumen global](docs/00_RESUMEN_GLOBAL.md).
2. Consultar la [matriz de acceso de fase 0](docs/integraciones/MATRIZ_ACCESO.md) y sus bloqueos antes de [la fase 1](docs/fases/FASE_01_BASE_Y_DATOS.md).
3. Ejecutar una fase cada vez con el prompt incluido al final de su documento.
4. Registrar resultados y bloqueos en [ESTADO.md](docs/ESTADO.md). Una fase con datos de prueba no equivale a una integración real verificada.

## Arranque local de la fase 1

Se comprobó con Docker Compose, Python 3.13, Node 24.21.0 y PostgreSQL 17.11. Podman no estaba instalado en el entorno de prueba, por lo que su invocación queda pendiente. Los puertos se publican solo en `127.0.0.1`.

```sh
cp .env.example .env
# Editar .env: sustituir POSTGRES_PASSWORD por una contraseña local aleatoria,
# formada por letras y números para que sea segura dentro de DATABASE_URL.
docker compose config --quiet
docker compose up -d --build
curl -fsS http://127.0.0.1:5173/health/ready
curl -fsS http://127.0.0.1:5173/api/v1/stations
```

La pantalla local está en `http://127.0.0.1:5173/`; OpenAPI en `http://127.0.0.1:5173/api/v1/docs`. La base empieza vacía: la respuesta comprobada de estaciones fue `{"items":[],"total":0,"limit":50,"offset":0}`. `docker compose down` detiene los contenedores y conserva el volumen; `docker compose down -v` borraría la base local y **no** forma parte del procedimiento ordinario. La clave AEMET no se configura ni se copia en esta fase. Véase el [contrato de API y datos](docs/CONTRATOS_FASE_1.md).

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

## Siguiente instrucción para Codex

```text
Lee AGENTS.md, docs/00_RESUMEN_GLOBAL.md, docs/DECISIONES.md,
docs/ESTADO.md, docs/integraciones/MATRIZ_ACCESO.md y
docs/fases/FASE_01_BASE_Y_DATOS.md. Implementa solo la fase 1.
Usa los polígonos completos del IGN para clasificar y los fixtures
sintéticos para las pruebas. Mantén Meteoclimatic pendiente de términos
y coordenadas; no actives su ingesta. No copies la clave AEMET a Git ni
despliegues. Registra pruebas reales y limitaciones en docs/ESTADO.md.
```

## Comportamientos esenciales

- El mapa y los históricos consultan nuestra base de datos.
- El worker recopila datos aunque no haya usuarios conectados.
- Un trabajo separado descubre nuevas estaciones y actualiza su catálogo.
- Eliminar una estación de la app la excluye del mapa, consultas y recogida individual; el descubrimiento no puede reactivarla.
- Los históricos anteriores al arranque se importan solo cuando la fuente los ofrece y el acceso permite conservarlos y mostrarlos.
- Las claves se configuran fuera de Git y del navegador.

El destino previsto es el servidor habitual mediante `ssh remote`, con Podman y Quadlet/systemd. Este plan no ha cambiado la configuración de ese servidor.
