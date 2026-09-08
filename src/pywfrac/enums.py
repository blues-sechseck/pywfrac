"""Named values for the airconStat fields that carry a small integer.

The wire numbers themselves are the library's own domain: CMD_MODE_MASKS and
RCV_AIRFLOW_MASKS in parser.py are keyed by exactly these values. Callers used
to recover a name by indexing their own ordered dict with the raw integer,
which holds only while the protocol numbers a field contiguously from zero -
the first gap turns into a silently wrong answer instead of an error.
"""

from enum import IntEnum


class OperationMode(IntEnum):
    """airconStat OperationMode."""

    AUTO = 0
    COOL = 1
    HEAT = 2
    FAN = 3
    DRY = 4


class AirFlow(IntEnum):
    """airconStat AirFlow - auto plus four steps.

    QUIET is the lowest step, not the remote's ECO: no ECO value exists
    anywhere in this protocol.
    """

    AUTO = 0
    QUIET = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4


class WindDirectionUD(IntEnum):
    """airconStat WindDirectionUD - the vertical vane, highest to lowest."""

    AUTO = 0
    POSITION_1 = 1
    POSITION_2 = 2
    POSITION_3 = 3
    POSITION_4 = 4


class WindDirectionLR(IntEnum):
    """airconStat WindDirectionLR - the horizontal vane, left to right.

    Only fitted on some models; ModelCapabilities.wind_direction_lr says
    which.
    """

    AUTO = 0
    POSITION_1 = 1
    POSITION_2 = 2
    POSITION_3 = 3
    POSITION_4 = 4
    POSITION_5 = 5
    POSITION_6 = 6
    POSITION_7 = 7
