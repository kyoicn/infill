import { useEffect, useState } from 'react';
import { Modal, Form, Input, Select, InputNumber, Button, Space, message } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { api } from '../../api/client';

export interface BomRow {
  component_name: string;
  color?: string;
  quantity: number;
}

export interface ProductInitial {
  name: string;
  description?: string;
  bom?: BomRow[];
}

interface Props {
  open: boolean;
  mode: 'create' | 'edit';
  initial?: ProductInitial;
  components: any[];
  onCancel: () => void;
  onSuccess: () => void;
}

export default function ProductModal({ open, mode, initial, components, onCancel, onSuccess }: Props) {
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const [bom, setBom] = useState<BomRow[]>([]);

  useEffect(() => {
    if (open) {
      form.resetFields();
      if (mode === 'edit' && initial) {
        form.setFieldsValue({
          name: initial.name,
          description: initial.description || '',
        });
        setBom(initial.bom ? initial.bom.map((b) => ({ ...b })) : []);
      } else {
        form.setFieldsValue({ name: '', description: '' });
        setBom([{ component_name: '', color: undefined, quantity: 1 }]);
      }
    }
  }, [open, mode, initial, form]);

  const updateRow = (idx: number, patch: Partial<BomRow>) => {
    setBom((prev) => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  };

  const removeRow = (idx: number) => {
    setBom((prev) => prev.filter((_, i) => i !== idx));
  };

  const addRow = () => {
    setBom((prev) => [...prev, { component_name: '', color: undefined, quantity: 1 }]);
  };

  const getColorOptions = (componentName: string) => {
    const c = components.find((x) => x.name === componentName);
    return (c?.colors || []).map((col: string) => ({ label: col, value: col }));
  };

  const handleOk = async () => {
    try {
      const values = await form.validateFields();
      // BOM 校验
      const cleaned: BomRow[] = [];
      for (let i = 0; i < bom.length; i++) {
        const row = bom[i];
        if (!row.component_name) {
          message.error(`BOM 第 ${i + 1} 行：请选择组件`);
          return;
        }
        if (!row.quantity || row.quantity < 1) {
          message.error(`BOM 第 ${i + 1} 行：件数必须为正整数`);
          return;
        }
        cleaned.push({
          component_name: row.component_name,
          color: row.color || undefined,
          quantity: row.quantity,
        });
      }
      if (cleaned.length === 0) {
        message.error('请至少添加一个 BOM 项');
        return;
      }
      setSubmitting(true);
      let res: any;
      if (mode === 'create') {
        res = await api.catalog.addProduct({
          name: values.name.trim(),
          description: values.description?.trim() || undefined,
          bom: cleaned,
        });
      } else {
        res = await api.catalog.updateProduct(initial!.name, {
          description: values.description?.trim() || '',
          bom: cleaned,
        });
      }
      if (res.ok) {
        message.success(mode === 'create' ? `已新增产品 ${values.name}` : `已更新产品 ${initial!.name}`);
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
      title={mode === 'create' ? '新增产品' : `编辑产品：${initial?.name}`}
      width={720}
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
          <Input disabled={mode === 'edit'} placeholder="如：小恐龙摆件" />
        </Form.Item>
        <Form.Item label="描述" name="description">
          <Input.TextArea rows={2} placeholder="可选" />
        </Form.Item>
      </Form>

      <div style={{ marginTop: 8 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <span style={{ fontWeight: 500 }}>BOM</span>
          <Button size="small" icon={<PlusOutlined />} onClick={addRow}>
            添加组件
          </Button>
        </div>
        {bom.length === 0 && (
          <div style={{ color: '#999', padding: '12px 0' }}>暂无 BOM 项，点击右上「添加组件」</div>
        )}
        {bom.map((row, idx) => (
          <Space.Compact key={idx} style={{ display: 'flex', marginBottom: 8 }}>
            <Select
              style={{ flex: 2 }}
              placeholder="组件"
              value={row.component_name || undefined}
              onChange={(v) => updateRow(idx, { component_name: v, color: undefined })}
              options={components.map((c) => ({ label: c.name, value: c.name }))}
              showSearch
              optionFilterProp="label"
            />
            <Select
              style={{ flex: 1.5 }}
              placeholder="颜色（可选）"
              value={row.color || undefined}
              onChange={(v) => updateRow(idx, { color: v })}
              allowClear
              options={getColorOptions(row.component_name)}
              disabled={!row.component_name}
            />
            <InputNumber
              style={{ flex: 1 }}
              min={1}
              precision={0}
              placeholder="件数"
              value={row.quantity}
              onChange={(v) => updateRow(idx, { quantity: (v as number) || 1 })}
            />
            <Button danger icon={<DeleteOutlined />} onClick={() => removeRow(idx)} />
          </Space.Compact>
        ))}
      </div>
    </Modal>
  );
}
