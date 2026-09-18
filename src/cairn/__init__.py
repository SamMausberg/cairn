"""CAIRN: an inspectable native compiler and agent editing toolkit."""
from .version import __version__
from .cairnc import compile_source, Diagnostic
__all__ = ["__version__", "compile_source", "Diagnostic"]
