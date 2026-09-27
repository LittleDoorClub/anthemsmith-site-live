#!/usr/bin/env python3
"""Recovery-only workflow boundary. Dispatch input is an order ID, never proof of payment.

The public relay is disabled. Only service-owned recovery records may supply payment,
source, brief and delivery data. No customer payload is interpolated into a shell or
exported as a GitHub Actions step environment variable.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request

ORIGIN = "https://veqpmdsiqcjjpxgcubrp.supabase.co"
ORDER_TABLE = "anthemsmith_recovery_orders"
UPLOAD_TABLE = "anthemsmith_recovery_uploads"
ORDER_PATTERN = r"AS-[A-Za-z0-9_-]{1,90}"
PUBLIC_SUFFIXES = (".json", ".mp3", "-full.mp3", ".blocked.json", ".failed.json", ".delivery.json")


class GateError(Exception):
    """Contains an operator-safe reason code, never user data or provider bodies."""


def order_id(value):
    if not isinstance(value, str) or not re.fullmatch(ORDER_PATTERN, value):
        raise GateError("invalid_order_id")
    return value


def output(name, value):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def mask(value):
    if isinstance(value, str) and value:
        value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::add-mask::" + value, flush=True)


def private_path():
    return Path(os.environ["RUNNER_TEMP"]) / "anthemsmith-canonical-order.json"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def record_blocked(root, oid, reason):
    # Do not overwrite an existing song manifest or delete source-specific failure evidence.
    write_json(root / "songs" / f"{oid}.blocked.json", {
        "order_id": oid, "status": "blocked", "reason": reason,
        "stage": "canonical_order_gate",
    })


def rest(table, params):
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise GateError("private_order_store_not_configured")
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(ORIGIN + "/rest/v1/" + table + "?" + query,
        headers={"Authorization": "Bearer " + key, "apikey": key})
    try:
        # Origin and tables are fixed; only URL-encoded filters come from validated data.
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise ValueError("too large")
            rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError("not rows")
        return rows
    except Exception:
        raise GateError("private_order_store_unavailable") from None


def string_field(data, key, limit=4000):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise GateError("invalid_canonical_brief")
    return value.strip()


def canonical_order(oid, read=rest):
    rows = read(ORDER_TABLE, {"order_id": "eq." + oid,
        "select": "order_id,status,payment_state,payment_ref,brief,delivery,delivery_result", "limit": "2"})
    if len(rows) != 1 or rows[0].get("order_id") != oid:
        raise GateError("canonical_order_missing")
    row = rows[0]
    if (row.get("payment_state") != "verified_paid" or not isinstance(row.get("payment_ref"), str)
            or not row["payment_ref"].strip() or len(row["payment_ref"]) > 500):
        raise GateError("payment_not_verified")
    if row.get("status") != "source_received":
        raise GateError("sources_not_received")
    brief, delivery = row.get("brief"), row.get("delivery")
    if not isinstance(brief, dict) or not isinstance(delivery, dict):
        raise GateError("invalid_canonical_order")
    intake = string_field(brief, "intake", 20)
    if intake not in ("photos", "screenshot"):
        raise GateError("only_private_photo_recovery_enabled")
    uploads = read(UPLOAD_TABLE, {"order_id": "eq." + oid, "status": "eq.ready",
        "select": "order_id,object_path,status,byte_size,mime_type,sha256", "limit": "11"})
    if not 1 <= len(uploads) <= 10:
        raise GateError("photo_manifest_missing")
    paths, total_bytes = [], 0
    pattern = re.escape(oid) + r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(jpg|png|webp)"
    for item in uploads:
        path, count = item.get("object_path"), item.get("byte_size")
        if (item.get("order_id") != oid or item.get("status") != "ready" or
            not isinstance(path, str) or not re.fullmatch(pattern, path) or
            type(count) is not int or not 1 <= count <= 10 * 1024 * 1024 or
            item.get("mime_type") not in ("image/jpeg", "image/png", "image/webp") or
            not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", "")))):
            raise GateError("invalid_private_photo_manifest")
        total_bytes += count
        paths.append(path)
    if len(set(paths)) != len(paths) or total_bytes > 50 * 1024 * 1024:
        raise GateError("invalid_private_photo_manifest")
    email, phone = string_field(delivery, "email", 320), string_field(delivery, "sms_to", 20)
    email_valid = bool(email and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email))
    phone_valid = bool(phone and re.fullmatch(r"\+[1-9]\d{7,14}", phone))
    mode = string_field(delivery, "mode", 10)
    if not mode:
        mode = "email" if email and not phone else "sms" if phone and not email else ""
    delivery_valid = (mode == "email" and email_valid) or (mode == "sms" and phone_valid)
    return {"order_id": oid, "intake": intake, "photo_urls": sorted(paths),
        "details": string_field(brief, "details", 12000),
        "category": string_field(brief, "category", 80),
        "voice": string_field(brief, "voice", 80), "mood": string_field(brief, "mood", 80),
        "reference_url": string_field(brief, "reference_url", 2000),
        "delivery": mode, "email": email, "sms_to": phone,
        "consent_verified": delivery.get("consent_verified") is True,
        "delivery_valid": delivery_valid,
        "delivery_result": row.get("delivery_result") if isinstance(row.get("delivery_result"), dict) else {},
        "payment_ref": row["payment_ref"], "paid_status": "paid_verified"}


def prepare(root):
    oid = None
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        payload = event.get("client_payload", {})
        # Compatibility with nested dispatch does not trust the remaining payload.
        raw = payload.get("order_id") or (payload.get("order") or {}).get("order_id")
        oid = order_id(raw)
        output("order_id", oid)
        output("valid_order", "true")
        order = canonical_order(oid)
        for key in ("details", "email", "sms_to", "reference_url", "payment_ref"):
            mask(order.get(key))
        path = private_path()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(order, handle)
        output("ready", "true")
        print("Canonical paid order and private photo manifest verified.")
        return 0
    except (GateError, KeyError, TypeError, ValueError, OSError) as exc:
        reason = str(exc) if isinstance(exc, GateError) else "canonical_gate_failed"
        if oid:
            record_blocked(root, oid, reason)
        output("ready", "false")
        print("Order blocked: " + reason)
        return 1


def read_private():
    order = json.loads(private_path().read_text(encoding="utf-8"))
    order_id(order.get("order_id"))
    return order


def child_environment(order):
    env = os.environ.copy()
    mapping = {"ORDER_ID": "order_id", "INTAKE": "intake", "CATEGORY": "category",
        "MOOD": "mood", "VOICE": "voice", "DETAILS": "details", "DELIVERY": "delivery",
        "EMAIL": "email", "SMS_TO": "sms_to", "REFERENCE_URL": "reference_url", "PAID_STATUS": "paid_status"}
    env.update({key: str(order.get(field, "")) for key, field in mapping.items()})
    env["IG_URL"] = ""  # Recovery lane uses durable uploaded photos only.
    env["PHOTO_URLS"] = ",".join(order["photo_urls"])
    env["SUPABASE_URL"] = ORIGIN
    return env


def audio_ready(root, oid):
    try:
        manifest = json.loads((root / "songs" / f"{oid}.json").read_text())
        if manifest.get("order_id") != oid or manifest.get("status") not in ("audio_ready", "delivered"):
            return False
        return all((root / "songs" / name).is_file() and (root / "songs" / name).stat().st_size > 0
                   for name in (f"{oid}.mp3", f"{oid}-full.mp3"))
    except (OSError, ValueError, TypeError):
        return False


def forge(root, run=subprocess.run):
    order = read_private()
    oid = order["order_id"]
    if audio_ready(root, oid):
        output("audio_ready", "true")
        print("Existing complete audio retained; generation not repeated.")
        return 0
    completed = run([sys.executable, "scripts/forge_order.py"], cwd=root,
        env=child_environment(order), capture_output=True, text=True)
    # Do not print provider bodies, raw customer captions or contacts from legacy scripts.
    ready = audio_ready(root, oid)
    output("audio_ready", "true" if ready else "false")
    if completed.returncode != 0 or not ready:
        if not any((root / "songs" / (oid + suffix)).exists() for suffix in (".failed.json", ".blocked.json")):
            write_json(root / "songs" / f"{oid}.failed.json", {
                "order_id": oid, "status": "failed", "reason": "forge_failed_or_audio_incomplete"})
        print("Forge failed; failure evidence and any existing audio are retained.")
        return 1
    # Remove obsolete failure markers only after a complete, successful forge.
    for suffix in (".failed.json", ".blocked.json", ".claim.json"):
        (root / "songs" / (oid + suffix)).unlink(missing_ok=True)
    print("Audio ready; customer notification is a separate stage.")
    return 0


def persist_delivery(oid, result):
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise GateError("private_delivery_store_not_configured")
    request = urllib.request.Request(ORIGIN + "/rest/v1/" + ORDER_TABLE + "?" +
        urllib.parse.urlencode({"order_id": "eq." + oid}),
        data=json.dumps({"delivery_result": result}).encode(), method="PATCH",
        headers={"Authorization": "Bearer " + key, "apikey": key,
                 "Content-Type": "application/json", "Prefer": "return=representation"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            rows = json.loads(response.read(512 * 1024))
        if len(rows) != 1 or rows[0].get("order_id") != oid:
            raise ValueError("not persisted")
    except Exception:
        raise GateError("private_delivery_receipt_not_persisted") from None


def public_receipt(oid, result):
    status = result.get("delivery_status", "failed")
    if status not in ("pending", "submitted", "delivered", "failed"):
        status = "failed"
    safe = {"order_id": oid, "delivery_status": status}
    provider_status = result.get("provider_status", "")
    if isinstance(provider_status, str) and re.fullmatch(r"[a-z_]{1,40}", provider_status):
        safe["provider_status"] = provider_status
    if status == "failed":
        safe["reason"] = "notification_provider_failed"
    # Provider error bodies and contact details stay in the private database.
    return safe


def deliver(root, run=subprocess.run, persist=persist_delivery):
    order = read_private()
    oid = order["order_id"]
    if not audio_ready(root, oid):
        raise GateError("audio_not_ready_for_delivery")
    persisted = order.get("delivery_result", {})
    if persisted.get("delivery_status") in ("submitted", "delivered"):
        write_json(root / "songs" / f"{oid}.delivery.json", public_receipt(oid, persisted))
        print("Existing private provider receipt recovered; notification not repeated.")
        return 0
    if not order.get("consent_verified") or not order.get("delivery_valid"):
        write_json(root / "songs" / f"{oid}.delivery.json", {
            "order_id": oid, "status": "delivery_pending", "delivery_status": "pending",
            "reason": "consent_required" if not order.get("consent_verified") else "valid_recipient_required"})
        print("Audio retained; notification awaiting verified authorization.")
        return 1
    receipt = root / "songs" / f"{oid}.delivery.json"
    try:
        existing = json.loads(receipt.read_text())
    except (OSError, ValueError):
        existing = {}
    if existing.get("delivery_status") in ("submitted", "delivered"):
        print("Notification already submitted; retry not duplicated.")
        return 0
    completed = run([sys.executable, "scripts/deliver_song.py"], cwd=root,
        env=child_environment(order), capture_output=True, text=True)
    try:
        result = json.loads(receipt.read_text())
    except (OSError, ValueError):
        result = {}
    if not result:
        result = {"order_id": oid, "delivery_status": "failed", "reason": "notification_failed_or_receipt_missing"}
    private_saved = False
    try:
        persist(oid, result)
        private_saved = True
    except GateError:
        pass
    safe = public_receipt(oid, result)
    if not private_saved:
        safe["reason"] = "private_notification_receipt_pending"
    write_json(receipt, safe)
    if not private_saved or completed.returncode != 0 or result.get("delivery_status") not in ("submitted", "delivered"):
        print("Notification or receipt persistence failed; generated audio remains available.")
        return 1
    print("Notification provider receipt recorded; submitted is not inbox delivery.")
    return 0


def git(root, args, check=True):
    return subprocess.run(["git", *args], cwd=root, check=check, text=True, capture_output=True)


def publish(root, oid):
    oid = order_id(oid)
    # A dependency/install failure can skip forge before its own failure handler runs.
    if (os.environ.get("ORDER_PREPARED") == "true" and
            os.environ.get("FORGE_OUTCOME") in ("skipped", "cancelled", "failure") and
            not audio_ready(root, oid) and
            not any((root / "songs" / (oid + suffix)).exists() for suffix in (".failed.json", ".blocked.json"))):
        write_json(root / "songs" / f"{oid}.failed.json", {
            "order_id": oid, "status": "failed", "reason": "workflow_preparation_or_forge_failed"})
    # No git add songs/: explicit order-scoped allowlist prevents unrelated files/PII from publishing.
    paths = ["songs/" + oid + suffix for suffix in PUBLIC_SUFFIXES]
    paths.append("songs/" + oid + ".claim.json")
    tracked = set(git(root, ["ls-files", "--", *paths]).stdout.splitlines())
    present = [p for p in paths if (root / p).exists() or p in tracked]
    if not present:
        print("No public order status artifact to publish.")
        return 0
    git(root, ["config", "user.name", "anthemsmith-forge"])
    git(root, ["config", "user.email", "forge@anthemsmith.com"])
    git(root, ["add", "-A", "--", *present])
    changed = git(root, ["diff", "--cached", "--quiet"], check=False).returncode
    if changed not in (0, 1):
        raise GateError("status_diff_failed")
    if changed == 0:
        print("No order artifact changes.")
        return 0
    git(root, ["commit", "-m", "order status " + oid])
    for _ in range(3):
        if git(root, ["push", "origin", "HEAD:main"], check=False).returncode == 0:
            print("Order audio/status committed and pushed.")
            return 0
        git(root, ["fetch", "origin", "main"])
        if git(root, ["rebase", "origin/main"], check=False).returncode:
            git(root, ["rebase", "--abort"], check=False)
            raise GateError("status_push_conflict")
    raise GateError("status_push_failed")


def main():
    root = Path.cwd()
    command = sys.argv[1]
    try:
        if command == "prepare":
            return prepare(root)
        if command == "forge":
            return forge(root)
        if command == "deliver":
            return deliver(root)
        if command == "publish":
            return publish(root, os.environ["ORDER_ID"])
        if command == "cleanup":
            private_path().unlink(missing_ok=True)
            return 0
        raise GateError("unknown_workflow_command")
    except (GateError, OSError, ValueError, subprocess.SubprocessError, KeyError):
        print("Workflow stage failed safely; inspect order status and runner stage outcome.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
