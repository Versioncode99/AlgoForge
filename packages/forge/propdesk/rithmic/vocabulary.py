"""Build the wire vocabulary from the operator's `.proto` files, at run time.

`session.Vocabulary` is six things — a template-id table, an `infra_type` table,
and `build` / `decode` / `encode`. The session was written against that shape so
that every behaviour in it could be exercised with a fake. This is where a real
one comes from.

**Nothing here is transcribed.** The template ids are not a table somebody typed
out of the reference guide: each Rithmic message declares its own id as the
*default value* of its `template_id` field, so the map is read out of the
compiled descriptors and is correct by construction for whatever version the
operator installed. `docs/ADR-0001-rithmic-transport.md` records why that
matters — the PDF in the archive and the `change_log` beside it disagree about
the version, and 0.89.0.0 changed a scalar field into a repeated one.

**What is written down is the role table**, because "which message means login"
is a fact about this adapter's intentions rather than about the wire. It is
checked at load: a role that cannot be bound to a message in the operator's SDK
raises, naming the role and the name that was looked for, rather than producing a
vocabulary that would fail on the first login with a `KeyError`.

**protobuf is not a dependency of AlgoForge.** It is imported here, inside the
functions that need it, so an installation that never configures Rithmic never
needs it — and one that does gets a message naming the package rather than an
`ImportError` out of an adapter.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from forge.propdesk.rithmic.sdk import RithmicSdk, SdkMissing
from forge.propdesk.rithmic.session import Plant, SessionError, Vocabulary


class VocabularyUnavailable(SessionError):
    """The vocabulary could not be built, and the reason is actionable.

    A subclass of `SessionError` so a caller that already handles "this
    connection cannot be made" does not need a second branch, and its own type
    so the adapter can distinguish a missing toolchain from a rejected login.
    """


#: Role → the message this adapter expects to carry it, by its `.proto` message
#: name. Requests and the responses they are answered with, because the
#: protocol's correlation *is* the response template id.
#:
#: These names are what this build looks for. An SDK that names them differently
#: is not silently tolerated: `build` raises naming the role and the name, which
#: is a five-minute fix against the operator's own `.proto` directory and is very
#: much better than a login that times out.
DEFAULT_ROLES: Mapping[str, str] = {
    "login": "RequestLogin",
    "login_response": "ResponseLogin",
    "logout": "RequestLogout",
    "logout_response": "ResponseLogout",
    "heartbeat": "RequestHeartbeat",
    "heartbeat_response": "ResponseHeartbeat",
    "system_info": "RequestRithmicSystemInfo",
    "system_info_response": "ResponseRithmicSystemInfo",
    "account_list": "RequestAccountList",
    "account_list_response": "ResponseAccountList",
    "position_list": "RequestPnLPositionSnapshot",
    "position_list_response": "ResponsePnLPositionSnapshot",
}

#: `infra_type` by plant. These are the protocol's own enum values, and unlike
#: the template ids they are not recoverable from a field default — the enum
#: lives on `RequestLogin` and its members are named, not numbered, per plant.
#: Read from the operator's compiled `RequestLogin` enum when it is present, and
#: only falling back to this order when it is not.
_INFRA_MEMBERS: Mapping[Plant, str] = {
    Plant.TICKER: "TICKER_PLANT",
    Plant.ORDER: "ORDER_PLANT",
    Plant.HISTORY: "HISTORY_PLANT",
    Plant.PNL: "PNL_PLANT",
    Plant.REPOSITORY: "REPOSITORY_PLANT",
}


def _protoc(arguments: list[str]) -> None:
    """Run protoc, from `grpcio-tools` if it is importable, else from the path."""
    if importlib.util.find_spec("grpc_tools") is not None:
        from grpc_tools import protoc as _tool

        code = _tool.main(["protoc", *arguments])
        if code != 0:
            raise VocabularyUnavailable(
                f"protoc rejected the SDK's .proto files (exit {code}). The archive may "
                "be incomplete or a version this adapter has not been reconciled against."
            )
        return
    try:
        subprocess.run(["protoc", *arguments], check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise VocabularyUnavailable(
            "No protobuf compiler is available. Install `grpcio-tools` into the "
            "environment running AlgoForge, or put `protoc` on the path. AlgoForge "
            "does not ship one, and it does not ship Rithmic's .proto files either."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise VocabularyUnavailable(
            "protoc rejected the SDK's .proto files: "
            + (detail[-1] if detail else f"exit {exc.returncode}")
        ) from exc


def compile_protos(source: Path, destination: Path) -> list[Path]:
    """Generate `*_pb2.py` for every `.proto` in `source`, into `destination`.

    The generated modules are derivatives of licensed `.proto` files, so they are
    written to a directory outside the repository and `.gitignore` carries
    `*_pb2.py` besides. Nothing from the archive is committed; see the ADR.
    """
    protos = sorted(source.glob("*.proto"))
    if not protos:
        raise VocabularyUnavailable(
            f"no .proto files were found in {source}. The SDK's proto directory is "
            "where the vocabulary comes from, and an empty one cannot produce it."
        )
    destination.mkdir(parents=True, exist_ok=True)
    _protoc(
        [
            f"--proto_path={source}",
            f"--python_out={destination}",
            *(str(path) for path in protos),
        ]
    )
    return sorted(destination.glob("*_pb2.py"))


def _load(generated: list[Path]) -> list[Any]:
    """Import the generated modules, with their own directory first on the path.

    Rithmic's protos import one another by bare file name, so the generated
    modules do `import base_pb2` — which only resolves if their directory is
    importable. It is inserted at the front and removed afterwards rather than
    left behind: a directory of freshly generated modules sitting permanently on
    `sys.path` would shadow anything that shares a name with them.
    """
    directory = str(generated[0].parent)
    sys.path.insert(0, directory)
    try:
        modules: list[Any] = []
        for path in generated:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:  # pragma: no cover - defensive
                raise VocabularyUnavailable(f"could not load the generated module {path.name}")
            module = importlib.util.module_from_spec(spec)
            # Registered before execution so protos that import one another
            # resolve to the module being built rather than re-executing it.
            sys.modules[path.stem] = module
            spec.loader.exec_module(module)
            modules.append(module)
        return modules
    finally:
        if directory in sys.path:
            sys.path.remove(directory)


def _messages(modules: list[Any]) -> dict[str, Any]:
    """Every generated message class, by its `.proto` name."""
    from google.protobuf.message import Message

    found: dict[str, Any] = {}
    for module in modules:
        for name in dir(module):
            attribute = getattr(module, name)
            if isinstance(attribute, type) and issubclass(attribute, Message):
                found.setdefault(name, attribute)
    return found


def template_ids(messages: Mapping[str, Any]) -> dict[str, int]:
    """Message name → the template id its own `.proto` declares.

    Every R | Protocol message carries `template_id` with a default equal to its
    id, which is what makes this readable rather than transcribable. A message
    without one is skipped: the nested and shared types have no id and are not
    routable.
    """
    ids: dict[str, int] = {}
    for name, message in messages.items():
        field = message.DESCRIPTOR.fields_by_name.get("template_id")
        if field is None:
            continue
        default = field.default_value
        if isinstance(default, int) and default:
            ids[name] = default
    return ids


def _infra(messages: Mapping[str, Any]) -> dict[Plant, int]:
    """`infra_type` per plant, read from the login message's own enum."""
    login = messages.get(DEFAULT_ROLES["login"])
    enum = None
    if login is not None:
        enum = login.DESCRIPTOR.enum_types_by_name.get("SysInfraType")
    if enum is None:
        raise VocabularyUnavailable(
            f"the SDK's {DEFAULT_ROLES['login']} declares no SysInfraType enum, so which "
            "infra_type identifies which plant cannot be read from it. This adapter will "
            "not guess: an infra_type that names the wrong plant logs in to the wrong "
            "one and the failure surfaces as missing data rather than as an error."
        )
    out: dict[Plant, int] = {}
    missing: list[str] = []
    for plant, member in _INFRA_MEMBERS.items():
        value = enum.values_by_name.get(member)
        if value is None:
            missing.append(member)
            continue
        out[plant] = value.number
    if missing:
        raise VocabularyUnavailable(
            "the SDK's SysInfraType enum does not name " + ", ".join(sorted(missing))
        )
    return out


def build(
    sdk: RithmicSdk | None = None,
    *,
    workspace: Path | None = None,
    cache: Path | None = None,
    roles: Mapping[str, str] = DEFAULT_ROLES,
) -> Vocabulary:
    """The vocabulary for the operator's SDK, compiled and bound.

    `cache` is where the generated modules go. It defaults to a temporary
    directory that is *kept* for the process lifetime, because the generated
    modules must remain importable for as long as the message classes are used.
    """
    if sdk is None:
        try:
            sdk = RithmicSdk.discover(workspace)
        except SdkMissing as exc:
            raise VocabularyUnavailable(str(exc)) from exc

    destination = cache or Path(tempfile.mkdtemp(prefix="algoforge-rithmic-"))
    generated = compile_protos(sdk.proto_directory, destination)
    if not generated:
        raise VocabularyUnavailable(
            f"protoc produced no modules from {sdk.proto_directory}"
        )
    messages = _messages(_load(generated))
    return bind(messages, roles=roles)


def bind(messages: Mapping[str, Any], *, roles: Mapping[str, str] = DEFAULT_ROLES) -> Vocabulary:
    """Turn compiled message classes into a `Vocabulary`.

    Separated from `build` so the binding — the part with the decisions in it —
    is exercised without a compiler, a network or an SDK.
    """
    ids = template_ids(messages)

    unbound = [
        f"{role} (looked for {name})" for role, name in roles.items() if name not in ids
    ]
    if unbound:
        raise VocabularyUnavailable(
            "this build could not bind " + ", ".join(sorted(unbound)) + " in the operator's "
            "SDK. Either the archive is a version this adapter has not been reconciled "
            "against, or the message names differ; both are visible in its proto directory."
        )

    templates = {role: ids[name] for role, name in roles.items()}
    by_template = {ids[name]: messages[name] for name in ids if name in messages}
    by_role = {role: messages[name] for role, name in roles.items()}

    def _build(role: str, fields: Mapping[str, Any]) -> Any:
        try:
            message_type = by_role[role]
        except KeyError:
            raise VocabularyUnavailable(
                f"no Rithmic message is bound to the '{role}' role"
            ) from None
        message = message_type()
        known = message.DESCRIPTOR.fields_by_name
        for key, value in fields.items():
            if key not in known:
                # Refused rather than dropped. A login that silently omits
                # `infra_type` connects to the wrong plant, and a field this
                # build sets but the SDK does not have is a version mismatch
                # worth stopping on.
                raise VocabularyUnavailable(
                    f"the SDK's {message_type.DESCRIPTOR.name} has no '{key}' field, so "
                    f"a '{role}' cannot be built from what this adapter set."
                )
            target = getattr(message, key)
            # Repeatedness is read off the container rather than off the
            # descriptor's `label`, which the C++ backing of protobuf does not
            # expose on every build.
            if hasattr(target, "extend"):
                target.extend(value)
            else:
                setattr(message, key, value)
        return message

    def _decode(payload: bytes) -> Any:
        from google.protobuf.message import DecodeError

        # Every R | Protocol message carries `template_id` at the same field
        # number, and protobuf keeps a field it does not recognise rather than
        # failing on it. So the id is read by parsing the frame as *any* message
        # that declares it, and the real type is chosen from the id. The
        # alternative — hand-decoding a varint out of the wire format — would be
        # this build reimplementing the part protobuf exists to do.
        head = by_role["heartbeat"]()
        try:
            head.ParseFromString(payload)
        except DecodeError as exc:
            raise VocabularyUnavailable(f"a Rithmic frame could not be decoded: {exc}") from exc
        template = int(getattr(head, "template_id", 0) or 0)
        message_type = by_template.get(template)
        if message_type is None:
            raise VocabularyUnavailable(
                f"template id {template} is not in the operator's SDK, so the frame "
                "cannot be decoded into a known message"
            )
        message = message_type()
        try:
            message.ParseFromString(payload)
        except DecodeError as exc:
            raise VocabularyUnavailable(
                f"a {message_type.DESCRIPTOR.name} frame could not be decoded: {exc}"
            ) from exc
        return message

    def _encode(message: Any) -> bytes:
        return bytes(message.SerializeToString())

    return Vocabulary(
        templates=templates,
        infra=_infra(messages),
        build=_build,
        decode=_decode,
        encode=_encode,
    )


def available() -> tuple[bool, str]:
    """Whether a vocabulary could be built here, and what is missing if not.

    Used by the adapter's capability report, so "we cannot connect" is answered
    with the missing piece rather than with a boolean.
    """
    if importlib.util.find_spec("google.protobuf") is None:
        return False, "the `protobuf` package is not installed"
    if importlib.util.find_spec("grpc_tools") is None:
        from shutil import which

        if which("protoc") is None:
            return False, "no protobuf compiler is available (`grpcio-tools`, or `protoc`)"
    return True, ""


__all__ = [
    "DEFAULT_ROLES",
    "VocabularyUnavailable",
    "available",
    "bind",
    "build",
    "compile_protos",
    "template_ids",
]


# A `Callable` alias kept for the adapter's type annotations, so the factory
# shape is named in one place rather than spelled out at each use.
VocabularyFactory = Callable[[], Vocabulary]
