// NavKit Studio 统一配色（2026 精简色板 + 分层）
// 层级：canvas < stage < panel < node；强调色只出现在节点顶条与端口

export const BG = {
  canvas: '#14161b',
  stage: '#191c23',
  panel: '#1f232b',
  node: '#242935',
  edge: '#3a4150',
};

export const TEXT = {
  primary: '#e6eaf0',
  secondary: '#aab3c2',
  muted: '#7a8494',
};

export const KIND_COLOR = {
  stage: '#5b8def',
  template: '#5b8def',
  ocr: '#4fd1c5',
  point: '#a98ff0',
  action: '#d18a4f',
};

// graph_document 的锚点 kind 直接作为节点 kind（template/ocr/point），
// 加上 stage 节点；映射到 4 种强调色
export function kindColor(kind) {
  return KIND_COLOR[kind] || '#6d7583';
}

// Semi Tag color 名（用于资产/模板视图）
export const SEMI_TAG_COLOR = {
  stage: 'blue',
  template: 'blue',
  ocr: 'cyan',
  point: 'violet',
  action: 'orange',
};