"""
Constrained assignment.

THIS IS THE FIX for the core problem stated in the brief:

    Invoice A = ₹10,000
    Invoice B = ₹10,000
    Bank Transaction A = ₹10,000

A naive similarity-clustering approach (e.g. union-find over "looks
similar" pairs) would happily merge Invoice A, Invoice B, and Bank A into
one connected component, because A-Bank and B-Bank both look similar. That
silently reconciles two independent invoices against a single bank
transaction -- a real accounting error.

DECISION: we treat this as a one-to-one bipartite assignment problem
between "left" records (e.g. invoices) and "right" records (e.g.
settlements, or bank transactions), not a general clustering problem.

Algorithm: constrained greedy assignment.
    1. Score every candidate (left, right) pair (already narrowed down by
       CandidateIndex, so this is not O(n^2) over the full dataset).
    2. Sort all candidate pairs by weighted score, descending.
    3. Walk the sorted list once. Assign a pair only if BOTH sides are
       still unconsumed. Once assigned, mark both sides consumed so
       neither can be claimed again.
    4. Anything left unconsumed becomes a candidate for the exception
       taxonomy (AMBIGUOUS_MATCH when it had a real competing claim,
       UNKNOWN/MISSING_* otherwise).

Why greedy instead of the Hungarian algorithm (optimal maximum-weight
matching): the Hungarian algorithm optimizes GLOBAL total score, which can
occasionally reassign a very-high-confidence pair to a slightly-better
global arrangement elsewhere -- undesirable here, because we want the
single highest-confidence claim on any given record to always win
regardless of what happens elsewhere in the batch, and we want the
decision for one transaction to be explainable without reasoning about
the whole batch. Greedy-by-descending-score also gives us exactly the
"AUTO_RECONCILE the ≥95% pairs, escalate the rest" tiering the policy
calls for, without extra reconciliation of the two objectives. For
datasets where truly optimal global matching is required, swap the sort
step for `scipy.optimize.linear_sum_assignment` on the score matrix --
the consumed-tracking loop below is unaffected either way.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ScoredCandidate:
    left_id: str
    right_id: str
    left: Any
    right: Any
    score: Dict[str, float]


@dataclass
class AssignmentResult:
    assigned: List[ScoredCandidate] = field(default_factory=list)
    unassigned_left_ids: List[str] = field(default_factory=list)
    unassigned_right_ids: List[str] = field(default_factory=list)
    # left/right ids that had at least one candidate but lost the greedy race
    left_had_competing_claim: Dict[str, List[str]] = field(default_factory=dict)


def constrained_assign(
    candidates: List[ScoredCandidate],
    all_left_ids: List[str],
    all_right_ids: List[str],
) -> AssignmentResult:
    """
    One-to-one constrained greedy assignment. See module docstring for why
    greedy-by-score, not generic clustering, is the correct mechanism here.
    """
    result = AssignmentResult()

    sorted_candidates = sorted(candidates, key=lambda c: c.score["weighted_score"], reverse=True)

    consumed_left: set = set()
    consumed_right: set = set()
    competing_claims: Dict[str, List[str]] = {}

    for cand in sorted_candidates:
        # track that this left record had a real candidate, win or lose
        competing_claims.setdefault(cand.left_id, []).append(cand.right_id)

        if cand.left_id in consumed_left or cand.right_id in consumed_right:
            continue  # one-to-one constraint: both sides must still be free

        consumed_left.add(cand.left_id)
        consumed_right.add(cand.right_id)
        result.assigned.append(cand)

    result.unassigned_left_ids = [lid for lid in all_left_ids if lid not in consumed_left]
    result.unassigned_right_ids = [rid for rid in all_right_ids if rid not in consumed_right]
    result.left_had_competing_claim = {
        lid: rights for lid, rights in competing_claims.items() if lid in result.unassigned_left_ids
    }

    return result
