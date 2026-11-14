from logging.config import fileConfig

from alembic import context

from field_shift.config import get_settings
from field_shift.db import build_engine
from field_shift.jobs import models as job_models  # noqa: F401 - register job tables
from field_shift.models import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_offline() -> None:
    url = config.attributes.get("database_url", get_settings().database_url)
    context.configure(
        url=url, target_metadata=metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    url = config.attributes.get("database_url", get_settings().database_url)
    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
