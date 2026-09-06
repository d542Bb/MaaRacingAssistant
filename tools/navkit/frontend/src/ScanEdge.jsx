import React, { memo } from 'react';
import { BaseEdge, getBezierPath } from '@xyflow/react';

// 自定义边：贝塞尔 + 白色高光扫描（方向 = 路径方向）+ 永不颠倒的标签。
// 标签用 getBezierPath 的 labelX/labelY 绝对定位 + 无旋转 transform，
// 不沿 path 排布，因此向左的边也不会出现倒置文字。
function ScanEdge({
  id, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition, markerEnd, data, selected,
}) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
    curvature: 0.35,
  });
  const label = data?.label;
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd}
        style={{ strokeWidth: selected ? 2.6 : 1.8 }} />
      <path d={path} className="nk-edge-scan" pathLength={100} />
      {label && (
        <g transform={`translate(${labelX},${labelY})`} pointerEvents="none">
          <rect className="nk-edge-label-bg" x={-label.length * 3.1 - 6} y={-8}
                width={label.length * 6.2 + 12} height={15} rx={4} />
          <text className={'nk-edge-label' + (selected ? ' hot' : '')} textAnchor="middle" dy={3}>
            {label}
          </text>
        </g>
      )}
    </>
  );
}

export const edgeTypes = { scan: memo(ScanEdge) };