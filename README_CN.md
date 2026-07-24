# ZuZhuang (组装) -- 便携式 Python 环境组装工具

ZuZhuang (组装) 为各种操作系统组装便携式、自包含的 Python 环境。下载 Python
发行版，安装你需要的包，解压后设置几个环境变量即可使用。

## 功能特性

- **跨平台**：支持构建 Windows、macOS、Linux 的便携式 Python
- **自包含**：输出目录完全可移植，可复制到 U 盘、网络共享或其他机器
- **包安装**：自动安装 pip 和你需要的任何包
- **激活脚本**：生成 `activate.bat`（Windows）和 `activate.sh`（macOS/Linux），一键配置
- **版本列表**：列出 python.org 上所有可用版本

## 系统要求

- Python 3.9+
- Linux 构建需要：git、make、gcc、libssl-dev

## 安装

```bash
pip install zuzhuang
```

## 快速开始

```bash
# 列出可用的 Python 版本
zuzhuang list-python

# 构建一个包含 numpy 和 pandas 的便携式 Python 3.12.6 环境
zuzhuang build 3.12.6 --packages numpy,pandas -o ./my-python

# 激活环境
# Windows: my-python\activate.bat
# macOS/Linux: source my-python/activate.sh

# 运行 Python
python -c "import numpy; print(numpy.__version__)"
```

## 使用方法

### 命令行

```
zuzhuang build <版本号> -o <输出目录> [选项]
zuzhuang list-python [选项]
```

### 构建选项

| 选项 | 说明 |
|------|------|
| `-o`, `--output` | 输出目录（必需） |
| `-p`, `--packages` | 逗号分隔的 pip 包列表 |
| `--target` | 目标系统：windows、macos、linux（自动检测） |
| `--force` | 覆盖已有的输出目录 |
| `--json` | JSON 格式输出 |
| `-v`, `--verbose` | 详细输出 |

## Python API

```python
from zuzhuang import zuzhuang_build, zuzhuang_list_python

# 列出可用版本
result = zuzhuang_list_python(target_os="windows")
print(result.data["versions"])

# 构建便携式环境
result = zuzhuang_build(
    python_version="3.12.6",
    packages=["numpy", "pandas"],
    output_dir="./my-python",
)
print(result.success)
```

## 开发

```bash
pip install -e ".[dev]"
pytest
ruff check .
ruff format .
```

## 许可证

GPL-3.0
