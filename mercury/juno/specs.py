"""Default :class:`~juno.agents.registry.SubagentSpec` for the Mercury Juno tool."""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from juno.agents.registry import SubagentSpec

MERCURY_SUBAGENT_RESUME_AFTER_APPROVAL = (
    "Session already includes `approval_response` from Telegram (human approved). "
    "Call `mercury_invoke` now with `intent_json` that is IDENTICAL to your previous "
    "mercury_invoke for this operation: same `kind`, fields, amounts, addresses, the "
    "same `idempotency_key` inside the intent, and the same Mercury `request_id` as the "
    "interrupted turn (set graph state `mercury_pending_request_id` to the `request_id` "
    "from the wallet_approval tool message if your runtime supports it). "
    "Top-level `approval_response` must include the matching `idempotency_key`. "
    "Do not substitute a new intent. Do not describe wallet UI steps; completion is via "
    "Mercury in-process invoke + 1Claw signer."
)

MERCURY_SUPERVISOR_TOOL_DESCRIPTION = """Mercury specialist: real balances, wallets, Base/Ethereum/L2, txs, approvals.

**When to call:** Any request involving money/crypto, wallets, holdings, named
chains (e.g. Base, Ethereum, L2), transactions, swaps, transfers, approvals, gas,
or addresses—or anything that needs live Mercury/backend data.

Pass the user's goal in one ``request`` string (chain, wallet, tokens if mentioned).
The Mercury sub-agent turns this into structured ``mercury_invoke`` JSON.

**Do not call** for generic small talk with no backend data.

**After Telegram Approve:** If state already contains ``approval_response``, call this
again immediately with instructions for the specialist to repeat the **same**
``mercury_invoke`` intent as before (same ``kind``, fields, ``idempotency_key``) and the
same Mercury ``request_id`` (from the prior tool output; persist as ``mercury_pending_request_id``
in session when possible)—never a new intent for the gated operation.

Completion is normally a second Mercury invoke with approval; prefer that over
asking the user to use browser wallets unless product docs say otherwise.
"""


def default_mercury_subagent_spec(graph: CompiledStateGraph) -> SubagentSpec:
    """Stable ``mercury`` supervisor tool (name aligns with checkpoints / prompts)."""
    return SubagentSpec(
        name="mercury",
        description=MERCURY_SUPERVISOR_TOOL_DESCRIPTION.strip(),
        graph=graph,
        state_keys=(
            "user_id",
            "wallet_id",
            "chain",
            "approval_response",
            "mercury_pending_request_id",
        ),
        resume_instruction=MERCURY_SUBAGENT_RESUME_AFTER_APPROVAL,
        supports_wallet_approval_ui=True,
    )


__all__ = [
    "MERCURY_SUBAGENT_RESUME_AFTER_APPROVAL",
    "MERCURY_SUPERVISOR_TOOL_DESCRIPTION",
    "default_mercury_subagent_spec",
]
