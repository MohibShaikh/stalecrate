"""Training cutoffs for coding models, and finding which model the project uses.

Only cutoffs that the model vendor states are listed. For anything else pass
--cutoff YYYY-MM explicitly.
"""
import calendar
import datetime as dt
import json
import pathlib

# model id prefix -> (year, month) of the stated training-data cutoff
KNOWN = {
    "claude-opus-5-5": (2026, 6),
}


def parse_cutoff(text):
    y, m = (int(x) for x in text.split("-")[:2])
    return y, m


def known_until(cutoff, margin_days):
    """Last release date the model plausibly learned: the end of the cutoff month
    minus a margin, since releases from the final weeks are thinly covered."""
    y, m = cutoff
    end = dt.date(y, m, calendar.monthrange(y, m)[1])
    return end - dt.timedelta(days=margin_days)


def detect_model(project):
    """The model a Claude Code project is set up with, from its settings files."""
    root = pathlib.Path(project)
    for f in [root / ".claude" / "settings.local.json", root / ".claude" / "settings.json",
              pathlib.Path.home() / ".claude" / "settings.json"]:
        try:
            model = json.loads(f.read_text()).get("model")
        except (OSError, ValueError):
            continue
        if model:
            return model
    return None


def cutoff_for(model):
    for prefix, c in KNOWN.items():
        if model and model.startswith(prefix):
            return c
    return None
