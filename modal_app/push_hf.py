"""Push a merged crawl run to a Hugging Face dataset repo as a dated subset.

Runs entirely on Modal so the data never has to touch your local disk: the
merged ``output/<run_type>/output.jsonl`` on the Volume is streamed into gzipped
JSONL shards (written back to the Volume, which has room), then uploaded to the
dataset repo under ``<date>/`` as a selectable config/subset.

The HF token is read from the local ``.env`` (``HF_TOKEN=...``) via
``modal.Secret.from_dotenv()`` and injected into the container as an env var —
it is never passed as a function argument or written to the Volume.

    # Merge first (if you haven't), then push the full run as the 2026_11_06 subset.
    # Invoke the `main` local entrypoint (not `::push`, which is the remote function
    # and exposes --private instead of the friendly --public below):
    modal run modal_app/app.py::merge --run-type full
    modal run modal_app/push_hf.py::main \
        --repo-id muzaffercky/kurdish-parallel-source --subset 2026_11_06

    # Options (--run-type defaults to full, dataset is private unless --public):
    modal run modal_app/push_hf.py::main \
        --repo-id muzaffercky/kurdish-parallel-source \
        --subset 2026_11_06 --run-type full --public
"""

from pathlib import Path

import modal

VOLUME_NAME = "kurdish_parallel_crawl"
VOLUME_MOUNT_PATH = "/vol"
OUTPUT_DIR = f"{VOLUME_MOUNT_PATH}/output"

# ~1 GiB of raw JSONL per shard before gzip (HTML compresses ~5-10x, so each
# uploaded part lands around 100-200 MB — a resumable, viewer-friendly size).
SHARD_BYTES = 1 * 1024 * 1024 * 1024

app = modal.App("kurdish_parallel_crawl_push_hf")
volume = modal.Volume.from_name(VOLUME_NAME)

image = modal.Image.debian_slim(python_version="3.10").pip_install(
    "huggingface_hub[hf_xet]==0.35.3"
)


def _dataset_card(subset: str) -> str:
    """Dataset card with a configs block so <subset>/ shows as a HF subset."""
    return f"""---
pretty_name: Kurdish Parallel Corpus — Raw Source
license: other
language:
- ku
- kmr
- ckb
- tr
- en
- ar
tags:
- kurdish
- multilingual
- news
- raw-html
- web-crawl
configs:
- config_name: "{subset}"
  data_files:
  - split: train
    path: "{subset}/*.jsonl.gz"
---

# Kurdish Parallel Corpus — Raw Source

Whole-domain crawls of Kurdish / multilingual news sites, stored as **raw HTML**
(no extraction, no filtering). This is a **raw source corpus** — a candidate for
building aligned/parallel data downstream, not parallel data itself. One JSON
object per line:

| field | description |
|-------|-------------|
| `url` | final page URL (after redirects) |
| `html` | raw HTML of the page, unchanged |
| `content_type` | the response `Content-Type` header |
| `fetched_at` | ISO 8601 UTC fetch timestamp |

Each dated subset (e.g. `{subset}`) is one crawl snapshot. Extraction, metadata
parsing and alignment are all done downstream against this raw HTML, so the
source is never lost and can be re-processed without re-crawling.
"""


@app.function(
    image=image,
    volumes={VOLUME_MOUNT_PATH: volume},
    secrets=[modal.Secret.from_dotenv()],
    cpu=2.0,
    memory=4096,
    timeout=60 * 60 * 6,
)
def push(repo_id: str, subset: str, run_type: str, private: bool) -> dict:
    import gzip
    import os
    import shutil

    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN not found in the container env (.env secret).")

    merged = Path(OUTPUT_DIR) / run_type / "output.jsonl"
    if not merged.is_file():
        raise FileNotFoundError(
            f"{merged} not found — run the merge step first "
            f"(modal run modal_app/app.py::merge --run-type {run_type})."
        )

    # Stage on the Volume (has room); mirror exactly what the repo should hold:
    # README.md at the root + <subset>/*.jsonl.gz.
    stage = Path(OUTPUT_DIR) / run_type / "_hf_stage"
    subset_dir = stage / subset
    if subset_dir.exists():
        shutil.rmtree(subset_dir)  # rebuild cleanly on re-run
    subset_dir.mkdir(parents=True, exist_ok=True)
    (stage / "README.md").write_text(_dataset_card(subset), encoding="utf-8")

    # Stream the merged file into gzipped shards by raw-byte size. Binary I/O
    # preserves the exact stored bytes and avoids a decode/encode round-trip.
    rows = 0
    shard_idx = 0
    bytes_in_shard = 0
    out = None

    def _open_shard(i: int):
        return gzip.open(subset_dir / f"part-{i:05d}.jsonl.gz", "wb")

    with merged.open("rb") as src:
        out = _open_shard(shard_idx)
        for line in src:
            if bytes_in_shard >= SHARD_BYTES:
                out.close()
                shard_idx += 1
                bytes_in_shard = 0
                out = _open_shard(shard_idx)
            out.write(line)
            bytes_in_shard += len(line)
            rows += 1
    if out is not None:
        out.close()

    n_shards = shard_idx + 1
    # Drop an empty trailing shard if the file size lined up exactly.
    last = subset_dir / f"part-{shard_idx:05d}.jsonl.gz"
    if rows and last.stat().st_size <= 30:  # gzip header of an empty stream
        last.unlink()
        n_shards -= 1

    volume.commit()  # persist the staged shards before the (long) upload

    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)
    api.upload_large_folder(
        repo_id=repo_id,
        repo_type="dataset",
        folder_path=str(stage),
    )

    return {
        "repo_id": repo_id,
        "subset": subset,
        "rows": rows,
        "shards": n_shards,
        "source": str(merged),
    }


@app.local_entrypoint()
def main(
    repo_id: str = "",
    subset: str = "",
    run_type: str = "full",
    public: bool = False,
):
    if not repo_id:
        raise ValueError(
            "Pass --repo-id, e.g. --repo-id muzaffercky/kurdish-parallel-source"
        )
    if not subset:
        raise ValueError(
            "Pass --subset, e.g. --subset 2026_11_06 (the dated snapshot name)"
        )
    import json

    result = push.remote(
        repo_id=repo_id, subset=subset, run_type=run_type, private=not public
    )
    print(json.dumps(result, indent=2))
    print(
        f"\nPushed {result['rows']} rows in {result['shards']} shard(s) to "
        f"https://huggingface.co/datasets/{repo_id}  (subset: {subset})"
    )
