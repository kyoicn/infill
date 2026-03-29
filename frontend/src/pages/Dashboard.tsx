import { useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, Table, Tag } from 'antd';
import { ShoppingCartOutlined, InboxOutlined, PrinterOutlined } from '@ant-design/icons';
import { api } from '../api/client';

export default function Dashboard() {
  const [orders, setOrders] = useState<any[]>([]);
  const [surplus, setSurplus] = useState<any[]>([]);
  const [printers, setPrinters] = useState<any[]>([]);

  useEffect(() => {
    api.getOrders('pending').then(setOrders).catch(() => {});
    api.getSurplus().then(setSurplus).catch(() => {});
    api.getPrinters().then(setPrinters).catch(() => {});
  }, []);

  const lowStock = surplus.filter(s => s.surplus < 0);

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>仪表盘</h2>
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={8}>
          <Card>
            <Statistic title="待处理订单" value={orders.length} prefix={<ShoppingCartOutlined />} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="打印机数量" value={printers.length} prefix={<PrinterOutlined />} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="库存预警"
              value={lowStock.length}
              prefix={<InboxOutlined />}
              valueStyle={lowStock.length > 0 ? { color: '#cf1322' } : undefined}
            />
          </Card>
        </Col>
      </Row>

      <Card title="组件库存与需求" style={{ marginBottom: 24 }}>
        <Table
          dataSource={surplus}
          rowKey="component_id"
          size="small"
          pagination={false}
          columns={[
            { title: '组件', dataIndex: 'component_name' },
            { title: '库存', dataIndex: 'stock' },
            { title: '订单需求', dataIndex: 'demand' },
            {
              title: '富余',
              dataIndex: 'surplus',
              render: (v: number) => (
                <Tag color={v >= 0 ? 'green' : 'red'}>{v >= 0 ? `+${v}` : v}</Tag>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
