"""Dedicated LangGraph pipeline for Alchemy Notify webhooks (not ``MercuryGraphRuntime``)."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from mercury.graph.logging import log_graph_event
from mercury.webhooks.alchemy_dedupe import AlchemyWebhookDedupeStore
from mercury.webhooks.alchemy_handler import (
    ADDRESS_ACTIVITY,
    filter_incoming_for_watched,
    normalize_activity_rows,
)


class AlchemyWebhookState(TypedDict, total=False):
    """State for the address-activity webhook graph."""

    payload: dict[str, Any]
    watched_addresses: list[str]
    webhook_id: str
    event_id: str
    network: str
    skipped: bool
    skip_reason: str | None
    normalized_alerts: list[dict[str, Any]]
    filtered_alerts: list[dict[str, Any]]
    new_alerts: list[dict[str, Any]]
    duplicate_count: int
    emit_summary: dict[str, Any]


def build_alchemy_webhook_graph(
    dedupe_store: AlchemyWebhookDedupeStore,
) -> StateGraph[AlchemyWebhookState]:
    """Compile a small graph: classify → normalize → filter → dedupe → emit."""

    def classify(state: AlchemyWebhookState) -> dict[str, Any]:
        payload = state.get("payload") or {}
        event_type = payload.get("type")
        if event_type != ADDRESS_ACTIVITY:
            reason = "ignored_type" if event_type is None else f"ignored_type:{event_type}"
            return {
                "skipped": True,
                "skip_reason": reason,
                "webhook_id": str(payload.get("webhookId", "")),
                "event_id": str(payload.get("id", "")),
                "normalized_alerts": [],
                "filtered_alerts": [],
                "new_alerts": [],
                "duplicate_count": 0,
                "network": "",
            }
        return {
            "skipped": False,
            "skip_reason": None,
            "webhook_id": str(payload.get("webhookId", "")),
            "event_id": str(payload.get("id", "")),
        }

    def normalize_activity(state: AlchemyWebhookState) -> dict[str, Any]:
        if state.get("skipped"):
            return {}
        payload = state.get("payload") or {}
        event = payload.get("event")
        if not isinstance(event, dict):
            return {"normalized_alerts": [], "network": ""}
        network = str(event.get("network", ""))
        activity = event.get("activity")
        act_list = activity if isinstance(activity, list) else None
        alerts = normalize_activity_rows(network=network, activity=act_list)
        return {"normalized_alerts": alerts, "network": network}

    def filter_incoming(state: AlchemyWebhookState) -> dict[str, Any]:
        if state.get("skipped"):
            return {}
        watched = state.get("watched_addresses") or []
        normalized = state.get("normalized_alerts") or []
        filtered = filter_incoming_for_watched(normalized, watched)
        return {"filtered_alerts": filtered}

    def dedupe(state: AlchemyWebhookState) -> dict[str, Any]:
        if state.get("skipped"):
            return {}
        wid = state.get("webhook_id", "")
        eid = state.get("event_id", "")
        fresh: list[dict[str, Any]] = []
        duplicates = 0
        for alert in state.get("filtered_alerts") or []:
            key = (
                wid,
                eid,
                str(alert.get("tx_hash", "")),
                str(alert.get("log_index", "na")),
            )
            if dedupe_store.mark_if_new(key):
                fresh.append(alert)
            else:
                duplicates += 1
        return {"new_alerts": fresh, "duplicate_count": duplicates}

    def emit_alert(state: AlchemyWebhookState) -> dict[str, Any]:
        new_alerts = state.get("new_alerts") or []
        summary: dict[str, Any] = {
            "received": True,
            "skipped": bool(state.get("skipped")),
            "skip_reason": state.get("skip_reason"),
            "network": state.get("network"),
            "normalized_count": len(state.get("normalized_alerts") or []),
            "filtered_count": len(state.get("filtered_alerts") or []),
            "processed": len(new_alerts),
            "duplicate_count": int(state.get("duplicate_count") or 0),
            "alerts": new_alerts,
        }
        sample_hashes = [
            a.get("tx_hash") for a in new_alerts[:3] if isinstance(a.get("tx_hash"), str)
        ]
        skip = summary["skipped"]
        summ = (
            f"Alchemy address-activity webhook: skipped={skip}"
            + (
                f" ({summary.get('skip_reason')})"
                if skip
                else f", emitting {summary['processed']} new alert(s), "
                f"{summary['duplicate_count']} duplicate(s) suppressed"
            )
        )
        log_graph_event(
            "alchemy_address_activity_webhook",
            summary=summ,
            skipped=summary["skipped"],
            skip_reason=summary.get("skip_reason"),
            normalized_count=summary["normalized_count"],
            filtered_count=summary["filtered_count"],
            processed=summary["processed"],
            duplicate_count=summary["duplicate_count"],
            sample_tx_hashes=sample_hashes,
        )
        return {"emit_summary": summary}

    def route_after_classify(state: AlchemyWebhookState) -> str:
        return "emit_alert" if state.get("skipped") else "normalize_activity"

    builder = StateGraph(AlchemyWebhookState)
    builder.add_node("classify", classify)
    builder.add_node("normalize_activity", normalize_activity)
    builder.add_node("filter_incoming", filter_incoming)
    builder.add_node("dedupe", dedupe)
    builder.add_node("emit_alert", emit_alert)
    builder.add_edge(START, "classify")
    builder.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "emit_alert": "emit_alert",
            "normalize_activity": "normalize_activity",
        },
    )
    builder.add_edge("normalize_activity", "filter_incoming")
    builder.add_edge("filter_incoming", "dedupe")
    builder.add_edge("dedupe", "emit_alert")
    builder.add_edge("emit_alert", END)
    return builder


def compiled_alchemy_webhook_graph(dedupe_store: AlchemyWebhookDedupeStore):
    """Return a compiled graph instance."""

    return build_alchemy_webhook_graph(dedupe_store).compile()
