"""Unit tests for wfrac/capabilities.py - the model_no_type table.

Ground truth is the app's own `res/values/arrays.xml`
(`model_no_type_function_*`) and `model/ModelNoType.java` (table selection,
flag order) - see capabilities.py's own docstring.
"""

import pytest

from pywfrac import OperationMode, RacParser
from pywfrac.capabilities import get_capabilities

from .live_captures import LIVE_CAPTURES


def test_raw_0_falls_back_to_separate_2021():
    caps = get_capabilities(0)
    assert caps.power_consumption is True
    assert caps.vacant_property is False
    assert caps.operation_data is False


def test_raw_1_is_global_2022():
    caps = get_capabilities(1)
    assert caps.vacant_property is True
    assert caps.home_leave_mode is True
    assert caps.self_clean_operation is True


def test_raw_2_is_high_end_for_japanese_2023():
    caps = get_capabilities(2)
    assert caps.vacant_property is False
    assert caps.call_center_phone_number is True


def test_raw_3_is_zt_2025_and_keeps_vacant_property():
    # The #187 regression this table fixes: raw=3 previously collapsed into
    # the ModelNr=2 wire-protocol bucket (see rac_parser.py), which cost it
    # VacantProperty even though the app's own zt_2025 table grants it.
    caps = get_capabilities(3)
    assert caps.vacant_property is True
    assert caps.preset_temp_range_2 is True
    assert caps.operation_data is True


def test_raw_64_is_fdt_2023():
    caps = get_capabilities(64)
    assert caps.led_light is True
    assert caps.preset_temp_auto is True
    assert caps.vacant_property is False


def test_unrecognized_raw_falls_back_to_separate_2021():
    caps = get_capabilities(99)
    assert caps == get_capabilities(0)


_PLAIN = {
    "AUTO": (18, 30),
    "COOL": (16, 30),
    "HEAT": (18, 30),
    "FAN": (18, 30),
    "DRY": (18, 30),
}
_RANGE_2 = {
    "AUTO": (16, 30),
    "COOL": (16, 33),
    "HEAT": (10, 30),
    "FAN": (18, 30),
    "DRY": (16, 33),
}


@pytest.mark.parametrize(
    ("raw_model", "expected"),
    # zt_2025 (3) is the only preset_temp_range_2 table
    [(0, _PLAIN), (1, _PLAIN), (2, _PLAIN), (64, _PLAIN), (3, _RANGE_2)],
)
def test_setpoint_range_per_mode(raw_model, expected):
    caps = get_capabilities(raw_model)
    for name, bounds in expected.items():
        assert caps.setpoint_range(OperationMode[name]) == bounds


def test_setpoint_range_accepts_the_raw_mode_an_aircon_reports():
    aircon = RacParser().translate_bytes(LIVE_CAPTURES["on_heat"][0])

    assert aircon.OperationMode == OperationMode.HEAT
    assert aircon.Capabilities.setpoint_range(aircon.OperationMode) == (18, 30)
