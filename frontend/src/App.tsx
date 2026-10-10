import { useState, type FormEvent } from 'react'
import { BrowserRouter, Link, Navigate, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  CircleHelp, Crown, FlaskConical, KeyRound, LoaderCircle, LogOut,
  Menu, Moon, Shield, Sparkles, X,
} from 'lucide-react'
import { AuthProvider } from './auth/AuthContext'
import { useAuth } from './auth/useAuth'
import type { Principal } from './auth/context'
import { AuthFlowError } from './auth/errors'
import { supabaseConfigured } from './lib/supabase'
import IngredientsPage from './admin/IngredientsPage'
import OrganizerPage from './organizer/OrganizerPage'

const navigation = [
  { label: 'Ingredients & Master Order', path: '/admin/ingredients', icon: FlaskConical, description: 'Configure the checker' },
]

function LoadingScreen({ label = 'Preparing your sessionâ€¦' }: { label?: string }) {
  return <main className="grid min-h-screen place-items-center bg-ink text-parchment"><div className="flex items-center gap-3 text-sm text-muted"><LoaderCircle className="animate-spin text-gold" size={18} />{label}</div></main>
}

function AuthRoutes() {
  const { status } = useAuth()
  if (status === 'initializing') return <LoadingScreen label="Restoring your sessionâ€¦" />
  return <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route element={<ProtectedRoute allowed="admin" />}>
      <Route path="/admin" element={<AdminLayout />}>
        <Route index element={<Navigate to="/admin/ingredients" replace />} />
        <Route path="ingredients" element={<IngredientsPage />} />
        <Route path="*" element={<Navigate to="/admin/ingredients" replace />} />
      </Route>
    </Route>
    <Route element={<ProtectedRoute allowed="organizer" />}>
      <Route path="/organizer" element={<OrganizerPage />} />
    </Route>
    <Route path="/" element={<HomeRedirect />} />
    <Route path="*" element={<HomeRedirect />} />
  </Routes>
}

function ProtectedRoute({ allowed }: { allowed: Principal['principal_type'] }) {
  const { status, principal, profileError } = useAuth()
  const location = useLocation()
  if (status === 'initializing' || status === 'loading') return <LoadingScreen label="Verifying your accountâ€¦" />
  if (status !== 'authenticated' || !principal) return <Navigate to="/login" replace state={{ from: location, message: profileError }} />
  if (principal.principal_type !== allowed) return <Navigate to={principal.principal_type === 'admin' ? '/admin' : '/organizer'} replace />
  return <Outlet />
}

function HomeRedirect() {
  const { status, principal } = useAuth()
  if (status === 'initializing' || status === 'loading') return <LoadingScreen />
  if (principal?.principal_type === 'admin') return <Navigate to="/admin" replace />
  if (principal?.principal_type === 'organizer') return <Navigate to="/organizer" replace />
  return <Navigate to="/login" replace />
}

function LoginPage() {
  const { status, principal, profileError, login } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const initialMessage = (location.state as { message?: string } | null)?.message

  if (status === 'authenticated' && principal && !pending && !success) {
    return <Navigate to={principal.principal_type === 'admin' ? '/admin' : '/organizer'} replace />
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSuccess(null)
    if (!supabaseConfigured) {
      setError('Supabase Auth is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in frontend/.env.local, then restart the dev server.')
      return
    }
    setPending(true)
    try {
      const profile = await login(email.trim(), password)
      const destination = profile.principal_type === 'admin' ? '/admin' : '/organizer'
      setSuccess(`Account verified. Opening the ${profile.principal_type === 'admin' ? 'admin' : 'organizer'} dashboardâ€¦`)
      window.setTimeout(() => navigate(destination, { replace: true }), 500)
    } catch (reason) {
      setError(reason instanceof AuthFlowError ? reason.message : 'Sign-in could not be completed. Please try again.')
    } finally {
      setPending(false)
      setPassword('')
    }
  }

  return <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-ink px-5 py-12 text-parchment">
    <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_20%_15%,rgba(134,82,173,.15),transparent_38%),radial-gradient(ellipse_at_83%_78%,rgba(206,167,91,.09),transparent_35%)]" />
    <div className="pointer-events-none absolute inset-0 opacity-[.07] [background-image:radial-gradient(#e8d9b4_0.7px,transparent_0.7px)] [background-size:19px_19px]" />
    <section className="relative grid w-full max-w-5xl overflow-hidden rounded-3xl border border-white/10 bg-panel/90 shadow-2xl shadow-black/40 md:grid-cols-[1.05fr_.95fr]">
      <div className="relative hidden min-h-[580px] flex-col justify-between overflow-hidden border-r border-white/10 bg-[#17151d] p-10 md:flex">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_50%,rgba(99,65,126,.28),transparent_58%)]" />
        <div className="relative flex items-center gap-3"><div className="grid size-11 place-items-center rounded-xl border border-gold/30 bg-gold/10 text-gold"><Moon size={21} /></div><div><div className="font-display text-sm tracking-[.22em] text-parchment">THE ETERNAL LIFE SOCIETY</div><div className="mt-1 text-[10px] uppercase tracking-[.24em] text-muted">A national treasure hunt</div></div></div>
        <div className="relative max-w-sm">
          <div className="mb-6 flex items-center gap-3 text-xs uppercase tracking-[.2em] text-gold"><Sparkles size={15} /> The Witch Hunt <span className="h-px flex-1 bg-gold/30" /></div>
          <h1 className="font-display text-5xl leading-[1.12] text-parchment">The Elixir of <span className="text-gold">Eternal Life</span></h1>
          <p className="mt-6 max-w-xs text-sm leading-7 text-muted">A curious mind may find the path. A steady hand may finish it. Enter the society to continue.</p>
        </div>
        <div className="relative flex items-center justify-between border-t border-white/10 pt-5 text-[11px] uppercase tracking-[.16em] text-muted"><span>Authorized event staff</span><span>Est. MMXXVI</span></div>
        <div aria-hidden="true" className="absolute -bottom-14 -right-8 size-64 rounded-full border border-gold/10" />
        <div aria-hidden="true" className="absolute -bottom-5 -right-1 size-48 rounded-full border border-gold/10" />
      </div>

      <div className="flex flex-col justify-center px-6 py-10 sm:px-10 md:px-12">
        <div className="mb-8 flex items-center gap-3 md:hidden"><div className="grid size-10 place-items-center rounded-xl border border-gold/30 bg-gold/10 text-gold"><Moon size={20} /></div><div className="font-display text-xs tracking-[.18em]">THE ETERNAL LIFE SOCIETY</div></div>
        <div className="mb-8"><p className="mb-3 text-xs uppercase tracking-[.22em] text-gold">Welcome back</p><h2 className="font-display text-3xl text-parchment">Sign in to your account</h2><p className="mt-3 text-sm text-muted">Use your event account to enter the console.</p></div>
        {(error || (!success && (initialMessage || profileError))) && <div role="alert" aria-live="assertive" className="mb-5 rounded-xl border border-rose-300/20 bg-rose-400/10 px-4 py-3 text-sm leading-6 text-rose-100">{error || initialMessage || profileError}</div>}
        {success && <div role="status" aria-live="polite" className="mb-5 rounded-xl border border-emerald-300/20 bg-emerald-300/10 px-4 py-3 text-sm leading-6 text-emerald-100">{success}</div>}
        {status === 'loading' && !pending && <div role="status" aria-live="polite" className="mb-5 rounded-xl border border-gold/20 bg-gold/[.06] px-4 py-3 text-sm text-gold">Verifying your saved sessionâ€¦</div>}
        {!supabaseConfigured && <div role="alert" className="mb-5 rounded-xl border border-gold/25 bg-gold/10 px-4 py-3 text-sm leading-6 text-gold">Set <code className="font-mono">VITE_SUPABASE_URL</code> and <code className="font-mono">VITE_SUPABASE_ANON_KEY</code> in <code className="font-mono">frontend/.env.local</code> to enable sign-in.</div>}
        <form onSubmit={submit} className="space-y-5">
          <label className="block text-sm text-parchment/90" htmlFor="email">Email address<input autoComplete="username" className="form-input mt-2" id="email" name="email" type="email" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" /></label>
          <label className="block text-sm text-parchment/90" htmlFor="password">Password<input autoComplete="current-password" className="form-input mt-2" id="password" name="password" type="password" required value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter your password" /></label>
          <button className="gold-button mt-2 w-full" type="submit" disabled={pending || Boolean(success)} aria-busy={pending}>{pending ? <><LoaderCircle className="animate-spin" size={17} /> Checking credentials and accessâ€¦</> : success ? 'Opening dashboardâ€¦' : <><KeyRound size={17} /> Sign in securely</>}</button>
        </form>
        <p className="mt-7 flex items-center gap-2 text-xs leading-5 text-muted"><Shield size={14} className="shrink-0 text-gold/80" /> Access is granted by your active Admin or Organizer profile.</p>
      </div>
    </section>
  </main>
}

function AdminLayout() {
  const { principal, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const [logoutError, setLogoutError] = useState<string | null>(null)
  const current = navigation.find((item) => item.path === location.pathname) || navigation[0]

  async function handleLogout() {
    setLogoutError(null)
    try { await logout(); navigate('/login', { replace: true }) }
    catch { setLogoutError('Could not complete sign out. Try again.') }
  }

  return <div className="min-h-screen bg-ink text-parchment">
    <div className="flex min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[260px] flex-col border-r border-white/[.07] bg-[#111116] lg:flex">
        <BrandMark />
        <div className="px-5 pb-2 pt-8 text-[10px] font-semibold uppercase tracking-[.2em] text-muted/70">Console</div>
        <nav aria-label="Admin navigation" className="space-y-1 px-3">
          {navigation.map((item) => <NavItem key={item.path} item={item} active={current.path === item.path} />)}
        </nav>
        <div className="mt-auto p-4"><div className="rounded-xl border border-white/[.07] bg-white/[.025] p-3"><div className="flex items-center gap-2 text-xs text-muted"><CircleHelp size={14} className="text-gold" /> Event console</div><p className="mt-2 text-[11px] leading-5 text-muted/70">Administrative access is verified by the backend for every request.</p></div></div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col lg:pl-[260px]">
        <header className="sticky top-0 z-20 border-b border-white/[.07] bg-ink/90 backdrop-blur-xl">
          <div className="flex h-[68px] items-center justify-between gap-4 px-4 sm:px-7 lg:px-9">
            <div className="flex min-w-0 items-center gap-3">
              <button type="button" aria-label={menuOpen ? 'Close navigation' : 'Open navigation'} aria-expanded={menuOpen} className="icon-button lg:hidden" onClick={() => setMenuOpen((open) => !open)}>{menuOpen ? <X size={19} /> : <Menu size={19} />}</button>
              <div className="min-w-0"><div className="truncate text-sm font-medium text-parchment">{current.label}</div><div className="hidden text-[11px] text-muted sm:block">The Elixir of Eternal Life Â· Admin console</div></div>
            </div>
            <div className="flex items-center gap-3">
              <div className="hidden text-right sm:block"><div className="text-xs font-medium text-parchment">{principal?.name || principal?.email || 'Administrator'}</div><div className="mt-0.5 text-[10px] uppercase tracking-[.12em] text-gold/80">Main admin</div></div>
              <div className="grid size-9 place-items-center rounded-full border border-gold/20 bg-gold/10 text-gold"><Crown size={16} /></div>
              <button type="button" onClick={handleLogout} className="icon-button" aria-label="Sign out" title="Sign out"><LogOut size={17} /></button>
            </div>
          </div>
          {menuOpen && <nav aria-label="Mobile admin navigation" className="space-y-1 border-t border-white/[.07] bg-[#111116] p-3 lg:hidden">{navigation.map((item) => <NavItem key={item.path} item={item} active={current.path === item.path} onClick={() => setMenuOpen(false)} />)}</nav>}
        </header>
        {logoutError && <div role="alert" className="mx-4 mt-4 rounded-xl border border-rose-300/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-100 sm:mx-7 lg:mx-9">{logoutError}</div>}
        <main className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-7 sm:px-7 sm:py-9 lg:px-9"><Outlet /></main>
        <footer className="border-t border-white/[.06] px-4 py-4 text-center text-[10px] uppercase tracking-[.15em] text-muted/55 sm:px-7 lg:px-9">The Elixir of Eternal Life <span className="mx-2 text-gold/40">Â·</span> Admin console</footer>
      </div>
    </div>
  </div>
}

function BrandMark() {
  return <div className="flex items-center gap-3 border-b border-white/[.07] px-5 py-[18px]"><div className="grid size-10 shrink-0 place-items-center rounded-xl border border-gold/25 bg-gold/[.08] text-gold"><Moon size={19} /></div><div className="min-w-0"><div className="truncate font-display text-[11px] tracking-[.18em] text-parchment">THE ETERNAL LIFE SOCIETY</div><div className="mt-1 text-[9px] uppercase tracking-[.2em] text-muted">Game administration</div></div></div>
}

function NavItem({ item, active, onClick }: { item: typeof navigation[number]; active: boolean; onClick?: () => void }) {
  const Icon = item.icon
  return <Link to={item.path} onClick={onClick} aria-current={active ? 'page' : undefined} className={`group flex items-center gap-3 rounded-xl px-3 py-3 transition-colors ${active ? 'border border-gold/15 bg-gold/[.08] text-gold' : 'text-muted hover:bg-white/[.04] hover:text-parchment'}`}>
    <Icon size={17} strokeWidth={1.8} className={active ? 'text-gold' : 'text-muted/80 group-hover:text-parchment'} />
    <span className="min-w-0 flex-1"><span className="block truncate text-[12px] font-medium">{item.label}</span><span className={`mt-0.5 block truncate text-[10px] ${active ? 'text-gold/60' : 'text-muted/55'}`}>{item.description}</span></span>
    {active && <span className="h-1.5 w-1.5 rounded-full bg-gold" />}
  </Link>
}

function App() {
  return <BrowserRouter><AuthProvider><AuthRoutes /></AuthProvider></BrowserRouter>
}

export default App
