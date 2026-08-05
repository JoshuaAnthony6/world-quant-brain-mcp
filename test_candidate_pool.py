"""Behavioural test for candidate_pool against a fake BRAIN client."""
import asyncio, json, os, sys, tempfile

TMP = tempfile.mkdtemp()
os.environ["CANDIDATE_POOL_FILE"] = os.path.join(TMP, "candidate_pool.json")
sys.path.insert(0, "/opt/project/world-quant-brain-mcp")

import candidate_pool as cp

# ---- fake client ---------------------------------------------------------- #
# Pairwise correlations we control exactly.
CORR = {
    ("A", "B"): 0.20,
    ("A", "C"): 0.85,   # A and C are near-duplicates -> submitting A kills C
    ("A", "D"): 0.10,
    ("B", "C"): 0.15,
    ("B", "D"): 0.45,   # above mutual(0.40) but below prod(0.70)
    ("C", "D"): 0.05,
}
META = {
    "A": ("analyst", 1.9, 1.80, 0.30),   # pyramid, mult, sharpe, prod_corr
    "B": ("news",    1.9, 1.70, 0.25),
    "C": ("analyst", 1.9, 1.60, 0.40),
    "D": ("macro",   1.9, 1.65, 0.20),
}

def c(a, b):
    if a == b: return 1.0
    return CORR.get((a, b)) or CORR.get((b, a)) or 0.0

class FakeClient:
    def __init__(self): self.prod_calls = 0
    async def get_alpha_details(self, aid):
        pyr, mult, sharpe, _ = META[aid]
        return {"id": aid, "status": "UNSUBMITTED", "code": f"expr({aid})",
                "settings": {"instrumentType": "EQUITY", "region": "GBR",
                             "universe": "TOP700", "delay": 1, "neutralization": "FAST"},
                "metrics": {"sharpe": sharpe, "fitness": 1.2, "turnover": 0.07,
                            "margin": 0.002, "two_year_sharpe": 1.8,
                            "sub_universe_sharpe": 1.1},
                "ra": {"failed_ra_count": 0, "failed_ppa_count": 0, "pyramid_short": pyr},
                "pyramids": {"list": [{"name": f"GBR/D1/{pyr.upper()}", "multiplier": mult}]}}
    async def get_mutual_correlation(self, ids, threshold=0.5, years=4):
        return {"matrix": {a: {b: c(a, b) for b in ids} for a in ids}, "missing_pnl": []}
    async def check_correlation(self, aid, ctype, thr):
        self.prod_calls += 1
        return {"checks": {"production": {"max_correlation": META[aid][3]}}}
    async def check_self_correlation(self, aid, threshold=0.7, correlation_type="self"):
        return {"max_correlation": 0.10}
    async def get_pyramid_alphas(self, s=None, e=None):
        # analyst already has 2 submitted; news/macro have 0.
        return {"pyramids": {"GBR": {"D1": {"analyst": 2, "news": 0, "macro": 0, "model": 4}}}}

FAIL = []
def check(label, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else f"   <-- {extra}"))
    if not cond: FAIL.append(label)

async def main():
    cl = FakeClient()

    print("\n[1] admission")
    r = await cp.add_candidate(cl, "A")
    check("A admitted into empty pool", r["added"], r)

    r = await cp.add_candidate(cl, "B")
    check("B admitted (corr 0.20 vs A)", r["added"], r)

    r = await cp.add_candidate(cl, "C")
    check("C REJECTED: 0.85 vs A breaks production safety", not r["added"], r)
    check("C rejection names the safety reason",
          any("push the other past the production gate" in x for x in r["reasons"]), r["reasons"])

    r = await cp.add_candidate(cl, "D")
    check("D REJECTED by diversity (0.45 vs B >= 0.40)", not r["added"], r)
    r = await cp.add_candidate(cl, "D", allow_diversity_fail=True)
    check("D admitted once diversity waived", r["added"], r)

    r = await cp.add_candidate(cl, "C", force=True)
    check("C admitted under force", r["added"], r)
    check("forced entry records why", bool(r["forced_reasons"]), r)

    print("\n[2] pyramid coverage (target=3)")
    cov = await cp.pyramid_coverage(cl, region="GBR", delay=1, target=3)
    rows = {x["pyramid"]: x for x in cov["rows"]}
    check("analyst: 2 submitted + 2 pooled = 4",
          rows["analyst"]["submitted"] == 2 and rows["analyst"]["pool"] == 2
          and rows["analyst"]["total"] == 4, rows.get("analyst"))
    check("analyst needs 1 more submission", rows["analyst"]["needed_submissions"] == 1,
          rows.get("analyst"))
    check("analyst reachable from pool",
          rows["analyst"]["status"] == "NEEDS_1_SUBMISSIONS_FROM_POOL", rows.get("analyst"))
    check("model already lit (4 submitted, 0 pooled)",
          rows["model"]["status"] == "OS_SUFFICIENT", rows.get("model"))
    check("news short by 2 (0 submitted, 1 pooled, needs 3)",
          rows["news"]["status"] == "SHORT_BY_2_CANDIDATES", rows.get("news"))

    print("\n[3] submission plan — the collateral-damage rule")
    plan = await cp.submission_plan(cl, max_submissions=4, region="GBR", delay=1, target=3)
    picked = [p["alpha_id"] for p in plan["plan"]]
    check("A and C are never in the same batch (0.85 apart)",
          not ("A" in picked and "C" in picked), picked)
    skipped_ids = [s["alpha_id"] for s in plan["skipped"]]
    check("the A/C conflict is reported as skipped",
          "C" in skipped_ids or "A" in skipped_ids, plan["skipped"])
    check("every candidate left behind stays under the gates",
          plan["all_remaining_safe"], plan["remaining_pool_after_batch"])

    proj = {r["alpha_id"]: r for r in plan["remaining_pool_after_batch"]}
    if "A" in picked and "C" in proj:
        check("C's projected prod corr reflects A's submission (0.85)",
              abs(proj["C"]["projected_prod_corr"] - 0.85) < 1e-6, proj["C"])
        check("C is flagged unsafe rather than silently left",
              proj["C"]["still_safe"] is False, proj["C"])

    print("\n[3b] deadlock resolution")
    check("conflicts reported for the A/C pair", len(plan["conflicts"]) >= 1, plan["conflicts"])
    plan3 = await cp.submission_plan(cl, max_submissions=4, region="GBR", delay=1,
                                     target=3, resolve_conflicts=True)
    p3 = [x["alpha_id"] for x in plan3["plan"]]
    check("with resolve_conflicts, the better of A/C IS submitted",
          ("A" in p3) or ("C" in p3), p3)
    check("the sacrificed one is named", bool(plan3["sacrificed"]), plan3["sacrificed"])
    check("sacrifice is deliberate, so the batch still reads as safe",
          plan3["all_remaining_safe"], plan3["remaining_pool_after_batch"])
    check("A (higher Sharpe) is kept over C", "A" in p3 and plan3["sacrificed"] == ["C"],
          (p3, plan3["sacrificed"]))

    print("\n[4] daily cap")
    plan2 = await cp.submission_plan(cl, max_submissions=1, region="GBR", delay=1)
    check("cap of 1 yields at most 1", len(plan2["plan"]) <= 1, plan2["plan"])

    print("\n[4b] raw (unslimmed) BRAIN payload shapes")
    class RawClient(FakeClient):
        async def get_pyramid_alphas(self, st=None, en=None):
            # shape returned by brain_client (flat list), not the slimmed nested dict
            return {"pyramids": [
                {"category": {"id": "analyst", "name": "Analyst"}, "region": "GBR", "delay": 1, "alphaCount": 2},
                {"category": {"id": "model",   "name": "Model"},   "region": "GBR", "delay": 1, "alphaCount": 4},
                {"category": {"id": "news",    "name": "News"},    "region": "GBR", "delay": 1, "alphaCount": 0},
                {"category": {"id": "analyst", "name": "Analyst"}, "region": "USA", "delay": 1, "alphaCount": 9},
            ]}
    covr = await cp.pyramid_coverage(RawClient(), region="GBR", delay=1, target=3)
    rr = {x["pyramid"]: x for x in covr["rows"]}
    check("raw list shape parsed", rr["analyst"]["submitted"] == 2, rr.get("analyst"))
    check("raw shape: model reads 4 submitted", rr["model"]["submitted"] == 4, rr.get("model"))
    check("raw shape: other regions excluded", rr["analyst"]["submitted"] != 11, rr.get("analyst"))

    print("\n[5] persistence + sync")
    listing = cp.list_pool(region="GBR")
    check("pool persisted to disk", listing["pool_size"] == 4, listing["pool_size"])
    check("pool file really written", os.path.exists(os.environ["CANDIDATE_POOL_FILE"]))

    class SubmittedClient(FakeClient):
        async def get_alpha_details(self, aid):
            d = await FakeClient.get_alpha_details(self, aid)
            if aid == "A": d["status"] = "ACTIVE"
            return d
    s = await cp.sync_pool(SubmittedClient())
    check("sync drops the now-submitted A", s["promoted_to_submitted"] == ["A"], s)
    check("pool shrank to 3", s["pool_size"] == 3, s)

    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
