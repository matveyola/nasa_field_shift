FROM ghcr.io/astral-sh/uv:0.11.0 AS uv
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never
ENV PATH="/apps/api/.venv/bin:$PATH"
WORKDIR /apps/api
COPY --from=shared-contracts . /packages/field-shift-contracts
COPY --from=shared-science . /packages/field-shift-science
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
RUN python -c "import rasterio; import shapely"
COPY src ./src
RUN uv sync --locked --no-dev --no-editable && rm -rf /apps/api/src /packages
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --create-home --uid 10001 fieldshift
USER fieldshift
EXPOSE 8000
CMD ["uvicorn", "field_shift.api:app", "--host", "0.0.0.0", "--port", "8000"]
