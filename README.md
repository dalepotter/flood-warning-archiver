# CAP Warnings Archive

[![Tests](https://github.com/dalepotter/flood-warning-archiver/actions/workflows/test.yml/badge.svg)](https://github.com/dalepotter/flood-warning-archiver/actions/workflows/test.yml)

Archives Environment Agency (EA) flood warning messages, in Common Alerting Protocol (CAP) format, as raw
XML. A GitHub Actions workflow polls the EA feed every hour and commits new or changed messages to
this repository.

## How it works

1. Read the EA CAP ATOM feed (`https://cap-xml.prd.defra.cloud/v2/messages.atom`). The feed is only an
   index: each entry points at a full CAP message and carries no warning content.
2. Fetch `/v2/message/<id>` for every entry, including messages already in `warnings/`, so the
   archived copy can be checked for changes. Messages named in a newly saved or changed message's
   `references` are fetched too if they are not yet archived, because superseded messages can leave
   the index while remaining retrievable.
3. Check the response is a CAP 1.2 `alert` (so an error page is never archived).
4. Save the bytes **exactly as received** as `warnings/<id>.xml`. If the fetched bytes differ from an
   existing copy, replace it atomically; the poll workflow commits the updated file.
5. Save the index itself, as received, to `feed/messages.atom`, replacing the previous copy. The file is
   only rewritten (and so only committed) when its content has changed, ignoring changes to the
   feed-level `<updated>` timestamp; git history keeps earlier versions. A feed that fails to parse
   is never saved.
6. Maintain `alerts.csv`, adding a row for each successfully fetched message and replacing its row
   when it changes. The workflow commits the CSV alongside the XML and feed.
7. Regenerate `alert-chains.yml` from the CSV records (see [Alert chains](#alert-chains-alert-chainsyml)).
   The whole file is rebuilt on every run, but it is only rewritten (and so only committed) when its
   content changes.

The CSV contains `message_id`, `sent`, `msgType`, `responseType`, `urgency`, `severity`,
`certainty`, `onset`, `expires`, `headline`, `awareness_level`, `awareness_type`, `impacts`, and
`references`. The awareness and impacts fields are matched by the CAP parameter's `valueName`.
The `references` column contains referenced message IDs separated by `|`, for example
`132629|132752|132810`. IDs retain their source order, with duplicates removed; no references
produces an empty cell. Values are preserved as text;
missing optional fields are empty. Repeated response types or matching parameters are joined with
`; `. Rows are sorted by numeric message ID and quoted using standard CSV escaping.
Each message has one row (including updates and cancellations). Multiple `info` blocks cannot be
represented by this schema: the raw XML is still archived, but a CSV failure is reported rather than
silently selecting a block. The CSV holds the latest recorded values; Git retains previous versions.

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

## Alert chains (`alert-chains.yml`)

`alert-chains.yml` groups the messages in `alerts.csv` into chains: one per `Alert` message, followed by
every `Update` and `Cancel` that supersedes it. It is derived entirely from `alerts.csv` and rebuilt
in full by every poll and every `--backfill-csv`, so it never needs editing by hand. The output is
deterministic: the same records always produce the same bytes. The first line is a comment, which
YAML parsers ignore.

A message belongs to the chain named by the **first** ID in its `references`. A message with no
references starts its own chain. Chains are ordered by the ID of their first message, and messages
within a chain are ordered by ID, matching the row order of `alerts.csv`.

```yaml
source: alerts.csv
message_count: 262
chain_count: 65
chains:
  - location: "North Sea coast at Bridlington"
    alert_id: 132874
    alert_sent: 2026-11-16T16:00:53+00:00
    status: closed
    latest_id: 133295
    latest_sent: 2026-11-18T09:17:24+00:00
    duration: 1d 17h
    duration_hours: 41
    issues: []
    messages:
      - {id: 132874, type: Alert, sent: 2026-11-16T16:00:53+00:00, elapsed: 0d 0h, elapsed_hours: 0, level: possible}
      - {id: 132888, type: Update, sent: 2026-11-16T20:59:44+00:00, elapsed: 0d 5h, elapsed_hours: 5, level: possible}
      - {id: 132952, type: Update, sent: 2026-11-17T08:43:06+00:00, elapsed: 0d 17h, elapsed_hours: 17, level: possible}
      - {id: 133190, type: Update, sent: 2026-11-17T20:56:49+00:00, elapsed: 1d 5h, elapsed_hours: 29, level: possible}
      - {id: 133295, type: Cancel, sent: 2026-11-18T09:17:24+00:00, elapsed: 1d 17h, elapsed_hours: 41, level: possible}
```

Top-level fields:

| Field | Description |
| --- | --- |
| `source` | The CSV the chains were built from. |
| `message_count` | Number of messages (CSV rows) included. Equals the total of all `messages` lists. |
| `chain_count` | Number of chains. |
| `chains` | List of chains, described below. |

Chain fields:

| Field | Description |
| --- | --- |
| `location` | The area from the `headline`: the text after the first `: `, for example `Flooding is possible: Tidal Fal Estuary` gives `Tidal Fal Estuary`. Several chains can share a location. |
| `alert_id` | Message ID of the chain's first message, normally the `Alert`. |
| `alert_sent` | `sent` time of that message, or `null` if it is not usable (see `invalid_sent`). |
| `status` | `closed` if the chain's latest message is a `Cancel`, otherwise `open`. This reflects the archive only, not what EA currently has in force. |
| `latest_id`, `latest_sent` | ID and `sent` time (or `null`) of the chain's latest message, which is the one with the highest ID. |
| `duration` | Time from `alert_sent` to `latest_sent`, as `<days>d <hours>h`, rounded to the nearest hour. `null` if either time is not usable. |
| `duration_hours` | The same, as a whole number of hours. |
| `issues` | Reconciliation tags, described below. `[]` when nothing was found. |
| `messages` | Every message in the chain, one flow mapping per line. |

Message fields:

| Field | Description |
| --- | --- |
| `id` | Message ID. |
| `type` | CAP `msgType`: `Alert`, `Update` or `Cancel`. |
| `sent` | CAP `sent` time. Timestamps are unquoted ISO 8601, so YAML parsers load them as datetimes. `null` if the time is missing, cannot be parsed, or has no UTC offset. |
| `elapsed`, `elapsed_hours` | Time since `alert_sent`, formatted and rounded as for `duration`. `null` if this message's or the chain's first time is not usable. |
| `level` | From the `headline` prefix: `possible` for "Flooding is possible" (flood alert) and `expected` for "Flooding is expected" (flood warning). Any other headline gives `unknown`, and a headline without `: ` is used whole as the location. |

Issue tags:

| Tag | Meaning |
| --- | --- |
| `no_cancel` | The latest message is not a `Cancel`, so the chain is `open`. |
| `update_after_cancel` | An `Update` follows a `Cancel` in the same chain: EA reopened the chain rather than issuing a new `Alert`. |
| `duplicate_cancel` | Two consecutive `Cancel` messages. |
| `level_change` | `level` differs between messages in the chain, for example a flood warning downgraded to an alert. |
| `location_change` | The `headline` location differs between messages. `location` then shows the alphabetically first one. |
| `reference_mismatch` | A message's `references` is not exactly the IDs of the earlier messages in its chain, in order. |
| `root_not_alert` | The chain's first message is not an `Alert`, for example because the `Alert` was never archived. |
| `invalid_sent` | At least one message's `sent` is `null`, so some elapsed times or the duration are `null`. |

Tags always appear in this order: `root_not_alert`, `reference_mismatch`, `invalid_sent`,
`update_after_cancel`, `duplicate_cancel`, `level_change`, `location_change`, `no_cancel`.

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python cap_poller.py                         # archive into ./warnings
python cap_poller.py --warnings-dir /tmp/w   # archive somewhere else
python cap_poller.py --feed-file /tmp/f.atom # keep the index copy somewhere else
python cap_poller.py -v                      # debug logging
python cap_poller.py --csv-file /tmp/alerts.csv # keep alert records somewhere else
python cap_poller.py --chains-file /tmp/c.yml # keep alert chains somewhere else
python cap_poller.py --backfill-csv           # rebuild alerts.csv and alert-chains.yml from all local warnings/*.xml
```

The offline backfill uses the same extractor and CSV writer as polling, makes no HTTP requests,
and replaces the CSV and the alert chains with records from the archive. Re-running it with unchanged
XML is a no-op.
Normal polling records successfully fetched messages even if their XML is unchanged, so a missing
CSV row can be recovered without re-archiving the message. Missing historical messages require a backfill.

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
feed" → "Run workflow"). New and updated messages, the feed index, `alerts.csv` and `alert-chains.yml`
are committed to `main` by `github-actions[bot]` using
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
  index while the message stayed retrievable. Following `references` recovers most, but not necessarily
  all, of them, so the archive is best-effort, not guaranteed complete.
- **Updates are detected only while a message remains in the feed.** Messages that have left the
  index are not periodically re-fetched unless a newly saved or changed message references them.
- **Absence from the feed does not mean a warning has been withdrawn.** Withdrawals are published as new
  messages (`msgType` `Cancel`, with `references`). Do not use this archive to decide what is currently in
  force without interpreting those fields.
- **Do not use this archive for safety-critical decisions.**

## Data licence

Warning data is © Environment Agency and licensed under
[Creative Commons BY 4.0](https://creativecommons.org/licenses/by/4.0/) (as stated in the feed's `rights`
element). Redistributing it, as this repository does, requires that attribution.
