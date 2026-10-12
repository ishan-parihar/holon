"""Minimal 5-field cron (minute hour day-of-month month day-of-week), UTC.

Supports *, lists (a,b), ranges (a-b), steps (*/n, a-b/n). Day-of-month and day-of-week
follow the classic rule: if both are restricted, either may match.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]  # day-of-week 7 = Sunday, as 0


class CronError(ValueError):
    pass


def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step < 1:
                raise CronError(f"bad step in '{spec}'")
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
        if a < lo or b > hi or a > b:
            raise CronError(f"'{spec}' out of range {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


class Cron:
    def __init__(self, expr: str):
        parts = expr.split()
        if len(parts) != 5:
            raise CronError(f"cron needs 5 fields, got {len(parts)}: '{expr}'")
        try:
            self.sets = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, _RANGES, strict=True)]
        except ValueError as e:
            raise CronError(str(e)) from None
        self.sets[4] = {0 if d == 7 else d for d in self.sets[4]}
        self.dom_any = parts[2] == "*"
        self.dow_any = parts[4] == "*"
        self.expr = expr

    def _day_ok(self, dt: datetime) -> bool:
        dom = dt.day in self.sets[2]
        dow = (dt.isoweekday() % 7) in self.sets[4]
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow
        if self.dow_any:
            return dom
        return dom or dow

    def next_after(self, dt: datetime) -> datetime:
        t = dt.astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = t + timedelta(days=366 * 5)
        while t < limit:
            if t.month not in self.sets[3] or not self._day_ok(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if t.hour not in self.sets[1]:
                t = (t + timedelta(hours=1)).replace(minute=0)
                continue
            if t.minute not in self.sets[0]:
                t += timedelta(minutes=1)
                continue
            return t
        raise CronError(f"'{self.expr}' never fires")
