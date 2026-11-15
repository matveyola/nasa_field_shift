FROM ghcr.io/astral-sh/uv:0.11.0 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /build
COPY packages/field-shift-rotation-model/pyproject.toml packages/field-shift-rotation-model/uv.lock ./packages/field-shift-rotation-model/
COPY packages/field-shift-rotation-model/src ./packages/field-shift-rotation-model/src
COPY packages/field-shift-science/pyproject.toml ./packages/field-shift-science/
COPY packages/field-shift-science/src ./packages/field-shift-science/src
RUN uv sync --project packages/field-shift-rotation-model --locked --no-dev --no-editable \
    && cp -a packages/field-shift-rotation-model/.venv /opt/rotation-model \
    && rm -rf /build/packages
RUN useradd --create-home --uid 10001 model
USER model
WORKDIR /home/model
EXPOSE 8080
CMD ["/opt/rotation-model/bin/python", "-I", "-m", "field_shift_rotation_model.server"]
