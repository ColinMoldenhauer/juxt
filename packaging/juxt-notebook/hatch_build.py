"""Build hook: package ../../juxt into the juxt-notebook wheel.

A plain force-include of the directory would also pick up whatever happens to
be lying in a working tree (``__pycache__``, the main build's ``_version.py``),
so files are chosen explicitly here: tracked source types only, and the
version file generated for *this* build.
"""
from __future__ import annotations

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

SUFFIXES = {".py", ".js", ".css", ".png"}
SKIP = {"_version.py"}


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        src = (root / "../../juxt").resolve()
        if not (src / "__init__.py").is_file():
            raise RuntimeError(f"juxt sources not found at {src}")
        for path in sorted(src.rglob("*")):
            rel = path.relative_to(src)
            if (path.is_file() and path.suffix in SUFFIXES and path.name not in SKIP
                    and "__pycache__" not in rel.parts):
                build_data["force_include"][str(path)] = f"juxt/{rel.as_posix()}"
        # same logos the main wheel adds from docs/assets
        for logo in ("logo_large_transparent.png", "logo_transparent.png"):
            build_data["force_include"][str((root / "../../docs/assets" / logo).resolve())] = f"juxt/assets/{logo}"
        build_data["force_include"][str(root / "_version.py")] = "juxt/_version.py"
