import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import {
  AlertCircle, ArrowDown, ArrowUp, Check, CircleHelp, GripVertical, LoaderCircle,
  Pencil, Plus, RefreshCw, Save, ShieldAlert, Sparkles, ToggleLeft, ToggleRight, X,
} from 'lucide-react'
import { ApiError, apiGet, apiRequest } from '../lib/api'
import { useAuth } from '../auth/useAuth'

type Ingredient = {
  id: string
  name: string
  emoji: string | null
  is_active: boolean
  created_at?: string | null
  updated_at?: string | null
}

type Readiness = {
  is_ready: boolean
  issues: string[]
  active_ingredient_count: number
  master_order_valid: boolean
}

type GameConfig = {
  master_order: string[]
  master_order_ingredients: Array<{ id: string; name: string | null; emoji: string | null; is_active: boolean | null }>
  readiness: Readiness
}

type IngredientDraft = { name: string; emoji: string }
type ModalState = { kind: 'create' } | { kind: 'edit'; ingredient: Ingredient } | { kind: 'toggle'; ingredient: Ingredient } | null

const DEFAULT_INGREDIENTS = [
  { name: "Dragon's Blood", emoji: '🩸' },
  { name: 'Enchanted Rose Petals', emoji: '🌹' },
  { name: 'Moonstone Dust', emoji: '🌙' },
  { name: 'Phoenix Feather', emoji: '🪶' },
  { name: 'Mooncap Mushroom', emoji: '🍄' },
  { name: 'Mandrake Root', emoji: '🌱' },
  { name: "Serpent's Scale", emoji: '🐍' },
  { name: 'Stardust', emoji: '✨' },
  { name: "Philosopher's Stone Shard", emoji: '🔮' },
  { name: 'Tear of the Griffin', emoji: '💧' },
]

function friendlyError(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Your session has expired. Sign in again to manage configuration.'
    if (error.status === 403) return 'Your account does not have permission to manage configuration.'
    if (error.status === 409) {
      if (/ingredient name already exists/i.test(error.message)) return 'An ingredient with that name already exists. Choose a different name.'
      if (/used by (the )?master order/i.test(error.message)) return 'This ingredient is still referenced by the master order. The backend did not change it.'
      return `Configuration could not be saved: ${error.message} No changes were saved.`
    }
    if (error.kind === 'network') return 'Could not reach the API. Check the backend connection and try again.'
    if (error.status >= 500) return 'The backend could not complete this request. Please retry.'
    return error.message
  }
  return error instanceof Error ? error.message : 'The request could not be completed.'
}

function emptyDraft(): IngredientDraft { return { name: '', emoji: '' } }

function seedDraftOrder(active: Ingredient[], currentOrder: string[]) {
  const activeById = new Map(active.map((ingredient) => [ingredient.id, ingredient]))
  const seen = new Set<string>()
  const ordered: Ingredient[] = []
  for (const id of currentOrder) {
    const ingredient = activeById.get(id)
    if (ingredient && !seen.has(id)) {
      ordered.push(ingredient)
      seen.add(id)
    }
  }
  return [...ordered, ...active.filter((ingredient) => !seen.has(ingredient.id))]
}

export default function IngredientsPage() {
  const { session } = useAuth()
  const token = session?.access_token
  const [ingredients, setIngredients] = useState<Ingredient[]>([])
  const [config, setConfig] = useState<GameConfig | null>(null)
  const [draftOrder, setDraftOrder] = useState<Ingredient[]>([])
  const [savedOrder, setSavedOrder] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [showInactive, setShowInactive] = useState(true)
  const [modal, setModal] = useState<ModalState>(null)
  const [form, setForm] = useState<IngredientDraft>(emptyDraft())
  const [formError, setFormError] = useState<string | null>(null)
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [formPending, setFormPending] = useState(false)
  const [actionPending, setActionPending] = useState(false)
  const [seedPending, setSeedPending] = useState(false)
  const [savingOrder, setSavingOrder] = useState(false)
  const [orderError, setOrderError] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null)
  const [draggedId, setDraggedId] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    if (!token) return () => { current = false }
    Promise.all([
      apiGet<Ingredient[]>('/admin/ingredients?include_inactive=true', token),
      apiGet<GameConfig>('/admin/game-config', token),
    ]).then(([ingredientRows, gameConfig]) => {
      if (!current) return
      const activeRows = ingredientRows.filter((ingredient) => ingredient.is_active)
      setIngredients(ingredientRows)
      setConfig(gameConfig)
      setSavedOrder(gameConfig.master_order)
      setDraftOrder(seedDraftOrder(activeRows, gameConfig.master_order))
    }).catch((error: unknown) => {
      if (current) setLoadError(friendlyError(error))
    }).finally(() => {
      if (current) setLoading(false)
    })
    return () => { current = false }
  }, [token, refreshKey])

  const activeIngredients = useMemo(() => ingredients.filter((ingredient) => ingredient.is_active), [ingredients])
  const dragEnabled = typeof navigator === 'undefined' || navigator.maxTouchPoints === 0
  const visibleIngredients = showInactive ? ingredients : activeIngredients
  const currentDraftIds = draftOrder.map((ingredient) => ingredient.id)
  const orderIsDirty = currentDraftIds.length !== savedOrder.length || currentDraftIds.some((id, index) => id !== savedOrder[index])
  const missingDefaults = useMemo(() => {
    const existingNames = new Set(ingredients.map((ingredient) => ingredient.name.trim().toLocaleLowerCase()))
    return DEFAULT_INGREDIENTS.filter((ingredient) => !existingNames.has(ingredient.name.toLocaleLowerCase()))
  }, [ingredients])

  function refresh() {
    setLoading(true)
    setLoadError(null)
    setRefreshKey((key) => key + 1)
  }
  function showNotice(text: string, error = false) { setNotice({ text, error }) }

  function beginCreate() {
    setForm(emptyDraft())
    setFieldError(null)
    setFormError(null)
    setModal({ kind: 'create' })
  }

  function beginEdit(ingredient: Ingredient) {
    setForm({ name: ingredient.name, emoji: ingredient.emoji || '' })
    setFieldError(null)
    setFormError(null)
    setModal({ kind: 'edit', ingredient })
  }

  async function submitIngredient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!modal || (modal.kind !== 'create' && modal.kind !== 'edit') || formPending) return
    const name = form.name.trim()
    if (!name) {
      setFieldError('Enter an ingredient name.')
      return
    }
    if (name.length > 200) {
      setFieldError('Ingredient names can contain up to 200 characters.')
      return
    }
    if (form.emoji.length > 64) {
      setFieldError('Emoji text can contain up to 64 characters.')
      return
    }
    if (!token) {
      setFormError('Your session is unavailable. Sign in again before saving.')
      return
    }
    setFieldError(null)
    setFormError(null)
    setFormPending(true)
    try {
      const payload = { name, emoji: form.emoji.trim() || null }
      if (modal.kind === 'create') {
        await apiRequest<Ingredient>('POST', '/admin/ingredients', token, payload)
        showNotice(`${name} was added.`)
      } else {
        await apiRequest<Ingredient>('PATCH', `/admin/ingredients/${encodeURIComponent(modal.ingredient.id)}`, token, payload)
        showNotice(`${name} was updated.`)
      }
      setModal(null)
      refresh()
    } catch (error) {
      const message = friendlyError(error)
      if (error instanceof ApiError && error.status === 409 && /already exists/i.test(error.message)) setFieldError(message)
      else setFormError(message)
    } finally {
      setFormPending(false)
    }
  }

  async function confirmToggle() {
    if (!modal || modal.kind !== 'toggle' || actionPending || !token) return
    const ingredient = modal.ingredient
    setActionPending(true)
    try {
      if (ingredient.is_active) {
        await apiRequest<Ingredient>('DELETE', `/admin/ingredients/${encodeURIComponent(ingredient.id)}`, token)
        showNotice(`${ingredient.name} was deactivated.`)
      } else {
        await apiRequest<Ingredient>('PATCH', `/admin/ingredients/${encodeURIComponent(ingredient.id)}`, token, { is_active: true })
        showNotice(`${ingredient.name} was activated.`)
      }
      setModal(null)
      refresh()
    } catch (error) {
      showNotice(friendlyError(error), true)
      setModal(null)
    } finally {
      setActionPending(false)
    }
  }

  async function addMissingDefaults() {
    if (!token || seedPending || missingDefaults.length === 0) return
    setSeedPending(true)
    let added = 0
    try {
      for (const ingredient of missingDefaults) {
        await apiRequest<Ingredient>('POST', '/admin/ingredients', token, ingredient)
        added += 1
      }
      showNotice(`Added ${added} missing default ingredient${added === 1 ? '' : 's'}.`)
    } catch (error) {
      const message = friendlyError(error)
      showNotice(added ? `Added ${added} default ingredient${added === 1 ? '' : 's'} before stopping. ${message}` : message, true)
    } finally {
      setSeedPending(false)
      refresh()
    }
  }

  function moveIngredient(from: number, to: number) {
    if (to < 0 || to >= draftOrder.length || from === to) return
    setDraftOrder((current) => {
      const next = [...current]
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      return next
    })
    setOrderError(null)
  }

  function dropIngredient(targetId: string) {
    if (!draggedId || draggedId === targetId) return
    const from = draftOrder.findIndex((ingredient) => ingredient.id === draggedId)
    const to = draftOrder.findIndex((ingredient) => ingredient.id === targetId)
    moveIngredient(from, to)
    setDraggedId(null)
  }

  function discardOrder() {
    setDraftOrder(seedDraftOrder(activeIngredients, savedOrder))
    setOrderError(null)
  }

  async function saveOrder() {
    if (savingOrder || !token) return
    const ids = draftOrder.map((ingredient) => ingredient.id)
    const uniqueIds = new Set(ids)
    if (activeIngredients.length !== 10 || ids.length !== 10 || uniqueIds.size !== 10 || ids.some((id) => !activeIngredients.some((ingredient) => ingredient.id === id))) {
      setOrderError('The master order must include every active ingredient exactly once, and there must be exactly 10 active ingredients.')
      return
    }
    setSavingOrder(true)
    setOrderError(null)
    try {
      await apiRequest<GameConfig>('PUT', '/admin/game-config/master-order', token, { ingredient_ids: ids })
      showNotice('Master order saved. Reloading the canonical configuration…')
      refresh()
    } catch (error) {
      setOrderError(friendlyError(error))
    } finally {
      setSavingOrder(false)
    }
  }

  const readiness = config?.readiness
  const effectiveLoadError = token ? loadError : 'Your session is unavailable. Sign in again to load configuration.'
  const readinessItems = readiness ? [
    { label: 'Exactly 10 active ingredients', valid: readiness.active_ingredient_count === 10, value: `${readiness.active_ingredient_count} active` },
    { label: 'Master order contains each active ingredient once', valid: readiness.master_order_valid, value: readiness.master_order_valid ? 'Valid' : 'Needs attention' },
  ] : []

  return <div className="space-y-6">
    <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
      <div><p className="section-eyebrow">Admin workspace</p><h1 className="mt-2 font-display text-3xl text-parchment sm:text-4xl">Ingredients & Master Order</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-muted">Maintain ingredient records and the canonical answer sequence. UUID references stay stable when names or emoji change.</p></div>
      <div className="flex flex-col gap-2 sm:flex-row"><button type="button" className="secondary-button" onClick={refresh} disabled={loading}><RefreshCw size={14} /> Refresh</button><button type="button" className="gold-button" onClick={beginCreate}><Plus size={16} /> Add ingredient</button></div>
    </header>

    {notice && <div role={notice.error ? 'alert' : 'status'} aria-live={notice.error ? 'assertive' : 'polite'} className={`flex items-start justify-between gap-3 rounded-xl border px-4 py-3 text-sm ${notice.error ? 'border-rose-300/20 bg-rose-400/10 text-rose-100' : 'border-gold/20 bg-gold/[.06] text-parchment'}`}><span className="flex items-start gap-2">{notice.error ? <AlertCircle size={16} className="mt-0.5 shrink-0 text-rose-200" /> : <Check size={16} className="mt-0.5 shrink-0 text-gold" />}{notice.text}</span><button type="button" onClick={() => setNotice(null)} className="text-muted hover:text-parchment" aria-label="Dismiss message"><X size={16} /></button></div>}

    {loading && token ? <div className="space-y-4" aria-label="Loading game configuration" aria-busy="true"><div className="h-44 animate-pulse rounded-2xl border border-white/[.06] bg-panel" /><div className="h-96 animate-pulse rounded-2xl border border-white/[.06] bg-panel" /></div>
      : effectiveLoadError ? <div role="alert" className="panel border-rose-300/20"><div className="flex items-start gap-3"><AlertCircle className="mt-0.5 shrink-0 text-rose-200" size={18} /><div><h2 className="text-sm font-medium text-parchment">Configuration could not be loaded</h2><p className="mt-1 text-sm leading-6 text-muted">{effectiveLoadError}</p>{token && <button type="button" className="secondary-button mt-4" onClick={refresh}><RefreshCw size={14} /> Retry</button>}</div></div></div>
      : <>
        {readiness && <section className="panel"><div className="mb-4 flex flex-col justify-between gap-3 sm:flex-row sm:items-start"><div><p className="section-eyebrow">Backend configuration readiness</p><h2 className="mt-1 font-display text-xl text-parchment">{readiness.is_ready ? 'Configuration ready' : 'Needs attention'}</h2></div><span className={`inline-flex w-fit items-center gap-2 rounded-full border px-3 py-1.5 text-xs ${readiness.is_ready ? 'border-emerald-300/20 bg-emerald-300/[.06] text-emerald-200' : 'border-amber-200/20 bg-amber-200/[.06] text-amber-100'}`}><span className={`size-1.5 rounded-full ${readiness.is_ready ? 'bg-emerald-300' : 'bg-amber-200'}`} />Configuration checks</span></div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">{readinessItems.map((item) => <div key={item.label} className={`rounded-xl border p-3 ${item.valid ? 'border-emerald-300/10 bg-emerald-300/[.025]' : 'border-amber-200/15 bg-amber-200/[.035]'}`}><div className="flex items-start gap-2"><span className={`mt-0.5 grid size-4 shrink-0 place-items-center rounded-full ${item.valid ? 'bg-emerald-300/15 text-emerald-200' : 'bg-amber-200/15 text-amber-100'}`}>{item.valid ? <Check size={11} /> : <CircleHelp size={11} />}</span><span className="text-xs leading-5 text-parchment/90">{item.label}</span></div><div className={`mt-2 pl-6 text-[10px] ${item.valid ? 'text-emerald-200/70' : 'text-amber-100/70'}`}>{item.value}</div></div>)}</div>
          {readiness.issues.length > 0 && <ul className="mt-4 space-y-1.5 border-t border-white/[.06] pt-3">{readiness.issues.map((issue) => <li key={issue} className="flex gap-2 text-xs leading-5 text-muted"><span className="mt-1.5 size-1 shrink-0 rounded-full bg-amber-200/70" />{issue}</li>)}</ul>}
        </section>}

        <section className="panel">
          <div className="mb-4 flex flex-col justify-between gap-3 sm:flex-row sm:items-end"><div><p className="section-eyebrow">Ingredient catalog</p><h2 className="mt-1 font-display text-xl text-parchment">Ingredients</h2><p className="mt-1 text-xs leading-5 text-muted">Names and emoji are labels; the backend UUID remains the reference used by game orders.</p></div><div className="flex flex-wrap items-center gap-2"><button type="button" className="secondary-button" onClick={() => setShowInactive((value) => !value)}>{showInactive ? 'Hide inactive' : 'Show inactive'}</button>{missingDefaults.length > 0 && <button type="button" className="secondary-button" onClick={() => void addMissingDefaults()} disabled={seedPending}><Sparkles size={14} />{seedPending ? 'Adding defaults…' : `Add ${missingDefaults.length} missing defaults`}</button>}</div></div>
          {ingredients.length === 0 ? <div className="flex min-h-36 flex-col items-center justify-center rounded-xl border border-dashed border-white/10 text-center"><p className="text-sm text-parchment">No ingredients have been configured.</p><p className="mt-1 text-xs text-muted">Add the default set or create ingredients individually.</p><button type="button" className="gold-button mt-4" onClick={() => void addMissingDefaults()} disabled={seedPending}>{seedPending ? <LoaderCircle className="animate-spin" size={15} /> : <Sparkles size={15} />} Add the 10 default ingredients</button></div>
            : visibleIngredients.length === 0 ? <p className="rounded-xl border border-white/[.06] p-4 text-sm text-muted">No inactive ingredients to show.</p>
              : <div className="overflow-hidden rounded-xl border border-white/[.07]"><div className="hidden grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_130px_100px] gap-3 border-b border-white/[.07] bg-white/[.02] px-4 py-3 text-[10px] uppercase tracking-[.14em] text-muted sm:grid"><span>Ingredient</span><span>Name</span><span>Status</span><span className="text-right">Actions</span></div><div className="divide-y divide-white/[.05]">{visibleIngredients.map((ingredient) => <IngredientRow key={ingredient.id} ingredient={ingredient} onEdit={() => beginEdit(ingredient)} onToggle={() => setModal({ kind: 'toggle', ingredient })} />)}</div></div>}
          <p className="mt-3 text-[10px] text-muted/70">{activeIngredients.length} active · {ingredients.length - activeIngredients.length} inactive</p>
        </section>

        <section className="panel">
          <div className="mb-4 flex flex-col justify-between gap-3 sm:flex-row sm:items-start"><div><p className="section-eyebrow">Canonical answer</p><h2 className="mt-1 font-display text-xl text-parchment">Master order</h2><p className="mt-1 max-w-2xl text-xs leading-5 text-muted">Arrange every active ingredient once. Drag on supported devices, or use the move buttons for touch and keyboard access.</p></div><div className="flex flex-wrap gap-2">{orderIsDirty && <span className="inline-flex items-center rounded-lg border border-amber-200/15 bg-amber-200/[.05] px-3 py-2 text-xs text-amber-100">Unsaved changes</span>}<button type="button" className="secondary-button" onClick={discardOrder} disabled={!orderIsDirty || savingOrder}>Discard</button><button type="button" className="gold-button min-h-9 py-2" onClick={() => void saveOrder()} disabled={!orderIsDirty || savingOrder}><Save size={14} />{savingOrder ? 'Saving…' : 'Save order'}</button></div></div>
          {orderError && <div role="alert" className="mb-4 flex items-start gap-2 rounded-xl border border-rose-300/20 bg-rose-400/[.06] px-4 py-3 text-sm leading-6 text-rose-100"><AlertCircle size={16} className="mt-1 shrink-0" />{orderError}</div>}
          {activeIngredients.length === 0 ? <p className="rounded-xl border border-white/[.06] p-5 text-sm text-muted">Activate ingredients before creating a master order.</p>
            : <ol aria-label="Master order draft" className="space-y-2">{draftOrder.map((ingredient, index) => <li key={ingredient.id} draggable={dragEnabled} onDragStart={dragEnabled ? (event) => { setDraggedId(ingredient.id); event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', ingredient.id) } : undefined} onDragOver={dragEnabled ? (event) => event.preventDefault() : undefined} onDrop={dragEnabled ? (event) => { event.preventDefault(); dropIngredient(ingredient.id) } : undefined} onDragEnd={dragEnabled ? () => setDraggedId(null) : undefined} className={`flex items-center gap-3 rounded-xl border bg-[#111116]/70 px-3 py-2.5 transition-colors sm:px-4 ${draggedId === ingredient.id ? 'border-gold/40 opacity-50' : 'border-white/[.07]'}`}>
              <span className="hidden cursor-grab text-muted sm:inline-flex" aria-hidden="true"><GripVertical size={17} /></span><span className="grid size-7 shrink-0 place-items-center rounded-lg border border-gold/15 bg-gold/[.06] font-mono text-xs text-gold">{index + 1}</span><span className="grid size-8 shrink-0 place-items-center rounded-lg bg-violet/10 text-lg" aria-hidden="true">{ingredient.emoji || '✦'}</span><span className="min-w-0 flex-1 truncate text-sm text-parchment">{ingredient.name}</span><div className="flex shrink-0 gap-1"><button type="button" className="icon-button size-8" aria-label={`Move ${ingredient.name} up`} onClick={() => moveIngredient(index, index - 1)} disabled={savingOrder || index === 0}><ArrowUp size={15} /></button><button type="button" className="icon-button size-8" aria-label={`Move ${ingredient.name} down`} onClick={() => moveIngredient(index, index + 1)} disabled={savingOrder || index === draftOrder.length - 1}><ArrowDown size={15} /></button></div>
            </li>)}</ol>}
          {activeIngredients.length !== 10 && <p className="mt-3 text-xs leading-5 text-amber-100/80">The backend requires exactly 10 active ingredients before a master order can be saved; current count is {activeIngredients.length}.</p>}
        </section>
      </>}

    {modal && <Modal onClose={() => { if (!formPending && !actionPending) setModal(null) }}>
      {(modal.kind === 'create' || modal.kind === 'edit') && <div><ModalHeading title={modal.kind === 'create' ? 'Add ingredient' : `Edit ${modal.ingredient.name}`} subtitle={modal.kind === 'edit' ? `UUID ${modal.ingredient.id}` : 'New ingredient record'} onClose={() => setModal(null)} /><form className="mt-5 space-y-4" onSubmit={(event) => void submitIngredient(event)} noValidate><label className="block text-xs text-parchment/90">Ingredient name <span className="text-gold">*</span><input autoFocus maxLength={200} className="form-input mt-2" value={form.name} onChange={(event) => { setForm((current) => ({ ...current, name: event.target.value })); setFieldError(null) }} placeholder="e.g. Moonstone Dust" /></label><label className="block text-xs text-parchment/90">Emoji <span className="text-muted">(optional)</span><input maxLength={64} className="form-input mt-2" value={form.emoji} onChange={(event) => setForm((current) => ({ ...current, emoji: event.target.value }))} placeholder="🌙" /></label>{fieldError && <p role="alert" className="text-xs text-rose-200">{fieldError}</p>}{formError && <p role="alert" className="rounded-xl border border-rose-300/20 bg-rose-400/[.06] p-3 text-sm text-rose-100">{formError}</p>}<div className="flex flex-col-reverse gap-2 border-t border-white/[.06] pt-4 sm:flex-row sm:justify-end"><button type="button" className="secondary-button" disabled={formPending} onClick={() => setModal(null)}>Cancel</button><button type="submit" className="gold-button" disabled={formPending} aria-busy={formPending}>{formPending ? <LoaderCircle size={15} className="animate-spin" /> : <Check size={15} />}{formPending ? 'Saving…' : modal.kind === 'create' ? 'Add ingredient' : 'Save changes'}</button></div></form></div>}
      {modal.kind === 'toggle' && <div className="space-y-4"><div className="grid size-11 place-items-center rounded-xl border border-amber-300/20 bg-amber-300/[.08] text-amber-200"><ShieldAlert size={19} /></div><div><p className="section-eyebrow">{modal.ingredient.is_active ? 'Deactivate ingredient' : 'Activate ingredient'}</p><h2 className="mt-1 font-display text-xl text-parchment">{modal.ingredient.is_active ? `Deactivate ${modal.ingredient.name}?` : `Activate ${modal.ingredient.name}?`}</h2><p className="mt-2 text-sm leading-6 text-muted">{modal.ingredient.is_active ? 'The ingredient will remain in the database. If it is referenced by the master order, the backend will reject deactivation.' : 'This ingredient will become available for the checker. The backend requires exactly ten active ingredients and a complete master order before checks can succeed.'}</p></div><div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end"><button type="button" className="secondary-button" disabled={actionPending} onClick={() => setModal(null)}>Cancel</button><button type="button" className="gold-button" disabled={actionPending} onClick={() => void confirmToggle()}>{actionPending ? <LoaderCircle size={15} className="animate-spin" /> : <ToggleRight size={16} />}{actionPending ? 'Saving…' : modal.ingredient.is_active ? 'Deactivate' : 'Activate'}</button></div></div>}
    </Modal>}
  </div>
}

function IngredientRow({ ingredient, onEdit, onToggle }: { ingredient: Ingredient; onEdit: () => void; onToggle: () => void }) {
  return <article className="grid gap-3 px-4 py-3.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_130px_100px] sm:items-center"><div className="flex min-w-0 items-center gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-xl border border-violet/15 bg-violet/[.07] text-xl" aria-hidden="true">{ingredient.emoji || '✦'}</span><div className="min-w-0"><div className="truncate text-sm font-medium text-parchment">{ingredient.name}</div><div className="mt-1 font-mono text-[10px] text-muted/70 sm:hidden">{ingredient.id}</div></div></div><div className="hidden min-w-0 truncate font-mono text-[10px] text-muted/60 sm:block">{ingredient.id}</div><div><span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] ${ingredient.is_active ? 'border-emerald-300/15 bg-emerald-300/[.06] text-emerald-200' : 'border-white/10 bg-white/[.04] text-muted'}`}><span className={`size-1.5 rounded-full ${ingredient.is_active ? 'bg-emerald-300' : 'bg-muted/60'}`} />{ingredient.is_active ? 'Active' : 'Inactive'}</span></div><div className="flex justify-end gap-1.5"><button type="button" className="icon-button" aria-label={`Edit ${ingredient.name}`} title="Edit ingredient" onClick={onEdit}><Pencil size={15} /></button><button type="button" className="icon-button" aria-label={`${ingredient.is_active ? 'Deactivate' : 'Activate'} ${ingredient.name}`} title={ingredient.is_active ? 'Deactivate' : 'Activate'} onClick={onToggle}>{ingredient.is_active ? <ToggleLeft size={17} /> : <ToggleRight size={17} />}</button></div></article>
}

function Modal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])
  return <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-0 backdrop-blur-sm sm:items-center sm:p-5" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><section role="dialog" aria-modal="true" tabIndex={-1} className="max-h-[94vh] w-full overflow-y-auto rounded-t-2xl border border-white/10 bg-[#17171d] p-5 shadow-2xl shadow-black/60 sm:max-w-lg sm:rounded-2xl sm:p-6">{children}</section></div>
}

function ModalHeading({ title, subtitle, onClose }: { title: string; subtitle?: string; onClose: () => void }) {
  return <div className="flex items-start justify-between gap-4 border-b border-white/[.07] pb-4"><div className="min-w-0"><p className="section-eyebrow">Ingredient configuration</p><h2 className="mt-1 truncate font-display text-xl text-parchment">{title}</h2>{subtitle && <p className="mt-1 break-all font-mono text-[10px] text-muted">{subtitle}</p>}</div><button type="button" onClick={onClose} className="icon-button" aria-label="Close dialog"><X size={17} /></button></div>
}
