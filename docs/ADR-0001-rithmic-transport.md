# ADR 0001 — Which Rithmic API AlgoForge integrates against

**Status** Accepted.
**Date** 2026-09-15.
**Decision** R | Protocol over WebSocket with protobuf framing. The operator
supplies the SDK; AlgoForge redistributes none of it.

## What was inspected

Both archives were unpacked outside the repository and read. Nothing from
either is committed.

| Archive | SHA-256 | Contents |
| --- | --- | --- |
| `RProtocolAPI.0.89.0.0.zip` | `44f70b92d152c37de3e87256d2aec39e4fd023b34337735e9c36b969864bd4ca` | 155 `.proto` files, `change_log`, `Release.Notes`, a TLS CA parameter file, Python and JavaScript samples, `Reference_Guide.pdf` |
| `RApiPlus.NET.13.7.0.0.zip` | `bd502e91a4b66021bddb56dea07178ba5478ebbc5cc101257f0c50d4646dab30` | `win10/lib_35/rapiplus.dll`, `win10/lib_472/rapiplus.dll`, Doxygen HTML, .NET 3.5 and 4.7.2 samples |

### The documentation and the protocol disagree, and the protocol wins

`proto/change_log` opens with **template version 5.42, released 6 February
2026**, and `Release.Notes` for 0.89.0.0 describes changes at that version.
`Reference_Guide.pdf` carries the PDF `Title` **"R Protocol API Reference Guide
0.48.0.0.docx"** — it documents an archive forty-one releases older than the one
it ships inside.

So the reference guide's template table is not authoritative here. The
`.proto` definitions and `change_log` are, and the adapter reads its vocabulary
from the operator's `.proto` files at run time rather than from any table
transcribed from the PDF. Changes at 0.89.0.0 that a transcribed table would
have missed, taken from `Release.Notes`:

* `request_show_fill_history` / `response_show_fill_history` — new;
* `order_copy_status` added to the login-info, user-info and user-info-update
  responses;
* `data_bar_seq_num` changed from a scalar to a repeated field in the tick-bar
  replay and tick-bar messages — a **breaking** change for any decoder written
  against the older shape;
* `rms_updates_only` added to the P&L position-update request;
* `level_1_market_data` and `level_2_market_data` entitlement fields added to
  the exchange-permissions response, and the older `entitlement_flag`
  **deprecated**.

An adapter that hard-coded field numbers from the PDF would decode
`data_bar_seq_num` incorrectly and read a deprecated entitlement flag. That is
the concrete reason the vocabulary is loaded rather than transcribed.

## The licence decides most of this

`Release.Notes` in both archives states that the software is protected by
copyright, that unauthorised reproduction or distribution **"(including its
documentation), or any portion of it"** may be prosecuted, and that it is
licensed under a separate agreement carrying restrictions on use, reverse
engineering, disclosure and confidentiality.

Therefore, and without needing to interpret anything further:

* **no `.proto` file is committed**;
* **no generated `*_pb2.py` is committed** — it is a derivative of a `.proto`;
* **no `rapiplus.dll` is committed**;
* **no documentation text, sample code or field table is committed**, including
  into comments;
* the archives are not vendored, mirrored, or fetched by any build step.

What *is* committed is metadata: version strings, file names, counts and
hashes, which are facts about the artefact rather than any portion of it. The
manifest that records them is built at run time from the operator's own copy —
see `forge.propdesk.rithmic.sdk`.

## The two options

### R | API+ (.NET 13.7.0.0)

**Rejected.**

* It ships as `rapiplus.dll` under `win10/`, for .NET Framework 3.5 and 4.7.2.
  Windows only, .NET Framework only — not .NET Core, not Linux, not macOS.
  AlgoForge runs a Python 3.13 FastAPI process and an Electron shell on all
  three.
* Reaching it from Python means Mono or `pythonnet` plus a .NET Framework
  runtime, which is a second runtime to install, package, sign and support, and
  which does not exist on the platforms half the product targets.
* The DLL cannot be committed, so the build would depend on an operator-supplied
  binary *and* a runtime bridge — two external dependencies where the
  alternative has one.
* Its callback-driven object model would have to be translated into the same
  normalised events the fabric already defines, so none of its higher-level
  convenience survives the boundary anyway.

The one thing it has that R | Protocol does not is a vendor-maintained
implementation of the session lifecycle. That is real, and it is not worth a
Windows-only native dependency in a cross-platform application.

### R | Protocol (0.89.0.0, template 5.42)

**Accepted.**

* Pure protocol: TLS WebSocket, protobuf messages, each carrying a
  `template_id` that routes it. No native binary, no second runtime, and
  identical on every platform AlgoForge runs on.
* The session shape — connect, ask for the system list, log in per plant,
  heartbeat at the interval the login response names, log out — is small enough
  to implement correctly and, more importantly, small enough to *test*. The
  whole of it is exercised against a fake transport in
  `tests/propdesk/test_rithmic_session.py`, with no credential and no network.
* The archive ships Python samples, so the protocol is supported for exactly
  the language AlgoForge's backend is written in.
* Everything AlgoForge needs from a provider is already normalised by
  `forge.propdesk.fabric`, so a protocol-level adapter loses nothing by being
  protocol-level.

The cost is that the session lifecycle is ours to get right. That is accepted
deliberately: it is the part that has to be correct under reconnection and
ambiguity, and owning it is what makes it testable.

## Consequences

**The SDK is a configured location, not a dependency.** `RithmicSdk.discover`
looks in `ALGOFORGE_RITHMIC_SDK`, then in the workspace's `vendor/rithmic`
directory. Absent, every Rithmic capability refuses with a sentence naming what
is missing and where to put it. It does not fall back, simulate, or partially
work.

**The vocabulary is loaded, not transcribed.** Template ids and message classes
come from the operator's `.proto` files (compiled at run time when
`grpcio-tools` is available) or from `*_pb2` modules they generated. A version
the adapter has not been reconciled against is reported as such rather than
assumed compatible.

**Generated code stays out of git.** `.gitignore` refuses `vendor/rithmic/`
and `*_pb2.py`, so a generated module cannot be committed by accident.

**No order is sent from this work.** Order translation is written and tested
against fixtures; sending one in the Rithmic Test environment is a separate,
explicitly confirmed step, and the prerequisite for it — logging into R | Trader
or R | Trader Pro once to accept the required agreements — is the operator's to
complete, not something this code may bypass.
