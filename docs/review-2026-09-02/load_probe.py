"""Closed-loop load probe against the probe server (never against production).

Each scenario runs `concurrency` workers that fire requests back to back for `seconds`.
Reports achieved RPS, latency percentiles, and non-2xx counts. httpx keeps connections
alive, so this measures the server, not TCP setup.
"""
import asyncio
import json
import os
import statistics
import sys
import time

import httpx

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8765")
TOKEN = os.environ.get("PROBE_TOKEN", "")
SECONDS = float(os.environ.get("PROBE_SECONDS", "12"))


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1)))))
    return xs[k]


async def worker(client, req, deadline, lat, errs):
    while time.perf_counter() < deadline:
        t0 = time.perf_counter()
        try:
            r = await client.request(req["method"], req["path"], json=req.get("json"),
                                     headers=req.get("headers"))
            lat.append(time.perf_counter() - t0)
            if r.status_code >= 400:
                errs[r.status_code] = errs.get(r.status_code, 0) + 1
        except Exception as e:  # noqa: BLE001
            lat.append(time.perf_counter() - t0)
            errs[type(e).__name__] = errs.get(type(e).__name__, 0) + 1


async def run_scenario(name, req, concurrency, seconds):
    lat, errs = [], {}
    limits = httpx.Limits(max_connections=concurrency + 4, max_keepalive_connections=concurrency + 4)
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0, limits=limits) as client:
        # warm-up
        try:
            await client.request(req["method"], req["path"], json=req.get("json"), headers=req.get("headers"))
        except Exception:
            pass
        deadline = time.perf_counter() + seconds
        t0 = time.perf_counter()
        await asyncio.gather(*[worker(client, req, deadline, lat, errs) for _ in range(concurrency)])
        elapsed = time.perf_counter() - t0
    n = len(lat)
    row = {
        "scenario": name, "concurrency": concurrency, "requests": n,
        "rps": round(n / elapsed, 1) if elapsed else 0,
        "p50_ms": round(pct(lat, 50) * 1000, 1), "p95_ms": round(pct(lat, 95) * 1000, 1),
        "p99_ms": round(pct(lat, 99) * 1000, 1), "max_ms": round(max(lat) * 1000, 1) if lat else 0,
        "errors": errs,
    }
    print(json.dumps(row), flush=True)
    return row


async def main():
    auth = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else None
    scenarios = [
        ("root_json_no_db", {"method": "GET", "path": "/"}),
        ("catalog_cached", {"method": "GET", "path": "/api/opportunities"}),
        ("catalog_cached_authed", {"method": "GET", "path": "/api/opportunities", "headers": auth}),
        ("data_load_authed", {"method": "POST", "path": "/api/data/load",
                              "json": {"keys": ["hs-tracker-data", "hs-tracker-saved", "student-profile"]},
                              "headers": auth}),
        ("ai_messages_mock", {"method": "POST", "path": "/api/messages",
                              "json": {"system": "You are a helpful assistant that ranks opportunities.",
                                       "userContent": "Rank these: A, B, C", "useWebSearch": False},
                              "headers": auth}),
        ("ai_claude_mock", {"method": "POST", "path": "/api/messages-claude",
                            "json": {"system": "Ask the student one short question about their interests.",
                                     "userContent": "hi", "useWebSearch": False},
                            "headers": auth}),
    ]
    only = set(sys.argv[1:])
    concs = [int(c) for c in os.environ.get("PROBE_CONC", "1,8,32").split(",")]
    results = []
    for name, req in scenarios:
        if only and name not in only:
            continue
        if "authed" in name and not TOKEN:
            continue
        for c in concs:
            results.append(await run_scenario(name, req, c, SECONDS))
    with open("load_results.json", "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
