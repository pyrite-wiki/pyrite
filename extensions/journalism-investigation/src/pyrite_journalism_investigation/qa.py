"""Investigation QA reporting and quality metrics."""

from datetime import UTC, datetime, timedelta
from typing import Any

from .utils import parse_meta


def compute_qa_metrics(db: Any, kb_name: str, stale_days: int = 30) -> dict[str, Any]:
    """Compute quality metrics for an investigation KB.

    Args:
        db: PyriteDB instance
        kb_name: KB name to analyze
        stale_days: Days after which unverified claims are considered stale.
            A claim is stale when its ``updated_at`` is older than this, the
            rule ``pyrite qa stale`` uses; a missing or unparseable
            ``updated_at`` counts as stale, as it does there.

    Returns:
        Dict with source_tiers, claims, quality_score, and warnings.
    """
    stale_cutoff = datetime.now(UTC) - timedelta(days=stale_days)
    # --- Source tier distribution ---
    sources = db.list_entries(kb_name=kb_name, entry_type="document_source", limit=10000)
    tier_counts = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    for s in sources:
        meta = parse_meta(s)
        reliability = meta.get("reliability", "unknown")
        if reliability in tier_counts:
            tier_counts[reliability] += 1
        else:
            tier_counts["unknown"] += 1

    total_sources = sum(tier_counts.values())
    high_pct = (tier_counts["high"] / total_sources * 100) if total_sources else 0

    source_tiers = {
        "total": total_sources,
        **tier_counts,
        "high_pct": round(high_pct, 1),
    }

    # --- Claim analysis ---
    claims = db.list_entries(kb_name=kb_name, entry_type="claim", limit=10000)
    total_claims = len(claims)
    orphans = 0
    confidence_dist = {"high": 0, "medium": 0, "low": 0}
    status_counts = {}
    stale_unverified = 0

    for c in claims:
        meta = parse_meta(c)
        evidence_refs = meta.get("evidence_refs", []) or []
        if not evidence_refs:
            orphans += 1

        confidence = meta.get("confidence", "low")
        if confidence in confidence_dist:
            confidence_dist[confidence] += 1

        claim_status = meta.get("claim_status", "unverified")
        status_counts[claim_status] = status_counts.get(claim_status, 0) + 1
        if claim_status == "unverified" and _updated_before(c.get("updated_at"), stale_cutoff):
            stale_unverified += 1

    coverage_pct = ((total_claims - orphans) / total_claims * 100) if total_claims else 100.0
    disputed_count = status_counts.get("disputed", 0) + status_counts.get("retracted", 0)
    disputed_ratio = (disputed_count / total_claims * 100) if total_claims else 0

    claims_metrics = {
        "total": total_claims,
        "orphans": orphans,
        "coverage_pct": round(coverage_pct, 1),
        "confidence": confidence_dist,
        "status": status_counts,
        "disputed_ratio": round(disputed_ratio, 2),
        "stale_unverified": stale_unverified,
    }

    # --- Quality score (0-100) ---
    # Formula:
    #   40% source quality (high_pct)
    #   40% claim coverage (coverage_pct)
    #   20% low dispute ratio (100 - disputed_ratio)
    source_score = high_pct  # 0-100
    coverage_score = coverage_pct  # 0-100
    dispute_score = max(0, 100 - disputed_ratio)  # 0-100

    quality_score = round(source_score * 0.4 + coverage_score * 0.4 + dispute_score * 0.2)
    quality_score = max(0, min(100, quality_score))

    # --- Warnings ---
    warnings: list[str] = []
    if total_sources > 0 and high_pct < 20:
        warnings.append(f"Low high-reliability sources: {high_pct:.0f}% (recommended: ≥20%)")
    if total_claims > 0:
        orphan_pct = orphans / total_claims * 100
        if orphan_pct > 30:
            warnings.append(
                f"High orphan claim ratio: {orphan_pct:.0f}% of claims have no evidence"
            )
    if total_claims > 0 and disputed_ratio > 50:
        warnings.append(
            f"High dispute ratio: {disputed_ratio:.0f}% of claims disputed or retracted"
        )

    if stale_unverified:
        warnings.append(
            f"Stale unverified claims: {stale_unverified} not updated in {stale_days} days"
        )

    return {
        "source_tiers": source_tiers,
        "claims": claims_metrics,
        "quality_score": quality_score,
        "warnings": warnings,
    }


def _updated_before(updated_at: Any, cutoff: datetime) -> bool:
    """True when *updated_at* is before *cutoff*, or is missing or unparseable."""
    try:
        updated = datetime.fromisoformat(str(updated_at))
    except (ValueError, TypeError):
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return updated < cutoff
