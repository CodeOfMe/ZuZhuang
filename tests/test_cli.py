"""Tests for ZuZhuang CLI integration."""

from __future__ import annotations

import subprocess
import sys


class TestCLIFlags:
    def _run(self, *args: str):
        return subprocess.run(
            [sys.executable, "-m", "zuzhuang"] + list(args),
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_version(self):
        r = self._run("-V")
        assert r.returncode == 0
        assert "zuzhuang" in r.stdout

    def test_help(self):
        r = self._run("--help")
        assert r.returncode == 0
        assert "build" in r.stdout
        assert "list-python" in r.stdout
        assert "--version" in r.stdout or "-V" in r.stdout

    def test_build_help(self):
        r = self._run("build", "--help")
        assert r.returncode == 0
        assert "--packages" in r.stdout or "-p" in r.stdout

    def test_list_python_json(self):
        r = self._run("list-python", "--json")
        assert r.returncode == 0
        assert "versions" in r.stdout

    def test_list_python_filtered_json(self):
        r = self._run("list-python", "--os", "windows", "--json")
        assert r.returncode == 0

    def test_build_no_output_fails(self):
        r = self._run("build", "3.11.9")
        assert r.returncode != 0
