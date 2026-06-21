import { useState } from 'react';
import { Breadcrumb, Spin, message } from 'antd';
import { Link } from 'react-router-dom';
import {
  api,
  type AutoImportScanResponse,
  type AutoImportCommitResponse,
  type AutoImportCommitItem,
} from '../api/client';
import XhsTab from './auto_import/XhsTab';
import XianyuTab from './auto_import/XianyuTab';
import PreviewTable from './auto_import/PreviewTable';
import SuccessPanel from './auto_import/SuccessPanel';
import FailurePanel from './auto_import/FailurePanel';

type SourcePlatform = 'xhs' | 'xianyu';

type AutoImportMode =
  | { kind: 'tabs'; activeTab: SourcePlatform }
  | { kind: 'preview'; batch: AutoImportScanResponse; sourcePlatform: SourcePlatform }
  | { kind: 'committing' }
  | { kind: 'success'; commitResponse: AutoImportCommitResponse; sourcePlatform: SourcePlatform }
  | { kind: 'failure'; error: string };

export default function AutoImport() {
  const [mode, setMode] = useState<AutoImportMode>({ kind: 'tabs', activeTab: 'xianyu' });

  const startXhsScan = (_batchId: string, scan: AutoImportScanResponse) => {
    setMode({ kind: 'preview', batch: scan, sourcePlatform: 'xhs' });
  };

  const startXianyuScan = (_batchId: string, scan: AutoImportScanResponse) => {
    setMode({ kind: 'preview', batch: scan, sourcePlatform: 'xianyu' });
  };

  const commit = async (items: AutoImportCommitItem[]) => {
    if (mode.kind !== 'preview') return;
    const platform = mode.sourcePlatform;
    setMode({ kind: 'committing' });
    try {
      const resp = await api.autoImport.commit({
        batch_id: mode.batch.batch_id,
        items,
      });
      if (!resp.ok) {
        setMode({ kind: 'failure', error: resp.error || resp.error_kind || '提交失败' });
        return;
      }
      setMode({ kind: 'success', commitResponse: resp, sourcePlatform: platform });
    } catch (e) {
      const msg = e instanceof Error ? e.message : '提交失败';
      message.error(msg);
      setMode({ kind: 'failure', error: msg });
    }
  };

  const backToTabs = (tab: SourcePlatform = 'xhs') => {
    setMode({ kind: 'tabs', activeTab: tab });
  };

  const stepLabel = (() => {
    switch (mode.kind) {
      case 'tabs': return null;
      case 'preview': return '预览校对';
      case 'committing': return '导入中';
      case 'success': return '完成';
      case 'failure': return '失败';
    }
  })();

  return (
    <div>
      <Breadcrumb
        style={{ marginBottom: 8 }}
        items={[
          { title: <Link to="/orders">订单管理</Link> },
          { title: '自动导入' },
          ...(stepLabel ? [{ title: stepLabel }] : []),
        ]}
      />
      <h1 style={{ fontSize: 22, margin: '0 0 6px', fontWeight: 600 }}>自动导入</h1>
      <div style={{ color: 'rgba(0,0,0,0.65)', fontSize: 13, marginBottom: 20 }}>
        从小红书千帆 Chrome 扩展或闲鱼 Android 设备 ADB 截屏导入订单 · 自动匹配 catalog SKU · 预览后入队。
      </div>

      {mode.kind === 'tabs' && (
        <TabsContainer
          activeTab={mode.activeTab}
          onTabChange={(tab) => setMode({ kind: 'tabs', activeTab: tab })}
        >
          {mode.activeTab === 'xianyu' ? (
            <XianyuTab onScan={startXianyuScan} otherInProgress={false} />
          ) : (
            <XhsTab onScan={startXhsScan} otherInProgress={false} />
          )}
        </TabsContainer>
      )}

      {mode.kind === 'preview' && (
        <PreviewTable
          batch={mode.batch}
          sourcePlatform={mode.sourcePlatform}
          onCommit={commit}
          onCancel={() => backToTabs(mode.sourcePlatform)}
        />
      )}

      {mode.kind === 'committing' && (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin size="large" tip="正在导入..." />
        </div>
      )}

      {mode.kind === 'success' && (
        <SuccessPanel
          response={mode.commitResponse}
          sourcePlatform={mode.sourcePlatform}
        />
      )}

      {mode.kind === 'failure' && (
        <FailurePanel
          error={mode.error}
          onBack={() => backToTabs()}
          onDiscard={() => backToTabs()}
        />
      )}
    </div>
  );
}

// ---------- Tabs container ----------

function TabsContainer({
  activeTab,
  onTabChange,
  children,
}: {
  activeTab: SourcePlatform;
  onTabChange: (tab: SourcePlatform) => void;
  children: React.ReactNode;
}) {
  return (
    <>
      <div
        style={{
          display: 'flex',
          gap: 4,
          borderBottom: '1px solid #f0f0f0',
        }}
      >
        <TabPill
          active={activeTab === 'xianyu'}
          color="#ff7a00"
          label="闲鱼"
          shortLabel="闲"
          onClick={() => onTabChange('xianyu')}
        />
        <TabPill
          active={activeTab === 'xhs'}
          color="#ff2442"
          label="小红书千帆"
          shortLabel="小"
          onClick={() => onTabChange('xhs')}
        />
      </div>
      <div
        style={{
          background: '#fff',
          border: '1px solid #f0f0f0',
          borderTop: 'none',
          borderRadius: '0 0 8px 8px',
          marginBottom: 24,
        }}
      >
        {children}
      </div>
    </>
  );
}

function TabPill({
  active,
  color,
  label,
  shortLabel,
  onClick,
}: {
  active: boolean;
  color: string;
  label: string;
  shortLabel: string;
  onClick: () => void;
}) {
  return (
    <div
      onClick={onClick}
      style={{
        padding: '12px 24px',
        cursor: 'pointer',
        color: active ? color : 'rgba(0,0,0,0.65)',
        fontSize: 15,
        fontWeight: 500,
        borderBottom: `2px solid ${active ? color : 'transparent'}`,
        marginBottom: -1,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        transition: 'color 0.15s, border-color 0.15s',
      }}
    >
      <span
        style={{
          width: 22,
          height: 22,
          borderRadius: 4,
          background: color,
          color: '#fff',
          fontWeight: 700,
          fontSize: 11,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {shortLabel}
      </span>
      {label}
    </div>
  );
}
