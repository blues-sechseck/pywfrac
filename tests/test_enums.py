"""The named values have to stay the wire values, not drift from them."""

import pytest

from pywfrac import AirFlow, OperationMode, WindDirectionLR, WindDirectionUD
from pywfrac.parser import (
    AIRFLOW_UNKNOWN,
    CMD_AIRFLOW_MASKS,
    CMD_MODE_MASKS,
    RCV_AIRFLOW_MASKS,
    RCV_MODE_MASKS,
)


@pytest.mark.parametrize(
    ("enum", "masks"),
    [
        pytest.param(OperationMode, CMD_MODE_MASKS, id="mode-command"),
        pytest.param(OperationMode, RCV_MODE_MASKS, id="mode-received"),
        pytest.param(AirFlow, CMD_AIRFLOW_MASKS, id="airflow-command"),
        pytest.param(AirFlow, RCV_AIRFLOW_MASKS, id="airflow-received"),
    ],
)
def test_every_wire_value_has_a_name(enum, masks):
    assert set(masks) == {member.value for member in enum}


def test_the_unknown_airflow_marker_is_not_a_named_step():
    assert AIRFLOW_UNKNOWN not in {member.value for member in AirFlow}


@pytest.mark.parametrize(
    ("enum", "count"),
    [
        pytest.param(WindDirectionUD, 5, id="vertical-auto-plus-four"),
        pytest.param(WindDirectionLR, 8, id="horizontal-auto-plus-seven"),
    ],
)
def test_the_vane_positions_run_from_auto_without_a_gap(enum, count):
    assert [member.value for member in enum] == list(range(count))
