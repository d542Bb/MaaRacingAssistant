import React, { useEffect, useState } from 'react';
import { Layout, Nav, Tag } from '@douyinfe/semi-ui';
import {
  IconHistogram, IconDesktop, IconImage, IconVideo, IconServer,
} from '@douyinfe/semi-icons';
import GraphView from './GraphView';
import CalibView from './CalibView';
import TemplatesView from './TemplatesView';
import ReplayView from './ReplayView';
import AssetsView from './AssetsView';
import Inspector from './Inspector';
import { api } from './api';

const { Header, Sider, Content, Footer } = Layout;

const NAV_ITEMS = [
  { itemKey: 'graph', text: '路径树', icon: <IconHistogram /> },
  { itemKey: 'calib', text: 'ROI 校准', icon: <IconDesktop /> },
  { itemKey: 'tpl', text: '模板库', icon: <IconImage /> },
  { itemKey: 'replay', text: '会话回放', icon: <IconVideo /> },
  { itemKey: 'assets', text: '资产 v3', icon: <IconServer /> },
];

const VIEW_KEYS = NAV_ITEMS.map(i => i.itemKey);
// hash 路由：#/graph、#/calib…—— 网址随视图变化，但始终是同一个页签（SPA 手感）
function viewFromHash() {
  const h = (window.location.hash || '').replace(/^#\/?/, '');
  return VIEW_KEYS.includes(h) ? h : 'graph';
}

export default function App() {
  const [view, setViewState] = useState(viewFromHash);
  const [selectedNode, setSelectedNode] = useState(null);
  const [traceRows, setTraceRows] = useState([]);
  const [graphDoc, setGraphDoc] = useState(null);

  useEffect(() => {
    api.trace().then(setTraceRows).catch(() => setTraceRows([]));
    api.graph().then(setGraphDoc).catch(() => setGraphDoc(null));
  }, []);

  useEffect(() => {
    const onHash = () => setViewState(viewFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  // 切视图 = 写 hash，由 hashchange 统一驱动状态（浏览器前进/后退可用）
  const setView = (key) => { window.location.hash = '/' + key; };

  return (
    <Layout className="app">
      <Header className="topbar">
        <div className="brand">
          <span className="brand-dot" />
          <span className="brand-name">NavKit Studio</span>
          <Tag size="small" color="blue" style={{ margin: 0 }}>treasure</Tag>
          <Tag size="small" color="violet" style={{ margin: 0 }}>schema v3</Tag>
        </div>
        <div style={{ flex: 1 }} />
      </Header>

      <Layout style={{ flex: 1, minHeight: 0 }}>
        <Sider style={{ background: 'var(--semi-color-bg-1)' }}>
          <Nav
            style={{ height: '100%', maxWidth: 176 }}
            items={NAV_ITEMS}
            selectedKeys={[view]}
            onSelect={({ itemKey }) => setView(itemKey)}
            footer={{ collapseButton: true }}
          />
        </Sider>

        <Layout style={{ flex: 1, minHeight: 0 }}>
          <Content style={{ minHeight: 0, display: 'flex' }}>
            <div className="content-main">
              {view === 'graph' && (
                <GraphView traceRows={traceRows} onSelectNode={setSelectedNode} />
              )}
              {view === 'calib' && <CalibView />}
              {view === 'tpl' && <TemplatesView />}
              {view === 'replay' && <ReplayView traceRows={traceRows} />}
              {view === 'assets' && <AssetsView graphDoc={graphDoc} />}
            </div>
            {view === 'graph' && (
              <Inspector node={selectedNode} traceRows={traceRows} />
            )}
          </Content>

          <Footer className="statusbar">
            <span>treasure_assets.json</span>
            <span>{graphDoc ? `${graphDoc.nodes.length} 节点 · ${graphDoc.edges.length} 边` : '加载中…'}</span>
            {graphDoc && <span>孤儿 {graphDoc.orphans.length} · 未担保 {graphDoc.unguarded_points.length}</span>}
            <span>trace {traceRows.length} 行</span>
            <div style={{ flex: 1 }} />
            <span>NavKit 控制台 · 构建版</span>
          </Footer>
        </Layout>
      </Layout>
    </Layout>
  );
}