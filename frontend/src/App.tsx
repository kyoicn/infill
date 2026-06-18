import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Spin } from 'antd';
import AppLayout from './components/Layout';
import Dashboard from './pages/Dashboard';
import Products from './pages/Products';
import Intake from './pages/Intake';
import Orders from './pages/Orders';
import Inventory from './pages/Inventory';
import Schedule from './pages/Schedule';
import Settings from './pages/Settings';
import AutoImportSettings from './pages/settings/AutoImportSettings';

const AutoImport = lazy(() => import('./pages/AutoImport'));

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/products" element={<Products />} />
          <Route path="/intake" element={<Intake />} />
          <Route path="/orders" element={<Orders />} />
          <Route
            path="/orders/import"
            element={
              <Suspense fallback={<Spin />}>
                <AutoImport />
              </Suspense>
            }
          />
          <Route path="/inventory" element={<Inventory />} />
          <Route path="/schedule" element={<Schedule />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/settings/auto-import" element={<AutoImportSettings />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
