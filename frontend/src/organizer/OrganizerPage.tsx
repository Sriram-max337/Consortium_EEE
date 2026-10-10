import { useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, Check, LoaderCircle, LogOut, RotateCcw, Sparkles, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { ApiError, apiGet, apiRequest } from '../lib/api'

type Ingredient = { id: string; name: string; emoji: string | null }
type CheckResponse = { result: 'CORRECT' | 'INCORRECT' }

function errorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Your session expired. Sign in again to continue.'
    if (error.status === 403) return 'This account does not have an active organizer profile.'
    if (error.status === 422) return error.message || 'Use all ten active ingredients exactly once.'
    if (error.kind === 'network') return 'Could not reach the API. Check the connection and try again.'
    if (error.status >= 500) return 'The checker is temporarily unavailable. Please retry.'
    return error.message
  }
  return error instanceof Error ? error.message : 'The order could not be checked.'
}

export default function OrganizerPage() {
  const { session, principal, logout } = useAuth()
  const navigate = useNavigate()
  const token = session?.access_token
  const [ingredients, setIngredients] = useState<Ingredient[]>([])
  const [order, setOrder] = useState<Ingredient[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [result, setResult] = useState<CheckResponse['result'] | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    if (!token) return
    let active = true
    apiGet<Ingredient[]>('/organizer/ingredients', token).then((rows) => {
      if (!active) return
      setIngredients(rows)
      setOrder(rows)
    }).catch((error: unknown) => {
      if (active) setLoadError(errorMessage(error))
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [token, refreshKey])

  function move(from: number, to: number) {
    if (to < 0 || to >= order.length || from === to) return
    setOrder((current) => {
      const updated = current.slice()
      const [item] = updated.splice(from, 1)
      updated.splice(to, 0, item)
      return updated
    })
    setResult(null)
    setSubmitError(null)
  }

  async function checkOrder() {
    if (!token || submitting) return
    setSubmitting(true)
    setSubmitError(null)
    setResult(null)
    try {
      const response = await apiRequest<CheckResponse>('POST', '/organizer/check-order', token, {
        ingredient_ids: order.map((ingredient) => ingredient.id),
      })
      setResult(response.result)
    } catch (error) {
      setSubmitError(errorMessage(error))
    } finally {
      setSubmitting(false)
    }
  }

  function resetOrder() {
    setOrder(ingredients)
    setResult(null)
    setSubmitError(null)
  }

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  const hasTenDistinct = order.length === 10 && new Set(order.map((ingredient) => ingredient.id)).size === 10

  return <main className="min-h-screen bg-ink px-4 py-5 text-parchment sm:px-6 sm:py-8">
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="flex items-center justify-between gap-3 rounded-2xl border border-white/[.07] bg-panel px-4 py-4 sm:px-6"><div className="flex items-center gap-3"><div className="grid size-10 place-items-center rounded-xl border border-gold/25 bg-gold/[.08] text-gold"><Sparkles size={18} /></div><div><p className="text-[10px] uppercase tracking-[.18em] text-gold">The Elixir of Eternal Life</p><h1 className="mt-1 font-display text-lg sm:text-xl">Ingredient Order Checker</h1><p className="mt-1 text-xs text-muted">{principal?.name || 'Organizer'}</p></div></div><button type="button" className="icon-button" aria-label="Sign out" title="Sign out" onClick={() => void handleLogout()}><LogOut size={17} /></button></header>

      <section className="rounded-2xl border border-gold/15 bg-[linear-gradient(130deg,rgba(211,177,107,.065),rgba(180,154,209,.035))] p-4 sm:p-6"><p className="section-eyebrow">How to check</p><p className="mt-2 text-sm leading-6 text-muted">Arrange all ten ingredients in the order provided by the team, then check the order. The result is based on the complete sequence.</p></section>

      {(!token || loadError) && <div role="alert" className="rounded-xl border border-rose-300/20 bg-rose-400/[.07] p-4 text-sm text-rose-100"><div>{loadError || 'Your session is unavailable. Sign in again to load ingredients.'}</div>{token && <button type="button" className="secondary-button mt-3" onClick={() => { setLoading(true); setLoadError(null); setRefreshKey((value) => value + 1) }}>Retry loading ingredients</button>}</div>}
      {loading && token && <div role="status" className="panel space-y-3"><span className="sr-only">Loading active ingredients</span>{[0, 1, 2, 3].map((item) => <div key={item} className="h-12 animate-pulse rounded-xl bg-white/[.04]" />)}</div>}

      {!loading && !loadError && token && <>
        {ingredients.length !== 10 && <div role="alert" className="rounded-xl border border-amber-200/20 bg-amber-200/[.06] p-4 text-sm leading-6 text-amber-100">The checker currently has {ingredients.length} active ingredients. An administrator must configure exactly ten active ingredients and a complete master order.</div>}
        <section className="panel" aria-labelledby="order-heading"><div className="mb-4 flex flex-wrap items-end justify-between gap-3"><div><p className="section-eyebrow">Team's sequence</p><h2 id="order-heading" className="mt-1 font-display text-xl">Arrange ingredients</h2></div><span className="rounded-full border border-white/[.08] px-3 py-1 text-xs text-muted">{order.length} / 10</span></div>
          <ol aria-label="Submitted ingredient order" className="space-y-2" onDragOver={(event) => event.preventDefault()}>
            {order.map((ingredient, index) => <li key={ingredient.id} draggable onDragStart={() => setDragIndex(index)} onDragEnd={() => setDragIndex(null)} onDrop={() => { if (dragIndex !== null) move(dragIndex, index); setDragIndex(null) }} className={`flex min-h-14 items-center gap-3 rounded-xl border px-3 py-2.5 transition-colors sm:px-4 ${dragIndex === index ? 'border-gold/40 bg-gold/[.07]' : 'border-white/[.07] bg-black/10'}`}>
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-white/[.045] text-xs text-muted">{index + 1}</span><span className="text-lg" aria-hidden="true">{ingredient.emoji || '✦'}</span><span className="min-w-0 flex-1 text-sm font-medium">{ingredient.name}</span><div className="flex items-center gap-1"><button type="button" className="icon-button !size-8" aria-label={`Move ${ingredient.name} up`} disabled={index === 0 || submitting} onClick={() => move(index, index - 1)}><ArrowUp size={15} /></button><button type="button" className="icon-button !size-8" aria-label={`Move ${ingredient.name} down`} disabled={index === order.length - 1 || submitting} onClick={() => move(index, index + 1)}><ArrowDown size={15} /></button></div>
            </li>)}
          </ol>
          <p className="mt-3 text-xs leading-5 text-muted">Use the arrow controls on phones and keyboards, or drag an ingredient to reorder it.</p>
          {submitError && <div role="alert" className="mt-4 rounded-xl border border-rose-300/20 bg-rose-400/[.07] px-4 py-3 text-sm leading-6 text-rose-100">{submitError}</div>}
          {result && <div role="status" aria-live="polite" className={`mt-5 flex items-center gap-3 rounded-xl border px-4 py-4 ${result === 'CORRECT' ? 'border-emerald-300/25 bg-emerald-300/[.07] text-emerald-100' : 'border-rose-300/20 bg-rose-400/[.07] text-rose-100'}`}><span className="grid size-9 place-items-center rounded-lg bg-black/15">{result === 'CORRECT' ? <Check size={19} /> : <X size={19} />}</span><span className="font-display text-2xl tracking-wide">{result}</span></div>}
          <div className="mt-5 grid gap-3 sm:grid-cols-[1fr_auto]"><button type="button" className="gold-button w-full" disabled={submitting || !hasTenDistinct || ingredients.length !== 10} onClick={() => void checkOrder()}>{submitting ? <><LoaderCircle size={17} className="animate-spin" />Checking order…</> : 'Check order'}</button><button type="button" className="secondary-button min-h-12" disabled={submitting} onClick={resetOrder}><RotateCcw size={15} /> Reset / New check</button></div>
        </section>
      </>}
    </div>
  </main>
}
