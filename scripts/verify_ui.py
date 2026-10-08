#!/usr/bin/env python
"""Live UI verification harness.

Drives the *running Chainlit server* over its real socket.io protocol — the same wire
format the browser uses — and asserts that a full scoping engagement completes.

This is deliberately not a unit test. It exercises the whole stack end to end: the
socket layer, the ``on_chat_start`` / ``on_message`` callbacks, the LangGraph state
machine, hybrid retrieval, the Critic loop, the approval gate and deliverable
rendering — against a real HTTP server, exactly as an iPhone on the same Wi-Fi would.

Usage::

    # terminal 1
    chainlit run app.py --host 0.0.0.0 --port 8000

    # terminal 2
    python scripts/verify_ui.py --url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

warnings_filter = __import__("warnings")
warnings_filter.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REQUIREMENT = """# Project Aurora Data Platform
Nordwind Logistics GmbH (Berlin) requires a customer analytics platform for our
operations organisation.

- Build a React dashboard used by 200 warehouse staff across 6 distribution centres
- Develop a Python FastAPI backend with PostgreSQL as the system of record
- Integrate with Salesforce for account data and SAP for shipment events
- Deliver GDPR-compliant audit logging that satisfies our Data Protection Officer
- Provide demand forecasting (the EU AI Act applies to this component)

Budget: EUR 120k. Delivery within 12 weeks. Success means 40% faster monthly close.
The platform processes personal data of EU employees.
Out of scope: mobile application and phase 2 reporting."""

#: Sent only when Triage decides the brief is incomplete.
CLARIFICATION_ANSWERS = (
    "1. Primary users are the 200 warehouse staff and the 12-person operations "
    "management team; both are internal employees.\n"
    "2. Milestone acceptance is signed off by the Product Owner, with the steering "
    "committee for milestone 4.\n"
    "3. Fixed price per milestone, invoiced on milestone acceptance.\n"
    "4. Warranty period of 90 days after go-live."
)

APPROVAL = "approve"


class Collector:
    """Accumulates every socket.io event the server emits.

    Chainlit emits ``new_message`` / ``update_message`` with the ``StepDict`` as the
    payload itself (not wrapped in an envelope), and ``update_message`` carries partial
    revisions, so messages are merged by id and the latest ``output`` wins.
    """

    MESSAGE_EVENTS = ("new_message", "update_message", "delete_message")

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.elements: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._order: list[str] = []

    def handler(self, event: str):
        def _record(data: Any) -> None:
            self.events.append((event, data))
            if event == "element" and isinstance(data, dict):
                self.elements.append(data)
            if event not in self.MESSAGE_EVENTS or not isinstance(data, dict):
                return
            if "id" not in data:
                return
            message_id = str(data["id"])
            if message_id not in self._by_id:
                self._by_id[message_id] = dict(data)
                self._order.append(message_id)
            else:
                self._by_id[message_id].update(data)

        return _record

    def has_cost_dashboard(self) -> bool:
        """True when the sticky cost widget was delivered as a custom element."""
        for element in self.elements:
            blob = json.dumps(element, default=str)
            if "CostDashboard" in blob:
                return True
        return False

    def cost_dashboard_props(self) -> dict:
        for element in reversed(self.elements):
            if "CostDashboard" in json.dumps(element, default=str):
                return element.get("props") or {}
        return {}

    def messages(self) -> list[dict]:
        return [self._by_id[mid] for mid in self._order if "type" in self._by_id[mid]]

    def steps(self) -> list[dict]:
        return [m for m in self.messages() if m.get("type") in ("run", "tool", "llm", "retrieval", "embedding")]

    def assistant_texts(self) -> list[str]:
        texts: list[str] = []
        for message in self.messages():
            if message.get("type") != "assistant_message":
                continue
            output = message.get("output") or ""
            if output:
                texts.append(str(output))
        return texts

    def joined_text(self) -> str:
        return "\n\n".join(self.assistant_texts())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the live SOWSprint Chainlit UI")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--jurisdiction",
        default=None,
        choices=["EU", "US", "BOTH"],
        help="Override the UI compliance regime for this run.",
    )
    args = parser.parse_args(argv)

    import socketio

    collector = Collector()
    client = socketio.Client(logger=False, engineio_logger=False, reconnection=False)

    for event in (
        "new_message",
        "update_message",
        "task_start",
        "task_end",
        "element",
        "audio_chunk",
        "chat_settings",
    ):
        client.on(event, collector.handler(event))

    session_id = str(uuid.uuid4())
    # Chainlit mounts its socket.io ASGI app at ``{root_path}/ws/socket.io``
    # (chainlit/server.py: SOCKET_IO_PATH).
    socket_path = "/ws/socket.io"
    print(f"→ connecting to {args.url}{socket_path} (session {session_id[:8]})")
    client.connect(
        args.url,
        auth={
            "sessionId": session_id,
            "clientType": "web",
            "userEnv": "{}",
            "threadId": None,
        },
        socketio_path=socket_path,
        transports=["websocket", "polling"],
    )
    print("✓ socket connected")

    client.emit("connection_successful")
    _wait_for(lambda: any("SOWSprint" in t for t in collector.assistant_texts()), 60, collector)
    print(f"✓ on_chat_start rendered ({len(collector.assistant_texts())} message(s))")

    # The regime change MUST be emitted after connection_successful: on_chat_start
    # resets cl.user_session["jurisdiction"] to the configured default, so setting it
    # any earlier is silently overwritten and the run proceeds under the wrong regime.
    if args.jurisdiction:
        client.emit("chat_settings_change", {"jurisdiction": args.jurisdiction})
        time.sleep(3)
        print(f"✓ compliance regime set to {args.jurisdiction}")

    def send(text: str) -> None:
        client.emit(
            "client_message",
            {
                "message": {
                    "id": str(uuid.uuid4()),
                    "name": "User",
                    "type": "user_message",
                    "output": text,
                    "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                "fileReferences": [],
            },
        )

    def transcript_has(*needles: str) -> bool:
        text = collector.joined_text().lower()
        return any(needle in text for needle in needles)

    # ---------------------------------------------------------------- turn 1
    print("→ turn 1: sending requirement")
    send(REQUIREMENT)

    _wait_for(
        lambda: transcript_has(
            "three questions", "approval required", "run finished"
        ),
        args.timeout,
        collector,
        label="agent pipeline",
    )

    # ---------------------------------------------------------------- turn 2
    if transcript_has("three questions"):
        print("✓ clarification loop engaged — answering the three questions")
        send(CLARIFICATION_ANSWERS)
        _wait_for(
            lambda: transcript_has("approval required", "run finished"),
            args.timeout,
            collector,
            label="post-clarification pipeline",
        )

    # ---------------------------------------------------------------- turn 3
    if transcript_has("approval required"):
        print("✓ approval gate reached — approving")
        send(APPROVAL)
        _wait_for(
            lambda: transcript_has("run finished"),
            args.timeout,
            collector,
            label="provisioning",
        )

    # ---------------------------------------------------------------- assertions
    texts = collector.joined_text()
    steps = collector.steps()

    print("\n" + "=" * 78)
    print("VERIFICATION RESULTS")
    print("=" * 78)
    print(f"  assistant messages : {len(collector.assistant_texts())}")
    print(f"  agent steps        : {len(steps)}")
    for step in steps:
        name = str(step.get("name", ""))
        if name:
            print(f"    · {name}")

    dashboard_props = collector.cost_dashboard_props()

    checks: list[tuple[str, bool]] = [
        ("on_chat_start published capabilities", "SOWSprint.ai" in texts),
        ("capability matrix rendered", "Resolved adapter" in texts),
        ("agent step trace rendered", len(steps) >= 5),
        ("Statement of Work produced", "Statement of Work" in texts),
        ("quality audit reported", "Quality audit" in texts),
        ("compliance retrieval reported", "Jurisdiction filter" in texts),
        ("sticky cost dashboard delivered as custom element", collector.has_cost_dashboard()),
        (
            "dashboard carries live cost telemetry",
            bool(dashboard_props) and float(dashboard_props.get("costUsd") or 0) > 0,
        ),
        (
            "dashboard reports per-agent cost breakdown",
            bool(dashboard_props.get("byNode")),
        ),
        ("run reached a terminal status", "Run finished" in texts),
        ("deliverable files attached", len(collector.elements) >= 3),
        (
            "no pipeline errors",
            "the agent pipeline failed" not in texts.lower(),
        ),
    ]

    if dashboard_props:
        print(
            f"\n  dashboard: ${dashboard_props.get('costUsd')} / "
            f"${dashboard_props.get('budgetUsd')} · "
            f"{dashboard_props.get('totalTokens')} tokens · "
            f"{dashboard_props.get('calls')} calls · "
            f"agents={list((dashboard_props.get('byNode') or {}).keys())}"
        )

    failed = 0
    for label, ok in checks:
        print(f"  {'✅' if ok else '❌'} {label}")
        failed += 0 if ok else 1

    if failed:
        print("\n--- transcript (tail) ---")
        print(texts[-4000:])

    client.disconnect()
    print("\n" + ("✅ ALL UI CHECKS PASSED" if failed == 0 else f"❌ {failed} CHECK(S) FAILED"))
    return 0 if failed == 0 else 1


def _wait_for(predicate, timeout: float, collector: Collector, label: str = "condition") -> bool:
    """Poll until ``predicate`` holds or the timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.5)
    print(f"  ⏱ timeout waiting for {label} after {timeout:.0f}s")
    print(f"  received {len(collector.events)} event(s); assistant text tail:")
    print(collector.joined_text()[-1500:])
    return False


if __name__ == "__main__":
    raise SystemExit(main())
