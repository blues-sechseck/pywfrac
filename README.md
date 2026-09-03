# pywfrac

[![PyPI](https://img.shields.io/pypi/v/pywfrac)](https://pypi.org/project/pywfrac/)
[![CI](https://github.com/blues-sechseck/pywfrac/actions/workflows/ci.yml/badge.svg)](https://github.com/blues-sechseck/pywfrac/actions/workflows/ci.yml)

Async Python client library for **Mitsubishi Heavy Industries** air
conditioners that use the **WF-RAC** WiFi module and the **Smart M-Air** app.
Talks the module's local HTTP(S) API and decodes/encodes the `airconStat`
protocol carried over it.

> **Not for Mitsubishi Electric systems** (e.g. a MAC-577IF2-E interface) or
> MELCloud — different manufacturer, different protocol.

This library is the protocol layer extracted from the
[`mitsubishi_wf_rac`](https://github.com/blues-sechseck/Mitsubishi-WF-RAC-Integration)
Home Assistant integration, which stays the reference for field notes and
supported devices. `pywfrac` has no dependency on Home Assistant — it only
needs an `aiohttp.ClientSession`.

## Install

```
pip install pywfrac
```

## Usage

```python
import aiohttp
from pywfrac import Repository

async def main() -> None:
    async with aiohttp.ClientSession() as session:
        repo = Repository(
            session,
            hostname="192.168.1.50",
            port=51443,
            operator_id="my-operator-id",
            device_id="my-device-id",
        )
        airco_id = await repo.get_airco_id()
        await repo.update_account_info(airco_id, time_zone="Europe/Berlin")
        stats = await repo.get_aircon_stats(airco_id)
```

`Repository` discovers whether the module speaks plain HTTP or HTTPS on the
first request and remembers the result; construct it with `method="http"` or
`method="https"` to skip discovery if you already know. Pass `cert_path` to
use a captured module certificate instead of the permissive fallback context
— see `Repository._get_ssl_context` for how to capture one.

`RacParser` turns a raw `getAirconStat` response into an `Aircon`/`AirconStat`
object and back; see the protocol reference linked below for the field
layout.

## Protocol reference

The `airconStat` wire format, error codes, and per-model capability tables
are documented in the integration's
[module reference](https://github.com/blues-sechseck/Mitsubishi-WF-RAC-Integration/blob/main/ha-integration/docs/wf-rac-module-reference.md).

## Versioning

This library follows [SemVer](https://semver.org/), independent of the
integration's own CalVer release scheme — pin an exact version.

## License

MIT, see [LICENSE](LICENSE). Extracted from code originally by
[@jeatheak](https://github.com/jeatheak), maintained since by
[@blues-sechseck](https://github.com/blues-sechseck).
