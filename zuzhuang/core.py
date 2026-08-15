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
        releases.append(
            PythonRelease(
                version=ver,
                url=f"{base}python-{ver}-embed-amd64.zip",
                os_name="windows",
                arch="x86_64",
            )
        )
        releases.append(
            PythonRelease(
                version=ver,
                url=f"{base}python-{ver}-embed-win32.zip",
                os_name="windows",
                arch="x86",
            )
        )
        releases.append(
            PythonRelease(
                version=ver,
                url=f"{base}python-{ver}-embed-arm64.zip",
                os_name="windows",
                arch="arm64",
            )
        )

        # macOS universal2 pkg
        releases.append(
            PythonRelease(
                version=ver,
                url=f"{base}python-{ver}-macos11.pkg",
                os_name="macos",
                arch="universal2",
            )
        )

        # Linux source tarball
        releases.append(
            PythonRelease(
                version=ver,
                url=f"{base}Python-{ver}.tgz",
                os_name="linux",
                arch="x86_64",
            )
        )

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

    # Install pip (only possible when running on a Windows host)
    get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
    get_pip_path = output_dir / "get-pip.py"
    if not _download_file(get_pip_url, get_pip_path):
        return False

    python_exe = output_dir / "python.exe"
    if not python_exe.exists():
        logger.error("python.exe not found after extraction")
        return False

    if _get_os() == "windows":
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
    else:
        # Cross-host: can't execute python.exe here. Leave get-pip.py in place
        # so the user can bootstrap pip on the target Windows machine, and
        # write a small helper script for them to run.
        helper = output_dir / "install-pip.bat"
        helper.write_text(
            "@echo off\r\n"
            "cd /d %~dp0\r\n"
            "python get-pip.py --no-warn-script-location\r\n"
            "echo pip installed. You can now: python -m pip install <packages>\r\n"
        )
        logger.info(
            "Cross-host build: pip not installed. Run install-pip.bat on the "
            "target Windows machine to bootstrap pip."
        )

    return True


def _rebuild_relocatable(python_dir: Path) -> bool:
    """Make macOS/Linux python relocatable by writing a sitecustomize.py
    that pins sys.prefix / sys.exec_prefix to the bundle location.
    """
    # Find the python binary
    python_exe = python_dir / "bin" / "python3"
    if not python_exe.exists():
        for candidate in python_dir.glob("bin/python*"):
            if candidate.is_file() and not candidate.is_symlink():
                python_exe = candidate
                break

    if not python_exe.is_file():
        logger.warning("Could not find python binary to patch")
        return False

    # Locate the lib/pythonX.Y directory that holds site.py
    ver = python_exe.name.replace("python", "")
    site_dir = None
    for candidate in (
        python_dir / "lib" / f"python{ver}",
        python_dir / "Versions" / ver.lstrip("3") / "lib" / f"python{ver}" if ver else None,
    ):
        if candidate and candidate.is_dir():
            site_dir = candidate
            break
    if site_dir is None:
        for d in python_dir.glob("**/python3.*/"):
            if d.is_dir() and (d / "site.py").exists() or (d / "os.py").exists():
                site_dir = d
                break

    if site_dir is None or not site_dir.is_dir():
        logger.warning("Could not find site-packages dir to patch")
        return False

    sitecustomize = site_dir / "sitecustomize.py"
    sitecustomize.write_text(
        "import os, sys\n"
        "# Resolve the version root relative to this file's location so the\n"
        "# environment is fully relocatable. sitecustomize.py lives in\n"
        "# <verroot>/lib/pythonX.Y/ -- climb two levels to reach <verroot>.\n"
        "_here = os.path.dirname(os.path.abspath(__file__))\n"
        "_verroot = os.path.dirname(os.path.dirname(_here))\n"
        "sys.prefix = _verroot\n"
        "sys.exec_prefix = _verroot\n"
        "if hasattr(sys, 'real_prefix'):\n"
        "    sys.real_prefix = _verroot\n"
        "# Fix the lib-dynload path: site.py computed it from the build-time\n"
        "# exec_prefix (an absolute /Library/Frameworks path). Replace any\n"
        "# such entry with the relocatable one.\n"
        "_dynload = os.path.join(_here, 'lib-dynload')\n"
        "if os.path.isdir(_dynload):\n"
        "    sys.path = [\n"
        "        (os.path.join(_verroot, 'lib', 'python' + sys.version[:3],\n"
        "         'lib-dynload') if 'lib-dynload' in p else p)\n"
        "        for p in sys.path\n"
        "    ]\n"
        "    if _dynload not in sys.path:\n"
        "        sys.path.insert(0, _dynload)\n"
        "# Ensure site-packages is importable\n"
        "_sp = os.path.join(_here, 'site-packages')\n"
        "if os.path.isdir(_sp) and _sp not in sys.path:\n"
        "    sys.path.insert(0, _sp)\n"
    )
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

        # The pkg contains several sub-packages; we only want the framework.
        # Find Python_Framework.pkg/Payload (and fall back to any Payload).
        payloads = sorted((expanded_dir / "expanded.pkg").rglob("Payload"))
        framework_payload = None
        for p in payloads:
            if "Framework" in p.parent.name:
                framework_payload = p
                break
        if framework_payload is None and payloads:
            framework_payload = payloads[0]

        if framework_payload is not None:
            subprocess.run(
                ["tar", "-xzf", str(framework_payload), "-C", str(output_dir)],
                check=True,
                capture_output=True,
            )

        shutil.rmtree(expanded_dir, ignore_errors=True)
    except subprocess.CalledProcessError as e:
        logger.error("pkg extraction failed: %s", e)
        return False
    finally:
        pkg_path.unlink(missing_ok=True)

    # The framework may be extracted directly under output_dir (Versions/3.x/...)
    # or nested under Python.framework/Versions/...
    versions = output_dir / "Versions"
    if not versions.exists():
        framework_root = output_dir / "Python.framework"
        if not framework_root.exists():
            for cand in output_dir.glob("**/Python.framework"):
                framework_root = cand
                break
        versions = framework_root / "Versions" if framework_root.exists() else None

    if versions and versions.exists():
        ver_dirs = [d for d in versions.iterdir() if d.is_dir()]
        if ver_dirs:
            ver_dir = ver_dirs[0]
            bin_dir = ver_dir / "bin"
            # Symlink the framework's bin into output_dir/bin for convenience
            bin_link = output_dir / "bin"
            bin_link.mkdir(exist_ok=True)
            if bin_dir.exists():
                for f in bin_dir.iterdir():
                    target = bin_link / f.name
                    if not target.exists():
                        target.symlink_to(os.path.relpath(f, bin_link))
            # Make the python binary relocatable-friendly
            _make_macos_relocatable(ver_dir)
            _rebuild_relocatable(output_dir)

    return True


def _make_macos_relocatable(ver_dir: Path) -> None:
    """Rewrite absolute dylib references in the macOS framework so it runs
    from any location (not just /Library/Frameworks/Python.framework).

    Uses @rpath for all cross-binary references (so binaries anywhere in the
    tree resolve uniformly) and @loader_path for sibling dylibs in lib/. Each
    binary gets @rpath entries pointing at the version root and its lib/ dir.
    Then re-signs ad-hoc without the hardened-runtime flag so the modified
    binaries run.
    """
    if _get_os() != "macos":
        return
    py_lib = ver_dir / "Python"
    if not py_lib.exists():
        return

    fw_prefix = f"/Library/Frameworks/Python.framework/Versions/{ver_dir.name}"

    def _is_macho(p: Path) -> bool:
        try:
            r = subprocess.run(["file", "-b", str(p)], capture_output=True, text=True, timeout=10)
            kind = r.stdout.strip()
            return kind.startswith("Mach-O") and (
                "executable" in kind or "dynamically" in kind or "bundle" in kind
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    _non_macho_ext = {
        ".py",
        ".pyc",
        ".pyo",
        ".txt",
        ".rst",
        ".html",
        ".xml",
        ".json",
        ".cfg",
        ".ini",
        ".toml",
        ".a",
        ".o",
        ".whl",
        ".dist-info",
    }

    macho_files: list[Path] = []
    for b in ver_dir.rglob("*"):
        if not b.is_file() or b.is_symlink() or b == py_lib:
            continue
        if b.suffix.lower() in _non_macho_ext:
            continue
        if _is_macho(b):
            macho_files.append(b)

    # Every absolute framework ref becomes @rpath/<basename>.
    known_refs = [
        f"{fw_prefix}/Python",
        f"{fw_prefix}/lib/libssl.3.dylib",
        f"{fw_prefix}/lib/libcrypto.3.dylib",
        f"{fw_prefix}/lib/libncursesw.6.dylib",
        f"{fw_prefix}/lib/libpanelw.6.dylib",
        f"{fw_prefix}/lib/libtcl8.6.dylib",
        f"{fw_prefix}/lib/libtk8.6.dylib",
        f"{fw_prefix}/lib/libsqlite3.dylib",
        f"{fw_prefix}/lib/liblzma.5.dylib",
        f"{fw_prefix}/lib/libffi.8.dylib",
        f"{fw_prefix}/lib/libintl.8.dylib",
        f"{fw_prefix}/lib/libreadline.8.dylib",
        f"{fw_prefix}/lib/libexpat.1.dylib",
        f"{fw_prefix}/lib/libgdbm.6.dylib",
        f"{fw_prefix}/lib/libgdbm_compat.4.dylib",
    ]

    def _rpath_ref(dep: str) -> str:
        return f"@rpath/{os.path.basename(dep)}"

    def _rpaths_for(binary: Path) -> list[str]:
        """@rpath entries relative to each binary's own dir (@loader_path).

        We add @loader_path/../.. (ver_dir) and @loader_path/../../lib so that
        @rpath/Python and @rpath/libssl.3.dylib both resolve regardless of where
        the binary sits in the tree.
        """
        depth = len(binary.parent.relative_to(ver_dir).parts)
        climb = "../" * depth
        return [f"@loader_path/{climb}", f"@loader_path/{climb}lib"]

    try:
        # Python dylib id -> @rpath/Python
        subprocess.run(
            ["install_name_tool", "-id", "@rpath/Python", str(py_lib)],
            check=True,
            capture_output=True,
        )
        # Rewrite every binary's deps on framework refs to @rpath/<basename>
        for b in macho_files:
            for ref in known_refs:
                subprocess.run(
                    ["install_name_tool", "-change", ref, _rpath_ref(ref), str(b)],
                    capture_output=True,
                )
            # Add @rpath entries relative to this binary's location.
            # -add_rpath errors if the path is already present; ignore.
            for rp in _rpaths_for(b):
                subprocess.run(
                    ["install_name_tool", "-add_rpath", rp, str(b)],
                    capture_output=True,
                )
        # Fix bundled dylibs' own install ids to @rpath/<name> and their deps
        # on sibling dylibs to @loader_path/<name> (same dir).
        lib_dir = ver_dir / "lib"
        if lib_dir.is_dir():
            for b in lib_dir.iterdir():
                if (
                    b.is_file()
                    and not b.is_symlink()
                    and b.name.endswith(".dylib")
                    and _is_macho(b)
                ):
                    subprocess.run(
                        ["install_name_tool", "-id", f"@rpath/{b.name}", str(b)],
                        capture_output=True,
                    )
                    for ref in known_refs:
                        if not ref.endswith(".dylib"):
                            continue
                        sibling = os.path.basename(ref)
                        new_ref = f"@loader_path/{sibling}"
                        subprocess.run(
                            ["install_name_tool", "-change", ref, new_ref, str(b)],
                            capture_output=True,
                        )
        # Re-sign ad-hoc WITHOUT the hardened-runtime flag.
        sign = ["codesign", "--force", "--sign", "-", "--options=0", "--timestamp=none"]
        subprocess.run(sign + [str(py_lib)], capture_output=True)
        for b in macho_files:
            subprocess.run(sign + [str(b)], capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning("relocatable rewrite failed (non-fatal): %s", e)


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
        "@echo off\r\n"
        "set PYTHON_HOME=%~dp0\r\n"
        "set PATH=%~dp0;%~dp0Scripts;%PATH%\r\n"
        "echo Python portable environment activated\r\n"
        "python --version\r\n"
    )

    # Unix
    sh = output_dir / "activate.sh"
    sh.write_text(
        "#!/bin/sh\n"
        'PYTHON_HOME="$(cd "$(dirname "$0")" && pwd)"\n'
        "export PYTHON_HOME\n"
        'export PATH="$PYTHON_HOME/bin:$PATH"\n'
        'echo "Python portable environment activated"\n'
        "python3 --version\n"
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

    host = _get_os()
    cross_host = host != target

    # Bootstrap pip on same-host unix builds (macOS pkg / Linux source don't
    # install pip automatically).
    if not cross_host and target != "windows":
        _ensurepip(python_bin)

    if packages:
        if cross_host:
            # Can't run the target python here; write a requirements file and a
            # helper script so the user can install on the target machine.
            logger.info(
                "Cross-host build: writing requirements.txt + install script "
                "(run on the target %s machine).",
                target,
            )
            _write_requirements(output, packages, target)
        else:
            logger.info("Installing packages...")
            if not _install_packages(python_bin, packages):
                return False

    logger.info("Assembly complete: %s", output)
    return True


def _ensurepip(python_bin: Path) -> bool:
    """Bootstrap pip via ensurepip if it isn't installed yet."""
    if not python_bin.exists():
        return False
    check = subprocess.run(
        [str(python_bin), "-c", "import pip; print(pip.__version__)"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check.returncode == 0:
        return True
    logger.info("Bootstrapping pip via ensurepip...")
    r = subprocess.run(
        [str(python_bin), "-m", "ensurepip", "--upgrade"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        logger.warning("ensurepip failed: %s", r.stderr[:300])
        return False
    return True


def _write_requirements(output_dir: Path, packages: list[str], target: str) -> None:
    """Write requirements.txt and an install helper for cross-host builds."""
    (output_dir / "requirements.txt").write_text("\n".join(packages) + "\n")
    if target == "windows":
        (output_dir / "install-packages.bat").write_text(
            "@echo off\r\n"
            "cd /d %~dp0\r\n"
            "python -m pip install --no-warn-script-location -r requirements.txt\r\n"
            "echo Packages installed.\r\n"
        )
    else:
        (output_dir / "install-packages.sh").write_text(
            "#!/bin/sh\n"
            'cd "$(dirname "$0")"\n'
            "python3 -m pip install --no-warn-script-location -r requirements.txt\n"
            'echo "Packages installed."\n'
        )
        (output_dir / "install-packages.sh").chmod(0o755)
