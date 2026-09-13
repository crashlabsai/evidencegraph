"""Compatibility imports for durable standard-library-only lab output helpers."""

from evidencegraph.lab.runtime.runtime_io import append_json, atomic_json, save_json, utc_now

__all__ = ["append_json", "atomic_json", "save_json", "utc_now"]
