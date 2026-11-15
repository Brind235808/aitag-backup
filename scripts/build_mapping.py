"""Build source-data <-> image mapping tables and archive plans.

Reads the local aitag backup tree (read-only) and writes:
  mapping/works.csv.gz      one row per work, from manifest/*.jsonl
  mapping/images.csv.gz     one row per queued image (ok/missing), with archive
  mapping/volumes.csv       planned archive list
  local/plan.json           packer input (archives -> entries)
"""
import csv
import gzip
import json
import os
import sys
import time

ROOT = os.environ.get("AITAG_ROOT", r"H:\ai精进\14_aitag全站备份_2026-11-05")
WORK = os.environ.get("AITAG_WORK", r"F:\aitag_github_upload")
REPO = os.path.join(WORK, "repo")
LOCAL = os.path.join(WORK, "local")
TARGET = 1_700_000_000  # ~1.7GB source bytes per archive


def log(msg):
    print(time.strftime("[%H:%M:%S]") + " " + str(msg), flush=True)


def read_tsv(path, ncols):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < ncols:
                continue
            yield parts


def plan_groups(items, target=TARGET):
    """items: list of (relpath, size). Returns list of lists."""
    groups, cur, cur_bytes = [], [], 0
    for rel, size in items:
        if cur and cur_bytes + size > target:
            groups.append(cur)
            cur, cur_bytes = [], 0
        cur.append((rel, size))
        cur_bytes += size
    if cur:
        groups.append(cur)
    return groups


def build_images(queue_path, done_path, missing_path):
    queue = []
    derived = 0
    for month, work_id, page, rel in read_tsv(queue_path, 4):
        stem = os.path.basename(rel)[:-len(".webp")] if rel.endswith(".webp") else os.path.basename(rel)
        try:
            wid = int(work_id)
        except (TypeError, ValueError):
            wid = int(stem.rsplit("_p", 1)[0])
            derived += 1
        try:
            pg = int(page)
        except (TypeError, ValueError):
            pg = int(stem.rsplit("_p", 1)[1])
        queue.append((month, wid, pg, rel))
    if derived:
        log(f"images: derived work_id from path for {derived} rows")
    done = {}
    for rel, size in read_tsv(done_path, 2):
        try:
            done[rel] = int(size)
        except ValueError:
            pass
    missing = set(rel for rel, _ in read_tsv(missing_path, 2))

    packed = [(rel, done[rel]) for _, _, _, rel in queue if rel in done]
    groups = plan_groups(packed)
    log(f"images: queue={len(queue)} done={len(done)} missing={len(missing)} packed={len(packed)} archives={len(groups)}")

    archive_of = {}
    volumes = []
    for i, grp in enumerate(groups, 1):
        name = f"aitag_images_{i:04d}.7z"
        for rel, _ in grp:
            archive_of[rel] = name
        volumes.append({
            "archive": name, "kind": "images", "entries": len(grp),
            "source_bytes": sum(s for _, s in grp),
            "first_entry": grp[0][0], "last_entry": grp[-1][0],
        })

    rows = []
    for month, work_id, page, rel in queue:
        if rel in done:
            rows.append((rel, done[rel], month, work_id, page, "ok", archive_of[rel]))
        elif rel in missing:
            rows.append((rel, 0, month, work_id, page, "missing_404", ""))
        else:
            rows.append((rel, 0, month, work_id, page, "unknown", ""))
    return rows, volumes, groups


def scan_tree(top):
    out = []
    for dirpath, _dirnames, filenames in os.walk(top):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            try:
                out.append((rel, os.path.getsize(p)))
            except OSError:
                pass
    out.sort()
    return out


def build_meta():
    meta_files = scan_tree(os.path.join(ROOT, "meta"))
    groups = plan_groups(meta_files)
    log(f"meta: files={len(meta_files)} bytes={sum(s for _, s in meta_files)/2**30:.2f}GB archives={len(groups)}")
    volumes, plan = [], []
    for i, grp in enumerate(groups, 1):
        name = f"aitag_meta_{i:04d}.7z"
        volumes.append({
            "archive": name, "kind": "meta", "entries": len(grp),
            "source_bytes": sum(s for _, s in grp),
            "first_entry": grp[0][0], "last_entry": grp[-1][0],
        })
        plan.append({"name": name, "kind": "meta", "cwd": ROOT,
                     "entries": [r for r, _ in grp], "password": False})
    return volumes, plan


def explicit_plan(kind, glob_dir, names):
    volumes, plan = [], []
    files = []
    for n in names:
        p = os.path.join(ROOT, glob_dir, n)
        files.append((f"{glob_dir}/{n}", os.path.getsize(p)))
    total = sum(s for _, s in files)
    if kind == "manifest":
        # ~700MB raw per archive, LZMA compresses these JSONL well
        chunks, cur, cur_bytes = [], [], 0
        for rel, size in files:
            if cur and cur_bytes + size > 700_000_000:
                chunks.append(cur); cur, cur_bytes = [], 0
            cur.append((rel, size)); cur_bytes += size
        if cur:
            chunks.append(cur)
    else:
        chunks = [files]
    for i, grp in enumerate(chunks, 1):
        name = f"aitag_{kind}_{i:04d}.7z"
        volumes.append({
            "archive": name, "kind": kind, "entries": len(grp),
            "source_bytes": sum(s for _, s in grp),
            "first_entry": grp[0][0], "last_entry": grp[-1][0],
        })
        plan.append({"name": name, "kind": kind, "cwd": ROOT,
                     "entries": [r for r, _ in grp], "password": False})
    log(f"{kind}: files={len(files)} bytes={total/2**30:.2f}GB archives={len(volumes)}")
    return volumes, plan


def build_works():
    """One row per indexed work (index/*.all.jsonl is the authoritative id list)."""
    index_dir = os.path.join(ROOT, "index")
    out = os.path.join(REPO, "mapping", "works.csv.gz")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    months = sorted(f for f in os.listdir(index_dir) if f.endswith(".all.jsonl"))
    n = 0
    dup = 0
    seen = set()
    t0 = time.time()
    with gzip.open(out, "wt", encoding="utf-8", newline="") as gz:
        w = csv.writer(gz)
        w.writerow(["work_id", "month", "ai_type", "user_id", "user_name", "title",
                    "create_date", "image_count", "total_bookmarks", "meta_path", "ok"])
        for fn in months:
            month = fn.split(".")[0]
            with open(os.path.join(index_dir, fn), encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    wid = d.get("id")
                    if wid in seen:
                        dup += 1
                        continue
                    seen.add(wid)
                    meta_path = f"meta/{month}/{str(wid)[:6]}/{wid}.json.gz"
                    w.writerow([
                        wid, month, d.get("AI_type"), d.get("userId"),
                        d.get("userName"), d.get("title"), d.get("create_date"),
                        d.get("image_count"), d.get("total_bookmarks"), meta_path, 1,
                    ])
                    n += 1
    log(f"works: rows={n} dup_skipped={dup} file={out} {os.path.getsize(out)/2**20:.1f}MB {time.time()-t0:.0f}s")


def main():
    t0 = time.time()
    os.makedirs(LOCAL, exist_ok=True)
    os.makedirs(os.path.join(REPO, "mapping"), exist_ok=True)

    state = os.path.join(ROOT, "state")
    img_rows, img_vols, img_groups = build_images(
        os.path.join(state, "img_queue.tsv"),
        os.path.join(state, "img_done.tsv"),
        os.path.join(state, "img_missing.tsv"),
    )

    meta_vols, meta_plan = build_meta()

    manifest_names = sorted(f for f in os.listdir(os.path.join(ROOT, "manifest")) if f.endswith(".jsonl"))
    manifest_vols, manifest_plan = explicit_plan("manifest", "manifest", manifest_names)

    index_names = sorted(f for f in os.listdir(os.path.join(ROOT, "index")) if os.path.isfile(os.path.join(ROOT, "index", f)))
    index_vols, index_plan = explicit_plan("index", "index", index_names)

    state_names = [n for n in ["img_queue.tsv", "img_done.tsv", "img_missing.tsv",
                               "all_month_totals.json", "dedup_manifest_20261113.json",
                               "audit_detail_20261115.json"]
                   if os.path.isfile(os.path.join(state, n))]
    state_files = [(f"state/{n}", os.path.getsize(os.path.join(state, n))) for n in state_names]
    state_plan = [{"name": "aitag_state.7z", "kind": "state", "cwd": ROOT,
                   "entries": [r for r, _ in state_files], "password": False}]
    state_vols = [{"archive": "aitag_state.7z", "kind": "state", "entries": len(state_files),
                   "source_bytes": sum(s for _, s in state_files),
                   "first_entry": state_files[0][0], "last_entry": state_files[-1][0]}]
    log(f"state: {len(state_files)} files {sum(s for _, s in state_files)/2**20:.1f}MB")

    volumes = img_vols + meta_vols + manifest_vols + index_vols + state_vols
    plan = ([{"name": f"aitag_images_{i:04d}.7z", "kind": "images",
              "cwd": os.path.join(ROOT, "thumbs"),
              "entries": [r for r, _ in grp], "password": True}
             for i, grp in enumerate(img_groups, 1)]
            + meta_plan + manifest_plan + index_plan + state_plan)

    with open(os.path.join(LOCAL, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False)
    with open(os.path.join(LOCAL, "volumes_plan.json"), "w", encoding="utf-8") as f:
        json.dump(volumes, f, ensure_ascii=False, indent=1)

    vpath = os.path.join(REPO, "mapping", "volumes.csv")
    with open(vpath, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["archive", "kind", "entries", "source_bytes", "sha256", "github_digest",
                    "first_entry", "last_entry"])
        for v in volumes:
            w.writerow([v["archive"], v["kind"], v["entries"], v["source_bytes"], "", "",
                        v["first_entry"], v["last_entry"]])

    ipath = os.path.join(REPO, "mapping", "images.csv.gz")
    with gzip.open(ipath, "wt", encoding="utf-8", newline="") as gz:
        w = csv.writer(gz)
        w.writerow(["relpath", "bytes", "month", "work_id", "page", "status", "archive"])
        w.writerows(img_rows)
    log(f"images.csv: rows={len(img_rows)} {os.path.getsize(ipath)/2**20:.1f}MB")

    build_works()

    total_arch = sum(v["source_bytes"] for v in volumes)
    log(f"plan: archives={len(volumes)} source={total_arch/2**30:.2f}GB images={len(img_groups)} "
        f"elapsed={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
