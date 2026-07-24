"""Core engine for assembling portable Python environments."""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen, urlretrieve

logger = logging.getLogger(__name__)

PYTHON_DOWNLOAD_BASE = "https://www.python.org/ftp/python/"
PYTHON_RELEASES_URL = "https://www.python.org/downloads/"

WINDOWS_EMBED_ARCH_MAP = {
    "AMD64": "amd64",
    "x86_64": "amd64",
    "x86": "win32",
    "ARM64": "arm64",
}

MACOS_ARCH_MAP = {
    "x86_64": "x86_64",
    "arm64": "universal2",
}


def _get_arch() -> str:
    """Get normalized architecture string for the current machine."""
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "x86_64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("i386", "i686", "x86"):
        return "x86"
    return machine


def _get_os() -> str:
    """Get normalized OS string."""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


@dataclass
class PythonRelease:
    """Metadata for a Python release."""

    version: str
    url: str
    os_name: str
    arch: str
    size: int | None = None
    sha256: str | None = None


def fetch_python_releases() -> list[PythonRelease]:
    """Fetch available Python releases from python.org.

    Returns:
        List of PythonRelease objects.
    """
    releases: list[PythonRelease] = []

    try:
        with urlopen(PYTHON_DOWNLOAD_BASE) as resp:
            html = resp.read().decode("utf-8")
    except OSError:
        return releases

    versions = re.findall(r'href="(\d+\.\d+\.\d+)/"', html)
    versions = sorted(set(versions), key=lambda v: tuple(int(x) for x in v.split(".")))

    for ver in versions:
        base = f"{PYTHON_DOWNLOAD_BASE}{ver}/"

        # Windows embeddable
        releases.append(PythonRelease(
            version=ver,
            url=f"{base}python-{ver}-embed-amd64.zip",
            os_name="windows",
            arch="x86_64",
        ))
        releases.append(PythonRelease(
            version=ver,
            url=f"{base}python-{ver}-embed-win32.zip",
            os_name="windows",
            arch="x86",
        ))
        releases.append(PythonRelease(
            version=ver,
            url=f"{base}python-{ver}-embed-arm64.zip",
            os_name="windows",
            arch="arm64",
        ))

        # macOS universal2 pkg
        releases.append(PythonRelease(
            version=ver,
            url=f"{base}python-{ver}-macos11.pkg",
            os_name="macos",
            arch="universal2",
        ))

        # Linux source tarball
        releases.append(PythonRelease(
            version=ver,
            url=f"{base}Python-{ver}.tgz",
            os_name="linux",
            arch="x86_64",
        ))

    return releases


def _windows_arch_key() -> str:
    return WINDOWS_EMBED_ARCH_MAP.get(_get_arch(), "amd64")


def _download_file(url: str, dest: Path) -> bool:
    """Download a file from URL to dest with progress logging."""
    logger.info("Downloading %s", url)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        urlretrieve(url, str(dest))
        return True
    except OSError as e:
        logger.error("Download failed: %s", e)
        return False


def _build_windows(output_dir: Path, version: str) -> bool:
    """Assemble Windows portable Python using embeddable zip."""
    arch = _windows_arch_key()
    url = f"{PYTHON_DOWNLOAD_BASE}{version}/python-{version}-embed-{arch}.zip"
    zip_path = output_dir / "_python_embed.zip"

    if not _download_file(url, zip_path):
        return False

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(output_dir)
    finally:
        zip_path.unlink(missing_ok=True)

    # Enable pip by modifying pythonXX._pth
    pth_file = None
    for f in output_dir.iterdir():
        if f.suffix == "._pth":
            pth_file = f
            break

    if pth_file:
        content = pth_file.read_text()
        if "import site" not in content:
            content += "\nimport site\n"
        # Ensure Lib directory is in path
        lib_path = output_dir / "Lib"
        lib_path.mkdir(exist_ok=True)
        pth_file.write_text(content)

    # Install pip
    get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
    get_pip_path = output_dir / "get-pip.py"
    if not _download_file(get_pip_url, get_pip_path):
        return False

    python_exe = output_dir / "python.exe"
    if not python_exe.exists():
        logger.error("python.exe not found after extraction")
        return False

    result = subprocess.run(
        [str(python_exe), str(get_pip_path), "--no-warn-script-location"],
        capture_output=True,
        text=True,
        cwd=output_dir,
    )
    get_pip_path.unlink(missing_ok=True)

    if result.returncode != 0:
        logger.error("pip install failed: %s", result.stderr)
        return False

    return True


def _rebuild_relocatable(python_dir: Path) -> bool:
    """Make macOS/Linux python relocatable by patching sysconfig paths."""

    python_exe = python_dir / "bin" / "python3"
    if not python_exe.exists():
        python_exe = python_dir / "bin" / "python3"
        for candidate in python_dir.glob("bin/python*"):
            if candidate.is_file() and not candidate.is_symlink():
                python_exe = candidate
                break

    if not python_exe.is_file():
        logger.warning("Could not find python binary to patch")
        return False

    # Write a _site.py that fixes sys.path to be relative
    site_py = python_dir / "lib" / f"python{python_exe.name.replace('python', '')}"
    if not site_py.exists():
        site_py = python_dir / "lib"
        for d in python_dir.glob("lib/python*"):
            if d.is_dir():
                site_py = d / "site.py"
                break

    if isinstance(site_py, Path) and site_py.parent.is_dir():
        sitecustomize = site_py.parent / "sitecustomize.py"
        sitecustomize.write_text("""import os, sys
_base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Set prefixes relative to the python binary location
sys.prefix = _base
if hasattr(sys, 'real_prefix'):
    sys.real_prefix = _base
""")
    return True


def _build_macos(output_dir: Path, version: str) -> bool:
    """Assemble macOS portable Python from python.org pkg."""
    url = f"{PYTHON_DOWNLOAD_BASE}{version}/python-{version}-macos11.pkg"
    pkg_path = output_dir / "_python.pkg"

    if not _download_file(url, pkg_path):
        return False

    try:
        expanded_dir = output_dir / "_pkg_expanded"
        expanded_dir.mkdir(exist_ok=True)
        subprocess.run(
            ["pkgutil", "--expand", str(pkg_path), str(expanded_dir / "expanded.pkg")],
            check=True,
            capture_output=True,
        )

        # Find the payload
        payload = expanded_dir / "expanded.pkg" / "Payload"
        if not payload.exists():
            # Try alternate location
            for p in (expanded_dir / "expanded.pkg").rglob("Payload"):
                payload = p
                break

        if payload.exists():
            subprocess.run(
                ["tar", "-xzf", str(payload), "-C", str(output_dir)],
                check=True,
                capture_output=True,
            )

        shutil.rmtree(expanded_dir, ignore_errors=True)
    except subprocess.CalledProcessError as e:
        logger.error("pkg extraction failed: %s", e)
        return False
    finally:
        pkg_path.unlink(missing_ok=True)

    # The extracted content is under a Python.framework
    framework = output_dir / "Python.framework" / "Versions"
    if framework.exists():
        ver_dir = next(framework.iterdir()) if any(framework.iterdir()) else None
        if ver_dir:
            bin_dir = ver_dir / "bin"
            # Symlink the framework's bin
            bin_link = output_dir / "bin"
            bin_link.mkdir(exist_ok=True)
            for f in bin_dir.iterdir():
                target = bin_link / f.name
                if not target.exists():
                    target.symlink_to(os.path.relpath(f, bin_link))

    return True


def _build_linux(output_dir: Path, version: str) -> bool:
    """Assemble Linux portable Python by building from source."""
    url = f"{PYTHON_DOWNLOAD_BASE}{version}/Python-{version}.tgz"
    tarball = output_dir / "_python.tgz"

    if not _download_file(url, tarball):
        return False

    build_dir = output_dir / "_build"
    build_dir.mkdir(exist_ok=True)

    try:
        with tarfile.open(tarball, "r:gz") as tf:
            tf.extractall(build_dir)

        src_dir = next(build_dir.glob("Python-*"))
        nproc = os.cpu_count() or 4

        prefix = output_dir.resolve()
        subprocess.run(
            [
                str(src_dir / "configure"),
                f"--prefix={prefix}",
                "--enable-optimizations",
                "--with-ensurepip=install",
            ],
            cwd=src_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["make", f"-j{nproc}"],
            cwd=src_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["make", "install"],
            cwd=src_dir,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error("Build failed: %s", e)
        return False
    finally:
        tarball.unlink(missing_ok=True)
        shutil.rmtree(build_dir, ignore_errors=True)

    return True


def _install_packages(python_bin: Path, packages: list[str]) -> bool:
    """Install pip packages into the portable environment."""
    if not packages:
        return True

    pip_args = [str(python_bin), "-m", "pip", "install", "--no-warn-script-location"]
    pip_args.extend(packages)

    result = subprocess.run(pip_args, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("Package install failed: %s", result.stderr)
        return False
    return True


def _write_activate_scripts(output_dir: Path) -> None:
    """Write activation scripts for the portable environment."""
    # Windows
    bat = output_dir / "activate.bat"
    bat.write_text(
        '@echo off\r\n'
        'set PYTHON_HOME=%~dp0\r\n'
        'set PATH=%~dp0;%~dp0Scripts;%PATH%\r\n'
        'echo Python portable environment activated\r\n'
        'python --version\r\n'
    )

    # Unix
    sh = output_dir / "activate.sh"
    sh.write_text(
        '#!/bin/sh\n'
        'PYTHON_HOME="$(cd "$(dirname "$0")" && pwd)"\n'
        'export PYTHON_HOME\n'
        'export PATH="$PYTHON_HOME/bin:$PATH"\n'
        'echo "Python portable environment activated"\n'
        'python3 --version\n'
    )
    sh.chmod(0o755)


def assemble(
    *,
    python_version: str,
    packages: list[str] | None = None,
    output_dir: str | Path,
    target_os: str | None = None,
    force: bool = False,
) -> bool:
    """Assemble a portable Python environment.

    Args:
        python_version: Python version (e.g. "3.11.9").
        packages: List of pip packages to install.
        output_dir: Output directory for the portable environment.
        target_os: Target OS (windows, macos, linux). Auto-detect if None.
        force: Overwrite existing output directory.

    Returns:
        True if assembly succeeded.
    """
    output = Path(output_dir).resolve()

    if output.exists():
        if force:
            shutil.rmtree(output)
        else:
            logger.error("Output directory already exists: %s (use --force)", output)
            return False

    output.mkdir(parents=True, exist_ok=True)
    packages = packages or []
    target = target_os or _get_os()

    logger.info("Assembling Python %s for %s", python_version, target)
    logger.info("Output: %s", output)
    if packages:
        logger.info("Packages: %s", ", ".join(packages))

    if target == "windows":
        ok = _build_windows(output, python_version)
    elif target == "macos":
        ok = _build_macos(output, python_version)
    elif target == "linux":
        ok = _build_linux(output, python_version)
    else:
        logger.error("Unsupported OS: %s", target)
        return False

    if not ok:
        return False

    # Find python binary
    if target == "windows":
        python_bin = output / "python.exe"
    else:
        python_bin = output / "bin" / "python3"
        if not python_bin.exists():
            candidates = sorted(output.glob("bin/python*"))
            for c in candidates:
                if c.is_file() and not c.is_symlink():
                    python_bin = c
                    break

    logger.info("Python binary: %s", python_bin)

    _write_activate_scripts(output)

    if packages:
        logger.info("Installing packages...")
        if not _install_packages(python_bin, packages):
            return False

    logger.info("Assembly complete: %s", output)
    return True
