#!/usr/bin/env python3
"""Recovery-only workflow boundary. Dispatch input is an order ID, never proof of payment.

The public relay is disabled. Only service-owned recovery records may supply payment,
source, brief and delivery data. No customer payload is interpolated into a shell or
exported as a GitHub Actions step environment variable.
"""
import json
import os
import hashlib
import uuid
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
PUBLIC_SUFFIXES = (".json", ".blocked.json", ".failed.json", ".delivery.json")
import private_audio as pa


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


class NoStoreRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise GateError("private_store_redirect_refused")


def store_open(request, timeout=30):
    return urllib.request.build_opener(NoStoreRedirect()).open(request, timeout=timeout)


def rest(table, params):
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise GateError("private_order_store_not_configured")
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(ORIGIN + "/rest/v1/" + table + "?" + query,
        headers={"Authorization": "Bearer " + key, "apikey": key})
    try:
        # Origin and tables are fixed; only URL-encoded filters come from validated data.
        with store_open(request, timeout=30) as response:
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
    payments = read(ORDER_TABLE, {"payment_ref": "eq." + row["payment_ref"],
        "payment_state": "eq.verified_paid", "select": "order_id", "limit": "2"})
    if len(payments) != 1 or payments[0].get("order_id") != oid:
        raise GateError("payment_reference_reused_or_unverifiable")
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
    delivery_valid = ((mode == "email" and email_valid and not phone) or
                      (mode == "sms" and phone_valid and not email))
    return {"order_id": oid, "intake": intake, "photo_urls": sorted(paths),
        "details": string_field(brief, "details", 12000),
        "category": string_field(brief, "category", 80),
        "voice": string_field(brief, "voice", 80), "mood": string_field(brief, "mood", 80),
        "reference_url": string_field(brief, "reference_url", 2000),
        "delivery": mode, "email": email, "sms_to": phone,
        "consent_verified": delivery.get("consent_verified") is True,
        "delivery_valid": delivery_valid,
        "delivery_result": row.get("delivery_result") if isinstance(row.get("delivery_result"), dict) else {},
        "public_audio_consent": brief.get("public_audio_consent") is True,
        "payment_ref": row["payment_ref"], "paid_status": "paid_verified"}


def prepare(root):
    oid = None
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        payload = event.get("client_payload", {})
        # Compatibility with nested dispatch does not trust the remaining payload.
        raw = payload.get("order_id") or (payload.get("order") or {}).get("order_id") or (event.get("inputs") or {}).get("order_id")
        oid = order_id(raw)
        output("order_id", oid)
        output("valid_order", "true")
        order = canonical_order(oid)
        for key in ("details", "email", "sms_to", "reference_url", "payment_ref"):
            mask(order.get(key))
        path = private_path()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = None
                json.dump(order, handle)
        finally:
            if fd is not None:
                os.close(fd)
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
        for suffix, lower, upper in ((".mp3", 1, 60), ("-full.mp3", 180, 240)):
            path = root / "songs" / (oid + suffix)
            if not path.is_file() or path.stat().st_size == 0:
                return False
            checked = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                "-of", "json", str(path)], capture_output=True, text=True, timeout=25)
            data = json.loads(checked.stdout)
            if (checked.returncode or not any(s.get("codec_type") == "audio" for s in data.get("streams", []))
                    or not lower <= float(data.get("format", {}).get("duration", 0)) <= upper):
                return False
            # VBR/Xing metadata can survive truncation; count actually decoded samples.
            decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn",
                "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True, timeout=35)
            decoded_seconds = len(decoded.stdout) / 16000
            if decoded.returncode or not lower <= decoded_seconds <= upper:
                return False
        return True
    except subprocess.SubprocessError:
        return False
    except (OSError, ValueError, TypeError):
        return False


def stage_path(root, oid):
    pa.oid_check(oid)
    base = Path(os.environ['RUNNER_TEMP']).resolve()
    if base == root.resolve() or root.resolve() in base.parents:
        raise GateError('private_staging_must_be_outside_checkout')
    stage = base / 'anthemsmith-private' / oid
    stage.mkdir(parents=True, exist_ok=True, mode=0o700)
    return stage


def forge(root, run=subprocess.run):
    order = read_private()
    oid = order['order_id']
    output('audio_ready', 'false')
    try:
        pa.require_config()
        # This check precedes local-file inspection and every chargeable call.
        manifest, context, row = pa.read_state(oid)
        if manifest:
            pa.verify_edge(oid, manifest, context)
        else:
            if row['delivery_result']:
                raise GateError('operator_notification_reconciliation_required')
            # Old public outputs are evidence, not permission to generate again.
            if any((root / 'songs' / (oid + s)).exists() for s in
                   ('.mp3', '-full.mp3', '.json', '.claim.json')):
                raise GateError('legacy_audio_reconciliation_required')
            stage = stage_path(root, oid)
            full = stage / 'songs' / (oid + '-full.mp3')
            job = row['delivery'].get('private_audio_job')
            if not full.exists():
                pa.output_hosts()  # Fail before spend when download hosts are unreviewed.
                if job and job.get('status') != 'preflight_failed':
                    pa.resume_generation(oid, full)  # GET/poll only; never submit again.
                else:
                    # Revalidate canonical source/payment immediately before reserving spend.
                    order = canonical_order(oid)
                    job = pa.reserve_generation(oid)
                    env = child_environment(order)
                    env.update(AS_PRIVATE_RECOVERY='1', AS_GENERATION_ATTEMPT=job['attempt_id'])
                    completed = run([sys.executable, str(root / 'scripts' / 'forge_order.py')],
                        cwd=stage, env=env, capture_output=True, text=True)
                    # Child stdout/stderr may contain source material. Never forward them.
                    if completed.returncode:
                        current = pa.snapshot(oid)['delivery'].get('private_audio_job')
                        if current == job and current.get('status') == 'reserved':
                            # Confirmed child exit, no submitting CAS: safe preflight retry.
                            pa.job_transition(oid, job, {**job, 'status': 'preflight_failed'})
                        raise GateError('forge_failed_or_audio_incomplete')
            elif not job:
                raise GateError('unbound_staged_audio_reconciliation_required')
            manifest, context = pa.register_audio(oid, full)
        write_json(root / 'songs' / (oid + '.json'), {'order_id': oid,
            'status': 'audio_ready', 'private_audio': True,
            'duration_full': manifest['duration_seconds']})
        for suffix in ('.failed.json', '.blocked.json'):
            (root / 'songs' / (oid + suffix)).unlink(missing_ok=True)
        output('audio_ready', 'true')
        print('Private audio and customer readback verified; notification remains separate.')
        return 0
    except (pa.PrivateAudioError, GateError) as exc:
        record_blocked(root, oid, str(exc))
        print('Private forge held: ' + str(exc))
        return 1


def persist_delivery(oid, result, expected=None):
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise GateError("private_delivery_store_not_configured")
    params = {"order_id": "eq." + oid}
    if expected is not None:
        params["delivery_result"] = "eq." + json.dumps(expected, separators=(",", ":"))
    request = urllib.request.Request(ORIGIN + "/rest/v1/" + ORDER_TABLE + "?" +
        urllib.parse.urlencode(params),
        data=json.dumps({"delivery_result": result}).encode(), method="PATCH",
        headers={"Authorization": "Bearer " + key, "apikey": key,
                 "Content-Type": "application/json", "Prefer": "return=representation"})
    try:
        with store_open(request, timeout=30) as response:
            rows = json.loads(response.read(512 * 1024))
        if (len(rows) != 1 or rows[0].get("order_id") != oid or
                rows[0].get("delivery_result") != result):
            raise ValueError("not persisted")
        check = rest(ORDER_TABLE, {"order_id": "eq." + oid, "select": "order_id,delivery_result", "limit": "2"})
        if len(check) != 1 or check[0].get("delivery_result") != result:
            raise ValueError("readback mismatch")
    except Exception:
        raise GateError("private_delivery_receipt_not_persisted") from None


def public_receipt(oid, result):
    status = result.get("delivery_status", "failed")
    if status in ("sending", "unknown"):
        status = "pending"
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


def private_delivery(oid):
    rows = rest(ORDER_TABLE, {"order_id": "eq." + oid, "select": "order_id,delivery_result", "limit": "2"})
    if len(rows) != 1 or rows[0].get("order_id") != oid or not isinstance(rows[0].get("delivery_result"), dict):
        raise GateError("private_delivery_state_missing")
    return rows[0]["delivery_result"]


def reserve_notification(oid, attempt, expected_delivery=None):
    """Atomic empty-outbox -> sending. Never take over an ambiguous send."""
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not key:
        raise GateError("private_delivery_store_not_configured")
    proposed = {"order_id": oid, "delivery_status": "sending", "attempt_id": attempt}
    filters = {"order_id": "eq." + oid, "delivery_result": "eq.{}"}
    if expected_delivery is not None:
        filters.update(delivery="eq." + json.dumps(expected_delivery, separators=(",", ":")),
                       status="eq.source_received", payment_state="eq.verified_paid")
    query = urllib.parse.urlencode(filters)
    request = urllib.request.Request(ORIGIN + "/rest/v1/" + ORDER_TABLE + "?" + query,
        method="PATCH", data=json.dumps({"delivery_result": proposed}).encode(), headers={
            "Authorization": "Bearer " + key, "apikey": key, "Content-Type": "application/json",
            "Prefer": "return=representation"})
    try:
        with store_open(request, timeout=30) as response:
            rows = json.loads(response.read(512 * 1024))
        if len(rows) != 1 or rows[0].get("delivery_result") != proposed:
            raise ValueError("not reserved")
        if private_delivery(oid) != proposed:
            raise ValueError("reservation readback")
        return proposed
    except Exception:
        raise GateError("notification_reservation_unverified") from None


def load_private_access(order):
    try:
        manifest, context, row = pa.read_state(order['order_id'])
        if not manifest:
            raise pa.PrivateAudioError('private_audio_not_registered')
        d = row['delivery']
        mode = d.get('mode') or ('email' if d.get('email') and not d.get('sms_to') else
                                 'sms' if d.get('sms_to') and not d.get('email') else '')
        if (mode != order.get('delivery') or
                any(d.get(k, '').strip() != order.get(k, '') for k in ('email', 'sms_to')) or
                (d.get('consent_verified') is True) != order.get('consent_verified')):
            raise pa.PrivateAudioError('private_audio_recipient_changed')
        return context, d
    except pa.PrivateAudioError as exc:
        raise GateError(str(exc)) from None


def verify_served_audio(root, oid):
    """Use the real bearer-header customer route, never GitHub/raw/public audio."""
    try:
        manifest, context, _ = pa.read_state(oid)
        if not manifest:
            raise pa.PrivateAudioError('private_audio_not_registered')
        return pa.verify_edge(oid, manifest, context)
    except pa.PrivateAudioError as exc:
        raise GateError(str(exc)) from None


def deliver(root, send=None, lookup=None, persist=None, read=None, reserve=None, verify=verify_served_audio):
    from notification_provider import send as provider_send, lookup as provider_lookup, NotificationError
    send, lookup = send or provider_send, lookup or provider_lookup
    read = read or private_delivery
    order = read_private()
    oid = order["order_id"]
    context, delivery_context = load_private_access(order)
    order = {**order, "private_song_access": context}
    reserve = reserve or (lambda oid, attempt: reserve_notification(oid, attempt, delivery_context))
    path = root / "songs" / f"{oid}.delivery.json"
    persisted = read(oid)  # Fresh private state, never a stale runner copy or public JSON.
    persist = persist or (lambda oid, result: persist_delivery(oid, result, persisted))
    state = persisted.get("delivery_status")
    if state == "delivered":
        write_json(path, public_receipt(oid, persisted))
        return 0
    if state == "submitted":
        try:
            updated = lookup(order, persisted)
            persist(oid, updated)
        except (NotificationError, GateError):
            safe = public_receipt(oid, persisted)
            safe["reason"] = "provider_status_check_pending"
            write_json(path, safe)
            return 1
        write_json(path, public_receipt(oid, updated))
        return 0 if updated.get("delivery_status") in ("submitted", "delivered") else 1
    if state in ("sending", "unknown", "failed") or persisted:
        # Unknown POST completion or failed receipt write is NOT permission to resend.
        safe = public_receipt(oid, persisted)
        safe["reason"] = "operator_notification_reconciliation_required"
        write_json(path, safe)
        return 1
    if not order.get("consent_verified") or not order.get("delivery_valid"):
        write_json(path, {"order_id": oid, "delivery_status": "pending",
            "reason": "consent_required" if not order.get("consent_verified") else "valid_recipient_required"})
        return 1
    try:
        public = json.loads(path.read_text())
    except (OSError, ValueError):
        public = {}
    if (public.get("delivery_status") in ("submitted", "delivered") or
            public.get("reason") == "private_receipt_reconciliation_required"):
        # Keep this fence durable across every retry; never erase potential acceptance.
        write_json(path, {"order_id": oid, "delivery_status": "pending",
            "reason": "private_receipt_reconciliation_required"})
        return 1
    verify(root, oid)
    attempt = str(uuid.uuid4())
    reserved = reserve(oid, attempt)  # Durable compare-and-set before the first provider side effect.
    # Closure above now CASes against our sending reservation, not the old empty row.
    persisted = reserved if isinstance(reserved, dict) else {"order_id": oid, "delivery_status": "sending", "attempt_id": attempt}
    try:
        result = send(order, attempt)
    except NotificationError as exc:
        result = {"order_id": oid, "attempt_id": attempt,
                  "delivery_status": "unknown" if exc.ambiguous else "failed", "reason": str(exc)}
    try:
        persist(oid, result)
    except GateError:
        write_json(path, {"order_id": oid, "delivery_status": "pending",
                         "reason": "private_notification_receipt_pending"})
        return 1
    write_json(path, public_receipt(oid, result))
    return 0 if result.get("delivery_status") in ("submitted", "delivered") else 1



def git(root, args, check=True):
    return subprocess.run(["git", *args], cwd=root, check=check, text=True, capture_output=True)


def sanitize_public_artifacts(root, oid):
    """Allowlist every public JSON even if a legacy child crashed mid-write."""
    for suffix in ('.json', '.blocked.json', '.failed.json', '.delivery.json'):
        path = root / 'songs' / (oid + suffix)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            raise GateError('invalid_public_artifact') from None
        if suffix == '.delivery.json':
            safe = public_receipt(oid, data)
            reason = data.get('reason', '')
            if reason in ('consent_required', 'valid_recipient_required', 'provider_status_check_pending',
                          'operator_notification_reconciliation_required', 'private_receipt_reconciliation_required',
                          'private_notification_receipt_pending'):
                safe['reason'] = reason
        elif suffix == '.json':
            safe = {'order_id': oid, 'status': 'audio_ready'}
            if data.get('status') not in ('audio_ready', 'delivered'):
                raise GateError('invalid_public_audio_status')
            duration = data.get('duration_full')
            if isinstance(duration, (int, float)) and 0 < duration <= 3600:
                safe['duration_full'] = duration
            if data.get('private_audio') is True:
                safe['private_audio'] = True
            if type(data.get('grounded_photo_lyrics')) is bool:
                safe['grounded_photo_lyrics'] = data['grounded_photo_lyrics']
            for key in ('sha256_full', 'sha256_preview'):
                if re.fullmatch(r'[a-f0-9]{64}', str(data.get(key, ''))):
                    safe[key] = data[key]
        else:
            reason = data.get('reason', '')
            if not isinstance(reason, str) or not re.fullmatch(r'[a-z_]{1,100}', reason):
                reason = 'order_processing_failed'
            safe = {'order_id': oid, 'status': 'blocked' if suffix == '.blocked.json' else 'failed', 'reason': reason}
        write_json(path, safe)


def publish(root, oid):
    oid = order_id(oid)
    sanitize_public_artifacts(root, oid)
    # A dependency/install failure can skip forge before its own failure handler runs.
    if (os.environ.get("ORDER_PREPARED") == "true" and
            os.environ.get("FORGE_OUTCOME") in ("skipped", "cancelled", "failure") and
            not audio_ready(root, oid) and
            not any((root / "songs" / (oid + suffix)).exists() for suffix in (".failed.json", ".blocked.json"))):
        write_json(root / "songs" / f"{oid}.failed.json", {
            "order_id": oid, "status": "failed", "reason": "workflow_preparation_or_forge_failed"})
    # No git add songs/: explicit order-scoped allowlist prevents unrelated files/PII from publishing.
    paths = ["songs/" + oid + suffix for suffix in PUBLIC_SUFFIXES]
    staged = set(git(root, ["diff", "--cached", "--name-only"]).stdout.splitlines())
    if staged - set(paths):
        raise GateError("unexpected_staged_public_files")
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
            print("Allowlisted order metadata committed and pushed; no audio published.")
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
    except (GateError, pa.PrivateAudioError, OSError, ValueError, subprocess.SubprocessError, KeyError):
        print("Workflow stage failed safely; inspect order status and runner stage outcome.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
