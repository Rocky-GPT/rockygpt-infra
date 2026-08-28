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

// Campus data is served by the brain. rockygpt-data was retired from Render on
// 2026-08-28; probing it here is what would turn a deliberate retirement into a
// recurring production-monitor incident.
const directMap = await request('direct brain map', `${base('BRAIN_URL')}/v1/map`);
const proxiedMap = await request('UI map proxy', `${base('UI_URL')}/api/map`);
if (!Array.isArray(directMap.locations) || !Array.isArray(proxiedMap.locations)) {
  throw new Error('Map contract is missing locations.');
}

await request(
  'invalid brain chat request',
  `${base('BRAIN_URL')}/v1/chat`,
  { method: 'POST', headers: { 'content-type': 'application/json' }, body: 'null' },
  400
);

console.log('cross-service smoke: passed');
