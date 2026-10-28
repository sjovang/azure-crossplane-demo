import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from app import main
from app.advisor import AdvisorError, Session, _append_message


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        main.sessions.clear()
        main.oauth_states.clear()

    def tearDown(self):
        main.sessions.clear()
        main.oauth_states.clear()

    def test_purge_removes_expired_sessions_and_oauth_states(self):
        expired_id = uuid4()
        active_id = uuid4()
        main.sessions[expired_id] = Session(expires_at=9)
        main.sessions[active_id] = Session(expires_at=11)
        main.oauth_states.update({"expired-state": expired_id, "active-state": active_id})

        main._purge_expired_sessions(now=10)

        self.assertNotIn(expired_id, main.sessions)
        self.assertNotIn("expired-state", main.oauth_states)
        self.assertIn(active_id, main.sessions)
        self.assertIn("active-state", main.oauth_states)

    def test_session_lookup_expires_credentials_and_history(self):
        session_id = uuid4()
        session = Session(
            expires_at=main.time.monotonic() - 1,
            github_access_token="token",
        )
        session.messages.append({"role": "user", "content": "private history"})
        main.sessions[session_id] = session

        self.assertIsNone(main._get_session(session_id))
        self.assertNotIn(session_id, main.sessions)

    def test_session_creation_rejects_over_capacity(self):
        with patch.object(main, "MAX_SESSIONS", 1):
            main._create_session()
            with self.assertRaises(HTTPException) as error:
                main._create_session()

        self.assertEqual(error.exception.status_code, 503)

    def test_conversation_history_is_bounded(self):
        session = Session()
        for _ in range(20):
            _append_message(session, "user", "x")
            _append_message(session, "assistant", "x")
        with self.assertRaises(AdvisorError):
            _append_message(session, "user", "x")

        session = Session()
        with self.assertRaises(AdvisorError):
            _append_message(session, "user", "x" * (64 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
