# YNAB Balance Monitor

Projects the minimum balance of a checking account over the next N days based on scheduled transactions and credit card payment obligations from [YNAB](https://www.ynab.com/). Sends an [ntfy](https://ntfy.sh) alert if the balance is projected to drop below a threshold.

Useful for keeping most of your cash in a high-yield savings account while making sure your checking account stays funded.

## How it works

1. Fetches the current balance of your checking account
2. Fetches all scheduled transactions for that account within the monitoring window
3. Fetches credit card payment category balances (money earmarked to pay CC bills)
4. Deduplicates — scheduled transfers to CC accounts aren't double-counted
5. Walks day-by-day to find the **minimum projected balance**
6. If it drops below your threshold, sends an ntfy notification

## Setup

### 1. Get your YNAB credentials

- Go to [YNAB Account Settings → Developer Settings](https://app.ynab.com/settings/developer)
- Create a Personal Access Token
- Find your account ID: visit `https://api.ynab.com/v1/budgets/last-used/accounts?access_token=YOUR_TOKEN` and locate your checking account's `id`

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your values
```

### 3. Run

**With Docker:**
```bash
docker compose run --rm monitor
```

**Without Docker:**
```bash
pip install requests  # not actually needed — uses stdlib only
python monitor.py
```

**On a cron schedule (recommended):**
```cron
# Check every morning at 8am
0 8 * * * cd /path/to/YNAB-Balance-Monitor && docker compose run --rm monitor
```

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `YNAB_API_TOKEN` | Yes | — | YNAB Personal Access Token |
| `YNAB_ACCOUNT_ID` | Yes | — | ID of the checking account to monitor |
| `YNAB_BUDGET_ID` | No | `last-used` | Budget ID (or `last-used`) |
| `YNAB_CC_CATEGORIES` | No | all | Comma-separated category IDs or names to monitor |
| `MONITOR_DAYS` | No | `30` | Number of days to project forward |
| `MIN_BALANCE` | No | `0` | Alert threshold in dollars |
| `NTFY_TOPIC` | Yes | — | ntfy topic name |
| `NTFY_URL` | No | `https://ntfy.sh` | ntfy server URL |

## Example output

```
============================================================
YNAB Balance Monitor — 2026-02-07 08:00
Monitoring next 30 days, threshold: $500.00
============================================================
Account: Primary Checking
Current balance: $2,450.00

Scheduled transactions in next 30 days: 3
  2026-02-10  Rent                            $ -1,500.00
  2026-02-14  Paycheck                        $  3,200.00
  2026-02-28  Car Payment                     $   -450.00

Credit card payments to account for: $1,200.00
  Chase Sapphire                              $    800.00
  Amex Gold                                   $    400.00

Unscheduled CC payments (applied today): $1,200.00

Projected minimum balance: $-250.00 on 2026-02-10

⚠ ALERT: Projected balance drops $750.00 below threshold!
Notification sent to my-balance-alerts
```
