from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.jobs import Page
from fb_data_pipeline.repositories.errors import DatabaseError
from .models import (
    AcceptedRow, BatchAck, RowFilters, SessionCounters, SessionCreate, SessionError,
    SessionFilters, SessionIdentity, SessionRow, SessionRowInput, SessionSummary,
    decode_page_cursor, encode_page_cursor, require_time, require_uuid,
)


def validate_batch(kind, rows, existing, total) -> tuple[SessionRowInput, ...]:
    if not 1 <= len(rows) <= 100:
        raise ValidationError("A session batch must contain 1 to 100 rows.")
    if len({r.client_row_id for r in rows}) != len(rows):
        raise SessionError("session_duplicate_row", 422)
    if total + sum(r.client_row_id not in existing for r in rows) > 5000:
        raise SessionError("session_limit_reached")
    changed = []
    for item in rows:
        if (kind == "reactions") != (item.kind == "reaction"):
            raise SessionError("session_kind_conflict", 422)
        old = existing.get(item.client_row_id)
        if old is not None:
            immutable = ("identity", "kind", "interaction_id", "parent_id", "synthetic")
            if any(getattr(old, name) != getattr(item, name) for name in immutable):
                raise SessionError("session_row_conflict")
            if item.row_revision < old.row_revision:
                continue
            if item.row_revision == old.row_revision:
                if item != old:
                    raise SessionError("session_row_conflict")
                continue
        changed.append(item)
    return tuple(changed)


_SUMMARY = """
SELECT s.*,
 (SELECT count(*) FROM session_interactions i WHERE i.session_id = s.id) AS interactions,
 (SELECT count(*) FROM session_people p WHERE p.session_id = s.id) AS people,
 (SELECT count(*) FROM session_people p LEFT JOIN lookup_events e ON e.id = p.lookup_event_id
   WHERE p.session_id = s.id AND COALESCE(e.outcome, p.lookup_state) IN ('found','not_found','failed','quota_exceeded')) AS processed,
 (SELECT count(*) FROM session_people p JOIN lookup_events e ON e.id = p.lookup_event_id
   WHERE p.session_id = s.id AND e.outcome = 'found') AS found,
 (SELECT count(*) FROM session_people p LEFT JOIN lookup_events e ON e.id = p.lookup_event_id
   WHERE p.session_id = s.id AND COALESCE(e.outcome, p.lookup_state) IN ('failed','quota_exceeded')) AS failures
FROM interaction_sessions s
"""


def _summary(row) -> SessionSummary:
    return SessionSummary(**{key: row[key] for key in (
        "id", "client_session_id", "source_url", "kind", "revision", "status", "created_at", "updated_at"
    )}, counters=SessionCounters(**{key: int(row[key]) for key in (
        "interactions", "people", "processed", "found", "failures"
    )}))


def _input(row) -> SessionRowInput:
    return SessionRowInput(**{key: row[key] for key in (
        "client_row_id", "row_revision", "interaction_id", "synthetic", "parent_id", "kind", "text", "observed_at"
    )}, identity=SessionIdentity(**row["identity"]))


class PostgresInteractionSessionRepository:
    def __init__(self, database_url: str, *, statement_timeout_seconds: float = 5.0, connect_factory=psycopg.connect):
        self.database_url = database_url
        self.connect_factory = connect_factory
        self.timeout_ms = max(1, round(statement_timeout_seconds * 1000))

    @contextmanager
    def _connect(self):
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor(row_factory=dict_row) as cursor:
                    cursor.execute("SELECT set_config('statement_timeout', %s, true)", (f"{self.timeout_ms}ms",))
                    yield cursor
        except (psycopg.Error, OSError) as error:
            raise DatabaseError("Database operation failed.") from error

    @staticmethod
    def _lock(cursor, account_id, session_id):
        if type(account_id) is not int or account_id <= 0:
            raise ValidationError("Invalid session account.")
        require_uuid(session_id)
        cursor.execute("SELECT * FROM interaction_sessions WHERE id = %s AND account_id = %s FOR UPDATE", (session_id, account_id))
        row = cursor.fetchone()
        if row is None:
            raise SessionError("interaction_session_not_found", 404)
        return row

    @staticmethod
    def _get(cursor, account_id, session_id):
        cursor.execute(_SUMMARY + " WHERE s.account_id = %s AND s.id = %s", (account_id, session_id))
        row = cursor.fetchone()
        return _summary(row) if row else None

    def create(self, account_id, value: SessionCreate, now: datetime):
        require_time(now)
        if type(account_id) is not int or account_id <= 0:
            raise ValidationError("Invalid session account.")
        with self._connect() as cursor:
            cursor.execute("""
                INSERT INTO interaction_sessions (id, account_id, client_session_id, source_url, kind, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (account_id, client_session_id) DO NOTHING
            """, (uuid4(), account_id, value.client_session_id, value.source_url, value.kind, now, now))
            cursor.execute("SELECT * FROM interaction_sessions WHERE account_id = %s AND client_session_id = %s FOR UPDATE", (account_id, value.client_session_id))
            row = cursor.fetchone()
            if row["source_url"] != value.source_url or row["kind"] != value.kind:
                raise SessionError("session_idempotency_conflict")
            return self._get(cursor, account_id, row["id"])

    def get(self, account_id, session_id):
        require_uuid(session_id)
        with self._connect() as cursor:
            return self._get(cursor, account_id, session_id)

    def upsert_rows(self, account_id, session_id, rows, now):
        require_time(now)
        with self._connect() as cursor:
            session = self._lock(cursor, account_id, session_id)
            cursor.execute("SELECT * FROM session_interactions WHERE session_id = %s AND client_row_id = ANY(%s)", (session_id, [r.client_row_id for r in rows]))
            existing = {r["client_row_id"]: _input(r) for r in cursor.fetchall()}
            cursor.execute("SELECT count(*) AS total FROM session_interactions WHERE session_id = %s", (session_id,))
            changed = validate_batch(session["kind"], rows, existing, cursor.fetchone()["total"])
            for item in changed:
                cursor.execute("""INSERT INTO session_people (id,session_id,identity_key,identity,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (session_id, identity_key) DO NOTHING""",
                    (uuid4(), session_id, item.identity.key, Jsonb(asdict(item.identity)), now, now))
                cursor.execute("SELECT id, identity FROM session_people WHERE session_id = %s AND identity_key = %s", (session_id, item.identity.key))
                person = cursor.fetchone()
                known = SessionIdentity(**person["identity"])
                if known.facebook_uid and item.identity.facebook_uid and known.facebook_uid != item.identity.facebook_uid:
                    raise SessionError("session_identity_conflict")
                # Two conflicting username claims for one UID are not alias evidence.
                if known.username and item.identity.username and known.username != item.identity.username:
                    raise SessionError("session_identity_conflict")
                cursor.execute("""INSERT INTO session_interactions
                    (session_id,client_row_id,row_revision,interaction_id,synthetic,parent_id,kind,person_id,identity,text,observed_at,ingested_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (session_id,client_row_id) DO UPDATE SET row_revision=EXCLUDED.row_revision,
                    text=EXCLUDED.text, observed_at=EXCLUDED.observed_at""",
                    (session_id,item.client_row_id,item.row_revision,item.interaction_id,item.synthetic,item.parent_id,item.kind,
                     person["id"],Jsonb(asdict(item.identity)),item.text,item.observed_at,now))
            if changed:
                cursor.execute("UPDATE interaction_sessions SET revision=revision+1, updated_at=%s WHERE id=%s", (now, session_id))
            cursor.execute("SELECT client_row_id,row_revision,person_id FROM session_interactions WHERE session_id=%s AND client_row_id=ANY(%s)", (session_id, [r.client_row_id for r in rows]))
            stored = {r["client_row_id"]: r for r in cursor.fetchall()}
            # Ack the submitted revision, including already superseded writes.
            return BatchAck(session["revision"] + bool(changed), tuple(
                AcceptedRow(r.client_row_id, r.row_revision, stored[r.client_row_id]["person_id"]) for r in rows
            ))

    def transition(self, account_id, session_id, status, revision, now):
        require_time(now)
        if status not in ("running", "stopped") or type(revision) is not int or revision < 1:
            raise ValidationError("Invalid session transition.")
        with self._connect() as cursor:
            session = self._lock(cursor, account_id, session_id)
            if status == "stopped" and session["status"] == "stopped":
                return self._get(cursor, account_id, session_id)
            if session["revision"] != revision:
                raise SessionError("session_revision_conflict")
            if session["status"] != status:
                cursor.execute("UPDATE interaction_sessions SET status=%s,revision=revision+1,updated_at=%s WHERE id=%s", (status, now, session_id))
            return self._get(cursor, account_id, session_id)

    def delete(self, account_id, session_id):
        with self._connect() as cursor:
            self._lock(cursor, account_id, session_id)
            cursor.execute("DELETE FROM interaction_sessions WHERE id=%s AND account_id=%s", (session_id, account_id))
            return True

    def get_person(self, account_id, session_id, person_id):
        with self._connect() as cursor:
            self._lock(cursor, account_id, session_id)
            cursor.execute("SELECT identity FROM session_people WHERE session_id=%s AND id=%s", (session_id, person_id))
            row = cursor.fetchone()
            if row is None:
                raise SessionError("interaction_session_not_found", 404)
            return SessionIdentity(**row["identity"])

    def record_result(self, account_id, session_id, person_id, event_id, state, now):
        with self._connect() as cursor:
            cursor.execute("SELECT id FROM interaction_sessions WHERE id=%s AND account_id=%s FOR UPDATE", (session_id, account_id))
            if cursor.fetchone() is None:
                return  # An in-flight lookup must never recreate a deleted session.
            cursor.execute("""UPDATE session_people SET lookup_state=%s,updated_at=%s
                WHERE session_id=%s AND id=%s AND lookup_event_id=%s AND lookup_state<>%s RETURNING id""",
                (state, now, session_id, person_id, event_id, state))
            if cursor.fetchone():
                cursor.execute("UPDATE interaction_sessions SET revision=revision+1,updated_at=%s WHERE id=%s", (now, session_id))

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError("Session page limit must be from 1 to 100.")

    def list_sessions(self, account_id, filters: SessionFilters, cursor: str | None = None, limit=100):
        self._limit(limit)
        where, params = ["s.account_id=%s"], [account_id]
        for field, operator in (("source_url", "="), ("status", "="), ("created_from", ">="), ("created_to", "<=")):
            value = getattr(filters, field)
            if value is not None:
                where.append(f"s.{'created_at' if field.startswith('created_') else field}{operator}%s")
                params.append(value)
        scope = str(account_id)
        if cursor:
            time, identifier = decode_page_cursor(cursor, scope, filters)
            where.append("(s.created_at,s.id)<(%s,%s)")
            params.extend((time, identifier))
        with self._connect() as db:
            db.execute(_SUMMARY + " WHERE " + " AND ".join(where) + " ORDER BY s.created_at DESC,s.id DESC LIMIT %s", (*params, limit + 1))
            rows = db.fetchall()
        items = tuple(_summary(r) for r in rows[:limit])
        token = encode_page_cursor(items[-1].created_at, items[-1].id, scope, filters) if len(rows) > limit else None
        return Page(items, token)

    def list_rows(self, account_id, session_id, filters: RowFilters, cursor: str | None = None, limit=100):
        self._limit(limit)
        where, params = ["i.session_id=%s"], [session_id]
        for field in ("author", "text"):
            value = getattr(filters, field)
            if value:
                column = "concat_ws(' ',i.identity->>'name',i.identity->>'username',i.identity->>'facebook_uid')" if field == "author" else "i.text"
                where.append(f"strpos(lower({column}),lower(%s))>0")
                params.append(value)
        if filters.kind:
            where.append("i.kind=%s")
            params.append(filters.kind)
        if filters.outcome:
            where.append("COALESCE(e.outcome,CASE WHEN p.lookup_state='not_looked_up' THEN 'not_looked_up' ELSE 'unavailable' END)=%s")
            params.append(filters.outcome)
        scope = f"{account_id}/{session_id}"
        if cursor:
            time, identifier = decode_page_cursor(cursor, scope, filters)
            where.append("(i.ingested_at,i.client_row_id)<(%s,%s)")
            params.extend((time, identifier))
        with self._connect() as db:
            self._lock(db, account_id, session_id)
            db.execute("""SELECT i.*,p.lookup_event_id FROM session_interactions i
                JOIN session_people p ON p.id=i.person_id AND p.session_id=i.session_id
                LEFT JOIN lookup_events e ON e.id=p.lookup_event_id AND e.account_id=%s WHERE """ +
                " AND ".join(where) + " ORDER BY i.ingested_at DESC,i.client_row_id DESC LIMIT %s", (account_id, *params, limit + 1))
            rows = db.fetchall()
        items = tuple(SessionRow(**{**asdict(_input(r)), "identity": SessionIdentity(**r["identity"])},
                                 person_id=r["person_id"], lookup_event_id=r["lookup_event_id"]) for r in rows[:limit])
        token = encode_page_cursor(rows[limit-1]["ingested_at"], items[-1].client_row_id, scope, filters) if len(rows) > limit else None
        return Page(items, token)
