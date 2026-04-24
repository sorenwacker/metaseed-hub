"""Metaseed Hub - Collaborative hub for metaseed projects."""

try:
    from metaseed_hub._version import version as __version__
except ImportError:
    # Package not installed, use fallback
    __version__ = "0.0.0.dev0"
