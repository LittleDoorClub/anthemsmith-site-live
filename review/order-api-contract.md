# AnthemSmith private order API v2

Deployment owner: OX, coordinated with Harrier. This document describes the API needed by `order-client.js`; it is a deployment contract, not proof that the API exists.

Set `window.ANTHEMSMITH_API_BASE` in `order-config.js` to the approved HTTPS API base, or proxy `/api` at the site origin. Permit CORS only for the actual storefront origin and approved preview origins. Never expose provider credentials or storage service keys.

## Customer flow

1. Save the entire brief, delivery contact and selected file bytes privately.
2. Validate/ingest the request, asking for more context before payment when needed.
3. Open a Stripe Checkout Session bound to that durable order ID.
4. A verified payment event marks the order paid and queues one generation.
5. Confirmation and song-ready messages are separate, durable notification jobs.
6. A private order link works after closing the tab or opening another device.

The frontend has no public relay and no “I've paid” authorization. Do not deploy a backend that restores either behavior. Payment creation is not payment confirmation.

## POST /orders

`multipart/form-data`: one `request` field containing JSON, plus repeated `files` fields containing the actual JPEG/PNG/WebP bytes. Do not JSON-serialize File objects. Maximum 2 screenshots or 10 photos; each <=15 MiB, combined <=50 MiB. Link input carries 1–2 normalized public Instagram profile URLs.

`Idempotency-Key`: random UUID stable across retries of an identical request and file content. Same key + different content must return 409. Same key + same content must recover the same order and recovery token, never create duplicate work. Store token encrypted or derive securely; storing only a hash without a retry-safe recovery mechanism is insufficient for an interrupted creation response.

```json
{
  "schema_version": 2,
  "intake": "photos",
  "instagram_urls": [],
  "captions": "Cake collapsed. Confidence did not.",
  "details": "Our best friend's birthday.",
  "names": "Maya",
  "category": "Funny",
  "mood": "Inside jokes",
  "voice": "Female",
  "reference": {"url":"", "artist":"", "song":"", "genre":"Pop"},
  "delivery": {"method":"email", "address":"customer@example.com"},
  "consent": true,
  "files": [{"name":"birthday.jpg","type":"image/jpeg","size":12345,"last_modified":0}]
}
```

Validate on the server: field types and lengths, allowed categories/moods/voices, consent, contact, file count/bytes/content signatures and actual image decode. Names are display text, not paths. Generate storage names independently. Do not allow SVG or arbitrary URL fetching. Both Instagram profiles, every caption, uploaded image, names, mood, and musical reference must reach the pipeline or produce an explicit recoverable unsupported-input response before charging. Reference handling must derive musical characteristics, not copy a recording, melody, lyric or voice.

Response:

```json
{"order_id":"AS-<random UUID>","recovery_token":"<at least 32 cryptographically random URL-safe characters>","status":"draft"}
```

No phone/email/source content in that response. Serve `Cache-Control: no-store`. Unavailable source data yields `needs_input`, not fabricated lyrics or a charge. Persist selected photos until the order's stated retention limit; temporary browser previews are not an upload.

## Order authentication

All following customer calls require `Authorization: Bearer <recovery_token>` scoped to the specified order. Compare hashes in constant time. Invalid IDs/tokens get a uniform non-disclosing response. Rate-limit intake, status and recovery; redact credentials and contact from logs. Recovery links put the bearer token in the URL fragment, not a query string. Do not use predictable IDs as authentication.

## POST /orders/{id}/checkout

`Idempotency-Key: <order_id>-checkout`. Verify the request is complete, delivery is valid and all uploads are privately stored. Create/reuse an unpaid Checkout Session; use server price/currency/product configuration (USD 500 cents), not a browser price. Omit explicit `payment_method_types`. Bind both `client_reference_id` and session metadata `order_id`. Pass the existing email when chosen so the customer need not type it twice. Do not claim checkout email is optional if the hosted product requires it.

Set actual server-side `success_url` and `cancel_url`, for example `https://anthemsmith.com/?order={order_id}`. Return `{"checkout_url":"https://checkout.stripe.com/..."}`. If already paid, return a conflict/status response and never open another charge. Payment Link `success_url` query parameters do not configure its return behavior.

Cash App/Venmo must use supported verified payment methods or an explicit manual reconciliation state. A browser assertion does not verify them.

## Stripe webhook

Verify the raw-body Stripe signature with the correct endpoint secret and timestamp tolerance. Check live/test mode, account context, allowed price/currency/amount, successful payment status, and the linked order. Handle `checkout.session.completed` and asynchronous payment success/failure correctly. Persist event IDs transactionally and make the paid transition/generation job unique per order. An unpaid completion event must not generate. Never rely on a return page hit to fulfill an order. Repeated events, retries and refreshes must not regenerate or charge twice. Reconcile older paid sessions separately; do not invent their association with an order.

## GET /orders/{id}

Authenticated response contains order_id, status, a safe customer-facing message, and optionally a short-lived signed `audio_url` and `notification_status`. Never return internal diagnostics, keys or source content.

Statuses supported by the client: `draft`, `awaiting_payment`, `paid`, `queued`, `processing`, `generating`, `needs_input`, `ready`, `failed`, `cancelled`, `refunded`.

Notification states are separate: `pending`, `queued`, `delivered`, `failed`. Provider acceptance/queued does not equal carrier delivery. Retain the provider ID, validate status callbacks and retry bounded transient errors. “Ready” means a verified decodable audio asset exists, not that a text arrived.

## PATCH /orders/{id}/details

JSON `{ "details": "customer-supplied additional context" }`, at most 12,000 characters. Preserve the existing order and payment. Append context safely and transition a needs-input order back to draft (unpaid) or queued (already paid). Queue at most one job. Never charge again to resolve missing context.

## Worker requirements

Load the entire private order by ID. Do not relay customer data or media through public ntfy/GitHub logs. Preserve source-backed lyric checks. If Instagram is unavailable, ask for uploaded screenshots/captions; no authenticated scraping or access-control bypass. Use the existing Harrier pipeline's image/reference processing where available rather than dropping fields. Capture errors into the private order status even if generation fails. Store output privately and return signed download links. Use claim/lease idempotency and a generation-provider request ID to recover after a worker crash without submitting again.

## Deployment gates and rollback

Do not deploy only the client while leaving an unimplemented API. Test exact frontend + API version together with sandbox Stripe events and mocked generation, then use one authorized real-surface delivery check without another customer charge. Back up the current commit/config and database before additive schema changes; provide an explicit rollback that leaves paid orders intact. Keep public checkout unavailable if backend validation/payment verification is not ready. Do not restore the unsafe relay as a fallback.

Acceptance: public link without `www`; two profiles; screenshot + caption; ten photos; music URL/artist/title/genre; SMS and email; missing/invalid contact correction; same request retry; cancelled payment; paid return; return without local storage using private link; duplicate/unpaid/bad-signature webhook; unreadable Instagram; needs-input recovery; failed generation; failed notification; signed private download; no client credentials or customer data in public logs.
