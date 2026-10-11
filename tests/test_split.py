import unittest

from tocab.train import evaluate_predictions, split_groups, train


class GroupSplitTests(unittest.TestCase):
    def test_train_rejects_non_finite_or_non_positive_c(self):
        for c in (0, -1, float("nan"), float("inf"), float("-inf")):
            with self.subTest(c=c), self.assertRaisesRegex(ValueError, "finite positive"):
                train(None, None, None, c=c)

    def test_evaluate_predictions_returns_binary_metrics(self):
        metrics = evaluate_predictions(["no", "yes", "yes"], ["no", "no", "yes"])
        self.assertAlmostEqual(metrics["accuracy"], 2 / 3)
        self.assertIn("macro_f1", metrics)
        self.assertEqual(set(metrics["classification_report"]) - {"accuracy", "macro avg", "weighted avg"}, {"no", "yes"})

    def test_evaluate_predictions_rejects_mismatched_shapes(self):
        with self.assertRaisesRegex(ValueError, "same shape"):
            evaluate_predictions(["no"], ["no", "yes"])

    def test_groups_are_disjoint_and_both_splits_have_both_labels(self):
        rows = []
        for i in range(20):
            rows.extend([
                {"group_id": f"post-{i}", "label": "yes" if i % 2 else "no"},
                {"group_id": f"post-{i}", "label": "no" if i % 2 else "yes"},
            ])
        train, valid = split_groups(rows, seed=17)
        train_groups = {rows[i]["group_id"] for i in train}
        valid_groups = {rows[i]["group_id"] for i in valid}
        self.assertFalse(train_groups & valid_groups)
        self.assertEqual({rows[i]["label"] for i in train}, {"yes", "no"})
        self.assertEqual({rows[i]["label"] for i in valid}, {"yes", "no"})

    def test_impossible_split_fails_clearly(self):
        rows = [{"group_id": "one", "label": "yes"}, {"group_id": "two", "label": "yes"}]
        with self.assertRaisesRegex(ValueError, "could not find a group split"):
            split_groups(rows)


if __name__ == "__main__":
    unittest.main()
