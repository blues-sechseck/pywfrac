"""How many operation-data codes one request may carry, and which a unit refuses.

A module drops answers once a request holds more than ten segments, and it
refuses the whole request when it does not know one of the codes in it - the
same result as when another client holds its write lock. Pinning such a
refusal on one code takes bookkeeping per code, which is a fact about the
device and not about whoever polls it, so it lives here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from enum import StrEnum
from typing import Final

_LOGGER = logging.getLogger(__name__)

# More than ten segments in one request is where the module starts to lose
# answers (13 flutter, 14 never work). The unrequested codes 0x80 and 0x94
# that ride along do not count against it.
SERVICE_DATA_BUDGET: Final = 10

# A code is suspended after this many refusals while it was the only code in
# the request the module had not answered before: result 11 is also what a
# request gets while another client holds the write lock.
SERVICE_DATA_REFUSALS_TO_SUSPEND: Final = 2


class CodeState(StrEnum):
    """What is known about a unit's answer to one code."""

    UNTESTED = "untested"  # never asked, or never answered
    OK = "ok"  # answered at least once
    SUSPECT = "suspect"  # in a refused request without being the only unproven code
    SUSPENDED = "suspended"  # refused alone often enough to stop asking


class ServiceDataRefusals:
    """Per-code answer history of one unit, and the selection it constrains.

    A code starts untested, becomes ok when a segment for it arrives, and
    suspect when a request holding it was refused. Only a code that was the
    single unproven one in a refused request is blamed for it, and a code
    refused alone twice is suspended. Memory only; a new instance starts over.
    """

    def __init__(self) -> None:
        """Start with every code untested."""
        self._states: dict[int, CodeState] = {}
        self._refusals: dict[int, int] = {}

    def state(self, code: int) -> CodeState:
        """What is known about this code."""
        return self._states.get(code, CodeState.UNTESTED)

    def is_suspended(self, code: int) -> bool:
        """Whether this code is no longer asked for."""
        return self.state(code) is CodeState.SUSPENDED

    def has_unproven(self, codes: Iterable[int]) -> bool:
        """Whether these codes hold one the module has never answered."""
        return any(self.state(code) is not CodeState.OK for code in codes)

    def note_answered(self, code: int) -> None:
        """A segment for this code arrived: it is proven, a suspension is lifted."""
        self._states[code] = CodeState.OK
        self._refusals.pop(code, None)

    def note_refused(self, requested: Iterable[int]) -> tuple[int, ...]:
        """Pin a refused request on its unproven codes, if it had any.

        Several unproven codes all become suspects and are then asked one at a
        time; with none, nobody is blamed. Returns the codes this call
        suspended.
        """
        unproven = [c for c in requested if self.state(c) is not CodeState.OK]
        if len(unproven) > 1:
            for code in unproven:
                if not self.is_suspended(code):
                    self._states[code] = CodeState.SUSPECT
            return ()
        if not unproven or self.is_suspended(unproven[0]):
            return ()
        code = unproven[0]
        self._states[code] = CodeState.SUSPECT
        self._refusals[code] = self._refusals.get(code, 0) + 1
        if self._refusals[code] < SERVICE_DATA_REFUSALS_TO_SUSPEND:
            return ()
        self._states[code] = CodeState.SUSPENDED
        _LOGGER.debug("Operation-data code 0x%02X refused on its own twice", code)
        return (code,)

    def admit(
        self, candidates: Iterable[int], budget: int = SERVICE_DATA_BUDGET
    ) -> tuple[int, ...]:
        """Take codes from `candidates` in their order, within the constraints.

        At most `budget` codes. A suspended code never rides. A code the
        module has never answered rides only if the request holds no suspect,
        and a suspect only if it holds nothing else unproven, so a refusal
        always has one suspect. The result keeps the order of `candidates`.
        """
        chosen: list[int] = []
        untested = suspects = 0
        for code in candidates:
            if len(chosen) >= budget:
                break
            state = self.state(code)
            if state is CodeState.UNTESTED and not suspects:
                untested += 1
            elif state is CodeState.SUSPECT and not untested and not suspects:
                suspects += 1
            elif state is not CodeState.OK:
                continue
            chosen.append(code)
        return tuple(chosen)
