from contextlib import contextmanager
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from fb_crawl.interaction_sessions.models import SessionError
from fb_data_pipeline.repositories.errors import DatabaseError


def match_lead(rows, identity):
    matches = [row for row in rows if (identity.facebook_uid and row['facebook_uid'] == identity.facebook_uid)
               or (identity.username and row['username'] == identity.username)]
    if len(matches) > 1 or (matches and identity.facebook_uid and matches[0]['facebook_uid']
                            and identity.facebook_uid != matches[0]['facebook_uid']):
        raise SessionError('lead_identity_conflict', 409)
    return matches[0] if matches else None


def lead_value(row):
    return {key: row[key] for key in ('status', 'notes', 'revision')} if row else {
        'status': 'unprocessed', 'notes': '', 'revision': 0,
    }


class PostgresLeadRepository:
    def __init__(self, database_url, *, statement_timeout_seconds=5, connect_factory=psycopg.connect):
        self.database_url = database_url
        self.connect_factory = connect_factory
        self.timeout = max(1, round(statement_timeout_seconds * 1000))

    @contextmanager
    def connect(self):
        try:
            with self.connect_factory(self.database_url) as connection:
                with connection.cursor(row_factory=dict_row) as db:
                    db.execute("SELECT set_config('statement_timeout', %s, true)", (f'{self.timeout}ms',))
                    yield db
        except (psycopg.Error, OSError) as error:
            raise DatabaseError('Database operation failed.') from error

    def read(self, owner, identities):
        with self.connect() as db:
            if any(i.facebook_uid and i.username for i in identities):
                db.execute('SELECT id FROM accounts WHERE id=%s FOR UPDATE', (owner,))
            db.execute('''SELECT * FROM account_leads WHERE account_id=%s
                AND (facebook_uid=ANY(%s) OR username=ANY(%s))''',
                (owner, [i.facebook_uid for i in identities if i.facebook_uid], [i.username for i in identities if i.username]))
            rows = db.fetchall()
            for identity in identities:
                row = match_lead(rows, identity)
                if row and identity.facebook_uid and identity.username and (not row['facebook_uid'] or not row['username']):
                    # Preserve the same customer when a later scan supplies the missing UID/username.
                    # Alias linking does not modify the user's notes or edit revision.
                    db.execute('''UPDATE account_leads SET facebook_uid=COALESCE(facebook_uid,%s),
                        username=COALESCE(username,%s) WHERE account_id=%s AND id=%s''',
                        (identity.facebook_uid, identity.username, owner, row['id']))
                    row['facebook_uid'] = row['facebook_uid'] or identity.facebook_uid
                    row['username'] = row['username'] or identity.username
            return [lead_value(match_lead(rows, identity)) for identity in identities]

    def save(self, owner, identity, status, notes, revision):
        with self.connect() as db:
            # Serialize alias claims for this account, including first-time inserts.
            db.execute('SELECT id FROM accounts WHERE id=%s FOR UPDATE', (owner,))
            if db.fetchone() is None:
                raise SessionError('account_not_found', 404)
            db.execute('''SELECT * FROM account_leads WHERE account_id=%s
                AND (facebook_uid=%s OR username=%s) FOR UPDATE''',
                (owner, identity.facebook_uid or None, identity.username or None))
            row = match_lead(db.fetchall(), identity)
            if revision != (row['revision'] if row else 0):
                raise SessionError('lead_revision_conflict', 409)
            if row:
                db.execute('''UPDATE account_leads SET status=%s, notes=%s, revision=revision+1,
                    facebook_uid=COALESCE(%s, facebook_uid), username=COALESCE(%s, username), updated_at=now()
                    WHERE id=%s AND account_id=%s RETURNING *''',
                    (status, notes, identity.facebook_uid or None, identity.username or None, row['id'], owner))
            else:
                db.execute('''INSERT INTO account_leads (id, account_id, facebook_uid, username, status, notes)
                    VALUES (%s,%s,%s,%s,%s,%s) RETURNING *''',
                    (uuid4(), owner, identity.facebook_uid or None, identity.username or None, status, notes))
            return lead_value(db.fetchone())
