# Naval Action Market

Private analytics pipeline for Naval Action MAIN / Caribbean market snapshots.

## Status

The repository is initialized for a provider-based collector. The preferred production source is an authorized direct NAAPI feed. A NavalGaming HTML adapter is included only as a fallback and should not be scheduled until permission to automate collection has been confirmed.

Known daily server cycle in `Europe/Bucharest`:

- 12:55: pre-shutdown snapshot target
- 12:59: server shuts down
- 13:30: server restarts
- 13:35: first post-restart snapshot target
- 13:40, 13:45, ...: retry window if the post-restart market is incomplete

## Architecture

```text
NAAPI (preferred) -----------\
                              -> normalized snapshot -> history/latest -> Google Sheets / route engine
NavalGaming HTML (fallback) -/
```

The normalized snapshot schema is `NG-MAIN-SNAPSHOT-2` and separates:

- ports
- player market contracts
- resources added
- server-controlled trade goods
- diagnostics and validation

## Quick start

```bash
python -m pip install -r requirements.txt
python -m naval_action_market.cli --provider html --output data/latest.json
```

The HTML collector currently requires explicit `--allow-html` acknowledgement because scheduled scraping should not be enabled without authorization.

```bash
python -m naval_action_market.cli --provider html --allow-html --output data/latest.json
```

## GitHub Actions

`.github/workflows/collect.yml` is intentionally manual-only at initialization. Once direct API access or permission for automated collection is confirmed, the scheduled trigger can be enabled without changing the downstream snapshot format.

## Google Sheet

Target spreadsheet:

`1DWUaU_Q9NCL3dkFUpuUqteWAVl8OO-XrciPSdEt1hKE`

The sheet already contains `Automation`, `Snapshot Log`, `Live Ports`, `Live Market`, `Live Resources`, and `Live Trade Goods` tabs prepared for ingestion.
