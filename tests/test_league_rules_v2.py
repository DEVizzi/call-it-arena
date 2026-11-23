"""v2 carry-over points elimination (rule-change spec acceptance criteria). Pure Python: runs under unittest and pytest."""
import copy
import unittest

from common import league_rules as LR


def _cfg(**kw):
    c = dict(LR.DEFAULT_CONFIG)
    c.update(kw)
    return c


def _entry(race, pts, order=None, elim=None, rules=None):
    order = order or sorted(pts, key=lambda c: -pts[c])
    e = {"season": 1, "race": race, "finish_order": order, "points_by_team": dict(pts), "winner": order[0],
         "podium": order[:3]}
    if elim:
        e["eliminated_all"] = list(elim)
        e["eliminated"] = elim[0]
    if rules:
        e["rules_version"] = rules
    return e


class WorkedExample(unittest.TestCase):
    """Spec section 4: league A30 B22 C18 D10; game 16 A2 B8 C9 D6; game 17 A5 B3 C7."""

    def setUp(self):
        self.codes = ["A", "B", "C", "D"]
        self.cfg = _cfg(league_games=1, total_games=10, teams_entering_semifinals=1)
        self.hist = [_entry(1, {"A": 30, "B": 22, "C": 18, "D": 10})]

    def test_lowest_cumulative_not_last_in_game(self):
        g16 = _entry(2, {"A": 2, "B": 8, "C": 9, "D": 6})
        teams = LR.replay_standings(self.hist + [g16], self.codes, 1, self.cfg)["teams"]
        self.assertEqual([teams[c]["total_points"] for c in "ABCD"], [32, 30, 27, 16])
        cand = LR.get_elimination_candidate(teams, self.cfg)
        self.assertEqual(cand["code"], "D")                     # v2: lowest total
        self.assertEqual(cand["margin"], 11)
        out, _, _ = LR._eliminations_for(LR.STAGE_ELIM, teams, g16["finish_order"], "", 2, self.cfg, LR.RULES_V1)
        self.assertEqual(out, ["A"])                            # v1 would have knocked out the leader

    def test_game_17_and_last_scorer_stays_in(self):
        h = self.hist + [_entry(2, {"A": 2, "B": 8, "C": 9, "D": 6}, elim=["D"])]
        g17 = _entry(3, {"A": 5, "B": 3, "C": 7})
        teams = LR.replay_standings(h + [g17], self.codes, 1, self.cfg)["teams"]
        self.assertEqual([teams[c]["total_points"] for c in "ABC"], [37, 33, 34])
        self.assertEqual(LR.get_elimination_candidate(teams, self.cfg)["code"], "B")

    def test_last_in_game_but_not_lowest_total_survives(self):
        g16 = _entry(2, {"A": 0, "B": 8, "C": 9, "D": 6}, order=["C", "B", "D", "A"])
        teams = LR.replay_standings(self.hist + [g16], self.codes, 1, self.cfg)["teams"]
        self.assertNotEqual(LR.get_elimination_candidate(teams, self.cfg)["code"], "A")

    def test_tiebreak_last_game_then_league_points(self):
        cfg = self.cfg
        teams = {
            "B": {"code": "B", "total_points": 33, "last_game_points": 3, "league_points": 22, "game_wins": 0,
                  "status": "active"},
            "C": {"code": "C", "total_points": 33, "last_game_points": 7, "league_points": 18, "game_wins": 0,
                  "status": "active"},
            "A": {"code": "A", "total_points": 40, "last_game_points": 1, "league_points": 30, "game_wins": 0,
                  "status": "active"},
        }
        cand = LR.get_elimination_candidate(teams, cfg)
        self.assertEqual((cand["code"], cand["tiebreak_used"]), ("B", "last_game_points"))
        teams["B"]["last_game_points"] = 7
        cand = LR.get_elimination_candidate(teams, cfg)
        self.assertEqual((cand["code"], cand["tiebreak_used"]), ("C", "league_points"))
        rev = _cfg(**dict(cfg, tiebreaker_order=["league_points", "last_game_points", "game_wins", "host_decision"]))
        self.assertEqual(LR.get_elimination_candidate(teams, rev)["tiebreak_used"], "league_points")
        teams["C"]["league_points"] = 22
        self.assertEqual(LR.get_elimination_candidate(teams, cfg)["tiebreak_used"], "host_decision:coin_flip")


class SeasonFlow(unittest.TestCase):
    def setUp(self):
        self.codes = [f"T{i:02d}" for i in range(16)]
        self.state = {"season": 1, "race_number": 0, "max_races_per_season": 28, "history": [],
                      "countries": {c: {"code": c, "points": 0, "wins": 0, "podiums": 0, "eliminated": False}
                                    for c in self.codes}}

    def _play(self, race):
        cfg = LR.load_config(self.state)
        active = [c for c in self.codes if not self.state["countries"][c]["eliminated"]]
        stage = LR.stage_for_race(race, cfg, len(active))
        order = active[race % len(active):] + active[: race % len(active)]
        rec = LR.record_game(self.state, race, stage, order, self.codes, seed=f"s{race}", cfg=cfg)
        e = _entry(race, rec["points_by_team"], order=order, elim=rec["eliminated_all"], rules=rec["rules_version"])
        e["stage"] = stage
        self.state["history"].append(e)
        LR.sync_countries(self.state, self.codes)
        return stage, rec

    def test_full_season(self):
        for r in range(1, 15):
            stage, rec = self._play(r)
            self.assertEqual(stage, LR.STAGE_LEAGUE)
            self.assertEqual(rec["eliminated_all"], [])
        teams = LR.replay_standings(self.state["history"], self.codes, 1)["teams"]
        for t in teams.values():                                # AC1: total == league points after the league
            self.assertEqual(t["total_points"], t["league_points"])
        for r in range(15, 27):
            before = LR.replay_standings(self.state["history"], self.codes, 1)["teams"]
            stage, rec = self._play(r)
            self.assertEqual(stage, LR.STAGE_ELIM)
            self.assertEqual(len(rec["eliminated_all"]), 1)    # AC5
            after = LR.replay_standings(self.state["history"], self.codes, 1)["teams"]
            out = rec["eliminated_all"][0]
            alive_totals = [after[c]["total_points"] for c, t in before.items() if t["status"] == "active"]
            self.assertEqual(after[out]["total_points"], min(alive_totals))   # AC2
            self.assertGreaterEqual(after[out]["total_points"], before[out]["total_points"])   # AC4 never reset
        stage, rec = self._play(27)
        self.assertEqual((stage, len(rec["eliminated_all"])), (LR.STAGE_SEMI, 2))
        stage, rec = self._play(28)
        self.assertEqual((stage, len(rec["eliminated_all"])), (LR.STAGE_FINAL, 1))
        alive = [c for c in self.codes if not self.state["countries"][c]["eliminated"]]
        self.assertEqual(len(alive), 1)

    def test_carry_over_false_is_v1(self):
        self.state["rules_config"] = {"carry_over_points": False}
        for r in range(1, 16):
            stage, rec = self._play(r)
        self.assertEqual(rec["rules_version"], LR.RULES_V1)
        self.assertEqual(rec["eliminated_all"], [self.state["history"][-1]["finish_order"][-1]])   # AC9

    def test_invalid_results_rejected(self):
        with self.assertRaises(LR.InvalidGameResult):
            LR.record_game(self.state, 1, LR.STAGE_LEAGUE, ["T00", "T00"], self.codes)
        with self.assertRaises(LR.InvalidGameResult):
            LR.record_game(self.state, 1, LR.STAGE_LEAGUE, ["XX"], self.codes)

    def test_published_v1_kept_and_what_if_read_only(self):
        for r in range(1, 15):
            self._play(r)
        last = self.state["history"][-1]["finish_order"][-1]
        e = _entry(15, LR.points_for_order(self.state["history"][-1]["finish_order"]),
                   order=self.state["history"][-1]["finish_order"], elim=[last], rules=LR.RULES_V1)
        self.state["history"].append(e)
        snap = copy.deepcopy(self.state)
        rep = LR.what_if_report(self.state["history"], self.codes, 1)
        self.assertIn("Race 15", rep)
        self.assertEqual(snap, self.state)                      # AC7: report never mutates stored results
        teams = LR.replay_standings(self.state["history"], self.codes, 1)["teams"]
        self.assertEqual(teams[last]["status"], "eliminated")

    def test_edit_past_game_flags_changes(self):
        for r in range(1, 17):
            self._play(r)
        first = self.state["history"][14]["eliminated_all"][0]
        order = list(self.state["history"][14]["finish_order"])
        order.remove(first)
        order.insert(0, first)                                  # give the eliminated team the race win
        new, warnings = LR.edit_past_game(self.state, 1, 15, order, self.codes)
        self.assertNotEqual(new["history"][14]["eliminated_all"][0], first)   # AC8
        self.assertTrue(warnings)


if __name__ == "__main__":
    unittest.main()
