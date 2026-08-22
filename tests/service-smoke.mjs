const required = ['UI_URL', 'BRAIN_URL', 'DATA_URL'];
for (const name of required) {
  if (!process.env[name]?.trim()) throw new Error(`${name} is required.`);
}

const base = (name) => process.env[name].replace(/\/+$/, '');
const serviceHeaders = process.env.STAGING_SERVICE_TOKEN
  ? { 'x-rockygpt-environment-token': process.env.STAGING_SERVICE_TOKEN }
  : {};
const uiHeaders = process.env.VERCEL_AUTOMATION_BYPASS_SECRET
  ? { 'x-vercel-protection-bypass': process.env.VERCEL_AUTOMATION_BYPASS_SECRET }
  : {};

async function request(label, url, init, expected = 200) {
  const headers = {
    ...(!url.startsWith(base('UI_URL')) ? serviceHeaders : {}),
    ...(url.startsWith(base('UI_URL')) ? uiHeaders : {}),
    ...(url.startsWith(base('UI_URL')) && process.env.STAGING_SERVICE_TOKEN
      ? { 'x-rockygpt-environment-token': 'browser-supplied-values-are-never-forwarded' }
      : {}),
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
await request('data readiness', `${base('DATA_URL')}/readiness`);

const directMap = await request('direct data map', `${base('DATA_URL')}/v1/map`);
const proxiedMap = await request('UI data map proxy', `${base('UI_URL')}/api/map`);
if (!Array.isArray(directMap.locations) || !Array.isArray(proxiedMap.locations)) {
  throw new Error('Map contract is missing locations.');
}

await request(
  'invalid brain chat request',
  `${base('BRAIN_URL')}/v1/chat`,
  { method: 'POST', headers: { 'content-type': 'application/json' }, body: 'null' },
  400
);

if (process.env.STAGING_SERVICE_TOKEN) {
  const deniedData = await fetch(`${base('DATA_URL')}/v1/map`, { signal: AbortSignal.timeout(90_000) });
  const deniedBrain = await fetch(`${base('BRAIN_URL')}/v1/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: 'null',
    signal: AbortSignal.timeout(90_000),
  });
  if (deniedData.status !== 401 || deniedBrain.status !== 401) {
    throw new Error(`staging access gate failed: data=${deniedData.status}, brain=${deniedBrain.status}`);
  }
  console.log('staging access gate: missing credentials denied, configured credentials accepted');
}

console.log('cross-service smoke: passed');
