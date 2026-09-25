@AGENTS.md

# Guía rápida para Claude Code

Las reglas de alcance, invariantes y despliegue están en `AGENTS.md` (importado arriba) y valen igual para Claude Code y Codex. La aplicación está **en producción** en `https://meteocentro.joserabalsegura.com` (host `ssh remote`, Podman rootless, puerto 8089).

## Mapa del código

- `backend/src/meteocentro/`: FastAPI (`api.py`, `map_api.py`, `history_api.py`, `admin_api.py`, `auth.py`), worker separado (`worker.py`, `job_queue.py`, `ingestion.py`), proveedores (`aemet.py`, `meteoclimatic*.py`), catálogo/exclusiones (`catalog.py`, `administration.py`, `domain/eligibility.py`) y agregados (`history.py`, `history_maintenance.py`).
- `backend/alembic/versions/`: migraciones. `schema.py::EXPECTED_REVISION` debe coincidir con la última; la API y el worker se niegan a arrancar si no.
- `frontend/src/`: React + MapLibre + ECharts (Vite). `main.tsx` es el mapa/tabla, `HistoryPage.tsx` los históricos, `AdminPage.tsx` la gestión.
- `deploy/`: Containerfiles, Quadlet, Nginx y `scripts/ops.py` (prepare/init-config/apply/smoke). `compose.yaml` es solo para desarrollo local.
- `tests/`: `test_phase1..7.py` necesitan PostgreSQL real; `test_phase0.py` y `test_deploy.py` son unittest sin base de datos.

## Comandos

```sh
make test        # arranca un PostgreSQL desechable en :55432 y ejecuta todas las pruebas
make lint        # Ruff con el mismo alcance que la CI
make web-build   # tsc + vite build
make check       # lint + test + web-build (lo que exige la CI)
make up          # stack local en http://localhost:5173 (db, migrate, api, web con recarga)
make up-worker   # además el worker (usa AEMET_API_KEY de .env: consume cuota real)
make admin ADMIN=nombre   # crear cuenta en el stack local
make e2e         # Playwright con fixtures, sin red externa
```

## Flujo de un cambio

1. Rama `claude/<tema>` desde `main`. Implementar con pruebas; `make check` en verde.
2. Probar en local con `make up` (Docker Desktop) y el navegador.
3. Actualizar `docs/ESTADO.md` solo con lo realmente ejecutado.
4. PR a `main`; la CI repite backend, frontend y ensayo de contenedores/migración.
5. Despliegue **solo cuando el usuario lo pida**: `docs/OPERACION.md` §5 opción B y §10 (construir desde el SHA exacto en `remote`, `ops.py prepare` en un directorio nuevo con el mismo puerto/config, revisar diff, `ops.py apply` con el mismo `--state`). Inspeccionar antes el estado del servidor y no tocar otros vhosts ni servicios.

## Precauciones

- Nunca `docker compose down -v` ni borrar volúmenes: el local también guarda históricos.
- `docs/ESTADO.md` puede tener cambios sin commitear de otra tarea: no incluirlos en commits ajenos.
- No leer ni mostrar `.env`, `deploy/env/*.env` ni la configuración privada de `remote`.
