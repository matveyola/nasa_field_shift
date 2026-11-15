FROM ghcr.io/astral-sh/uv:0.11.0 AS uv
FROM python:3.12-slim AS python
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never
ENV PATH="/opt/venv/bin:$PATH" AGENT_DATA_DIR=/var/lib/field-shift-agent

FROM python AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY agent_service ./agent_service
COPY contracts ./contracts
COPY integration_stub ./integration_stub
COPY evals ./evals
RUN uv sync --locked --no-dev --no-editable

# Public allowlisted documents are downloaded only in this explicit build stage.
# The runtime serves a prebuilt lexical index without model downloads.
FROM python AS knowledge
COPY --from=build /opt/venv /opt/venv
WORKDIR /tmp
RUN python -I -m agent_service.knowledge fetch && python -I -m agent_service.knowledge build --no-embed

FROM python
COPY --from=build /opt/venv /opt/venv
COPY --from=knowledge --chown=10002:10002 /var/lib/field-shift-agent/knowledge/index /var/lib/field-shift-agent/knowledge/index
RUN useradd --create-home --uid 10002 fieldagent && chown -R fieldagent:fieldagent /var/lib/field-shift-agent
WORKDIR /home/fieldagent
USER fieldagent
RUN python -I -c "from agent_service.api import create_app; from agent_service.integrations.fixture import FixtureGeoProvider; assert FixtureGeoProvider().contexts"
EXPOSE 8000
CMD ["python", "-I", "-m", "agent_service"]
