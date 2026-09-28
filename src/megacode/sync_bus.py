"""SyncBus: the cross-window link between separately launched MegaCode windows.

TOPOLOGY: a star relay over one local socket (QLocalServer on the hub,
QLocalSocket on every other instance) -- a Windows named pipe via Qt, an
AF_UNIX socket file on Linux (absolute path under the per-user runtime dir,
so the 0700 directory is the access boundary the pipe DACL provided).
The hub is elected with a QLockFile
-- NEVER by listen-failure: on Windows two QLocalServer.listen(sameName)
calls BOTH succeed (verified on PySide6 6.11.1), so listen failure cannot
mean "name taken". A hung-but-alive hub is never usurped (the OS byte-range
lock holds until the process dies); correctness over availability.

IDENTITY: one bus per process, ``wid`` = uuid4().hex[:12]. Display names
("W2", "W3", ...) are assigned by the hub, monotonic per link era; the hub
is W1.

CRITICAL INVARIANT (loop-back suppression): the bus NEVER emits ``incoming``
for a message whose ``src`` is its own window id. Origin-side effects belong
to the origin's own call path -- the local mirror and the local flash have
already happened by the time publish_* is called. Clients' messages reach
the HUB's workspace via ``incoming``; the hub's messages reach the clients
via the relay. Breaking this double-delivers keystrokes and double-flashes
the sync toggle.

The hot path is fire-and-forget: ``input`` frames are relayed as the
ORIGINAL RAW BYTES (no re-encode) and never acknowledged. Local delivery
always happened first; a re-election gap drops in-flight keys by design.

This module never imports workspace; WorkspaceView sees only the LinkPort
surface (NullLink is the no-ops default that guarantees the unlinked app
stays byte-identical).
"""

from __future__ import annotations

import hmac
import logging
import os
import random
import secrets as pysecrets
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import plat_helpers
from . import sync_protocol as proto
from . import sync_security
from .remote_registry import RemoteRegistry

log = logging.getLogger("megacode")

#: statusChanged strings (transitions only -- no per-tick churn)
ST_LINKING = "relinking…"
ST_DEGRADED = "link degraded (hub not responding)"
ST_FULL = "link full (16 windows)"
ST_ELEVATED = "window linking off (elevated)"
ST_NETWORK_PROFILE = "window linking off (network profile)"
ST_NO_STATE_DIR = "window linking off (no link state folder)"
ST_NO_LISTEN = "window linking off (socket unavailable)"
ST_PROTO_MISMATCH = "window linking off (other MegaCode is older)"

# roles
_OFF = "off"                # never started / killed by the user
_ELECTING = "electing"
_HUB = "hub"
_CLIENT = "client"          # connected or connecting
_RECONNECTING = "reconnecting"
_SESSION_OFF = "session_off"  # rejected with bye("full"); no probing until re-toggle


def _abort(sock) -> None:
    """Abort a socket whose C++ side may already be gone (teardown races)."""
    try:
        if sock is not None:
            sock.abort()
    except RuntimeError:
        pass


def _lock_holder_pid(lock: QLockFile, lock_path: Path) -> int:
    """The hub.lock holder's pid, or 0 when unknown.

    PySide6 changed QLockFile.getLockInfo's shape across releases: 6.6+
    returns ``(pid, hostname, appname)`` from a zero-arg call, while 6.5
    (the Astra Linux pin) still takes the three C++ out-params and cannot
    report them back from Python. Read the lock file itself in that case:
    QLockFile writes plain ``pid\\nappname\\nhostname\\n`` (stable since
    Qt 5.1), which no binding can take away.
    """
    try:
        info = lock.getLockInfo()
        if isinstance(info, tuple) and info:
            return int(info[0])
    except (TypeError, RuntimeError):
        pass
    try:
        first = lock_path.read_text(
            encoding="utf-8", errors="replace").split("\n", 1)[0]
        return int(first)
    except (OSError, ValueError):
        return 0


class NullLink(QObject):
    """The default port: every method is a no-op, the registry is empty,
    no signal ever fires. This object IS the unlinked-app contract."""

    incoming = Signal(object)      # a validated remote message (input/sync/run)
    syncAdopted = Signal(bool, str)  # welcome-time adoption, state-only, no flash
    rosterChanged = Signal()
    peersChanged = Signal(int)     # other linked windows
    statusChanged = Signal(str)

    def start(self) -> None:
        return None

    def stop(self, reason: str = "kill") -> None:  # noqa: ARG002
        return None

    def is_alive(self) -> bool:
        return False

    def enabled(self) -> bool:
        return False

    def publish_input(self, pane_id: str, tags: List[str],  # noqa: ARG002
                      seq: str, pasted: bool,
                      all_mode: bool = False) -> bool:  # noqa: ARG002
        return False

    def publish_digest(self, panes: List[Dict]) -> None:  # noqa: ARG002
        return None

    def publish_sync(self, on: bool, tag: str = "") -> None:  # noqa: ARG002
        return None

    def publish_run(self, tag: str, cmd: str) -> bool:  # noqa: ARG002
        return False

    def registry(self) -> RemoteRegistry:
        return self._registry

    def __init__(self) -> None:
        super().__init__()
        self._registry = RemoteRegistry()


class _Peer:
    """Hub-side bookkeeping for one client socket."""

    __slots__ = ("sock", "buf", "sid", "wid", "name", "digest",
                 "tokens", "refill", "helloed")

    def __init__(self, sock: QLocalSocket) -> None:
        self.sock = sock
        self.buf = bytearray()
        self.sid = ""
        self.wid = ""
        self.name = ""
        self.digest: List[Dict] = []
        self.tokens = float(proto.RATE_BURST)
        self.refill = time.monotonic()
        self.helloed = False


class SyncBus(QObject):
    """One process's link endpoint. See the module docstring for topology."""

    incoming = Signal(object)
    syncAdopted = Signal(bool, str)
    rosterChanged = Signal()
    peersChanged = Signal(int)
    statusChanged = Signal(str)

    def __init__(
        self,
        state_dir: Path,
        *,
        name_suffix: str = "",
        jitter_fn: Optional[Callable[[], int]] = None,
        hello_timeout_ms: int = proto.HELLO_TIMEOUT_MS,
        ping_ms: int = proto.PING_MS,
        degrade_ms: int = proto.DEGRADE_MS,
        backoff: Optional[List[int]] = None,
    ) -> None:
        super().__init__()
        self._dir = Path(state_dir)
        # the test seam: distinct rendezvous names for parallel bus groups
        # inside one pytest process; production passes ""
        self._pipe = plat_helpers.socket_path(
            proto.pipe_name(
                plat_helpers.user_name(),
                plat_helpers.process_session_id(),
            ) + name_suffix
        )
        self._jitter_fn = jitter_fn or (lambda: random.randint(
            0, proto.ELECTION_JITTER_MS))
        self._hello_timeout_ms = hello_timeout_ms
        self._backoff = list(backoff or proto.RECONNECT_BACKOFF_MS)
        self._wid = uuid.uuid4().hex[:12]
        self._role = _OFF
        self._registry = RemoteRegistry()
        # consecutive hub listen() failures (Unix-only failure mode)
        self._listen_fail = 0

        # hub state
        self._lock = None  # QLockFile while we hold the election
        self._server: Optional[QLocalServer] = None
        self._peers: Dict[QLocalSocket, _Peer] = {}
        self._roster: Dict[str, Dict] = {}  # client wid -> {id, name, panes}
        self._name_counter = 1  # the hub is W1
        self._group_sync = False
        self._group_sync_tag = ""  # the armed scope ("" = all windows)
        self._secret = b""
        self._epoch = ""

        # client state
        self._csock: Optional[QLocalSocket] = None
        self._cbuf = bytearray()
        self._csid = ""
        self._helloed = False
        self._backoff_i = 0
        self._epoch_retried = False
        self._degraded = False
        self._last_line = 0.0

        # shared
        self._own_digest: List[Dict] = []
        self._last_sent_digest: Optional[List[Dict]] = None

        self._ping_timer = QTimer(self)
        self._ping_timer.setInterval(ping_ms)
        self._ping_timer.timeout.connect(self._on_ping_tick)
        self._degrade_deadline_ms = degrade_ms

    # --- LinkPort surface ------------------------------------------------------
    def registry(self) -> RemoteRegistry:
        return self._registry

    def enabled(self) -> bool:
        return self._role != _OFF

    def is_alive(self) -> bool:
        if self._role == _HUB:
            return True
        return self._role == _CLIENT and self._helloed

    def start(self) -> None:
        """Bring the link up (async: election happens on the next loop turn).

        Hard gates live here -- env var, elevation, network-profile state
        dir, unwritable state dir. The persistent settings preference is
        MainWindow's gate, not this one: the toolbar chip must always be
        able to re-link for the session. _SESSION_OFF (bye("full") /
        proto mismatch) also restarts here -- the chip is the promised way
        back, and the cap or the mismatching neighbor may be gone by now.
        """
        if self._role not in (_OFF, _SESSION_OFF):
            return
        if os.environ.get("MEGACODE_NO_LINK"):
            return
        if plat_helpers.is_network_dir(str(self._dir)):
            self.statusChanged.emit(ST_NETWORK_PROFILE)
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            if sys.platform != "win32":
                # Debian-era $HOME is 0755 by default; the state dir itself
                # is the boundary that the Windows profile ACL used to be
                try:
                    os.chmod(self._dir, 0o700)
                except OSError:
                    pass
        except OSError:
            log.info("sync state dir unavailable: %s", self._dir)
            self.statusChanged.emit(ST_NO_STATE_DIR)
            return
        prefs = sync_security.settings_load(self._dir)
        if plat_helpers.is_process_elevated() and not prefs.get(
                "link_windows_forced"):
            self.statusChanged.emit(ST_ELEVATED)
            return
        self._role = _ELECTING
        QTimer.singleShot(0, self._elect)

    def stop(self, reason: str = "kill") -> None:
        """Tear the link down (bye frame best-effort, one shared flush)."""
        if self._role == _OFF:
            return
        # The role flips FIRST: abort() fires `disconnected` synchronously on
        # Windows, and _on_server_gone / _on_client_error must not re-arm the
        # bus (role -> _RECONNECTING, "relinking…" status) mid-stop -- the
        # workspace chip would read it as "still linking" and a re-toggle
        # would no-op on an already-checked button.
        self._role = _OFF
        bye = proto.encode({"v": proto.PROTO, "t": "bye", "src": self._wid,
                            "reason": reason})
        sockets: List[QLocalSocket] = list(self._peers)
        if self._csock is not None:
            sockets.append(self._csock)
        for sock in sockets:
            try:
                sock.write(bye)
            except RuntimeError:  # C++ object already gone
                pass
        # shutdown-only blocking: ONE shared 150 ms budget across all
        # sockets, and none at all for a socket with nothing pending
        deadline = time.monotonic() + 0.150
        for sock in sockets:
            try:
                if sock.bytesToWrite() > 0:
                    sock.waitForBytesWritten(
                        max(1, int((deadline - time.monotonic()) * 1000)))
                sock.disconnectFromServer()
            except RuntimeError:
                pass
        self._teardown_sockets()
        self._registry.clear()
        self._roster.clear()
        # _own_digest is the WORKSPACE's pane table (the bus is only the
        # courier): keeping it means a kill-switch -> re-link hello carries
        # the real panes again -- the workspace publishes on tile events, so
        # nothing else would re-announce them after a restart.
        self._last_sent_digest = None
        self.rosterChanged.emit()
        self.peersChanged.emit(0)

    # --- publish API (called by the workspace) ----------------------------------
    def publish_input(self, pane_id: str, tags: List[str], seq: str,
                      pasted: bool, all_mode: bool = False) -> bool:
        """One keystroke/paste to the group. False when it must not cross
        (oversized payload or not linked) -- the workspace then flashes.

        ``all_mode`` (the left-click sync arm) tells receivers to bypass the
        domain rule and deliver to every live pane; the wire key is set only
        when true, so every other frame stays byte-identical to before.

        Two caps, both here: MAX_SEQ bounds the payload in chars (the
        receiver's grammar re-checks it), MAX_LINE bounds the encoded frame
        in bytes (non-ASCII and VT controls grow several-fold under JSON
        escaping, so the two caps trigger at different payloads).
        """
        if len(seq) > proto.MAX_SEQ:
            return False
        frame = {
            "v": proto.PROTO, "t": "input", "src": self._wid,
            "pane": pane_id, "tags": list(tags), "seq": seq, "paste": pasted,
        }
        if all_mode:
            frame["all"] = True
        raw = proto.encode(frame)
        if len(raw) > proto.MAX_LINE:
            return False
        if self._role == _HUB:
            self._relay_raw(None, raw)  # origin = self -> every peer, no loop-back
            return True
        if self._role == _CLIENT and self._helloed and self._csock is not None:
            self._csock.write(raw)
            self._csock.flush()
            return True
        return False

    def publish_digest(self, panes: List[Dict]) -> None:
        """Announce our pane table. ALWAYS cached (hello/re-hello/hub roster
        row read it), the wire send is connection-gated and diff-suppressed.
        The hub does not relay a digest frame -- clients learn its row from
        the roster broadcast (its rows are built from _own_digest)."""
        self._own_digest = panes
        if panes == self._last_sent_digest:
            return
        self._last_sent_digest = panes
        if self._role == _HUB:
            self._broadcast_roster()
        elif self._role == _CLIENT and self._helloed and self._csock is not None:
            self._csock.write(proto.encode(
                {"v": proto.PROTO, "t": "digest", "src": self._wid,
                 "panes": panes}))
            self._csock.flush()

    def publish_sync(self, on: bool, tag: str = "") -> None:
        """Replicate the ONE global sync state (last-writer-wins via hub).

        ``tag`` is the right-click menu's group choice; "" means the
        all-windows arm (every pane in every linked window). Optional on
        the wire, so an older same-proto peer simply keeps its own
        unscoped semantics for an absent field."""
        if self._role == _HUB:
            self._group_sync = on
            self._group_sync_tag = tag
            self._relay_raw(None, proto.encode(
                {"v": proto.PROTO, "t": "sync", "src": self._wid, "on": on,
                 "tag": tag}))
        elif self._role == _CLIENT and self._helloed and self._csock is not None:
            self._csock.write(proto.encode(
                {"v": proto.PROTO, "t": "sync", "src": self._wid, "on": on,
                 "tag": tag}))
            self._csock.flush()

    def publish_run(self, tag: str, cmd: str) -> bool:
        """A scoped "@tag" run-all crossing windows. False = oversize."""
        raw = proto.encode({
            "v": proto.PROTO, "t": "run", "src": self._wid,
            "tag": tag, "cmd": cmd,
        })
        if len(raw) > proto.MAX_LINE:
            return False
        if self._role == _HUB:
            self._relay_raw(None, raw)
            return True
        if self._role == _CLIENT and self._helloed and self._csock is not None:
            self._csock.write(raw)
            self._csock.flush()
            return True
        return False

    # --- election ---------------------------------------------------------------
    def _elect(self) -> None:
        if self._role not in (_ELECTING, _RECONNECTING):
            return
        lock = QLockFile(str(self._dir / "hub.lock"))
        lock.setStaleLockTime(0)
        if lock.tryLock(0):
            self._become_hub(lock)
            return
        # holder liveness gates on the PID ALONE: getLockInfo's app name is
        # the runner image (verified: "python" in pytest, the exe's file
        # name in production) -- a name check would reject valid hubs
        pid = _lock_holder_pid(lock, self._dir / "hub.lock")
        if pid and plat_helpers.is_pid_alive(pid):
            self._become_client()
        else:
            lock.removeStaleLockFile()
            self._schedule_elect_retry()

    def _schedule_elect_retry(self) -> None:
        # jitter so N-1 simultaneous survivors don't stampede the lock
        QTimer.singleShot(max(0, self._jitter_fn()), self._elect)

    def _become_hub(self, lock) -> None:
        self._lock = lock
        self._secret = sync_security.rotate_secret(self._dir)
        self._epoch = pysecrets.token_hex(8)
        server = QLocalServer(self)
        # load-bearing: the DEFAULT named-pipe DACL grants Everyone READ;
        # this option builds a single-ACE DACL for the current user's SID.
        # (On Unix the rendezvous is a socket file inside the per-user 0700
        # runtime dir -- the directory is the boundary there.)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if sys.platform != "win32":
            # A SIGKILLed hub leaves its socket file behind and listen()
            # then fails with AddressInUseError -- the elect->probe loop
            # would livelock on it. The hub.lock we already hold proves no
            # live hub owns the name, so unlink the stale file first.
            QLocalServer.removeServer(self._pipe)
        if not server.listen(self._pipe):
            # On Windows listen() never fails; on Unix a persistent bind
            # error (socket dir permissions, path overflow) must not park
            # the bus as a silent client forever -- retry the election a
            # couple of times, then park VISIBLY (the chip is the way back)
            log.warning("listen(%s) failed (attempt %d)",
                        self._pipe, self._listen_fail + 1)
            server.deleteLater()
            lock.unlock()
            self._lock = None
            self._listen_fail += 1
            if self._listen_fail >= 3:
                self.statusChanged.emit(ST_NO_LISTEN)
                self._role = _SESSION_OFF
                return
            self._role = _ELECTING
            self._schedule_elect_retry()
            return
        self._listen_fail = 0
        try:
            # link.json is written only for a LISTENING hub: a client must
            # never dial a rendezvous its own era failed to bind
            sync_security.write_link_info(self._dir, {
                "name": self._pipe, "epoch": self._epoch,
                "proto": proto.PROTO, "pid": os.getpid(),
            })
        except OSError:
            log.exception("link.json write failed")
            server.close()
            server.deleteLater()
            self._teardown_sockets()
            lock.unlock()
            self._lock = None
            self.statusChanged.emit(ST_NO_STATE_DIR)
            self._role = _OFF
            return
        server.newConnection.connect(self._on_accept)
        self._server = server
        self._role = _HUB
        self._helloed = False
        self._degraded = False
        self._backoff_i = 0
        self._ping_timer.start()
        self._apply_roster_rows()
        self.peersChanged.emit(0)

    def _become_client(self) -> None:
        self._role = _CLIENT
        self._helloed = False
        self._epoch_retried = False
        # the same timer drives the hub's ping AND the client's silence
        # clock -- a hub that stops talking must be noticed client-side
        self._ping_timer.start()
        self._read_link_then_connect(attempts=5, delay_ms=50)

    def _read_link_then_connect(self, attempts: int, delay_ms: int) -> None:
        if self._role != _CLIENT:
            return
        info = sync_security.read_link_info(self._dir)
        if info is None or not isinstance(info.get("name"), str):
            if attempts > 1:
                # the winner may still be writing link.json (cold start)
                QTimer.singleShot(
                    delay_ms,
                    lambda: self._read_link_then_connect(attempts - 1, delay_ms))
                return
            # retries exhausted: never treat a live holder as unreachable
            # just because the file was slow -- re-check and either probe
            # as a client or take over a dead holder's lock
            self._probe_or_reelect()
            return
        if info.get("proto") != proto.PROTO:
            self.statusChanged.emit(ST_PROTO_MISMATCH)
            self._role = _SESSION_OFF
            return
        sock = QLocalSocket(self)
        sock.readyRead.connect(lambda: self._on_client_bytes(sock))
        sock.disconnected.connect(lambda s=sock: self._on_server_gone(s))
        sock.errorOccurred.connect(
            lambda err: self._on_client_error(sock, err))
        # retire the previous attempt's socket (an aborted-but-undeleted
        # QLocalSocket stays parented here and would accumulate one object
        # per backoff round). _csock is cleared FIRST so the old socket's
        # synchronous `disconnected` fails the identity check below instead
        # of tearing the new connection down.
        old = self._csock
        self._csock = None
        if old is not None and old is not sock:
            try:
                old.abort()
                old.deleteLater()
            except RuntimeError:
                pass
        self._csock = sock
        self._cbuf = bytearray()
        self._csid = ""
        sock.connectToServer(info["name"])
        # hello watchdog: a silent connector is a squatter or a corpse
        QTimer.singleShot(self._hello_timeout_ms, lambda: self._hello_watchdog(sock))

    def _hello_watchdog(self, sock: QLocalSocket) -> None:
        if (self._role == _CLIENT and self._csock is sock
                and not self._helloed):
            sock.abort()

    def _probe_or_reelect(self) -> None:
        """After failures: holder alive -> backoff probe; dead -> re-elect."""
        lock = QLockFile(str(self._dir / "hub.lock"))
        lock.setStaleLockTime(0)
        pid = _lock_holder_pid(lock, self._dir / "hub.lock")
        if pid and plat_helpers.is_pid_alive(pid):
            delay = self._backoff[min(self._backoff_i, len(self._backoff) - 1)]
            self._backoff_i = min(self._backoff_i + 1, len(self._backoff) - 1)
            QTimer.singleShot(delay, lambda: self._read_link_then_connect(1, 0))
        else:
            lock.removeStaleLockFile()
            self._schedule_elect_retry()

    # --- hub side ----------------------------------------------------------------
    def _on_accept(self) -> None:
        assert self._server is not None
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if len(self._peers) >= proto.MAX_WINDOWS:
                # the cap counts CLIENTS (the hub is the 17th window).
                # Give the bye a moment to actually reach the wire before
                # tearing the socket down -- abort() alone would discard
                # it and the refused window would cycle forever.
                sock.write(proto.encode(
                    {"v": proto.PROTO, "t": "bye", "src": self._wid,
                     "reason": "full"}))
                if sock.bytesToWrite() > 0:
                    sock.waitForBytesWritten(100)
                sock.abort()
                continue
            peer = _Peer(sock)
            peer.sid = pysecrets.token_hex(8)
            self._peers[sock] = peer
            sock.readyRead.connect(lambda s=sock: self._on_peer_bytes(s))
            sock.disconnected.connect(lambda s=sock: self._on_peer_gone(s))
            sock.write(proto.encode(
                {"v": proto.PROTO, "t": "challenge", "src": self._wid,
                 "sid": peer.sid, "epoch": self._epoch}))
            sock.flush()  # the every-write-flushes invariant applies here too
            QTimer.singleShot(self._hello_timeout_ms,
                              lambda s=sock: self._hello_watchdog_hub(s))

    def _hello_watchdog_hub(self, sock: QLocalSocket) -> None:
        peer = self._peers.get(sock)
        if peer is not None and not peer.helloed:
            sock.abort()

    def _on_peer_bytes(self, sock: QLocalSocket) -> None:
        peer = self._peers.get(sock)
        if peer is None:
            return
        peer.buf += sock.readAll().data()
        if len(peer.buf) > proto.MAX_LINE and b"\n" not in peer.buf:
            sock.abort()
            return
        for raw in proto.split_frames(peer.buf):
            self._on_peer_line(peer, raw)

    def _on_peer_line(self, peer: _Peer, raw: bytes) -> None:
        # token bucket: a flooding peer must not starve the event loop
        now = time.monotonic()
        peer.tokens = min(
            float(proto.RATE_BURST),
            peer.tokens + (now - peer.refill) * proto.RATE_PER_SEC)
        peer.refill = now
        peer.tokens -= 1.0
        if peer.tokens < 0:
            self._drop_peer(peer, "rate")
            return

        msg = proto.decode_line(raw)
        if msg is None:
            peer.sock.abort()
            return
        t = msg["t"]

        if not peer.helloed:
            if t != "hello":
                peer.sock.abort()
                return
            self._finish_handshake(peer, msg)
            return
        if t in ("hello", "challenge", "welcome"):
            peer.sock.abort()  # protocol violation mid-session
            return
        if msg.get("src") != peer.wid:
            # the handshake MAC bound this socket to ONE wid; a frame wearing
            # another window's id would overwrite that window's roster row
            # and break loop-back suppression on the victim (its own bus
            # would emit incoming for its own wid). A helloed peer speaks
            # only as itself.
            peer.sock.abort()
            return

        if t == "input":
            # hot path: relay the ORIGINAL RAW BYTES (re-framed with the
            # newline split_frames stripped -- the wire format needs it),
            # feed our own workspace (src is the client, so loop-back
            # suppression keeps this safe)
            frame = raw + b"\n"
            self._relay_raw(peer.sock, frame)
            self.incoming.emit(msg)
            self._kick_check()
        elif t == "sync":
            self._group_sync = msg["on"]
            self._group_sync_tag = msg.get("tag", "")
            self._relay_raw(peer.sock, raw + b"\n")
            self.incoming.emit(msg)
        elif t == "run":
            self._relay_raw(peer.sock, raw + b"\n")
            self.incoming.emit(msg)
        elif t == "digest":
            self._roster[msg["src"]] = {
                "id": msg["src"], "name": peer.name, "panes": msg["panes"]}
            self._broadcast_roster()
            self._apply_roster_rows()  # the hub's own registry follows too
        elif t == "bye":
            self._drop_peer(peer, "exit")
        # ping/gone from a client: nothing to do

    def _finish_handshake(self, peer: _Peer, msg: Dict) -> None:
        if msg.get("proto") != proto.PROTO:
            peer.sock.write(proto.encode(
                {"v": proto.PROTO, "t": "bye", "src": self._wid,
                 "reason": "proto"}))
            peer.sock.flush()
            peer.sock.abort()
            return
        secret = self._secret
        # self-heal: if the secret file vanished (AV, user cleanup), put our
        # in-memory copy back -- otherwise no new client can ever join
        if sync_security.read_secret(self._dir) != secret:
            sync_security.rotate_secret(self._dir)
            # rotate_secret writes a NEW secret; re-read what we just wrote
            # so hub and file agree (rotate returns it, but stay explicit)
            secret = sync_security.read_secret(self._dir) or secret
            self._secret = secret
        expected = proto.mac(secret, "c1", peer.sid, msg["src"])
        if not hmac.compare_digest(expected, msg.get("mac", "")):
            peer.sock.write(proto.encode(
                {"v": proto.PROTO, "t": "bye", "src": self._wid,
                 "reason": "auth"}))
            peer.sock.flush()
            peer.sock.abort()
            return
        peer.helloed = True
        peer.wid = msg["src"]
        peer.digest = msg["panes"]
        self._name_counter += 1
        peer.name = f"W{self._name_counter}"
        self._roster[peer.wid] = {
            "id": peer.wid, "name": peer.name, "panes": peer.digest}
        peer.sock.write(proto.encode({
            "v": proto.PROTO, "t": "welcome", "src": self._wid,
            "you": peer.name, "epoch": self._epoch,
            "mac": proto.mac(secret, "s1", peer.sid, peer.wid),
            "sync_on": self._group_sync,
            "sync_tag": self._group_sync_tag,
            "windows": self._windows_rows(),
        }))
        peer.sock.flush()
        self._broadcast_roster()
        self._apply_roster_rows()
        self.peersChanged.emit(len(self._peers))

    def _windows_rows(self) -> List[Dict]:
        """Roster rows INCLUDING the hub (a client's "other windows")."""
        rows = [{"id": self._wid, "name": "W1", "panes": self._own_digest}]
        rows.extend(self._roster.values())
        return rows

    def _broadcast_roster(self) -> None:
        raw = proto.encode({"v": proto.PROTO, "t": "roster", "src": self._wid,
                            "windows": self._windows_rows()})
        for sock, peer in list(self._peers.items()):
            if not peer.helloed:
                continue  # never send relayed traffic pre-handshake
            try:
                sock.write(raw)
                sock.flush()
            except RuntimeError:
                pass
        self._kick_check()

    def _apply_roster_rows(self) -> None:
        self._registry.apply_roster(list(self._roster.values()), self._wid)
        self.rosterChanged.emit()

    def _relay_raw(self, origin_sock: Optional[QLocalSocket], raw: bytes) -> None:
        # ONLY helloed peers: an unauthenticated connection must not receive
        # relayed traffic, and a mid-handshake client would treat any frame
        # other than its welcome as a protocol violation and hang up
        for sock, peer in list(self._peers.items()):
            if sock is origin_sock or not peer.helloed:
                continue
            try:
                sock.write(raw)
                # the write notifier does not reliably engage between event
                # loop turns on Windows pipes: flush explicitly so delivery
                # never depends on notifier scheduling
                sock.flush()
            except RuntimeError:
                pass

    def _kick_check(self) -> None:
        # a wedged peer (never drains) would otherwise grow the hub's
        # buffers without bound: drop it; it re-hellos when it recovers
        for sock, peer in list(self._peers.items()):
            if sock.bytesToWrite() > proto.KICK_PENDING:
                self._drop_peer(peer, "drop")

    def _drop_peer(self, peer: _Peer, reason: str) -> None:
        self._peers.pop(peer.sock, None)
        self._roster.pop(peer.wid, None)
        log.debug("dropped peer %s (%s)", peer.wid or "<unhelloed>", reason)
        # the roster IS the departure notice: clients apply it wholesale, so
        # survivors prune the departed window's rows in the same breath. (The
        # old separate `gone` frame had two bugs -- an id="" broadcast for
        # never-helloed peers and a rate reason outside the wire grammar --
        # both of which made every receiving client abort its connection.)
        self._apply_roster_rows()
        self._broadcast_roster()
        try:
            peer.sock.abort()
        except RuntimeError:
            pass
        self.peersChanged.emit(len(self._peers))

    def _on_peer_gone(self, sock: QLocalSocket) -> None:
        peer = self._peers.get(sock)
        if peer is not None:
            self._drop_peer(peer, "exit")

    # --- client side ---------------------------------------------------------------
    def _on_client_error(self, sock: QLocalSocket, _err) -> None:
        # any error before the welcome (pipe not up yet, refused, closed)
        # is a rendezvous race, not a hub death: back off and retry as a
        # client, re-reading link.json (the epoch may have advanced)
        if self._role == _CLIENT and sock is self._csock and not self._helloed:
            _abort(sock)
            self._probe_or_reelect()

    def _backoff_reconnect(self) -> None:
        delay = self._backoff[min(self._backoff_i, len(self._backoff) - 1)]
        self._backoff_i = min(self._backoff_i + 1, len(self._backoff) - 1)
        QTimer.singleShot(delay, lambda: self._read_link_then_connect(1, 0))

    def _on_client_bytes(self, sock: QLocalSocket) -> None:
        if self._role != _CLIENT or sock is not self._csock:
            return
        self._cbuf += sock.readAll().data()
        if len(self._cbuf) > proto.MAX_LINE and b"\n" not in self._cbuf:
            sock.abort()
            return
        for raw in proto.split_frames(self._cbuf):
            self._on_client_line(raw)

    def _on_client_line(self, raw: bytes) -> None:
        self._last_line = time.monotonic()
        self._degraded = False
        msg = proto.decode_line(raw)
        if msg is None:
            _abort(self._csock)
            return
        t = msg["t"]

        if t == "challenge":
            if self._helloed:  # a mid-session challenge is a violation
                _abort(self._csock)
                return
            info = sync_security.read_link_info(self._dir) or {}
            if msg.get("epoch") != info.get("epoch") and not self._epoch_retried:
                # a stale-epoch challenge means we dialed a dying era's
                # pipe (or a squatter): re-read the rendezvous and redial
                self._epoch_retried = True
                _abort(self._csock)
                self._csid = ""
                QTimer.singleShot(0, lambda: self._read_link_then_connect(1, 0))
                return
            self._csid = msg["sid"]
            secret = sync_security.read_secret(self._dir)
            if secret is None:
                self.statusChanged.emit(ST_LINKING)
                _abort(self._csock)
                self._backoff_reconnect()
                return
            self._csock.write(proto.encode({
                "v": proto.PROTO, "t": "hello", "src": self._wid,
                "proto": proto.PROTO,
                "mac": proto.mac(secret, "c1", self._csid, self._wid),
                "panes": self._own_digest,
            }))
            self._csock.flush()
            return
        if t == "welcome":
            # only meaningful while our hello is outstanding (_csid set,
            # handshake not yet finished); a welcome out of the blue is a
            # rogue server -- mac verification inside decides for real
            if self._helloed or not self._csid:
                _abort(self._csock)
                return
            self._finish_welcome(msg)
            return
        if t == "bye":
            # meaningful at ANY stage: a full-house rejection arrives
            # before we could even say hello
            self._on_server_bye(msg)
            return
        if not self._helloed:
            # anything else before the handshake completes is a violation
            _abort(self._csock)
            return

        if t == "roster":
            self._registry.apply_roster(msg["windows"], self._wid)
            self.rosterChanged.emit()
            self.peersChanged.emit(self._registry.other_windows())
        elif t in ("input", "sync", "run"):
            # "src is remote by construction" is the HUB's invariant (it
            # aborts a helloed peer that wears another wid); this second
            # check is belt-and-braces against a hostile hub, keeping the
            # loop-back suppression invariant true even there.
            if msg["src"] != self._wid:
                self.incoming.emit(msg)
        elif t == "ping":
            pass  # the silence clock was reset above
        elif t == "gone":
            # roster that follows carries the truth; nothing to do here
            pass

    def _on_server_bye(self, msg: Dict) -> None:
        if msg.get("reason") == "full":
            self.statusChanged.emit(ST_FULL)
            self._role = _SESSION_OFF
            self._teardown_sockets()
            self._registry.clear()
            self.rosterChanged.emit()
            self.peersChanged.emit(0)
        else:
            # an orderly goodbye behaves exactly like a disconnect:
            # registry kept, jittered liveness probe, reconnect/re-elect
            gone = self._csock
            _abort(gone)
            self._on_server_gone(gone)

    def _finish_welcome(self, msg: Dict) -> None:
        secret = sync_security.read_secret(self._dir)
        if (secret is None
                or not hmac.compare_digest(
                    proto.mac(secret, "s1", self._csid, self._wid),
                    msg.get("mac", ""))):
            # a rogue server on the pipe name cannot compute the mac --
            # never apply its roster; treat it as a failed connection
            _abort(self._csock)
            self._probe_or_reelect()
            return
        self._helloed = True
        self._backoff_i = 0
        self._registry.apply_roster(msg["windows"], self._wid)
        self.rosterChanged.emit()
        self.peersChanged.emit(self._registry.other_windows())
        # adoption is state-only (no flash): live relays flash "set in Wn",
        # and processing the roster first lets the first-link flash win
        self.syncAdopted.emit(msg["sync_on"], msg.get("sync_tag", ""))

    def _on_server_gone(self, sock=None) -> None:
        if self._role != _CLIENT:
            return
        if sock is not None and sock is not self._csock:
            # a retired attempt's socket reporting in: its teardown already
            # happened (or the next attempt owns the connection now)
            return
        self._teardown_sockets()
        # registry deliberately RETAINED during the gap: menus and counts
        # don't blink; they rebuild from the next welcome/roster
        self._helloed = False
        self._role = _RECONNECTING
        self.statusChanged.emit(ST_LINKING)
        self.peersChanged.emit(0)
        QTimer.singleShot(max(0, self._jitter_fn()), self._elect)

    # --- liveness --------------------------------------------------------------------
    def _on_ping_tick(self) -> None:
        if self._role == _HUB:
            helloed = [sock for sock, peer in self._peers.items()
                       if peer.helloed]
            if not helloed:
                return
            self._kick_check()
            raw = proto.encode({"v": proto.PROTO, "t": "ping",
                                "src": self._wid})
            for sock in helloed:
                try:
                    sock.write(raw)
                    sock.flush()
                except RuntimeError:
                    pass
            return
        if self._role == _CLIENT and self._helloed and self._degrade_deadline_ms:
            if time.monotonic() - self._last_line > (
                    self._degrade_deadline_ms / 1000.0) and not self._degraded:
                self._degraded = True
                self.statusChanged.emit(ST_DEGRADED)
                # ONE reconnect attempt; a truly hung hub then fails the
                # hello watchdog and we continue capped-backoff probing
                if self._csock is not None:
                    self._csock.abort()

    # --- teardown helpers ----------------------------------------------------------------
    def _teardown_sockets(self) -> None:
        for sock in list(self._peers):
            try:
                sock.abort()
                sock.deleteLater()
            except RuntimeError:
                pass
        self._peers.clear()
        if self._csock is not None:
            try:
                self._csock.abort()
                self._csock.deleteLater()
            except RuntimeError:
                pass
            self._csock = None
        self._cbuf = bytearray()
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None
        self._ping_timer.stop()
        if self._lock is not None:
            self._lock.unlock()
            self._lock = None
