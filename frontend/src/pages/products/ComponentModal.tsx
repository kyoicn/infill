import { useEffect, useState } from 'react';
import { Modal, Form, Input, Select, message } from 'antd';
import { api } from '../../api/client';

export interface ComponentInitial {
  sku: string;
  name: string;
  description?: string;
  colors?: string[];
}

interface Props {
  open: boolean;
  mode: 'create' | 'edit';
  initial?: ComponentInitial;
  onCancel: () => void;
  onSuccess: () => void;
}

export default function ComponentModal({ open, mode, initial, onCancel, onSuccess }: Props) {
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      form.resetFields();
      if (mode === 'edit' && initial) {
        form.setFieldsValue({
          name: initial.name,
          description: initial.description || '',
          colors: initial.colors || [],
        });
      } else {
        form.setFieldsValue({ name: '', description: '', colors: [] });
      }
    }
  }, [open, mode, initial, form]);

  const handleOk = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      const colors = (values.colors || []).map((c: string) => c.trim()).filter(Boolean);
      let res: any;
      if (mode === 'create') {
        res = await api.catalog.addComponent({
          name: values.name.trim(),
          description: values.description?.trim() || undefined,
          colors,
        });
      } else {
        res = await api.catalog.updateComponent(initial!.sku, {
          name: values.name.trim(),
          description: values.description?.trim() || '',
          colors,
        });
      }
      if (res.ok) {
        message.success(mode === 'create' ? `已新增组件 ${values.name}` : `已更新组件 ${initial!.name}`);
        onSuccess();
      } else {
        message.error(res.error || '操作失败');
      }
    } catch (e: any) {
      if (e?.errorFields) return; // form validation
      message.error(e.message || '操作失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={mode === 'create' ? '新增组件' : `编辑组件：${initial?.name}`}
      width={480}
      onCancel={onCancel}
      onOk={handleOk}
      confirmLoading={submitting}
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        {mode === 'edit' && (
          <Form.Item label="SKU">
            <Input
              value={initial?.sku}
              disabled
              style={{ fontFamily: '"SF Mono", Menlo, Consolas, monospace' }}
            />
          </Form.Item>
        )}
        <Form.Item
          label="名称"
          name="name"
          rules={[{ required: true, message: '请输入名称' }]}
        >
          <Input placeholder="如：底座" />
        </Form.Item>
        <Form.Item label="描述" name="description">
          <Input.TextArea rows={2} placeholder="可选" />
        </Form.Item>
        <Form.Item label="可选颜色" name="colors" tooltip="回车确认；不填表示无色">
          <Select mode="tags" placeholder="输入颜色名后回车" tokenSeparators={[',']} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
