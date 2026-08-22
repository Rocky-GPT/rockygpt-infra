const required = ['UI_URL', 'BRAIN_URL', 'DATA_URL'];
for (const name of required) {
  if (!process.env[name]?.trim()) throw new Error(`${name} is required.`);
}

const base = (name) => process.env[name].replace(/\/+$/, '');

async function request(label, url, init, expected = 200) {
  const response = await fetch(url, { ...init, signal: AbortSignal.timeout(15_000) });
  if (response.status !== expected) {
    throw new Error(`${label} answered ${response.status}; expected ${expected}.`);
  }
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('application/json')) {
    throw new Error(`${label} did not return JSON.`);
  }
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

console.log('cross-service smoke: passed');
