"""Run the contract stub: uv run python -m integration_stub [--port 8100] [--fault geo:unavailable ...]

Base URLs for the agent: AGENT_GEO_URL=http://127.0.0.1:8100/geo, AGENT_SIM_URL=http://127.0.0.1:8100/sim.
Set STUB_TOKEN in the environment to require `Authorization: Bearer <STUB_TOKEN>` on every call.
--sim-marsden http://127.0.0.1:8081 puts the Marsden rotation API behind /sim (see README › Marsden); --marsden-field
maps a host field id to a Marsden field id (default demo-marsden-01=marsden_demo).
"""

import argparse
import os

import uvicorn

from agent_service.integrations.marsden import MarsdenSimulationProvider

from .app import FAULTS, create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--fault", action="append", default=[], help=f"service:fault, faults: {', '.join(sorted(FAULTS))}")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds added to every answer")
    parser.add_argument("--sim-marsden", metavar="URL", help="serve /sim from the Marsden rotation API at URL")
    parser.add_argument("--marsden-field", action="append", default=[], metavar="HOST_ID=MARSDEN_ID",
                        help="field mapping for --sim-marsden (repeatable)")
    args = parser.parse_args()
    sim = None
    if args.sim_marsden:
        pairs = args.marsden_field or ["demo-marsden-01=marsden_demo"]
        sim = MarsdenSimulationProvider(args.sim_marsden, dict(p.split("=", 1) for p in pairs))
    faults = {}
    for item in args.fault:
        service, _, fault = item.partition(":")
        faults[service] = fault
    app = create_app(faults=faults, delay_s=args.delay, token=os.environ.get("STUB_TOKEN") or None, sim=sim)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
