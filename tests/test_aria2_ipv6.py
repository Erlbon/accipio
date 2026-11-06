"""aria2 and a PC without IPv6 (2026-11-06): vikingfile's storage host has an IPv6 address, the PC had no IPv6 route,
and aria2 gave up with "A socket operation was attempted to an unreachable network" where Chrome fell back to IPv4."""
import socket

from accipio.core import aria2


def test_without_an_ipv6_route_aria2_is_told_to_leave_ipv6_alone(tmp_path):
    session = str(tmp_path / "session.txt")
    assert "--disable-ipv6=true" in aria2.Aria2("aria2c", session, ipv6=False).command_line()
    assert "--disable-ipv6=true" not in aria2.Aria2("aria2c", session, ipv6=True).command_line()


def test_the_ipv6_check_sends_nothing_and_says_no_when_there_is_no_route(monkeypatch):
    calls = []

    class NoRoute:
        def __init__(self, family, kind):
            calls.append((family, kind))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def connect(self, address):
            raise OSError(10051, "A socket operation was attempted to an unreachable network")
    monkeypatch.setattr(aria2.socket, "socket", NoRoute)
    assert aria2.ipv6_works() is False
    assert calls == [(socket.AF_INET6, socket.SOCK_DGRAM)]           # UDP connect only picks a route


def test_the_ipv6_check_says_yes_when_a_route_exists(monkeypatch):
    class Route:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def connect(self, address):
            pass
    monkeypatch.setattr(aria2.socket, "socket", Route)
    assert aria2.ipv6_works() is True


def test_unset_means_the_engine_checks_for_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(aria2, "ipv6_works", lambda: False)
    assert "--disable-ipv6=true" in aria2.Aria2("aria2c", str(tmp_path / "s.txt")).command_line()
