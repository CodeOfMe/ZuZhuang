"""Public API for ZuZhuang."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zuzhuang.__version__ import __version__


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
        }


def zuzhuang_build(
    *,
    python_version: str,
    packages: list[str] | None = None,
    output_dir: str | Path,
    target_os: str | None = None,
    force: bool = False,
) -> ToolResult:
    """Assemble a portable Python environment.

    Args:
        python_version: Python version (e.g. "3.11.9").
        packages: List of pip packages to install.
        output_dir: Output directory for the portable environment.
        target_os: Target OS (windows, macos, linux). Auto-detect if None.
        force: Overwrite existing output directory.

    Returns:
        ToolResult with success status and output path.
    """
    from .core import assemble

    try:
        ok = assemble(
            python_version=python_version,
            packages=packages,
            output_dir=output_dir,
            target_os=target_os,
            force=force,
        )
        if ok:
            return ToolResult(
                success=True,
                data={"output_dir": str(Path(output_dir).resolve())},
                metadata={"version": __version__},
            )
        return ToolResult(success=False, error="Assembly failed")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


def zuzhuang_list_python(target_os: str | None = None) -> ToolResult:
    """List available Python versions for download.

    Args:
        target_os: Filter by OS (windows, macos, linux). None for all.

    Returns:
        ToolResult with list of available releases.
    """
    from .core import fetch_python_releases

    try:
        releases = fetch_python_releases()
        if target_os:
            releases = [r for r in releases if r.os_name == target_os]

        versions = sorted({r.version for r in releases}, reverse=True)
        return ToolResult(
            success=True,
            data={"versions": versions, "count": len(versions)},
            metadata={"version": __version__},
        )
    except Exception as e:
        return ToolResult(success=False, error=str(e))
