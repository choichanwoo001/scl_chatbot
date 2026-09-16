"""Bounded demo sessions and shared, expiring database sessions."""

from __future__ import annotations

import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock

from sqlalchemy import delete, select

from .models import ChatSession
from .schemas import TestInfo


@dataclass
class SessionState:
    history: list[dict[str, str]] = field(default_factory=list)
    last_test_code: str | None = None
    last_test_variant_key: str | None = None
    previous_tests: list[TestInfo] = field(default_factory=list)
    session_id: str = ""


def safe_session_id(value):
    return value if value and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) else uuid.uuid4().hex


class SessionStore:
    """Process-local store for isolated tests and standalone demo instances."""

    def __init__(self, history_limit, ttl_seconds=1800, max_entries=10000, *, clock=time.monotonic):
        self.history_limit = history_limit
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.clock = clock
        self._sessions = OrderedDict()
        self._lock = RLock()

    def get(self, session_id):
        safe_id = safe_session_id(session_id)
        now = self.clock()
        with self._lock:
            while self._sessions and next(iter(self._sessions.values()))[0] <= now:
                self._sessions.popitem(last=False)
            entry = self._sessions.pop(safe_id, None)
            state = entry[1] if entry else SessionState(session_id=safe_id)
            self._sessions[safe_id] = (now + self.ttl_seconds, state)
            while len(self._sessions) > self.max_entries:
                self._sessions.popitem(last=False)
        return safe_id, state

    def append(self, state, user, assistant):
        with self._lock:
            state.history.extend(
                [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
            )
            state.history[:] = state.history[-self.history_limit :]

    def delete(self, session_id):
        with self._lock:
            return self._sessions.pop(session_id, None) is not None


class DatabaseSessionStore(SessionStore):
    """Shared state across API processes; expired state is never returned."""

    def __init__(self, session_factory, history_limit, ttl_seconds=1800, max_entries=10000):
        super().__init__(history_limit, ttl_seconds, max_entries)
        self.session_factory = session_factory

    def get(self, session_id):
        safe_id = safe_session_id(session_id)
        now = datetime.now(UTC)
        with self.session_factory.begin() as session:
            session.execute(delete(ChatSession).where(ChatSession.expires_at <= now))
            row = session.get(ChatSession, safe_id)
            if row is None:
                return safe_id, SessionState(session_id=safe_id)
            payload = row.state_json
            return safe_id, SessionState(
                history=payload.get("history", [])[-self.history_limit :],
                last_test_code=payload.get("last_test_code"),
                last_test_variant_key=payload.get("last_test_variant_key"),
                previous_tests=[TestInfo.model_validate(item) for item in payload.get("previous_tests", [])],
                session_id=safe_id,
            )

    def append(self, state, user, assistant):
        super().append(state, user, assistant)
        now = datetime.now(UTC)
        values = dict(
            session_id=state.session_id,
            state_json={
                "history": state.history,
                "last_test_code": state.last_test_code,
                "last_test_variant_key": state.last_test_variant_key,
                "previous_tests": [item.model_dump(mode="json") for item in state.previous_tests],
            },
            updated_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
        )
        with self.session_factory.begin() as session:
            # Dialect-native upsert also handles two simultaneous first requests.
            if session.bind.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert
            statement = insert(ChatSession).values(**values)
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[ChatSession.session_id],
                    set_=values,
                )
            )
            session.execute(delete(ChatSession).where(ChatSession.expires_at <= now))
            overflow = list(
                session.scalars(
                    select(ChatSession.session_id)
                    .order_by(ChatSession.updated_at.desc(), ChatSession.session_id)
                    .offset(self.max_entries)
                )
            )
            for offset in range(0, len(overflow), 500):
                session.execute(
                    delete(ChatSession).where(ChatSession.session_id.in_(overflow[offset : offset + 500]))
                )

    def delete(self, session_id):
        with self.session_factory.begin() as session:
            return bool(
                session.execute(delete(ChatSession).where(ChatSession.session_id == session_id)).rowcount
            )
