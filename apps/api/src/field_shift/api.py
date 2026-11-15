from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from field_shift.config import Settings, get_settings
from field_shift.db import SessionDep, build_session_factory
from field_shift.errors import ConflictError, NotFoundError
from field_shift.jobs.routes import router as jobs_router
from field_shift.modules.access.middleware import access_middleware
from field_shift.modules.access.routes import router as access_router
from field_shift.modules.agent.provider import AgentProvider, DemoAgentProvider
from field_shift.modules.agent.remote import RemoteAgentError, RemoteAgentProvider
from field_shift.modules.agent.routes import router as agent_router
from field_shift.modules.farm.routes import router as farm_router
from field_shift.modules.fields.boundary_suggestion_routes import (
    router as boundary_suggestion_router,
)
from field_shift.modules.fields.history_routes import router as field_history_router
from field_shift.modules.fields.planning_profile.routes import router as profile_router
from field_shift.modules.fields.routes import router as fields_router
from field_shift.modules.imagery.routes import router as imagery_router
from field_shift.modules.onboarding.routes import router as onboarding_router
from field_shift.modules.planning_runtime.body_limit import PlanningBodyLimit
from field_shift.modules.planning_runtime.routes import router as scenario_router
from field_shift.modules.planning_runtime.routes import workspace_router
from field_shift.modules.precision.routes import router as precision_router
from field_shift.modules.rotations.planning_routes import router as rotation_planning_router
from field_shift.modules.rotations.routes import router as rotations_router
from field_shift.modules.simulation.routes import router as simulation_router
from field_shift.modules.terrain.routes import router as terrain_router
from field_shift.modules.weather.routes import router as weather_router


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
    agent_provider: AgentProvider | None = None,
) -> FastAPI:
    application = FastAPI(title="Field Shift API", version="0.1.0")
    settings = settings if settings is not None else get_settings()
    application.state.settings = settings
    application.state.session_factory = (
        session_factory
        if session_factory is not None
        else build_session_factory(settings.database_url)
    )
    if agent_provider is None:
        agent_provider = (
            RemoteAgentProvider(
                settings.agent_url, settings.agent_api_key, settings.agent_signing_key
            )
            if settings.agent_url
            else DemoAgentProvider()
        )
    application.state.agent_provider = agent_provider
    application.add_middleware(PlanningBodyLimit)
    application.middleware("http")(access_middleware)

    @application.exception_handler(NotFoundError)
    def not_found(_request: Request, error: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @application.exception_handler(ConflictError)
    def conflict(_request: Request, error: ConflictError):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @application.exception_handler(RemoteAgentError)
    def remote_agent_error(_request: Request, error: RemoteAgentError):
        return JSONResponse(status_code=error.status, content={"detail": str(error)})

    @application.get("/api/health", tags=["health"])
    def health(session: SessionDep):
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError as error:
            raise HTTPException(503, "Database is unavailable") from error
        return {"status": "ok"}

    for router in (
        access_router,
        profile_router,
        scenario_router,
        workspace_router,
        boundary_suggestion_router,
        onboarding_router,
        field_history_router,
        rotation_planning_router,
        precision_router,
        fields_router,
        imagery_router,
        terrain_router,
        weather_router,
        farm_router,
        simulation_router,
        rotations_router,
        agent_router,
        jobs_router,
    ):
        application.include_router(router, prefix="/api")

    return application


app = create_app()
