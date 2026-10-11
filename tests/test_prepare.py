import unittest
from collections import Counter

from tocab.prepare import records


class PrepareTests(unittest.TestCase):
    def test_wrapper_string_permalink_and_conversion(self):
        raw = {
            "dataset_name": "TOCAB",
            "ptt_post": [{
                "permalink_info": "M.1561282423.A.D87",
                "comment": [{"sentence": "這是繁體中文", "abusive": "no"}],
            }],
        }
        counts = Counter()
        rows = list(records(raw, counts))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["group_id"], "M.1561282423.A.D87")
        self.assertEqual(rows[0]["text_raw"], "這是繁體中文")
        self.assertEqual(rows[0]["text_simplified"], "这是繁体中文")
        self.assertEqual(rows[0]["label"], "no")
        self.assertEqual(counts["kept"], 1)


if __name__ == "__main__":
    unittest.main()
