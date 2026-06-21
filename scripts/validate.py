# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Validate the zha_tools custom integration.

Performs lightweight, dependency-free checks on the integration before it is
packaged for distribution:

* the version is consistent across ``manifest.json``, ``pyproject.toml`` and
  the package ``__init__.py``;
* ``services.yaml`` is valid YAML and declares the ``reconfigure`` service;
* ``strings.json`` and ``translations/en.json`` are valid JSON;
* the bundled brand icons exist and have the expected pixel dimensions.

Run via ``uv run python scripts/validate.py``.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "zha_tools"
# Since HA 2026.3.0 a custom integration serves its own brand images from a
# ``brand/`` subdirectory; Home Assistant prefers these over the brands CDN.
BRAND_DIR = INTEGRATION_DIR / "brand"

# Brand icons and their expected pixel sizes (the conventional brands sizes),
# including the dark-theme variants.
BRAND_ICONS = {
    "icon.png": (256, 256),
    "icon@2x.png": (512, 512),
    "dark_icon.png": (256, 256),
    "dark_icon@2x.png": (512, 512),
}


def read_manifest_version() -> str:
    """Return the ``version`` field from the integration's manifest.json."""
    manifest_path = INTEGRATION_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest["version"]


def read_pyproject_version() -> str:
    """Return the ``[project].version`` field from pyproject.toml."""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    with pyproject_path.open("rb") as fp:
        pyproject = tomllib.load(fp)
    return pyproject["project"]["version"]


def read_init_version() -> str:
    """Return ``__version__`` from the package __init__.py via regex.

    The module is parsed textually rather than imported so that this script
    does not need Home Assistant (or any other runtime dependency) available.
    """
    init_path = INTEGRATION_DIR / "__init__.py"
    source = init_path.read_text(encoding="utf-8")
    match = re.search(
        r"""^__version__\s*=\s*['"]([^'"]+)['"]""",
        source,
        re.MULTILINE,
    )
    if match is None:
        raise ValueError(f"__version__ not found in {init_path}")
    return match.group(1)


def validate_services() -> None:
    """Confirm services.yaml is valid YAML and defines the expected actions."""
    expected = {"reconfigure", "reinterview", "rejoin"}
    services_path = INTEGRATION_DIR / "services.yaml"
    services = yaml.safe_load(services_path.read_text(encoding="utf-8"))
    if not isinstance(services, dict) or not expected.issubset(services):
        missing = expected.difference(services if isinstance(services, dict) else {})
        raise ValueError(
            f"{services_path} must define services {sorted(expected)} "
            f"(missing: {sorted(missing)})"
        )


def validate_json_files() -> None:
    """Confirm strings.json and translations/en.json are valid JSON."""
    for rel in ("strings.json", "translations/en.json"):
        path = INTEGRATION_DIR / rel
        json.loads(path.read_text(encoding="utf-8"))


def _read_png_size(path: Path) -> tuple[int, int]:
    """Return the ``(width, height)`` of a PNG from its IHDR header.

    The dimensions are parsed straight from the file header so no image library
    is required. ``width`` and ``height`` are the two big-endian 32-bit integers
    that immediately follow the 16-byte signature-plus-IHDR-marker prefix.
    """
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG file")
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    return width, height


def validate_brand_icons() -> None:
    """Confirm the bundled brand icons exist with their expected sizes."""
    for name, expected in BRAND_ICONS.items():
        path = BRAND_DIR / name
        if not path.is_file():
            raise ValueError(f"missing brand icon: {path}")
        size = _read_png_size(path)
        if size != expected:
            raise ValueError(
                f"{path} must be {expected[0]}x{expected[1]} px, "
                f"got {size[0]}x{size[1]} px"
            )


def main() -> int:
    """Run all validations, returning 0 on success and 1 on failure."""
    manifest_version = read_manifest_version()
    pyproject_version = read_pyproject_version()
    init_version = read_init_version()

    versions = {
        "manifest.json": manifest_version,
        "pyproject.toml": pyproject_version,
        "__init__.py": init_version,
    }
    if len(set(versions.values())) != 1:
        print("ERROR: version mismatch across project files:")
        for source, value in versions.items():
            print(f"  {source}: {value}")
        return 1

    validate_services()
    validate_json_files()
    validate_brand_icons()

    print(f"OK: zha_tools {manifest_version} validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
