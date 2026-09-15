"""A WebSocket client for the Rithmic session, written against the standard library.

The session in `session.py` talks to a `Transport` — four methods, no protocol
knowledge — precisely so the state machine could be tested without a socket.
This is the socket.

**Why not a library.** Nothing in this repository depended on a WebSocket client
before now, and adding one to reach a provider whose SDK the operator supplies
separately would put a dependency in every installation for a connector most of
them will never configure. RFC 6455's client side is a handshake, a mask and a
length prefix; the parts that are genuinely hard — reconnection, correlation,
heartbeats, sequence gaps — are already solved one layer up and are not repeated
here. This file does framing and nothing else.

**What it refuses.** A plaintext `ws://` URI to anything but the loopback
interface is refused rather than downgraded. Rithmic's endpoints are `wss://`
and a credential travels on the first frame after the handshake, so a
misconfigured scheme is not a thing to be tolerant about. Loopback is permitted
because that is what the tests connect to, and a frame that never leaves the
machine is not the exposure the rule exists to stop.

**What it does not do.** No compression (`permessage-deflate` is not offered, so
no negotiated extension can arrive), no fragmentation of outbound frames, and no
text frames — the protocol above is protobuf and a text frame would mean
something has gone wrong rather than something needs decoding.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import socket
import ssl
import struct
from contextlib import suppress
from dataclasses import dataclass, field
from urllib.parse import urlsplit

#: RFC 6455 §1.3. Concatenated with the client key to derive the accept value.
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_CONTINUATION = 0x0
_TEXT = 0x1
_BINARY = 0x2
_CLOSE = 0x8
_PING = 0x9
_PONG = 0xA

#: A frame larger than this is refused rather than allocated. Rithmic's largest
#: documented messages are multi-part account and history responses in the tens
#: of kilobytes; eight megabytes is far above anything the protocol sends and far
#: below what a malformed length prefix would ask for.
MAX_FRAME = 8 * 1024 * 1024


class TransportError(Exception):
    """The socket, the handshake or the framing failed.

    Distinct from `SessionError` on purpose: the session layer catches this and
    decides whether to reconnect, and it can only do that if "the socket died"
    arrives differently from "the login was rejected".
    """


def _is_loopback(host: str) -> bool:
    if host in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass
class WebSocketTransport:
    """One WebSocket connection, synchronous, one frame at a time.

    Synchronous because the session drives four of these from its own pump and
    an event loop would have to be threaded through a state machine that does
    not otherwise need one. `receive` takes a timeout and returns `None` rather
    than blocking, which is what lets one thread service every plant.
    """

    #: Seconds to wait for the TCP connect and the handshake. Separate from the
    #: per-`receive` timeout, which the caller sets per call.
    connect_timeout: float = 20.0

    _socket: socket.socket | None = field(default=None, init=False, repr=False)
    _buffer: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _fragments: list[bytes] = field(default_factory=list, init=False, repr=False)
    _fragment_opcode: int = field(default=0, init=False, repr=False)
    _closed_by_peer: bool = field(default=False, init=False, repr=False)

    # ── connect ──────────────────────────────────────────────────────────────
    def connect(self, uri: str, *, verify: object) -> None:
        """Open the socket and complete the upgrade, or raise saying which failed.

        `verify` is an `ssl.SSLContext` to use, `True` to build a verifying
        default context, or `False` — which is refused. There is no path here
        that reaches a Rithmic endpoint without certificate verification, and
        offering one as a "troubleshooting" flag is how it ends up set.
        """
        if self._socket is not None:
            raise TransportError("this transport is already connected")

        parts = urlsplit(uri)
        if parts.scheme not in {"ws", "wss"}:
            raise TransportError(
                f"{uri.split('://')[0]}:// is not a WebSocket scheme; Rithmic's gateways "
                "are wss://"
            )
        host = parts.hostname or ""
        if not host:
            raise TransportError(f"no host in {uri!r}")
        secure = parts.scheme == "wss"
        if not secure and not _is_loopback(host):
            raise TransportError(
                f"refusing a plaintext ws:// connection to {host}: the Rithmic login "
                "frame carries the operator's password. Use wss://."
            )
        port = parts.port or (443 if secure else 80)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"

        # Settled before the socket is opened. A TLS policy checked after the
        # connect would let a misconfiguration spend a connection attempt on the
        # far end before being refused, and the refusal would read like a
        # network fault rather than a setting.
        context = self._context(verify) if secure else None

        try:
            raw = socket.create_connection((host, port), timeout=self.connect_timeout)
        except OSError as exc:
            raise TransportError(f"could not reach {host}:{port}: {exc}") from exc

        try:
            if context is not None:
                try:
                    raw = context.wrap_socket(raw, server_hostname=host)
                except ssl.SSLError as exc:
                    raise TransportError(f"TLS handshake with {host} failed: {exc}") from exc
            self._socket = raw
            self._handshake(host, port, path, secure)
        except Exception:
            self._discard()
            raise

    @staticmethod
    def _context(verify: object) -> ssl.SSLContext:
        if verify is False:
            raise TransportError(
                "certificate verification cannot be disabled for a Rithmic connection"
            )
        context = verify if isinstance(verify, ssl.SSLContext) else ssl.create_default_context()
        if context.verify_mode is ssl.CERT_NONE:
            raise TransportError(
                "the supplied SSL context does not verify certificates, and a Rithmic "
                "login frame carries the operator's password"
            )
        return context

    def _handshake(self, host: str, port: int, path: str, secure: bool) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        default_port = 443 if secure else 80
        authority = host if port == default_port else f"{host}:{port}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {authority}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        self._send_all(request)

        header = self._read_until(b"\r\n\r\n", self.connect_timeout)
        status, _, rest = header.partition(b"\r\n")
        try:
            code = int(status.split(b" ")[1])
        except (IndexError, ValueError) as exc:
            raise TransportError("the server did not answer with an HTTP status") from exc
        if code != 101:
            # The status line is the server's, and it is the useful part. The
            # body is not read: a non-101 answer to an upgrade is not something
            # to parse, and it may be arbitrarily large.
            raise TransportError(
                f"the server refused the WebSocket upgrade with HTTP {code}. "
                "Nothing was sent beyond the handshake."
            )

        headers: dict[str, str] = {}
        for line in rest.split(b"\r\n"):
            name, sep, value = line.partition(b":")
            if sep:
                headers[name.decode("latin-1").strip().lower()] = value.decode(
                    "latin-1"
                ).strip()

        expected = base64.b64encode(
            hashlib.sha1((key + _GUID).encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise TransportError(
                "the server's Sec-WebSocket-Accept did not match the key this client "
                "sent, so the connection is not the one that was opened"
            )
        if headers.get("sec-websocket-extensions"):
            # Nothing was offered, so nothing may be negotiated. A server that
            # replies with an extension anyway is framing in a way this does not
            # decode, and reading it as raw payload would be silent corruption.
            raise TransportError(
                "the server negotiated a WebSocket extension that was not offered: "
                f"{headers['sec-websocket-extensions']}"
            )

    # ── frames ───────────────────────────────────────────────────────────────
    def send(self, payload: bytes) -> None:
        """One binary frame, masked, unfragmented."""
        sock = self._require()
        header = bytearray()
        header.append(0x80 | _BINARY)
        length = len(payload)
        mask_bit = 0x80
        if length < 126:
            header.append(mask_bit | length)
        elif length < (1 << 16):
            header.append(mask_bit | 126)
            header += struct.pack("!H", length)
        else:
            header.append(mask_bit | 127)
            header += struct.pack("!Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        sock.settimeout(self.connect_timeout)
        self._send_all(bytes(header) + masked)

    def receive(self, timeout: float) -> bytes | None:
        """The next binary message, or `None` if none arrived inside `timeout`.

        Control frames are handled here and never returned: a ping is answered
        with a pong, and a close is recorded so the next call raises rather than
        returning `None` forever. A caller polling a closed socket must find out,
        and a quiet `None` reads identically to an idle connection.
        """
        deadline_reached = False
        while not deadline_reached:
            try:
                frame = self._read_frame(timeout)
            except TimeoutError:
                return None
            if frame is None:
                return None
            fin, opcode, payload = frame

            if opcode == _PING:
                self._send_control(_PONG, payload)
                continue
            if opcode == _PONG:
                continue
            if opcode == _CLOSE:
                self._closed_by_peer = True
                raise TransportError("the peer closed the WebSocket connection")
            if opcode == _TEXT:
                raise TransportError(
                    "the peer sent a text frame; this protocol carries protobuf in "
                    "binary frames, so the connection is not what it should be"
                )

            if opcode == _BINARY:
                if fin:
                    return payload
                self._fragments = [payload]
                self._fragment_opcode = _BINARY
                continue
            if opcode == _CONTINUATION:
                if not self._fragments:
                    raise TransportError("a continuation frame arrived with nothing to continue")
                self._fragments.append(payload)
                if fin:
                    whole = b"".join(self._fragments)
                    self._fragments = []
                    return whole
                continue
            raise TransportError(f"unknown WebSocket opcode {opcode:#x}")
        return None

    def close(self) -> None:
        """Send a close frame if the socket is still usable, then drop it.

        Best effort by design: `close` is called on paths where the socket is
        already gone, and raising there would turn a tidy shutdown into an
        error the desk has to explain.
        """
        sock = self._socket
        if sock is None:
            return
        if not self._closed_by_peer:
            with suppress(OSError, TransportError):
                self._send_control(_CLOSE, struct.pack("!H", 1000))
        self._discard()

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _require(self) -> socket.socket:
        if self._socket is None:
            raise TransportError("the WebSocket is not connected")
        return self._socket

    def _discard(self) -> None:
        sock, self._socket = self._socket, None
        self._buffer.clear()
        self._fragments = []
        if sock is not None:
            with suppress(OSError):
                sock.close()

    def _send_control(self, opcode: int, payload: bytes) -> None:
        sock = self._require()
        if len(payload) > 125:
            raise TransportError("a control frame payload may not exceed 125 bytes")
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        sock.settimeout(self.connect_timeout)
        self._send_all(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + masked)

    def _send_all(self, data: bytes) -> None:
        sock = self._require()
        try:
            sock.sendall(data)
        except OSError as exc:
            raise TransportError(f"the WebSocket send failed: {exc}") from exc

    def _read_frame(self, timeout: float) -> tuple[bool, int, bytes] | None:
        head = self._read_exactly(2, timeout)
        if head is None:
            return None
        first, second = head[0], head[1]
        fin = bool(first & 0x80)
        if first & 0x70:
            raise TransportError(
                "a reserved WebSocket bit is set, which means an extension this client "
                "did not negotiate"
            )
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            extended = self._read_exactly(2, timeout, required=True)
            assert extended is not None
            length = struct.unpack("!H", extended)[0]
        elif length == 127:
            extended = self._read_exactly(8, timeout, required=True)
            assert extended is not None
            length = struct.unpack("!Q", extended)[0]
        if length > MAX_FRAME:
            raise TransportError(
                f"the peer announced a {length}-byte frame, above the {MAX_FRAME}-byte "
                "ceiling this client will allocate"
            )
        mask = b""
        if masked:
            # A server must not mask (RFC 6455 §5.1), but unmasking costs
            # nothing and refusing here would drop a connection over a
            # non-problem.
            got = self._read_exactly(4, timeout, required=True)
            assert got is not None
            mask = got
        payload = b""
        if length:
            got = self._read_exactly(length, timeout, required=True)
            assert got is not None
            payload = got
        if mask:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return fin, opcode, payload

    def _read_exactly(
        self, count: int, timeout: float, *, required: bool = False
    ) -> bytes | None:
        """`count` bytes, or `None` on timeout when nothing has been consumed yet.

        `required=True` is for the tail of a frame whose header has already been
        read: there, a timeout is not "nothing arrived", it is a half-read frame,
        and returning `None` would resynchronise the stream onto a payload byte.
        """
        while len(self._buffer) < count:
            chunk = self._read_some(timeout)
            if chunk is None:
                if required:
                    raise TransportError(
                        "the peer stopped mid-frame; the stream cannot be resynchronised"
                    )
                return None
            self._buffer += chunk
        taken = bytes(self._buffer[:count])
        del self._buffer[:count]
        return taken

    def _read_some(self, timeout: float) -> bytes | None:
        sock = self._require()
        sock.settimeout(max(timeout, 0.0))
        try:
            chunk = sock.recv(65536)
        except TimeoutError:
            return None
        except ssl.SSLWantReadError:
            return None
        except OSError as exc:
            raise TransportError(f"the WebSocket read failed: {exc}") from exc
        if not chunk:
            self._closed_by_peer = True
            raise TransportError("the peer closed the connection without a close frame")
        return chunk

    def _read_until(self, marker: bytes, timeout: float) -> bytes:
        while marker not in self._buffer:
            chunk = self._read_some(timeout)
            if chunk is None:
                raise TransportError("the server did not complete the WebSocket handshake")
            self._buffer += chunk
            if len(self._buffer) > 65536:
                raise TransportError("the server's handshake response is implausibly large")
        index = self._buffer.index(marker)
        head = bytes(self._buffer[:index])
        del self._buffer[: index + len(marker)]
        return head
