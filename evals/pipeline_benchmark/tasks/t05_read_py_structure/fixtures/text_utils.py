"""Text processing helpers for the pipeline."""

import re


def normalize_space(text):
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text):
    parts = re.split(r"[.!?。！？]", text)
    return [p.strip() for p in parts if p.strip()]


def count_vowels(text):
    return sum(1 for ch in text.lower() if ch in "aeiou")


class TokenBucket:
    def __init__(self, capacity):
        self.capacity = capacity
        self.tokens = []

    def add(self, token):
        if len(self.tokens) < self.capacity:
            self.tokens.append(token)


def process_records(records):
    """Clean, deduplicate and re-index a list of raw text records."""
    seen = set()
    cleaned = []
    for record in records:
        if record is None:
            continue
        text = normalize_space(str(record))
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    indexed = []
    for pos, text in enumerate(cleaned, start=1):
        tokens = text.split()
        entry = {
            "id": pos,
            "text": text,
            "n_tokens": len(tokens),
            "n_chars": len(text),
            "has_digit": any(ch.isdigit() for ch in text),
        }
        indexed.append(entry)
    total_tokens = sum(item["n_tokens"] for item in indexed)
    summary = {
        "records": indexed,
        "n_records": len(indexed),
        "total_tokens": total_tokens,
        "avg_tokens": (total_tokens / len(indexed)) if indexed else 0.0,
    }
    return summary


class RecordStore:
    def __init__(self):
        self.items = []

    def load(self, rows):
        self.items = process_records(rows)["records"]


def longest_token(text):
    tokens = text.split()
    return max(tokens, key=len) if tokens else ""


def strip_punct(text):
    return re.sub(r"[^\w\s]", "", text)


def snippet(text, width=20):
    return text if len(text) <= width else text[: width - 1] + "…"
