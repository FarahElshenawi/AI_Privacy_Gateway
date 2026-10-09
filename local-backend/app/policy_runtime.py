"""One place that changes live masking policy (used by cloud sync and the local admin API)."""
from __future__ import annotations


def apply_policies(policies: dict, *, strict: bool = False) -> tuple[int, list[str]]:
    """Apply a {LABEL: "faker"|"redact"|"keep"} map to the LIVE masking path (cloud pull AND /api/policies/apply).

    Masking reads two tables: the merge engine's label->action Policy (decides what happens to a
    span) and the active routing table (storage/audit metadata). Both are updated, atomically
    per table, starting from the built-in defaults so labels removed in the cloud revert.

    Safety: unknown actions are ignored, and the local guard in configure_policy_override
    refuses to downgrade critical secrets (cards, keys, SSN, ...) to KEEP. Rejected labels are
    returned so they can be logged. Returns (applied_count, rejected_labels).

    The map is a SNAPSHOT: labels not listed go back to the built-in default. With strict=True
    nothing is changed unless every entry is acceptable (ValueError listing the rejected ones).
    """
    from dlp_core import policy as P
    from dlp_core.policy import Action, Policy, PolicyConfigError

    actions = {"faker": Action.FAKER, "redact": Action.REDACT, "keep": Action.KEEP, "block": Action.BLOCK}
    table = dict(P.DEFAULT_ACTIONS)
    rejected: list[str] = []
    accepted: dict[str, Action] = {}
    if strict:      # validate everything first; touch nothing if anything is unacceptable
        for label, action_str in policies.items():
            act = actions.get(str(action_str).lower())
            canon = P.normalize_label(str(label))
            if act is None or not canon or (canon in P._IMMUTABLE_CRITICAL_SECRETS and act is Action.KEEP):
                rejected.append(canon or str(label))
        if rejected:
            raise ValueError(", ".join(rejected))
    P.reset_policy_to_defaults()
    for label, action_str in policies.items():
        act = actions.get(str(action_str).lower())
        canon = P.normalize_label(str(label))
        if act is None or not canon:
            rejected.append(str(label)); continue
        try:
            P.configure_policy_override(canon, act)
        except PolicyConfigError:
            rejected.append(canon); continue
        accepted[canon] = act
    table.update(accepted)
    from app.pipeline import engine
    engine._pipeline.set_policy(Policy(table))
    return len(accepted), rejected
