"""The WebSocket client, against a real socket.

Every test here opens a loopback TCP server and speaks RFC 6455 to it by hand.
That is the point: a transport tested against a mock of itself proves the mock,
and the failures this layer exists to survive — a half-read frame, a peer that
vanishes, a negotiated extension nobody offered — only appear on a socket.

No network leaves the machine, no TLS certificate is needed, and the server is a
hundred lines of `struct` at the bottom of this file.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading
from collections.abc import Callable, Iterator

import pytest
from forge.propdesk.rithmic.transport import MAX_FRAME, TransportError, WebSocketTransport

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ── a WebSocket server, by hand ──────────────────────────────────────────────
class Server:
    """A loopback WebSocket server that does exactly what a test tells it to."""

    def __init__(self, handler: Callable[[socket.socket], None]) -> None:
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port: int = self._listener.getsockname()[1]
        self.error: BaseException | None = None
        self._handler = handler
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def uri(self) -> str:
        return f"ws://127.0.0.1:{self.port}/"

    def _serve(self) -> None:
        try:
            self._listener.settimeout(10.0)
            client, _ = self._listener.accept()
            with client:
                self._handler(client)
        except BaseException as exc:
            self.error = exc
        finally:
            self._listener.close()

    def stop(self) -> None:
        self._thread.join(timeout=10.0)


def accept_upgrade(client: socket.socket) -> None:
    """Read the client's handshake and answer it correctly."""
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        chunk = client.recv(4096)
        if not chunk:
            raise AssertionError("the client closed before finishing the handshake")
        buffer += chunk
    key = ""
    for line in buffer.split(b"\r\n"):
        name, sep, value = line.partition(b":")
        if sep and name.decode().strip().lower() == "sec-websocket-key":
            key = value.decode().strip()
    accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
    client.sendall(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        ).encode()
    )


def frame(payload: bytes, *, opcode: int = 0x2, fin: bool = True) -> bytes:
    """A server frame: unmasked, as RFC 6455 requires of a server."""
    head = bytearray([(0x80 if fin else 0x00) | opcode])
    length = len(payload)
    if length < 126:
        head.append(length)
    elif length < (1 << 16):
        head.append(126)
        head += struct.pack("!H", length)
    else:
        head.append(127)
        head += struct.pack("!Q", length)
    return bytes(head) + payload


def read_frame(client: socket.socket) -> tuple[int, bytes]:
    """One client frame, unmasked. Clients must mask, and this asserts they did."""

    def exactly(count: int) -> bytes:
        out = b""
        while len(out) < count:
            chunk = client.recv(count - len(out))
            if not chunk:
                raise AssertionError("the client closed mid-frame")
            out += chunk
        return out

    first, second = exactly(2)
    opcode = first & 0x0F
    assert second & 0x80, "a client frame must be masked"
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", exactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", exactly(8))[0]
    mask = exactly(4)
    body = exactly(length) if length else b""
    return opcode, bytes(byte ^ mask[i % 4] for i, byte in enumerate(body))


@pytest.fixture
def transport() -> Iterator[WebSocketTransport]:
    ws = WebSocketTransport(connect_timeout=5.0)
    yield ws
    ws.close()


# ── the handshake ────────────────────────────────────────────────────────────
def test_a_completed_handshake_carries_a_frame_each_way(transport) -> None:
    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        opcode, payload = read_frame(client)
        assert opcode == 0x2
        assert payload == b"\x08\x01ping"
        client.sendall(frame(b"\x08\x02pong"))

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    transport.send(b"\x08\x01ping")
    assert transport.receive(5.0) == b"\x08\x02pong"
    transport.close()
    server.stop()
    assert server.error is None


def test_a_refused_upgrade_says_so_without_reading_a_body(transport) -> None:
    def handler(client: socket.socket) -> None:
        while b"\r\n\r\n" not in client.recv(4096):
            continue
        client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")

    server = Server(handler)
    with pytest.raises(TransportError, match="refused the WebSocket upgrade with HTTP 403"):
        transport.connect(server.uri, verify=None)
    server.stop()


def test_a_wrong_accept_key_is_refused(transport) -> None:
    """A correct-looking 101 with the wrong digest is not the connection we opened."""

    def handler(client: socket.socket) -> None:
        while b"\r\n\r\n" not in client.recv(4096):
            continue
        client.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Sec-WebSocket-Accept: bm90LXRoZS1yaWdodC1kaWdlc3Q=\r\n\r\n"
        )

    server = Server(handler)
    with pytest.raises(TransportError, match="did not match the key"):
        transport.connect(server.uri, verify=None)
    server.stop()


def test_an_unoffered_extension_is_refused_rather_than_misread(transport) -> None:
    def handler(client: socket.socket) -> None:
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            buffer += client.recv(4096)
        key = ""
        for line in buffer.split(b"\r\n"):
            name, sep, value = line.partition(b":")
            if sep and name.decode().strip().lower() == "sec-websocket-key":
                key = value.decode().strip()
        digest = base64.b64encode(
            hashlib.sha1((key + _GUID).encode()).digest()
        ).decode()
        client.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                f"Sec-WebSocket-Accept: {digest}\r\n"
                "Sec-WebSocket-Extensions: permessage-deflate\r\n\r\n"
            ).encode()
        )

    server = Server(handler)
    with pytest.raises(TransportError, match="negotiated a WebSocket extension"):
        transport.connect(server.uri, verify=None)
    server.stop()


def test_a_plaintext_connection_off_the_loopback_is_refused(transport) -> None:
    """The login frame carries a password, so the scheme is not negotiable."""
    with pytest.raises(TransportError, match="refusing a plaintext ws:// connection"):
        transport.connect("ws://rprotocol.rithmic.com:443/", verify=None)


def test_a_non_websocket_scheme_is_refused(transport) -> None:
    with pytest.raises(TransportError, match="not a WebSocket scheme"):
        transport.connect("https://rprotocol.rithmic.com/", verify=None)


def test_verification_cannot_be_switched_off() -> None:
    """`verify=False` is refused before a socket is opened, not honoured."""
    ws = WebSocketTransport()
    with pytest.raises(TransportError, match="verification cannot be disabled"):
        ws.connect("wss://127.0.0.1:1/", verify=False)


def test_a_context_that_does_not_verify_is_refused() -> None:
    import ssl

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    ws = WebSocketTransport()
    with pytest.raises(TransportError, match="does not verify certificates"):
        ws.connect("wss://127.0.0.1:1/", verify=context)


# ── framing ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("size", [0, 1, 125, 126, 1024, 70000])
def test_payloads_survive_every_length_prefix(transport, size) -> None:
    """125, 126 and 65536 are where the length encoding changes shape."""
    body = bytes(range(256)) * (size // 256) + bytes(range(size % 256))

    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        _, payload = read_frame(client)
        assert payload == body
        client.sendall(frame(body))

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    transport.send(body)
    assert transport.receive(5.0) == body
    server.stop()
    assert server.error is None


def test_a_fragmented_message_is_reassembled(transport) -> None:
    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        client.sendall(frame(b"first-", opcode=0x2, fin=False))
        client.sendall(frame(b"second-", opcode=0x0, fin=False))
        client.sendall(frame(b"third", opcode=0x0, fin=True))

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    assert transport.receive(5.0) == b"first-second-third"
    server.stop()
    assert server.error is None


def test_a_ping_is_answered_and_not_delivered_upward(transport) -> None:
    """The session above counts its own heartbeats; a protocol ping is not one."""

    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        client.sendall(frame(b"alive?", opcode=0x9))
        opcode, payload = read_frame(client)
        assert opcode == 0xA
        assert payload == b"alive?"
        client.sendall(frame(b"data"))

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    assert transport.receive(5.0) == b"data"
    server.stop()
    assert server.error is None


def test_a_timeout_returns_none_rather_than_blocking(transport) -> None:
    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        # Hold the socket open, say nothing.
        client.recv(1)

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    assert transport.receive(0.2) is None
    transport.close()
    server.stop()


def test_a_close_frame_raises_rather_than_reading_as_idle(transport) -> None:
    """A closed socket that returns `None` is indistinguishable from a quiet one."""

    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        client.sendall(frame(struct.pack("!H", 1000), opcode=0x8))

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    with pytest.raises(TransportError, match="peer closed the WebSocket"):
        transport.receive(5.0)
    server.stop()


def test_a_peer_that_vanishes_raises(transport) -> None:
    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        client.close()

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    with pytest.raises(TransportError, match="closed the connection"):
        transport.receive(5.0)
    server.stop()


def test_a_half_sent_frame_is_not_resynchronised_onto_its_payload(transport) -> None:
    """A header promising ten bytes followed by three is a broken stream, not a timeout."""

    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        client.sendall(bytes([0x82, 10]) + b"abc")
        client.recv(1)

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    with pytest.raises(TransportError, match="stopped mid-frame"):
        transport.receive(0.3)
    server.stop()


def test_an_implausible_length_is_refused_before_it_is_allocated(transport) -> None:
    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        client.sendall(bytes([0x82, 127]) + struct.pack("!Q", MAX_FRAME + 1))
        client.recv(1)

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    with pytest.raises(TransportError, match="above the"):
        transport.receive(5.0)
    server.stop()


def test_a_text_frame_is_refused(transport) -> None:
    """This protocol is protobuf; a text frame means the far end is not Rithmic."""

    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        client.sendall(frame(b"hello", opcode=0x1))

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    with pytest.raises(TransportError, match="text frame"):
        transport.receive(5.0)
    server.stop()


def test_a_reserved_bit_is_refused(transport) -> None:
    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        client.sendall(bytes([0xC2, 1]) + b"x")
        client.recv(1)

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    with pytest.raises(TransportError, match="reserved WebSocket bit"):
        transport.receive(5.0)
    server.stop()


def test_a_continuation_with_nothing_to_continue_is_refused(transport) -> None:
    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        client.sendall(frame(b"orphan", opcode=0x0, fin=True))

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    with pytest.raises(TransportError, match="nothing to continue"):
        transport.receive(5.0)
    server.stop()


# ── lifecycle ────────────────────────────────────────────────────────────────
def test_sending_before_connecting_is_refused() -> None:
    with pytest.raises(TransportError, match="not connected"):
        WebSocketTransport().send(b"x")


def test_connecting_twice_is_refused(transport) -> None:
    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        client.recv(1)

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    with pytest.raises(TransportError, match="already connected"):
        transport.connect(server.uri, verify=None)
    transport.close()
    server.stop()


def test_close_is_safe_on_a_transport_that_never_connected() -> None:
    WebSocketTransport().close()


def test_close_sends_a_close_frame(transport) -> None:
    seen: list[int] = []

    def handler(client: socket.socket) -> None:
        accept_upgrade(client)
        opcode, _ = read_frame(client)
        seen.append(opcode)

    server = Server(handler)
    transport.connect(server.uri, verify=None)
    transport.close()
    server.stop()
    assert server.error is None
    assert seen == [0x8]


def test_a_failed_handshake_leaves_no_socket_behind(transport) -> None:
    """The socket is discarded on a failed connect, so a retry is a fresh one."""

    def handler(client: socket.socket) -> None:
        while b"\r\n\r\n" not in client.recv(4096):
            continue
        client.sendall(b"HTTP/1.1 500 Server Error\r\n\r\n")

    server = Server(handler)
    with pytest.raises(TransportError):
        transport.connect(server.uri, verify=None)
    server.stop()
    # Not "already connected" — the failed attempt left nothing.
    with pytest.raises(TransportError, match="could not reach"):
        transport.connect("ws://127.0.0.1:1/", verify=None)
