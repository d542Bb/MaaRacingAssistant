import React, { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import { kindColor } from './theme';

// ---------- 锚点节点（template / ocr / point / action） ----------
function AnchorNode({ data, selected }) {
  const c = kindColor(data.kind);
  return (
    <div className={'nk-anchor' + (selected ? ' sel' : '')} style={{ '--accent': c }}>
      <Handle type="target" position={Position.Left} className="nk-handle" />
      <div className="nk-anchor-head">
        <span className="nk-dot" style={{ background: c }} />
        <span className="nk-anchor-label">{data.label}</span>
        {data.dynamic && <span className="nk-dyn">dyn</span>}
      </div>
      <div className="nk-anchor-meta">
        <span>{data.kind}</span>
        {data.owner && <span>· {data.owner}</span>}
        {data.guarded_by && <span className="nk-guard" title={`guarded_by: ${data.guarded_by}`}>盾</span>}
      </div>
      <Handle type="source" position={Position.Right} className="nk-handle" />
    </div>
  );
}

// ---------- Stage 节点 ----------
function StageNode({ data, selected }) {
  const c = kindColor('stage');
  return (
    <div className={'nk-stage' + (selected ? ' sel' : '')} style={{ '--accent': c }}>
      <Handle type="target" position={Position.Left} className="nk-handle" />
      <div className="nk-stage-accent" />
      <div className="nk-stage-head">
        <span className="nk-stage-title">{data.label}</span>
        {data.dynamic && <span className="nk-dyn">dyn</span>}
      </div>
      <div className="nk-stage-meta">
        <span>{data.page || '-'}</span>
        <span>{(data.anchors?.length || 0)} 锚</span>
        {(data.ocr?.length || 0) > 0 && <span>{data.ocr.length} OCR</span>}
      </div>
      <Handle type="source" position={Position.Right} className="nk-handle" />
    </div>
  );
}

export const nodeTypes = {
  anchor: memo(AnchorNode),
  stage: memo(StageNode),
};