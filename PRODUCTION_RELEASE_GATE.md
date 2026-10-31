# Commercial Release Gate — EverAfter Studio

**Definition:** Do not advertise as a fully supported paid photo-booth platform until every critical gate is passed with documented evidence.

- [ ] Public backend Web Service deployed on HTTPS with persistent database backups, validated restore and secure secrets; old static prototype not used as production backend.
- [ ] Production S3/R2 access configured with least-privilege access, image retention, failure notifications, monitoring and privacy/deletion process.
- [ ] Real bride, groom and guest consent, age/guardian rules, operator terms, privacy notice and image rights review.
- [ ] At least 50 representative images reviewed (hair edges, veils, dark tuxedos, complex outfits, dim reception lighting, multiple guests). Quality and failure fallback documented.
- [ ] Actual MediaPipe AI cutout loads on Safari iPhone/iPad and desired booth kiosk browser, without model/CDN failures.
- [ ] Documented user-facing indication images are composited, not original posed photographs.
- [ ] Device camera permissions verified on exact host domain; camera swap, rotation and front/back orientation.
- [ ] Physical 4×6 printer, printer driver, paper format, margins and unattended queue tested using PrintNode; paper-out/queue-failure fallback available.
- [ ] Resend verified domain and actual guest delivery tests, link expiry and email abuse controls.
- [ ] Stripe test-mode Checkout session, positive/negative webhook signature, replay events, refund/cancellation policy, merchant taxes and live account verified before charge.
- [ ] Automated backups, observability, outages/retries, live-event load testing, accessibility and third-party security review.
- [ ] B2B purchase terms, reseller license, support scope, usage/overage policy and operator training completed.
- [ ] Photo-booth company accepts pilot sign-off. Publish sales claims only for verified items.
