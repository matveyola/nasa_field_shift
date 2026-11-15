from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier, Lock

from field_shift_rotation_model import network


def test_independent_sources_overlap_and_identical_requests_share_evidence(monkeypatch):
    in_flight = Barrier(2)
    lock = Lock()
    requested = []

    class Opener:
        def open(self, request, *, timeout):
            with lock:
                requested.append(request.full_url)
            in_flight.wait(timeout=3)
            return BytesIO(request.full_url.encode())

    monkeypatch.setattr(network, "real_build_opener", lambda *_: Opener())
    first = "https://maps.isric.org/first"
    second = "https://ds.nccs.nasa.gov/second"
    session = network.EvidenceSession(acquire=True)

    def fetch(url):
        return session.open(url).read()

    with ThreadPoolExecutor(max_workers=3) as pool:
        one, duplicate, two = [pool.submit(fetch, url) for url in (first, first, second)]
        assert one.result() == duplicate.result() == first.encode()
        assert two.result() == second.encode()

    assert sorted(requested) == sorted([first, second])
    assert session.total == len(first) + len(second)
    replay = network.EvidenceSession(records=session.records)
    assert replay.open(first).read() == first.encode()
    assert replay.open(second).read() == second.encode()
