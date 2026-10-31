from __future__ import annotations

import tempfile
import threading
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

from remote_agent.band.arithmetic import RuntimeDriver
from remote_agent.band.generic import GenericRuntimeDriver
from remote_agent.controller import HybridAgent
from remote_agent.network import HTTPRoom
from remote_agent.service import Service
from remote_agent.store import Store
from remote_agent.transport import EngineProcess


class RoomRefusalHandler(BaseHTTPRequestHandler):
    def __init__(
        self, *args: Any, encoded: bytes, content_type: str, **kwargs: Any
    ) -> None:
        self.encoded, self.content_type = encoded, content_type
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(404)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.encoded)))
        self.end_headers()
        self.wfile.write(self.encoded)


class RoomScopeLossFixture(unittest.TestCase):
    def driver(self, kind: type[RuntimeDriver] = RuntimeDriver) -> Any:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        store = Store(root / "participant.sqlite3")
        self.addCleanup(store.close)
        store.commit(
            "initialize",
            {},
            updates={
                "queue": [],
                "staged": None,
                "execution": None,
                "inbox_cursor": 0,
                "turns": 0,
                "state": {"answered": 0},
                "program": {"sha256": "p"},
                "metadata": {"coverage": {"mode": "full"}},
            },
        )
        room = HTTPRoom.__new__(HTTPRoom)
        room.url, room.token, room.ca_file, room.timeout = (
            "https://band.example/session",
            "x" * 32,
            None,
            1,
        )
        room.authentication, room.inbox_profile = (
            "band-api-key",
            "contract-room/inbox/1",
        )
        room.actor, room.session = "agent", "session"
        agent = HybridAgent.__new__(HybridAgent)
        agent.store, agent.room, agent.actor = store, room, "agent"
        agent.lock, agent.stopping, agent.closed = (
            threading.RLock(),
            threading.Event(),
            False,
        )
        agent.repair_thread = None
        agent.recover = Mock(return_value={"status": "idle"})
        agent.retain_history = Mock()
        engine = SimpleNamespace(
            closed=False, process=SimpleNamespace(poll=lambda: None), close=Mock()
        )
        service = Service.__new__(Service)
        service.agent, service.room, service.store, service.engine = (
            agent,
            room,
            store,
            cast(EngineProcess, engine),
        )
        service.stopping, service.poll_wake = threading.Event(), threading.Event()
        service.held = False
        service.resources = ExitStack()
        service.resources.callback(engine.close)
        service.resources.callback(store.close)
        service.http = Mock()
        driver: Any = kind.__new__(kind)
        driver.service, driver.workspace, driver.durable = service, root, True
        driver.hold, driver.business_hold, driver.last_seen = None, None, None
        driver.stopping, driver.last_retained_turns = threading.Event(), -1
        driver.lifecycle_lock = threading.RLock()
        driver.options = {"target_bytes": 1000, "minimum_steps": 1}
        driver.verify_business_results = Mock()
        if kind is GenericRuntimeDriver:
            driver.task_rooms, driver.quality_sha256 = {}, "quality"
        driver.thread = threading.Thread(target=driver.run)
        return driver
