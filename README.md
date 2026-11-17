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
