"""
Acceptance audit runner (spec §6): sweep every stored brief, apply the
deterministic checks, and surface each brief's scrubber removal log for the
human review gate (briefs 1-10). LLM audits are run separately (on-demand /
dry-run) to keep this fast and cost-free.
"""

from typing import Dict, Any

from .audit import (
    check_freeze_integrity, check_word_count, check_snapshot_timing,
    check_hash_reproducible, check_regex_odds, Finding,
)
from .llm_audit import neutrality_audit, odds_llm_audit
from ...storage.db import list_predictions, get_provenance
from ...storage.brief_store import read_brief, BriefIntegrityError
from ...utils.logger import get_logger

logger = get_logger('mirofish.services.acceptance.run_audit')


def audit_all(conn, briefs_dir: str, llm=None) -> Dict[str, Any]:
    briefs = []
    passed = 0
    for row in list_predictions(conn):
        provenance = get_provenance(conn, row.brief_hash)
        checks = [
            check_word_count(row).to_dict(),
            check_snapshot_timing(row).to_dict(),
            check_hash_reproducible(row.brief_hash, briefs_dir).to_dict(),
            # I2: a brief with no provenance skips the freeze check — that must fail, not silently pass.
            Finding("provenance_present", provenance is not None,
                    "provenance record present" if provenance is not None
                    else "MISSING provenance record").to_dict(),
        ]
        if provenance is not None:
            checks.append(check_freeze_integrity(provenance).to_dict())

        brief_text = None
        try:
            brief_text = read_brief(row.brief_hash, briefs_dir)
            checks.append(check_regex_odds(brief_text).to_dict())
        except (BriefIntegrityError, FileNotFoundError):
            pass  # hash_reproducible already records the failure

        # I1: LLM neutrality + odds audits only run when a model is supplied (cost-gated).
        llm_audits_run = False
        if llm is not None and brief_text is not None:
            llm_audits_run = True
            neut = neutrality_audit(brief_text, row.resolution_criteria, llm)
            checks.append(Finding("neutrality_llm", not neut["implies_outcome"],
                                  neut.get("note") or "neutral").to_dict())
            oa = odds_llm_audit(brief_text, llm)
            checks.append(Finding("odds_llm", oa["clean"],
                                  "clean" if oa["clean"] else f"found: {oa['found'][:3]}").to_dict())

        removals = [r.to_dict() for r in provenance.scrubber_removals] if provenance else []
        ok = all(c["ok"] for c in checks)
        if ok:
            passed += 1
        briefs.append({
            "brief_hash": row.brief_hash, "question_id": row.question_id,
            "passed": ok, "checks": checks, "removals": removals,
            "llm_audits_run": llm_audits_run,
        })

    total = len(briefs)
    return {
        "briefs": briefs,
        "summary": {
            "total": total, "passed": passed, "failed": total - passed,
            "llm_audits_run": llm is not None,
            # honesty notes for criteria not machine-judged here:
            "notes": [
                "Two-source rule is code-enforced upstream in triage (validate_two_source); not re-audited here.",
                "Removal logs are surfaced per brief for human review, not scored.",
                "LLM neutrality/odds audits run only when the sweep is invoked with an llm.",
            ],
        },
    }
