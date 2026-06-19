import { useEffect, useState, useCallback } from 'react';
import { Button, Alert, Input, Space, Spin, message } from 'antd';
import { api, type AutoImportScanResponse } from '../../api/client';
import { getExtensionId, pingExtension, scrapeXhs, setStoredExtId } from '../../api/extension';
import ScanningProgress from './ScanningProgress';

interface XhsTabProps {
  onScan: (batchId: string, scanResponse: AutoImportScanResponse) => void;
  otherInProgress: boolean;
}

type ProbeState =
  | { kind: 'loading' }
  | { kind: 'ready'; extVersion?: string; xhsUrl?: string }
  | { kind: 'no_ext' }
  | { kind: 'no_ext_id' }
  | { kind: 'no_xhs_tab'; extVersion?: string }
  | { kind: 'error'; message: string };

type ScanState =
  | { kind: 'idle' }
  | { kind: 'scanning'; batchId: string; step: number; startedAt: number }
  | { kind: 'error'; message: string };

const SCAN_STEPS = [
  '已找到千帆 Tab',
  '注入 content script 完成',
  '正在抓取订单列表 DOM',
  '后端去重 + LLM 匹配 SKU',
  '跳转预览页',
];

export default function XhsTab({ onScan, otherInProgress }: XhsTabProps) {
  const [probe, setProbe] = useState<ProbeState>({ kind: 'loading' });
  const [scan, setScan] = useState<ScanState>({ kind: 'idle' });
  const [elapsedMs, setElapsedMs] = useState(0);

  const detect = useCallback(async (notify = false) => {
    setProbe({ kind: 'loading' });
    const pong = await pingExtension();
    if (!pong.ok) {
      if (pong.error_kind === 'no_ext_id') {
        setProbe({ kind: 'no_ext_id' });
        if (notify) message.warning('请先填写扩展 ID');
      } else {
        setProbe({ kind: 'no_ext' });
        if (notify) message.error(`扩展未检测到：${pong.error_kind ?? 'unknown'}`);
      }
      return;
    }
    try {
      const probeResp = await api.autoImport.xhs.probe();
      if (probeResp.has_xhs_tab) {
        setProbe({ kind: 'ready', extVersion: pong.version });
        if (notify) message.success(`扩展已检测到 v${pong.version ?? '?'}`);
      } else {
        setProbe({ kind: 'no_xhs_tab', extVersion: pong.version });
        if (notify) message.warning('扩展已检测到，但未发现千帆 Tab');
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '探测千帆 Tab 失败';
      setProbe({ kind: 'error', message: msg });
      if (notify) message.error(msg);
    }
  }, []);

  useEffect(() => {
    detect();
  }, [detect]);

  // tick scanning elapsed timer
  useEffect(() => {
    if (scan.kind !== 'scanning') {
      setElapsedMs(0);
      return;
    }
    const startedAt = scan.startedAt;
    const id = window.setInterval(() => {
      setElapsedMs(Date.now() - startedAt);
    }, 100);
    return () => window.clearInterval(id);
  }, [scan]);

  const startScan = async () => {
    const batchId = (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
      ? crypto.randomUUID()
      : `xhs-${Date.now()}`;
    const startedAt = Date.now();
    setScan({ kind: 'scanning', batchId, step: 1, startedAt });

    // 模拟前端步骤进度：1 → 2 → 3，等扩展响应后跳到 4，最后 5
    // 真实扩展抓取通常 < 1s，故意拉慢让用户能看清每一步
    window.setTimeout(() => {
      setScan((s) => (s.kind === 'scanning' && s.step < 2 ? { ...s, step: 2 } : s));
    }, 700);
    window.setTimeout(() => {
      setScan((s) => (s.kind === 'scanning' && s.step < 3 ? { ...s, step: 3 } : s));
    }, 1600);

    const resp = await scrapeXhs(batchId);
    if (!resp.ok) {
      const msg = resp.error_kind || '扩展抓取失败';
      setScan({ kind: 'error', message: msg });
      message.error(`扫描失败：${msg}`);
      return;
    }
    if (!resp.scan_response) {
      setScan({ kind: 'error', message: '扩展未返回 scan_response' });
      return;
    }

    setScan((s) => (s.kind === 'scanning' ? { ...s, step: 4 } : s));
    // 把扩展的诊断信息塞进 scan_response，方便 PreviewTable 在 0 单时展示
    const enrichedScan = { ...resp.scan_response, ext_debug: resp.ext_debug };
    // 给最后两步小动画时间，避免一闪而过看不清
    window.setTimeout(() => {
      setScan((s) => (s.kind === 'scanning' ? { ...s, step: 5 } : s));
      window.setTimeout(() => {
        onScan(batchId, enrichedScan);
        setScan({ kind: 'idle' });
      }, 700);
    }, 800);
  };

  const cancelScan = () => {
    setScan({ kind: 'idle' });
  };

  if (scan.kind === 'scanning') {
    return (
      <div style={{ padding: '32px 24px' }}>
        <div
          style={{
            color: 'rgba(0,0,0,0.65)',
            fontSize: 13,
            marginBottom: 24,
            textAlign: 'center',
          }}
        >
          正在从千帆扩展抓取订单数据，请稍候...
        </div>
        <ScanningProgress
          steps={SCAN_STEPS}
          currentStep={scan.step}
          elapsedMs={elapsedMs}
          onCancel={cancelScan}
        />
      </div>
    );
  }

  if (scan.kind === 'error') {
    return (
      <div style={{ padding: 32, maxWidth: 560, margin: '0 auto' }}>
        <Alert
          type="error"
          showIcon
          message="扫描失败"
          description={scan.message}
          action={<Button onClick={() => setScan({ kind: 'idle' })}>重试</Button>}
        />
      </div>
    );
  }

  return (
    <div style={{ padding: '32px 24px' }}>
      <div
        style={{
          color: 'rgba(0,0,0,0.65)',
          fontSize: 13,
          marginBottom: 24,
          textAlign: 'center',
        }}
      >
        Chrome 扩展直接读千帆订单页 DOM · <b>无 OCR · 无 LLM</b> · 5~10 秒完成
      </div>

      <div style={{ maxWidth: 560, margin: '0 auto' }}>
        {probe.kind === 'loading' && (
          <div style={{ textAlign: 'center', padding: 32 }}>
            <Spin />
          </div>
        )}

        {probe.kind === 'ready' && (
          <ReadyBlock
            extVersion={probe.extVersion}
            onScan={startScan}
            disabled={otherInProgress}
          />
        )}

        {probe.kind === 'no_ext_id' && (
          <ExtIdInputBlock onSaved={() => detect(true)} />
        )}

        {probe.kind === 'no_ext' && (
          <NoExtensionBlock
            onRefresh={detect}
            onChangeExtId={() => setProbe({ kind: 'no_ext_id' })}
          />
        )}

        {probe.kind === 'no_xhs_tab' && (
          <NoXhsTabBlock extVersion={probe.extVersion} onRefresh={detect} />
        )}

        {probe.kind === 'error' && (
          <Alert
            type="error"
            showIcon
            message="检测异常"
            description={probe.message}
            action={<Button onClick={() => detect(true)}>重试</Button>}
          />
        )}
      </div>
    </div>
  );
}

// ---------- Sub-blocks ----------

function ExtIdInputBlock({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState(getExtensionId() ?? '');
  const trimmed = value.trim();
  const isValid = /^[a-z]{32}$/.test(trimmed);
  const save = () => {
    if (!isValid) {
      message.warning('扩展 ID 应该是 32 位小写字母');
      return;
    }
    setStoredExtId(trimmed);
    message.success('已保存扩展 ID');
    onSaved();
  };
  return (
    <Alert
      type="warning"
      showIcon
      message="未配置扩展 ID"
      description={
        <div>
          <p style={{ margin: '0 0 8px' }}>
            在 <code>chrome://extensions/</code> 找到本扩展，复制 ID（32 位小写字母）粘贴到下面：
          </p>
          <Space.Compact style={{ width: '100%', maxWidth: 480 }}>
            <Input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="abcdefghijklmnopqrstuvwxyzabcdef"
              onPressEnter={save}
              style={{ fontFamily: 'monospace' }}
            />
            <Button type="primary" onClick={save} disabled={!isValid}>
              保存
            </Button>
          </Space.Compact>
          <p style={{ margin: '8px 0 0', fontSize: 12, color: 'rgba(0,0,0,0.45)' }}>
            存到浏览器 localStorage，下次直接生效，无需改 .env 也不用重启 vite。
          </p>
        </div>
      }
    />
  );
}

function ReadyBlock({
  extVersion,
  onScan,
  disabled,
}: {
  extVersion?: string;
  onScan: () => void;
  disabled: boolean;
}) {
  return (
    <>
      <div
        style={{
          background: '#fafafa',
          borderRadius: 8,
          padding: '16px 20px',
          marginBottom: 24,
        }}
      >
        <CheckLine label="扩展" value={`已装${extVersion ? ` v${extVersion}` : ''}`} />
        <CheckLine label="千帆 Tab" value="ark.xiaohongshu.com/app-order/dispatch/waybill/wait" mono />
      </div>
      <Button
        type="primary"
        size="large"
        block
        danger
        style={{
          background: '#ff2442',
          borderColor: '#ff2442',
          height: 56,
          fontSize: 17,
          fontWeight: 600,
        }}
        onClick={onScan}
        disabled={disabled}
      >
        捕捉千帆订单 →
      </Button>
      <div
        style={{
          textAlign: 'center',
          color: 'rgba(0,0,0,0.45)',
          fontSize: 12,
          marginTop: 10,
        }}
      >
        扫描后自动跳转预览页 · 你可逐单核对再确认导入
        {disabled && <div>另一平台扫描中，请稍候</div>}
      </div>
    </>
  );
}

function CheckLine({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '6px 0',
        fontSize: 13,
      }}
    >
      <span
        style={{
          width: 18,
          height: 18,
          borderRadius: '50%',
          background: '#52c41a',
          color: '#fff',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 11,
          fontWeight: 700,
          flexShrink: 0,
        }}
      >
        ✓
      </span>
      <span style={{ color: 'rgba(0,0,0,0.65)', minWidth: 80 }}>{label}</span>
      <span
        style={
          mono
            ? {
                fontFamily: 'monospace',
                fontSize: 11,
                background: '#fff',
                padding: '1px 6px',
                borderRadius: 3,
                color: 'rgba(0,0,0,0.65)',
                border: '1px solid #f0f0f0',
              }
            : { color: 'rgba(0,0,0,0.88)' }
        }
      >
        {value}
      </span>
    </div>
  );
}

function NoExtensionBlock({
  onRefresh,
  onChangeExtId,
}: {
  onRefresh: (notify?: boolean) => void;
  onChangeExtId: () => void;
}) {
  const storedId = getExtensionId();
  const storedTruncated = storedId && storedId.length > 14
    ? `${storedId.slice(0, 6)}…${storedId.slice(-6)}`
    : storedId;
  return (
    <>
      <div
        style={{
          background: '#e6f4ff',
          border: '1px solid #91caff',
          borderRadius: 8,
          padding: '18px 20px',
          marginBottom: 16,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontWeight: 600,
            color: '#1677ff',
            fontSize: 14,
            marginBottom: 8,
          }}
        >
          <span
            style={{
              width: 20,
              height: 20,
              borderRadius: '50%',
              background: '#1677ff',
              color: '#fff',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 700,
              fontSize: 11,
              flexShrink: 0,
            }}
          >
            i
          </span>
          装一下 Chrome 扩展
        </div>
        <div
          style={{
            color: 'rgba(0,0,0,0.65)',
            fontSize: 13,
            lineHeight: 1.6,
            marginBottom: 12,
          }}
        >
          扩展只在你 Chrome 中运行，不会上传任何数据；只在你点导入的瞬间读取当前千帆页面 DOM。
        </div>
        <div
          style={{
            background: '#fff',
            borderRadius: 6,
            padding: '14px 16px',
            fontSize: 12.5,
            color: 'rgba(0,0,0,0.65)',
            lineHeight: 1.7,
          }}
        >
          <ol style={{ margin: 0, paddingLeft: 18 }}>
            <li>下载扩展 zip 并解压到本地</li>
            <li>
              Chrome 地址栏输入 <code style={codeStyle}>chrome://extensions/</code>
            </li>
            <li>右上角打开「开发者模式」开关</li>
            <li>
              点「加载已解压扩展程序」，选解压后的 <code style={codeStyle}>infill-xhs-ext/</code> 文件夹
            </li>
            <li>回这页点「已装好，重新检测」</li>
          </ol>
        </div>
        <Button
          type="primary"
          size="large"
          href="/static/extensions/infill-xhs-scraper-v0.1.2.zip"
          download
          style={{ marginTop: 16 }}
        >
          下载扩展 zip
        </Button>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <Button type="primary" size="large" block onClick={() => onRefresh(true)}>
          已装好，重新检测
        </Button>
        <div
          style={{
            fontSize: 12,
            color: 'rgba(0,0,0,0.45)',
            textAlign: 'center',
            marginTop: 4,
          }}
        >
          已存的扩展 ID: <code style={codeStyle}>{storedTruncated || '(无)'}</code>{' '}
          ·{' '}
          <a onClick={onChangeExtId} style={{ color: '#1677ff', cursor: 'pointer' }}>
            重装扩展后 ID 变了？改一个
          </a>
        </div>
      </div>
    </>
  );
}

function NoXhsTabBlock({
  extVersion,
  onRefresh,
}: {
  extVersion?: string;
  onRefresh: (notify?: boolean) => void;
}) {
  return (
    <>
      <div
        style={{
          background: '#fffbe6',
          border: '1px solid #ffe58f',
          borderRadius: 8,
          padding: '18px 20px',
          marginBottom: 16,
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontWeight: 600,
            color: '#d48806',
            fontSize: 14,
            marginBottom: 8,
          }}
        >
          <span
            style={{
              width: 20,
              height: 20,
              borderRadius: '50%',
              background: '#faad14',
              color: '#fff',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 700,
              fontSize: 11,
              flexShrink: 0,
            }}
          >
            !
          </span>
          请在 Chrome 中打开千帆订单页
        </div>
        <div
          style={{
            color: 'rgba(0,0,0,0.65)',
            fontSize: 13,
            lineHeight: 1.6,
            marginBottom: 12,
          }}
        >
          千帆扩展需要一个已登录、停留在订单列表的 Tab 才能抓数据。
          你可能没开千帆 Tab，或开了但停留在其他页（售后 / 聊天 / 统计）。
        </div>
        <div
          style={{
            background: '#fff',
            borderRadius: 6,
            padding: '12px 14px',
            fontSize: 12.5,
          }}
        >
          <CheckLine label="扩展已装" value={extVersion ? `v${extVersion}` : ''} />
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '6px 0',
              fontSize: 13,
            }}
          >
            <span
              style={{
                width: 18,
                height: 18,
                borderRadius: '50%',
                background: '#ff4d4f',
                color: '#fff',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 11,
                fontWeight: 700,
                flexShrink: 0,
              }}
            >
              ✗
            </span>
            <span style={{ color: 'rgba(0,0,0,0.88)' }}>未找到匹配的千帆订单页 Tab</span>
            <span
              style={{
                marginLeft: 'auto',
                fontFamily: 'monospace',
                fontSize: 11,
                color: 'rgba(0,0,0,0.45)',
              }}
            >
              ark.xiaohongshu.com/app-order/dispatch/waybill/wait
            </span>
          </div>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <Button
          type="primary"
          size="large"
          style={{ flex: 1, background: '#ff2442', borderColor: '#ff2442' }}
          onClick={() => {
            window.open('https://ark.xiaohongshu.com/app-order/dispatch/waybill/wait', '_blank');
            // 旧域名 qfh.xiaohongshu.com 已废弃，统一跳 ark
          }}
        >
          在 Chrome 新 Tab 打开千帆
        </Button>
        <Button size="large" style={{ flex: 1 }} onClick={() => onRefresh(true)}>
          重新检测
        </Button>
      </div>
    </>
  );
}

const codeStyle: React.CSSProperties = {
  background: '#fafafa',
  padding: '1px 5px',
  borderRadius: 3,
  fontFamily: 'monospace',
  fontSize: 11,
};
