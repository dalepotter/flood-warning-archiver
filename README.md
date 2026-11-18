# CAP Warnings Archive

[![Tests](https://github.com/dalepotter/flood-warning-archiver/actions/workflows/test.yml/badge.svg)](https://github.com/dalepotter/flood-warning-archiver/actions/workflows/test.yml)

Archives Environment Agency (EA) flood warning messages, in Common Alerting Protocol (CAP) format, as raw
XML. A GitHub Actions workflow polls the EA feed every hour and commits any new messages to this
repository.

## How it works

1. Read the EA CAP ATOM feed (`https://cap-xml.prd.defra.cloud/v2/messages.atom`). The feed is only an
   index: each entry points at a full CAP message and carries no warning content.
2. For every entry whose message ID is not already in `warnings/`, fetch `/v2/message/<id>`.
3. Check the response is a CAP 1.2 `alert` (so an error page is never archived).
4. Save the bytes **exactly as received** as `warnings/<id>.xml`. Existing files are never overwritten.

Message IDs are validated as digits only, because they become filenames.

The repository is the only state. Because the whole index is inspected on every run and de-duplicated by
message ID, a missed or failed run is recovered automatically by the next one.

## Archive layout

```
warnings/
├── 132457.xml
├── 132508.xml
└── 132514.xml
```

The filename is the message number from the EA URL. To see how a warning evolved, read the `references`
element in each message: updates and cancellations point back at the messages they supersede.

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python cap_poller.py                         # archive into ./warnings
python cap_poller.py --warnings-dir /tmp/w   # archive somewhere else
python cap_poller.py -v                      # debug logging
```

Exit codes: `0` success (including "nothing new"); `1` the feed could not be read, or at least one message
could not be archived (the others are still saved, and failures are retried on the next run).

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

Tests never call the live EA service; HTTP is mocked.

## Automation

`.github/workflows/poll.yml` runs hourly (at 17 minutes past) and on demand (Actions tab → "Poll CAP
feed" → "Run workflow"). New messages are committed to `main` by `github-actions[bot]` using
[git-auto-commit-action](https://github.com/stefanzweifel/git-auto-commit-action), for example
`data: Archive 2 new CAP message(s) (132562, 132570)`. Each run's summary lists what was archived and
what failed. If polling fails, messages archived before the failure are still committed and the run is
marked failed.

Operating notes:

- **Permissions:** the workflow needs `contents: write`. Check Settings → Actions → General → Workflow
  permissions if the push is rejected.
- **Branch protection:** if `main` requires pull requests, the bot's push will be rejected. Allow the bot
  to bypass the rule, or archive to a dedicated branch.
- **Scheduled runs are best-effort:** GitHub may delay them, and in public repositories it disables
  scheduled workflows after 60 days without repository activity. Re-enable from the Actions tab if that
  happens.
- **Commits made with `GITHUB_TOKEN` do not trigger other workflows**, so archive commits do not run the
  test workflow.
- **Repository growth:** messages are typically 23-127 KB each. Watch the repository size over time.

## Known limitations

This is an unofficial archive of a service for which EA has not published consumer documentation, a rate
limit or a service level agreement.

- **Messages can leave the feed before we see them.** Entries have been observed to disappear from the
  index while the message stayed retrievable. With hourly polling, a message superseded within the hour
  may never appear in the index. The archive is therefore best-effort, not guaranteed complete.
- **Immutability is assumed, not confirmed.** Each message is fetched once and never re-checked.
- **Absence from the feed does not mean a warning has been withdrawn.** Withdrawals are published as new
  messages (`msgType` `Cancel`, with `references`). Do not use this archive to decide what is currently in
  force without interpreting those fields.
- **Do not use this archive for safety-critical decisions.**

## Data licence

Warning data is © Environment Agency and licensed under
[Creative Commons BY 4.0](https://creativecommons.org/licenses/by/4.0/) (as stated in the feed's `rights`
element). Redistributing it, as this repository does, requires that attribution.
