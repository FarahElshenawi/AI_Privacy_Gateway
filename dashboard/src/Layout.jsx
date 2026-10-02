import { NavLink, useLocation, Outlet } from 'react-router-dom'
import { ShieldCheck, BarChart3, Settings, ScrollText, Home } from 'lucide-react'

export default function Layout() {
  const location = useLocation()

  const navItems = [
    { to: '/app', label: 'Verification', icon: BarChart3 },
    { to: '/app/policy', label: 'Policy Config', icon: Settings },
    { to: '/app/audit', label: 'Audit Log', icon: ScrollText },
  ]

  return (
    <div className="app-container">
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="logo-icon">
            <ShieldCheck size={28} color="#4f8cff" />
          </div>
          <div>
            <div className="logo-text">Doppel</div>
            <div className="logo-subtext">Control Plane</div>
          </div>
        </div>

        <nav className="nav-menu">
          {navItems.map((item) => {
            const Icon = item.icon
            const isActive = location.pathname === item.to
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={isActive ? 'nav-item active' : 'nav-item'}
                end={item.to === '/app'}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </NavLink>
            )
          })}
        </nav>

        <div className="sidebar-footer">
          <NavLink to="/" className="nav-item">
            <Home size={18} />
            <span>Back to Landing</span>
          </NavLink>
          <div className="status-pill">
            <span className="status-dot online"></span>
            <span>Cloud Backend Online</span>
          </div>
          <div className="version-text">v1.0.0</div>
        </div>
      </aside>

      <main className="main-content">
        <Outlet />
      </main>
    </div>
  )
}