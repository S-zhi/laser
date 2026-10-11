"""Predict abusive yes/no labels for already simplified comments."""
import argparse
import json
from pathlib import Path

import joblib

def predict(text: str, bundle: dict) -> dict:
    vector = bundle["vectorizer"].transform([text])
    classifier = bundle["classifier"]
    result = {"label": str(classifier.predict(vector)[0]), "text_simplified": text}
    if hasattr(classifier, "predict_proba"):
        result["probabilities"] = {str(c): float(p) for c, p in zip(classifier.classes_, classifier.predict_proba(vector)[0])}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("model", type=Path)
    p.add_argument("text", nargs="?", help="Comment text; omit to read one comment per stdin line")
    a = p.parse_args()
    bundle = joblib.load(a.model)
    if a.text is not None:
        print(json.dumps(predict(a.text, bundle), ensure_ascii=False))
    else:
        import sys
        for line in sys.stdin:
            print(json.dumps(predict(line.rstrip("\n"), bundle), ensure_ascii=False))


if __name__ == "__main__":
    main()
