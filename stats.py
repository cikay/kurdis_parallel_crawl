"""Quick statistics over a crawled dataset (.jsonl / .json / .csv).

Each record has three fields: ``url``, ``html`` (raw HTML), and ``fetched_at``.

Usage:
    python stats.py --file-name output.jsonl
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse


def load_records(path: Path) -> list[dict]:
    ext = path.suffix.lower()
    if ext == ".jsonl":
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    if ext == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if ext == ".csv":
        import csv

        with path.open(encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    raise ValueError(f"Unsupported extension: {ext}")


def _domain(url: str) -> str:
    host = (urlparse(url or "").netloc or "?").lower()
    return host[4:] if host.startswith("www.") else host


def main() -> None:
    parser = argparse.ArgumentParser(description="Dataset statistics.")
    parser.add_argument("--file-name", required=True, help="Path to the dataset file")
    args = parser.parse_args()

    records = load_records(Path(args.file_name))
    print(f"Total pages: {len(records)}")

    urls = [r.get("url") for r in records if r.get("url")]
    print(f"Unique urls: {len(set(urls))}")

    empty = sum(1 for r in records if not (r.get("html") or "").strip())
    print(f"Pages with empty content: {empty}")

    total_bytes = sum(len(r.get("html") or "") for r in records)
    print(f"Total HTML size: {total_bytes / 1_048_576:.1f} MiB")

    by_domain = Counter(_domain(r.get("url", "")) for r in records)
    print("\nPages per domain:")
    for domain, count in by_domain.most_common():
        print(f"  {count:>7}  {domain}")


if __name__ == "__main__":
    main()
