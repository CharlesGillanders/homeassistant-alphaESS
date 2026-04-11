"""
AlphaESS OpenAPI Rate Limit Stress Tester

Last Test Run: 2024-06-15, No rate limit detected after 500+ calls over 5 minutes, including bursts of 50 concurrent calls and sustained rapid fire for 60 seconds.

Aggressively hammers the API with escalating call volumes to find the rate limit.
Phases:
  1. Discover serials
  2. Rapid sequential — 100 calls, zero delay
  3. Parallel bursts — 10/20/50 concurrent
  4. Sustained machine-gun — as many as possible for 60s
Stops early if a rate limit is detected (HTTP 429 or API error code).
"""

import asyncio
import hashlib
import time
import json
import aiohttp

APP_ID = ""
APP_SECRET = ""
BASE_URL = "https://openapi.alphaess.com/api"


def make_headers():
    timestamp = str(int(time.time()))
    sign_str = APP_ID + APP_SECRET + timestamp
    sign = hashlib.sha512(sign_str.encode("ascii")).hexdigest()
    return {
        "Content-Type": "application/json",
        "appId": APP_ID,
        "timeStamp": timestamp,
        "sign": sign,
    }


def is_ok(r):
    return r["status"] == 200 and r.get("code") in (None, 200)


def is_rate_limited(r):
    if r["status"] == 429:
        return True
    if r.get("code") not in (None, 200) and r.get("code") is not None:
        msg = (r.get("msg") or "").lower()
        if any(kw in msg for kw in ("rate", "limit", "throttl", "too many", "frequent", "busy")):
            return True
    return False


async def api_get(session, url, label=""):
    headers = make_headers()
    t0 = time.monotonic()
    try:
        async with session.get(url, headers=headers, ssl=True, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            elapsed = (time.monotonic() - t0) * 1000
            body = await resp.text()
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = body
            return {
                "label": label,
                "status": resp.status,
                "elapsed_ms": round(elapsed, 1),
                "code": data.get("code") if isinstance(data, dict) else None,
                "msg": data.get("msg", data.get("info", "")) if isinstance(data, dict) else str(data)[:200],
            }
    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        return {
            "label": label,
            "status": -1,
            "elapsed_ms": round(elapsed, 1),
            "code": None,
            "msg": str(e)[:200],
        }


def pr(r, idx=None):
    prefix = f"  [{idx:>4}]" if idx is not None else "      "
    status_str = f"HTTP {r['status']}" if r['status'] != -1 else "ERROR"
    code_str = f" code={r['code']}" if r['code'] is not None else ""
    rl = " *** RATE LIMITED ***" if is_rate_limited(r) else ""
    print(f"{prefix} {r['label']:40s} | {status_str:8s}{code_str} | {r['elapsed_ms']:7.1f}ms | {r['msg'][:60]}{rl}")


def summary(results, label):
    ok = sum(1 for r in results if is_ok(r))
    rl = sum(1 for r in results if is_rate_limited(r))
    err = len(results) - ok - rl
    avg = sum(r["elapsed_ms"] for r in results) / len(results) if results else 0
    print(f"\n  {label}: {ok} OK, {rl} rate-limited, {err} other errors / {len(results)} total | avg {avg:.0f}ms")
    return rl


async def main():
    print("=" * 110)
    print("  AlphaESS OpenAPI — AGGRESSIVE RATE LIMIT STRESS TEST")
    print("=" * 110)
    global_start = time.monotonic()
    all_results = []

    connector = aiohttp.TCPConnector(limit=50)  # allow up to 50 concurrent connections
    async with aiohttp.ClientSession(connector=connector) as session:

        # ── Phase 1: Discover serial numbers ──
        print("\n── Phase 1: Discover systems ──")
        headers = make_headers()
        async with session.get(f"{BASE_URL}/getEssList", headers=headers, ssl=True) as resp:
            body = await resp.json()
        serials = []
        if body.get("data"):
            for unit in body["data"]:
                sn = unit.get("sysSn", "")
                if sn:
                    serials.append(sn)
                    print(f"  Found: {sn} ({unit.get('minv', '?')})")
        if not serials:
            print("  No systems found! Exiting.")
            return

        today = time.strftime("%Y-%m-%d")
        sn = serials[0]  # primary serial for testing
        test_url = f"{BASE_URL}/getLastPowerData?sysSn={sn}"

        # ── Phase 2: 100 sequential calls, ZERO delay ──
        print(f"\n── Phase 2: 100 sequential calls to getLastPowerData [{sn[-6:]}], ZERO delay ──")
        phase2 = []
        rate_hit = False
        t0 = time.monotonic()
        for i in range(100):
            r = await api_get(session, test_url, f"seq #{i+1}")
            phase2.append(r)
            # Print every 10th, plus any errors/rate-limits
            if (i + 1) % 10 == 0 or not is_ok(r):
                pr(r, i + 1)
            if is_rate_limited(r):
                rate_hit = True
                print(f"\n  *** RATE LIMIT HIT at call #{i+1} after {time.monotonic()-t0:.1f}s ***")
                break
        elapsed_phase2 = time.monotonic() - t0
        rl2 = summary(phase2, f"Phase 2 ({elapsed_phase2:.1f}s)")
        all_results.extend(phase2)
        print(f"  Effective rate: {len(phase2)/elapsed_phase2:.1f} calls/sec")

        if rate_hit:
            print("\n  Rate limit found in Phase 2! Skipping remaining phases.")
        else:
            # ── Phase 3: Parallel bursts of 10, 20, 50 ──
            for burst_size in [10, 20, 50]:
                print(f"\n── Phase 3-{burst_size}: {burst_size} simultaneous parallel calls ──")
                tasks = [api_get(session, test_url, f"burst-{burst_size} #{j+1}") for j in range(burst_size)]
                t0 = time.monotonic()
                burst = await asyncio.gather(*tasks)
                elapsed_burst = time.monotonic() - t0
                for i, r in enumerate(burst):
                    if not is_ok(r) or i == 0 or i == len(burst) - 1:
                        pr(r, i + 1)
                rl_b = summary(list(burst), f"Burst-{burst_size} ({elapsed_burst:.1f}s)")
                all_results.extend(burst)
                print(f"  Effective rate: {burst_size/elapsed_burst:.1f} calls/sec")
                if rl_b:
                    rate_hit = True
                    print(f"\n  *** RATE LIMIT HIT in burst-{burst_size}! ***")
                    break

        if not rate_hit:
            # ── Phase 4: Mixed endpoints, 200 sequential, zero delay ──
            print(f"\n── Phase 4: 200 sequential calls, mixed endpoints, ZERO delay ──")
            mixed_urls = [
                (f"{BASE_URL}/getLastPowerData?sysSn={s}", f"lastPower [{s[-6:]}]")
                for s in serials
            ] + [
                (f"{BASE_URL}/getSumDataForCustomer?sysSn={s}", f"sumData [{s[-6:]}]")
                for s in serials
            ] + [
                (f"{BASE_URL}/getOneDateEnergyBySn?sysSn={s}&queryDate={today}", f"oneDateEnergy [{s[-6:]}]")
                for s in serials
            ] + [
                (f"{BASE_URL}/getChargeConfigInfo?sysSn={s}", f"chargeConfig [{s[-6:]}]")
                for s in serials
            ]
            phase4 = []
            t0 = time.monotonic()
            idx = 0
            while idx < 200:
                url, label = mixed_urls[idx % len(mixed_urls)]
                r = await api_get(session, url, f"mix #{idx+1} {label}")
                phase4.append(r)
                if (idx + 1) % 20 == 0 or not is_ok(r):
                    pr(r, idx + 1)
                if is_rate_limited(r):
                    rate_hit = True
                    print(f"\n  *** RATE LIMIT HIT at call #{idx+1} after {time.monotonic()-t0:.1f}s ***")
                    break
                idx += 1
            elapsed_phase4 = time.monotonic() - t0
            rl4 = summary(phase4, f"Phase 4 ({elapsed_phase4:.1f}s)")
            all_results.extend(phase4)
            print(f"  Effective rate: {len(phase4)/elapsed_phase4:.1f} calls/sec")

        if not rate_hit:
            # ── Phase 5: 60-second sustained fire, as fast as possible ──
            print(f"\n── Phase 5: Sustained rapid fire for 60 seconds ──")
            phase5 = []
            t0 = time.monotonic()
            idx = 0
            while (time.monotonic() - t0) < 60:
                s = serials[idx % len(serials)]
                r = await api_get(session, f"{BASE_URL}/getLastPowerData?sysSn={s}", f"fire #{idx+1} [{s[-6:]}]")
                phase5.append(r)
                if (idx + 1) % 25 == 0:
                    elapsed_so_far = time.monotonic() - t0
                    ok_so_far = sum(1 for x in phase5 if is_ok(x))
                    rl_so_far = sum(1 for x in phase5 if is_rate_limited(x))
                    print(f"  [{elapsed_so_far:5.1f}s] {idx+1} calls | {ok_so_far} OK | {rl_so_far} rate-limited | {(idx+1)/elapsed_so_far:.1f} calls/sec")
                if is_rate_limited(r):
                    rate_hit = True
                    elapsed_so_far = time.monotonic() - t0
                    print(f"\n  *** RATE LIMIT HIT at call #{idx+1} after {elapsed_so_far:.1f}s ***")
                    pr(r, idx + 1)
                    # Keep going for a few more to see the pattern
                    for extra in range(10):
                        r2 = await api_get(session, f"{BASE_URL}/getLastPowerData?sysSn={serials[0]}", f"post-limit #{extra+1}")
                        phase5.append(r2)
                        pr(r2, idx + 2 + extra)
                        await asyncio.sleep(1)
                    break
                idx += 1
            elapsed_phase5 = time.monotonic() - t0
            rl5 = summary(phase5, f"Phase 5 ({elapsed_phase5:.1f}s)")
            all_results.extend(phase5)
            if phase5:
                print(f"  Effective rate: {len(phase5)/elapsed_phase5:.1f} calls/sec")

        if not rate_hit:
            # ── Phase 6: Parallel waves — 10 waves of 20 concurrent ──
            print(f"\n── Phase 6: 10 waves of 20 concurrent calls (200 total) ──")
            phase6 = []
            t0 = time.monotonic()
            for wave in range(10):
                tasks = [
                    api_get(session, f"{BASE_URL}/getLastPowerData?sysSn={serials[j % len(serials)]}", f"wave{wave+1} #{j+1}")
                    for j in range(20)
                ]
                results_wave = await asyncio.gather(*tasks)
                phase6.extend(results_wave)
                ok_w = sum(1 for x in results_wave if is_ok(x))
                rl_w = sum(1 for x in results_wave if is_rate_limited(x))
                elapsed_w = time.monotonic() - t0
                print(f"  Wave {wave+1:>2}: {ok_w}/20 OK, {rl_w} rate-limited | cumulative {len(phase6)} calls in {elapsed_w:.1f}s")
                if rl_w:
                    rate_hit = True
                    print(f"\n  *** RATE LIMIT HIT in wave {wave+1}! ***")
                    break
            elapsed_phase6 = time.monotonic() - t0
            rl6 = summary(phase6, f"Phase 6 ({elapsed_phase6:.1f}s)")
            all_results.extend(phase6)
            print(f"  Effective rate: {len(phase6)/elapsed_phase6:.1f} calls/sec")

        # ── Final Summary ──
        total_elapsed = time.monotonic() - global_start
        print("\n" + "=" * 110)
        print("  FINAL SUMMARY")
        print("=" * 110)
        total = len(all_results)
        total_ok = sum(1 for r in all_results if is_ok(r))
        total_rl = sum(1 for r in all_results if is_rate_limited(r))
        total_err = total - total_ok - total_rl
        avg = sum(r["elapsed_ms"] for r in all_results) / total if total else 0

        print(f"  Total calls:       {total}")
        print(f"  Successful:        {total_ok}")
        print(f"  Rate limited:      {total_rl}")
        print(f"  Other errors:      {total_err}")
        print(f"  Total time:        {total_elapsed:.1f}s")
        print(f"  Overall rate:      {total/total_elapsed:.1f} calls/sec")
        print(f"  Avg response time: {avg:.0f}ms")

        if total_rl:
            print(f"\n  *** RATE LIMIT WAS FOUND ***")
            # Find first rate-limited result
            for i, r in enumerate(all_results):
                if is_rate_limited(r):
                    print(f"  First rate-limited response at call #{i+1}:")
                    pr(r, i + 1)
                    break
        else:
            print(f"\n  *** NO RATE LIMIT DETECTED after {total} calls in {total_elapsed:.1f}s ***")

        # Show any non-200 API codes
        api_errs = [r for r in all_results if r["status"] == 200 and r.get("code") not in (None, 200)]
        if api_errs:
            print(f"\n  API-level errors (HTTP 200 but non-success code):")
            for r in api_errs[:20]:
                pr(r)


if __name__ == "__main__":
    asyncio.run(main())
