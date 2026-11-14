FROM ghcr.io/astral-sh/uv:0.11.0 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /services/crop-model
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never
COPY --from=shared-contracts . /packages/field-shift-contracts
COPY --from=shared-science . /packages/field-shift-science
COPY --from=crop-package . /packages/field-shift-models
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-editable
COPY crop_runtime ./crop_runtime
RUN uv sync --locked --no-dev --no-editable \
    && rm -rf /packages /services/crop-model/crop_runtime
ENV PATH="/services/crop-model/.venv/bin:$PATH"
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 HOME=/tmp
RUN useradd --uid 10002 --no-create-home cropmodel
USER cropmodel
CMD ["python", "-m", "crop_runtime.server"]
