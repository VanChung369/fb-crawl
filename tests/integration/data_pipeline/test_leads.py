import os
from uuid import uuid4

import psycopg
import pytest

from fb_crawl.history.leads import PostgresLeadRepository
from fb_crawl.interaction_sessions.models import SessionIdentity, SessionError
from fb_data_pipeline.repositories.migrations import MigrationRunner
from tests.integration.data_pipeline.test_history_isolation import _safe_test_database

URL = os.environ.get('TEST_DATABASE_URL', '').strip()
pytestmark = pytest.mark.skipif(not _safe_test_database(URL), reason='Requires explicit disposable PostgreSQL TEST_DATABASE_URL ending in _test')


def test_leads_persist_across_connections_are_account_scoped_and_reject_stale_edits():
    MigrationRunner(URL).apply()
    owners = []
    try:
        with psycopg.connect(URL) as db:
            for _ in range(2):
                email = f'{uuid4()}@example.test'
                owners.append(db.execute("INSERT INTO accounts(normalized_email,display_email,password_hash,status) VALUES (%s,%s,'test','active') RETURNING id", (email, email)).fetchone()[0])
        first = PostgresLeadRepository(URL)
        identity = SessionIdentity(username='sample')
        assert first.save(owners[0], identity, 'potential', 'Gọi lại', 0)['revision'] == 1
        second = PostgresLeadRepository(URL)
        assert second.read(owners[0], [identity])[0]['notes'] == 'Gọi lại'
        assert second.read(owners[1], [identity])[0]['notes'] == ''
        with pytest.raises(SessionError, match='lead_revision_conflict'):
            second.save(owners[0], identity, 'closed', 'stale', 0)
        linked = SessionIdentity(facebook_uid='100123456', username='sample')
        assert second.read(owners[0], [linked])[0]['notes'] == 'Gọi lại'
        assert first.read(owners[0], [SessionIdentity(facebook_uid='100123456')])[0]['notes'] == 'Gọi lại'
        second.save(owners[0], linked, 'called', 'Đã gọi', 1)
        assert first.read(owners[0], [SessionIdentity(facebook_uid='100123456')])[0]['notes'] == 'Đã gọi'
    finally:
        with psycopg.connect(URL) as db:
            db.execute('DELETE FROM accounts WHERE id=ANY(%s)', (owners,))
