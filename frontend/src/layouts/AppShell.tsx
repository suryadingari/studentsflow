import { NavLink, Outlet } from 'react-router-dom'

const links = [
  ['/', '⌂', 'Dashboard'], ['/find', '＋', 'Find candidates'], ['/workflows', '◷', 'Workflows'],
  ['/candidates', '◎', 'Candidates'], ['/emails', '▤', 'Email review'], ['/outreach', '↗', 'Outreach'], ['/analytics', '▥', 'Analytics'],
]
export function AppShell() {
  const user = (() => { try { return JSON.parse(sessionStorage.getItem('studentsflow-user') || '{}') as { username?: string; role?: string } } catch { return {} } })()
  const visibleLinks = user.role === 'user' ? links.filter(([, , title]) => title !== 'Analytics') : links
  return <div className="app-shell">
    <aside className="sidebar"><a className="brand" href="/" aria-label="CandidateFlow home"><span className="brand-icon">C</span><span>candidate<span className="brand-light">flow</span><small>RESEARCH OPERATIONS</small></span></a>
      <p className="nav-caption">WORKSPACE</p><nav aria-label="Primary navigation">{visibleLinks.map(([to, icon, title]) => <NavLink key={to} to={to} end={to === '/'} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}><span className="nav-icon" aria-hidden="true">{icon}</span>{title}</NavLink>)}</nav>
      <div className="sidebar-bottom"><span className="live-dot" /> API workspace <small>Local development</small></div>
    </aside>
    <main className="main-area"><header className="topbar"><div className="breadcrumb">Research workspace <span>/</span> <strong>Evidence-led discovery</strong></div><div className="topbar-right"><span className="demo-pill">{user.username || 'LOCAL DEMO'}{user.role ? ` · ${user.role.toUpperCase()}` : ''}</span>{user.username && <button className="button button-secondary" onClick={() => { sessionStorage.removeItem('studentsflow-token'); sessionStorage.removeItem('studentsflow-user'); window.location.reload() }}>Sign out</button>}<span className="avatar" aria-label="Signed in user">{(user.username || 'L')[0].toUpperCase()}</span></div></header><section className="page-content"><Outlet /></section></main>
  </div>
}
