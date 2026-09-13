"""Compatibility name used by the unchanged Crossledger polling script."""

if __package__:
    from .client import request as request
else:
    from client import request as request  # pyright: ignore[reportMissingImports]
