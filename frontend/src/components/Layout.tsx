import { Layout, Menu } from 'antd';
import {
  DashboardOutlined,
  AppstoreOutlined,
  ShoppingCartOutlined,
  InboxOutlined,
  ScheduleOutlined,
  SettingOutlined,
  ScanOutlined,
} from '@ant-design/icons';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';

const { Sider, Content } = Layout;

// 构建时由 Vite 注入；Dockerfile 把 release tag 通过 build-arg 传进容器再 export 为 VITE_APP_VERSION
const APP_VERSION = import.meta.env.VITE_APP_VERSION || 'dev';

const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
  { key: '/products', icon: <AppstoreOutlined />, label: '产品目录' },
  { key: '/intake', icon: <ScanOutlined />, label: '产品录入' },
  { key: '/orders', icon: <ShoppingCartOutlined />, label: '订单管理' },
  { key: '/inventory', icon: <InboxOutlined />, label: '库存管理' },
  { key: '/schedule', icon: <ScheduleOutlined />, label: '排班中心' },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
];

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        breakpoint="lg"
        collapsedWidth={60}
        style={{ display: 'flex', flexDirection: 'column' }}
      >
        <div style={{ color: '#fff', textAlign: 'center', padding: '16px 0', fontSize: 18, fontWeight: 600 }}>
          打印排班
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ flex: 1 }}
        />
        <div
          style={{
            color: 'rgba(255,255,255,0.45)',
            textAlign: 'center',
            padding: '8px 0',
            fontSize: 11,
            fontFamily: 'monospace',
            borderTop: '1px solid rgba(255,255,255,0.08)',
          }}
          title="部署版本（用于确认前端是否为最新构建）"
        >
          {APP_VERSION}
        </div>
      </Sider>
      <Layout>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
