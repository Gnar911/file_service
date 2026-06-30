import os

_here = os.path.dirname(__file__)
_src_pkg = os.path.join(_here, "src", "file_service")

# Keep repository-root subpackages (e.g. unit_test) and also expose src package modules.
if os.path.isdir(_src_pkg) and _src_pkg not in __path__:
    __path__.insert(0, _src_pkg)
