"""Budget: what has been spent, what a batch will cost, and the guard that stops overspending.

  python scripts/budget.py status
  python scripts/budget.py film                     # whole-film estimate from shots.yaml
  python scripts/budget.py prices [--refresh]       # show / refresh prices from fal pricing API
  python scripts/budget.py reconcile                # pull actual charges per request (admin key)
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import common as C
import prompts as P


# --------------------------------------------------------------------------- guard
def guard(batch_usd: float, *, yes: bool, past_warning: bool = False, what: str = "batch"):
    """Raise SystemExit unless this batch is allowed. Called before every paid submit."""
    cfg = C.models_cfg()["budget"]
    batch = C.usd_to_eur(batch_usd)
    spent = C.spent_eur()
    cap, target = float(cfg["cap_eur"]), float(cfg["target_eur"])
    warn = float(cfg["warn_fraction"]) * target
    after = spent + batch
    print(f"\n  {what}: est €{batch:.2f}   spent so far €{spent:.2f}   after €{after:.2f}"
          f"   (stop-and-ask €{warn:.0f} / target €{target:.0f} / hard cap €{cap:.0f})")
    if C.MOCK:
        print("  [MOCK] no money will be spent")
        return
    if after > cap:
        C.die(f"would exceed the hard cap of €{cap:.0f}. Not running.")
    if after > warn and not past_warning:
        C.die(f"would pass €{warn:.0f} (80% of target). Ask Dima, then rerun with --past-warning.", 3)
    if batch > float(cfg["ask_above_eur"]) and not yes:
        print(f"  Batch is above €{cfg['ask_above_eur']} — needs Dima's OK. Rerun with --yes after approval.")
        sys.exit(2)


# --------------------------------------------------------------------------- reports
def status():
    cfg = C.models_cfg()["budget"]
    rows = C.cost_rows()
    spent = C.spent_eur()
    print(f"Spent: €{spent:.2f} of €{cfg['target_eur']} target (hard cap €{cfg['cap_eur']}), "
          f"{len(rows)} paid jobs")
    by = {}
    for r in rows:
        k = r["kind"]
        by[k] = by.get(k, 0.0) + C.usd_to_eur(float(r.get("actual_usd") or r["est_usd"] or 0))
    for k, v in sorted(by.items(), key=lambda kv: -kv[1]):
        print(f"  {k:10s} €{v:8.2f}")
    mock = len(C.cost_rows(include_mock=True)) - len(rows)
    if mock:
        print(f"  ({mock} mock jobs not counted)")
    pend = C.pending_jobs()
    if pend:
        print(f"  {len(pend)} unfinished jobs — run `python scripts/fal_gen.py resume`")


def film_estimate(n_stills: int = 3, retry: float | None = None, quiet=False):
    """Whole-film plan: stills × options, video seconds × tier price × retry factor, TTS, music."""
    mc = C.models_cfg()
    retry = retry or float(mc["budget"]["retry_factor"])
    shots = C.Shots()
    still = C.model("still")
    lines, total = [], 0.0
    ai = [s for s in shots.shots if s.get("type") in ("ai_video", "present_day")]
    usd = len(ai) * n_stills * float(still["usd"])
    lines.append((f"stills: {len(ai)} shots × {n_stills} options", usd))
    total += usd
    for tier in ("draft", "standard", "hero"):
        m = C.model(P.TIER_TO_MODEL[tier])
        secs = sum(P.fit_duration(m, float(s.get("duration_s", 5)))
                   for s in ai if s.get("model_tier", "standard") == tier)
        if secs:
            usd = secs * retry * float(m["usd"])
            lines.append((f"video {tier}: {secs}s × {retry} retries @ ${m['usd']}/s", usd))
            total += usd
    chars = sum(len(str(sc.get("narration_ru") or "")) for sc in shots.scenes)
    tts = C.model("tts")
    usd = chars / 1000 * float(tts["usd"]) * 2
    lines.append((f"narration: {chars} chars × 2 takes", usd))
    total += usd
    film_s = sum(float(s.get("duration_s", 5)) for s in shots.shots)
    mus = C.model("music")
    usd = math.ceil(film_s / 60) * float(mus["usd"]) * 2
    lines.append((f"music: {math.ceil(film_s/60)} min × 2 tries", usd))
    total += usd
    if not quiet:
        print(f"Film estimate ({len(shots.shots)} shots, ~{film_s/60:.1f} min, "
              f"{sum(float(s.get('duration_s',5)) for s in ai)/60:.1f} min AI video):")
        for label, u in lines:
            print(f"  {label:55s} ${u:8.2f}  €{C.usd_to_eur(u):8.2f}")
        print(f"  {'TOTAL':55s} ${total:8.2f}  €{C.usd_to_eur(total):8.2f}")
    return total


def prices(refresh: bool):
    mc = C.models_cfg()
    if refresh:
        C.require_key()
        import requests
        doc = C.load_yaml(C.PROJECT / "models.yaml")
        for name, m in doc["models"].items():
            r = requests.get("https://api.fal.ai/v1/models/pricing",
                             params={"endpoint_id": m["endpoint"]},
                             headers={"Authorization": f"Key {os.environ['FAL_KEY']}"}, timeout=30)
            if r.status_code != 200:
                print(f"  {name}: HTTP {r.status_code} — keep ${m['usd']}")
                continue
            data = r.json()
            items = data.get("prices") or data.get("items") or [data]
            price = items[0].get("unit_price") if items else None
            unit = items[0].get("unit") if items else None
            print(f"  {name}: API says {price} per {unit} (yaml: ${m['usd']} per {m['unit']})")
            # Units can differ (e.g. per 5s video); only auto-update when the unit matches.
            if price is not None and unit and str(unit).lower().startswith(m["unit"][:3]):
                m["usd"] = float(price)
        C.save_yaml(doc, C.PROJECT / "models.yaml")
        mc = C.models_cfg()
    for name, m in mc["models"].items():
        print(f"  {name:15s} ${float(m['usd']):.3f} per {m['unit']:8s} {m['endpoint']}")


def reconcile():
    """Fill actual_usd in costs.csv from fal billing events (needs FAL_ADMIN_KEY)."""
    C.load_env()
    key = os.environ.get("FAL_ADMIN_KEY") or os.environ.get("FAL_KEY")
    if not key:
        C.die("no key")
    import csv
    import requests
    rows = C.cost_rows(include_mock=True)
    todo = [r for r in rows if r["mock"] != "1" and not r["actual_usd"]]
    for i in range(0, len(todo), 50):
        ids = [r["request_id"] for r in todo[i:i + 50]]
        resp = requests.get("https://api.fal.ai/v1/models/billing-events",
                            params=[("request_id", x) for x in ids],
                            headers={"Authorization": f"Key {key}"}, timeout=60)
        if resp.status_code != 200:
            C.die(f"billing API HTTP {resp.status_code}: {resp.text[:200]}")
        events = resp.json().get("billing_events") or resp.json().get("items") or []
        cost = {e.get("request_id"): e.get("cost_total") for e in events}
        for r in todo[i:i + 50]:
            if cost.get(r["request_id"]) is not None:
                r["actual_usd"] = f"{float(cost[r['request_id']]):.4f}"
    with open(C.COSTS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=C.COST_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"reconciled {sum(1 for r in todo if r['actual_usd'])}/{len(todo)} jobs")
    status()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    f = sub.add_parser("film")
    f.add_argument("--stills", type=int, default=3)
    f.add_argument("--retry", type=float)
    p = sub.add_parser("prices")
    p.add_argument("--refresh", action="store_true")
    sub.add_parser("reconcile")
    a = ap.parse_args()
    if a.cmd == "status":
        status()
    elif a.cmd == "film":
        film_estimate(a.stills, a.retry)
    elif a.cmd == "prices":
        prices(a.refresh)
    elif a.cmd == "reconcile":
        reconcile()


if __name__ == "__main__":
    main()
