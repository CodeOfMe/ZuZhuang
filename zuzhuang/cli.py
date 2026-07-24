"""Command-line interface for ZuZhuang."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from zuzhuang.__version__ import __version__
from zuzhuang.api import zuzhuang_build, zuzhuang_list_python


def _setup_logging(verbose: bool, quiet: bool) -> None:
    if quiet:
        logging.getLogger().setLevel(logging.WARNING)
    elif verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)
    logging.basicConfig(
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="zuzhuang",
        description="Assemble portable Python environments (组装)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  zuzhuang build 3.12.6 --packages numpy,pandas -o ./py312\n"
            "  zuzhuang build 3.11.9 -o ./py311 --target windows\n"
            "  zuzhuang list-python\n"
            "  zuzhuang list-python --os windows\n"
        ),
    )
    parser.add_argument("-V", "--version", action="version", version=f"zuzhuang {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress non-essential output")

    sub = parser.add_subparsers(dest="command", help="Commands")

    # build
    build_parser = sub.add_parser("build", help="Build a portable Python environment")
    build_parser.add_argument("python_version", help="Python version (e.g. 3.11.9)")
    build_parser.add_argument(
        "-o", "--output", dest="output_dir", required=True, help="Output directory"
    )
    build_parser.add_argument(
        "-p", "--packages", help="Comma-separated list of pip packages"
    )
    build_parser.add_argument(
        "--target",
        dest="target_os",
        choices=["windows", "macos", "linux"],
        help="Target OS (auto-detect if not specified)",
    )
    build_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing output directory"
    )
    build_parser.add_argument(
        "--json", action="store_true", dest="json_output", help="Output result as JSON"
    )
    build_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output"
    )

    # list-python
    list_parser = sub.add_parser(
        "list-python", help="List available Python versions"
    )
    list_parser.add_argument(
        "--os",
        dest="target_os",
        choices=["windows", "macos", "linux"],
        help="Filter by OS",
    )
    list_parser.add_argument(
        "--json", action="store_true", dest="json_output", help="Output as JSON"
    )
    list_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output"
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    verbose = getattr(args, "verbose", False)
    quiet = getattr(args, "quiet", False)
    _setup_logging(verbose, quiet)

    if not args.command:
        _parse_args(["--help"])
        sys.exit(1)

    if args.command == "build":
        packages = args.packages.split(",") if args.packages else []
        packages = [p.strip() for p in packages if p.strip()]

        result = zuzhuang_build(
            python_version=args.python_version,
            packages=packages,
            output_dir=args.output_dir,
            target_os=getattr(args, "target_os", None),
            force=getattr(args, "force", False),
        )
    elif args.command == "list-python":
        result = zuzhuang_list_python(
            target_os=getattr(args, "target_os", None),
        )
    else:
        _parse_args(["--help"])
        sys.exit(1)

    json_output = getattr(args, "json_output", False)
    if json_output:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        if result.success:
            if isinstance(result.data, dict):
                for k, v in result.data.items():
                    if isinstance(v, list):
                        print(f"{k}:")
                        for item in v:
                            print(f"  {item}")
                    else:
                        print(f"{k}: {v}")
        else:
            print(f"Error: {result.error}", file=sys.stderr)

    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
