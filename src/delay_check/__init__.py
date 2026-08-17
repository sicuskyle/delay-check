from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("delay-check")
except PackageNotFoundError:
    __version__ = "0.0.0+dev"

__author__ = "Sicuskyle"
__license__ = "MIT"
