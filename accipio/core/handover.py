"""Downloads handed from one Accipio to another: what the queue and the line (``link.py``) say to each other.

The *sender* is the PC the user clicks on; the *collector* downloads and keeps the files. No window code here
(the user's 101C), so a screenless Accipio can use both halves later.

**Sender.** A rule whose place is another Accipio leaves a row ``WAITING`` (``Queue._hand_over``). ``Sender.pump``
does the talking, for each collector on one connection:

1. every waiting row is sent as ``link.add`` with the ORIGINAL link, the page, and the site's cookies only when
   the place's tick is on (99B). The collector answers with its own row id: the row is ``SENT``.
2. the collector is asked how the sent ones went (``link.status``). Finished or failed there is recorded on the
   row (98B: the outcome, never a live percent).
3. what is still owed to it is told: "remove that one" (Remove on the sender, 103A) and "I have heard how that
   one went", after which the collector need not keep its finished row for this sender any longer.

A collector that cannot be reached leaves everything as it is (the rows wait, what is owed stays owed), unless a
row's place says "download here" (97C). The pump never runs twice at once, and never on the queue's own worker:
``Queue.in_background`` starts it, from ``nudge`` (something new to tell) and from ``tick`` (the queue's timer).

**Collector.** ``Collector`` is the ``handler`` of a ``link.Listener``. It lists an incoming link at once and
answers with the row id; the check and the start (network I/O) go through ``run``, which the window sets to its
worker thread. The collector's OWN rules choose folder and name (96A). An Accipio can ask about and remove only
the downloads it sent itself.

**Service.** ``Service`` is one Accipio's whole link: the sender, and the listener that runs only while the
settings ask for it. The windows (stage 3) call ``apply`` with the settings and show ``status_text``.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from accipio.core import debug_log, link, torrents
from accipio.core.store import COMPLETE, HANDED_OVER, OWE_HEARD, OWE_REMOVE, SENT, WAITING, Owed, Row

POLL = 15.0          # seconds between two questions to a collector about what it has
RETRY = 30.0         # seconds before a collector that was away is tried again
ROUNDS = 5           # how often one pump goes round again because something new came up while it ran

_LINK = re.compile(r"(?:https?|ftp)://\S+", re.IGNORECASE)


def _in_place(fn: Callable, *args, **kwargs):
    return fn(*args, **kwargs)


def status_text(row: Row) -> str:
    """The Status column for a row that belongs to another Accipio (98B); empty for every other row."""
    place = row.remote.get("place")
    if not place:
        return ""
    if row.state == WAITING:
        return f"Waiting for {place}"
    if row.state == SENT:
        return f"Sent to {place}"
    if row.state == COMPLETE:
        return str(row.remote.get("outcome") or "")
    return ""


def sent_to(row: Row) -> str:
    """On the sender: the place (another Accipio) this row waits for, was sent to, finished on or failed on;
    empty for every other row. A row that was downloaded here instead has no ``remote`` any more."""
    return str(row.remote.get("place") or "")


def came_from(row: Row) -> str:
    """On the collector: the name of the Accipio that sent this row; empty for every other row."""
    if not row.remote.get("from"):
        return ""
    return str(row.remote.get("from_name") or "another Accipio")


def takeable(url: str) -> bool:
    """What a collector accepts: one http(s) or ftp link, or one magnet link (never a file path or a script)."""
    return bool(_LINK.fullmatch(url)) or torrents.is_magnet(url)


# -- the sender ------------------------------------------------------------------------------------------------

class Sender:
    """Carries the queue's rows for other Accipios over the line. ``run`` is how a change that may start a
    download reaches the queue (the window's worker; in place by default). ``timeout`` is for each connection
    and each answer."""

    def __init__(self, queue, node: link.Node, *, run: Optional[Callable] = None, timeout: float = link.TIMEOUT,
                 clock: Callable[[], float] = time.monotonic):
        self.queue = queue
        self.node = node
        self.run = run or _in_place
        self.timeout = timeout
        self.clock = clock
        self._busy = threading.Lock()
        self._again = False
        self._not_before: Dict[str, float] = {}           # collector id -> when it may be talked to next
        queue.link_nudge = self.nudge
        queue.link_tick = self.tick

    def _work(self):
        return self.queue.store.rows(HANDED_OVER), self.queue.store.owed()

    def nudge(self) -> None:
        """There is something new to tell: talk to the collectors now, whatever the waiting times say."""
        self._not_before.clear()
        self.queue.in_background(self.pump)

    def tick(self) -> None:
        """From the queue's timer: start a pump when a collector with something open is due."""
        rows, owed = self._work()
        now = self.clock()
        if any(self._not_before.get(peer, 0.0) <= now for peer in self._peers(rows, owed)):
            self.queue.in_background(self.pump)

    @staticmethod
    def _peers(rows: List[Row], owed: List[Owed]) -> List[str]:
        return list(dict.fromkeys([r.remote.get("peer") or "" for r in rows] + [o.peer for o in owed]))

    def pump(self) -> None:
        """One go at every collector that is due. Safe to call from anywhere, any number of times: a second
        call while one runs only asks the running one to go round again."""
        if not self._busy.acquire(blocking=False):
            self._again = True
            return
        try:
            for _ in range(ROUNDS):
                self._again = False
                rows, owed = self._work()
                now = self.clock()
                for peer_id in self._peers(rows, owed):
                    if self._not_before.get(peer_id, 0.0) <= now:
                        self._talk(peer_id, [r for r in rows if r.remote.get("peer") == peer_id])
                if not self._again:
                    break
        except Exception as error:                        # a thread of its own: nothing may escape it
            debug_log.note("hand-over pump failed", error=repr(error))
        finally:
            self._busy.release()

    def _talk(self, peer_id: str, rows: List[Row]) -> None:
        waiting = [r for r in rows if r.state == WAITING]
        sent = [r for r in rows if r.state == SENT and r.remote.get("id") is not None]
        place = next((r.remote.get("place") for r in rows if r.remote.get("place")), "") or "The other Accipio"
        peer = self.node.peer(peer_id)
        try:
            if peer is None:
                raise link.Refused(f"This Accipio is not paired with {place} (any more). Pair the two again.")
            with link.Line(self.node, peer, self.timeout) as line:
                for row in waiting:
                    self._send(line, row, peer_id, place)
                if sent:
                    self._ask(line, sent)
                self._settle(line, peer_id)
            self._not_before[peer_id] = self.clock() + POLL
        except link.Away as error:
            debug_log.note("the other Accipio is away", place=place, error=str(error))
            for row in waiting:                           # those already handed over are no longer waiting: untouched
                self.run(self.queue.not_handed, row.id, row.remote.get("ref", ""), str(error), True)
            self._not_before[peer_id] = self.clock() + RETRY
        except link.LinkError as error:                   # reached and refused, or no encryption here: no use waiting
            debug_log.note("the other Accipio refused", place=place, error=str(error))
            for row in waiting:
                self.run(self.queue.not_handed, row.id, row.remote.get("ref", ""), str(error), False)
            if peer is None:                              # nobody to tell any more
                for row in sent:                          # how these went there can never be asked again
                    self.run(self.queue.unpaired, row.id, row.remote.get("ref", ""))
                self.queue.store.settle(o.id for o in self.queue.store.owed() if o.peer == peer_id)
            self._not_before[peer_id] = self.clock() + RETRY

    def _send(self, line: link.Line, row: Row, peer_id: str, place: str) -> None:
        ref = row.remote.get("ref", "")
        headers = row.headers
        reply = line.ask(link.add(row.url, page_url=row.page_url, referrer=headers.get("Referer", ""),
                                  user_agent=headers.get("User-Agent", ""), ref=ref,
                                  cookies=headers.get("Cookie", "") if row.remote.get("cookies") else ""))
        remote_id = reply.get("id")
        if reply.get("ok") and isinstance(remote_id, int) and not isinstance(remote_id, bool):
            self.run(self.queue.handed, row.id, ref, peer_id, remote_id)
        else:
            self.run(self.queue.not_handed, row.id, ref,
                     f"{place} did not take it: {reply.get('error') or 'no reason given'}.", False)

    def _ask(self, line: link.Line, sent: List[Row]) -> None:
        reply = line.ask(link.status([r.remote["id"] for r in sent]))
        reports = reply.get("downloads") if reply.get("ok") else None
        if not isinstance(reports, list):
            return
        by_id = {r.get("id"): r for r in reports if isinstance(r, dict)}
        for row in sent:
            report = by_id.get(row.remote["id"])
            if report is not None:
                self.run(self.queue.outcome, row.id, row.remote.get("ref", ""), report)

    def _settle(self, line: link.Line, peer_id: str) -> None:
        """Tell the collector what is owed to it; each kind is settled only when it has answered yes."""
        owed = [o for o in self.queue.store.owed() if o.peer == peer_id]
        removes = [o for o in owed if o.kind == OWE_REMOVE]
        heard = [o for o in owed if o.kind == OWE_HEARD]
        if removes and line.ask(link.remove([o.remote_id for o in removes])).get("ok"):
            self.queue.store.settle(o.id for o in removes)
        if heard and line.ask(link.status([], heard=[o.remote_id for o in heard])).get("ok"):
            self.queue.store.settle(o.id for o in heard)


# -- one Accipio's whole link ----------------------------------------------------------------------------------

def _joined(items: List[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


class Service:
    """The link of one Accipio: always a ``Sender`` (so rows for another Accipio never wait for ever, also where
    this Python cannot do the encryption: ``Sender`` then fails them with the reason), and a ``Listener`` that runs
    only while ``apply`` is given accepting on and at least one address. ``run`` is how the queue's changes reach
    its worker (the window's; in place by default) and goes to both halves."""

    def __init__(self, queue, node: link.Node, *, run: Optional[Callable] = None, timeout: float = link.TIMEOUT):
        self.queue = queue
        self.node = node
        self.run = run or _in_place
        self.unavailable = link.why_unavailable()
        self.sender = Sender(queue, node, run=run, timeout=timeout)
        self.listener: Optional[link.Listener] = None
        self.accept = False
        self.addresses: List[str] = []
        self.port = 0
        self._failure = ""                                # why the listener could not start at all
        self._lock = threading.RLock()

    def apply(self, accept: bool, addresses: List[str], port: int) -> None:
        """Make the listener what the settings say. Nothing happens when it already is. Never raises."""
        with self._lock:
            wanted = (bool(accept), list(addresses), port)
            if wanted == (self.accept, self.addresses, self.port):
                return
            self.accept, self.addresses, self.port = wanted
            self._stop_listener()
            self._failure = ""
            if self.unavailable or not self.accept or not self.addresses:
                return
            listener = link.Listener(self.node, Collector(self.queue, run=self.run), self.addresses, self.port)
            try:
                listener.start()
            except link.LinkError as error:
                self._failure = str(error)
                debug_log.note("the link could not listen", error=self._failure)
                return
            except Exception as error:                    # anything else: the window must still open
                self._failure = f"{type(error).__name__}: {error}"
                debug_log.note("the link could not listen", error=self._failure)
                return
            self.listener = listener
            debug_log.note("the link listens", bound=listener.bound, problems=listener.problems)

    def _stop_listener(self) -> None:
        listener, self.listener = self.listener, None
        if listener is not None:
            listener.stop()

    @property
    def listening(self) -> List[Tuple[str, int]]:
        listener = self.listener
        return list(listener.bound) if listener is not None else []

    @property
    def problems(self) -> Dict[str, str]:
        """Address -> why it could not be listened on."""
        listener = self.listener
        found = dict(listener.problems) if listener is not None else {}
        if self._failure:
            found.update({address: self._failure for address in self.addresses})
        return found

    def status_text(self) -> str:
        if self.unavailable:
            return self.unavailable
        if not self.accept:
            return "Not accepting downloads from other Accipios."
        if not self.addresses:
            return "Accepting is on, but no address is ticked, so nothing listens."
        bound = self.listening
        sentences = []
        if bound:
            ports = {port for _, port in bound}
            names = _joined([address for address, _ in bound])
            sentences.append(f"Listening on {names}, port {ports.pop()}." if len(ports) == 1 else
                             "Listening on " + _joined([f"{a} (port {p})" for a, p in bound]) + ".")
        else:
            sentences.append("Not listening on any address.")
        for address, reason in self.problems.items():
            sentences.append(f"Could not listen on {address}: {str(reason).rstrip('.')}.")
        return " ".join(sentences)

    def reach(self, peer_id: str, timeout: float = 3.0) -> str:
        """Empty when the paired Accipio answers, else why not, as a sentence. Blocking: call it on a thread."""
        peer = self.node.peer(peer_id)
        if peer is None:
            return "This Accipio is not paired with it (any more)."
        if not peer.host or not peer.port:
            return f"No address is known for {peer.name or peer.id}."
        try:
            reply = link.send(self.node, peer, link.hello(), timeout)
        except link.LinkError as error:
            return str(error)
        except Exception as error:
            return f"{peer.name or peer.id} could not be asked ({error})."
        if reply.get("ok"):
            return ""
        return f"{peer.name or peer.id} answered no: {reply.get('error') or 'no reason given'}."

    def forget(self, peer_id: str) -> None:
        """End the pairing from this side: the key goes, the downloads this PC collected for that Accipio are
        marked as heard, and the sender's pump finds its rows for a peer that is gone."""
        self.node.forget(peer_id)
        self.run(self.queue.forget_sender, peer_id)
        self.sender.nudge()

    def stop(self) -> None:
        with self._lock:
            self._stop_listener()
            self.accept, self.addresses, self.port = False, [], 0


# -- the collector ---------------------------------------------------------------------------------------------

class Collector:
    """The ``handler`` of a ``link.Listener``: queues what paired Accipios send and tells them how it went.
    ``run`` is how the queue's network work is started (the window's worker thread; in place by default)."""

    def __init__(self, queue, run: Optional[Callable] = None):
        self.queue = queue
        self.run = run or _in_place

    def __call__(self, message: Dict, peer: link.Peer) -> Dict:
        kind = message.get("type")
        if kind == "add":
            return self._add(message, peer)
        if kind == "status":
            for row_id in message.get("heard", []):
                self.queue.heard(row_id, peer.id)
            return {"ok": True, "downloads": [self.queue.report(row_id, peer.id) for row_id in message["ids"]]}
        if kind == "remove":
            for row_id in message["ids"]:
                self.run(self.queue.remove_for, row_id, peer.id)
            return {"ok": True}
        return {"ok": False, "error": f"unknown message {kind!r}"}

    def _add(self, message: Dict, peer: link.Peer) -> Dict:
        url = message["url"].strip()
        if not takeable(url):
            return {"ok": False, "error": "not a link Accipio can download"}
        headers = {name: message[key] for key, name in
                   (("cookies", "Cookie"), ("referrer", "Referer"), ("user_agent", "User-Agent")) if message.get(key)}
        row_id, new = self.queue.receive(url, peer_id=peer.id, peer_name=peer.name or peer.id,
                                         page_url=message.get("page_url", ""), headers=headers,
                                         ref=message.get("ref", ""))
        debug_log.note("from another Accipio", sender=peer.name, url=url, row=row_id, new=new,
                       cookies="Cookie" in headers)
        if new:
            self.run(self.queue.begin, row_id)
        return {"ok": True, "id": row_id}
