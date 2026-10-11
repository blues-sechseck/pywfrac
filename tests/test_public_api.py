"""The package root exports what callers need, so they need no submodule paths."""

import pywfrac
from pywfrac import parser


def test_every_exported_name_exists():
    assert [name for name in pywfrac.__all__ if not hasattr(pywfrac, name)] == []


def test_every_operation_data_code_constant_is_exported():
    codes = {name for name in dir(parser) if name.startswith("SERVICE_DATA_")}
    assert codes - set(pywfrac.__all__) == set()
