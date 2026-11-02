const required = ['UI_URL', 'BRAIN_URL'];
for (const name of required) {
  if (!process.env[name]?.trim()) throw new Error(`${name} is required.`);
}

const base = (name) => process.env[name].replace(/\/+$/, '');
const uiHeaders = process.env.VERCEL_AUTOMATION_BYPASS_SECRET
  ? { 'x-vercel-protection-bypass': process.env.VERCEL_AUTOMATION_BYPASS_SECRET }
  : {};

async function request(label, url, init, expected = 200) {
  const headers = {
    ...(url.startsWith(base('UI_URL')) ? uiHeaders : {}),
    ...(init?.headers || {}),
  };
  const started = performance.now();
  const response = await fetch(url, { ...init, headers, signal: AbortSignal.timeout(90_000) });
  const elapsed = Math.round(performance.now() - started);
  if (response.status !== expected) {
    throw new Error(`${label} answered ${response.status}; expected ${expected}.`);
  }
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('application/json')) {
    throw new Error(`${label} did not return JSON.`);
  }
  console.log(`${label}: ${response.status} in ${elapsed}ms`);
  return response.json();
}

await request('UI readiness', `${base('UI_URL')}/api/readiness`);
await request('brain readiness', `${base('BRAIN_URL')}/readiness`);

// The Brain answers /v1/* only with the shared service token (SERVICE_TOKEN, sent as
// x-rockygpt-environment-token). A request without it must be refused with 401 "unauthorized":
// 503 "service_not_configured" means production has no token, and anything else means the gate is
// open. Neither check makes a model call.
const refused = await request(
  'brain chat without the service token',
  `${base('BRAIN_URL')}/v1/chat`,
  { method: 'POST', headers: { 'content-type': 'application/json' }, body: 'null' },
  401
);
if (refused?.error?.code !== 'unauthorized') {
  throw new Error(`brain chat without the service token answered "${refused?.error?.code}"; expected "unauthorized".`);
}

// With the token, a malformed body must reach the Brain's own validation.
const serviceToken = (process.env.SERVICE_TOKEN || process.env.STAGING_SERVICE_TOKEN)?.trim();
if (serviceToken) {
  await request(
    'invalid brain chat request',
    `${base('BRAIN_URL')}/v1/chat`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-rockygpt-environment-token': serviceToken },
      body: 'null',
    },
    422
  );
} else {
  console.log('invalid brain chat request: skipped (no SERVICE_TOKEN)');
}

console.log('cross-service smoke: passed');
