"""Small, deliberately simple rules oracle; never used by the production entry."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

DAY = 86400000


def fields(line, count):
    parts = line.split(",")
    if len(parts) != count:
        return None
    result = []
    for part in parts:
        if part.startswith('"') and part.endswith('"') and len(part) >= 2:
            part = part[1:-1]
        if '"' in part:
            return None
        result.append(part)
    return result


def rows(data, name, count):
    paths = sorted((data / name).glob("*.csv")) if (data / name).is_dir() else [data / name]
    for path in paths:
        # bytes.split preserves CR as field content and does not invent a final
        # empty line when a file ends with its last newline.
        for line in path.read_bytes().split(b"\n")[1:]:
            row = fields(line.decode("utf-8"), count)
            if row is not None:
                yield row


def ts(value):
    if re.fullmatch("[0-9]+", value):
        return int(value)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|[+-][0-9]{2}:[0-9]{2})", value):
        return None
    # Python 3.15 accepts ISO end-of-day 24:00:00; this track explicitly forbids it.
    if int(value[11:13]) > 23 or int(value[14:16]) > 59 or int(value[17:19]) > 59:
        return None
    if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        return None
    try:
        return int((datetime.fromisoformat(value) - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()) * 1000
    except ValueError:
        return None


def solve(data):
    data = Path(data)
    manifest = json.loads((data / "manifest.json").read_text())
    start, end = ts(manifest["window_start"]), ts(manifest["window_end"])
    parents = {}

    def root(a):
        parents.setdefault(a, a)
        if parents[a] != a:
            parents[a] = root(parents[a])
        return parents[a]

    for a, b in rows(data, "links", 2):
        if a and b:
            x, y = root(a), root(b)
            parents[max(x, y)] = min(x, y)
    products = dict(rows(data, "products.csv", 2))
    campaigns = dict(rows(data, "campaigns.csv", 2))
    winners = {}
    for row in rows(data, "events", 7):
        if row[0] and (row[0] not in winners or row[3] < winners[row[0]][3]):
            winners[row[0]] = row
    events = defaultdict(list)
    for _, user, value, ingest, kind, product, price in winners.values():
        when = ts(value)
        if not user or not product or kind not in ("view", "cart", "purchase") or when is None or not start <= when < end:
            continue
        if ts(ingest) - when > DAY:
            continue
        revenue = 0
        if kind == "purchase":
            if not re.fullmatch("[0-9]+", price) or not 1 <= int(price) <= 10000000:
                continue
            revenue = int(price)
        events[root(user)].append((when, revenue, products.get(product, "unknown")))
    clicks = defaultdict(list)
    for click, user, campaign, value in rows(data, "clicks", 4):
        when = ts(value)
        if click and user and campaign in campaigns and when is not None and start-7*DAY <= when < end:
            clicks[root(user)].append((when, click, campaign))
    user_days = {}
    camp_days = defaultdict(lambda: [0, 0, 0, 0])
    for user, values in events.items():
        values.sort()
        sessions = []
        for event in values:
            if not sessions or event[0] - sessions[-1][-1][0] > 1800000:
                sessions.append([])
            sessions[-1].append(event)
        for session in sessions:
            start_ts = session[0][0]
            day = (datetime(1970, 1, 1) + timedelta(days=start_ts//DAY)).date()
            info = user_days.setdefault((user, day), [0, 0, 0, defaultdict(int)])
            info[0] += 1
            info[1] += len(session)
            revenue = sum(event[1] for event in session)
            info[2] += revenue
            for _, amount, cat in session:
                if amount:
                    info[3][cat] += amount
            eligible = [c for c in clicks[user] if start_ts-7*DAY <= c[0] <= start_ts]
            credited = min(eligible, key=lambda c: (-c[0], c[1]))[2] if eligible else "organic"
            stats = camp_days[credited, day]
            stats[0] += 1
            stats[1] += revenue > 0
            stats[2] += revenue
            for campaign in {c[2] for c in eligible}:
                camp_days[campaign, day][3] += 1
    user_rows = []
    for (user, day), (sessions, count, revenue, cats) in user_days.items():
        top = min(cats, key=lambda c: (-cats[c], c)) if cats else None
        user_rows.append((user, day, sessions, count, revenue, top))
    campaign_rows = [(c, campaigns.get(c, "organic"), day, *values) for (c, day), values in camp_days.items()]
    return {"user_daily": user_rows, "campaign_daily": campaign_rows}
