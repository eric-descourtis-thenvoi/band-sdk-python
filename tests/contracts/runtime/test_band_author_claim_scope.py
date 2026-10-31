from __future__ import annotations

from typing import Any
from urllib.error import HTTPError

import pytest
from hypothesis import Phase, given, settings
from hypothesis import strategies as st

from remote_agent.band.authoring import Journal
from remote_agent.band.studio import StudioAgent
from tests.contracts.runtime.bandauthorscope import AuthorScopeFixture, http_error


@pytest.mark.contract_linux
class TestAuthorClaimScope(AuthorScopeFixture):
    def test_lost_claim_ack_then_scope_loss_releases_queue_across_restart(self) -> None:
        root, journal = self.fixture(legacy=True)
        claims: list[str] = []

        def transport(operation: str, body: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(operation, "claim")
            claims.append(body["id"])
            if len(claims) == 1:
                raise TimeoutError("Claim committed but acknowledgement lost")
            if len(claims) == 2:
                raise http_error()
            return {"job": self.job("other-room")}

        with self.assertRaises(TimeoutError):
            StudioAgent(journal, transport).poll()
        journal.close()
        journal = Journal(root)
        agent = StudioAgent(journal, transport)
        agent.poll()
        self.assertEqual(claims, [claims[0], claims[0]])
        held = [
            {"claim": claims[0], "status": "scope_unavailable", "acknowledged": False}
        ]
        self.assertEqual(agent.held_claims(), held)
        self.assertEqual(agent.held_completions(), [])
        journal.close()
        journal = Journal(root)
        self.addCleanup(journal.close)
        journal.recover()
        agent = StudioAgent(journal, transport)
        self.assertEqual(agent.held_claims(), held)
        agent.poll()
        self.assertNotEqual(claims[-1], claims[0])
        self.assertEqual(journal.result("studio:other-room:0")["status"], "pending")
        self.assertEqual(agent.held_claims(), held)
        self.assertEqual(
            journal.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1
        )

    def test_uncertain_claim_errors_preserve_exact_retry_identity(self) -> None:
        for status, body, content_type in (
            (500, b'{"error":"not_found"}', "application/json"),
            (401, b'{"error":"not_found"}', "application/json"),
            (404, b'{"error":"not_found"}', "text/html"),
            (404, b'{"error":"not_found","error":"not_found"}', "application/json"),
            (404, b'{"error":"not_found","extra":true}', "application/json"),
            (404, b"not json", "application/json"),
            (404, b" " * 4096 + b'{"error":"not_found"}', "application/json"),
        ):
            with self.subTest(status=status, body=body[:60], content_type=content_type):
                root, journal = self.fixture()
                claims: list[str] = []

                def transport(
                    operation: str,
                    value: dict[str, Any],
                    *,
                    claims: list[str] = claims,
                    status: int = status,
                    body: bytes = body,
                    content_type: str = content_type,
                ) -> dict[str, Any]:
                    claims.append(value["id"])
                    raise http_error(status, body, content_type)

                with self.assertRaises(HTTPError):
                    StudioAgent(journal, transport).poll()
                journal.close()
                journal = Journal(root)
                self.addCleanup(journal.close)
                agent = StudioAgent(journal, transport)
                with self.assertRaises(HTTPError):
                    agent.poll()
                self.assertEqual(claims, [claims[0], claims[0]])
                self.assertEqual(agent.held_claims(), [])

    @settings(max_examples=20, deadline=None, phases=(Phase.generate,))
    @given(
        steps=st.lists(
            st.sampled_from(["timeout", "restart", "scope"]), min_size=1, max_size=12
        )
    )
    def test_generated_claim_lifecycle_preserves_uncertain_and_retired_ids(
        self, steps: list[str]
    ) -> None:
        root, journal = self.fixture()
        outcome = ["timeout"]
        claims: list[str] = []
        retired: list[dict[str, Any]] = []
        pending: str | None = None

        def transport(operation: str, body: dict[str, Any]) -> dict[str, Any]:
            claims.append(body["id"])
            if outcome[0] == "timeout":
                raise TimeoutError("Unknown claim acknowledgement")
            raise http_error()

        agent = StudioAgent(journal, transport)
        for step in steps:
            if step == "restart":
                journal.close()
                journal = Journal(root)
                self.addCleanup(journal.close)
                agent = StudioAgent(journal, transport)
            else:
                outcome[0] = step
                if step == "timeout":
                    with self.assertRaises(TimeoutError):
                        agent.poll()
                else:
                    agent.poll()
                if pending is not None:
                    self.assertEqual(claims[-1], pending)
                else:
                    self.assertNotIn(claims[-1], [row["claim"] for row in retired])
                pending = claims[-1] if step == "timeout" else None
                if step == "scope":
                    retired.append(
                        {
                            "claim": claims[-1],
                            "status": "scope_unavailable",
                            "acknowledged": False,
                        }
                    )
            self.assertEqual(agent.held_claims(), retired)
