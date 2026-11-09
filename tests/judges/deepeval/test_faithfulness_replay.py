"""The DeepEval faithfulness judge on the gold set, replayed from recorded scores (MTLJ-90).

Runs in the default suite and in CI with no API key and no network: every score comes from
tests/fixtures/judge/deepeval-faithfulness.jsonl, recorded with scripts/record_fixtures.py, on the
records and mutants saved in tests/fixtures/gold-set/. Replay uses those saved inputs and the
judge identity in the file, so editing an operator or upgrading DeepEval doesn't break it. See
docs/fixtures.md.
"""

from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from statistics import mean

import pytest

from mtlj.engine.fixtures import FixtureStore, load_inputs
from mtlj.engine.runner import DEFAULT_SAMPLES, MemorySink, run_judge_sync
from mtlj.judges.deepeval.faithfulness import DeepEvalFaithfulnessJudge

FIXTURES_DIR = Path(__file__).parents[2] / "fixtures"
FIXTURES = FIXTURES_DIR / "judge" / "deepeval-faithfulness.jsonl"
GOLD_SET = FIXTURES_DIR / "gold-set"


def never_called(input, output, passages):
    raise AssertionError("replay must not call the judge")


def replay_judge(store: FixtureStore) -> DeepEvalFaithfulnessJudge:
    """The recorded judge, as it was recorded, with a measure that refuses to run.

    Its version is the recorded DeepEval version, not the installed one: replay never runs
    DeepEval, so an upgrade must not change which scores it looks up.
    """
    recorded = store.judge
    assert recorded is not None, f"{store.path} holds no fixtures"
    judge = DeepEvalFaithfulnessJudge(
        recorded.model,
        penalize_ambiguous_claims=recorded.name.endswith("-penalized"),
        measure=never_called,
    )
    judge.version = recorded.version
    assert judge.name == recorded.name
    return judge


def _is_loopback(host: object) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False  # any other host name


@pytest.fixture
def no_network(monkeypatch):
    """Fail any attempt to reach another machine during the test.

    Loopback stays allowed: on Windows, asyncio's event loop wakes itself through a socket pair
    that connects to 127.0.0.1, which is no network call.
    """
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guard(real):
        def connect(sock, address, *args, **kwargs):
            host = address[0] if isinstance(address, tuple) else address
            if not _is_loopback(host):
                raise AssertionError(f"a network connection was attempted: {address!r}")
            return real(sock, address, *args, **kwargs)

        return connect

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not _is_loopback(host):
            raise AssertionError(f"a network connection was attempted: lookup of {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guard(real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(real_connect_ex))
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def test_the_network_guard_refuses_other_machines(no_network):
    with pytest.raises(AssertionError, match="network connection was attempted"):
        socket.create_connection(("api.openai.com", 443), timeout=1)
    with pytest.raises(AssertionError, match="network connection was attempted"):
        socket.create_connection(("93.184.216.34", 443), timeout=1)


def test_the_network_guard_lets_asyncio_run(no_network):
    a, b = socket.socketpair()  # what asyncio's event loop needs, on every platform
    a.close()
    b.close()


def test_gold_set_replays_from_fixtures_with_no_judge_call_and_no_network(no_network):
    records, mutants = load_inputs(GOLD_SET)
    store = FixtureStore(FIXTURES, mode="replay")
    sink = MemorySink()

    summary = run_judge_sync(
        replay_judge(store), records, mutants, sink, cache=store, samples=DEFAULT_SAMPLES
    )

    expected = DEFAULT_SAMPLES * (len(records) + len(mutants))
    assert summary.ok == summary.cache_hits == expected
    assert summary.errors == 0
    rows = sink.evaluations
    assert all(r.cached and r.attempts == 0 for r in rows)
    assert all(r.n_claims is not None and r.verdict_yes is not None for r in rows)

    # The recorded scores mean something: swapping entities into an answer makes it less
    # faithful to its passages, on average, than the original.
    originals = mean(r.score for r in rows if r.kind == "original")
    swapped = mean(r.score for r in rows if r.operator_name == "entity_swap")
    assert swapped < originals
