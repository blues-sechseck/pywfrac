"""The credential fields must not reach a debug log.

Debug logs are what users attach to public issue reports, and the operator
and device id are what the module checks a write against.
"""

import json

from pywfrac.repository import Repository, _redact_mapping

from .test_repository import _FakeResponse, _FakeSession

_OPERATOR_ID = "operator-id"
_DEVICE_ID = "device-id"


def test_the_credential_fields_are_replaced():
    redacted = _redact_mapping(
        {
            "apiVer": "1.0",
            "command": "getAirconStat",
            "deviceId": "homeassistant-device-5a48eb80f89",
            "operatorId": "hassio-3-03fc-4336-95f0-ef985ea578d3",
            "contents": {"airconId": "14b5cd001aa3", "accountId": "hassio-3-03fc"},
        }
    )

    assert redacted["deviceId"] == "**redacted**"
    assert redacted["operatorId"] == "**redacted**"
    assert redacted["contents"]["accountId"] == "**redacted**"
    # Kept: it says which unit the line is about and cannot be acted on.
    assert redacted["contents"]["airconId"] == "14b5cd001aa3"
    assert redacted["command"] == "getAirconStat"


def test_the_original_is_not_touched():
    """It is the object about to go on the wire."""
    data = {"operatorId": "hassio-1", "contents": {"airconId": "a"}}

    _redact_mapping(data)

    assert data["operatorId"] == "hassio-1"


def test_a_list_in_the_answer_is_walked():
    """remoteList comes back as a list of account records."""
    redacted = _redact_mapping({"remoteList": [{"accountId": "hassio-1"}]})

    assert redacted["remoteList"][0]["accountId"] == "**redacted**"


async def test_neither_the_request_nor_the_answer_carries_them(caplog):
    """The whole round trip, not just the helper.

    The answer echoes the ids it was sent, and it is logged before it is
    parsed - so the body has to be scrubbed as text.
    """
    caplog.set_level("DEBUG", logger="pywfrac.repository.http")
    echoed = json.dumps(
        {
            "result": 0,
            "contents": {"airconId": "14b5cd001aa3"},
            "accountId": _OPERATOR_ID,
            "deviceId": _DEVICE_ID,
        }
    )
    repo = Repository(
        _FakeSession([_FakeResponse(200, echoed)]),
        "127.0.0.1",
        51443,
        _OPERATOR_ID,
        _DEVICE_ID,
        method="http",
    )

    await repo.get_airco_id()

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert _OPERATOR_ID not in logged
    assert _DEVICE_ID not in logged
    # Still readable: which unit, and what the module answered.
    assert "14b5cd001aa3" in logged
    assert "127.0.0.1" in logged
