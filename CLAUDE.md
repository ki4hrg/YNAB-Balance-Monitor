# YNAB Balance Monitor

## Project overview

A lightweight Python tool that projects the minimum balance of a checking account through the end of the current month using YNAB data, and sends ntfy alerts if the balance is projected to drop below a threshold. Designed for users who keep most cash in an HYSA and need early warning to transfer funds to checking.

## Architecture

- **Single file**: `monitor.py` — all logic in one stdlib-only Python script (no pip dependencies)
- **Docker**: `Dockerfile` + `docker-compose.yml` — runs as a long-lived service with built-in scheduling
- **Config**: Environment variables via `stack.env` (Portainer) or `.env`

## Key concepts

- **YNAB API** (`https://api.ynab.com/v1`): Amounts are in milliunits (1 dollar = 1000). Scheduled transactions use `date_next`/`date_first` fields (not `date`). Rate limit: 200 requests/hour.
- **Recurrence expansion**: YNAB only returns the next occurrence of scheduled transactions. `_expand_occurrences()` generates all occurrences within the monitoring window for all 13 YNAB frequency types.
- **CC payment deduplication**: Credit card payment category balances represent money earmarked to leave checking. Scheduled transfers to CC accounts are identified and subtracted to avoid double-counting. Remaining unscheduled CC payments are applied on day 1 (conservative).
- **Projection**: Day-by-day balance walk to find the minimum point, not just end-of-period balance.

## Development notes

- Uses only Python stdlib (`urllib`, `json`, `calendar`) — no external dependencies
- `python -u` flag in Dockerfile for unbuffered output (required for Docker log visibility)
- `stack.env` in docker-compose.yml for Portainer compatibility
- `SCHEDULE` env var supports `HH:MM` (daily) or `Nh` (interval) formats
