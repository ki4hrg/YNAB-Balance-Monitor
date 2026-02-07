#!/usr/bin/env python3
"""YNAB Balance Monitor - Projects minimum checking account balance and alerts via ntfy."""

import calendar
import os
import sys
import json
from datetime import datetime, timedelta, date
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

YNAB_API_TOKEN = os.environ.get("YNAB_API_TOKEN", "")
YNAB_BUDGET_ID = os.environ.get("YNAB_BUDGET_ID", "last-used")
YNAB_ACCOUNT_ID = os.environ.get("YNAB_ACCOUNT_ID", "")
YNAB_CC_CATEGORIES = os.environ.get("YNAB_CC_CATEGORIES", "")  # comma-separated IDs, empty = all
MONITOR_DAYS = os.environ.get("MONITOR_DAYS", "")  # empty = end of current month
MIN_BALANCE = int(os.environ.get("MIN_BALANCE", "0"))  # in dollars
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

YNAB_BASE = "https://api.ynab.com/v1"

# ---------------------------------------------------------------------------
# YNAB API helpers
# ---------------------------------------------------------------------------

def ynab_get(path):
    """Make an authenticated GET request to the YNAB API."""
    url = f"{YNAB_BASE}{path}"
    req = Request(url, headers={"Authorization": f"Bearer {YNAB_API_TOKEN}"})
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())["data"]
    except HTTPError as e:
        body = e.read().decode()
        print(f"YNAB API error ({e.code}): {body}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"Network error: {e.reason}", file=sys.stderr)
        sys.exit(1)


def milliunits_to_dollars(milliunits):
    """YNAB stores amounts in milliunits (1 dollar = 1000 milliunits)."""
    return milliunits / 1000.0


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def get_end_date():
    """Compute the projection end date.

    If MONITOR_DAYS is set, project that many days forward.
    Otherwise, project through the end of the current month.
    """
    today = datetime.now().date()
    if MONITOR_DAYS:
        return today + timedelta(days=int(MONITOR_DAYS))
    last_day = calendar.monthrange(today.year, today.month)[1]
    return date(today.year, today.month, last_day)


def get_account_balance():
    """Get the current balance of the monitored account."""
    data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/accounts/{YNAB_ACCOUNT_ID}")
    account = data["account"]
    balance = milliunits_to_dollars(account["balance"])
    print(f"Account: {account['name']}")
    print(f"Current balance: ${balance:,.2f}")
    return balance


def _add_months(d, months):
    """Add months to a date, clamping to the last day of the target month."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _expand_occurrences(next_date, frequency, start, end):
    """Generate all occurrence dates of a recurring transaction within [start, end].

    next_date:  the next scheduled occurrence (from the API)
    frequency:  YNAB frequency string
    start/end:  the monitoring window bounds
    """
    # Map YNAB frequency to a delta-generating function.
    # Each function returns the next date given the current one.
    DELTAS = {
        "daily":          lambda d: d + timedelta(days=1),
        "weekly":         lambda d: d + timedelta(weeks=1),
        "everyOtherWeek": lambda d: d + timedelta(weeks=2),
        "every4Weeks":    lambda d: d + timedelta(weeks=4),
        "monthly":        lambda d: _add_months(d, 1),
        "everyOtherMonth":lambda d: _add_months(d, 2),
        "every3Months":   lambda d: _add_months(d, 3),
        "every4Months":   lambda d: _add_months(d, 4),
        "twiceAMonth":    None,  # special case
        "twiceAYear":     lambda d: _add_months(d, 6),
        "yearly":         lambda d: _add_months(d, 12),
        "everyOtherYear": lambda d: _add_months(d, 24),
    }

    if frequency == "never" or frequency not in DELTAS:
        # One-time transaction — just return it if in range
        if start <= next_date <= end:
            return [next_date]
        return []

    # Special handling for twiceAMonth: YNAB schedules on the 1st & 15th
    # (or the original day and that day + ~15). We approximate by using
    # the next_date's day-of-month and that day ± 15.
    if frequency == "twiceAMonth":
        dates = []
        # Generate monthly anchors, then add both the "first" and "second" hit
        day1 = next_date.day
        day2 = day1 + 15 if day1 <= 15 else day1 - 15
        d = next_date.replace(day=1)  # start of month
        # Back up one month to make sure we don't miss anything
        d = _add_months(d, -1)
        month_end = end
        while d <= month_end:
            last_day = calendar.monthrange(d.year, d.month)[1]
            for target_day in (day1, day2):
                clamped = min(target_day, last_day)
                candidate = date(d.year, d.month, clamped)
                if start <= candidate <= end:
                    dates.append(candidate)
            d = _add_months(d, 1)
        return sorted(set(dates))

    # General case: walk forward from next_date using the delta function
    advance = DELTAS[frequency]
    dates = []
    d = next_date
    while d <= end:
        if d >= start:
            dates.append(d)
        d = advance(d)
    return dates


def get_scheduled_transactions(end_date):
    """Get all scheduled transactions for the monitored account.

    Expands recurring transactions into individual occurrences within the
    monitoring window.
    """
    data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/scheduled_transactions")
    today = datetime.now().date()

    transactions = []
    for txn in data["scheduled_transactions"]:
        if txn["account_id"] != YNAB_ACCOUNT_ID:
            continue
        if txn.get("deleted", False):
            continue

        next_date = datetime.strptime(txn["date"], "%Y-%m-%d").date()
        frequency = txn.get("frequency", "never")
        amount = milliunits_to_dollars(txn["amount"])
        payee = txn.get("payee_name", "Unknown")
        transfer_account_id = txn.get("transfer_account_id")

        occurrences = _expand_occurrences(next_date, frequency, today, end_date)
        for occ_date in occurrences:
            freq_label = f" ({frequency})" if frequency != "never" else ""
            transactions.append({
                "date": occ_date,
                "amount": amount,
                "payee": payee,
                "transfer_account_id": transfer_account_id,
                "frequency": frequency,
                "label": f"{payee}{freq_label}",
            })

    transactions.sort(key=lambda t: t["date"])
    print(f"\nScheduled transactions through {end_date}: {len(transactions)}")
    for t in transactions:
        print(f"  {t['date']}  {t['label']:40s}  ${t['amount']:>10,.2f}")
    return transactions


def get_cc_payment_amounts():
    """Get credit card payment category available balances.

    Returns a dict of {account_id: available_amount} for credit card accounts,
    and the total amount to be paid.
    """
    # Get all accounts to identify credit card accounts and map category names
    accounts_data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/accounts")
    cc_accounts = {}
    for acct in accounts_data["accounts"]:
        if acct["type"] == "creditCard" and not acct.get("deleted", False) and not acct.get("closed", False):
            cc_accounts[acct["name"]] = acct["id"]

    # Get categories and find the Credit Card Payments group
    categories_data = ynab_get(f"/budgets/{YNAB_BUDGET_ID}/categories")

    # Parse user-specified CC categories filter
    cc_filter = set()
    if YNAB_CC_CATEGORIES:
        cc_filter = {c.strip() for c in YNAB_CC_CATEGORIES.split(",")}

    cc_payments = {}
    for group in categories_data["category_groups"]:
        if group["name"] != "Credit Card Payments":
            continue
        for cat in group["categories"]:
            if cat.get("deleted", False) or cat.get("hidden", False):
                continue
            # If user specified specific categories, filter
            if cc_filter and cat["id"] not in cc_filter and cat["name"] not in cc_filter:
                continue

            available = milliunits_to_dollars(cat["balance"])
            # Map category name back to account ID
            account_id = cc_accounts.get(cat["name"])
            if account_id and available > 0:
                cc_payments[account_id] = {
                    "name": cat["name"],
                    "amount": available,
                }

    total = sum(p["amount"] for p in cc_payments.values())
    print(f"\nCredit card payments to account for: ${total:,.2f}")
    for p in cc_payments.values():
        print(f"  {p['name']:30s}  ${p['amount']:>10,.2f}")
    return cc_payments, total


def project_minimum_balance(current_balance, scheduled_transactions, cc_payments, end_date):
    """Walk day-by-day to find the minimum projected balance.

    CC payments that are already in scheduled_transactions (as transfers to CC
    accounts) are not double-counted. Any remaining CC payment amounts are
    applied on day 1 (conservative: assumes they could hit at any time).
    """
    today = datetime.now().date()

    # Identify which CC payments are already covered by scheduled transfers
    remaining_cc = dict(cc_payments)  # shallow copy of outer dict
    for txn in scheduled_transactions:
        transfer_id = txn["transfer_account_id"]
        if transfer_id and transfer_id in remaining_cc:
            # This scheduled transaction already covers (part of) the CC payment
            covered = min(remaining_cc[transfer_id]["amount"], abs(txn["amount"]))
            remaining_cc[transfer_id] = {
                **remaining_cc[transfer_id],
                "amount": remaining_cc[transfer_id]["amount"] - covered,
            }
            if remaining_cc[transfer_id]["amount"] <= 0.005:
                del remaining_cc[transfer_id]

    # Unscheduled CC payment total — apply on day 1
    unscheduled_cc_total = sum(p["amount"] for p in remaining_cc.values())
    if unscheduled_cc_total > 0:
        print(f"\nUnscheduled CC payments (applied today): ${unscheduled_cc_total:,.2f}")

    # Build day-by-day projection
    balance = current_balance - unscheduled_cc_total
    min_balance = balance
    min_date = today

    # Group scheduled transactions by date
    txn_by_date = {}
    for txn in scheduled_transactions:
        txn_by_date.setdefault(txn["date"], []).append(txn)

    day = today
    while day <= end_date:
        if day in txn_by_date:
            for txn in txn_by_date[day]:
                balance += txn["amount"]
        if balance < min_balance:
            min_balance = balance
            min_date = day
        day += timedelta(days=1)

    print(f"\nProjected minimum balance: ${min_balance:,.2f} on {min_date}")
    return min_balance, min_date


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def send_notification(min_balance, min_date):
    """Send an alert via ntfy."""
    title = "YNAB Balance Alert"
    message = (
        f"Your checking account balance is projected to drop to "
        f"${min_balance:,.2f} by {min_date.strftime('%b %d, %Y')}. "
        f"Minimum threshold: ${MIN_BALANCE:,.2f}."
    )

    url = f"{NTFY_URL.rstrip('/')}/{NTFY_TOPIC}"
    data = message.encode("utf-8")
    req = Request(url, data=data, method="POST", headers={
        "Title": title,
        "Priority": "high" if min_balance < 0 else "default",
        "Tags": "warning,dollar",
    })

    try:
        with urlopen(req) as resp:
            print(f"\nNotification sent to {NTFY_TOPIC}")
    except (HTTPError, URLError) as e:
        print(f"Failed to send notification: {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def validate_config():
    """Check required configuration is present."""
    errors = []
    if not YNAB_API_TOKEN:
        errors.append("YNAB_API_TOKEN is required")
    if not YNAB_ACCOUNT_ID:
        errors.append("YNAB_ACCOUNT_ID is required")
    if not NTFY_TOPIC:
        errors.append("NTFY_TOPIC is required")
    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    validate_config()

    end_date = get_end_date()

    print("=" * 60)
    print(f"YNAB Balance Monitor — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Projecting through {end_date}, threshold: ${MIN_BALANCE:,.2f}")
    print("=" * 60)

    balance = get_account_balance()
    transactions = get_scheduled_transactions(end_date)
    cc_payments, cc_total = get_cc_payment_amounts()
    min_balance, min_date = project_minimum_balance(balance, transactions, cc_payments, end_date)

    if min_balance < MIN_BALANCE:
        shortfall = MIN_BALANCE - min_balance
        print(f"\n⚠ ALERT: Projected balance drops ${shortfall:,.2f} below threshold!")
        send_notification(min_balance, min_date)
    else:
        print(f"\n✓ Balance stays above ${MIN_BALANCE:,.2f} threshold.")


if __name__ == "__main__":
    main()
