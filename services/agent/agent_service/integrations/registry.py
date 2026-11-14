"""Builds the configured geo and simulation providers and reports, honestly, what is connected.

"Connected" means the service answered GET /v1/capabilities with a contract-valid body recently.
It says nothing about whether a particular computation succeeded, and a reachable stub or fixture
is reported as such (mode "stub"/"fixture"), never as a live NASA or ML integration.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from contracts.integration import CapabilitiesResponse

from ..config import Settings
from .fixture import FixtureGeoProvider, FixtureSimulationProvider
from .http import HttpGeoProvider, HttpService, HttpSimulationProvider
from .ports import GeoProfileProvider, IntegrationFailure, SimulationProvider, UnavailableProvider

STATUS_TTL_S = 30.0


def transport_facts(url: str, has_token: bool) -> dict[str, str]:
    """The four identity notions, kept apart: (1) the configured endpoint, (2) what the transport authenticates,
    (3) the credential we present. (4) the service's self-description and (5) per-value provenance come from
    response bodies and are never treated as authentication."""
    https = url.startswith("https://")
    return {
        "endpoint": url.split("://", 1)[0] + "://" + url.split("://", 1)[1].split("/", 1)[0],  # scheme + host, no path
        "server_authenticated_by": "TLS certificate (system trust store)" if https else "nothing (plain http: development only)",
        "agent_authenticates_with": "bearer token" if has_token else "nothing",
    }


@dataclass
class _Probe:
    caps: CapabilitiesResponse | None = None
    problem: str | None = None
    # Never checked. Not 0.0: time.monotonic() counts from boot, so on a fresh machine (a CI runner) 0.0 looked
    # "checked moments ago" and the first capabilities read returned the empty cache.
    checked_at: float = float("-inf")
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class IntegrationRegistry:
    def __init__(self, geo: GeoProfileProvider, sim: SimulationProvider, *, geo_mode: str, sim_mode: str,
                 geo_timeout_s: float = 8.0, sim_timeout_s: float = 30.0, status_ttl_s: float = STATUS_TTL_S,
                 require_live: bool = False):
        self.geo, self.sim = geo, sim
        # Production: a stub or fixture behind a configured URL must never pass for a real service.
        self.require_live = require_live
        # What we know about each endpoint independently of what it says about itself (set by from_settings).
        self.transport: dict[str, dict[str, str]] = {}
        self.geo_mode, self.sim_mode = geo_mode, sim_mode
        self.geo_timeout_s, self.sim_timeout_s, self.status_ttl_s = geo_timeout_s, sim_timeout_s, status_ttl_s
        self._probes = {"geo": _Probe(), "simulation": _Probe()}

    @classmethod
    def from_settings(cls, s: Settings) -> "IntegrationRegistry":
        def http(service, url, timeout):
            return HttpService(service, url, timeout_s=timeout, max_bytes=s.integration_max_bytes, token=s.integration_token)

        geo = {"off": lambda: UnavailableProvider("geo"), "fixture": FixtureGeoProvider,
               "http": lambda: HttpGeoProvider(http("geo", s.geo_url, s.integration_timeout_s))}[s.geo_provider]()
        sim = {"off": lambda: UnavailableProvider("simulation"), "fixture": FixtureSimulationProvider,
               "http": lambda: HttpSimulationProvider(http("simulation", s.sim_url, s.sim_timeout_s))}[s.sim_provider]()
        registry = cls(geo, sim, geo_mode=s.geo_provider, sim_mode=s.sim_provider,
                       geo_timeout_s=s.integration_timeout_s, sim_timeout_s=s.sim_timeout_s,
                       require_live=s.environment == "production")
        for service, mode, url in (("geo", s.geo_provider, s.geo_url), ("simulation", s.sim_provider, s.sim_url)):
            if mode == "http" and url:
                registry.transport[service] = transport_facts(url, s.integration_token is not None)
        return registry

    def invalidate(self, service: str) -> None:
        """Forget cached capabilities after a failure, so the next turn probes again."""
        self._probes[service].checked_at = float("-inf")

    async def capabilities(self, service: str, *, refresh: bool = False) -> CapabilitiesResponse | None:
        """Cached capabilities of one service; None when it is off or did not answer correctly."""
        probe = self._probes[service]
        provider = self.geo if service == "geo" else self.sim
        mode = self.geo_mode if service == "geo" else self.sim_mode
        if mode == "off":
            probe.problem = "not configured"
            return None
        async with probe.lock:
            if not refresh and time.monotonic() - probe.checked_at < self.status_ttl_s:
                return probe.caps
            try:
                caps = await asyncio.wait_for(provider.capabilities(f"cap_{uuid.uuid4().hex[:12]}"), self.geo_timeout_s)
                if caps.service.service != service:
                    raise IntegrationFailure("invalid_response", f"capabilities are for {caps.service.service}")
                if self.require_live and caps.service.mode != "live":
                    raise IntegrationFailure("rejected", f"a {caps.service.mode} service is not accepted in production")
                probe.caps, probe.problem = caps, None
            except IntegrationFailure as f:
                probe.caps, probe.problem = None, f.category
            except TimeoutError:
                probe.caps, probe.problem = None, "timeout"
            probe.checked_at = time.monotonic()
            return probe.caps

    async def simulation_available(self) -> bool:
        caps = await self.capabilities("simulation")
        return bool(caps and caps.simulation and caps.simulation.compare_rotations and caps.simulation.scenarios)

    async def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for service, mode in (("geo", self.geo_mode), ("simulation", self.sim_mode)):
            caps = await self.capabilities(service)
            entry: dict[str, Any] = {"configured": mode, "connected": caps is not None}
            if service in self.transport:
                entry["transport"] = self.transport[service]
            if caps:
                # implementation and service_mode are what the service CLAIMS about itself, not authenticated facts.
                entry |= {"service_mode": caps.service.mode, "implementation": caps.service.implementation,
                          "contract_version": caps.service.contract_version}
                if caps.simulation:
                    entry |= {"compare_rotations": caps.simulation.compare_rotations,
                              "scenarios": list(caps.simulation.scenarios)}
                if caps.geo:
                    entry |= {"field_profile": caps.geo.field_profile}
            elif mode != "off":
                entry["problem"] = self._probes[service].problem
            out[service] = entry
        modes = [e.get("service_mode") for e in out.values() if e["connected"]]
        out["demo_mode"] = any(m in ("stub", "fixture") for m in modes)
        out["degraded"] = any(e["configured"] != "off" and not e["connected"] for e in out.values() if isinstance(e, dict))
        out["live_services"] = [s for s in ("geo", "simulation") if out[s].get("service_mode") == "live"]
        return out

    async def aclose(self) -> None:
        await self.geo.aclose()
        await self.sim.aclose()
