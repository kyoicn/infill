import { useEffect, useState } from 'react';
import {
  Breadcrumb,
  Card,
  Row,
  Col,
  Form,
  Select,
  Input,
  InputNumber,
  Button,
  Space,
  Spin,
  message,
} from 'antd';
import { Link } from 'react-router-dom';
import {
  api,
  type AutoImportAdbConfig,
  type AutoImportTestAdbResponse,
} from '../../api/client';
import { pingExtension } from '../../api/extension';

type DeviceType = AutoImportAdbConfig['device_type'];
type AdbTestResult = AutoImportTestAdbResponse;


const DEVICE_OPTIONS: { value: DeviceType; label: string; defaultPort: number }[] = [
  { value: 'mumu', label: 'MuMu 模拟器', defaultPort: 7555 },
  { value: 'bluestacks', label: '蓝叠模拟器', defaultPort: 5555 },
  { value: 'ldplayer', label: '雷电模拟器', defaultPort: 5555 },
  { value: 'usb', label: 'USB 真机', defaultPort: 5037 },
];

const EXT_ID = (import.meta.env.VITE_INFILL_EXT_ID as string | undefined) || '';

function truncateExtId(id: string): string {
  if (id.length <= 20) return id;
  return `${id.slice(0, 8)}...${id.slice(-8)}`;
}

type XhsState =
  | { kind: 'loading' }
  | { kind: 'unconfigured' }
  | { kind: 'detected'; version?: string }
  | { kind: 'undetected' };

export default function AutoImportSettings() {
  const [xhsState, setXhsState] = useState<XhsState>({ kind: 'loading' });
  const [adbForm] = Form.useForm<AutoImportAdbConfig>();
  const [adbInitial, setAdbInitial] = useState<AutoImportAdbConfig | null>(null);
  const [adbTesting, setAdbTesting] = useState(false);
  const [adbSaving, setAdbSaving] = useState(false);
  const [adbResult, setAdbResult] = useState<AdbTestResult | null>(null);

  const watched = Form.useWatch([], adbForm);

  const detectExtension = async () => {
    setXhsState({ kind: 'loading' });
    if (!EXT_ID) {
      setXhsState({ kind: 'unconfigured' });
      return;
    }
    try {
      await api.autoImport.xhs.extensionStatus();
    } catch {
      // backend status fetch is best-effort; we still try ping
    }
    const pong = await pingExtension();
    if (pong.ok) {
      setXhsState({ kind: 'detected', version: pong.version });
    } else {
      setXhsState({ kind: 'undetected' });
    }
  };

  useEffect(() => {
    detectExtension();
    api.autoImport.xianyu
      .getConfig()
      .then((cfg) => {
        setAdbInitial(cfg);
        adbForm.setFieldsValue(cfg);
      })
      .catch(() => {
        const fallback: AutoImportAdbConfig = {
          device_type: 'mumu',
          pc_ip: '127.0.0.1',
          port: 7555,
        };
        setAdbInitial(fallback);
        adbForm.setFieldsValue(fallback);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onDeviceChange = (value: DeviceType) => {
    const opt = DEVICE_OPTIONS.find((o) => o.value === value);
    if (opt) {
      adbForm.setFieldsValue({ port: opt.defaultPort });
    }
  };

  const onTestAdb = async () => {
    try {
      const values = await adbForm.validateFields();
      setAdbTesting(true);
      setAdbResult(null);
      const res = await api.autoImport.xianyu.testAdb(values);
      setAdbResult(res);
    } catch (e: any) {
      if (e?.errorFields) return; // validation error
      setAdbResult({
        ok: false,
        diagnostics: [{ name: 'request', label: 'ADB 测试请求失败', ok: false, hint: e?.message }],
      });
    } finally {
      setAdbTesting(false);
    }
  };

  const onSaveAdb = async () => {
    try {
      const values = await adbForm.validateFields();
      setAdbSaving(true);
      await api.autoImport.xianyu.putConfig(values);
      setAdbInitial(values);
      message.success('已保存自动导入配置');
    } catch (e: any) {
      if (e?.errorFields) return;
      message.error(e?.message || '保存失败');
    } finally {
      setAdbSaving(false);
    }
  };

  const isAdbDirty = (() => {
    if (!adbInitial || !watched) return false;
    return (
      watched.device_type !== adbInitial.device_type ||
      watched.pc_ip !== adbInitial.pc_ip ||
      Number(watched.port) !== Number(adbInitial.port)
    );
  })();

  return (
    <div>
      <Breadcrumb
        style={{ marginBottom: 12 }}
        items={[
          { title: <Link to="/settings">系统设置</Link> },
          { title: '自动导入' },
        ]}
      />
      <h1 style={{ fontSize: 22, marginTop: 0, marginBottom: 4 }}>自动导入设置</h1>
      <p style={{ color: '#666', marginTop: 0, marginBottom: 24 }}>
        从小红书千帆 Chrome 扩展或闲鱼 Android 设备 ADB 截屏导入订单 / 自动匹配 catalog SKU / 预览后入队
      </p>

      <Row gutter={24}>
        <Col span={12}>
          <Card
            title={
              <span
                style={{
                  background: '#ff2442',
                  color: '#fff',
                  padding: '2px 10px',
                  borderRadius: 4,
                  fontSize: 13,
                  fontWeight: 600,
                }}
              >
                小红书
              </span>
            }
          >
            <div style={{ fontWeight: 600, marginBottom: 12 }}>千帆 · Chrome 扩展</div>
            {xhsState.kind === 'loading' && <Spin />}
            {xhsState.kind === 'detected' && (
              <Space direction="vertical" size="small" style={{ width: '100%' }}>
                <Space>
                  <span
                    style={{
                      display: 'inline-block',
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: '#52c41a',
                    }}
                  />
                  <span>
                    扩展已检测到{xhsState.version ? ` · v${xhsState.version}` : ''}
                  </span>
                </Space>
                <div style={{ color: '#666', fontSize: 12, fontFamily: 'monospace' }}>
                  扩展 ID: {truncateExtId(EXT_ID)}
                </div>
                <Button onClick={detectExtension}>重新检测</Button>
              </Space>
            )}
            {xhsState.kind === 'undetected' && (
              <div
                style={{
                  background: '#e6f4ff',
                  border: '1px solid #91caff',
                  padding: 16,
                  borderRadius: 6,
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: 8 }}>● 扩展未检测到</div>
                <div style={{ marginBottom: 8 }}>
                  请{' '}
                  <a
                    href="/static/extensions/infill-xhs-scraper-v0.1.0.zip"
                    download
                  >
                    下载扩展安装包
                  </a>{' '}
                  并按以下步骤安装：
                </div>
                <ol style={{ marginTop: 0, paddingLeft: 20 }}>
                  <li>解压下载的 zip 包到本地目录</li>
                  <li>打开 Chrome，访问 chrome://extensions/</li>
                  <li>右上角开启“开发者模式”，点击“加载已解压的扩展程序”</li>
                  <li>选择解压目录后，记下扩展 ID 并写入 frontend/.env</li>
                </ol>
                <Button type="primary" onClick={detectExtension}>
                  我已安装，重新检测
                </Button>
              </div>
            )}
            {xhsState.kind === 'unconfigured' && (
              <div
                style={{
                  background: '#e6f4ff',
                  border: '1px solid #91caff',
                  padding: 16,
                  borderRadius: 6,
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: 8 }}>● 未配置</div>
                <div>
                  请在 <code>frontend/.env</code> 设置{' '}
                  <code>VITE_INFILL_EXT_ID=&lt;扩展ID&gt;</code> 后重启前端
                </div>
              </div>
            )}
          </Card>
        </Col>

        <Col span={12}>
          <Card
            title={
              <span
                style={{
                  background: '#ff7a00',
                  color: '#fff',
                  padding: '2px 10px',
                  borderRadius: 4,
                  fontSize: 13,
                  fontWeight: 600,
                }}
              >
                闲鱼
              </span>
            }
          >
            <div style={{ fontWeight: 600, marginBottom: 12 }}>Android · ADB</div>
            <Form
              form={adbForm}
              layout="vertical"
              initialValues={{ device_type: 'mumu', pc_ip: '127.0.0.1', port: 7555 }}
            >
              <Form.Item
                label="设备类型"
                name="device_type"
                rules={[{ required: true, message: '请选择设备类型' }]}
              >
                <Select
                  options={DEVICE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
                  onChange={onDeviceChange}
                />
              </Form.Item>
              <Form.Item
                label="PC IP"
                name="pc_ip"
                rules={[{ required: true, message: '请填写 PC IP' }]}
              >
                <Input placeholder="例如 127.0.0.1" />
              </Form.Item>
              <Form.Item
                label="端口号"
                name="port"
                rules={[{ required: true, message: '请填写端口号' }]}
              >
                <InputNumber min={1} max={65535} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item style={{ marginBottom: 0 }}>
                <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
                  <Button
                    type="primary"
                    loading={adbTesting}
                    onClick={onTestAdb}
                    style={{ background: '#ff7a00', borderColor: '#ff7a00' }}
                  >
                    测试 ADB 连接
                  </Button>
                  <Button
                    onClick={onSaveAdb}
                    loading={adbSaving}
                    disabled={!isAdbDirty}
                  >
                    保存配置
                  </Button>
                </Space>
              </Form.Item>
            </Form>
            {adbResult && (
              <div style={{ marginTop: 16 }}>
                {adbResult.ok && adbResult.adb_connected ? (
                  <div
                    style={{
                      background: '#f6ffed',
                      border: '1px solid #b7eb8f',
                      color: '#389e0d',
                      padding: 12,
                      borderRadius: 6,
                    }}
                  >
                    连接成功 · 序列号 {adbResult.device_serial} · Android{' '}
                    {adbResult.android_version}
                  </div>
                ) : (
                  <div
                    style={{
                      background: '#fff2f0',
                      border: '1px solid #ffccc7',
                      color: '#cf1322',
                      padding: 12,
                      borderRadius: 6,
                    }}
                  >
                    <div style={{ fontWeight: 600, marginBottom: 8 }}>
                      连接失败 — 请检查诊断
                    </div>
                    <ul style={{ margin: 0, paddingLeft: 20 }}>
                      {(adbResult.diagnostics || []).map((d, i) => (
                        <li key={i}>
                          <span style={{ marginRight: 6 }}>{d.ok ? '✓' : '✗'}</span>
                          <span>{d.label}</span>
                          {d.hint && (
                            <span style={{ color: '#999', marginLeft: 6 }}>— {d.hint}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </Card>
        </Col>
      </Row>

      <div
        style={{
          marginTop: 24,
          padding: 12,
          background: '#fafafa',
          border: '1px solid #f0f0f0',
          borderRadius: 6,
          color: '#666',
          fontSize: 13,
        }}
      >
        LLM 匹配阈值固定（≥0.85 高 / 0.55~0.84 中 / &lt;0.55 低）。LLM API key 走{' '}
        <code>.env</code> 配置。
      </div>
    </div>
  );
}
