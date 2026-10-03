import { NavLink, Outlet } from 'react-router-dom'

const links = [
  ['/', '⌂', 'Dashboard'], ['/find', '＋', 'Find candidates'], ['/workflows', '◷', 'Workflows'],
  ['/candidates', '◎', 'Candidates'], ['/emails', '▤', 'Email review'], ['/outreach', '↗', 'Outreach'], ['/analytics', '▥', 'Analytics'],
]
export function AppShell() {
  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href="/" aria-label="CandidateFlow home"><span className="brand-icon">C</span><span>candidate<span className="brand-light">flow</span><small>RESEARCH OPERATIONS</small></span></a>
      <p className="nav-caption">WORKSPACE</p><nav aria-label="Primary navigation">{links.map(([to, icon, title]) => <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}><span className="nav-icon" aria-hidden="true">{icon}</span>{title}</NavLink>)}</nav>
      <div className="sidebar-bottom"><span className="live-dot" /> API workspace <small>Local development</small></div>
    </aside>
    <main className="main-area"><header className="topbar"><div className="breadcrumb">Research workspace <span>/</span> <strong>Evidence-led discovery</strong></div><div className="topbar-right"><span className="demo-pill">MOCK EMAIL PROVIDER</span><span className="avatar" aria-label="Local user">L</span></div></header><section className="page-content"><Outlet /></section></main>
  </div>
}
