// 后端 API 封装（与 tools/navkit/server.py 契约对齐）

async function j(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`);
  return r.json();
}

export const api = {
  sessions: () => j('/api/list_sessions'),
  images: (session) => j(`/api/list_images?session=${encodeURIComponent(session)}`),
  imageUrl: (session, name) => `/api/image?session=${encodeURIComponent(session)}&name=${encodeURIComponent(name)}`,
  templateStatus: () => j('/api/template_status'),
  graph: () => j('/api/graph'),
  assets: () => j('/api/assets'),
  trace: () => j('/api/trace'),
};