"""Text normalization shared by preparation and inference."""
from functools import lru_cache


@lru_cache(maxsize=1)
def _converter():
    try:
        import opencc
    except ImportError as exc:
        raise RuntimeError("OpenCC is required; install requirements.txt") from exc
    return opencc.OpenCC("t2s.json")


def traditional_to_simplified(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return _converter().convert(text)
