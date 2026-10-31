# EverAfter Studio — Partner Platform v2.0

> **Status: commercial pilot / integration candidate, not an independently certified production release.** This repository contains an implementation of the guest booth, operator events, basic compositing, event galleries, QR links, booking inquiries, payment checkout endpoints, partner API and print/email adapters. The external vendors, physical camera/printer and photorealistic quality still require live credential and hardware verification.

## Core workflows

**Guest booth:** Operator creates an event, uploads 1–3 real couple poses with space for the guest, shares `/?booth=EVENT_CODE` or installs the iframe in a kiosk. Guests select a pose; capture a half- or full-body photo on the booth camera or upload a photo; remove the background with MediaPipe (internet required on first load) or the simple-backdrop fallback; drag, scale and light-match the guest; add more guests; consent; download a 4×6 (1200×1800) JPG, print, or save to a private gallery and display its QR. Never exports a rectangular guest selfie.

**Operator studio:** A partner creates a login, manages event separation, uploads original couple portraits, views and deletes its photo gallery, exports event galleries, creates integration credentials, discovers and selects a PrintNode printer, and gets an event-specific booth URL.

**Booking and deposit:** Partner shares `/?book=OPERATOR_ID`, guest submits an inquiry; partner reviews it and generates a Stripe Checkout deposit link (only after Stripe is configured), then shares that link. Stripe webhook verifies signature and records successful paid deposits. Inquiries are **not** confirmed reservations; live availability calendars, merchant onboarding, refunds, tax invoices and payout splitting are outside this build.

## Quick start on a local computer

Python 3.11+ (tested under Python 3.13) is required.

```bash
python -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # Optional; set production values via hosting secrets
export APP_ENV=development
export APP_BASE_URL=http://127.0.0.1:8000
uvicorn server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` and sign up as the first operator. In local development you can preview the interface and create test events without vendor credentials. **Never use these default settings on the public internet.**

## Deploy to Render (new Web Service, NOT existing Static Site)

1. Create a **new private GitHub repository** `everafter-studio-v2` and upload this package's source files at repository root (not the ZIP itself). Avoid committing `.env`, photo assets, database files or credentials.
2. In Render, create **Web Service** using that repository. Runtime `Python`, region near users (Virginia), branch `main`.
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn server:app --host 0.0.0.0 --port $PORT`
5. Set `APP_ENV=production`, `APP_BASE_URL=https://your-new-site.onrender.com`, `APP_SECRET=` a cryptographically random 64+ character secret, and `ENABLE_SIGNUP=true` only during approved onboarding (change to false after initial operator account if you don't intend open registration).
6. **Persistent state is mandatory.** This app uses SQLite. Mount a Render persistent disk and set `DATA_DIR` to the mounted directory (for example `/var/data`). Render plans without a disk have **ephemeral storage**, and would lose vendor accounts, events and bookings on redeployment. For beta scale, run only one app instance. Back up the DB; use a managed transactional DB before multi-instance expansion.
7. Optionally store image bytes in Cloudflare R2 or S3 by configuring `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`. **An S3 bucket does not replace the SQLite persistent disk.**
8. Verify `/api/health`, account creation, event setup, photo QR and gallery, consent and deletion on the *live site*, plus Stripe webhook, Resend, and PrintNode integrations before customer launch.
9. Do not replace the existing `everafter-photo-booth` static site until the new backend is working. After certification, route the customer domain to the new service and preserve a rollback link.

## Vendor setup

- **AI person cutout:** Browser downloads MediaPipe Selfie Segmentation. CDN must be reachable from the booth device; auto-cutout cannot be guaranteed on offline-only hardware. MediaPipe quality depends on lighting, hair, pose, clothing and background. The blending is an **edge-masked, brightness/warmth-adjustable composite**, not a generative photorealistic re-render. Realism is not guaranteed.
- **QR delivery:** Generated on the server after photo storage; guest links contain hard-to-guess access tokens; protect them from reposting. Uses event retention days, default 30. Call the maintenance endpoint on a schedule to actually delete old files.
- **Email:** Configure `RESEND_API_KEY` and `RESEND_FROM` with a verified sender domain. Email contains a private gallery link, not image bytes. Test actual inbox delivery, opt-in language and spam filters.
- **Payments:** Configure `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` and the webhook destination `https://your-site/api/stripe/webhook`; subscribe to `checkout.session.completed` and `checkout.session.async_payment_succeeded`. Use Stripe **test keys** until pricing, refund policy and merchant account review are completed. No refunds, invoices, subscriptions or revenue-sharing/marketplace split implemented.
- **Printing:** Install PrintNode Client on the computer connected to the printer; set `PRINTNODE_API_KEY`, sign in to studio, navigate Integrations → Discover PrintNode printers → Save printer. The booth prints a 4×6 portrait as PDF. Calibrate margins, paper orientation and device driver; test paper-out, duplicates, queue failures and unplugged printer. Browser Print is a manual fallback.
- **Embedding:** `https://your-site/?booth=EVENT_CODE&embed=1` with iframe `allow="camera"`. Set `PARTNER_FRAME_ANCESTORS=https://boothpartner.example` (space-separated **HTTPS** origins for multiple partner domains) to permit approved cross-origin embedding; the default security policy blocks cross-domain framing. Chrome and Safari must both grant camera permissions.
- **Partner API:** Generate and securely store an API key in Integrations. API keys are hashed in storage and returned only once. Create/list events using `X-API-Key` with `/api/v1/events`. Treat API keys as secrets, never paste them into public HTML.

## Security & privacy boundaries

- Guest must explicitly consent before download, saving or printing. The visual output is an edited/composited image; don't claim the group physically posed together.
- Retention is per event; deletion endpoints remove stored images, and the maintenance endpoint must be run regularly to enforce retention in storage.
- Signed login tokens expire in 12 hours; operator accounts are scoped to the creator. Client auth tokens are held in browser session storage. Passwords are PBKDF2 hashed (350,000 rounds).
- Photo download access requires unguessable random token; private operator gallery access requires authentication. Server validates image formats, max size and dimensions.
- Basic in-process rate limiting is included for photo creation, printing, emails and bookings. For multiple instances, use durable centralized rate limiting, monitoring, backup/restore drills, security review, audit logging, and abuse reporting.
- There is no automatic moderation of uploaded or generated images. Have an operator present; obtain necessary rights and consent from bride, groom and guests, including a parent/guardian for minors where required.
- The app has not undergone an independent security audit, accessibility audit, load test, service level test or high-volume event testing. Those are required before claiming professional event reliability.

## Implementation status (do not oversell)

| Capability | Included code | Confirmed testing | Remaining production dependency |
|---|---|---|---|
| Guest booth UX, portrait export | Yes | Desktop/mobile simulation, local download | Physical devices; better precision |
| Cutout and visual compositing | Yes | Plain-backdrop mode; mock object/composition tests | Live MediaPipe model, production quality/portrait realism |
| QR + private gallery | Yes | Real backend integration tested | Production hosting, storage, retention cron |
| Operator accounts and event management | Yes | API integration tested | Backups, operator policies and independent security testing |
| Email guest delivery | Provider adapter | Mock/unconfigured refusal | Resend API key, verified sender, live send |
| Remote printing | Provider adapter | Mock/unconfigured refusal | PrintNode account, exact printer and physical test |
| Booking requests | Yes | API integration tested | Customer fulfillment workflow and rate controls |
| Stripe deposit checkout | Code + verified webhook logic | Refusal when unconfigured | Stripe test/live credentials, real payment test, refunds |
| Partner embed, partner API | Yes | API tests, UI simulations | Approved iframe domains, third-party kiosk integration |
| Full generative photorealistic AI | No | No | Separate inference model, quality thresholds, pricing, consent, human review |
| Live person-segmented AR video | No | Camera guide only | Device-specific performance build |

## API quick reference

Authenticated bearer session: `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/operator/me`.
Events: `POST/GET /api/events`, `POST /api/events/{id}/poses/{formal|fun|party}`, `GET /api/events/{id}/photos`, `GET /api/events/{id}/export.zip`, deletion endpoints.
Public booth: `GET /api/booth/{code}`, `POST /api/booth/{code}/photos`, `GET /api/gallery/{id}/qr?t=TOKEN`, `GET /api/gallery/{id}/image?t=TOKEN`.
Delivery/print: `POST /api/gallery/{id}/email`, `GET /api/print/printers`, `POST /api/operator/printer`, `POST /api/print/{code}`.
Bookings: `POST /api/bookings`, `GET /api/bookings`, `POST /api/bookings/{id}/checkout`, `POST /api/stripe/webhook`.
Integrations: `POST /api/operator/partner-key`, `GET /api/operator/partner-keys`, `DELETE /api/operator/partner-key/{id}`, `POST/GET /api/v1/events` with `X-API-Key`.
Maintenance: `POST /api/maintenance/expire` with `X-Maintenance-Key: APP_SECRET` from a scheduled private job. Avoid public scheduled GET requests for deletion.

## Tests

```
python tests/test_api.py
python tests/test_browser.py
python tests/test_guest_flow.py
```

The API tests assume the server is listening on port `8765`. The browser tests use system Chromium and simulate mobile/desktop; they are not equivalent to physical iPhone or printed photo verification. No live vendor payments, cloud credentials, email, AI model inference or printer API have been exercised.
