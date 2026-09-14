FROM python:3.13.14-slim-bookworm
WORKDIR /app/backend
RUN python -m pip install --no-cache-dir uv==0.12.13
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
RUN uv sync --locked --no-dev --no-editable
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY config/provinces-full.geojson.gz /app/config/provinces-full.geojson.gz
ENV PATH="/app/backend/.venv/bin:${PATH}"
CMD ["uvicorn", "meteocentro.api:app", "--host", "0.0.0.0", "--port", "8000"]
