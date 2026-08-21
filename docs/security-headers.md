# Security header deployment

RockyGPT sends the security headers from `next.config.ts` on every application response. CSP is
**enforced by default**, including production builds. The policy allows the resources the current
application needs: same-origin scripts and API calls, Next.js inline bootstrap/styles, data-driven
HTTPS images, and the Ramapo campus-map frame. It denies plugins, embedding RockyGPT in another
site, and all other frame origins.

## CSP rollout workflow

Use a report-only deployment when changing the CSP or adding a browser resource dependency:

1. Set `ROCKY_CSP_MODE=report-only` in a preview environment and redeploy. Optionally set
   `ROCKY_CSP_REPORT_URI` to an institution-approved HTTPS collector. Do not send reports to an
   unreviewed third party: reports can contain page and blocked-resource URLs.
2. Exercise the primary chat flow, feedback, quick-access modals, campus map, privacy page, and
   dev-only tools that the target environment exposes. Review the collector and browser console.
3. Run `npx tsx scripts/verify-security-headers.ts https://preview.example.edu --mode=report-only`.
   The check validates the response headers, exercises the menu, student-organization, and campus
   map flows, and fails on browser `securitypolicyviolation` events.
4. Fix the policy or application until the exercised flows produce no violations.
5. Remove `ROCKY_CSP_MODE` (or set it to `enforce`), redeploy, and run the same command against the
   deployed URL with `--mode=enforce`.

`ROCKY_CSP_REPORT_URI` accepts only a root-relative path or HTTPS URL. It adds the legacy,
widely-supported `report-uri` directive; the receiving service and its retention policy are
deployment-owned. Invalid CSP modes or report URLs fail configuration loading rather than silently
disabling enforcement.

## Local verification

Build and start the production server, then run the checks from another terminal:

```bash
npm run build
npm run start
npx tsx scripts/tests/security-headers.ts
npx tsx scripts/verify-security-headers.ts http://localhost:3000 --mode=enforce
```

The deployed verification command is required after every CSP or hosting-header change because a
CDN or reverse proxy can alter headers after Next.js returns them.
