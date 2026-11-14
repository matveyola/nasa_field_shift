"""Run the agent service: `uv run python -m agent_service`."""

import logging
import sys

import uvicorn
from pydantic import ValidationError

from .api import create_app
from .config import Settings


def main() -> int:
    try:
        settings = Settings.from_env()
    except ValidationError as exc:
        # Print without input values: they may contain the API key.
        for error in exc.errors(include_input=False, include_url=False):
            print(f"config error: {'.'.join(map(str, error['loc'])) or 'settings'}: {error['msg']}", file=sys.stderr)
        return 2
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port,
                log_level=settings.log_level.lower(), access_log=False, server_header=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
