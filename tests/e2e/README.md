# E2E tests

`*_e2e.py` drives the real migrated SQLite database and application services while replacing only
Telegram and the model at their network boundaries. `tests/test_*.py` keeps focused domain and
adapter checks; `*_ui.py` covers a Telegram screen or handler. Live Telegram tests stay under
`tests/e2e/live/` and require their explicit opt-in.

Each E2E file is named for the responsibility it checks, not for the feature that happens to use
it. `test_advisor_flow_e2e.py` keeps routing, session and multi-step process scenarios;
`test_proposals_e2e.py` keeps review and queue behaviour; `test_screens_e2e.py` keeps review-screen
behaviour; `test_saved_requests_e2e.py` keeps the saved-request flow. A BRD scenario may have
domain, UI and E2E evidence, so it does not need one test file of its own.
