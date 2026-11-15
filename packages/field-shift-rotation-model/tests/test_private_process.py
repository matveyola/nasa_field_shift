import json
from datetime import UTC, datetime
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Event, Thread

from field_shift_rotation_model import server


def test_installed_spawn_catalog_and_offline_reference_round_trip():
    status, catalog = server.execute("catalog", {})
    assert status == 200 and len(catalog["crops"]) == 13
    status, evidence = server.execute(
        "acquire",
        {
            "operation": "sutherland_2027",
            "parameters": {},
            "decision_date": datetime.now(UTC).date().isoformat(),
            "allow_public_data": False,
        },
    )
    assert status == 200
    status, result = server.execute("evaluate", evidence)
    assert status == 200 and result["evidence_sha256"] == evidence["evidence_sha256"]
    assert result["empirical_status"] == "not_validated" and not result["ranking_usable"]


def test_private_http_auth_route_and_body_bounds(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "execute", lambda *args: calls.append(args) or (200, {"ok": True}))
    token = "unit-test-private-token-" * 2
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(token))
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()

    def request(path, body, headers):
        connection = HTTPConnection(*http.server_address)
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        status, data = response.status, json.loads(response.read())
        connection.close()
        return status, data

    try:
        assert request("/catalog", "{}", {})[0] == 401
        headers = {"Authorization": "Bearer " + token}
        assert request("/wrong", "{}", headers)[0] == 404
        assert (
            request("/catalog", "{}", headers | {"Content-Length": str(server.MAX_BYTES + 1)})[0]
            == 413
        )
        assert request("/catalog", "[]", headers)[0] == 400
        assert calls == []
        assert request("/catalog", "{}", headers)[0] == 200
        assert calls == [("catalog", {})]
    finally:
        http.shutdown()
        http.server_close()
        thread.join(5)


def test_process_timeout_reaps_child():
    import multiprocessing

    before = {p.pid for p in multiprocessing.active_children()}
    status, result = server.execute("catalog", {}, timeout=0)
    assert status == 503 and "time bound" in result["detail"]
    assert {p.pid for p in multiprocessing.active_children()} <= before


def test_warm_catalog_remains_authenticated_and_available_during_calculation(monkeypatch):
    from field_shift_rotation_model import runtime

    catalog = runtime.catalog()
    started, finish = Event(), Event()
    calls = []

    def calculation(operation, payload):
        calls.append(operation)
        started.set()
        assert finish.wait(5), "Test did not release the calculation"
        return 200, {"completed": True}

    monkeypatch.setattr(server, "execute", calculation)
    token = "unit-test-private-token-" * 2
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(token, catalog=catalog))
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()

    def request(path, body="{}", authorized=True):
        connection = HTTPConnection(*http.server_address, timeout=2)
        headers = {"Authorization": "Bearer " + token} if authorized else {}
        try:
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    completed = []
    worker = Thread(target=lambda: completed.append(request("/acquire")))
    try:
        worker.start()
        assert started.wait(2)
        assert request("/evaluate")[0] == 503
        assert request("/catalog", authorized=False)[0] == 401
        assert request("/catalog", body="[]")[0] == 400
        for _ in range(2):
            status, actual = request("/catalog")
            assert status == 200 and actual == catalog
            assert len(actual["crops"]) == 13
        assert calls == ["acquire"]
        finish.set()
        worker.join(5)
        assert completed == [(200, {"completed": True})]
    finally:
        finish.set()
        worker.join(5)
        http.shutdown()
        http.server_close()
        thread.join(5)
