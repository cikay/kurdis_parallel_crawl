"""
Modal entry points for the Kurdish parallel-corpus crawler.

The crawler is CPU-only and I/O-bound (polite, throttled domain walks), so no GPU
is requested anywhere here. Each domain is crawled in its own container, in
parallel, and writes its raw-HTML shard to a persistent Modal Volume so the output
survives the container ending.

Output is organised by *run type*, not a per-run timestamp: a full-domain walk
writes under ``output/full/`` and a capped smoke test under ``output/max_pages_<n>/``
(``<n>`` being the ``--max-pages`` value).
Feeds append (``overwrite: False``), and each domain's seen-article-ID set persists
beside its shard as ``<host>_seen_ids.json``, so re-running a ``full`` crawl is
incremental — only articles not seen before are fetched and appended. Full runs also
get a Scrapy JOBDIR at ``output/full/<host>_job/`` (pending request queue + seen
fingerprints), so a crawl cut short by the timeout or a preemption resumes exactly
where it stopped on the next run. (Capped smoke tests get no JOBDIR.)

    # Full crawl of every domain in parallel_data_domains.json (resolving redirects):
    modal run --detach modal_app/app.py::crawl

    # Skip redirect resolution, or point at a different domains file:
    modal run --detach modal_app/app.py::crawl --no-resolve
    modal run --detach modal_app/app.py::crawl --domains-file parallel_data_domains.json

    # Merge a run type's per-domain shards into one output.jsonl on the Volume:
    modal run modal_app/app.py::merge --run-type full

Then pull the results down to your machine with the Modal CLI. The run-type folder
holds one <host>.jsonl + <host>.log per domain, so the destination must be an
existing directory (note the trailing slash) — otherwise the shards collapse onto
one path and overwrite each other:

    modal volume ls   kurdish_parallel_crawl output/full
    modal volume get  kurdish_parallel_crawl output/full data/
"""

import json
from pathlib import Path
from urllib.parse import urlsplit

import modal

VOLUME_NAME = "kurdish_parallel_crawl"
VOLUME_MOUNT_PATH = "/vol"
OUTPUT_DIR = f"{VOLUME_MOUNT_PATH}/output"

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOMAINS_FILE = "parallel_data_domains.json"

app = modal.App("kurdish_parallel_crawl")

volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

# CPU-only image. Dependencies mirror the Pipfile; no torch/CUDA anywhere.
image = (
    modal.Image.debian_slim(python_version="3.10")
    .pip_install("scrapy==2.14.1", "requests", "dotenv==0.9.9")
    .env({"SCRAPY_SETTINGS_MODULE": "kurdish_crawl.settings"})
    .add_local_file(REPOSITORY_ROOT / "scrapy.cfg", "/root/scrapy.cfg")
    .add_local_python_source("kurdish_crawl", "extractor", "run_crawler", "main")
)


def _safe_name(url: str) -> str:
    """Filesystem-safe shard name derived from a domain URL."""
    host = urlsplit(url).netloc or url
    return host.replace(":", "_").replace("/", "_") or "domain"


@app.function(image=image, volumes={VOLUME_MOUNT_PATH: volume}, timeout=60 * 30)
def resolve_domains(urls: list[str]) -> list[str]:
    """Follow redirects to the final domains (news sites move often). Runs remotely
    so the local machine needs no dependencies and no network access."""
    from main import resolve_redirected_domains

    return resolve_redirected_domains(urls)


@app.function(
    image=image,
    volumes={VOLUME_MOUNT_PATH: volume},
    cpu=1.0,
    memory=2048,
    # A whole-domain walk at DOWNLOAD_DELAY can run for many hours; give it room.
    timeout=60 * 60 * 12,
    # Timeouts (and other failures) are retried by this policy. Each retry re-runs
    # with the same args, so it hits the same JOBDIR and *resumes* where the
    # previous attempt stopped — progress accumulates across attempts rather than
    # restarting. Generous count so even the largest sites finish over resumes.
    retries=5,
    # One crawl per container, container retired afterwards. Scrapy's Twisted
    # reactor is a process-global singleton that cannot be started twice, so a
    # reused (warm) container running a second domain — or a retry landing on a
    # container that already crawled — would crash with ReactorNotRestartable.
    # A fresh container per input guarantees a fresh reactor each time.
    single_use_containers=True,
)
def crawl_domain(url: str, run_type: str, max_pages: int | None = None) -> dict:
    """Crawl one whole domain and write its raw-HTML shard to the Volume.

    ``run_type`` is the output folder ("full" or "max_pages_<n>"). ``max_pages`` caps
    the pages stored per domain (smoke test); ``None`` means a full-domain walk.
    The seen-article-ID set is saved beside the shard as ``<host>_seen_ids.json``
    so repeat full crawls are incremental."""
    import os

    from run_crawler import run_crawler

    # Stamp the container's stdout with its domain + task id, so the
    # domain<->container mapping is recoverable from `modal container logs <id>`
    # (the spider logs go to the Volume log file, not here). MODAL_TASK_ID is the
    # same `ta-...` id shown by `modal container list`.
    print(f"[crawl] task={os.environ.get('MODAL_TASK_ID')} url={url}", flush=True)

    run_dir = Path(OUTPUT_DIR) / run_type
    run_dir.mkdir(parents=True, exist_ok=True)
    shard = run_dir / f"{_safe_name(url)}.jsonl"
    log_file = run_dir / f"{_safe_name(url)}.log"
    # Scrapy JOBDIR on the Volume: persists this domain's scheduler queue and
    # seen-request fingerprints, so a crawl cut short by the timeout or a
    # preemption resumes where it stopped on the next run (nothing re-fetched).
    # Only for full runs — capped smoke tests are disposable, and resuming old
    # state across smoke runs would be confusing.
    job_dir = None if max_pages else str(run_dir / f"{_safe_name(url)}_job")

    try:
        run_crawler(
            output_path=str(shard),
            urls=[url],
            log_file=str(log_file),
            log_level="INFO",
            max_pages=max_pages,
            job_dir=job_dir,
            # Full runs: flush shard + JOBDIR to the Volume every few minutes, so a
            # container killed mid-crawl (timeout, preemption) loses almost no work
            # and the auto-retry resumes from a very recent checkpoint. Smoke tests
            # (no job_dir) don't need it.
            commit_callback=volume.commit if job_dir else None,
            commit_interval=120.0,
        )
    finally:
        # Final flush of whatever was fetched (shard, seen-ID sets, JOBDIR resume
        # state), even on partial failure, so a re-run can continue.
        volume.commit()

    pages = 0
    if shard.is_file():
        with shard.open(encoding="utf-8") as handle:
            pages = sum(1 for _ in handle)

    return {"url": url, "pages": pages, "shard": str(shard)}


@app.function(image=image, volumes={VOLUME_MOUNT_PATH: volume}, timeout=60 * 30)
def merge(run_type: str = "full") -> dict:
    """Concatenate a run type's per-domain shards into one output.jsonl on the Volume."""
    run_dir = Path(OUTPUT_DIR) / run_type
    merged = run_dir / "output.jsonl"
    shards = sorted(p for p in run_dir.glob("*.jsonl") if p.name != merged.name)

    total = 0
    with merged.open("w", encoding="utf-8") as out:
        for shard in shards:
            with shard.open(encoding="utf-8") as handle:
                for line in handle:
                    out.write(line)
                    total += 1
    volume.commit()

    result = {"run_type": run_type, "shards": len(shards), "pages": total, "merged": str(merged)}
    print(json.dumps(result, indent=2))
    return result


@app.local_entrypoint()
def crawl(
    domains_file: str = DEFAULT_DOMAINS_FILE,
    no_resolve: bool = False,
    urls: str = "",
    max_pages: int = 0,
):
    """Fan out one crawler container per domain; each writes a shard to the Volume.

    --urls      comma-separated URL(s) to crawl instead of the domains file
                (debug/smoke test; passed as-is, redirect resolution is skipped).
    --max-pages cap stored pages per domain (0 = full-domain walk).

        modal run --detach modal_app/app.py::crawl
        modal run modal_app/app.py::crawl --urls "https://bianet.org" --max-pages 20
    """
    if urls:
        url_list = [u.strip().rstrip("/") for u in urls.split(",") if u.strip()]
        if not url_list:
            raise ValueError("--urls was given but contained no URL")
    else:
        entries = json.loads((REPOSITORY_ROOT / domains_file).read_text(encoding="utf-8"))
        url_list = [e.strip().rstrip("/") for e in entries if isinstance(e, str) and e.strip()]
        if not no_resolve:
            url_list = resolve_domains.remote(url_list)

    cap = max_pages or None
    run_type = f"max_pages_{cap}" if cap else "full"
    print(
        f"Crawling {len(url_list)} domain(s)"
        f"{f', max {cap} pages each' if cap else ''} — run_type={run_type}"
    )

    results = list(crawl_domain.map(url_list, kwargs={"run_type": run_type, "max_pages": cap}))

    total = sum(r["pages"] for r in results)
    for r in sorted(results, key=lambda r: r["pages"], reverse=True):
        status = "OK  " if r["pages"] else "NONE"
        print(f"  [{status}] {r['pages']:>7} pages  {r['url']}")
    print(f"\nTotal: {total} pages across {len(results)} domain(s)")
    print(f"Shards on Volume '{VOLUME_NAME}' under output/{run_type}/")
    if not total:
        print("WARNING: nothing was stored — check the per-domain .log shard for errors.")
    print(f"Merge with:  modal run modal_app/app.py::merge --run-type {run_type}")
