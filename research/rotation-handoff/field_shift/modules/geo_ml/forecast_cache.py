from collections.abc import Callable
from functools import lru_cache
from hashlib import sha256
from json import dumps, loads
from os import getenv

from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

_SLOTS = 256
_MAX_BYTES = 16 * 1024
_TTL_SECONDS = 48 * 3600


class ForecastCache:
    """Share immutable forecast inputs across workers with a fixed memory budget."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _read(self, slot: str, fingerprint: str) -> dict | None:
        encoded = self._redis.get(slot)
        if encoded is None or len(encoded) > _MAX_BYTES:
            return None
        try:
            key, payload = loads(encoded)
        except (TypeError, ValueError):
            return None
        return payload if key == fingerprint and isinstance(payload, dict) else None

    def get_or_load(
        self,
        identity: str,
        load: Callable[[], dict],
        *,
        cacheable: Callable[[dict], bool],
    ) -> dict:
        digest = sha256(identity.encode()).digest()
        fingerprint = digest.hex()
        slot = f"field-shift:ifs-input:v1:{int.from_bytes(digest[:2]) % _SLOTS}"
        try:
            cached = self._read(slot, fingerprint)
            if cached is not None:
                return cached
            lock = self._redis.lock(
                slot + ":lock",
                timeout=12,
                blocking_timeout=11,
                sleep=0.25,
                raise_on_release_error=False,
            )
            acquired = lock.acquire()
        except RedisError:
            return load()
        if not acquired:
            try:
                cached = self._read(slot, fingerprint)
            except RedisError:
                cached = None
            return cached if cached is not None else load()

        try:
            try:
                cached = self._read(slot, fingerprint)
            except RedisError:
                cached = None
            if cached is not None:
                return cached

            payload = load()
            if cacheable(payload):
                encoded = dumps([fingerprint, payload], separators=(",", ":")).encode()
                if len(encoded) <= _MAX_BYTES:
                    try:
                        self._redis.set(slot, encoded, ex=_TTL_SECONDS)
                    except RedisError:
                        pass
            return payload
        finally:
            try:
                lock.release()
            except RedisError:
                pass


@lru_cache(maxsize=1)
def _cache(url: str) -> ForecastCache:
    client = Redis.from_url(
        url,
        socket_timeout=0.25,
        socket_connect_timeout=0.25,
        max_connections=4,
        retry=Retry(NoBackoff(), 0),
    )
    return ForecastCache(client)


def shared_forecast_cache() -> ForecastCache | None:
    url = getenv("REDIS_URL")
    return _cache(url) if url and getenv("IFS_FORECAST_CACHE", "1") == "1" else None
