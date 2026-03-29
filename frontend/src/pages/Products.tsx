import { useEffect, useState } from 'react';
import { Card, Table, Button, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { api } from '../api/client';

export default function Products() {
  const [components, setComponents] = useState<any[]>([]);
  const [products, setProducts] = useState<any[]>([]);
  const [allConfigs, setAllConfigs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

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

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>产品目录</h2>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={reloadCatalog}>
          重新加载目录
        </Button>
      </div>
      <p style={{ color: '#999', marginBottom: 16 }}>
        数据源：catalog.yaml — 修改文件后点击"重新加载目录"生效
      </p>

      {/* 产品 */}
      <Card title="产品列表" style={{ marginBottom: 24 }}>
        <Table
          dataSource={products}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '描述', dataIndex: 'description' },
            {
              title: 'BOM',
              render: (_: any, rec: any) =>
                rec.bom_items?.map((b: any) => `${getCompName(b.component_id)} x${b.quantity}`).join(', '),
            },
          ]}
        />
      </Card>

      {/* 组件 */}
      <Card title="组件列表" style={{ marginBottom: 24 }}>
        <Table
          dataSource={components}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            { title: '名称', dataIndex: 'name' },
            { title: '描述', dataIndex: 'description' },
            {
              title: '打印盘',
              render: (_: any, rec: any) => {
                const cfgs = allConfigs.filter(c => c.component_id === rec.id);
                return cfgs.length > 0
                  ? cfgs.map(c => `${c.plate_name}(x${c.quantity})`).join(', ')
                  : <span style={{ color: '#999' }}>无</span>;
              },
            },
          ]}
        />
      </Card>

      {/* 打印盘 */}
      <Card title="打印盘" style={{ marginBottom: 24 }}>
        <Table
          dataSource={allConfigs}
          rowKey="id"
          size="small"
          pagination={false}
          columns={[
            { title: '盘号', dataIndex: 'plate_name', width: 120 },
            { title: '组件', dataIndex: 'component_id', render: (v: number) => getCompName(v) },
            { title: '数量', dataIndex: 'quantity', width: 80 },
            { title: '耗时(分钟)', dataIndex: 'duration_minutes', width: 110 },
          ]}
        />
      </Card>
    </div>
  );
}
