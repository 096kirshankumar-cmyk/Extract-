"""Multi-key Gemini pool: discovery/dedupe, rotation, 429
classification, persistence, and the llm._call wiring."""
import io
import json
import time
import urllib.error

from qbank import keypool
from qbank.keypool import KeyPool, PoolExhausted, discover_keys


def test_discover_merges_and_dedupes():
    env = {"GEMINI_API_KEYS": "a1,a2 a3\na1",
           "GEMINI_API_KEY_1": "b1",
           "GEMINI_API_KEY_2": "b2",
           "GEMINI_API_KEY": "a2"}
    assert discover_keys(env) == ["a1", "a2", "a3", "b1", "b2"]


def test_single_key_still_works():
    assert discover_keys({"GEMINI_API_KEY": "solo"}) == ["solo"]


def test_daily_cap_advances_then_exhausts():
    pool = KeyPool(["k1", "k2"], max_calls_per_day=2)
    assert pool.acquire() == "k1"
    pool.note_call()
    pool.note_call()                      # cap reached -> exhausted
    assert pool.acquire() == "k2"
    pool.note_429("PerDayPerProjectPerModelFreeTierRequests exceeded")
    try:
        pool.acquire()
        raise AssertionError("expected PoolExhausted")
    except PoolExhausted:
        pass


def test_per_minute_429_cools_down_not_exhausts():
    pool = KeyPool(["k1", "k2"], max_calls_per_day=100)
    pool.note_429("Resource exhausted: PerMinutePerProjectPerModel...")
    assert pool.st["key1"]["status"] == "cooldown"
    assert pool.acquire() == "k2"         # cooled key is skipped
    pool.st["key1"]["cooldown_until"] = time.time() - 1
    pool.active = 0                       # pointer back at key1
    assert pool.acquire() == "k1"         # cooled down -> usable again
    assert pool.st["key1"]["status"] != "exhausted"


def test_state_persists_and_never_leaks_key_material(tmp_path):
    sp = tmp_path / "keypool_state.json"
    p1 = KeyPool(["secret-key-A", "secret-key-B"], max_calls_per_day=10,
                 state_path=sp)
    p1.note_call()
    p1.note_call()
    p2 = KeyPool(["secret-key-A", "secret-key-B"], max_calls_per_day=10,
                 state_path=sp)
    assert p2.st["key1"]["calls"] == 2
    raw = sp.read_text()
    assert "secret-key-A" not in raw and "secret-key-B" not in raw
    assert p2.st["key1"]["fp"] in raw     # fingerprint yes, key no


def test_state_ignores_different_key_material(tmp_path):
    sp = tmp_path / "keypool_state.json"
    KeyPool(["aaa", "bbb"], max_calls_per_day=10,
            state_path=sp).note_call()
    p2 = KeyPool(["ccc", "ddd"], max_calls_per_day=10, state_path=sp)
    assert p2.st["key1"]["calls"] == 0    # different keys: fresh start


def test_day_rollover_resets_counters(monkeypatch):
    pool = KeyPool(["k1"], max_calls_per_day=1)
    pool.note_call()
    assert pool.st["key1"]["status"] == "exhausted"
    monkeypatch.setattr(keypool.time, "strftime",
                        lambda fmt: "2030-01-01")
    assert pool.acquire() == "k1"         # new day -> usable again
    assert pool.st["key1"]["calls"] == 0


# ---- llm wiring ----------------------------------------------------------

def _resp(rows):
    return {"candidates": [{"content": {"parts": [
        {"text": json.dumps({"rows": rows})}]}}]}


def _http429(body: bytes):
    return urllib.error.HTTPError(
        "http://x", 429, "Too Many Requests", {}, io.BytesIO(body))


def test_call_rotates_key_on_daily_429(monkeypatch):
    from qbank import llm
    pool = KeyPool(["k1", "k2"], max_calls_per_day=100)
    seen = []

    def fake_post(url, payload, key):
        seen.append(key)
        if key == "k1":
            raise _http429(b'{"error":{"message":"quota exceeded for '
                           b'metric PerDayPerProjectPerModel"}}')
        return _resp([["a", "b"]])

    monkeypatch.setattr(llm, "_post", fake_post)
    rows = llm._call(pool, "", "m", {})
    assert rows == [["a", "b"]]
    assert seen == ["k1", "k2"]
    assert pool.st["key1"]["status"] == "exhausted"
    assert pool.st["key2"]["calls"] == 1


def test_call_gives_up_when_whole_pool_dead(monkeypatch):
    from qbank import llm
    pool = KeyPool(["k1", "k2"], max_calls_per_day=100)

    def fake_post(url, payload, key):
        raise _http429(b'{"error":{"message":"PerDayPerProject quota"}}')

    monkeypatch.setattr(llm, "_post", fake_post)
    assert llm._call(pool, "", "m", {}) is None
    assert pool.st["key1"]["status"] == "exhausted"
    assert pool.st["key2"]["status"] == "exhausted"


def test_enabled_follows_keypool_forms(monkeypatch):
    from qbank import llm
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("QBANK_LLM_TABLES", raising=False)
    assert not llm.enabled()
    monkeypatch.setenv("GEMINI_API_KEY_3", "k3")
    assert llm.enabled()
    monkeypatch.setenv("QBANK_LLM_TABLES", "0")
    assert not llm.enabled()
