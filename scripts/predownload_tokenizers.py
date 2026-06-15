"""Pre-download HuggingFace tokenizers used by the usage estimator.

This is run at Docker build time so the image ships with the tokenizer files
and does not need to call HuggingFace Hub at runtime.
"""
from __future__ import annotations

from app.dataplane.usage_estimator import _KNOWN_TOKENIZER_MAPPINGS


def main() -> int:
    from transformers import AutoTokenizer

    downloaded: set[str] = set()
    for hf_name, _calibration in _KNOWN_TOKENIZER_MAPPINGS.values():
        if hf_name in downloaded:
            continue
        downloaded.add(hf_name)
        print(f"Downloading tokenizer: {hf_name}")
        AutoTokenizer.from_pretrained(hf_name, trust_remote_code=True)
        print(f"  done: {hf_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
