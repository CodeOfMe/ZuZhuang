"""Orchestrator: build, verify, and package a portable Python environment.

This is the engine behind the web UI. It wraps :mod:`zuzhuang.core` with:

1. A progress callback so the UI can stream live status.
2. A verification step that actually runs the assembled Python and imports
   every requested package - this is the "no dependency problems" check.
3. A zip step so the final artefact is downloadable.

The orchestrator is intentionally framework-agnostic; the web layer feeds it
jobs and consumes the progress events.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .__version__ import __version__
from .core import _get_os, assemble

logger = logging.getLogger(__name__)

ProgressFn = Callable[[dict], None]


@dataclass
class JobResult:
    """Final outcome of an orchestration run."""

    success: bool
    output_dir: str = ""
    zip_path: str = ""
    verify_log: list[dict] = field(default_factory=list)
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "output_dir": self.output_dir,
            "zip_path": self.zip_path,
            "verify_log": self.verify_log,
            "error": self.error,
            "metadata": self.metadata,
        }


def _emit(progress: ProgressFn | None, **fields: Any) -> None:
    if progress is None:
        return
    try:
        progress({"ts": time.time(), **fields})
    except Exception:
        logger.debug("progress callback raised", exc_info=True)


def _find_python_bin(output_dir: Path, target_os: str) -> Path | None:
    """Locate the assembled python executable inside output_dir."""
    if target_os == "windows":
        p = output_dir / "python.exe"
        return p if p.exists() else None

    # unix: bin/python3 or bin/python
    for name in ("python3", "python"):
        p = output_dir / "bin" / name
        if p.exists():
            return p
    for c in sorted((output_dir / "bin").glob("python*")) if (output_dir / "bin").exists() else []:
        if c.is_file():
            return c
    return None


def _verify_imports(
    python_bin: Path,
    packages: list[str],
    progress: ProgressFn | None,
) -> list[dict]:
    """Run the assembled python and import each package. Returns a per-package log."""
    log: list[dict] = []
    names = []
    for spec in packages:
        # spec may be "name==1.2.3" or "name[extra]==1.2.3" - take the base name
        import re

        m = re.match(r"^([A-Za-z0-9_.\-]+)", spec)
        if m:
            names.append(m.group(1))

    if not names:
        return log

    _emit(progress, stage="verify", message="Verifying imports with assembled Python...")

    # First a sanity check that the interpreter itself runs.
    try:
        r = subprocess.run(
            [str(python_bin), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            _emit(progress, stage="verify", message="Interpreter failed to start", status="error")
            return [{"name": "<interpreter>", "ok": False, "error": r.stderr.strip()}]
        _emit(progress, stage="verify", message=f"Interpreter OK: {r.stdout.strip()}")
    except Exception as e:
        _emit(progress, stage="verify", message=f"Interpreter check failed: {e}", status="error")
        return [{"name": "<interpreter>", "ok": False, "error": str(e)}]

    # Build a single import script so we only fork the interpreter once.
    # This is faster and also catches top-level import-order issues.
    script_lines = ["import sys, json", "results = []"]
    for n in names:
        script_lines.append(
            "try:\n"
            f"    import {n}\n"
            f"    v = getattr({n}, '__version__', '?')\n"
            f"    results.append({{'name': {n!r}, 'ok': True, 'version': v}})\n"
            "except Exception as e:\n"
            f"    results.append({{'name': {n!r}, 'ok': False, 'error': str(e)}})\n"
        )
    script_lines.append("print(json.dumps(results))")
    script = "\n".join(script_lines)

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script)
        script_path = f.name

    try:
        env = dict(os.environ)
        # Ensure the portable site-packages is on the path for embeddable python
        r = subprocess.run(
            [str(python_bin), script_path],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if r.returncode == 0 and r.stdout.strip():
            import json as _json

            try:
                log = _json.loads(r.stdout.strip().splitlines()[-1])
            except Exception:
                log = []
        if r.returncode != 0:
            log.append(
                {"name": "<script>", "ok": False, "error": (r.stderr or r.stdout).strip()[:500]}
            )
    finally:
        Path(script_path).unlink(missing_ok=True)

    # Stream per-package status
    for entry in log:
        status = "ok" if entry.get("ok") else "fail"
        ver = entry.get("version", "")
        msg = f"{entry['name']}: {status}" + (f" ({ver})" if ver else "")
        if not entry.get("ok"):
            msg += f" - {entry.get('error', '')}"
        _emit(progress, stage="verify", message=msg, package=entry["name"], status=status)

    return log


def _zip_dir(output_dir: Path, zip_path: Path) -> bool:
    """Recursively zip a directory into a portable archive."""
    try:
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(output_dir):
                for fn in files:
                    fp = Path(root) / fn
                    arcname = fp.relative_to(output_dir)
                    zf.write(fp, arcname)
        return True
    except OSError as e:
        logger.error("zip failed: %s", e)
        return False


def run_assembly(
    *,
    python_version: str,
    packages: list[str],
    output_dir: str | Path,
    target_os: str | None = None,
    force: bool = True,
    progress: ProgressFn | None = None,
    do_verify: bool = True,
    do_zip: bool = True,
    zip_path: str | Path | None = None,
) -> JobResult:
    """End-to-end assembly + verification + zipping.

    Args:
        python_version: e.g. "3.12.6".
        packages: list of pip specifiers ("numpy", "pandas==2.2.0", ...).
        output_dir: where the portable env is assembled.
        target_os: windows/macos/linux (auto-detect if None).
        force: overwrite existing output dir.
        progress: optional callback receiving event dicts.
        do_verify: run the import-verification step.
        do_zip: zip the result for download.
        zip_path: where to write the zip. Auto-named if None.

    Returns:
        JobResult with full status.
    """
    output = Path(output_dir).resolve()
    target = target_os or _get_os()

    _emit(
        progress,
        stage="start",
        message=f"Starting assembly: Python {python_version} for {target}",
    )
    if packages:
        _emit(progress, stage="start", message=f"Packages: {', '.join(packages)}")

    # ---- Build ----
    _emit(progress, stage="build", message="Downloading and assembling Python...")
    try:
        ok = assemble(
            python_version=python_version,
            packages=packages,
            output_dir=output,
            target_os=target,
            force=force,
        )
    except Exception as e:
        _emit(progress, stage="build", message=f"Build error: {e}", status="error")
        return JobResult(success=False, error=str(e))

    if not ok:
        _emit(progress, stage="build", message="Build failed", status="error")
        return JobResult(success=False, error="Assembly failed")

    _emit(progress, stage="build", message="Build complete", status="ok")

    # ---- Verify ----
    verify_log: list[dict] = []
    host = _get_os()
    cross_host = host != target
    if do_verify and cross_host:
        _emit(
            progress,
            stage="verify",
            message=(
                f"Cross-host build ({host} -> {target}): can't run the target "
                "interpreter here. Verification deferred to the target machine. "
                "A requirements.txt + install script is included in the zip."
            ),
            status="warn",
        )
    elif do_verify:
        python_bin = _find_python_bin(output, target)
        if python_bin is None or not python_bin.exists():
            _emit(
                progress,
                stage="verify",
                message="Python binary not found - skipping verify",
                status="warn",
            )
        else:
            _emit(progress, stage="verify", message=f"Using {python_bin}")
            # If packages were installed during build, verify them; otherwise
            # just confirm the interpreter runs.
            specs = packages if packages else []
            verify_log = _verify_imports(python_bin, specs, progress)
            all_ok = all(e.get("ok") for e in verify_log) if verify_log else True
            if not all_ok:
                _emit(progress, stage="verify", message="Some imports failed", status="error")
                return JobResult(
                    success=False,
                    output_dir=str(output),
                    verify_log=verify_log,
                    error="Dependency verification failed",
                )
            _emit(progress, stage="verify", message="All imports OK", status="ok")

    # ---- Zip ----
    final_zip = ""
    if do_zip:
        if zip_path is None:
            zip_path = output.parent / f"{output.name}.zip"
        zp = Path(zip_path)
        _emit(progress, stage="zip", message=f"Creating archive: {zp.name}")
        if not _zip_dir(output, zp):
            _emit(progress, stage="zip", message="Zip failed", status="error")
            return JobResult(success=False, error="Zip failed", output_dir=str(output))
        final_zip = str(zp)
        _emit(progress, stage="zip", message=f"Archive ready: {zp.name}", status="ok")

    _emit(progress, stage="done", message="Done", status="ok")
    return JobResult(
        success=True,
        output_dir=str(output),
        zip_path=final_zip,
        verify_log=verify_log,
        metadata={"version": __version__, "target_os": target, "python_version": python_version},
    )


def run_in_thread(
    kwargs: dict,
    on_done: Callable[[JobResult], None],
    progress: ProgressFn | None = None,
) -> threading.Thread:
    """Run :func:`run_assembly` in a background thread, calling on_done when finished."""

    def _worker():
        try:
            result = run_assembly(progress=progress, **kwargs)
        except Exception as e:
            result = JobResult(success=False, error=str(e))
        try:
            on_done(result)
        except Exception:
            logger.debug("on_done callback raised", exc_info=True)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t


def current_host_supports(target_os: str) -> bool:
    """Whether the host can produce a usable artefact for the given target OS.

    Windows embeddable zips can be downloaded and packaged on any host (pip
    install + import verification are deferred to the target machine). macOS
    pkg extraction needs `pkgutil` (macOS only) and Linux source builds need a
    native toolchain (Linux only).
    """
    host = _get_os()
    if target_os == "windows":
        return True
    if target_os == "macos":
        return host == "macos"
    if target_os == "linux":
        return host == "linux"
    return False


def host_can_verify(target_os: str) -> bool:
    """Whether the host can actually run the assembled interpreter to verify imports."""
    return _get_os() == target_os


def available_python_versions(target_os: str | None = None) -> list[str]:
    """Convenience wrapper around core.fetch_python_releases returning just versions."""
    from .core import fetch_python_releases

    try:
        releases = fetch_python_releases()
    except Exception:
        return []
    if target_os:
        releases = [r for r in releases if r.os_name == target_os]
    return sorted({r.version for r in releases}, reverse=True)
