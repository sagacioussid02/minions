"""Per-project sprint counter — JSON backend atomicity + monotonicity."""

from __future__ import annotations

import threading
from pathlib import Path

from minions.sprints.store import SprintCounterStore


def test_first_bump_returns_zero(tmp_path: Path) -> None:
    store = SprintCounterStore(tmp_path / "sprints.json")
    assert store.current("Demo") is None
    assert store.bump("Demo") == 0
    assert store.current("Demo") == 0


def test_subsequent_bumps_monotonically_increase(tmp_path: Path) -> None:
    store = SprintCounterStore(tmp_path / "sprints.json")
    assert store.bump("Demo") == 0
    assert store.bump("Demo") == 1
    assert store.bump("Demo") == 2
    assert store.current("Demo") == 2


def test_counters_are_per_project(tmp_path: Path) -> None:
    store = SprintCounterStore(tmp_path / "sprints.json")
    assert store.bump("Demo") == 0
    assert store.bump("demo_three") == 0
    assert store.bump("Demo") == 1
    assert store.bump("demo_three") == 1
    assert store.current("Demo") == 1
    assert store.current("demo_three") == 1


def test_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "sprints.json"
    s1 = SprintCounterStore(path)
    s1.bump("Demo")
    s1.bump("Demo")
    s2 = SprintCounterStore(path)
    assert s2.current("Demo") == 1
    assert s2.bump("Demo") == 2


def test_concurrent_bumps_do_not_skip_or_collide(tmp_path: Path) -> None:
    """File-lock guards against two threads observing the same `current`."""
    store = SprintCounterStore(tmp_path / "sprints.json")
    n_threads = 10
    results: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        n = store.bump("Demo")
        with lock:
            results.append(n)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == list(range(n_threads))


def test_list_all_returns_every_project(tmp_path: Path) -> None:
    store = SprintCounterStore(tmp_path / "sprints.json")
    store.bump("Demo")
    store.bump("demo_three")
    store.bump("demo_three")
    all_rows = sorted(store.list_all(), key=lambda r: r.project)
    assert [r.project for r in all_rows] == ["Demo", "demo_three"]
    assert [r.current_sprint_number for r in all_rows] == [0, 1]
