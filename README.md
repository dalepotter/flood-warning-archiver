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

## Archive layout

Each CAP message is stored unchanged as `warnings/<message-id>.xml`, where the ID is the number in the
message URL (`/v2/message/<id>`). Files are never overwritten: if an ID is already archived it is not
fetched again.
