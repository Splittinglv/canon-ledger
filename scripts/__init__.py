"""
CanonLedger scripts package

This package contains the Python scripts for the CanonLedger plugin.

The plugin release version has a single source of truth:
``.cursor-plugin/plugin.json`` at the repository root.
"""

__all__ = [
    "security_utils",
    "project_locator",
    "chapter_paths",
]


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    module = importlib.import_module(f"{__name__}.{name}")
    globals()[name] = module
    return module
