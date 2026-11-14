FROM ghcr.io/astral-sh/uv:0.11.0 AS uv
FROM python:3.12-slim AS base
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY agent_service ./agent_service
COPY contracts ./contracts

# Knowledge base (AGENT_RAG_MODE=lexical): the allowlisted FAO-56 chapters and NAAS recommendations, fetched and
# indexed at build time. The documents themselves stay out of Git.
FROM base AS knowledge
RUN python -m agent_service.knowledge fetch && python -m agent_service.knowledge build --no-embed

FROM base
COPY --from=knowledge /app/data/knowledge/index ./data/knowledge/index
RUN python -c "from agent_service.api import create_app"
RUN useradd --create-home --uid 10002 fieldagent
USER fieldagent
EXPOSE 8000
CMD ["python", "-m", "agent_service"]
