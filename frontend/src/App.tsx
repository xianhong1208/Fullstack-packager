import { lazy, Suspense, useState } from 'react'
import { ConfigProvider, Spin, Modal, Drawer } from 'antd'
import zhTW from 'antd/locale/zh_TW'
import { BrowserRouter, Routes, Route, Navigate, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  HomeOutlined,
  PlusOutlined,
  HistoryOutlined,
  BuildOutlined,
  LogoutOutlined,
  SettingOutlined,
  TeamOutlined,
  SafetyOutlined,
  KeyOutlined,
  DashboardOutlined,
  ClockCircleOutlined,
  MenuOutlined,
} from '@ant-design/icons'

// Route-level code-splitting: lazy-load heavy page components so they are
// fetched on demand instead of shipping in the single initial JS chunk.
const Dashboard = lazy(() => import('./pages/Dashboard'))
const CreateTask = lazy(() => import('./pages/CreateTask'))
const History = lazy(() => import('./pages/History'))
const TaskDetail = lazy(() => import('./pages/TaskDetail'))
const HistoryDetail = lazy(() => import('./pages/HistoryDetail'))
const LoginPage = lazy(() => import('./pages/LoginPage'))
const ForgotPasswordPage = lazy(() => import('./pages/ForgotPasswordPage'))
const SecuritySettingsPage = lazy(() => import('./pages/SecuritySettingsPage'))
const UserManagementPage = lazy(() => import('./pages/admin/UserManagementPage'))
const UserDetailPage = lazy(() => import('./pages/admin/UserDetailPage'))
const RoleManagementPage = lazy(() => import('./pages/admin/RoleManagementPage'))
const GitCredentialsPage = lazy(() => import('./pages/admin/GitCredentialsPage'))
const MonitoringPage = lazy(() => import('./pages/MonitoringPage'))
import { useTaskStore } from './store/taskStore'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { PermissionGate } from './components/PermissionGate'
import { useIdleTimeout } from './hooks/useIdleTimeout'

/** 429 is the one status where retrying is guaranteed to make things worse.
 *
 * The limiter counts the retry too, so a client that retries on 429 pushes
 * itself further past the limit and stays there: every query on the page
 * fails, each one retries, and the window never gets a chance to drain. What
 * the user sees is an application that has stopped working rather than one
 * asking them to slow down. Everything else keeps its single retry.
 */
const isRateLimited = (error: unknown): boolean =>
  (error as { response?: { status?: number } })?.response?.status === 429

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => !isRateLimited(error) && failureCount < 1,
    },
    mutations: {
      retry: false,
    },
  },
})

// Protected route wrapper
function ProtectedRoute() {
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Spin size="large" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return <Outlet />
}

// Permission-protected route wrapper
function PermissionRoute({ permission, children }: { permission: string | string[]; children: React.ReactNode }) {
  const { hasAnyPermission } = useAuth()

  const permissions = Array.isArray(permission) ? permission : [permission]
  const hasAccess = hasAnyPermission(...permissions)

  if (!hasAccess) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

// Main layout with cyber design
function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const { user, logout } = useAuth()
  const tasks = useTaskStore((state) => state.tasks)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const activeCount = Array.from(tasks.values()).filter(
    (t) => t.status === 'pending' || t.status === 'running'
  ).length

  const navItems = [
    { key: '/', icon: <HomeOutlined />, label: '儀表板', badge: activeCount },
    { key: '/create', icon: <PlusOutlined />, label: '建立任務' },
    { key: '/history', icon: <HistoryOutlined />, label: '建置歷史' },
    { key: '/monitor', icon: <DashboardOutlined />, label: '系統監控' },
  ]

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  // Idle timeout auto-logout
  const { showWarning, remainingSeconds, resetTimer } = useIdleTimeout({
    onLogout: handleLogout,
    enabled: true,
  })

  const formatCountdown = (seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  // Get role display name
  const getRoleDisplay = () => {
    if (user?.role?.display_name) {
      return user.role.display_name
    }
    return 'User'
  }

  // Navigate then close the mobile drawer (no-op on desktop).
  const handleNav = (path: string) => {
    navigate(path)
    setMobileNavOpen(false)
  }

  // Shared sidebar content, reused by the desktop sidebar and the mobile drawer.
  const sidebarContent = (
    <div className="h-full flex flex-col bg-void-900">
      {/* Logo */}
      <div className="h-16 flex items-center gap-3 px-5 border-b border-gray-700/50">
        <div className="w-9 h-9 rounded-md bg-cyber-500 flex items-center justify-center">
          <BuildOutlined className="text-lg text-void-950" />
        </div>
        <span className="text-lg font-semibold text-white" style={{ fontFamily: 'var(--font-display)' }}>
          Build Center
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 space-y-1 overflow-y-auto">
        {navItems.map((item) => (
          <button
            key={item.key}
            onClick={() => handleNav(item.key)}
            className={`nav-item w-full ${location.pathname === item.key ? 'active' : ''}`}
          >
            <span className="text-lg">{item.icon}</span>
            <span className="flex-1 text-left">{item.label}</span>
            {item.badge ? (
              <span className="px-2 py-0.5 text-xs rounded-full bg-cyber-500/20 text-cyber-400 border border-cyber-500/30">
                {item.badge}
              </span>
            ) : null}
          </button>
        ))}

        {/* Settings */}
        <button
          onClick={() => handleNav('/settings/security')}
          className={`nav-item w-full ${location.pathname.startsWith('/settings') ? 'active' : ''}`}
        >
          <span className="text-lg"><SettingOutlined /></span>
          <span className="flex-1 text-left">安全設定</span>
        </button>

        {/* Admin: User & Role Management */}
        <PermissionGate permission={['user:view', 'role:view']}>
          <div className="pt-4 mt-4 border-t border-gray-700/30">
            <p className="px-3 mb-2 text-xs font-medium text-gray-500 uppercase tracking-wider">
              系統管理
            </p>
            <PermissionGate permission="user:view">
              <button
                onClick={() => handleNav('/admin/users')}
                className={`nav-item w-full ${location.pathname.startsWith('/admin/users') ? 'active' : ''}`}
              >
                <span className="text-lg"><TeamOutlined /></span>
                <span className="flex-1 text-left">使用者管理</span>
              </button>
            </PermissionGate>
            <PermissionGate permission="role:view">
              <button
                onClick={() => handleNav('/admin/roles')}
                className={`nav-item w-full ${location.pathname === '/admin/roles' ? 'active' : ''}`}
              >
                <span className="text-lg"><SafetyOutlined /></span>
                <span className="flex-1 text-left">角色管理</span>
              </button>
            </PermissionGate>
            <PermissionGate permission="user:manage">
              <button
                onClick={() => handleNav('/settings/git-credentials')}
                className={`nav-item w-full ${location.pathname === '/settings/git-credentials' ? 'active' : ''}`}
              >
                <span className="text-lg"><KeyOutlined /></span>
                <span className="flex-1 text-left">Git 憑證</span>
              </button>
            </PermissionGate>
          </div>
        </PermissionGate>
      </nav>

      {/* User section */}
      <div className="p-4 border-t border-gray-700/50">
        <div className="glass-card p-3">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-full bg-void-700 border border-cyber-500/40 flex items-center justify-center">
              <span className="text-sm font-semibold text-cyber-300">
                {user?.username?.charAt(0).toUpperCase()}
              </span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-white truncate">
                {user?.username}
              </p>
              <p className="text-xs text-gray-500">{getRoleDisplay()}</p>
            </div>
            <button
              onClick={handleLogout}
              className="p-2 rounded-lg text-gray-400 hover:text-alert-400 hover:bg-alert-500/10 transition-colors"
              title="登出"
              aria-label="登出"
            >
              <LogoutOutlined />
            </button>
          </div>
        </div>
      </div>
    </div>
  )

  return (
    <div className="min-h-screen flex">
      {/* Desktop sidebar: fixed on wide screens, hidden below the lg breakpoint */}
      <aside className="hidden lg:flex w-64 flex-col border-r border-gray-700/50">
        {sidebarContent}
      </aside>

      {/* Mobile hamburger: shown only below the lg breakpoint */}
      <button
        type="button"
        onClick={() => setMobileNavOpen(true)}
        aria-label="開啟導覽選單"
        className="lg:hidden fixed top-4 left-4 z-40 w-10 h-10 rounded-md flex items-center justify-center border border-void-600 bg-void-900 text-cyber-300 hover:border-cyber-500 transition-colors"
      >
        <MenuOutlined className="text-lg" />
      </button>

      {/* Mobile navigation drawer: reuses the exact same sidebar content */}
      <Drawer
        placement="left"
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
        closable={false}
        width={256}
        title="導覽選單"
        rootClassName="lg:hidden"
        styles={{
          body: { padding: 0, background: '#0e1319' },
          header: { background: '#0e1319', borderBottom: '1px solid rgba(55, 65, 81, 0.5)' },
        }}
      >
        {sidebarContent}
      </Drawer>

      {/* Main content — Suspense boundary sits INSIDE the authenticated layout
          so the sidebar stays visible while a lazy route page loads */}
      <main className="flex-1 overflow-auto px-6 pt-16 pb-10 lg:pt-8">
        <div className="mx-auto w-full max-w-[1440px]">
        <Suspense
          fallback={
            <div className="min-h-[60vh] flex items-center justify-center">
              <Spin size="large" />
            </div>
          }
        >
          <Outlet />
        </Suspense>
        </div>
      </main>

      {/* Idle timeout warning modal */}
      <Modal
        open={showWarning}
        title={
          <span className="flex items-center gap-2">
            <ClockCircleOutlined className="text-yellow-500" />
            Session Timeout Warning
          </span>
        }
        okText="Continue Session"
        cancelText="Logout Now"
        onOk={resetTimer}
        onCancel={handleLogout}
        closable={false}
        maskClosable={false}
      >
        <div className="py-4 text-center">
          <div className="text-4xl font-mono font-bold mb-3" style={{ color: remainingSeconds <= 60 ? '#ff4d4f' : '#faad14' }}>
            {formatCountdown(remainingSeconds)}
          </div>
          <p className="text-gray-400">
            You have been inactive. Your session will automatically end for security reasons.
          </p>
          <p className="text-gray-500 text-sm mt-2">
            Click "Continue Session" or perform any action to stay logged in.
          </p>
        </div>
      </Modal>
    </div>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      {/* locale alongside theme: without it antd's built-in strings stay
          English while everything around them is Chinese — Tour's
          "Next"/"Finish", Table pagination, Select's "No Data", Modal's
          "OK"/"Cancel", Empty's "No data". Theme gets noticed during
          development because it is always visible; these only surface in
          particular component states, so they survive to production. */}
      <ConfigProvider
        locale={zhTW}
        theme={{
          token: {
            // Instrument theme: flat cobalt primary on cool slate.
            colorPrimary: '#4c8df0',
            colorInfo: '#4c8df0',
            colorSuccess: '#33c489',
            colorWarning: '#e6a53a',
            colorError: '#ec5f5f',
            borderRadius: 7,
            fontFamily: "'Inter', system-ui, sans-serif",
            colorBgContainer: '#141b23',
            colorBgElevated: '#1b232d',
            colorBgLayout: '#0e1319',
            colorBgSpotlight: '#273241',
            colorText: '#e7ecf2',
            colorTextSecondary: '#93a0b0',
            colorTextTertiary: '#5e6b7a',
            colorBorder: '#34424f',
            colorBorderSecondary: '#273241',
          },
          components: {
            Modal: {
              contentBg: '#1b232d',
              headerBg: 'transparent',
              titleColor: '#e7ecf2',
              colorIcon: '#5e6b7a',
              colorIconHover: '#e7ecf2',
            },
            Form: {
              labelColor: '#93a0b0',
            },
            Input: {
              colorBgContainer: '#0e1319',
              colorBorder: '#273241',
              colorText: '#e7ecf2',
            },
            Select: {
              colorBgContainer: '#0e1319',
              colorBgElevated: '#1b232d',
              colorBorder: '#374151',
              optionSelectedBg: 'rgba(76, 141, 240, 0.2)',
            },
            Checkbox: {
              colorBgContainer: '#141b23',
              colorBorder: '#374151',
            },
          },
        }}
      >
        <AuthProvider>
          <BrowserRouter>
            <Suspense
              fallback={
                <div className="min-h-screen flex items-center justify-center">
                  <Spin size="large" />
                </div>
              }
            >
            <Routes>
              {/* Public routes */}
              <Route path="/login" element={<LoginPage />} />
              <Route path="/forgot-password" element={<ForgotPasswordPage />} />

              {/* Protected routes */}
              <Route element={<ProtectedRoute />}>
                <Route path="/" element={<AppLayout />}>
                  <Route index element={<Dashboard />} />
                  <Route path="create" element={<CreateTask />} />
                  <Route path="history" element={<History />} />
                  <Route path="history/:taskId" element={<HistoryDetail />} />
                  <Route path="task/:taskId" element={<TaskDetail />} />
                  <Route path="settings/security" element={<SecuritySettingsPage />} />
                  <Route path="monitor" element={<MonitoringPage />} />

                  {/* Admin routes */}
                  <Route
                    path="admin/users"
                    element={
                      <PermissionRoute permission="user:view">
                        <UserManagementPage />
                      </PermissionRoute>
                    }
                  />
                  <Route
                    path="admin/users/:userId"
                    element={
                      <PermissionRoute permission="user:view">
                        <UserDetailPage />
                      </PermissionRoute>
                    }
                  />
                  <Route
                    path="admin/roles"
                    element={
                      <PermissionRoute permission="role:view">
                        <RoleManagementPage />
                      </PermissionRoute>
                    }
                  />
                  <Route
                    path="settings/git-credentials"
                    element={
                      <PermissionRoute permission="user:manage">
                        <GitCredentialsPage />
                      </PermissionRoute>
                    }
                  />
                </Route>
              </Route>
            </Routes>
            </Suspense>
          </BrowserRouter>
        </AuthProvider>
      </ConfigProvider>
    </QueryClientProvider>
  )
}

export default App
