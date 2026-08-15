# ZuZhuang -- Assemble Portable Python Environments

ZuZhuang (组装) assembles portable, self-contained Python environments for any
operating system. Download a Python release, install your packages, unpack, set
a few environment variables, and you're ready to go.

## Features

- **Cross-platform**: build portable Python for Windows, macOS, and Linux
- **Self-contained**: the output directory is fully portable - copy it to a USB
  drive, network share, or another machine
- **Package installation**: automatically install pip and any packages you need
- **Activation scripts**: generates `activate.bat` (Windows) and `activate.sh`
  (macOS/Linux) for one-command setup
- **Python versions**: lists all available versions from python.org

## Requirements

- Python 3.9+
- git, make, gcc, and libssl-dev (Linux builds only)
- Flask + requests (web UI only): `pip install 'zuzhuang[web]'`

## Installation

```bash
pip install zuzhuang
```

## Quick Start

```bash
# List available Python versions
zuzhuang list-python

# Build a portable Python 3.12.6 environment with numpy and pandas
zuzhuang build 3.12.6 --packages numpy,pandas -o ./my-python

# Activate the environment
# Windows: my-python\activate.bat
# macOS/Linux: source my-python/activate.sh

# Run Python
python -c "import numpy; print(numpy.__version__)"
```

## Web UI

A browser-based interface lets you configure a build, link packages against
PyPI, assemble + verify, and download the resulting zip — all operating
systems supported.

```bash
pip install 'zuzhuang[web]'
zuzhuang web
# open http://127.0.0.1:5000
```

The UI lets you:

1. **Pick a target OS** (Windows / macOS / Linux) — cross-host packaging is
   supported; the host warns when it can't natively run the target.
2. **Pick a Python version** (fetched live from python.org).
3. **Add packages** — search PyPI, pin versions, and resolve specifiers
   (`numpy`, `pandas==2.2.0`, `requests[socks]>=2.31`).
4. **Assemble & verify** — runs the assembly in the background, streams live
   progress via Server-Sent Events, and **actually runs the assembled
   interpreter to import every requested package** so there are no hidden
   dependency problems. Cross-host builds defer verification to the target
   machine and include a `requirements.txt` + install script in the zip.
5. **Download the zip** — once verification passes, download a portable zip.

## Usage

### CLI

```
zuzhuang build <version> -o <output_dir> [options]
zuzhuang list-python [options]
zuzhuang web [options]
```

### Build Options

| Flag | Description |
|------|-------------|
| `-o`, `--output` | Output directory (required) |
| `-p`, `--packages` | Comma-separated pip packages |
| `--target` | Target OS: windows, macos, or linux (auto-detect) |
| `--force` | Overwrite existing output directory |
| `--json` | Output as JSON |
| `-v`, `--verbose` | Verbose output |

## Python API

```python
from zuzhuang import zuzhuang_build, zuzhuang_list_python

# List available versions
result = zuzhuang_list_python(target_os="windows")
print(result.data["versions"])

# Build a portable environment
result = zuzhuang_build(
    python_version="3.12.6",
    packages=["numpy", "pandas"],
    output_dir="./my-python",
)
print(result.success)
```

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
ruff format .
```

## License

GPL-3.0
