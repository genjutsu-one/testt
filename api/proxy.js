export const config = { runtime: 'edge' };

export default async function handler(req) {
  const cors = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': '*',
    'Access-Control-Allow-Methods': 'POST,OPTIONS'
  };
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (req.method !== 'POST') return new Response('Method not allowed', { status: 405, headers: cors });

  try {
    const { url, method = 'GET', headers = {}, body } = await req.json();
    if (!url || !url.startsWith('http')) {
      return Response.json({ status: 400, headers: {}, body: 'bad url' }, { headers: cors });
    }
    const r = await fetch(url, { method, headers, body: body == null ? undefined : body, redirect: 'follow' });
    const text = await r.text();
    return Response.json(
      { status: r.status, headers: Object.fromEntries(r.headers), body: text.slice(0, 500000) },
      { headers: cors }
    );
  } catch (e) {
    return Response.json({ status: 502, headers: {}, body: 'Proxy error: ' + e.message }, { headers: cors });
  }
}
