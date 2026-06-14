import type { CSSProperties } from 'react';
import { Button } from 'antd';
import { ArrowRightOutlined, CheckCircleOutlined } from '@ant-design/icons';

export interface SuccessModeProps {
  stats: { components_added: number; plates_added: number; products_added: number };
  backupPath: string;
  timingMs: Record<string, number>;
  variantNames: string[];
  onContinue: () => void;
  onGotoProducts: () => void;
}

export default function SuccessMode(props: SuccessModeProps) {
  const { stats, backupPath, timingMs, variantNames, onContinue, onGotoProducts } = props;

  // 后端 schemas_intake.MergeResponse.timing_ms 用中文键「写入」「重新加载」（参见 prd-005 §CUJ-5）。
  const writeMs = timingMs['写入'] ?? timingMs.write ?? 0;
  const reloadMs = timingMs['重新加载'] ?? timingMs.reload ?? 0;

  return (
    <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 24 }}>
      <div style={styles.card}>
        <CheckCircleOutlined style={{ fontSize: 48, color: '#52c41a', marginBottom: 16 }} />

        <div style={styles.title}>合并成功</div>

        <div style={styles.desc}>
          已向 <code>data/catalog.yaml</code> 追加 <b>{stats.components_added}</b> 个组件、
          <b>{stats.plates_added}</b> 张打印盘、<b>{stats.products_added}</b> 个产品变体
          {variantNames.length > 0 ? <>（{variantNames.join(' / ')}）</> : null}
          ，目录已自动重新加载，可立即使用。
        </div>

        <div style={styles.meta}>
          <div>备份文件：{backupPath || '（未知）'}</div>
          <div>合并耗时：写入 {writeMs} ms · 重新加载 {reloadMs} ms</div>
        </div>

        <div style={styles.actions}>
          <Button onClick={onContinue}>继续录入下一个产品</Button>
          <Button type="primary" icon={<ArrowRightOutlined />} onClick={onGotoProducts}>
            前往产品目录查看
          </Button>
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  card: {
    width: 640,
    background: '#fff',
    borderRadius: 8,
    padding: '64px 40px',
    boxShadow: '0 2px 16px rgba(0,0,0,0.04)',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
  },
  title: {
    fontSize: 24,
    fontWeight: 600,
    color: 'rgba(0,0,0,0.88)',
    marginBottom: 8,
  },
  desc: {
    fontSize: 14,
    color: 'rgba(0,0,0,0.45)',
    marginBottom: 28,
    textAlign: 'center',
    maxWidth: 480,
    lineHeight: 1.7,
  },
  meta: {
    background: '#fafafa',
    borderRadius: 4,
    padding: 12,
    fontFamily: '"SF Mono", Menlo, Consolas, monospace',
    fontSize: 12,
    color: 'rgba(0,0,0,0.65)',
    marginBottom: 28,
    width: '100%',
    maxWidth: 520,
    lineHeight: 1.8,
    wordBreak: 'break-all',
  },
  actions: {
    display: 'flex',
    gap: 12,
  },
};
