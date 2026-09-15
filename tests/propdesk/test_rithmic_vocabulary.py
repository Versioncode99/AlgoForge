"""The vocabulary loader, against protos written here rather than Rithmic's.

Every `.proto` in this file is authored for the test. None of it is copied from
the R | Protocol archive, which is licensed to the operator and not
redistributable — see `docs/ADR-0001-rithmic-transport.md`. What is reproduced
is the *shape* the loader depends on: a `template_id` field whose default is the
message's id, a `SysInfraType` enum on the login message, and messages that
import one another by bare file name.

That shape is the whole claim. If a real archive has it, these tests say the
loader reads it; if it does not, the loader raises naming what it could not
bind, and there are tests for that too.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from forge.propdesk.rithmic import vocabulary as vocab
from forge.propdesk.rithmic.session import Plant
from forge.propdesk.rithmic.vocabulary import VocabularyUnavailable

protobuf = pytest.importorskip("google.protobuf", reason="protobuf is an optional dependency")
pytest.importorskip("grpc_tools", reason="a protobuf compiler is an optional dependency")


# ── a miniature protocol in the same shape ───────────────────────────────────
SHARED = """
syntax = "proto2";
package forgetest;

message Shared {
  optional string note = 1;
}
"""

MESSAGES = """
syntax = "proto2";
package forgetest;
import "shared.proto";

message RequestLogin {
  enum SysInfraType {
    TICKER_PLANT = 1;
    ORDER_PLANT = 2;
    HISTORY_PLANT = 3;
    PNL_PLANT = 4;
    REPOSITORY_PLANT = 5;
  }
  required int32 template_id = 154467 [default = 10];
  optional string system_name = 1;
  optional string user = 2;
  optional string password = 3;
  optional int32 infra_type = 4;
  repeated string tags = 5;
}

message ResponseLogin {
  required int32 template_id = 154467 [default = 11];
  optional int32 heartbeat_interval = 1;
}

message RequestLogout {
  required int32 template_id = 154467 [default = 12];
}

message ResponseLogout {
  required int32 template_id = 154467 [default = 13];
}

message RequestHeartbeat {
  required int32 template_id = 154467 [default = 18];
}

message ResponseHeartbeat {
  required int32 template_id = 154467 [default = 19];
}

message RequestRithmicSystemInfo {
  required int32 template_id = 154467 [default = 16];
}

message ResponseRithmicSystemInfo {
  required int32 template_id = 154467 [default = 17];
  repeated string system_name = 1;
}

message RequestAccountList {
  required int32 template_id = 154467 [default = 302];
}

message ResponseAccountList {
  required int32 template_id = 154467 [default = 303];
  optional string account_id = 1;
}

message RequestPnLPositionSnapshot {
  required int32 template_id = 154467 [default = 402];
}

message ResponsePnLPositionSnapshot {
  required int32 template_id = 154467 [default = 403];
  optional string symbol = 1;
}

message NotRoutable {
  optional string detail = 1;
}
"""


@pytest.fixture
def protos(tmp_path: Path) -> Path:
    source = tmp_path / "proto"
    source.mkdir()
    (source / "shared.proto").write_text(SHARED)
    (source / "messages.proto").write_text(MESSAGES)
    return source


@pytest.fixture
def compiled(protos: Path, tmp_path: Path) -> Iterator[dict[str, object]]:
    before = set(sys.modules)
    generated = vocab.compile_protos(protos, tmp_path / "generated")
    yield vocab._messages(vocab._load(generated))
    # The generated modules register themselves under bare names; leaving them
    # behind would let one test's `messages_pb2` satisfy another's import.
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


# ── compilation ──────────────────────────────────────────────────────────────
def test_every_proto_in_the_directory_is_compiled(protos: Path, tmp_path: Path) -> None:
    generated = vocab.compile_protos(protos, tmp_path / "out")
    assert {path.name for path in generated} == {"shared_pb2.py", "messages_pb2.py"}


def test_an_empty_proto_directory_is_refused_with_the_path(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(VocabularyUnavailable, match=r"no \.proto files were found"):
        vocab.compile_protos(empty, tmp_path / "out")


def test_a_proto_the_compiler_rejects_stops_the_load(tmp_path: Path) -> None:
    """A vocabulary half-built from a broken archive is worse than none."""
    source = tmp_path / "proto"
    source.mkdir()
    (source / "broken.proto").write_text("syntax = 'proto2'; this is not a proto")
    with pytest.raises(VocabularyUnavailable, match="protoc rejected"):
        vocab.compile_protos(source, tmp_path / "out")


def test_compiling_does_not_leave_the_generated_directory_importable(
    protos: Path, tmp_path: Path
) -> None:
    """A directory of freshly generated modules on sys.path shadows real ones."""
    generated = vocab.compile_protos(protos, tmp_path / "out")
    before = list(sys.path)
    vocab._load(generated)
    assert sys.path == before


# ── template ids are read, never transcribed ─────────────────────────────────
def test_template_ids_come_from_each_message_s_own_default(compiled) -> None:
    ids = vocab.template_ids(compiled)
    assert ids["RequestLogin"] == 10
    assert ids["ResponseLogin"] == 11
    assert ids["ResponsePnLPositionSnapshot"] == 403


def test_a_message_without_a_template_id_is_not_routable(compiled) -> None:
    """Shared and nested types have no id, and inventing one would route noise."""
    ids = vocab.template_ids(compiled)
    assert "NotRoutable" not in ids
    assert "Shared" not in ids


# ── binding ──────────────────────────────────────────────────────────────────
def test_every_role_binds_to_the_id_its_message_declares(compiled) -> None:
    built = vocab.bind(compiled)
    assert built.templates["login"] == 10
    assert built.templates["login_response"] == 11
    assert built.templates["heartbeat_response"] == 19
    assert built.template("account_list") == 302


def test_infra_types_are_read_from_the_login_message_s_own_enum(compiled) -> None:
    built = vocab.bind(compiled)
    assert built.infra[Plant.TICKER] == 1
    assert built.infra[Plant.ORDER] == 2
    assert built.infra[Plant.PNL] == 4
    assert built.infra[Plant.REPOSITORY] == 5


def test_a_role_that_cannot_be_bound_names_the_role_and_the_message(compiled) -> None:
    with pytest.raises(VocabularyUnavailable) as caught:
        vocab.bind(compiled, roles={**vocab.DEFAULT_ROLES, "login": "RequestSignOn"})
    assert "login (looked for RequestSignOn)" in str(caught.value)


def test_an_sdk_without_the_plant_enum_is_refused_rather_than_guessed(compiled) -> None:
    """An infra_type that names the wrong plant reads as missing data, not an error."""
    without_login = {name: cls for name, cls in compiled.items() if name != "RequestLogin"}
    roles = {r: n for r, n in vocab.DEFAULT_ROLES.items() if n != "RequestLogin"}
    with pytest.raises(VocabularyUnavailable, match="declares no SysInfraType"):
        vocab.bind(without_login, roles=roles)


# ── build / encode / decode ──────────────────────────────────────────────────
def test_a_built_message_carries_the_fields_it_was_given(compiled) -> None:
    built = vocab.bind(compiled)
    message = built.build(
        "login",
        {
            "template_id": built.template("login"),
            "system_name": "Rithmic Test",
            "user": "operator",
            "password": "hunter2",
            "infra_type": built.infra[Plant.ORDER],
        },
    )
    assert message.system_name == "Rithmic Test"
    assert message.infra_type == 2
    assert message.template_id == 10


def test_a_repeated_field_is_extended_rather_than_assigned(compiled) -> None:
    built = vocab.bind(compiled)
    message = built.build(
        "login", {"template_id": 10, "tags": ["one", "two"]}
    )
    assert list(message.tags) == ["one", "two"]


def test_a_field_the_sdk_does_not_have_is_refused_not_dropped(compiled) -> None:
    """A login silently missing infra_type logs in to the wrong plant."""
    built = vocab.bind(compiled)
    with pytest.raises(VocabularyUnavailable, match="has no 'mfa_code' field"):
        built.build("login", {"template_id": 10, "mfa_code": "000000"})


def test_a_frame_round_trips_through_encode_and_decode(compiled) -> None:
    built = vocab.bind(compiled)
    sent = built.build("account_list", {"template_id": 302})
    decoded = built.decode(built.encode(sent))
    assert decoded.template_id == 302
    assert type(decoded).__name__ == "RequestAccountList"


def test_decode_routes_by_template_id_not_by_what_was_expected(compiled) -> None:
    """A response arriving out of order must decode as itself, not as the request."""
    built = vocab.bind(compiled)
    reply = compiled["ResponsePnLPositionSnapshot"]()
    reply.template_id = 403
    reply.symbol = "ESZ5"
    decoded = built.decode(bytes(reply.SerializeToString()))
    assert type(decoded).__name__ == "ResponsePnLPositionSnapshot"
    assert decoded.symbol == "ESZ5"


def test_an_unknown_template_id_is_refused_rather_than_guessed(compiled) -> None:
    built = vocab.bind(compiled)
    stranger = compiled["RequestLogin"]()
    stranger.template_id = 9999
    with pytest.raises(VocabularyUnavailable, match="template id 9999 is not in"):
        built.decode(bytes(stranger.SerializeToString()))


def test_a_corrupt_frame_is_refused_with_a_reason(compiled) -> None:
    built = vocab.bind(compiled)
    with pytest.raises(VocabularyUnavailable, match="could not be decoded"):
        built.decode(b"\xff\xff\xff\xff\xff\xff\xff\xff")


# ── availability ─────────────────────────────────────────────────────────────
def test_availability_reports_the_missing_piece_rather_than_a_boolean() -> None:
    ready, why = vocab.available()
    assert ready is True
    assert why == ""


def test_availability_names_protobuf_when_it_is_absent(monkeypatch) -> None:
    real = importlib.util.find_spec

    def absent(name: str, *args, **kwargs):
        if name == "google.protobuf":
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", absent)
    ready, why = vocab.available()
    assert ready is False
    assert "protobuf" in why


def test_availability_names_the_compiler_when_it_is_absent(monkeypatch) -> None:
    real = importlib.util.find_spec

    def absent(name: str, *args, **kwargs):
        if name == "grpc_tools":
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", absent)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    ready, why = vocab.available()
    assert ready is False
    assert "compiler" in why


# ── the licence boundary ─────────────────────────────────────────────────────
def test_no_generated_module_is_written_into_the_repository(protos: Path, tmp_path: Path) -> None:
    """`*_pb2.py` is a derivative of a licensed `.proto` and stays out of the tree."""
    destination = tmp_path / "generated"
    vocab.compile_protos(protos, destination)
    repository = Path(__file__).resolve().parents[2]
    assert destination.is_absolute()
    assert repository not in destination.parents
    # The source tree, not the virtualenv: protobuf ships its own `*_pb2.py`.
    for tree in ("packages", "apps", "tests", "scripts"):
        assert not list((repository / tree).rglob("*_pb2.py"))
