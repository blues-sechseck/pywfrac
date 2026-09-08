"""pywfrac - async client library for Mitsubishi Heavy Industries WF-RAC modules.

Talks the local HTTP(S) API of the WF-RAC/WCBN4612L WiFi adapter that ships
with several MHI split-system air conditioners, and decodes/encodes the
airconStat protocol carried over it. Extracted from the
`mitsubishi_wf_rac` Home Assistant integration
(https://github.com/blues-sechseck/Mitsubishi-WF-RAC-Integration), which
remains the reference for the protocol's field notes.
"""

from .capabilities import ModelCapabilities, get_capabilities
from .error_codes import describe_error_code
from .models.aircon import Aircon, AirconCommands, AirconStat, HomeLeaveModeSetting
from .parser import AIRFLOW_UNKNOWN, RacParser
from .repository import (
    READ_RESULT_CODES,
    RESULT_CODES,
    Repository,
    WfRacCommandError,
    WfRacConnectionError,
    WfRacError,
    WfRacRegistrationError,
    WfRacWriteRefusedError,
    describe_result,
)

__all__ = [
    "Repository",
    "WfRacError",
    "WfRacCommandError",
    "WfRacRegistrationError",
    "WfRacWriteRefusedError",
    "WfRacConnectionError",
    "RESULT_CODES",
    "READ_RESULT_CODES",
    "describe_result",
    "RacParser",
    "AIRFLOW_UNKNOWN",
    "Aircon",
    "AirconStat",
    "AirconCommands",
    "HomeLeaveModeSetting",
    "ModelCapabilities",
    "get_capabilities",
    "describe_error_code",
]
