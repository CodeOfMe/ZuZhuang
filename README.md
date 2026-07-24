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

## Usage

### CLI

```
zuzhuang build <version> -o <output_dir> [options]
zuzhuang list-python [options]
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
