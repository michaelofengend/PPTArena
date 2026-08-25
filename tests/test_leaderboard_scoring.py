import ast
import csv
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "src" / "app.py"
DUMATE_RESULTS = ROOT / "agent_bench" / "results" / "baidu_dumate_pptx_skill_r266_judge_results.csv"
SUBSET_PATH = ROOT / "agent_bench" / "subset25.json"


def load_scoring_functions():
    """Execute the production scoring helpers without importing the Flask app."""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if "_SCORE_K" in names:
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {"_metric_pct", "_case_pct"}:
            selected.append(node)

    namespace = {}
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, str(APP_PATH), "exec"), namespace)
    return namespace["_metric_pct"], namespace["_case_pct"]


def load_leaderboard_functions():
    """Execute the production leaderboard builder with lightweight test globals."""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
    selected_names = {
        "_metric_pct",
        "_case_pct",
        "_build_leaderboard_entry",
        "get_leaderboard_data",
    }
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in selected_names
    ]
    namespace = {
        "json": json,
        "SCRIPT_DIR": ROOT / "src",
        "_EDIT_TIMES": {},
        "_edit_time_key": lambda source: None,
        "_fmt_edit_time": lambda seconds: None,
        "LEADERBOARD_STATIC_ENTRIES": [],
        "LEADERBOARD_CATEGORY_SPLITS": [],
        "_build_static_entry": lambda entry: None,
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, str(APP_PATH), "exec"), namespace)
    return namespace


class LeaderboardScoringTests(unittest.TestCase):
    def test_metric_percentage_is_linear_fraction_of_available_points(self):
        metric_pct, _ = load_scoring_functions()

        for raw_score, expected_pct in {
            0: 0,
            1: 20,
            2: 40,
            3: 60,
            4: 80,
            5: 100,
        }.items():
            with self.subTest(raw_score=raw_score):
                self.assertAlmostEqual(metric_pct(raw_score), expected_pct)

    def test_case_percentage_equal_weights_instruction_and_visual_scores(self):
        _, case_pct = load_scoring_functions()

        self.assertAlmostEqual(case_pct(2, 4), 60.0)

    def test_missing_hard_subset_case_counts_as_zero(self):
        namespace = load_leaderboard_functions()
        subset_names = json.loads(SUBSET_PATH.read_text(encoding="utf-8"))
        all_names = subset_names + [f"Extra Case {index}" for index in range(75)]
        scored_names = subset_names[:-1]

        source = {
            "name": "Missing-one system",
            "model": "Test model",
            "provider": "Test provider",
            "judge": "Test judge",
            "split": "subset",
        }
        scored = [
            {"case_name": name, "if_score": 5.0, "vq_score": 5.0}
            for name in scored_names
        ]
        namespace.update({
            "LEADERBOARD_SOURCES": [source],
            "_load_case_metadata": lambda: ({name: {} for name in all_names}, {}),
            "_collect_source_scores": lambda selected_source: (scored, scored_names),
        })

        result = namespace["get_leaderboard_data"]()
        hard_group = next(group for group in result["groups"] if group["base"] == "subset")
        entry = hard_group["views"][0]["entries"][0]

        self.assertEqual(entry["expected_cases"], 25)
        self.assertEqual(entry["scored_cases"], 24)
        self.assertEqual(entry["coverage_pct"], 96.0)
        self.assertEqual(entry["score_pct"], 96.0)

    def test_dumate_results_are_complete_sanitized_and_reproduce_published_scores(self):
        self.assertTrue(DUMATE_RESULTS.exists(), f"missing {DUMATE_RESULTS}")

        with DUMATE_RESULTS.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 100)
        self.assertEqual(len({row["case_name"] for row in rows}), 100)
        self.assertTrue(all(not Path(row["prediction"]).is_absolute() for row in rows))
        self.assertEqual({row["judge_model"] for row in rows}, {"kimi-k2.6 (median of 3)"})
        self.assertTrue(all(not row["errors"].strip() for row in rows))

        def linear_scores(selected_rows):
            count = len(selected_rows)
            instruction = sum(float(row["instruction_following_score"]) for row in selected_rows)
            visual = sum(float(row["visual_quality_score"]) for row in selected_rows)
            if_pct = instruction / (5 * count) * 100
            vq_pct = visual / (5 * count) * 100
            return if_pct, vq_pct, (if_pct + vq_pct) / 2

        full_scores = linear_scores(rows)
        self.assertEqual(tuple(round(value, 1) for value in full_scores), (53.4, 62.8, 58.1))

        subset_names = set(json.loads(SUBSET_PATH.read_text(encoding="utf-8")))
        subset_rows = [row for row in rows if row["case_name"] in subset_names]
        self.assertEqual(len(subset_rows), 25)
        subset_scores = linear_scores(subset_rows)
        self.assertEqual(tuple(round(value, 1) for value in subset_scores), (48.8, 62.4, 55.6))


if __name__ == "__main__":
    unittest.main()
