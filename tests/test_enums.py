"""The named values have to stay the wire values, not drift from them."""

import dataclasses

import pytest

from pywfrac import Aircon, AirFlow, OperationMode, WindDirectionLR, WindDirectionUD
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


@pytest.mark.parametrize(
    ("attr", "field", "enum"),
    [
        ("operation_mode", "OperationMode", OperationMode),
        ("air_flow", "AirFlow", AirFlow),
        ("wind_direction_ud", "WindDirectionUD", WindDirectionUD),
        ("wind_direction_lr", "WindDirectionLR", WindDirectionLR),
    ],
)
def test_aircon_properties_return_every_member(attr, field, enum):
    for member in enum:
        assert getattr(Aircon(**{field: member.value}), attr) is member


@pytest.mark.parametrize(
    ("attr", "field"),
    [
        ("operation_mode", "OperationMode"),
        ("air_flow", "AirFlow"),
        ("wind_direction_ud", "WindDirectionUD"),
        ("wind_direction_lr", "WindDirectionLR"),
    ],
)
def test_aircon_properties_are_none_without_a_member(attr, field):
    assert getattr(Aircon(**{field: 99}), attr) is None
    assert getattr(Aircon(**{field: -1}), attr) is None


def test_aircon_air_flow_is_none_for_the_unknown_marker():
    assert Aircon(AirFlow=AIRFLOW_UNKNOWN).air_flow is None


def test_aircon_wind_direction_lr_is_none_when_the_field_is_none():
    assert Aircon(WindDirectionLR=None).wind_direction_lr is None  # type: ignore[arg-type]


def test_aircon_properties_leave_the_dataclass_alone():
    aircon = Aircon(OperationMode=2, AirFlow=AIRFLOW_UNKNOWN)
    names = {f.name for f in dataclasses.fields(aircon)}
    assert not names & {"operation_mode", "air_flow", "wind_direction_ud", "wind_direction_lr"}
    assert aircon.OperationMode == 2
    assert dataclasses.replace(aircon, PresetTemp=20.0).operation_mode is OperationMode.HEAT
    assert dataclasses.asdict(aircon)["AirFlow"] == AIRFLOW_UNKNOWN
