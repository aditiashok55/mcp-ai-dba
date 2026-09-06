import time

from mcp_server.rate_limit import RateLimiter


def test_token_bucket_caps_bursts():
    rl = RateLimiter(capacity=3, refill_per_sec=1000.0)
    assert rl.try_acquire("t")
    assert rl.try_acquire("t")
    assert rl.try_acquire("t")
    assert not rl.try_acquire("t")


def test_token_bucket_refills():
    rl = RateLimiter(capacity=1, refill_per_sec=1000.0)
    assert rl.try_acquire("t")
    assert not rl.try_acquire("t")
    time.sleep(0.005)  # 5ms * 1000/s = 5 tokens; capped at 1
    assert rl.try_acquire("t")


def test_buckets_are_per_key():
    rl = RateLimiter(capacity=1, refill_per_sec=0.0)
    assert rl.try_acquire("a")
    assert rl.try_acquire("b")
    assert not rl.try_acquire("a")
