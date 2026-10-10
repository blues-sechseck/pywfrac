"""What one getAirconStat answer carries besides the state block."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .aircon import Aircon

_UNKNOWN = "unknown"


def _revision(value: Any) -> str:
    return str(value) if value else _UNKNOWN


def _section_revision(section: Any) -> str:
    if not isinstance(section, Mapping):
        return _UNKNOWN
    return _revision(section.get("firmVer"))


@dataclass(frozen=True)
class FirmwareInfo:
    """Firmware strings of a status answer; "unknown" where a revision sent none."""

    firm_type: str = _UNKNOWN
    mcu: str = _UNKNOWN
    wireless: str = _UNKNOWN

    @classmethod
    def from_contents(cls, contents: Mapping[str, Any]) -> "FirmwareInfo":
        """Read the three strings; revisions differ in which sub-keys they send."""
        return cls(
            firm_type=_revision(contents.get("firmType")),
            mcu=_section_revision(contents.get("mcu")),
            wireless=_section_revision(contents.get("wireless")),
        )

    def __str__(self) -> str:
        return f"{self.firm_type}, mcu: {self.mcu}, wireless: {self.wireless}"


@dataclass
class AirconStatus:
    """One decoded getAirconStat answer."""

    aircon: Aircon
    firmware: FirmwareInfo
    # Epoch second at which the write lock held by whoever wrote last lapses.
    expires: int | None = None
