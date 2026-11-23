"""Read-only what-if report: replays the Marble League log under another rules version (stored results never change).

    .venv/bin/python scripts/what_if_report.py                 # current season under v2_cumulative
    .venv/bin/python scripts/what_if_report.py --rules v1_single_game_last_place --season 1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common import league_rules  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", default=league_rules.RULES_V2,
                    choices=[league_rules.RULES_V1, league_rules.RULES_V2])
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--state", default=str(ROOT / "state" / "league.json"))
    a = ap.parse_args()
    state = json.loads(Path(a.state).read_text(encoding="utf-8"))
    season = a.season or int(state.get("season", 1))
    codes = list(state.get("countries", {}))
    print(league_rules.what_if_report(state.get("history", []), codes, season, a.rules, league_rules.load_config(state)))


if __name__ == "__main__":
    main()
