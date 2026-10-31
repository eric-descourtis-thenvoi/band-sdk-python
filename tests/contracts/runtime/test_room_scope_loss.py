from __future__ import annotations

from functools import partial
from typing import Any
from unittest.mock import patch

import pytest
from hypothesis import Phase, example, given, settings
from hypothesis import strategies as st

from remote_agent.band.arithmetic import RuntimeDriver
from remote_agent.band.generic import GenericRuntimeDriver
from remote_agent.network import HTTPRoom
from remote_agent.store import Store
from remote_agent.transport import TransportError
from tests.contracts.runtime.httppeer import HTTPPeer
from tests.contracts.runtime.roomscopeloss import (
    RoomRefusalHandler,
    RoomScopeLossFixture,
)


@pytest.mark.contract_linux
class TestRoomScopeLoss(RoomScopeLossFixture):
    @pytest.mark.timeout(90)
    def test_real_http_worker_rejects_unvalidated_scope_refusals(self) -> None:
        for encoded, content_type, permanent, message in (
            (
                b'{"error":"not_found"}',
                "application/json",
                True,
                "Native room scope is unavailable",
            ),
            (b'{"error":"not_found"}', "text/html", False, "Room connection failed"),
            (
                b'{"error":"not_found","error":"not_found"}',
                "application/json",
                False,
                "Room connection failed",
            ),
            (
                b'{"error":"not_found"} trailing',
                "application/json",
                False,
                "Room connection failed",
            ),
            (
                b'{"error":"not_found","extra":true}',
                "application/json",
                False,
                "Room HTTP status 404",
            ),
        ):
            with self.subTest(encoded=encoded, content_type=content_type):
                peer = HTTPPeer(
                    partial(
                        RoomRefusalHandler, encoded=encoded, content_type=content_type
                    )
                )
                peer.start()
                room = HTTPRoom.__new__(HTTPRoom)
                room.url = "http://127.0.0.1:" + str(peer.server.server_port)
                room.token, room.ca_file, room.timeout = "x" * 32, None, 10
                room.authentication = "band-api-key"
                try:
                    with self.assertRaises(TransportError) as caught:
                        room._request("POST", "/v1/inbox", {})
                    self.assertEqual(
                        type(caught.exception).__name__ == "RoomScopeUnavailable",
                        permanent,
                    )
                    self.assertEqual(str(caught.exception), message)
                finally:
                    peer.close()

    def test_native_scope_loss_closes_both_participants_once_and_retains_journal(
        self,
    ) -> None:
        for kind in (RuntimeDriver, GenericRuntimeDriver):
            with self.subTest(kind=kind.__name__):
                driver = self.driver(kind)
                polls: list[dict[str, Any]] = []

                def response(
                    *args: Any,
                    driver: Any = driver,
                    polls: list[dict[str, Any]] = polls,
                    **kwargs: Any,
                ) -> dict[str, Any]:
                    polls.append(args[1])
                    if len(polls) >= 3:
                        driver.stopping.set()
                    return {"status": 404, "body": {"error": "not_found"}}

                with patch("remote_agent.network.call", side_effect=response):
                    driver.thread.start()
                    driver.thread.join(10)
                try:
                    self.assertFalse(driver.thread.is_alive())
                    self.assertEqual(
                        len(polls), 1, "Permanent scope loss must not poll again"
                    )
                    driver.service.engine.close.assert_called_once()
                    self.assertIsNone(driver.service.store.db)
                    self.assertTrue(driver.service.stopping.is_set())
                    self.assertEqual(driver.summary()["hold"], "scope_unavailable")
                    self.assertEqual(driver.summary()["status"], "held")
                    with self.assertRaisesRegex(ValueError, "scope_unavailable"):
                        driver.task({"id": "after-scope-loss"})
                    with self.assertRaisesRegex(ValueError, "scope_unavailable"):
                        driver.control("owner", {"action": "retry"})
                    with self.assertRaisesRegex(ValueError, "scope_unavailable"):
                        driver.replay()
                    with Store(driver.service.store.path) as retained:
                        self.assertEqual(retained.get("state"), {"answered": 0})
                        self.assertEqual(
                            retained.get("scope_hold"), "scope_unavailable"
                        )
                        retained._verify()
                    page = driver.journal(kind="scope_unavailable")
                    self.assertEqual(len(page["records"]), 1)
                finally:
                    driver.close()
                driver.service.engine.close.assert_called_once()

    def test_only_native_exact_scope_refusal_is_permanent(self) -> None:
        for authentication, status, body, permanent in (
            ("band-api-key", 404, {"error": "not_found"}, True),
            ("bearer", 404, {"error": "not_found"}, False),
            ("band-api-key", 404, {"error": "Unknown endpoint"}, False),
            ("band-api-key", 404, {"error": "not_found", "extra": True}, False),
            ("band-api-key", 500, {"error": "not_found"}, False),
            ("band-api-key", 401, {"error": "not_found"}, False),
            ("band-api-key", 403, {"error": "agent_stopped"}, False),
        ):
            with self.subTest(authentication=authentication, status=status, body=body):
                room = HTTPRoom.__new__(HTTPRoom)
                room.url, room.token, room.ca_file, room.timeout = (
                    "https://band.example/session",
                    "x" * 32,
                    None,
                    1,
                )
                room.authentication = authentication
                with (
                    patch(
                        "remote_agent.network.call",
                        return_value={"status": status, "body": body},
                    ),
                    self.assertRaises(TransportError) as caught,
                ):
                    room._request("POST", "/v1/inbox", {})
                self.assertEqual(
                    type(caught.exception).__name__ == "RoomScopeUnavailable", permanent
                )

    @settings(max_examples=12, deadline=None, phases=(Phase.explicit, Phase.generate))
    @given(statuses=st.lists(st.sampled_from([200, 503, 404]), min_size=1, max_size=6))
    @example(statuses=[503, 200, 404])
    @example(statuses=[503, 200])
    def test_generated_transient_then_permanent_lifecycle(
        self, statuses: list[int]
    ) -> None:
        driver = self.driver(GenericRuntimeDriver)
        polls: list[int] = []

        def response(*args: Any, **kwargs: Any) -> dict[str, Any]:
            status = statuses[len(polls)]
            polls.append(status)
            if len(polls) == len(statuses):
                driver.stopping.set()
            if status == 200:
                return {"status": 200, "body": {"events": [], "cursor": 0}}
            return {
                "status": status,
                "body": {"error": "not_found" if status == 404 else "unavailable"},
            }

        try:
            with patch("remote_agent.network.call", side_effect=response):
                driver.thread.start()
                driver.thread.join(10)
            self.assertFalse(driver.thread.is_alive())
            expected = (
                statuses[: statuses.index(404) + 1] if 404 in statuses else statuses
            )
            self.assertEqual(polls, expected)
            self.assertEqual(driver.service.store.db is None, 404 in statuses)
            if 404 in statuses:
                self.assertEqual(driver.summary()["hold"], "scope_unavailable")
            else:
                self.assertFalse(driver.service.stopping.is_set())
                if statuses[-1] == 200:
                    self.assertIsNone(driver.hold)
                    self.assertIsNotNone(driver.last_seen)
        finally:
            driver.close()
            self.doCleanups()
