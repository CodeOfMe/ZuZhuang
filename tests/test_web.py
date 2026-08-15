"""Tests for the Flask web UI."""

from __future__ import annotations

import json
from unittest import mock

import pytest

flask = pytest.importorskip("flask")
from zuzhuang.web import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = create_app(work_dir=tmp_path / "work")
    app.testing = True
    with app.test_client() as c:
        yield c


class TestIndex:
    def test_renders(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"ZuZhuang" in r.data
        assert b"python_version" in r.data


class TestHostCapabilities:
    def test_ok(self, client):
        r = client.get("/api/host-capabilities")
        assert r.status_code == 200
        d = r.get_json()
        assert d["success"] is True
        assert "host_os" in d
        assert "can_build" in d
        assert "can_verify" in d
        assert d["can_build"]["windows"] is True


class TestPythonVersions:
    def test_ok(self, client):
        with mock.patch(
            "zuzhuang.orchestrator.available_python_versions", return_value=["3.12.6", "3.11.9"]
        ):
            r = client.get("/api/python-versions?os=windows")
        assert r.status_code == 200
        d = r.get_json()
        assert d["success"] is True
        assert "3.12.6" in d["versions"]

    def test_error(self, client):
        with mock.patch(
            "zuzhuang.orchestrator.available_python_versions",
            side_effect=RuntimeError("nope"),
        ):
            r = client.get("/api/python-versions")
        assert r.status_code == 500
        d = r.get_json()
        assert d["success"] is False


class TestPyPiSearch:
    def test_empty(self, client):
        r = client.get("/api/pypi/search?q=")
        assert r.status_code == 200
        assert r.get_json()["results"] == []

    def test_with_results(self, client):
        with mock.patch("zuzhuang.web.search_packages", return_value=[{"name": "numpy"}]):
            r = client.get("/api/pypi/search?q=numpy")
        d = r.get_json()
        assert d["success"] is True
        assert d["results"][0]["name"] == "numpy"


class TestPyPiLookup:
    def test_found(self, client):
        from zuzhuang.pypi import PackageInfo

        with mock.patch(
            "zuzhuang.web.lookup_package",
            return_value=PackageInfo(name="numpy", version="1.26.0"),
        ):
            r = client.get("/api/pypi/lookup?name=numpy")
        assert r.status_code == 200
        d = r.get_json()
        assert d["success"] is True
        assert d["package"]["name"] == "numpy"

    def test_not_found(self, client):
        with mock.patch("zuzhuang.web.lookup_package", return_value=None):
            r = client.get("/api/pypi/lookup?name=nope")
        assert r.status_code == 404

    def test_no_name(self, client):
        r = client.get("/api/pypi/lookup")
        assert r.status_code == 400


class TestPyPiResolve:
    def test_ok(self, client):
        with mock.patch(
            "zuzhuang.web.resolve_packages",
            return_value=([{"spec": "numpy==1.26.0"}], []),
        ):
            r = client.post(
                "/api/pypi/resolve",
                data=json.dumps({"packages": ["numpy"]}),
                content_type="application/json",
            )
        d = r.get_json()
        assert d["success"] is True
        assert len(d["resolved"]) == 1

    def test_bad_payload(self, client):
        r = client.post(
            "/api/pypi/resolve",
            data=json.dumps({"packages": "notalist"}),
            content_type="application/json",
        )
        assert r.status_code == 400


class TestJobs:
    def test_create_requires_version(self, client):
        r = client.post("/api/jobs", data=json.dumps({}), content_type="application/json")
        assert r.status_code == 400

    def test_create_and_status(self, client):
        with mock.patch("zuzhuang.web.JobManager.start") as start:
            r = client.post(
                "/api/jobs",
                data=json.dumps({"python_version": "3.12.6", "packages": ["numpy"]}),
                content_type="application/json",
            )
        d = r.get_json()
        assert d["success"] is True
        job_id = d["job_id"]
        assert start.called

        r2 = client.get(f"/api/jobs/{job_id}")
        assert r2.status_code == 200
        assert r2.get_json()["job"]["id"] == job_id

    def test_status_not_found(self, client):
        r = client.get("/api/jobs/nope")
        assert r.status_code == 404

    def test_download_not_ready(self, client):
        r = client.get("/api/jobs/nope/download")
        assert r.status_code == 404


class TestSSEStream:
    def test_not_found(self, client):
        r = client.get("/api/jobs/nope/stream")
        assert r.status_code == 404


class TestNotFound:
    def test_json_404(self, client):
        r = client.get("/api/does-not-exist")
        assert r.status_code == 404
        assert r.get_json()["success"] is False
