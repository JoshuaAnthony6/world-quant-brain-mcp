#!/usr/bin/env python3
"""Live verification for the BRAIN Labs MCP tools.

Run inside the mcp container:
    docker compose exec -T mcp python test_labs_live.py [N]

Part A: open_labs_session N times (default 20) sequentially -> stability.
Part B: prove the single-concurrency lock serializes Labs operations (max in-flight == 1).
"""
import asyncio
import sys
import time

from labs_functions import labs_client
from main import load_config, brain_client


async def stability(n: int):
    cfg = load_config().get("credentials", {})
    email, password = cfg.get("email"), cfg.get("password")
    await brain_client.authenticate(email, password)

    ok = 0
    rows = []
    for i in range(1, n + 1):
        t0 = time.time()
        try:
            res = await labs_client.open_labs_session(email, password)
            dt = time.time() - t0
            good = res.get("status") == "ok" and bool(res.get("token"))
            ok += 1 if good else 0
            rows.append((i, "OK" if good else "BAD", round(dt, 1), (res.get("token") or "")[:18]))
        except Exception as e:
            rows.append((i, "ERR", round(time.time() - t0, 1), str(e)[:60]))
        print(f"  run {i:>2}: {rows[-1][1]:<3} {rows[-1][2]:>5}s token={rows[-1][3]}", flush=True)
    print(f"\nStability: {ok}/{n} successful Labs sign-ins")
    return ok


async def concurrency_lock(workers: int = 8):
    """All Labs ops share labs_client._labs_operation_semaphore; prove only 1 runs at once."""
    active = 0
    max_active = 0
    sem = labs_client._labs_operation_semaphore

    async def probe(i):
        nonlocal active, max_active
        async with sem:
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.15)
            active -= 1

    await asyncio.gather(*(probe(i) for i in range(workers)))
    print(f"Concurrency: max simultaneous holders of the Labs lock = {max_active} "
          f"(limit={labs_client.max_concurrency}) -> {'PASS' if max_active == 1 else 'FAIL'}")
    return max_active


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    print("== Part B: concurrency lock ==")
    mx = await concurrency_lock()
    print("\n== Part A: stability x%d ==" % n)
    ok = await stability(n)
    print("\n=== RESULT ===")
    print(f"concurrency_max={mx} stability={ok}/{n}")
    sys.exit(0 if (mx == 1 and ok == n) else 1)


if __name__ == "__main__":
    asyncio.run(main())
