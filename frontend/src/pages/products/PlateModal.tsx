import { useEffect, useState } from 'react';
import { Modal, Form, Input, Select, InputNumber, message } from 'antd';
import { api } from '../../api/client';
import { parseDuration, formatDuration } from '../intake/durationFormat';

export interface PlateInitial {
  plate_name: string;
  component_name?: string; // 父组件传入（用 components 查 id）
  quantity: number;
  duration_minutes: number;
}

interface Props {
  open: boolean;
  mode: 'create' | 'edit';
  initial?: PlateInitial;
  components: any[];
  onCancel: () => void;
  onSuccess: () => void;
}

export default function PlateModal({ open, mode, initial, components, onCancel, onSuccess }: Props) {
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const [durationText, setDurationText] = useState('');

  useEffect(() => {
    if (open) {
      form.resetFields();
      if (mode === 'edit' && initial) {
        form.setFieldsValue({
          plate_name: initial.plate_name,
          component_name: initial.component_name,
          quantity: initial.quantity,
        });
        setDurationText(formatDuration(initial.duration_minutes));
      } else {
        form.setFieldsValue({ plate_name: '', component_name: undefined, quantity: 1 });
        setDurationText('');
      }
    }
  }, [open, mode, initial, form]);

  const handleOk = async () => {
    try {
      const values = await form.validateFields();
      const minutes = parseDuration(durationText);
      if (minutes === null || minutes <= 0) {
        message.error('耗时格式无效，请输入如 "2h43m" 或 "45m"');
        return;
      }
      setSubmitting(true);
      let res: any;
      if (mode === 'create') {
        res = await api.catalog.addPlate({
          plate_name: values.plate_name.trim(),
          component_name: values.component_name,
          quantity: values.quantity,
          duration_minutes: minutes,
        });
      } else {
        res = await api.catalog.updatePlate(initial!.plate_name, {
          component_name: values.component_name,
          quantity: values.quantity,
          duration_minutes: minutes,
        });
      }
      if (res.ok) {
        message.success(mode === 'create' ? `已新增打印盘 ${values.plate_name}` : `已更新打印盘 ${initial!.plate_name}`);
        onSuccess();
      } else {
        message.error(res.error || '操作失败');
      }
    } catch (e: any) {
      if (e?.errorFields) return;
      message.error(e.message || '操作失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={mode === 'create' ? '新增打印盘' : `编辑打印盘：${initial?.plate_name}`}
      width={480}
      onCancel={onCancel}
      onOk={handleOk}
      confirmLoading={submitting}
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        <Form.Item
          label="盘号"
          name="plate_name"
          rules={[{ required: true, message: '请输入盘号' }]}
        >
          <Input disabled={mode === 'edit'} placeholder="如：底座-A" />
        </Form.Item>
        <Form.Item
          label="所属组件"
          name="component_name"
          rules={[{ required: true, message: '请选择组件' }]}
        >
          <Select
            placeholder="选择组件"
            options={components.map((c) => ({ label: c.name, value: c.name }))}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item
          label="单盘件数"
          name="quantity"
          rules={[{ required: true, message: '请输入件数' }]}
        >
          <InputNumber min={1} precision={0} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item label="耗时" required tooltip="格式：2h43m / 45m / 1h">
          <Input
            value={durationText}
            onChange={(e) => setDurationText(e.target.value)}
            placeholder="例如：2h43m"
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
