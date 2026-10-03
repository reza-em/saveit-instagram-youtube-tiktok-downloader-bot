# Contributing to SaveIt downloader bot

Thanks for your interest in improving this project! Bug reports, ideas, translations and pull requests are all welcome (English or Persian).

## Reporting bugs and requesting features

- Search the existing issues first to avoid duplicates.
- Use the issue templates and describe what you expected, what happened, and how to reproduce it.
- **Never paste bot tokens, API keys, cookies or personal data** into an issue. If you did by accident, revoke the token and edit the issue.
- For security problems, please follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Development setup

```bash
git clone <your fork>
cd <repo>
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Copy the example environment settings described in the README and use a **test** bot token of your own. Never commit real tokens, `.env` files, databases or `state.json`.

## Running the tests

The offline test suite uses a mocked chat-platform API, so it needs no network access and no real token:

```bash
./venv/bin/python test_offline.py                              # Telegram build
DL_PLATFORM=bale DL_BALE_BOT_TOKEN=x ./venv/bin/python test_offline.py   # same suite against the Bale build
./venv/bin/python test_bale.py                              # Bale adapter tests
```

Please make sure the suite passes before opening a pull request.

The `live_*_test.py` scripts hit real services and are optional; do not run them in a way that posts anything or uses someone else's account.

## Pull requests

1. Fork the repo and create a topic branch from `main` (for example `fix/short-description`).
2. Keep each pull request small and focused; one logical change per PR.
3. Add or update tests when you change behaviour, and update the README if user-facing behaviour changes.
4. Keep user-facing strings in the texts module, in both Persian and English where the project is bilingual.
5. Write clear commit messages and explain *why* the change is needed in the PR description.

By contributing you agree that your contribution is licensed under the project's license (see `LICENSE`).
