import { useEffect, useState } from 'react';
import { Modal, Form, Input, Typography, Button, Space, message } from 'antd';
import { api } from '../api/client';

// TODO(T4.1 merge): remove these placeholders and import from '../api/client'
//   when T4.1 lands (它会 export Printer / PrinterUpdateBody)。
export interface Printer {
  id: number;
  name: string;
  ip: string | null;
  serial: string | null;
  access_code_masked: string | null;
}

export type PrinterUpdateBody = Partial<{
  name: string;
  ip: string;
  serial: string;
  access_code: string;
}>;

interface EditPrinterModalProps {
  open: boolean;
  printer: Printer | null;
  onCancel: () => void;
  onSaved: () => void;
}

interface FormValues {
  name: string;
  ip: string;
  serial: string;
  access_code: string;
}

export default function EditPrinterModal({ open, printer, onCancel, onSaved }: EditPrinterModalProps) {
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);
  // access_code 三态：
  //   - 'unchanged'：用户没动密码框 / 也没点清除 → 不发送 access_code
  //   - 'set'：用户输了新值 → 发送 access_code = 新值（非空）
  //   - 'cleared'：用户点了「清除访问码」 → 发送 access_code = ''（后端置 NULL）
  const [accessCodeMode, setAccessCodeMode] = useState<'unchanged' | 'set' | 'cleared'>('unchanged');

  useEffect(() => {
    if (open && printer) {
      form.resetFields();
      form.setFieldsValue({
        name: printer.name,
        ip: printer.ip ?? '',
        serial: printer.serial ?? '',
        access_code: '',
      });
      setAccessCodeMode('unchanged');
    }
  }, [open, printer, form]);

  const handleClearAccessCode = () => {
    form.setFieldsValue({ access_code: '' });
    setAccessCodeMode('cleared');
  };

  const handleAccessCodeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    if (v === '') {
      setAccessCodeMode('unchanged');
    } else {
      setAccessCodeMode('set');
    }
  };

  const handleOk = async () => {
    if (!printer) return;
    try {
      const values = await form.validateFields();
      const body: PrinterUpdateBody = {};

      const nameTrimmed = values.name.trim();
      if (nameTrimmed !== printer.name) {
        body.name = nameTrimmed;
      }

      const ipTrimmed = values.ip.trim();
      const ipOriginal = printer.ip ?? '';
      if (ipTrimmed !== ipOriginal) {
        body.ip = ipTrimmed;
      }

      const serialTrimmed = values.serial.trim();
      const serialOriginal = printer.serial ?? '';
      if (serialTrimmed !== serialOriginal) {
        body.serial = serialTrimmed;
      }

      if (accessCodeMode === 'set') {
        body.access_code = values.access_code;
      } else if (accessCodeMode === 'cleared') {
        body.access_code = '';
      }

      setSubmitting(true);
      await api.updatePrinter(printer.id, body);
      message.success('已保存');
      onSaved();
    } catch (e: any) {
      if (e?.errorFields) return;
      message.error(e?.message || '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  const accessCodeHint = accessCodeMode === 'cleared'
    ? <Typography.Text type="warning">将清除访问码</Typography.Text>
    : (
      <Typography.Text type="secondary">
        当前：<code>{printer?.access_code_masked || '未设置'}</code>；留空则保持不变。
      </Typography.Text>
    );

  return (
    <Modal
      open={open}
      title={printer ? `编辑打印机：${printer.name}` : '编辑打印机'}
      width={480}
      onCancel={onCancel}
      onOk={handleOk}
      confirmLoading={submitting}
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        <Form.Item
          label="名称"
          name="name"
          rules={[{ required: true, message: '请输入名称' }]}
        >
          <Input placeholder="如：1号机" />
        </Form.Item>

        <Form.Item label="IP 地址" name="ip">
          <Input placeholder="如：192.168.1.123" />
        </Form.Item>

        <Form.Item label="序列号 Serial" name="serial">
          <Input placeholder="如：01P00A123456789" />
        </Form.Item>

        <Form.Item
          label={
            <Space size={4}>
              <span>访问码 Access Code</span>
              <Button type="link" size="small" onClick={handleClearAccessCode} style={{ padding: 0 }}>
                清除访问码
              </Button>
            </Space>
          }
          name="access_code"
          extra={accessCodeHint}
        >
          <Input.Password
            placeholder="留空保持不变，输入新值覆盖"
            autoComplete="new-password"
            onChange={handleAccessCodeChange}
          />
        </Form.Item>

        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          IP / 序列号 / 访问码三项全填才会启动监测；任一为空显示「未配置」。访问码勿外传。
        </Typography.Text>
      </Form>
    </Modal>
  );
}
