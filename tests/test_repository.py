"""Tests for repository.py: how _post() classifies failures and what that
classification does to the discovered communication method. The aiohttp
session is replaced with a fake - no real network involved.
"""

import json
import ssl
from datetime import timedelta
from unittest.mock import patch

import pytest
from aiohttp import ClientConnectionError, ClientPayloadError

from pywfrac import RacParser
from pywfrac.models.aircon import AirconCommands, AirconStat
from pywfrac.models.status import AirconStatus, FirmwareInfo
from pywfrac.repository import (
    MIN_TIME_BETWEEN_REQUESTS,
    RESULT_CODES,
    WRITE_LOCK_MAX_WAIT,
    WRITE_LOCK_RETRY_DELAY,
    Repository,
    WfRacAccountTableFullError,
    WfRacCommandError,
    WfRacConnectionError,
    WfRacError,
    WfRacMalformedResponseError,
    WfRacRegistrationError,
    WfRacWriteRefusedError,
)

from .live_captures import LIVE_CAPTURES

_STAT = LIVE_CAPTURES["on_cool"][0]
_OK_BODY = json.dumps({"result": 0, "contents": {"airconId": "airco-id"}})


class _FakeResponse:
    """A body may be given as text or, where that is the point of the test, as
    the raw bytes the module put on the wire.
    """

    def __init__(self, status: int, body: str | bytes) -> None:
        self.status = status
        self.content_type = "application/json"
        self._body = body.encode() if isinstance(body, str) else body

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False


class _FakeSession:
    """Answers every post with the next queued outcome, recording the URLs so
    a test can tell which protocol was attempted.
    """

    def __init__(self, outcomes) -> None:
        self._outcomes = list(outcomes)
        self.urls: list[str] = []

    def post(self, url: str, **_kwargs):
        self.urls.append(url)
        outcome = self._outcomes.pop(0) if self._outcomes else _OK_BODY
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def repository():
    def _build(outcomes, method="http"):
        session = _FakeSession(outcomes)
        repo = Repository(session, "127.0.0.1", 51443, "operator-id", "device-id", method=method)
        return repo, session

    return _build


async def test_http_error_status_raises_command_error(repository):
    repo, _ = repository([_FakeResponse(501, "Not supported this command")])
    with pytest.raises(WfRacCommandError):
        await repo.get_aircon_stats("airco-id")


async def test_connection_failure_raises_connection_error(repository):
    cause = ClientConnectionError("connection refused")
    repo, _ = repository([cause, cause])
    with pytest.raises(WfRacConnectionError) as error:
        await repo.get_aircon_stats("airco-id")
    assert error.value.__cause__ is cause


async def test_timeout_raises_connection_error(repository):
    cause = TimeoutError()
    repo, _ = repository([cause, cause])
    with pytest.raises(WfRacConnectionError) as error:
        await repo.get_aircon_stats("airco-id")
    assert error.value.__cause__ is cause


# The module garbles its own answers: a body that was valid ASCII for its first
# hundred-odd bytes and then carried 0xd3 was reported from the field (#373),
# and overrunning the request trailer makes one serialise binary outright.
# Neither is a refusal, and neither must escape as a plain ValueError - that
# skipped every caller's retry handling and took the unit straight offline.
_GARBLED_BODY = (
    b'{"result":0,"airconStat":"AACqj6r/AAAIAAAUigAAAAAAAf////9hp4\xd3EECAcqmg==","numOfAccount":1}'
)


async def test_a_body_that_is_not_utf8_is_a_connection_failure(repository):
    repo, _ = repository([_FakeResponse(200, _GARBLED_BODY)])

    with pytest.raises(WfRacMalformedResponseError) as error:
        await repo.get_aircon_stats("airco-id")

    assert isinstance(error.value, WfRacConnectionError)
    assert isinstance(error.value.__cause__, UnicodeDecodeError)


async def test_a_body_that_is_not_json_is_a_connection_failure(repository):
    repo, _ = repository([_FakeResponse(200, '{"result":0,"airconStat":"AACq')])

    with pytest.raises(WfRacMalformedResponseError) as error:
        await repo.get_aircon_stats("airco-id")

    assert isinstance(error.value, WfRacConnectionError)
    assert isinstance(error.value.__cause__, json.JSONDecodeError)


async def test_a_garbled_body_is_reported_with_its_bytes(repository):
    """The bytes are the whole diagnosis, and this is the only place they
    survive: the debug log is off in every installation that has not gone
    looking for a fault yet.
    """
    repo, _ = repository([_FakeResponse(200, _GARBLED_BODY)])

    with pytest.raises(WfRacMalformedResponseError) as error:
        await repo.get_aircon_stats("airco-id")

    assert "\\xd3" in str(error.value)
    assert "numOfAccount" in str(error.value)


async def test_a_garbled_body_keeps_the_discovered_method(repository):
    """The unit answered over this protocol, so there is nothing to rediscover
    - unlike a transport outage, which may mean the firmware changed branch.
    """
    repo, session = repository([_FakeResponse(200, _GARBLED_BODY), _FakeResponse(200, _OK_BODY)])

    with pytest.raises(WfRacMalformedResponseError):
        await repo.get_aircon_stats("airco-id")
    assert repo.method == "http"

    await repo.get_aircon_stats("airco-id")
    assert session.urls == [
        "http://127.0.0.1:51443/beaver/command/getAirconStat",
        "http://127.0.0.1:51443/beaver/command/getAirconStat",
    ]


async def test_a_truncated_transfer_is_a_connection_failure(repository):
    """ClientPayloadError is a sibling of the connection errors, not one of
    them, so it needs catching in its own right.
    """
    cause = ClientPayloadError("response payload is not completed")
    repo, _ = repository([cause, cause])

    with pytest.raises(WfRacConnectionError) as error:
        await repo.get_aircon_stats("airco-id")

    assert error.value.__cause__ is cause


async def test_a_failed_request_still_arms_the_throttle(repository):
    """What the module rations is connections, not answers. Firing the next
    request early because this one broke is how one failure becomes a run of
    them.
    """
    repo, _ = repository([_FakeResponse(501, "Not supported this command")])
    before = repo._next_request_after

    with pytest.raises(WfRacCommandError):
        await repo.get_aircon_stats("airco-id")

    assert repo._next_request_after > before


async def test_refused_command_keeps_the_discovered_method(repository):
    """A 501 means the unit answered - the stored method is still correct, so
    the next request must not pay for a rediscovery.
    """
    repo, session = repository(
        [_FakeResponse(501, "Not supported this command"), _FakeResponse(200, _OK_BODY)]
    )

    with pytest.raises(WfRacCommandError):
        await repo.get_aircon_stats("airco-id")
    assert repo.method == "http"

    await repo.get_aircon_stats("airco-id")
    assert session.urls == [
        "http://127.0.0.1:51443/beaver/command/getAirconStat",
        "http://127.0.0.1:51443/beaver/command/getAirconStat",
    ]


async def test_a_failed_unit_is_retried_on_the_last_working_method_first(repository):
    """Recovery must not put an HTTPS unit behind an HTTP-first timeout."""
    repo, session = repository(
        [
            ClientConnectionError("boom"),
            ClientConnectionError("boom"),
            _FakeResponse(200, _OK_BODY),
        ],
        method="https",
    )
    repo._ssl_context = ssl.create_default_context()

    with pytest.raises(WfRacConnectionError):
        await repo.get_aircon_stats("airco-id")
    assert repo.method is None

    await repo.get_aircon_stats("airco-id")
    assert repo.method == "https"
    assert session.urls == [
        "https://127.0.0.1:51443/beaver/command/getAirconStat",
        "http://127.0.0.1:51443/beaver/command/getAirconStat",
        "https://127.0.0.1:51443/beaver/command/getAirconStat",
    ]


async def test_a_stored_method_that_works_costs_one_request(repository):
    repo, session = repository([_FakeResponse(200, _OK_BODY)] * 2, method="http")

    await repo.get_aircon_stats("airco-id")
    await repo.get_aircon_stats("airco-id")

    assert len(session.urls) == 2
    assert repo.method == "http"


async def test_a_stored_method_is_not_logged_as_discovered(repository, caplog):
    caplog.set_level("INFO", logger="pywfrac.repository")
    repo, _ = repository([_FakeResponse(200, _OK_BODY)], method="http")

    await repo.get_aircon_stats("airco-id")

    assert not [r for r in caplog.records if "Discovered" in r.message]


async def test_both_methods_failing_raises_the_stored_methods_error(repository):
    first = ClientConnectionError("stored method unreachable")
    repo, _ = repository([first, ClientConnectionError("other")], method="http")

    with pytest.raises(WfRacConnectionError) as error:
        await repo.get_aircon_stats("airco-id")

    assert error.value.__cause__ is first


@pytest.mark.parametrize(("old_method", "new_method"), (("http", "https"), ("https", "http")))
async def test_a_stored_method_falls_back_within_the_same_call(repository, old_method, new_method):
    """A firmware line that changes protocol must not cost a failed request."""
    repo, session = repository(
        [ClientConnectionError("old protocol refused"), _FakeResponse(200, _OK_BODY)],
        method=old_method,
    )
    repo._ssl_context = ssl.create_default_context()

    await repo.get_aircon_stats("airco-id")

    assert repo.method == new_method
    assert session.urls == [
        f"{old_method}://127.0.0.1:51443/beaver/command/getAirconStat",
        f"{new_method}://127.0.0.1:51443/beaver/command/getAirconStat",
    ]


async def test_the_other_protocol_waits_its_turn(repository):
    """The fallback is a second connection, spaced like any other."""
    repo, _ = repository(
        [ClientConnectionError("old protocol refused"), _FakeResponse(200, _OK_BODY)],
        method="http",
    )
    repo._ssl_context = ssl.create_default_context()
    waits: list[float] = []

    async def _sleep(seconds: float) -> None:
        waits.append(seconds)

    with patch("pywfrac.repository.asyncio.sleep", _sleep):
        await repo.get_aircon_stats("airco-id")

    assert MIN_TIME_BETWEEN_REQUESTS.total_seconds() in waits


@pytest.mark.parametrize("outcome", [_FakeResponse(501, "no"), _FakeResponse(200, _GARBLED_BODY)])
async def test_a_stored_method_that_answered_is_not_second_guessed(repository, outcome):
    repo, session = repository([outcome], method="http")

    with pytest.raises(WfRacError):
        await repo.get_aircon_stats("airco-id")

    assert len(session.urls) == 1
    assert repo.method == "http"


async def test_discovery_falls_back_to_https_on_a_command_error(repository):
    """An HTTPS-only module can answer a plaintext request with a status code
    rather than dropping the connection; discovery still has to try HTTPS.
    """
    repo, session = repository(
        [_FakeResponse(400, "bad request"), _FakeResponse(200, _OK_BODY)], method=None
    )

    await repo.get_aircon_stats("airco-id")

    assert repo.method == "https"
    assert session.urls == [
        "http://127.0.0.1:51443/beaver/command/getAirconStat",
        "https://127.0.0.1:51443/beaver/command/getAirconStat",
    ]


async def test_refusal_in_the_result_field_is_reported_once(repository, caplog):
    """HTTP 200 with a non-zero result is a request the unit accepted and did
    not carry out - invisible until now (#212). Reported, but not acted on:
    which firmware reports what on success is not established.
    """
    caplog.set_level("DEBUG", logger="pywfrac.repository")
    refused = json.dumps({"result": 12, "contents": {"airconStat": "AAA="}})
    repo, _ = repository(
        [
            _FakeResponse(200, refused),
            _FakeResponse(200, refused),
            _FakeResponse(200, _OK_BODY),
            _FakeResponse(200, refused),
        ]
    )

    def _reports():
        return [r for r in caplog.records if "was accepted but not carried out" in r.message]

    # The caller still gets the response: nothing about the control flow moves.
    assert await repo.get_aircon_stats("airco-id") == {"airconStat": "AAA="}
    await repo.get_aircon_stats("airco-id")

    assert len(_reports()) == 1
    assert "result 12 (refused - another client holds the write lock" in _reports()[0].message
    # Debug, not warning: this layer cannot tell whether the refusal mattered,
    # and the common ones clear by themselves. See _report_result_code.
    assert {r.levelname for r in _reports()} == {"DEBUG"}

    # A success clears it, so a later refusal is worth saying again.
    await repo.get_aircon_stats("airco-id")
    await repo.get_aircon_stats("airco-id")

    assert len(_reports()) == 2

    # Every refusal is counted even though only two were logged - this is what
    # a diagnostics download carries in place of the log lines.
    assert repo.result_codes == {"getAirconStat": {"12": 3}}


async def test_send_airco_command_raises_on_registration_result_code(repository):
    """Unlike getAirconStat (asserted above), setAirconStat refusing with
    result 2 must be visible to the caller rather than swallowed - a
    coordinator relies on this to re-register and retry instead of losing the
    command (#294).
    """
    refused = json.dumps({"result": 2, "contents": {"airconStat": "AAA="}})
    repo, _ = repository([_FakeResponse(200, refused)])

    with pytest.raises(WfRacRegistrationError):
        await repo.send_airco_command("airco-id", "cmd")


@pytest.mark.parametrize("code", [1, 11, 12])
async def test_send_airco_command_raises_on_write_refusal_codes(repository, code):
    """1/11/12 mean the unit declined to carry the write out - usually
    another client's 60s write lock (#294). They must reach the caller as
    their own error type: waiting and retrying helps, re-registering does
    not.
    """
    refused = json.dumps({"result": code, "contents": {"airconStat": "AAA="}})
    repo, _ = repository([_FakeResponse(200, refused)])

    with pytest.raises(WfRacWriteRefusedError):
        await repo.send_airco_command("airco-id", "cmd")

    # ...and not as the registration error, which would send a caller down
    # the pointless re-register path.
    assert not issubclass(WfRacWriteRefusedError, WfRacRegistrationError)


async def test_send_airco_command_does_not_raise_on_unrelated_result_code(repository):
    """Result 10 is an internal error in the unit, not something either
    recovery path can act on - just the existing logged-but-not-acted-on
    refusal.
    """
    refused = json.dumps({"result": 10, "contents": {"airconStat": "AAA="}})
    repo, _ = repository([_FakeResponse(200, refused)])

    assert await repo.send_airco_command("airco-id", "cmd") == "AAA="


async def test_unknown_result_code_is_still_reported(repository, caplog):
    caplog.set_level("DEBUG", logger="pywfrac.repository")
    repo, _ = repository([_FakeResponse(200, json.dumps({"result": 77, "contents": {}}))])

    await repo.get_aircon_stats("airco-id")

    reports = [r for r in caplog.records if "was accepted but not carried out" in r.message]
    assert len(reports) == 1
    assert "result 77 (meaning unknown)" in reports[0].message


async def test_read_refusal_does_not_blame_the_write_lock(repository, caplog):
    """getAirconStat touches neither the write lock nor the account table, so
    its result 1 must not be described with the setAirconStat wording - that
    reading sent a tester chasing a lock that was never involved (#294).
    """
    caplog.set_level("DEBUG", logger="pywfrac.repository")
    repo, _ = repository([_FakeResponse(200, json.dumps({"result": 1, "contents": {}}))])

    await repo.get_aircon_stats("airco-id")

    reports = [r for r in caplog.records if "was accepted but not carried out" in r.message]
    assert len(reports) == 1
    assert "no fresh data from the indoor unit" in reports[0].message
    assert "write lock" not in reports[0].message


async def test_timestamp_offset_backdates_the_request_stamp(repository):
    """The module reads its clock from the request's `timestamp` and locks
    until timestamp + 60. send_airco_command(timestamp_offset=...) shifts that
    field so an operation-data request can give up part of the lock; every
    other request leaves it at 0.
    """
    ok = json.dumps({"result": 0, "contents": {"airconStat": "AAA="}})
    repo, session = repository([_FakeResponse(200, ok), _FakeResponse(200, ok)])
    session.bodies = []
    original_post = session.post

    def _recording_post(url, **kwargs):
        session.bodies.append(kwargs.get("json"))
        return original_post(url, **kwargs)

    session.post = _recording_post

    with patch("pywfrac.repository.time.time", return_value=1_000_000.0):
        await repo.send_airco_command("airco-id", "cmd")
        await repo.send_airco_command("airco-id", "cmd", timestamp_offset=-30)

    assert session.bodies[0]["timestamp"] == 1_000_000
    assert session.bodies[1]["timestamp"] == 1_000_000 - 30


async def test_get_info_returns_the_contents(repository):
    repo, _ = repository(
        [_FakeResponse(200, json.dumps({"result": 0, "contents": {"airconId": "x"}}))]
    )
    assert await repo.get_info() == {"airconId": "x"}


async def test_get_airco_id_reads_it_from_get_info(repository):
    repo, _ = repository(
        [_FakeResponse(200, json.dumps({"result": 0, "contents": {"airconId": "abc123"}}))]
    )
    assert await repo.get_airco_id() == "abc123"


async def test_update_account_info_sends_the_expected_contents(repository):
    repo, session = repository([_FakeResponse(200, _OK_BODY)])
    session.bodies = []
    original_post = session.post

    def _recording_post(url, **kwargs):
        session.bodies.append(kwargs.get("json"))
        return original_post(url, **kwargs)

    session.post = _recording_post

    await repo.update_account_info("airco-id", time_zone="Europe/Berlin")

    assert session.bodies[0]["contents"] == {
        "accountId": "operator-id",
        "airconId": "airco-id",
        "remote": 0,
        "timezone": "Europe/Berlin",
    }


async def test_del_account_info_sends_the_expected_contents(repository):
    repo, session = repository([_FakeResponse(200, _OK_BODY)])
    session.bodies = []
    original_post = session.post

    def _recording_post(url, **kwargs):
        session.bodies.append(kwargs.get("json"))
        return original_post(url, **kwargs)

    session.post = _recording_post

    await repo.del_account_info("airco-id")

    assert session.bodies[0]["contents"] == {
        "accountId": "operator-id",
        "airconId": "airco-id",
    }


async def test_ssl_context_uses_the_certificate_file_when_present(repository, tmp_path):
    """cert_path is the decoupled replacement for hass.config.path("ac_cert.pem")
    - a real cert file must produce a verifying context, not the permissive
    fallback.
    """
    cert_path = tmp_path / "ac_cert.pem"
    cert_path.write_text("-----BEGIN CERTIFICATE-----\nMA==\n-----END CERTIFICATE-----\n")
    session = _FakeSession([])
    repo = Repository(
        session,
        "127.0.0.1",
        51443,
        "operator-id",
        "device-id",
        cert_path=str(cert_path),
    )

    # An invalid cert body makes ssl.create_default_context raise - which is
    # itself proof the cert_path branch (not the permissive fallback) ran.
    with pytest.raises(ssl.SSLError):
        await repo._get_ssl_context()


async def test_ssl_context_falls_back_when_no_cert_path_given(repository):
    session = _FakeSession([])
    repo = Repository(session, "127.0.0.1", 51443, "operator-id", "device-id")

    context = await repo._get_ssl_context()

    assert context.verify_mode == ssl.CERT_NONE


@pytest.mark.parametrize("body", ["null", "[]", '"text"', "12", "true"])
async def test_a_body_that_is_not_an_object_is_malformed(repository, body):
    """Valid JSON of another shape used to escape as AttributeError from the
    result-code bookkeeping, past every caller's error handling.
    """
    repo, _ = repository([_FakeResponse(200, body)])

    with pytest.raises(WfRacMalformedResponseError, match="not an object"):
        await repo.get_aircon_stats("airco-id")


def _status_body(**contents):
    return json.dumps({"result": 0, "contents": {"airconStat": _STAT, **contents}})


async def test_get_status_decodes_state_firmware_and_deadline(repository):
    repo, session = repository(
        [
            _FakeResponse(
                200,
                _status_body(
                    firmType="WF-RAC",
                    mcu={"firmVer": "010"},
                    wireless={"firmVer": "131"},
                    expires=1_700_000_060,
                ),
            )
        ]
    )

    status = await repo.async_get_status("airco-id")

    assert isinstance(status, AirconStatus)
    assert status.aircon.Operation is True
    assert status.aircon.PresetTemp == 26.0
    assert status.firmware == FirmwareInfo("WF-RAC", "010", "131")
    assert str(status.firmware) == "WF-RAC, mcu: 010, wireless: 131"
    assert status.expires == 1_700_000_060
    assert session.urls == ["http://127.0.0.1:51443/beaver/command/getAirconStat"]


async def test_get_status_sends_the_airco_id(repository):
    repo, session = repository([_FakeResponse(200, _status_body())])
    sent = []
    original_post = session.post

    def _recording_post(url, **kwargs):
        sent.append(kwargs["json"])
        return original_post(url, **kwargs)

    session.post = _recording_post

    await repo.async_get_status("airco-id")

    assert sent[0]["contents"] == {"airconId": "airco-id"}


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"firmType": "", "mcu": "010", "wireless": None},
        {"firmType": None, "mcu": [], "wireless": {"firmVer": ""}},
        {"mcu": {"other": 1}, "wireless": 3},
    ],
)
async def test_get_status_reports_missing_firmware_as_unknown(repository, extra):
    repo, _ = repository([_FakeResponse(200, _status_body(**extra))])

    status = await repo.async_get_status("airco-id")

    assert status.firmware == FirmwareInfo()
    assert str(status.firmware) == "unknown, mcu: unknown, wireless: unknown"


@pytest.mark.parametrize("expires", [None, "1700000060", 1.5, True, [1]])
async def test_get_status_keeps_only_an_integer_deadline(repository, expires):
    repo, _ = repository([_FakeResponse(200, _status_body(expires=expires))])

    assert (await repo.async_get_status("airco-id")).expires is None


async def test_get_status_never_blames_the_account(repository):
    """Reads are not account-checked, so result 2 without state is no eviction."""
    repo, _ = repository([_FakeResponse(200, json.dumps({"result": 2}))])

    with pytest.raises(WfRacCommandError) as error:
        await repo.async_get_status("airco-id")

    assert not isinstance(error.value, WfRacRegistrationError)


async def test_get_status_result_2_with_state_still_succeeds(repository):
    body = json.dumps({"result": 2, "contents": {"airconStat": _STAT}})
    repo, _ = repository([_FakeResponse(200, body)])

    assert (await repo.async_get_status("airco-id")).aircon.Operation is True


async def test_get_status_maps_other_results_without_state_to_command_error(repository):
    repo, _ = repository([_FakeResponse(200, json.dumps({"result": 1, "contents": {}}))])

    with pytest.raises(WfRacCommandError, match="no fresh data") as error:
        await repo.async_get_status("airco-id")

    assert not isinstance(error.value, WfRacRegistrationError)


async def test_get_status_http_error_is_a_command_error(repository):
    repo, _ = repository([_FakeResponse(400, "bad request")])

    with pytest.raises(WfRacCommandError):
        await repo.async_get_status("airco-id")


async def test_get_status_transport_failure_is_a_connection_error(repository):
    repo, _ = repository([ClientConnectionError("boom")] * 2)

    with pytest.raises(WfRacConnectionError):
        await repo.async_get_status("airco-id")


@pytest.mark.parametrize(
    "body",
    [
        json.dumps({"result": 0}),
        json.dumps({"result": 0, "contents": "x"}),
        json.dumps({"result": 0, "contents": {}}),
        json.dumps({"result": 0, "contents": {"airconStat": None}}),
        json.dumps({"result": 0, "contents": {"airconStat": 5}}),
        json.dumps({"result": 0, "contents": {"airconStat": "!!notbase64"}}),
        json.dumps({"result": 0, "contents": {"airconStat": "AAAA"}}),
        "[]",
        "null",
    ],
)
async def test_get_status_malformed_answers_are_typed(repository, body):
    repo, _ = repository([_FakeResponse(200, body)])

    with pytest.raises(WfRacMalformedResponseError):
        await repo.async_get_status("airco-id")


def _result(code):
    return _FakeResponse(200, json.dumps({"result": code}))


async def test_register_accepts_result_0(repository):
    repo, _ = repository([_result(0)])

    assert await repo.async_register("airco-id", "Europe/Berlin") is None


async def test_register_result_2_is_a_full_account_table(repository):
    repo, _ = repository([_result(2)])

    with pytest.raises(WfRacAccountTableFullError) as error:
        await repo.async_register("airco-id", "Europe/Berlin")

    assert isinstance(error.value, WfRacCommandError)


@pytest.mark.parametrize("code", [1, 10, 11, 12, 20, 99, 429])
async def test_register_names_every_other_known_refusal(repository, code):
    repo, _ = repository([_result(code)])

    with pytest.raises(WfRacCommandError, match=f"result {code}") as error:
        await repo.async_register("airco-id", "Europe/Berlin")

    assert not isinstance(error.value, WfRacAccountTableFullError)
    assert RESULT_CODES[code] in str(error.value)


async def test_register_lets_an_unknown_code_through_with_a_warning(repository, caplog):
    caplog.set_level("WARNING", logger="pywfrac.repository")
    repo, _ = repository([_result(77)])

    await repo.async_register("airco-id", "Europe/Berlin")

    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert "result 77" in caplog.records[0].message


@pytest.mark.parametrize(
    "body", ["{}", '{"result": null}', '{"result": "x"}', "[]", '{"result": []}']
)
async def test_register_without_a_readable_result_is_malformed(repository, body):
    repo, _ = repository([_FakeResponse(200, body)])

    with pytest.raises(WfRacMalformedResponseError):
        await repo.async_register("airco-id", "Europe/Berlin")


async def test_register_propagates_transport_errors(repository):
    repo, _ = repository([ClientConnectionError("boom")] * 2)

    with pytest.raises(WfRacConnectionError):
        await repo.async_register("airco-id", "Europe/Berlin")


async def test_unregister_is_true_only_on_result_0(repository):
    repo, _ = repository([_result(0)])
    assert await repo.async_unregister("airco-id") is True


@pytest.mark.parametrize("body", [_result(2), _result(12), _result(77), "{}"])
async def test_unregister_is_false_on_any_other_answer(repository, body):
    if isinstance(body, str):
        body = _FakeResponse(200, body)
    repo, _ = repository([body])

    assert await repo.async_unregister("airco-id") is False


@pytest.mark.parametrize(
    "outcome",
    [ClientConnectionError("boom"), _FakeResponse(400, "bad"), _FakeResponse(200, "[]")],
)
async def test_unregister_propagates_errors(repository, outcome):
    repo, _ = repository([outcome, outcome])

    with pytest.raises(WfRacError):
        await repo.async_unregister("airco-id")


# --- async_send_command -------------------------------------------------

_HEAT_STAT = LIVE_CAPTURES["on_heat"][0]


def _recorded(session):
    """Record every request body the session was asked to post."""
    session.bodies = []
    original_post = session.post

    def _recording_post(url, **kwargs):
        session.bodies.append(kwargs.get("json"))
        return original_post(url, **kwargs)

    session.post = _recording_post
    return session.bodies


def _set_answer(code=0, stat=_STAT):
    return _FakeResponse(200, json.dumps({"result": code, "contents": {"airconStat": stat}}))


def _frame(base_stat, **params):
    parser = RacParser()
    stat = AirconStat.from_aircon(parser.translate_bytes(base_stat))
    for key, value in params.items():
        setattr(stat, key, value)
    return parser.to_base64(stat)


@pytest.fixture
def sleeps():
    """Replace the lock wait, and drop the request throttle so it records only that."""
    recorded: list[float] = []

    async def _sleep(delay):
        recorded.append(delay)

    with (
        patch("pywfrac.repository.asyncio.sleep", _sleep),
        patch("pywfrac.repository.MIN_TIME_BETWEEN_REQUESTS", timedelta(0)),
    ):
        yield recorded


async def test_send_command_encodes_the_full_block_and_returns_the_answer(repository, sleeps):
    repo, session = repository([_set_answer(0, _HEAT_STAT)])
    bodies = _recorded(session)
    base = RacParser().translate_bytes(_STAT)

    result = await repo.async_send_command("airco-id", base, {AirconCommands.PresetTemp: 22.5})

    assert bodies[0]["command"] == "setAirconStat"
    assert bodies[0]["contents"] == {
        "airconId": "airco-id",
        "airconStat": _frame(_STAT, PresetTemp=22.5),
    }
    assert result == RacParser().translate_bytes(_HEAT_STAT)
    assert sleeps == []


@pytest.mark.parametrize("code", [10, 20, 99, 429])
async def test_send_command_treats_known_failure_codes_as_command_errors(repository, sleeps, code):
    repo, _ = repository([_set_answer(code)])

    with pytest.raises(WfRacCommandError) as error:
        await repo.async_send_command("airco-id", RacParser().translate_bytes(_STAT), {})

    assert not isinstance(error.value, (WfRacWriteRefusedError, WfRacRegistrationError))
    assert RESULT_CODES[code] in str(error.value)


async def test_send_command_lets_an_unknown_code_with_state_through(repository, sleeps):
    repo, _ = repository([_set_answer(77)])

    result = await repo.async_send_command("airco-id", RacParser().translate_bytes(_STAT), {})

    assert result.Operation is True


@pytest.mark.parametrize(
    "body",
    [
        json.dumps({"result": 0}),
        json.dumps({"result": 0, "contents": {"airconStat": 3}}),
        json.dumps({"result": 0, "contents": {"airconStat": "AAAA"}}),
    ],
)
async def test_send_command_malformed_answers_are_typed(repository, sleeps, body):
    repo, _ = repository([_FakeResponse(200, body)])

    with pytest.raises(WfRacMalformedResponseError):
        await repo.async_send_command("airco-id", RacParser().translate_bytes(_STAT), {})


async def test_send_command_waits_out_the_lock_and_resends_from_the_fresh_state(repository, sleeps):
    """The other client's state is in the status answer; a retry built from the
    stale block would hand its fields straight back and revert them.
    """
    expires = 1_000_000 + 40
    repo, session = repository(
        [
            _set_answer(12),
            _FakeResponse(200, _status_body(airconStat=_HEAT_STAT, expires=expires)),
            _set_answer(0),
        ]
    )
    bodies = _recorded(session)
    base = RacParser().translate_bytes(_STAT)

    with patch("pywfrac.repository.time.time", return_value=1_000_000.0):
        await repo.async_send_command("airco-id", base, {AirconCommands.Operation: False})

    assert [b["command"] for b in bodies] == [
        "setAirconStat",
        "getAirconStat",
        "setAirconStat",
    ]
    assert bodies[0]["contents"]["airconStat"] == _frame(_STAT, Operation=False)
    assert bodies[2]["contents"]["airconStat"] == _frame(_HEAT_STAT, Operation=False)
    assert bodies[0]["contents"]["airconStat"] != bodies[2]["contents"]["airconStat"]
    assert sleeps == [41.0]


@pytest.mark.parametrize(
    ("expires", "expected"),
    [
        (1_000_000 - 500, 0.0),
        (1_000_000 + 500, WRITE_LOCK_MAX_WAIT.total_seconds()),
        (None, WRITE_LOCK_RETRY_DELAY.total_seconds()),
    ],
)
async def test_send_command_clamps_the_lock_wait(repository, sleeps, expires, expected):
    repo, _ = repository(
        [
            _set_answer(1),
            _FakeResponse(200, _status_body(expires=expires)),
            _set_answer(0),
        ]
    )

    with patch("pywfrac.repository.time.time", return_value=1_000_000.0):
        await repo.async_send_command("airco-id", RacParser().translate_bytes(_STAT), {})

    assert sleeps == [expected]


async def test_send_command_falls_back_when_the_status_is_unreadable(repository, sleeps):
    """The retry then goes out with the state the caller supplied."""
    repo, session = repository([_set_answer(11), _FakeResponse(200, "[]"), _set_answer(0)])
    bodies = _recorded(session)

    await repo.async_send_command(
        "airco-id", RacParser().translate_bytes(_STAT), {AirconCommands.Operation: False}
    )

    assert sleeps == [WRITE_LOCK_RETRY_DELAY.total_seconds()]
    assert bodies[2]["contents"]["airconStat"] == bodies[0]["contents"]["airconStat"]


async def test_send_command_retries_the_lock_only_once(repository, sleeps):
    repo, session = repository(
        [_set_answer(12), _FakeResponse(200, _status_body()), _set_answer(12)]
    )

    with pytest.raises(WfRacWriteRefusedError):
        await repo.async_send_command("airco-id", RacParser().translate_bytes(_STAT), {})

    assert len(session.urls) == 3


async def test_send_command_registers_and_resends_the_same_frame(repository, sleeps):
    repo, session = repository([_set_answer(2), _result(0), _set_answer(0)])
    repo._time_zone = "Europe/Berlin"
    bodies = _recorded(session)

    await repo.async_send_command(
        "airco-id", RacParser().translate_bytes(_STAT), {AirconCommands.PresetTemp: 20.0}
    )

    assert [b["command"] for b in bodies] == [
        "setAirconStat",
        "updateAccountInfo",
        "setAirconStat",
    ]
    assert bodies[1]["contents"]["timezone"] == "Europe/Berlin"
    assert bodies[2]["contents"] == bodies[0]["contents"]
    assert sleeps == []


async def test_send_command_registers_with_the_time_zone_of_an_earlier_registration(
    repository, sleeps
):
    repo, session = repository([_result(0), _set_answer(2), _result(0), _set_answer(0)])
    bodies = _recorded(session)
    await repo.async_register("airco-id", "Asia/Tokyo")

    await repo.async_send_command("airco-id", RacParser().translate_bytes(_STAT), {})

    assert bodies[2]["contents"]["timezone"] == "Asia/Tokyo"


async def test_send_command_registers_only_once(repository, sleeps):
    repo, session = repository([_set_answer(2), _result(0), _set_answer(2)])
    repo._time_zone = "Europe/Berlin"

    with pytest.raises(WfRacRegistrationError):
        await repo.async_send_command("airco-id", RacParser().translate_bytes(_STAT), {})

    assert len(session.urls) == 3


async def test_send_command_reports_a_full_account_table(repository, sleeps):
    repo, _ = repository([_set_answer(2), _result(2)])
    repo._time_zone = "Europe/Berlin"

    with pytest.raises(WfRacAccountTableFullError):
        await repo.async_send_command("airco-id", RacParser().translate_bytes(_STAT), {})


async def test_send_command_without_a_known_time_zone_cannot_register(repository, sleeps):
    repo, session = repository([_set_answer(2)])

    with pytest.raises(WfRacRegistrationError):
        await repo.async_send_command("airco-id", RacParser().translate_bytes(_STAT), {})

    assert len(session.urls) == 1


async def test_time_zone_constructor_argument_enables_re_registration(sleeps):
    session = _FakeSession([_set_answer(2), _result(0), _set_answer(0)])
    repo = Repository(
        session,
        "127.0.0.1",
        51443,
        "operator-id",
        "device-id",
        method="http",
        time_zone="Europe/Berlin",
    )

    await repo.async_send_command("airco-id", RacParser().translate_bytes(_STAT), {})

    assert len(session.urls) == 3


async def test_send_command_propagates_transport_errors(repository, sleeps):
    repo, _ = repository([ClientConnectionError("boom")] * 2)

    with pytest.raises(WfRacConnectionError):
        await repo.async_send_command("airco-id", RacParser().translate_bytes(_STAT), {})
