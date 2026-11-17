"""The line between two Accipios on different machines: one hands its downloads to the other.

The *sender* is the PC the user clicks on; the *collector* is the PC that downloads and keeps the files. The
collector listens on a port, only on the addresses the user has ticked; the sender connects to it. Nothing here
knows about windows, rules or the queue: a ``Listener`` gives each message to a handler and sends back what the
handler answers, as ``ipc.Server`` does for the local line.

**Encryption.** TLS with a pre-shared key, from Python's own ``ssl`` module (no certificate, no new dependency).
Python has this from 3.13 on (``ssl.HAS_PSK``); ``available()`` says whether this Python can, and everything that
needs it raises ``LinkUnavailable`` with a plain sentence when it cannot. The line is pinned to TLS 1.2 with
``ECDHE-PSK-CHACHA20-POLY1305`` on purpose: in TLS 1.3 the first packet carries a check value made from the key
alone, so someone merely listening to a pairing could try every code at home. With ECDHE-PSK under TLS 1.2
a listener learns nothing, and the keys of one connection do not open another.

**Pairing.** The collector shows a twelve-character code for a few minutes (``Node.begin_pairing``). The sender connects with
a key made from that code by a slow, memory-hard function (scrypt, salted with a random value the collector sends
as the TLS identity hint). Inside that first connection each side gives half of a long random key; from then on
only the long key is used. A code works once, runs out after ``PAIRING_SECONDS`` and is dropped after
``PAIRING_TRIES`` connections, right or wrong.

**Keys.** A paired Accipio's long key is kept in the system keyring (``SystemKeys``, through redactor_common's
secret store, never its unencrypted file). ``link.json`` in the config folder holds only what is not secret:
this Accipio's id and name, and the id, name and address of each paired one.

**Messages** are small dicts sent as JSON behind a four-byte length; every message gets one dict back. Each has
``v`` (``PROTOCOL``) and ``type``; see ``hello``, ``add`` and ``status``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import ssl
import struct
import sys
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional, Tuple

from accipio.core.settings import write_atomically
from accipio.core.version import APP_VERSION

PROTOCOL = 1
DEFAULT_PORT = 47610
TIMEOUT = 10.0                        # seconds for connecting, for the handshake and for each answer
MAX_MESSAGE = 1024 * 1024             # bytes; a link with its cookies is a few KiB
CIPHER = "ECDHE-PSK-CHACHA20-POLY1305"

# Twelve characters (the user's pick, 2026-11-17, over six digits): about 59 bits, so nobody who gets in between
# the two at the first connection can try them all, slow hash or not. No 0/O, 1/I/L: nothing to misread.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 12
CODE_GROUP = 4                        # shown as K7QM-2XRD-9FHP
PAIRING_SECONDS = 300.0
PAIRING_TRIES = 3
SCRYPT_N = 2 ** 15                    # with r=8: 32 MiB and about a tenth of a second for each try of a code
PAIR_IDENTITY = "accipio-pair"        # the TLS identity of a sender that knows only the code
PAIR_HINT = "accipio-pair:"           # + the salt in hex: the collector's identity hint while a code is shown

Handler = Callable[[Dict, "Peer"], Dict]


class LinkError(OSError):
    """Anything that stopped a message from getting through. The text is a plain sentence for the user."""


class LinkUnavailable(LinkError):
    """This Python cannot do the encryption, or there is no keyring to keep a key in."""


class Away(LinkError):
    """The other Accipio could not be reached (PC off, not listening, wrong address): worth trying again later."""


class Refused(LinkError):
    """The other Accipio was reached and said no: it does not know this one (any more), or the key differs."""


class WrongCode(Refused):
    """Pairing: the code was wrong, has run out, was used already, or no code is being shown."""


def available() -> bool:
    return bool(getattr(ssl, "HAS_PSK", False))


def why_unavailable() -> str:
    """Empty when the link can work here, else the sentence to show."""
    if available():
        return ""
    return ("Sending downloads to another Accipio needs Python 3.13 or newer (this is Python "
            f"{sys.version_info.major}.{sys.version_info.minor}, whose ssl module has no pre-shared keys).")


def _need_psk() -> None:
    if not available():
        raise LinkUnavailable(why_unavailable())


# -- keys ------------------------------------------------------------------------------------------------------

class SystemKeys:
    """The system keyring (Windows Credential Manager, macOS Keychain, Secret Service). No fallback to a file:
    without a keyring there is no link, and the error says so."""

    APP = "accipio"

    def get(self, name: str) -> str:
        from redactor_common.core import secret_store
        return secret_store.get_secret(self.APP, name)

    def set(self, name: str, value: str) -> None:
        from redactor_common.core import secret_store
        try:
            secret_store.set_secret(self.APP, name, value, allow_unencrypted_fallback=False)
        except secret_store.SecretStoreUnavailable:
            raise LinkUnavailable("The key for the other Accipio could not be saved: this computer's credential "
                                  "store is not available.") from None

    def delete(self, name: str) -> None:
        from redactor_common.core import secret_store
        try:
            secret_store.delete_secret(self.APP, name)
        except secret_store.SecretStoreUnavailable:
            pass                                          # nothing was removed; the peer is forgotten all the same


def _key_name(peer_id: str) -> str:
    return f"link-key-{peer_id}"


# -- who we are and whom we know -------------------------------------------------------------------------------

@dataclass
class Peer:
    """A paired Accipio. ``host`` and ``port`` are where it listens; empty for one that only sends to us."""
    id: str
    name: str
    host: str = ""
    port: int = 0


@dataclass
class _Pairing:
    code: str
    salt: bytes
    key: bytes
    until: float
    tries: int = 0


def tidy_code(text: str) -> str:
    """A typed code without the spaces and dashes people put in, in capitals."""
    return "".join(ch for ch in str(text).upper() if ch.isalnum())


def show_code(code: str) -> str:
    """The code as it is shown and read aloud: in groups, with dashes."""
    code = tidy_code(code)
    return "-".join(code[i:i + CODE_GROUP] for i in range(0, len(code), CODE_GROUP))


def code_key(code: str, salt: bytes) -> bytes:
    """The key of the pairing connection. Slow and memory-hard on purpose: a short code is all that goes in."""
    return hashlib.scrypt(tidy_code(code).encode(), salt=b"accipio-link-pair-v1" + salt, n=SCRYPT_N, r=8, p=1,
                          maxmem=256 * SCRYPT_N * 8, dklen=32)


def _long_key(sender_half: bytes, collector_half: bytes, sender_id: str, collector_id: str) -> bytes:
    return hashlib.sha256(b"accipio-link-key-v1\0" + sender_half + collector_half
                          + sender_id.encode() + b"\0" + collector_id.encode()).digest()


class Node:
    """This Accipio's side of the link: its id and name, the Accipios it is paired with, their keys, and the
    code while one is being shown. ``keys`` is the key store (tests pass a stand-in, never the real keyring)."""

    def __init__(self, folder: str, keys=None, clock: Callable[[], float] = time.monotonic):
        self.folder = folder
        self.keys = keys if keys is not None else SystemKeys()
        self.clock = clock
        self._lock = threading.RLock()
        self._pairing: Optional[_Pairing] = None
        self._state = self._load()

    # the file
    def _path(self) -> str:
        return os.path.join(self.folder, "link.json")

    def _load(self) -> Dict:
        state: Dict = {}
        try:
            with open(self._path(), encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        peers = [p for p in state.get("peers", []) if isinstance(p, dict) and p.get("id")] \
            if isinstance(state.get("peers"), list) else []
        clean = {"id": str(state.get("id") or ""), "name": str(state.get("name") or ""), "peers": peers}
        if not clean["id"] or not clean["name"]:
            clean["id"] = clean["id"] or secrets.token_hex(8)
            clean["name"] = clean["name"] or (socket.gethostname() or "Accipio")
            self._save(clean)
        return clean

    def _save(self, state: Optional[Dict] = None) -> None:
        os.makedirs(self.folder, exist_ok=True)
        write_atomically(self._path(), json.dumps(state if state is not None else self._state, indent=2,
                                                  ensure_ascii=False))

    # who we are
    @property
    def id(self) -> str:
        return self._state["id"]

    @property
    def name(self) -> str:
        return self._state["name"]

    def rename(self, name: str) -> None:
        """The name the other Accipios show for this one. They learn a new name at the next pairing."""
        name = " ".join(str(name).split())
        if name:
            with self._lock:
                self._state["name"] = name
                self._save()

    # whom we know
    def peers(self) -> List[Peer]:
        with self._lock:
            return [_peer_from(p) for p in self._state["peers"]]

    def peer(self, peer_id: str) -> Optional[Peer]:
        for known in self.peers():
            if known.id == peer_id:
                return known
        return None

    def key_of(self, peer_id: str) -> bytes:
        """The long key shared with a paired Accipio; empty when there is none."""
        if not peer_id or self.peer(peer_id) is None:
            return b""
        try:
            return bytes.fromhex(self.keys.get(_key_name(peer_id)) or "")
        except ValueError:
            return b""

    def remember(self, peer: Peer, key: bytes) -> None:
        """Pair with ``peer``: the key goes to the keyring first, so a list entry never lacks its key."""
        self.keys.set(_key_name(peer.id), key.hex())
        with self._lock:
            self._state["peers"] = [p for p in self._state["peers"] if p.get("id") != peer.id] + [asdict(peer)]
            self._save()

    def forget(self, peer_id: str) -> None:
        """End the pairing from this side. The other Accipio is refused from now on."""
        with self._lock:
            self._state["peers"] = [p for p in self._state["peers"] if p.get("id") != peer_id]
            self._save()
        self.keys.delete(_key_name(peer_id))

    # the code (collector)
    def begin_pairing(self) -> str:
        """A new code to show (already in groups: ``K7QM-2XRD-9FHP``); any earlier one stops working."""
        _need_psk()
        code = show_code("".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH)))
        salt = secrets.token_bytes(16)
        pairing = _Pairing(code, salt, code_key(code, salt), self.clock() + PAIRING_SECONDS)
        with self._lock:
            self._pairing = pairing
        return code

    def cancel_pairing(self) -> None:
        with self._lock:
            self._pairing = None

    def pairing_left(self) -> float:
        """Seconds the shown code still works; 0 when none is shown (run out, used, or tried too often)."""
        pairing = self._live_pairing()
        return max(0.0, pairing.until - self.clock()) if pairing else 0.0

    def _live_pairing(self) -> Optional[_Pairing]:
        with self._lock:
            pairing = self._pairing
            if pairing is not None and (self.clock() >= pairing.until or pairing.tries >= PAIRING_TRIES):
                self._pairing = pairing = None
            return pairing

    def _try_pairing(self) -> Optional[_Pairing]:
        """Called when a connection asks for the pairing key: every such connection uses up one try, before
        anyone knows whether its code is right, so many guesses at once count like many in a row."""
        with self._lock:
            pairing = self._pairing
            if pairing is None or self.clock() >= pairing.until or pairing.tries >= PAIRING_TRIES:
                self._pairing = None
                return None
            pairing.tries += 1
            return pairing

    def _use_pairing(self, pairing: _Pairing) -> bool:
        """True for the one connection that gets to use the code."""
        with self._lock:
            if self._pairing is not pairing:
                return False
            self._pairing = None
            return True

    def _hint(self) -> str:
        pairing = self._live_pairing()
        return PAIR_HINT + pairing.salt.hex() if pairing else self.id


def _peer_from(data: Dict) -> Peer:
    try:
        port = int(data.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    return Peer(id=str(data.get("id") or ""), name=str(data.get("name") or ""), host=str(data.get("host") or ""),
                port=port)


# -- messages --------------------------------------------------------------------------------------------------

def hello() -> Dict:
    """Are you there, and who are you? Answered by the listener itself: ok, id, name, version."""
    return {"v": PROTOCOL, "type": "hello"}


def add(url: str, *, page_url: str = "", referrer: str = "", user_agent: str = "", cookies: str = "") -> Dict:
    """Download this for me. The fields are the browser extension's ``add`` fields, so the collector can queue
    it the same way; empty ones are left out. The handler's answer carries the collector's ``id`` for it."""
    message = {"v": PROTOCOL, "type": "add", "url": url}
    for name, value in (("page_url", page_url), ("referrer", referrer), ("user_agent", user_agent),
                        ("cookies", cookies)):
        if value:
            message[name] = value
    return message


def status(ids: List[int]) -> Dict:
    """How did these go? ``ids`` are the ones the collector answered ``add`` with."""
    return {"v": PROTOCOL, "type": "status", "ids": list(ids)}


def problem_with(message) -> str:
    """Empty for a message a handler can trust the shape of, else what is wrong with it."""
    if not isinstance(message, dict):
        return "not a message"
    if message.get("v") != PROTOCOL:
        return (f"the other Accipio speaks version {message.get('v')!r} of the link and this one version {PROTOCOL}; "
                "update the older one")
    kind = message.get("type")
    if kind == "hello":
        return ""
    if kind == "add":
        if not isinstance(message.get("url"), str) or not message["url"].strip():
            return "no link in the message"
        for name in ("page_url", "referrer", "user_agent", "cookies"):
            if not isinstance(message.get(name, ""), str):
                return f"{name} is not text"
        return ""
    if kind == "status":
        ids = message.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            return "ids is not a list of numbers"
        return ""
    return f"unknown message {kind!r}"


def _write(connection, message: Dict) -> None:
    data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_MESSAGE:
        raise LinkError(f"The message is too large for the link ({len(data)} bytes).")
    connection.sendall(struct.pack(">I", len(data)) + data)


def _exactly(connection, count: int) -> bytes:
    parts = []
    while count:
        part = connection.recv(min(count, 65536))
        if not part:
            raise EOFError("the other side closed the line")
        parts.append(part)
        count -= len(part)
    return b"".join(parts)


def _read(connection) -> Dict:
    (size,) = struct.unpack(">I", _exactly(connection, 4))
    if size > MAX_MESSAGE:
        raise ValueError("message too large")
    message = json.loads(_exactly(connection, size).decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("not a message")
    return message


# -- the encrypted line ----------------------------------------------------------------------------------------

def _context(server: bool) -> ssl.SSLContext:
    _need_psk()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE                   # there are no certificates: the shared key is the proof
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2      # see the module docstring
    context.set_ciphers(CIPHER)
    return context


def local_addresses() -> List[str]:
    """This PC's own addresses, for the list the user ticks. Link-local IPv6 ones are left out (they need an
    interface name to be reached); 127.0.0.1 is always there, for two Accipios on one PC."""
    found = ["127.0.0.1"]
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, type=socket.SOCK_STREAM)
    except OSError:
        infos = []
    for info in infos:
        address = str(info[4][0])
        if address.lower().startswith("fe80") or "%" in address or address in found:
            continue
        found.append(address)
    return found


class Listener:
    """The collector's open door. Listens on ``addresses`` only (the ones the user has ticked; none ticked =
    nothing listens) and answers each message from a paired Accipio with ``handler(message, peer)``.
    ``problems`` names every address that could not be listened on, with the reason."""

    def __init__(self, node: Node, handler: Handler, addresses: List[str], port: int = DEFAULT_PORT):
        self.node = node
        self.handler = handler
        self.addresses = list(addresses)
        self.port = port
        self.bound: List[Tuple[str, int]] = []
        self.problems: Dict[str, str] = {}
        self._sockets: List[socket.socket] = []
        self._threads: List[threading.Thread] = []
        self._stopping = False

    def start(self) -> "Listener":
        _need_psk()
        for address in self.addresses:
            try:
                family, kind, proto, _, where = socket.getaddrinfo(
                    address, self.port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE | socket.AI_NUMERICHOST)[0]
                listening = socket.socket(family, kind, proto)
                try:
                    if sys.platform == "win32":           # nobody else may take the same address and port beside us
                        listening.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    listening.bind(where)
                    listening.listen(16)
                    listening.settimeout(0.5)             # so stop() is noticed without a wake-up connection
                except OSError:
                    listening.close()
                    raise
            except OSError as error:
                self.problems[address] = str(error)
                continue
            self._sockets.append(listening)
            self.bound.append((address, listening.getsockname()[1]))
            thread = threading.Thread(target=self._serve, args=(listening,), name="accipio-link", daemon=True)
            thread.start()
            self._threads.append(thread)
        return self

    def _serve(self, listening: socket.socket) -> None:
        while not self._stopping:
            try:
                connection, _ = listening.accept()
            except TimeoutError:
                continue
            except OSError:                               # the socket was closed by stop()
                return
            # each on a thread of its own: one that stalls in the handshake blocks no one after it
            threading.Thread(target=self._answer, args=(connection,), daemon=True).start()

    def _answer(self, connection: socket.socket) -> None:
        claimed: Dict = {}

        def key_for(identity):
            claimed["identity"] = identity or ""
            if identity == PAIR_IDENTITY:
                claimed["pairing"] = self.node._try_pairing()
                key = claimed["pairing"].key if claimed["pairing"] else b""
            else:
                key = self.node.key_of(identity or "")
            return key or secrets.token_bytes(32)         # a key nobody has: refused like any wrong key

        try:
            connection.settimeout(TIMEOUT)
            context = _context(server=True)
            context.set_psk_server_callback(key_for, self.node._hint())
            line = context.wrap_socket(connection, server_side=True)
        except (OSError, ValueError):                     # a wrong key or code, a stranger, a stalled client
            connection.close()
            return
        with line:
            try:
                if claimed.get("identity") == PAIR_IDENTITY:
                    self._pair(line, claimed.get("pairing"))
                    return
                peer = self.node.peer(claimed.get("identity", ""))
                while peer is not None:
                    message = _read(line)
                    _write(line, self._reply(message, peer))
            except (EOFError, OSError, ValueError):
                pass                                      # the sender hung up, went silent or sent rubbish

    def _reply(self, message: Dict, peer: Peer) -> Dict:
        problem = problem_with(message)
        if problem:
            return {"ok": False, "error": problem, "v": PROTOCOL}
        if message["type"] == "hello":
            return {"ok": True, "id": self.node.id, "name": self.node.name, "version": APP_VERSION, "v": PROTOCOL}
        try:
            return self.handler(message, peer)
        except Exception as error:                        # the handler's trouble is the sender's answer, not a crash
            return {"ok": False, "error": str(error)}

    def _pair(self, line, pairing: Optional[_Pairing]) -> None:
        """The first connection of a sender that knew the code: swap names and the halves of the long key."""
        message = _read(line)
        sender_id, sender_name = str(message.get("id") or ""), " ".join(str(message.get("name") or "").split())
        try:
            sender_half = base64.b64decode(str(message.get("half") or ""), validate=True)
        except ValueError:
            sender_half = b""
        if message.get("type") != "pair" or message.get("v") != PROTOCOL or not sender_id or len(sender_half) != 32:
            _write(line, {"ok": False, "error": "not a pairing message this Accipio understands"})
            return
        if sender_id == self.node.id:
            _write(line, {"ok": False, "error": "an Accipio cannot be paired with itself"})
            return
        if pairing is None or not self.node._use_pairing(pairing):
            _write(line, {"ok": False, "error": "the code has been used already"})
            return
        half = secrets.token_bytes(32)
        try:
            self.node.remember(Peer(id=sender_id, name=sender_name or sender_id),
                               _long_key(sender_half, half, sender_id, self.node.id))
        except LinkError as error:
            _write(line, {"ok": False, "error": str(error)})
            return
        _write(line, {"ok": True, "id": self.node.id, "name": self.node.name,
                      "half": base64.b64encode(half).decode("ascii")})

    def stop(self) -> None:
        self._stopping = True
        for listening in self._sockets:
            try:
                listening.close()
            except OSError:
                pass
        for thread in self._threads:
            thread.join(2)
        self._sockets, self._threads, self.bound = [], [], []


def _connect(host: str, port: int, timeout: float, key_for, who: str):
    """An encrypted connection, or the LinkError that says why not."""
    context = _context(server=False)
    context.set_psk_client_callback(key_for)
    try:
        plain = socket.create_connection((host, port), timeout=timeout)
    except OSError as error:
        raise Away(f"{who} could not be reached at {host}, port {port} ({error}).") from None
    try:
        plain.settimeout(timeout)
        return context.wrap_socket(plain)
    except TimeoutError:
        plain.close()
        raise Away(f"{who} did not answer within {timeout:.0f} seconds.") from None
    except OSError:                                       # ssl.SSLError too: the keys differ, or it hung up on us
        plain.close()
        raise Refused(f"{who} refused the connection: it is not paired with this Accipio (any more). "
                      "Pair the two again.") from None


class Line:
    """An open, encrypted connection from this Accipio to a paired one that listens. ``ask`` sends one message
    and returns its answer; several may follow each other on the same line."""

    def __init__(self, node: Node, peer: Peer, timeout: float = TIMEOUT):
        key = node.key_of(peer.id)
        if not key:
            raise Refused(f"This Accipio is not paired with {peer.name or peer.id}.")
        if not peer.host or not peer.port:
            raise Away(f"No address is known for {peer.name or peer.id}.")
        self.peer = peer
        self._line = _connect(peer.host, peer.port, timeout, lambda hint: (node.id, key), peer.name or peer.id)

    def ask(self, message: Dict) -> Dict:
        try:
            _write(self._line, message)
            return _read(self._line)
        except LinkError:
            raise
        except TimeoutError:
            raise Away(f"{self.peer.name or self.peer.id} did not answer in time.") from None
        except (EOFError, OSError, ValueError) as error:
            raise Away(f"The line to {self.peer.name or self.peer.id} broke ({error}).") from None

    def close(self) -> None:
        try:
            self._line.close()
        except OSError:
            pass

    def __enter__(self) -> "Line":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def send(node: Node, peer: Peer, message: Dict, timeout: float = TIMEOUT) -> Dict:
    """One message to a paired Accipio, and its answer. Raises ``Away`` when it cannot be reached (try again
    later), ``Refused`` when it no longer knows this Accipio, ``LinkUnavailable`` when this Python cannot."""
    with Line(node, peer, timeout) as line:
        return line.ask(message)


def pair(node: Node, host: str, port: int, code: str, timeout: float = TIMEOUT) -> Peer:
    """The sender's half of pairing: connect to the collector at ``host`` with the code it shows, agree on the
    long key and remember the collector. Raises ``WrongCode`` when the collector does not accept the code."""
    _need_psk()
    seen: Dict = {}

    def key_for(hint):
        hint = hint or ""
        seen["showing"] = hint.startswith(PAIR_HINT)
        try:
            salt = bytes.fromhex(hint[len(PAIR_HINT):]) if seen["showing"] else b""
        except ValueError:
            salt, seen["showing"] = b"", False
        # no code shown over there: a key nobody has, so the handshake fails and the reason is told below
        return PAIR_IDENTITY, (code_key(code, salt) if seen["showing"] else secrets.token_bytes(32))

    who = f"The Accipio at {host}"
    try:
        line = _connect(host, port, timeout, key_for, who)
    except Refused:
        if seen.get("showing") is False:
            raise WrongCode(f"{who} is not showing a pairing code right now. Ask it for a new one.") from None
        raise WrongCode(f"{who} did not accept the code. Check it and type it again; after "
                        f"{PAIRING_TRIES} tries it needs a new code.") from None
    half = secrets.token_bytes(32)
    with line:
        try:
            _write(line, {"v": PROTOCOL, "type": "pair", "id": node.id, "name": node.name,
                          "half": base64.b64encode(half).decode("ascii")})
            reply = _read(line)
        except (EOFError, OSError, ValueError) as error:
            raise Away(f"The line to {host} broke during pairing ({error}). Ask it for a new code.") from None
    if not reply.get("ok"):
        raise WrongCode(f"{who} said no: {reply.get('error') or 'no reason given'}.")
    try:
        collector_half = base64.b64decode(str(reply.get("half") or ""), validate=True)
    except ValueError:
        collector_half = b""
    collector_id = str(reply.get("id") or "")
    if not collector_id or len(collector_half) != 32:
        raise Refused(f"{who} gave an answer this Accipio does not understand.")
    peer = Peer(id=collector_id, name=" ".join(str(reply.get("name") or "").split()) or host, host=host, port=port)
    node.remember(peer, _long_key(half, collector_half, node.id, collector_id))
    return peer
