"""Tests for repository.py: how _post() classifies failures and what that
classification does to the discovered communication method. The aiohttp
session is replaced with a fake - no real network involved.
"""

import json
import ssl
from unittest.mock import patch

import pytest
from aiohttp import ClientConnectionError

from pywfrac.repository import (
    Repository,
    WfRacCommandError,
    WfRacConnectionError,
    WfRacRegistrationError,
    WfRacWriteRefusedError,
)

_OK_BODY = json.dumps({"result": 0, "contents": {"airconId": "airco-id"}})


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.content_type = "application/json"
        self._body = body

    async def text(self) -> str:
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
        repo = Repository(
            session, "127.0.0.1", 51443, "operator-id", "device-id", method=method
        )
        return repo, session

    return _build


async def test_http_error_status_raises_command_error(repository):
    repo, _ = repository([_FakeResponse(501, "Not supported this command")])
    with pytest.raises(WfRacCommandError):
        await repo.get_aircon_stats("airco-id")


async def test_connection_failure_raises_connection_error(repository):
    cause = ClientConnectionError("connection refused")
    repo, _ = repository([cause])
    with pytest.raises(WfRacConnectionError) as error:
        await repo.get_aircon_stats("airco-id")
    assert error.value.__cause__ is cause


async def test_timeout_raises_connection_error(repository):
    cause = TimeoutError()
    repo, _ = repository([cause])
    with pytest.raises(WfRacConnectionError) as error:
        await repo.get_aircon_stats("airco-id")
    assert error.value.__cause__ is cause


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


async def test_rediscovery_tries_the_last_working_method_first(repository):
    """Recovery must not put an HTTPS unit behind an HTTP-first timeout."""
    repo, session = repository(
        [ClientConnectionError("boom"), _FakeResponse(200, _OK_BODY)],
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
        "https://127.0.0.1:51443/beaver/command/getAirconStat",
    ]


@pytest.mark.parametrize(
    ("old_method", "new_method"), (("http", "https"), ("https", "http"))
)
async def test_rediscovery_recovers_after_a_protocol_change(
    repository, old_method, new_method
):
    """The alternative remains reachable if a firmware line changes protocol."""
    repo, session = repository(
        [
            ClientConnectionError("unit offline"),
            ClientConnectionError("old protocol refused"),
            _FakeResponse(200, _OK_BODY),
        ],
        method=old_method,
    )
    repo._ssl_context = ssl.create_default_context()

    with pytest.raises(WfRacConnectionError):
        await repo.get_aircon_stats("airco-id")

    await repo.get_aircon_stats("airco-id")

    assert repo.method == new_method
    assert session.urls == [
        f"{old_method}://127.0.0.1:51443/beaver/command/getAirconStat",
        f"{old_method}://127.0.0.1:51443/beaver/command/getAirconStat",
        f"{new_method}://127.0.0.1:51443/beaver/command/getAirconStat",
    ]


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
        return [
            r for r in caplog.records
            if "was accepted but not carried out" in r.message
        ]

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
    cert_path.write_text(
        "-----BEGIN CERTIFICATE-----\nMA==\n-----END CERTIFICATE-----\n"
    )
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
