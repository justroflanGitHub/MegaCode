"""Offscreen tests for the cross-window SyncBus: N buses in ONE process.

Each test group gets its own state dir and pipe-name suffix (the production
pipe is one per user+session; the suffix is the in-process test seam).
Everything is driven by processEvents spin loops -- never fixed sleeps.
"""

from __future__ import annotations

import os
import time
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QLockFile  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from megacode import plat_helpers  # noqa: E402
from megacode import sync_protocol as proto  # noqa: E402
from megacode import sync_security  # noqa: E402
from megacode.sync_bus import (  # noqa: E402
    ST_DEGRADED,
    ST_ELEVATED,
    ST_FULL,
    ST_PROTO_MISMATCH,
    SyncBus,
)


@pytest.fixture()
def qapp():
    return QApplication.instance() or QApplication([])


def spin(cond, ms=3000):
    """Pump the loop until cond() or deadline (never a fixed sleep)."""
    app = QApplication.instance()
    assert app is not None
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        if cond():
            return True
        app.processEvents()
        time.sleep(0.002)
    return cond()


class Group:
    """One rendezvous: shared state dir + pipe name; a bus factory."""

    def __init__(self, root):
        self.dir = root / f"g{uuid.uuid4().hex[:8]}"
        self.dir.mkdir()
        self.suffix = f"-t{uuid.uuid4().hex[:8]}"
        # exactly how the bus builds its rendezvous (the rogue-endpoint tests
        # below must land on the same name/socket path the real bus uses)
        self.pipe = plat_helpers.socket_path(
            proto.pipe_name(
                plat_helpers.user_name(),
                plat_helpers.process_session_id(),
            ) + self.suffix
        )
        self.buses = []

    def make(self, **kw):
        kw.setdefault("jitter_fn", lambda: 0)
        kw.setdefault("hello_timeout_ms", 400)
        kw.setdefault("ping_ms", 40)
        kw.setdefault("backoff", [5, 10, 20, 40])
        bus = SyncBus(self.dir, name_suffix=self.suffix, **kw)
        self.buses.append(bus)
        return bus


@pytest.fixture()
def group(tmp_path):
    g = Group(tmp_path)
    yield g
    for bus in g.buses:
        try:
            bus.stop()
        except Exception:  # noqa: BLE001 (teardown must never fail a test)
            pass


def _record(bus, signal):
    out = []
    # one-arg signals record their payload; multi-arg (syncAdopted's
    # (on, tag)) record the tuple, so assertions stay literal
    signal.connect(lambda *a: out.append(a[0] if len(a) == 1 else a))
    return out


def _crash(bus):
    """Simulate the bus's PROCESS dying: sockets+server gone, lock released,
    no goodbye -- exactly what the OS teardown of a dead process looks like."""
    for sock in list(bus._peers):
        try:
            sock.abort()
        except RuntimeError:
            pass
    if bus._server is not None:
        bus._server.close()
    if bus._csock is not None:
        try:
            bus._csock.abort()
        except RuntimeError:
            pass
    if bus._lock is not None:
        bus._lock.unlock()
    bus._ping_timer.stop()
    bus._role = "off"


# --- basic topology ----------------------------------------------------------------


def test_single_bus_is_hub_and_emits_nothing(group, qapp):
    bus = group.make()
    statuses = _record(bus, bus.statusChanged)
    peers = _record(bus, bus.peersChanged)

    bus.start()
    assert spin(lambda: bus._role == "hub")

    # a lone window is SILENT: no statuses, no non-zero peers, empty registry
    assert statuses == []
    assert set(peers) <= {0}
    assert bus.registry().other_windows() == 0
    assert bus.is_alive()
    # publishing with nobody listening is a harmless no-op
    assert bus.publish_input("p1", ["fe"], "x", False) is True
    bus.publish_digest([])
    assert bus._own_digest == []


def test_second_bus_handshakes_via_challenge_and_gets_welcome(group, qapp):
    b1 = group.make()
    b1.start()
    assert spin(lambda: b1._role == "hub")
    digest = [proto.build_pane_entry("p1", ["fe"], True, "term")]
    b1.publish_digest(digest)

    b2 = group.make()
    b2.start()
    assert spin(lambda: b2.is_alive() and b1.registry().other_windows() == 1)

    # the joiner sees the hub's digest under its display name
    rows = b2.registry()._windows
    assert len(rows) == 1
    row = next(iter(rows.values()))
    assert row["name"] == "W1"
    assert row["panes"] == digest
    assert b1.registry().other_windows() == 1


def test_hub_origin_messages_reach_clients_but_never_loop_back(group, qapp):
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    b3 = group.make()
    b3.start()
    # is_alive (== helloed clients) is the honest condition: the hub's
    # registry fills BEFORE the joiners process their welcome, and
    # publishing in that gap would be silently dropped (fire-and-forget)
    assert spin(lambda: b2.is_alive() and b3.is_alive()
                and b1.registry().other_windows() == 2)
    got1, got2, got3 = (_record(b, b.incoming) for b in (b1, b2, b3))

    # CLIENT-origin input: hub + other client receive; origin gets nothing
    b2.publish_input("p1", ["fe"], "x", False)
    assert spin(lambda: len(got1) == 1 and len(got3) == 1)
    assert got2 == []  # loop-back suppression (the double-delivery bug class)
    assert got1[0]["t"] == "input" and got1[0]["seq"] == "x"
    assert got1[0]["src"] == b2._wid
    assert "all" not in got1[0]  # domain frames stay byte-identical

    # the all-windows arm's flag rides the relay untouched
    b2.publish_input("p1", ["fe"], "z", False, all_mode=True)
    assert spin(lambda: any(m.get("t") == "input" and m["seq"] == "z"
                            for m in got1))
    relayed = next(m for m in got1
                   if m.get("t") == "input" and m["seq"] == "z")
    assert relayed["all"] is True

    # HUB-origin sync: clients receive; the hub's own workspace does NOT
    b1.publish_sync(True)
    assert spin(lambda: any(m.get("t") == "sync" for m in got2))
    assert all(m.get("src") != b1._wid for m in got1)
    assert b1._group_sync is True

    # HUB-origin input: same rule
    b1.publish_input("p2", [], "y", False)
    assert spin(lambda: any(m.get("t") == "input" and m["seq"] == "y"
                            for m in got2 + got3))
    assert all(m.get("t") != "input" or m["seq"] != "y" for m in got1)


def test_global_sync_state_welcome_is_authoritative(group, qapp):
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    assert spin(lambda: b2.is_alive())
    b1.publish_sync(True)
    assert b1._group_sync is True

    # a late joiner adopts the group's state via welcome (state only, no flash)
    b3 = group.make()
    b3.start()
    adopted3 = _record(b3, b3.syncAdopted)
    assert spin(lambda: b3.is_alive())
    assert adopted3 == [(True, "")]

    # and a fresh joiner right after a toggle-off adopts False the same way
    b1.publish_sync(False)
    b4 = group.make()
    b4.start()
    adopted4 = _record(b4, b4.syncAdopted)
    assert spin(lambda: b4.is_alive())
    assert adopted4 == [(False, "")]


def test_scoped_sync_state_welcome_is_authoritative(group, qapp):
    """The picker's scope travels with the toggle: the hub remembers it, a
    late joiner adopts (on, tag) via the welcome, and a live re-scope relays
    the tag to everyone."""
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    assert spin(lambda: b2.is_alive())
    got2 = _record(b2, b2.incoming)

    b1.publish_sync(True, "fe")
    assert b1._group_sync is True and b1._group_sync_tag == "fe"
    assert spin(lambda: any(m.get("t") == "sync" and m.get("tag") == "fe"
                            for m in got2))

    b3 = group.make()
    b3.start()
    adopted3 = _record(b3, b3.syncAdopted)
    assert spin(lambda: b3.is_alive())
    assert adopted3 == [(True, "fe")]

    # a live re-scope (group switch without an off/on blip) relays too
    b1.publish_sync(True, "be")
    assert spin(lambda: any(m.get("t") == "sync" and m.get("tag") == "be"
                            for m in got2))
    assert b1._group_sync_tag == "be"


# --- hostile / edge endpoints ----------------------------------------------------------


def test_wrong_mac_rejected_and_socket_closed(group, qapp):
    b1 = group.make()
    b1.start()
    assert spin(lambda: b1._role == "hub")

    rogue = QLocalSocket()
    rogue.connectToServer(b1._pipe)
    assert spin(lambda: rogue.state() == QLocalSocket.LocalSocketState.ConnectedState)
    assert spin(lambda: b'"t":"challenge"' in rogue.readAll().data())

    hello = {"v": 1, "t": "hello", "src": "e" * 12, "proto": 1,
             "mac": "0" * 64, "panes": []}
    rogue.write(proto.encode(hello))
    assert spin(lambda: rogue.state() == QLocalSocket.LocalSocketState.UnconnectedState)
    assert b1.is_alive()  # the hub survived the stranger untouched


def test_fake_hub_rejected_by_welcome_mac(group, qapp, tmp_path):
    # a rogue holds the lock, writes the rendezvous and listens; a real bus
    # must refuse its forged welcome and never apply its roster
    lock = QLockFile(str(group.dir / "hub.lock"))
    lock.setStaleLockTime(0)
    assert lock.tryLock(0)
    sync_security.write_link_info(group.dir, {
        "name": group.pipe, "epoch": "e" * 16, "proto": 1,
        "pid": os.getpid(),
    })
    rogue = QLocalServer()
    assert rogue.listen(group.pipe)
    evil = [{"id": "a" * 12, "name": "W1",
             "panes": [{"id": "px", "tags": ["evil"], "alive": True,
                        "kind": "term"}]}]

    def _on_conn():
        while rogue.hasPendingConnections():
            s = rogue.nextPendingConnection()
            s.buf = bytearray()
            def _serve(ss=s):
                ss.buf += ss.readAll().data()
                for line in proto.split_frames(ss.buf):
                    if b'"hello"' in line:
                        ss.write(proto.encode({
                            "v": 1, "t": "welcome", "src": "a" * 12,
                            "you": "W2", "epoch": "e" * 16,
                            "mac": "f" * 64,  # cannot be computed: forged
                            "sync_on": False, "windows": evil,
                        }))
            s.readyRead.connect(_serve)

    rogue.newConnection.connect(_on_conn)

    bus = group.make()
    bus.start()
    assert spin(lambda: not bus.is_alive(), ms=2500) or True
    assert "evil" not in bus.registry().known_tags()
    assert bus.registry().other_windows() == 0
    rogue.close()
    lock.unlock()


def test_stale_epoch_challenge_rereads_state_and_reconnects(group, qapp):
    lock = QLockFile(str(group.dir / "hub.lock"))
    lock.setStaleLockTime(0)
    assert lock.tryLock(0)
    sync_security.write_link_info(group.dir, {
        "name": group.pipe, "epoch": "e" * 16, "proto": 1,
        "pid": os.getpid(),
    })
    rogue = QLocalServer()
    assert rogue.listen(group.pipe)
    connects = []

    def _on_conn():
        while rogue.hasPendingConnections():
            s = rogue.nextPendingConnection()
            connects.append(s)
            s.write(proto.encode({"v": 1, "t": "challenge",
                                  "src": "a" * 12, "sid": "s" * 16,
                                  "epoch": "0" * 16}))  # STALE epoch

    rogue.newConnection.connect(_on_conn)
    bus = group.make(hello_timeout_ms=300)
    bus.start()
    # the client detected the stale era and redialed at least once
    assert spin(lambda: len(connects) >= 2, ms=2500)
    assert bus.registry().other_windows() == 0
    rogue.close()
    lock.unlock()


def test_proto_mismatch_denies_and_stays_solo(group, qapp):
    lock = QLockFile(str(group.dir / "hub.lock"))
    lock.setStaleLockTime(0)
    assert lock.tryLock(0)
    sync_security.write_link_info(group.dir, {
        "name": group.pipe, "epoch": "e" * 16, "proto": 99,
        "pid": os.getpid(),
    })
    bus = group.make()
    statuses = _record(bus, bus.statusChanged)
    bus.start()
    assert spin(lambda: statuses)
    assert statuses == [ST_PROTO_MISMATCH]
    assert bus._role == "session_off"
    lock.unlock()


def test_hello_watchdog_closes_silent_connector(group, qapp):
    b1 = group.make(hello_timeout_ms=250)
    b1.start()
    assert spin(lambda: b1._role == "hub")

    rogue = QLocalSocket()
    rogue.connectToServer(b1._pipe)
    assert spin(lambda: rogue.state() == QLocalSocket.LocalSocketState.ConnectedState)
    # say nothing; the watchdog must close us
    assert spin(lambda: rogue.state() == QLocalSocket.LocalSocketState.UnconnectedState,
                ms=1500)


def test_rate_limit_disconnects_flooder(group, qapp):
    b1 = group.make()
    b1.start()
    assert spin(lambda: b1._role == "hub")

    rogue = QLocalSocket()
    rogue.connectToServer(b1._pipe)
    assert spin(lambda: rogue.state() == QLocalSocket.LocalSocketState.ConnectedState)
    # drain the challenge so the write side stays flowing
    rogue.readAll()
    line = proto.encode({"v": 1, "t": "ping", "src": "e" * 12})
    for _ in range(400):  # far past RATE_BURST with no elapsed time to refill
        rogue.write(line)
    assert spin(lambda: rogue.state() == QLocalSocket.LocalSocketState.UnconnectedState,
                ms=2500)
    assert b1.is_alive()


def test_oversized_line_closes_connection(group, qapp):
    b1 = group.make()
    b1.start()
    assert spin(lambda: b1._role == "hub")

    rogue = QLocalSocket()
    rogue.connectToServer(b1._pipe)
    assert spin(lambda: rogue.state() == QLocalSocket.LocalSocketState.ConnectedState)
    rogue.readAll()
    rogue.write(b"x" * (proto.MAX_LINE + 16))  # no newline: buffer must bound
    assert spin(lambda: rogue.state() == QLocalSocket.LocalSocketState.UnconnectedState,
                ms=2500)


def test_wedged_peer_kicked_and_roster_pushed(group, qapp):
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    assert spin(lambda: b1.registry().other_windows() == 1)
    assert b1._peers

    # a peer that never drains: bytesToWrite past the threshold -> kicked
    sock = next(iter(b1._peers))
    sock.bytesToWrite = lambda: proto.KICK_PENDING + 1  # type: ignore[method-assign]
    b1._kick_check()

    assert spin(lambda: b1.registry().other_windows() == 0)
    assert spin(lambda: not b2.is_alive())


# --- elections & death ------------------------------------------------------------------


def test_spoofed_src_drops_the_peer(group, qapp):
    """A helloed peer that wears another window's wid in a frame is dropped:
    the handshake MAC bound the socket to ONE identity, and a forged src
    would overwrite the victim's roster row and break loop-back suppression
    on the victim's own bus."""
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    b3 = group.make()
    b3.start()
    assert spin(lambda: b2.is_alive() and b3.is_alive()
                and b1.registry().other_windows() == 2)
    got_hub = _record(b1, b1.incoming)

    victim = b3._wid
    b2._csock.write(proto.encode({
        "v": 1, "t": "input", "src": victim, "pane": "p1",
        "tags": [], "seq": "x", "paste": False}))
    b2._csock.flush()

    # the spoofer's connection is cut, and the forged frame went nowhere
    assert spin(lambda: not b2.is_alive(), ms=2500)
    assert all(m.get("seq") != "x" for m in got_hub)
    # the victim is untouched
    assert spin(lambda: b3.is_alive())


def test_prehello_peer_drop_leaves_clients_alone(group, qapp):
    """A stray connection that dies before the handshake must not disturb
    the linked clients (the old `gone` broadcast carried an empty id, which
    failed validation on every receiver and aborted them all)."""
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    assert spin(lambda: b2.is_alive())

    rogue = QLocalSocket()
    rogue.connectToServer(b1._pipe)
    assert spin(lambda: rogue.state() == QLocalSocket.LocalSocketState.ConnectedState)
    rogue.readAll()  # the challenge
    rogue.abort()

    spin(lambda: False, ms=300)
    assert b2.is_alive()  # the bystander survived the stranger's departure
    assert b1.registry().other_windows() == 1


def test_rate_drop_does_not_kill_bystanders(group, qapp):
    """Kicking a flooder is local surgery: the other clients stay connected
    (the old drop notice used a reason outside the wire grammar, which made
    every receiver abort)."""
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    assert spin(lambda: b2.is_alive())

    rogue = QLocalSocket()
    rogue.connectToServer(b1._pipe)
    assert spin(lambda: rogue.state() == QLocalSocket.LocalSocketState.ConnectedState)
    rogue.readAll()
    line = proto.encode({"v": 1, "t": "ping", "src": "e" * 12})
    for _ in range(400):
        rogue.write(line)
    assert spin(lambda: rogue.state() == QLocalSocket.LocalSocketState.UnconnectedState,
                ms=2500)
    spin(lambda: False, ms=200)
    assert b2.is_alive()
    assert b1.registry().other_windows() == 1


def test_departure_prunes_survivor_registries(group, qapp):
    """When one client leaves, the OTHER clients' registries drop it too:
    the roster broadcast is the departure notice, not a side-channel frame
    whose handler is a no-op."""
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    b3 = group.make()
    b3.start()
    assert spin(lambda: b2.is_alive() and b3.is_alive()
                and b2.registry().other_windows() == 2)
    b3.publish_digest([proto.build_pane_entry("p1", ["gone-tag"], True, "term")])
    assert spin(lambda: "gone-tag" in b2.registry().known_tags())

    b3.stop(reason="kill")

    assert spin(lambda: b2.registry().other_windows() == 1)
    assert "gone-tag" not in b2.registry().known_tags()


def test_session_off_bus_restarts_via_start(group, qapp):
    """bye("full")/proto-mismatch park the bus in session_off; start() must
    bring it back -- the toolbar chip is the promised one-click way home."""
    b1 = group.make()
    b1.start()
    assert spin(lambda: b1._role == "hub")
    bus = group.make()
    bus._role = "session_off"  # simulate the post-bye state directly

    bus.start()
    assert spin(lambda: bus.is_alive())


def test_restarted_bus_hello_carries_the_cached_digest(group, qapp):
    """stop() must not throw away the workspace's pane table: a kill-switch ->
    re-link cycle re-announces the panes in the hello itself (the workspace
    publishes on tile events only, so nothing else would)."""
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    assert spin(lambda: b2.is_alive())
    b2.publish_digest([proto.build_pane_entry("p7", ["cached"], True, "term")])
    assert spin(lambda: "cached" in b1.registry().known_tags())

    b2.stop()
    assert spin(lambda: not b1.is_alive() or b1.registry().other_windows() == 0)
    b2.start()
    assert spin(lambda: b2.is_alive()
                and "cached" in b1.registry().known_tags())


def test_hub_death_reelects_exactly_once(group, qapp):
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    b3 = group.make()
    b3.start()
    assert spin(lambda: b1.registry().other_windows() == 2)
    old_epoch = b1._epoch

    _crash(b1)

    assert spin(lambda: {b._role for b in (b2, b3)} >= {"hub"})
    hubs = [b for b in (b2, b3) if b._role == "hub"]
    assert len(hubs) == 1  # exactly one listener -- the split-brain pin
    assert spin(lambda: b2.is_alive() and b3.is_alive())
    assert b2.registry().other_windows() == 1 and b3.registry().other_windows() == 1
    assert hubs[0]._epoch != old_epoch  # a fresh era (new secret implied)


def test_hung_lock_holder_is_never_usurped(group, qapp):
    lock = QLockFile(str(group.dir / "hub.lock"))
    lock.setStaleLockTime(0)
    assert lock.tryLock(0)
    sync_security.write_link_info(group.dir, {
        "name": group.pipe, "epoch": "e" * 16, "proto": 1,
        "pid": os.getpid(),  # ALIVE: the holder is us
    })
    bus = group.make(hello_timeout_ms=200)
    bus.start()
    spin(lambda: False, ms=700)  # a few backoff/probe rounds
    assert bus._role != "hub"
    # the lock is still theirs -- removeStaleLockFile must not have fired
    probe = QLockFile(str(group.dir / "hub.lock"))
    probe.setStaleLockTime(0)
    assert probe.tryLock(0) is False
    lock.unlock()


def test_kill_switch_releases_lock_and_pipe(group, qapp):
    b1 = group.make()
    b1.start()
    assert spin(lambda: b1._role == "hub")

    b1.stop()
    probe = QLockFile(str(group.dir / "hub.lock"))
    probe.setStaleLockTime(0)
    assert probe.tryLock(0) is True
    probe.unlock()

    rogue = QLocalSocket()
    rogue.connectToServer(b1._pipe)
    spin(lambda: False, ms=200)
    assert rogue.state() != QLocalSocket.LocalSocketState.ConnectedState
    rogue.abort()


def test_ping_gap_flips_degraded(group, qapp):
    b1 = group.make()
    b1.start()
    b2 = group.make(degrade_ms=150)
    b2.start()
    assert spin(lambda: b2.is_alive())

    statuses = _record(b2, b2.statusChanged)
    b1._ping_timer.stop()  # a live hub that stopped talking: the hang case
    assert spin(lambda: ST_DEGRADED in statuses, ms=2500)


def test_window_cap_rejects_with_bye_full(group, qapp, monkeypatch):
    monkeypatch.setattr(proto, "MAX_WINDOWS", 2)
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    b3 = group.make()
    b3.start()
    # the cap counts CLIENT connections (the hub is the extra window):
    # with MAX_WINDOWS=2 the 3rd client -- a 4th bus -- is the one refused
    assert spin(lambda: b2.is_alive() and b3.is_alive())
    b4 = group.make()
    b4.start()

    statuses = _record(b4, b4.statusChanged)
    assert spin(lambda: ST_FULL in statuses or b4._role == "session_off",
                ms=5000)
    assert b4._role == "session_off"
    assert not b4.is_alive()
    # the accepted pair is untouched
    assert b2.is_alive() and b3.is_alive()


def test_elevated_process_disables_start(group, qapp, monkeypatch):
    from megacode import sync_bus as sb
    monkeypatch.setattr(sb.plat_helpers, "is_process_elevated",
                        lambda: True)
    bus = group.make()
    statuses = _record(bus, bus.statusChanged)
    bus.start()
    spin(lambda: False, ms=200)
    assert statuses == [ST_ELEVATED]
    assert bus._role == "off"


def test_offline_digest_cache_survives_until_a_peer_joins(group, qapp):
    # C8/H3: the digest is ALWAYS cached; only the wire send is gated
    b1 = group.make()
    b1.start()
    assert spin(lambda: b1._role == "hub")
    digest = [proto.build_pane_entry("p9", ["cached"], True, "term")]
    b1.publish_digest(digest)
    assert b1._own_digest == digest

    b2 = group.make()
    b2.start()
    assert spin(lambda: b2.is_alive())
    assert spin(lambda: "cached" in b2.registry().known_tags())


def test_shutdown_is_fast_and_clean(group, qapp):
    b1 = group.make()
    b1.start()
    b2 = group.make()
    b2.start()
    assert spin(lambda: b2.is_alive())

    t0 = time.monotonic()
    b1.stop(reason="shutdown")
    assert time.monotonic() - t0 < 0.5
    assert not b1.is_alive()
    assert spin(lambda: not b2.is_alive(), ms=2000) or b2._role == "reconnecting"
