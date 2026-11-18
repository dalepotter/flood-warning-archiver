# CAP Warnings Archive

[![Tests](https://github.com/dalepotter/flood-warning-archiver/actions/workflows/test.yml/badge.svg)](https://github.com/dalepotter/flood-warning-archiver/actions/workflows/test.yml)

Archives Environment Agency flood warning (CAP) messages as raw XML.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Testing

```bash
pytest
```

## How it works

1. Read the EA CAP ATOM feed. The feed is only an index: each entry points at a full CAP message.
   Message IDs are validated as digits only, because they become filenames.
2. Fetch each message that is not yet archived and check it really is a CAP 1.2 `alert`, so error pages
   are never archived.

## Archive layout

Each CAP message is stored unchanged as `warnings/<message-id>.xml`, where the ID is the number in the
message URL (`/v2/message/<id>`). Files are never overwritten: if an ID is already archived it is not
fetched again.

## Usage

```bash
python cap_poller.py                         # archive into ./warnings
python cap_poller.py --warnings-dir /tmp/w   # archive somewhere else
python cap_poller.py -v                      # debug logging
```

Exit codes: `0` success (including "nothing new"); `1` the feed could not be read, or at least one
message could not be archived (the others are still saved, and failures are retried on the next run).

## Automation

`.github/workflows/poll.yml` runs `python cap_poller.py` every hour (at 17 minutes past) and on demand
(Actions tab → "Poll CAP feed" → "Run workflow"). Any new messages are committed to `main` by
`github-actions[bot]` using
[git-auto-commit-action](https://github.com/stefanzweifel/git-auto-commit-action).

Each run's summary page lists what was archived, and the commit message names the message IDs, for
example `data: Archive 2 new CAP message(s) (132562, 132570)`. If polling fails, any messages already
archived are still committed and the run is marked failed.

Scheduled runs are best-effort: GitHub may delay them at busy times.
