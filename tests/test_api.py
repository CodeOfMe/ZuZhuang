"""Tests for ZuZhuang unified API."""

from __future__ import annotations

from pathlib import Path


class TestToolResult:
    def test_success_result(self):
        from zuzhuang.api import ToolResult

        r = ToolResult(success=True, data={"key": "value"})
        assert r.success is True
        assert r.error is None

    def test_failure_result(self):
        from zuzhuang.api import ToolResult

        r = ToolResult(success=False, error="failed")
        assert r.success is False
        assert r.error == "failed"

    def test_to_dict(self):
        from zuzhuang.api import ToolResult

        r = ToolResult(success=True, data=[1, 2])
        d = r.to_dict()
        assert set(d.keys()) == {"success", "data", "error", "metadata"}

    def test_default_metadata_isolation(self):
        from zuzhuang.api import ToolResult

        r1 = ToolResult(success=True)
        r2 = ToolResult(success=True)
        r1.metadata["a"] = 1
        assert "a" not in r2.metadata


class TestAPI:
    def test_list_python(self):
        from zuzhuang.api import zuzhuang_list_python

        result = zuzhuang_list_python()
        assert result.success is True
        assert isinstance(result.data, dict)
        assert "versions" in result.data
        assert isinstance(result.data["versions"], list)

    def test_list_python_filtered(self):
        from zuzhuang.api import zuzhuang_list_python

        result = zuzhuang_list_python(target_os="windows")
        assert result.success is True

    def test_build_invalid_version(self):
        import tempfile

        from zuzhuang.api import zuzhuang_build

        with tempfile.TemporaryDirectory() as tmpdir:
            result = zuzhuang_build(
                python_version="99.99.99",
                output_dir=Path(tmpdir) / "doesnotexist",
            )
            assert not result.success


class TestCore:
    def test_fetch_releases(self):
        from zuzhuang.core import fetch_python_releases

        releases = fetch_python_releases()
        assert isinstance(releases, list)
        # Should have entries for each OS
        if releases:
            assert hasattr(releases[0], "version")
            assert hasattr(releases[0], "os_name")

    def test_get_os(self):
        from zuzhuang.core import _get_os

        os_name = _get_os()
        assert os_name in ("windows", "macos", "linux")

    def test_get_arch(self):
        from zuzhuang.core import _get_arch

        arch = _get_arch()
        assert arch in ("x86_64", "arm64", "x86")
