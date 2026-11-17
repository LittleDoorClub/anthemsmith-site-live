"""Make the deliverable set match the keeper take.

`qc_take_lyrics` decides WHICH take wins from a number (lyric coverage, anchor
lines). This applies that decision to the two files a customer actually
receives, so the pick is not just a JSON opinion:

  <order>-full.mp3   the full song   <- keeper bytes
  <order>.mp3        the 30s hook    <- re-cut from the keeper at the same rule

Refuses on any disagreement between `..takes.json` and the bytes on disk
(sha256), because a keeper adopted on a swapped file is worse than no keeper.
Idempotent: the second run writes nothing and says so.

  python scripts/adopt_keeper.py AS-XXXX [--songs DIR] [--check] [--no-rehook]

Exit 0 = adopted or already adopted. Exit 2 = refused (nothing written).
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

TAKE_QC = "scripts/qc_take_lyrics.py"
HOOK_SECONDS = 30
HOOK_RULE = "start = max(0, int(duration*0.72) - 15)  # forge_order.py hook rule"
ADOPTED_BY = "gforce-ox dna4 1c31"


class Refused(Exception):
    """The keeper cannot be adopted without lying about a file."""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise Refused("ffprobe_failed:%s:%s" % (path, out.stderr.strip()[:160]))
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise Refused("ffprobe_unparsable:%s:%s" % (path, out.stdout.strip()[:80]))


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, payload):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def copy_atomic(src, dst):
    tmp = Path(str(dst) + ".tmp")
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        for chunk in iter(lambda: fin.read(1 << 20), b""):
            fout.write(chunk)
    os.replace(str(tmp), str(dst))


def cut_hook(full_path, hook_path, duration):
    """Same rule the forge uses, so the demo stays a window on the delivered song."""
    start = max(0, int(duration * 0.72) - 15)
    tmp = Path(str(hook_path) + ".tmp.mp3")
    out = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-t", str(HOOK_SECONDS), "-i", str(full_path),
         "-c", "copy", str(tmp)], capture_output=True, text=True)
    if out.returncode != 0:
        raise Refused("ffmpeg_hook_failed:%s" % out.stderr.strip()[:160])
    return start, tmp


def plan(order, songs_dir, rehook=True):
    """Read-only: everything adopt would do, or a Refused explaining why not."""
    songs = Path(songs_dir)
    takes_path = songs / ("%s.takes.json" % order)
    if not takes_path.exists():
        raise Refused("no_takes_manifest:%s" % takes_path)
    takes = load_json(takes_path)

    keeper = takes.get("keeper")
    keeper_file = takes.get("keeper_file")
    keeper_sha = (takes.get("keeper_sha256") or "").lower()
    if not keeper or not keeper_file or not keeper_sha:
        raise Refused("takes_manifest_missing_keeper_fields")

    keeper_path = songs / keeper_file
    if not keeper_path.exists():
        raise Refused("keeper_file_missing:%s" % keeper_file)
    actual = sha256_file(keeper_path)
    if actual != keeper_sha:
        raise Refused("keeper_sha256_mismatch:declared=%s actual=%s" % (keeper_sha[:16], actual[:16]))

    declared_dur = takes.get("keeper_duration")
    measured = probe_duration(keeper_path)
    if declared_dur is not None and abs(measured - float(declared_dur)) > 1.0:
        raise Refused("keeper_duration_mismatch:declared=%s measured=%.2f" % (declared_dur, measured))

    full_path = songs / ("%s-full.mp3" % order)
    hook_path = songs / ("%s.mp3" % order)
    alt = takes.get("alternate") or {}
    alt_take, alt_sha = alt.get("take"), (alt.get("sha256") or "").lower()

    full_sha = sha256_file(full_path) if full_path.exists() else None
    backup_path = songs / ("%s-%s-full.mp3" % (order, alt_take)) if alt_take else None

    ops = []
    if full_sha == keeper_sha:
        ops.append("full_already_keeper")
    elif full_sha is None:
        ops.append("full_missing_create")
    else:
        ops.append("full_replace_with_keeper")
        if alt_sha and full_sha != alt_sha:
            # The current deliverable is neither the keeper nor the documented
            # alternate: back it up under a name that does not claim to be the
            # alternate take.
            backup_path = songs / ("%s-pre-keeper-full.mp3" % order)

    hook_start = max(0, int(measured * 0.72) - 15)
    manifest_path = songs / ("%s.json" % order)
    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    hook_meta = manifest.get("hook") or {}
    hook_sha = sha256_file(hook_path) if hook_path.exists() else None
    # A hook already cut from this keeper at this rule is not re-cut: re-running
    # the tool must not rewrite an artifact that is already correct.
    hook_is_current = bool(
        rehook and hook_sha and hook_meta.get("sha256") == hook_sha
        and hook_meta.get("start_seconds") == hook_start
        and hook_meta.get("derived_from") == keeper_file)
    if rehook:
        ops.append("hook_already_from_keeper" if hook_is_current else "hook_recut_from_keeper")
    return {
        "order_id": order,
        "songs_dir": str(songs),
        "keeper": keeper,
        "keeper_file": keeper_file,
        "keeper_sha256": keeper_sha,
        "keeper_duration": round(measured, 2),
        "full_sha256": full_sha,
        "full_is_keeper": full_sha == keeper_sha,
        "backup_of_current_full": str(backup_path) if backup_path and full_sha != keeper_sha else None,
        "hook_seconds": HOOK_SECONDS if rehook else None,
        "hook_start": hook_start if rehook else None,
        "hook_is_current": hook_is_current,
        "ops": ops,
        "takes": takes,
        "full_path": str(full_path),
        "hook_path": str(hook_path),
        "manifest_path": str(songs / ("%s.json" % order)),
    }


def adopt(order, songs_dir, rehook=True, check=False):
    p = plan(order, songs_dir, rehook=rehook)
    if check:
        p["mode"] = "check"
        return p

    songs = Path(p["songs_dir"])
    keeper_path = songs / p["keeper_file"]
    full_path = Path(p["full_path"])
    hook_path = Path(p["hook_path"])
    manifest_path = Path(p["manifest_path"])
    takes = p["takes"]
    done = []

    if not p["full_is_keeper"]:
        if full_path.exists() and p["backup_of_current_full"]:
            copy_atomic(full_path, p["backup_of_current_full"])
            done.append("backed_up_current_full:%s" % Path(p["backup_of_current_full"]).name)
        copy_atomic(keeper_path, full_path)
        if sha256_file(full_path) != p["keeper_sha256"]:
            raise Refused("post_write_sha256_mismatch:%s" % full_path.name)
        done.append("full_replaced_with_keeper")
    else:
        done.append("full_already_keeper")

    duration = probe_duration(full_path)

    hook_changed = False
    start = p["hook_start"]
    if rehook and not p["hook_is_current"]:
        start, tmp_hook = cut_hook(full_path, hook_path, duration)
        hook_dur = probe_duration(tmp_hook)
        if not 28.0 <= hook_dur <= 32.0:
            tmp_hook.unlink(missing_ok=True)
            raise Refused("hook_duration_out_of_range:%.2f" % hook_dur)
        os.replace(str(tmp_hook), str(hook_path))
        done.append("hook_recut:%s@%ds" % (hook_path.name, start))
        hook_changed = True
    elif rehook:
        done.append("hook_already_from_keeper")

    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    prior_keeper = (manifest.get("keeper") or {}).get("sha256")
    if not (hook_changed or not p["full_is_keeper"] or prior_keeper != p["keeper_sha256"]):
        # Nothing to learn and nothing to change: report, do not rewrite.
        return {"order_id": order, "mode": "noop", "keeper": p["keeper"],
                "duration_full": round(duration, 2), "hook_start_seconds": start,
                "ops": done, "note": "no_writes", "manifest": str(manifest_path)}
    old_full = manifest.get("duration_full")
    manifest["duration_full"] = round(duration, 2)
    if old_full is not None and abs(float(old_full) - duration) > 0.05:
        manifest["duration_full_superseded"] = old_full
    adopted_at = (manifest.get("keeper") or {}).get("adopted_at") if prior_keeper == p["keeper_sha256"] else None
    manifest["keeper"] = {
        "take": p["keeper"],
        "file": p["keeper_file"],
        "sha256": p["keeper_sha256"],
        "duration": round(duration, 2),
        "decided_by": takes.get("keeper_decided_by"),
        "decided_at": takes.get("keeper_decided_at"),
        "adopted_by": ADOPTED_BY,
        "adopted_at": adopted_at or datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "qc_tool": TAKE_QC,
        "takes_manifest": "%s.takes.json" % order,
    }
    if rehook:
        manifest["hook"] = {
            "file": hook_path.name,
            "seconds": HOOK_SECONDS,
            "start_seconds": start,
            "duration": round(probe_duration(hook_path), 2),
            "sha256": sha256_file(hook_path),
            "derived_from": p["keeper_file"],
            "rule": HOOK_RULE,
        }
    # A keeper that has never been read by the private lane must be re-ingested
    # before anyone claims the customer can hear it.
    manifest["needs_private_ingest"] = True
    write_json(manifest_path, manifest)
    done.append("manifest_updated")

    return {"order_id": order, "mode": "adopt", "keeper": p["keeper"],
            "duration_full": manifest["duration_full"], "hook_start_seconds": start if rehook else None,
            "ops": done, "manifest": str(manifest_path)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Apply the keeper take to the deliverable set.")
    ap.add_argument("order_id")
    ap.add_argument("--songs", default="songs")
    ap.add_argument("--check", action="store_true", help="report the plan, write nothing")
    ap.add_argument("--no-rehook", action="store_true", help="leave the 30s hook untouched")
    args = ap.parse_args(argv)
    try:
        report = adopt(args.order_id, args.songs, rehook=not args.no_rehook, check=args.check)
    except Refused as exc:
        print(json.dumps({"order_id": args.order_id, "status": "refused", "reason": str(exc)}, indent=1))
        return 2
    report.setdefault("status", "ok")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
