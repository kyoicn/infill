import { useEffect, useState, type CSSProperties } from 'react';
import { Card, Table, Button, Space, Popconfirm, message } from 'antd';
import { ReloadOutlined, PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import { api } from '../api/client';
import ComponentModal, { type ComponentInitial } from './products/ComponentModal';
import PlateModal, { type PlateInitial } from './products/PlateModal';
import ProductModal, { type ProductInitial } from './products/ProductModal';

const skuCellStyle: CSSProperties = {
  fontFamily: '"SF Mono", Menlo, Consolas, monospace',
  fontSize: 12,
  color: 'rgba(0,0,0,0.55)',
};

export default function Products() {
  const [components, setComponents] = useState<any[]>([]);
  const [products, setProducts] = useState<any[]>([]);
  const [allConfigs, setAllConfigs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  // Modal state
  const [compModal, setCompModal] = useState<{ open: boolean; mode: 'create' | 'edit'; initial?: ComponentInitial }>({ open: false, mode: 'create' });
  const [plateModal, setPlateModal] = useState<{ open: boolean; mode: 'create' | 'edit'; initial?: PlateInitial }>({ open: false, mode: 'create' });
  const [prodModal, setProdModal] = useState<{ open: boolean; mode: 'create' | 'edit'; initial?: ProductInitial }>({ open: false, mode: 'create' });

  const reload = () => {
    api.getComponents().then(setComponents);
    api.getProducts().then(setProducts);
    api.getAllConfigs().then(setAllConfigs);
  };

  useEffect(() => { reload(); }, []);

  const reloadCatalog = async () => {
    setLoading(true);
    try {
      const res = await api.reloadCatalog();
      if (res.ok) {
        message.success(`目录已重新加载：${res.stats.组件} 个组件，${res.stats.打印盘} 个打印盘，${res.stats.产品} 个产品`);
        reload();
      } else {
        message.error(`加载失败：${res.error}`);
      }
    } catch (e: any) {
      message.error(e.message);
    } finally {
      setLoading(false);
    }
  };

  const getCompName = (id: number) => components.find(c => c.id === id)?.name || `#${id}`;
  const getCompSku = (id: number) => components.find(c => c.id === id)?.sku || '';

  // ---------- 删除处理 ----------
  const handleDeleteComponent = async (sku: string, name: string) => {
    try {
      const res = await api.catalog.deleteComponent(sku);
      if (res.ok) {
        message.success(`已删除组件 ${name}`);
        reload();
      } else if (res.error_kind === 'component_in_use') {
        message.warning(res.error || `组件 ${name} 仍被引用，无法删除`);
      } else {
        message.error(res.error || '删除失败');
      }
    } catch (e: any) {
      message.error(e.message || '删除失败');
    }
  };

  const handleDeletePlate = async (sku: string, plateName: string) => {
    try {
      const res = await api.catalog.deletePlate(sku);
      if (res.ok) {
        message.success(`已删除打印盘 ${plateName}`);
        reload();
      } else {
        message.error(res.error || '删除失败');
      }
    } catch (e: any) {
      message.error(e.message || '删除失败');
    }
  };

  const handleDeleteProduct = async (sku: string, name: string) => {
    try {
      const res = await api.catalog.deleteProduct(sku);
      if (res.ok) {
        message.success(`已删除产品 ${name}`);
        reload();
      } else if (res.error_kind === 'product_in_use') {
        message.warning(res.error || `产品 ${name} 仍被引用，无法删除`);
      } else {
        message.error(res.error || '删除失败');
      }
    } catch (e: any) {
      message.error(e.message || '删除失败');
    }
  };

  const handleMigrateToSku = async () => {
    setLoading(true);
    try {
      const res = await api.catalog.migrateToSku();
      if (res.ok) {
        message.success('已迁移到 SKU');
        reload();
      } else {
        message.error(res.error || '迁移失败');
      }
    } catch (e: any) {
      message.error(e.message || '迁移失败');
    } finally {
      setLoading(false);
    }
  };

  // ---------- 编辑：从行数据映射到 Modal initial ----------
  const editProduct = (rec: any) => {
    const bom = (rec.bom_items || []).map((b: any) => ({
      component_sku: getCompSku(b.component_id),
      color: b.color || undefined,
      quantity: b.quantity,
    }));
    setProdModal({ open: true, mode: 'edit', initial: { sku: rec.sku, name: rec.name, description: rec.description, bom } });
  };

  const editComponent = (rec: any) => {
    setCompModal({ open: true, mode: 'edit', initial: { sku: rec.sku, name: rec.name, description: rec.description, colors: rec.colors || [] } });
  };

  const editPlate = (rec: any) => {
    setPlateModal({
      open: true,
      mode: 'edit',
      initial: {
        sku: rec.sku,
        plate_name: rec.plate_name,
        component_sku: getCompSku(rec.component_id),
        quantity: rec.quantity,
        duration_minutes: rec.duration_minutes,
      },
    });
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>产品目录</h2>
        <Space>
          <Button loading={loading} onClick={handleMigrateToSku}>
            迁移到 SKU
          </Button>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={reloadCatalog}>
            重新加载目录
          </Button>
        </Space>
      </div>
      <p style={{ color: '#999', marginBottom: 16 }}>
        数据源：catalog.yaml — 可在下方直接编辑，或修改文件后点击"重新加载目录"，效果一样
      </p>

      {/* 产品 */}
      <Card
        title="产品列表"
        style={{ marginBottom: 24 }}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setProdModal({ open: true, mode: 'create' })}>
            新增产品
          </Button>
        }
      >
        <Table
          dataSource={products}
          rowKey="sku"
          size="small"
          pagination={false}
          columns={[
            { title: 'SKU', dataIndex: 'sku', width: 90, render: (v: string) => <span style={skuCellStyle}>{v}</span> },
            { title: '名称', dataIndex: 'name' },
            { title: '描述', dataIndex: 'description' },
            {
              title: 'BOM',
              render: (_: any, rec: any) =>
                rec.bom_items?.map((b: any) => {
                  const color = b.color ? `(${b.color})` : '';
                  return `${getCompName(b.component_id)}${color} x${b.quantity}`;
                }).join(', '),
            },
            {
              title: '操作',
              width: 160,
              render: (_: any, rec: any) => (
                <Space size="small">
                  <Button size="small" icon={<EditOutlined />} onClick={() => editProduct(rec)}>
                    编辑
                  </Button>
                  <Popconfirm title={`删除产品 ${rec.name}？`} onConfirm={() => handleDeleteProduct(rec.sku, rec.name)} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}>
                    <Button size="small" danger icon={<DeleteOutlined />}>
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {/* 组件 */}
      <Card
        title="组件列表"
        style={{ marginBottom: 24 }}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCompModal({ open: true, mode: 'create' })}>
            新增组件
          </Button>
        }
      >
        <Table
          dataSource={components}
          rowKey="sku"
          size="small"
          pagination={false}
          columns={[
            { title: 'SKU', dataIndex: 'sku', width: 90, render: (v: string) => <span style={skuCellStyle}>{v}</span> },
            { title: '名称', dataIndex: 'name' },
            { title: '描述', dataIndex: 'description' },
            { title: '可选颜色', render: (_: any, rec: any) =>
              rec.colors?.length > 0 ? rec.colors.join('、') : <span style={{ color: '#999' }}>无</span>,
            },
            {
              title: '打印盘',
              render: (_: any, rec: any) => {
                const cfgs = allConfigs.filter(c => c.component_id === rec.id);
                return cfgs.length > 0
                  ? cfgs.map(c => `${c.plate_name}(x${c.quantity})`).join(', ')
                  : <span style={{ color: '#999' }}>无</span>;
              },
            },
            {
              title: '操作',
              width: 160,
              render: (_: any, rec: any) => (
                <Space size="small">
                  <Button size="small" icon={<EditOutlined />} onClick={() => editComponent(rec)}>
                    编辑
                  </Button>
                  <Popconfirm title={`删除组件 ${rec.name}？`} onConfirm={() => handleDeleteComponent(rec.sku, rec.name)} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}>
                    <Button size="small" danger icon={<DeleteOutlined />}>
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {/* 打印盘 */}
      <Card
        title="打印盘"
        style={{ marginBottom: 24 }}
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setPlateModal({ open: true, mode: 'create' })}>
            新增打印盘
          </Button>
        }
      >
        <Table
          dataSource={allConfigs}
          rowKey="sku"
          size="small"
          pagination={false}
          columns={[
            { title: 'SKU', dataIndex: 'sku', width: 90, render: (v: string) => <span style={skuCellStyle}>{v}</span> },
            { title: '盘号', dataIndex: 'plate_name', width: 120 },
            { title: '组件', dataIndex: 'component_id', render: (v: number) => getCompName(v) },
            { title: '数量', dataIndex: 'quantity', width: 80 },
            { title: '耗时(分钟)', dataIndex: 'duration_minutes', width: 110 },
            {
              title: '操作',
              width: 160,
              render: (_: any, rec: any) => (
                <Space size="small">
                  <Button size="small" icon={<EditOutlined />} onClick={() => editPlate(rec)}>
                    编辑
                  </Button>
                  <Popconfirm title={`删除打印盘 ${rec.plate_name}？`} onConfirm={() => handleDeletePlate(rec.sku, rec.plate_name)} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}>
                    <Button size="small" danger icon={<DeleteOutlined />}>
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <ComponentModal
        open={compModal.open}
        mode={compModal.mode}
        initial={compModal.initial}
        onCancel={() => setCompModal((s) => ({ ...s, open: false }))}
        onSuccess={() => { setCompModal((s) => ({ ...s, open: false })); reload(); }}
      />
      <PlateModal
        open={plateModal.open}
        mode={plateModal.mode}
        initial={plateModal.initial}
        components={components}
        onCancel={() => setPlateModal((s) => ({ ...s, open: false }))}
        onSuccess={() => { setPlateModal((s) => ({ ...s, open: false })); reload(); }}
      />
      <ProductModal
        open={prodModal.open}
        mode={prodModal.mode}
        initial={prodModal.initial}
        components={components}
        onCancel={() => setProdModal((s) => ({ ...s, open: false }))}
        onSuccess={() => { setProdModal((s) => ({ ...s, open: false })); reload(); }}
      />
    </div>
  );
}
