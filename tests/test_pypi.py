"""Tests for the PyPI integration module."""

from __future__ import annotations

from unittest import mock

import pytest

from zuzhuang import pypi


class TestNormalize:
    def test_basic(self):
        assert pypi._normalize("NumPy") == "numpy"
        assert pypi._normalize("scikit_learn") == "scikit-learn"
        assert pypi._normalize("Pillow.PIL") == "pillow-pil"


class TestVersionKey:
    def test_ordering(self):
        assert pypi._version_key("1.2.3") > pypi._version_key("1.2.2")
        assert pypi._version_key("3.12.6") > pypi._version_key("3.11.9")


class TestLookupPackage:
    def test_not_found_returns_none(self):
        with mock.patch("zuzhuang.pypi._fetch_json", side_effect=OSError("boom")):
            assert pypi.lookup_package("definitely-not-real-xyz") is None

    def test_parses_info(self):
        payload = {
            "info": {
                "name": "numpy",
                "version": "1.26.0",
                "summary": "NumPy is the fundamental package for array computing with Python.",
                "requires_python": ">=3.9",
                "project_urls": {"Homepage": "https://numpy.org"},
                "requires_dist": ["pytest; extra == 'test'"],
            },
            "releases": {"1.26.0": [], "1.25.0": []},
        }
        with mock.patch("zuzhuang.pypi._fetch_json", return_value=payload):
            info = pypi.lookup_package("numpy")
        assert info is not None
        assert info.name == "numpy"
        assert info.version == "1.26.0"
        assert "1.26.0" in info.available_versions
        assert info.requires_dist == ["pytest; extra == 'test'"]

    def test_to_dict(self):
        info = pypi.PackageInfo(name="x", version="1.0", summary="s")
        d = info.to_dict()
        assert d["name"] == "x"
        assert d["version"] == "1.0"
        assert "available_versions" in d


class TestResolveVersion:
    def test_latest(self):
        with mock.patch(
            "zuzhuang.pypi.lookup_package",
            return_value=pypi.PackageInfo(
                name="x", version="2.0.0", available_versions=["2.0.0", "1.0.0"]
            ),
        ):
            assert pypi.resolve_version("x") == "2.0.0"
            assert pypi.resolve_version("x", "latest") == "2.0.0"

    def test_exact_pin(self):
        with mock.patch(
            "zuzhuang.pypi.lookup_package",
            return_value=pypi.PackageInfo(
                name="x", version="2.0.0", available_versions=["2.0.0", "1.0.0"]
            ),
        ):
            assert pypi.resolve_version("x", "1.0.0") == "1.0.0"
            assert pypi.resolve_version("x", "==1.0.0") == "1.0.0"

    def test_ge_spec(self):
        with mock.patch(
            "zuzhuang.pypi.lookup_package",
            return_value=pypi.PackageInfo(
                name="x", version="3.0.0", available_versions=["3.0.0", "2.0.0", "1.0.0"]
            ),
        ):
            assert pypi.resolve_version("x", ">=2.0.0") == "3.0.0"

    def test_not_found(self):
        with mock.patch("zuzhuang.pypi.lookup_package", return_value=None):
            assert pypi.resolve_version("nope") is None


class TestResolvePackages:
    def test_simple(self):
        info = pypi.PackageInfo(name="numpy", version="1.26.0", available_versions=["1.26.0"])
        with mock.patch("zuzhuang.pypi.lookup_package", return_value=info):
            resolved, failed = pypi.resolve_packages(["numpy"])
        assert len(resolved) == 1
        assert resolved[0]["spec"] == "numpy==1.26.0"
        assert failed == []

    def test_with_extras_and_version(self):
        info = pypi.PackageInfo(name="requests", version="2.31.0", available_versions=["2.31.0"])
        with mock.patch("zuzhuang.pypi.lookup_package", return_value=info):
            resolved, failed = pypi.resolve_packages(["requests[socks]==2.31.0"])
        assert len(resolved) == 1
        assert "socks" in resolved[0]["spec"]
        assert failed == []

    def test_not_found(self):
        with mock.patch("zuzhuang.pypi.lookup_package", return_value=None):
            resolved, failed = pypi.resolve_packages(["definitely-not-real-xyz"])
        assert resolved == []
        assert len(failed) == 1
        assert "not found" in failed[0]["error"]

    def test_empty_and_whitespace(self):
        resolved, failed = pypi.resolve_packages(["", "  "])
        assert resolved == []
        assert failed == []

    def test_invalid_specifier(self):
        resolved, failed = pypi.resolve_packages(["!!!"])
        assert resolved == []
        assert len(failed) == 1


class TestSearchPackages:
    def test_empty_query(self):
        assert pypi.search_packages("") == []

    def test_filters_from_index(self):
        fake_index = ["requests", "requests-kerberos", "urllib3", "flask"]
        with mock.patch("zuzhuang.pypi._load_name_index", return_value=fake_index):
            out = pypi.search_packages("requests", limit=10)
        names = [r["name"] for r in out]
        assert "requests" in names
        assert "requests-kerberos" in names
        assert "urllib3" not in names

    def test_no_index_returns_empty(self):
        with mock.patch("zuzhuang.pypi._load_name_index", return_value=None):
            assert pypi.search_packages("anything") == []

    def test_limit(self):
        fake = [f"pkg{i}" for i in range(50)]
        with mock.patch("zuzhuang.pypi._load_name_index", return_value=fake):
            out = pypi.search_packages("pkg", limit=5)
        assert len(out) == 5


@pytest.mark.skipif(True, reason="network call - run manually")
class TestLiveNetwork:
    def test_lookup_real_package(self):
        info = pypi.lookup_package("numpy")
        assert info is not None
        assert info.name.lower() == "numpy"
