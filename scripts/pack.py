import json
import os
import subprocess
import time

ROOT = os.environ.get("AITAG_ROOT", r"H:\ai精进\14_aitag全站备份_2026-11-05")
WORK = os.environ.get("AITAG_WORK", r"F:\aitag_github_upload")
ASSETS = os.path.join(WORK, "assets")
LOCAL = os.path.join(WORK, "local")
SEVEN = r"C:\Program Files\7-Zip\7z.exe"
KIND_DIR = {"images": "images", "meta": "meta", "manifest": "manifest", "index": "index", "state": "state"}


def log(msg):
    print(time.strftime("[%H:%M:%S]") + " " + str(msg), flush=True)


def load_password():
    path = os.path.join(LOCAL, "secret.env")
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("AITAG_PW="):
                return line.split("=", 1)[1]
    raise SystemExit("AITAG_PW missing in local/secret.env")


def archive_path(entry):
    return os.path.join(ASSETS, KIND_DIR[entry["kind"]], entry["name"])


def marker_path(name):
    return os.path.join(LOCAL, "packed", name + ".json")


def pack(entry):
    name = entry["name"]
    out = archive_path(entry)
    marker = marker_path(name)
    if os.path.isfile(out) and os.path.isfile(marker):
        with open(marker, encoding="utf-8") as f:
            meta = json.load(f)
        if os.path.getsize(out) == meta.get("archive_bytes"):
            return meta
    os.makedirs(os.path.dirname(out), exist_ok=True)
    os.makedirs(os.path.join(LOCAL, "lists"), exist_ok=True)
    os.makedirs(os.path.join(LOCAL, "packed"), exist_ok=True)
    if os.path.isfile(out):
        os.remove(out)
    kept, missing = [], []
    for rel in entry["entries"]:
        if os.path.isfile(os.path.join(entry["cwd"], rel)):
            kept.append(rel)
        else:
            missing.append(rel)
    if missing:
        log(f"pack {name}: {len(missing)} files missing on disk, skipped")
        with open(os.path.join(LOCAL, "missing_at_pack.tsv"), "a", encoding="utf-8") as f:
            for rel in missing:
                f.write(f"{rel}\t{name}\n")
    if not kept:
        raise RuntimeError(f"no files left to pack for {name}")
    lst = os.path.join(LOCAL, "lists", name + ".txt")
    with open(lst, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(kept))

    args = [SEVEN, "a", "-t7z", out, "@" + lst, "-bso0", "-bsp0", "-y"]
    if entry["kind"] == "images":
        args += ["-mx=0", "-ms=off", "-mhe=on", "-p" + load_password()]
    elif entry["kind"] == "meta":
        args += ["-mx=0", "-ms=off"]
    elif entry["kind"] == "manifest":
        args += ["-mx=5", "-ms=on"]
    else:
        args += ["-mx=5", "-ms=on"]

    log(f"pack start {name} entries={len(entry['entries'])}")
    t0 = time.time()
    p = subprocess.run(args, cwd=entry["cwd"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode not in (0, 1):
        raise RuntimeError(f"7z failed {name} rc={p.returncode}: {(p.stderr or '')[-800:]}")
    size = os.path.getsize(out)
    meta = {
        "name": name, "kind": entry["kind"], "entries": len(entry["entries"]),
        "archive_bytes": size, "elapsed_s": round(time.time() - t0, 1),
        "password": entry["password"],
    }
    with open(marker, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    log(f"pack done {name} {size/2**30:.2f}GB {meta['elapsed_s']}s")
    return meta
