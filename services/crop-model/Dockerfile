FROM ghcr.io/astral-sh/uv:0.11.0 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /model
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never
COPY --from=crop-package pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY --from=crop-package src ./src
RUN uv sync --locked --no-dev --no-editable
COPY --from=planning-contracts . /contracts/field_shift/contracts
COPY crop_runtime /service/crop_runtime
ENV PATH="/model/.venv/bin:$PATH" PYTHONPATH="/contracts:/service"
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 HOME=/tmp
RUN useradd --uid 10002 --no-create-home cropmodel
USER cropmodel
CMD ["python", "-m", "crop_runtime.server"]
