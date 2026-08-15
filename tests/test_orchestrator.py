"""Tests for the orchestrator module."""

from __future__ import annotations

import zipfile
from pathlib import Path
from unittest import mock

from zuzhuang import orchestrator


class TestCurrentHostSupports:
    def test_windows_always_supported(self):
        assert orchestrator.current_host_supports("windows") is True

    def test_unknown(self):
        assert orchestrator.current_host_supports("solaris") is False


class TestHostCanVerify:
    def test_windows_on_windows(self):
        with mock.patch("zuzhuang.orchestrator._get_os", return_value="windows"):
            assert orchestrator.host_can_verify("windows") is True
            assert orchestrator.host_can_verify("macos") is False

    def test_cross_host_false(self):
        with mock.patch("zuzhuang.orchestrator._get_os", return_value="macos"):
            assert orchestrator.host_can_verify("windows") is False
            assert orchestrator.host_can_verify("macos") is True


class TestFindPythonBin:
    def test_windows(self, tmp_path):
        (tmp_path / "python.exe").write_text("x")
        p = orchestrator._find_python_bin(tmp_path, "windows")
        assert p == tmp_path / "python.exe"

    def test_windows_missing(self, tmp_path):
        assert orchestrator._find_python_bin(tmp_path, "windows") is None

    def test_unix(self, tmp_path):
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "python3").write_text("x")
        p = orchestrator._find_python_bin(tmp_path, "linux")
        assert p == tmp_path / "bin" / "python3"


class TestZipDir:
    def test_zips_files(self, tmp_path):
        src = tmp_path / "env"
        src.mkdir()
        (src / "python.exe").write_text("hello")
        (src / "Lib").mkdir()
        (src / "Lib" / "x.py").write_text("print(1)")
        zp = tmp_path / "out.zip"
        assert orchestrator._zip_dir(src, zp) is True
        assert zp.exists()
        with zipfile.ZipFile(zp) as zf:
            names = zf.namelist()
        assert "python.exe" in names
        assert "Lib/x.py" in names


class TestVerifyImports:
    def test_interpreter_check_ok(self, tmp_path, capsys):
        # use the current python which definitely runs
        import sys

        bin_path = Path(sys.executable)
        log = orchestrator._verify_imports(bin_path, [], progress=None)
        # no packages -> empty log (interpreter check only runs when names present)
        assert log == []

    def test_real_import(self, tmp_path):
        import sys

        bin_path = Path(sys.executable)
        log = orchestrator._verify_imports(bin_path, ["json", "os"], progress=None)
        assert len(log) == 2
        assert all(e["ok"] for e in log)

    def test_failed_import(self, tmp_path):
        import sys

        bin_path = Path(sys.executable)
        log = orchestrator._verify_imports(
            bin_path, ["definitely_no_such_module_xyz"], progress=None
        )
        assert len(log) == 1
        assert log[0]["ok"] is False


class TestRunAssemblyFailure:
    def test_invalid_version(self, tmp_path):
        events = []
        result = orchestrator.run_assembly(
            python_version="99.99.99",
            packages=[],
            output_dir=tmp_path / "out",
            target_os="windows",
            do_verify=False,
            do_zip=False,
            progress=lambda e: events.append(e),
        )
        assert result.success is False
        # some progress events should have been emitted
        assert any(e.get("stage") == "start" for e in events)


class TestJobResult:
    def test_to_dict(self):
        r = orchestrator.JobResult(success=True, output_dir="/x", zip_path="/x.zip")
        d = r.to_dict()
        assert d["success"] is True
        assert d["output_dir"] == "/x"
        assert d["zip_path"] == "/x.zip"

    def test_defaults(self):
        r = orchestrator.JobResult(success=False, error="boom")
        assert r.verify_log == []
        assert r.metadata == {}


class TestRunInThread:
    def test_runs_and_calls_on_done(self, tmp_path):
        done_results = []

        def on_done(r):
            done_results.append(r)

        t = orchestrator.run_in_thread(
            {
                "python_version": "99.99.99",
                "packages": [],
                "output_dir": tmp_path / "out",
                "target_os": "windows",
                "do_verify": False,
                "do_zip": False,
            },
            on_done=on_done,
        )
        t.join(timeout=60)
        assert len(done_results) == 1
        assert done_results[0].success is False


class TestAvailablePythonVersions:
    def test_returns_list(self):
        with mock.patch(
            "zuzhuang.core.fetch_python_releases",
            return_value=[],
        ):
            assert orchestrator.available_python_versions() == []
