import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'

type Session = {
  kind: 'staff' | 'demo'
  user: string
  role: string
  force_change?: boolean
  demo_id?: string
  expires_at: number
}

type Product = {
  id: string; sku: string; barcode: string; name: string; category_code: string
  category: string; storage: string; priority: number; stock: number; quarantined: number
  length: number; width: number; height: number; weight: number
}

type Lot = {
  id: number; product_id: string; product_name: string; lot_code: string
  qty: number; expiry: string | null; shelf_id: string | null; quarantined: number
  quarantine_reason: string | null; storage: string; category: string; disposition: string
}

type Shelf = {
  id: string; warehouse_id: string; warehouse_name: string; zone: string; storage: string
  status: string; capacity_l: number; used_l: number; max_weight: number; used_kg: number
  stock_units: number; shelf_barcode: string; restricted: number
}

type Bootstrap = {
  session: Session; warehouses: Array<{ id: string; name: string; location_count: number }>
  shelves: Shelf[]; products: Product[]; product_count: number; lots: Lot[]
  movements: Array<Record<string, any>>; audit: Array<Record<string, any>>
  transfers: Array<Record<string, any>>
  waste_categories: string[]
}

type Suggestion = {
  shelf_id: string; warehouse_name: string; zone: string; level: number
  remaining_l: number; remaining_kg: number; score: number; reasons: string[]
}

const storages = ['AMBIENT', 'CHILLED', 'FROZEN', 'DRY_SECURE', 'HAZMAT', 'CONTROLLED', 'WASTE']
const categories = [
  ['FOOD', 'Food & beverages'], ['ELEC', 'Electrical & electronics'], ['CHEM', 'Chemicals & process materials'],
  ['PHAR', 'Healthcare & pharmaceuticals'], ['BUILD', 'Building materials'], ['AUTO', 'Automotive & transport'],
  ['AGRI', 'Agriculture & outdoor'], ['TEXT', 'Textiles & apparel'], ['PACK', 'Packaging & shipping'],
  ['METAL', 'Metals'], ['OTHER', 'Other'],
]

async function api<T>(path: string, body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new Error('Cannot connect to Smart Slots. Confirm that the development server is running.')
  }
  const result = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(result.error || 'Request failed.')
  return result as T
}

export const Route = createFileRoute('/')({ component: WarehouseApp })

function newProductForm() {
  return {
    id: '', sku: '', barcode: '', name: '', category: 'FOOD', storage: 'AMBIENT', priority: '3',
    length: '20', width: '20', height: '20', weight: '1', quantity: '1', lot: '',
    expiry: '', disposition: 'USEFUL', waste_category: '', quarantined: false,
  }
}

function WarehouseApp() {
  const [session, setSession] = useState<Session | null>(null)
  const [data, setData] = useState<Bootstrap | null>(null)
  const [page, setPage] = useState('Overview')
  const [warehouse, setWarehouse] = useState('ALL')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [login, setLogin] = useState({ username: 'staff.demo', password: '123' })
  const [password, setPassword] = useState('')
  const [search, setSearch] = useState('')
  const [products, setProducts] = useState<Product[]>([])
  const [selected, setSelected] = useState<Product | null>(null)
  const [selectedLot, setSelectedLot] = useState<Lot | null>(null)
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [conditionLot, setConditionLot] = useState<Lot | null>(null)
  const [condition, setCondition] = useState('USEFUL')
  const [wasteCategory, setWasteCategory] = useState('')
  const [receiving, setReceiving] = useState(false)
  const [addingShelf, setAddingShelf] = useState(false)
  const [assistantQuestion, setAssistantQuestion] = useState('')
  const [assistantAnswer, setAssistantAnswer] = useState('')
  const [productForm, setProductForm] = useState(newProductForm)

  const refresh = async () => {
    const fresh = await api<Bootstrap>('/bootstrap')
    setData(fresh)
    setSession(fresh.session)
    setProducts(fresh.products)
  }

  useEffect(() => {
    api<{ session: Session | null }>('/session').then(({ session: current }) => {
      if (!current) return
      setSession(current)
      api<Bootstrap>('/bootstrap').then((fresh) => {
        setData(fresh)
        setProducts(fresh.products)
      }).catch((e: Error) => setError(e.message))
    }).catch((e: Error) => setError(e.message))
  }, [])

  useEffect(() => {
    if (!search.trim()) {
      setProducts(data?.products || [])
      return
    }
    const timer = window.setTimeout(() => {
      api<{ products: Product[] }>(`/search?q=${encodeURIComponent(search.trim())}`)
        .then((result) => setProducts(result.products))
        .catch((e: Error) => setError(e.message))
    }, 200)
    return () => window.clearTimeout(timer)
  }, [search, data])

  const visibleShelves = useMemo(() => (data?.shelves || []).filter(
    (shelf) => warehouse === 'ALL' || shelf.warehouse_id === warehouse,
  ), [data, warehouse])
  const visibleLots = useMemo(() => (data?.lots || []).filter(
    (lot) => warehouse === 'ALL' || data?.shelves.find((shelf) => shelf.id === lot.shelf_id)?.warehouse_id === warehouse,
  ), [data, warehouse])
  const metrics = {
    products: data?.product_count || 0,
    locations: visibleShelves.length,
    units: visibleLots.reduce((sum, lot) => sum + Number(lot.qty || 0), 0),
    quarantine: visibleLots.filter((lot) => lot.quarantined).reduce((sum, lot) => sum + Number(lot.qty || 0), 0),
  }

  async function act(action: () => Promise<void>) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await action()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  async function signIn(event: FormEvent) {
    event.preventDefault()
    await act(async () => {
      const result = await api<{ session: Session }>('/login', login)
      setSession(result.session)
      if (!result.session.force_change) await refresh()
    })
  }

  async function startDemo() {
    await act(async () => {
      const result = await api<{ session: Session }>('/entry/demo', {})
      setSession(result.session)
      await refresh()
    })
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault()
    await act(async () => {
      await api('/password', { password })
      setSession((current) => current ? { ...current, force_change: false } : current)
      setPassword('')
      await refresh()
    })
  }

  async function signOut() {
    await act(async () => {
      await api('/logout', {})
      setData(null)
      setSession(null)
      setPage('Overview')
    })
  }

  async function saveProduct(event: FormEvent) {
    event.preventDefault()
    await act(async () => {
      const newProduct = { ...productForm }
      const category = categories.find(([code]) => code === newProduct.category)
      const result = await api<{ lot_id: number }>('/products', {
        ...newProduct,
        category_name: category?.[1] || productForm.category,
        priority: Number(newProduct.priority), length: Number(newProduct.length),
        width: Number(newProduct.width), height: Number(newProduct.height),
        weight: Number(newProduct.weight), quantity: Number(newProduct.quantity),
      })
      setReceiving(false)
      await refresh()
      const exactProduct = await api<{ products: Product[] }>(`/search?q=${encodeURIComponent(newProduct.id)}`)
      setSearch(newProduct.id)
      setProducts(exactProduct.products)
      setProductForm(newProductForm())
      setNotice(`Product received successfully. Lot record #${result.lot_id} is ready for shelf suggestions.`)
      setPage('Products')
    })
  }

  async function suggest(product: Product, lot: Lot) {
    await act(async () => {
      const result = await api<{ suggestions: Suggestion[]; message: string | null }>('/suggest', {
        product_id: product.id, lot: lot.lot_code, lot_id: lot.id,
      })
      setSelected(product)
      setSelectedLot(lot)
      setSuggestions(result.suggestions)
      if (!result.suggestions.length) setError(result.message || 'No suitable locations found.')
    })
  }

  async function allocate(product: Product, lot: Lot, shelfId: string) {
    await act(async () => {
      await api('/allocate', { product_id: product.id, lot: lot.lot_code, lot_id: lot.id, shelf_id: shelfId })
      setSelected(null)
      setSelectedLot(null)
      setSuggestions([])
      await refresh()
      setNotice(`${product.name} allocated to ${shelfId}.`)
    })
  }

  async function submitMovement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const formElement = event.currentTarget
    const form = new FormData(formElement)
    const lotOption = formElement.elements.namedItem('lot_id') as HTMLSelectElement
    const option = lotOption.selectedOptions[0]
    await act(async () => {
      await api('/movement', {
        kind: form.get('kind'), product_id: option.dataset.productId, lot_id: Number(form.get('lot_id')),
        lot: option.dataset.lot, qty: Number(form.get('qty')), reason: form.get('reason'),
      })
      await refresh()
      setNotice('Inventory movement recorded.')
      formElement.reset()
    })
  }

  async function submitTransfer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const fields = new FormData(form)
    const option = (form.elements.namedItem('lot_id') as HTMLSelectElement).selectedOptions[0]
    await act(async () => {
      await api('/transfer/start', {
        product_id: option.dataset.productId, lot_id: Number(fields.get('lot_id')), lot: option.dataset.lot,
        qty: Number(fields.get('qty')), dest_shelf: fields.get('dest_shelf'),
        reason: fields.get('reason'), reference: fields.get('reference'),
      })
      await refresh()
      setNotice('Transfer started. The stock is now in transit pending destination receipt.')
      form.reset()
    })
  }

  async function submitShelf(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const fields = new FormData(form)
    await act(async () => {
      await api('/shelf', {
        id: fields.get('id'), warehouse_id: fields.get('warehouse_id'), zone: fields.get('zone'),
        category_codes: fields.get('category_codes') || 'ALL', storage: fields.get('storage'),
        length: Number(fields.get('length')), width: Number(fields.get('width')), height: Number(fields.get('height')),
        capacity_l: Number(fields.get('capacity_l')), max_weight: Number(fields.get('max_weight')),
        level: Number(fields.get('level')), access_easy: fields.get('access_easy') === 'on',
        restricted: fields.get('restricted') === 'on',
      })
      await refresh()
      setAddingShelf(false)
      setNotice('Warehouse location added.')
    })
  }

  async function receiveTransfer(transfer: Record<string, any>) {
    await act(async () => {
      await api('/transfer/receive', { transfer_id: transfer.id, shelf_id: transfer.dest_shelf })
      await refresh()
      setNotice(`Transfer ${transfer.id} received at ${transfer.dest_shelf}.`)
    })
  }

  async function cancelTransfer(transfer: Record<string, any>) {
    if (!window.confirm(`Cancel transfer ${transfer.id}? Its stock will return to unallocated inventory.`)) return
    await act(async () => {
      await api('/transfer/cancel', { transfer_id: transfer.id })
      await refresh()
      setNotice(`Transfer ${transfer.id} cancelled. Its stock is awaiting allocation.`)
    })
  }

  async function setShelfStatus(shelf: Shelf) {
    const status = shelf.status === 'Available' ? 'Unavailable' : 'Available'
    await act(async () => {
      await api('/shelf/status', { shelf_id: shelf.id, status })
      await refresh()
      setNotice(`${shelf.id} is now ${status.toLowerCase()}.`)
    })
  }

  async function askAssistant(event: FormEvent) {
    event.preventDefault()
    await act(async () => {
      const result = await api<{ answer: string }>('/assistant', { question: assistantQuestion })
      setAssistantAnswer(result.answer)
    })
  }

  async function saveCondition(event: FormEvent) {
    event.preventDefault()
    if (!conditionLot) return
    await act(async () => {
      await api('/lot/disposition', {
        lot_id: conditionLot.id, disposition: condition,
        waste_category: condition === 'WASTE' ? wasteCategory : null,
      })
      setConditionLot(null)
      await refresh()
      setNotice('Product condition updated and recorded.')
    })
  }

  if (!session) {
    return (
      <main className="blueprint flex min-h-screen items-center justify-center px-5 py-12">
        <div className="grid w-full max-w-4xl overflow-hidden rounded-2xl border border-sky-200/30 bg-white/95 shadow-2xl md:grid-cols-2">
          <section className="bg-gradient-to-br from-[#0b2741] to-[#1663a3] p-9 text-white md:p-12">
            <div className="mb-12 text-xs font-semibold uppercase tracking-[0.28em] text-sky-200">SMART SLOTS · local operations</div>
            <h1 className="text-4xl font-light tracking-wide">Warehouse<br /><strong className="font-bold">Operations</strong></h1>
            <p className="mt-5 max-w-sm text-sm leading-6 text-sky-100">Receive inventory, inspect storage fit, and put away stock with explainable shelf recommendations.</p>
            <div className="mt-10 flex gap-2 text-xs text-sky-100"><span className="rounded-full border border-white/20 px-3 py-1">SQLite</span><span className="rounded-full border border-white/20 px-3 py-1">Local demo</span><span className="rounded-full border border-white/20 px-3 py-1">Staff-confirmed</span></div>
          </section>
          <section className="p-8 md:p-10">
            <h2 className="text-2xl font-semibold text-slate-800">Welcome back</h2>
            <p className="mt-1 text-sm text-slate-500">Sign in as staff or start an isolated local demo session.</p>
            <form onSubmit={signIn} className="mt-7 space-y-4">
              <Field label="Username"><input className="input" autoComplete="username" value={login.username} onChange={(e) => setLogin({ ...login, username: e.target.value })} /></Field>
              <Field label="Password"><input className="input" type="password" autoComplete="current-password" value={login.password} onChange={(e) => setLogin({ ...login, password: e.target.value })} /></Field>
              <button className="button-primary w-full" disabled={busy}>Sign in to Smart Slots</button>
            </form>
            <div className="my-5 flex items-center gap-3 text-xs text-slate-400"><span className="h-px flex-1 bg-slate-200" />OR<span className="h-px flex-1 bg-slate-200" /></div>
            <button className="button w-full" disabled={busy} onClick={startDemo}>Continue without login</button>
            <p className="mt-4 text-center text-xs leading-5 text-slate-500">Demo inventory runs in a temporary database and is discarded when the server stops. Staff login: <code>staff.demo</code> / <code>123</code>.</p>
            <Notice error={error} success={notice} />
          </section>
        </div>
      </main>
    )
  }

  if (session.force_change) {
    return (
      <main className="blueprint flex min-h-screen items-center justify-center px-5">
        <form onSubmit={changePassword} className="w-full max-w-md rounded-2xl bg-white p-8 shadow-2xl">
          <div className="eyebrow">Account security</div><h1 className="mt-2 text-2xl font-semibold text-slate-800">Set a new password</h1>
          <p className="mt-2 mb-6 text-sm text-slate-500">Choose at least 12 characters before continuing.</p>
          <Field label="New password"><input className="input" type="password" minLength={12} required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          <button className="button-primary mt-4 w-full" disabled={busy}>Set password and continue</button><Notice error={error} success="" />
        </form>
      </main>
    )
  }

  return (
    <div className="min-h-screen bg-[#e7f0f8] text-[#1c2e3e]">
      <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-sky-900/15 bg-white/90 px-4 shadow-sm backdrop-blur md:px-6">
        <div className="flex items-center gap-3"><div className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-sky-500 to-sky-900 font-bold text-white">S</div><div className="font-bold tracking-[.12em] text-[#143b5b]">SMART SLOTS <span className="hidden font-normal tracking-normal text-slate-500 sm:inline">/ warehouse operations</span></div></div>
        <div className="flex items-center gap-3">
          <select className="hidden max-w-48 rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs sm:block" value={warehouse} onChange={(e) => setWarehouse(e.target.value)}>
            <option value="ALL">All warehouses</option>{data?.warehouses.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          <span className="hidden text-xs text-slate-500 sm:inline">{session.kind === 'demo' ? `DEMO · ${session.demo_id}` : `${session.user} · ${session.role}`}</span>
          <button className="button px-3 py-1.5 text-xs" onClick={signOut}>Sign out</button>
        </div>
      </header>
      <div className="mx-auto flex max-w-[1600px]">
        <aside className="sticky top-14 hidden h-[calc(100vh-3.5rem)] w-60 shrink-0 flex-col bg-[#0a2740] p-4 text-slate-200 md:flex">
          <div className="mb-5 rounded-lg border border-white/10 bg-white/5 p-3"><div className="text-[10px] uppercase tracking-widest text-sky-200">Current workspace</div><div className="mt-1 text-sm font-semibold">{warehouse === 'ALL' ? 'All warehouses' : data?.warehouses.find((item) => item.id === warehouse)?.name}</div><div className="mt-1 text-xs text-slate-400">{visibleShelves.length} locations</div></div>
          <div className="mb-2 px-2 text-[10px] uppercase tracking-[.18em] text-slate-500">Operations</div>
          {['Overview', 'Products', 'Locations', 'Transfers', 'Movements', 'Audit log', 'Assistant'].map((item) => <button key={item} onClick={() => setPage(item)} className={`mb-1 rounded-lg px-3 py-2.5 text-left text-sm transition ${page === item ? 'bg-sky-400/15 text-white shadow-[inset_3px_0_#70ccff]' : 'text-slate-300 hover:bg-white/5'}`}>{item}</button>)}
          <div className="mt-auto rounded-lg border border-sky-300/15 bg-sky-300/5 p-3 text-xs leading-5 text-slate-400">Storage profiles, dimensions, capacity, and role rules are hard allocation constraints.</div>
        </aside>
        <main className="min-w-0 flex-1 px-4 py-6 md:px-8">
          <div className="mb-4 flex gap-2 overflow-x-auto md:hidden">{['Overview', 'Products', 'Locations', 'Transfers', 'Movements', 'Audit log', 'Assistant'].map((item) => <button key={item} onClick={() => setPage(item)} className={`button whitespace-nowrap px-3 py-2 text-xs ${page === item ? 'border-sky-600 bg-sky-50 text-sky-800' : ''}`}>{item}</button>)}</div>
          <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
            <div><div className="eyebrow">{page === 'Overview' ? 'Live inventory workspace' : 'Warehouse operations'}</div><h1 className="mt-1 text-2xl font-semibold text-[#1c3c56]">{page}</h1><p className="mt-1 text-sm text-slate-500">{pageDescription(page)}</p></div>
            <div className="flex gap-2">
              {page === 'Products' && <button className="button-primary" onClick={() => { setReceiving((open) => !open); if (!receiving) setProductForm(newProductForm()) }}>＋ Receive product</button>}
              {page === 'Products' && <input className="input w-52" placeholder="Find name, SKU, barcode…" value={search} onChange={(e) => setSearch(e.target.value)} />}
              <select className="input max-w-48 md:hidden" value={warehouse} onChange={(e) => setWarehouse(e.target.value)}><option value="ALL">All warehouses</option>{data?.warehouses.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
            </div>
          </div>
          <Notice error={error} success={notice} />
          {busy && <div className="mb-3 h-1 overflow-hidden rounded bg-sky-100"><div className="h-full w-1/2 animate-pulse rounded bg-sky-500" /></div>}
          {page === 'Overview' && <Overview metrics={metrics} data={data} lots={visibleLots} onNavigate={setPage} />}
          {page === 'Products' && <section className="space-y-4">
            {receiving && <form className="panel grid gap-4 p-5 md:grid-cols-3" onSubmit={saveProduct}>
              <div className="md:col-span-3"><h2 className="font-semibold text-slate-800">Receive a product and create a lot</h2><p className="text-xs text-slate-500">Enter its storage needs and physical dimensions; shelf suitability is checked after receiving.</p></div>
              <TextField label="Product ID" required value={productForm.id} onChange={(v) => setProductForm({ ...productForm, id: v })} placeholder="e.g. DEMO-MILK-01" />
              <TextField label="SKU" required value={productForm.sku} onChange={(v) => setProductForm({ ...productForm, sku: v })} placeholder="e.g. SKU-MILK-01" />
              <TextField label="Barcode" required value={productForm.barcode} onChange={(v) => setProductForm({ ...productForm, barcode: v })} placeholder="Unique barcode" />
              <TextField label="Product name" required value={productForm.name} onChange={(v) => setProductForm({ ...productForm, name: v })} placeholder="Product name" />
              <Field label="Condition"><select className="input" value={productForm.disposition} onChange={(e) => setProductForm({ ...productForm, disposition: e.target.value })}><option value="USEFUL">Useful / reusable</option><option value="WASTE">Waste</option></select></Field>
              {productForm.disposition === 'WASTE' ? <Field label="Waste stream"><select className="input" required value={productForm.waste_category} onChange={(e) => setProductForm({ ...productForm, waste_category: e.target.value })}><option value="">Choose configured stream</option>{data?.waste_categories.map((item) => <option key={item}>{item}</option>)}</select></Field> : <Field label="Category"><select className="input" value={productForm.category} onChange={(e) => setProductForm({ ...productForm, category: e.target.value })}>{categories.map(([code, name]) => <option key={code} value={code}>{name}</option>)}</select></Field>}
              {productForm.disposition !== 'WASTE' && <Field label="Storage profile"><select className="input" value={productForm.storage} onChange={(e) => setProductForm({ ...productForm, storage: e.target.value })}>{storages.filter((item) => item !== 'WASTE').map((item) => <option key={item}>{item}</option>)}</select></Field>}
              <TextField label="Priority (1–5)" type="number" required value={productForm.priority} onChange={(v) => setProductForm({ ...productForm, priority: v })} />
              <TextField label="Length (cm)" type="number" required value={productForm.length} onChange={(v) => setProductForm({ ...productForm, length: v })} />
              <TextField label="Width (cm)" type="number" required value={productForm.width} onChange={(v) => setProductForm({ ...productForm, width: v })} />
              <TextField label="Height (cm)" type="number" required value={productForm.height} onChange={(v) => setProductForm({ ...productForm, height: v })} />
              <TextField label="Weight per unit (kg)" type="number" required value={productForm.weight} onChange={(v) => setProductForm({ ...productForm, weight: v })} />
              <TextField label="Quantity" type="number" required value={productForm.quantity} onChange={(v) => setProductForm({ ...productForm, quantity: v })} />
              <TextField label="Lot code" required value={productForm.lot} onChange={(v) => setProductForm({ ...productForm, lot: v })} placeholder="LOT-2026-001" />
              <TextField label="Expiry (optional)" type="date" value={productForm.expiry} onChange={(v) => setProductForm({ ...productForm, expiry: v })} />
              <Field label="Receiving status"><select className="input" value={String(productForm.quarantined)} onChange={(e) => setProductForm({ ...productForm, quarantined: e.target.value === 'true' })}><option value="false">Available for allocation</option><option value="true">Quarantine for review</option></select></Field>
              <div className="flex items-end gap-2 md:col-span-3"><button className="button-primary" disabled={busy}>Receive &amp; save lot</button><button type="button" className="button" onClick={() => setReceiving(false)}>Cancel</button></div>
            </form>}
            <div className="panel overflow-hidden"><div className="overflow-x-auto"><table className="data-table"><thead><tr><th>Product</th><th>Barcode</th><th>Category</th><th>Storage</th><th>Priority</th><th>Stock</th><th>Lots / allocation</th></tr></thead>
              <tbody>{products.map((product) => {
                const lots = data?.lots.filter((item) => item.product_id === product.id) || []
                return <tr key={product.id}><td><strong>{product.name}</strong><small>{product.id} · {product.sku}</small></td><td>{product.barcode}</td><td>{product.category}</td><td><Pill>{product.storage}</Pill></td><td>{product.priority}/5</td><td>{product.stock}{product.quarantined > 0 && <small className="text-red-700">{product.quarantined} in quarantine</small>}</td><td className="min-w-52">{lots.length ? lots.map((lot) => <div key={lot.id} className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs"><span>{lot.lot_code} · {lot.qty} {lot.shelf_id ? `· ${lot.shelf_id}` : '· Not allocated'}{lot.quarantined ? ' · Quarantine' : ''}</span><span className="flex gap-1">{!lot.shelf_id && !lot.quarantined && <button className="button-primary px-2 py-1 text-[11px]" onClick={() => suggest(product, lot)}>Suggest</button>}<button className="button px-2 py-1 text-[11px]" onClick={() => { setConditionLot(lot); setCondition(lot.disposition || 'USEFUL'); setWasteCategory(lot.category || '') }}>Condition</button></span></div>) : <span className="text-xs text-slate-400">Catalog only</span>}</td></tr>
              })}{!products.length && <tr><td colSpan={7} className="py-12 text-center text-slate-400">No matching products.</td></tr>}</tbody></table></div></div>
          </section>}
          {page === 'Locations' && <section className="space-y-4">
            {addingShelf && <form onSubmit={submitShelf} className="panel grid gap-3 p-5 md:grid-cols-3">
              <div className="md:col-span-3"><h2 className="font-semibold">Add a warehouse location</h2><p className="text-xs text-slate-500">Only managers and admins can add shelves.</p></div>
              <Field label="Location ID"><input className="input" name="id" required placeholder="e.g. WH001-A01-B09" /></Field>
              <Field label="Warehouse"><select className="input" name="warehouse_id" required>{data?.warehouses.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.name}</option>)}</select></Field>
              <Field label="Zone"><input className="input" name="zone" placeholder="e.g. Zone A" /></Field>
              <Field label="Storage profile"><select className="input" name="storage">{storages.map((item) => <option key={item}>{item}</option>)}</select></Field>
              <Field label="Category coverage"><input className="input" name="category_codes" defaultValue="ALL" placeholder="ALL or FOOD,PHAR" /></Field>
              <Field label="Level"><input className="input" name="level" type="number" min="1" defaultValue="1" /></Field>
              <Field label="Length (cm)"><input className="input" name="length" type="number" min="1" defaultValue="300" /></Field>
              <Field label="Width (cm)"><input className="input" name="width" type="number" min="1" defaultValue="300" /></Field>
              <Field label="Height (cm)"><input className="input" name="height" type="number" min="1" defaultValue="300" /></Field>
              <Field label="Capacity (L)"><input className="input" name="capacity_l" type="number" min="1" defaultValue="100000" /></Field>
              <Field label="Max weight (kg)"><input className="input" name="max_weight" type="number" min="1" defaultValue="100000" /></Field>
              <label className="flex items-center gap-2 text-xs text-slate-600"><input type="checkbox" name="access_easy" /> Easy access</label>
              <label className="flex items-center gap-2 text-xs text-slate-600"><input type="checkbox" name="restricted" /> Restricted location</label>
              <div className="flex gap-2 md:col-span-3"><button className="button-primary">Add location</button><button type="button" className="button" onClick={() => setAddingShelf(false)}>Cancel</button></div>
            </form>}
            {['manager', 'admin'].includes(session.role) && <div className="flex justify-end"><button className="button-primary" onClick={() => setAddingShelf((open) => !open)}>＋ Add location</button></div>}
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{visibleShelves.map((shelf) => {
            const volume = shelf.capacity_l ? Math.min(100, shelf.used_l / shelf.capacity_l * 100) : 0
            return <article key={shelf.id} className="panel p-4"><div className="flex items-start justify-between"><div><div className="font-semibold text-[#234a67]">{shelf.id}</div><div className="mt-1 text-xs text-slate-500">{shelf.warehouse_name} · {shelf.zone || '—'}</div></div><Pill tone={shelf.status === 'Available' ? 'green' : 'red'}>{shelf.status}</Pill></div><div className="mt-4 flex flex-wrap gap-2"><Pill>{shelf.storage}</Pill><Pill>{shelf.stock_units} units</Pill>{Boolean(shelf.restricted) && <Pill tone="amber">Restricted</Pill>}</div><div className="mt-4 flex justify-between text-xs text-slate-500"><span>Volume used</span><b>{format(shelf.used_l)} / {format(shelf.capacity_l)} L</b></div><div className="mt-1 h-2 rounded-full bg-slate-100"><div className="h-full rounded-full bg-gradient-to-r from-sky-500 to-blue-800" style={{ width: `${volume}%` }} /></div><div className="mt-2 text-xs text-slate-500">Weight {format(shelf.used_kg)} / {format(shelf.max_weight)} kg</div><div className="mt-3 flex items-center justify-between gap-2"><span className="truncate text-[11px] text-slate-400">Barcode · {shelf.shelf_barcode}</span>{['manager', 'admin'].includes(session.role) && <button className="button px-2 py-1 text-[11px]" disabled={busy} onClick={() => setShelfStatus(shelf)}>{shelf.status === 'Available' ? 'Mark unavailable' : 'Reopen'}</button>}</div></article>
          })}</div></section>}
          {page === 'Movements' && <section className="space-y-4">
            <form className="panel grid gap-3 p-5 md:grid-cols-4" onSubmit={submitMovement}>
              <div className="md:col-span-4"><h2 className="font-semibold">Record a stock movement</h2><p className="text-xs text-slate-500">Use Transfers for guided shelf-to-shelf moves. This form records dispatch, quarantine, release, and disposal actions.</p></div>
              <Field label="Product lot"><select className="input" name="lot_id" required>{data?.lots.map((lot) => <option key={lot.id} value={lot.id} data-product-id={lot.product_id} data-lot={lot.lot_code}>{lot.product_name} · {lot.lot_code} · {lot.qty}</option>)}</select></Field>
              <Field label="Movement type"><select className="input" name="kind"><option>DISPATCH</option><option>QUARANTINE</option><option>QUARANTINE_RELEASE</option><option>DISPOSAL</option></select></Field>
              <Field label="Quantity"><input className="input" type="number" min="1" defaultValue="1" name="qty" required /></Field>
              <Field label="Reason"><input className="input" name="reason" placeholder="Optional reason" /></Field>
              <button className="button-primary md:col-span-4" disabled={busy}>Record movement</button>
            </form>
            <div className="panel overflow-hidden"><DataTable headers={['When', 'Type', 'Product', 'Lot', 'Qty', 'From → to', 'Actor']} rows={(data?.movements || []).map((move) => [formatDate(move.created_at), move.kind, move.product_id, move.lot_code, move.qty, `${move.source_shelf || 'Receiving'} → ${move.dest_shelf || '—'}`, move.actor])} /></div>
          </section>}
          {page === 'Transfers' && <section className="space-y-4">
            <form className="panel grid gap-3 p-5 md:grid-cols-4" onSubmit={submitTransfer}>
              <div className="md:col-span-4"><h2 className="font-semibold">Start guided shelf transfer</h2><p className="text-xs text-slate-500">The destination is reserved while the lot is in transit and is revalidated when staff confirm receipt.</p></div>
              <Field label="Product lot"><select className="input" name="lot_id" required>{(data?.lots || []).filter((lot) => lot.shelf_id && !lot.quarantined).map((lot) => <option key={lot.id} value={lot.id} data-product-id={lot.product_id} data-lot={lot.lot_code}>{lot.product_name} · {lot.lot_code} · {lot.qty} at {lot.shelf_id}</option>)}</select></Field>
              <Field label="Destination location"><select className="input" name="dest_shelf" required>{(data?.shelves || []).filter((shelf) => shelf.status === 'Available').map((shelf) => <option key={shelf.id} value={shelf.id}>{shelf.id} · {shelf.storage}</option>)}</select></Field>
              <Field label="Quantity"><input className="input" type="number" min="1" defaultValue="1" name="qty" required /></Field>
              <Field label="Reference"><input className="input" name="reference" placeholder="Transfer order / docket" /></Field>
              <Field label="Reason"><input className="input" name="reason" placeholder="Optional reason" /></Field>
              <button className="button-primary self-end" disabled={busy}>Start transfer</button>
            </form>
            <div className="panel overflow-hidden"><div className="border-b border-slate-100 p-4"><h2 className="font-semibold">Awaiting destination receipt</h2></div>
              {(data?.transfers || []).length ? <div className="divide-y divide-slate-100">{data?.transfers.map((transfer) => <div key={transfer.id} className="flex flex-wrap items-center justify-between gap-3 p-4"><div><div className="font-semibold text-[#234a67]">{transfer.id} · {transfer.product_id} · {transfer.lot_code}</div><div className="mt-1 text-xs text-slate-500">{transfer.qty} units · {transfer.source_shelf} → {transfer.dest_shelf} · {transfer.reference || 'No reference'}</div></div><div className="flex gap-2"><button className="button-primary" disabled={busy} onClick={() => receiveTransfer(transfer)}>Confirm receipt at {transfer.dest_shelf}</button><button className="button" disabled={busy} onClick={() => cancelTransfer(transfer)}>Cancel</button></div></div>)}</div> : <p className="p-8 text-center text-sm text-slate-400">No transfers are awaiting receipt.</p>}
            </div>
          </section>}
          {page === 'Assistant' && <section className="panel max-w-3xl p-5">
            <div className="eyebrow">Rule-based guidance</div><h2 className="mt-1 text-lg font-semibold">Smart Slots assistant</h2><p className="mt-1 text-sm text-slate-500">Ask for guidance about a product or a warehouse workflow. The assistant never performs inventory actions.</p>
            <form onSubmit={askAssistant} className="mt-5 flex flex-col gap-2 sm:flex-row"><input className="input" required value={assistantQuestion} onChange={(event) => setAssistantQuestion(event.target.value)} placeholder="Where should a chilled product be stored?" /><button className="button-primary" disabled={busy}>Ask</button></form>
            {assistantAnswer && <div className="mt-4 rounded-lg border border-sky-100 bg-sky-50 p-4 text-sm leading-6 text-slate-700">{assistantAnswer}</div>}
          </section>}
          {page === 'Audit log' && <div className="panel overflow-hidden"><DataTable headers={['When', 'Actor', 'Action', 'Entity', 'Details']} rows={(data?.audit || []).map((entry) => [formatDate(entry.created_at), entry.actor, entry.action, entry.entity, entry.details])} /></div>}
          <footer className="mt-8 border-t border-slate-200 pt-4 text-xs text-slate-400">Smart Slots local demo · Storage and waste rules are sample configurations, not safety or compliance determinations.</footer>
        </main>
      </div>
      {selected && selectedLot && <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/55 p-4" role="dialog" aria-modal="true">
        <section className="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-xl bg-white p-5 shadow-2xl">
          <div className="flex justify-between gap-4"><div><div className="eyebrow">Ranked location recommendations</div><h2 className="mt-1 text-xl font-semibold">{selected.name}</h2><p className="mt-1 text-sm text-slate-500">{selected.storage} · {selected.length} × {selected.width} × {selected.height} cm · {selected.weight} kg/unit</p></div><button className="button h-9 px-3" onClick={() => setSelected(null)} aria-label="Close recommendations">✕</button></div>
          <div className="mt-5 space-y-2">{suggestions.length ? suggestions.map((item, index) => <article key={item.shelf_id} className={`rounded-lg border p-4 ${index === 0 ? 'border-sky-300 bg-sky-50' : 'border-slate-200'}`}>
            <div className="flex flex-wrap items-center justify-between gap-3"><div><div className="font-semibold text-[#174c70]">{index === 0 ? 'Best match · ' : `Alternative ${index} · `}{item.shelf_id}</div><div className="text-xs text-slate-500">{item.warehouse_name} · {item.zone} · level {item.level}</div></div><div className="text-right text-xs text-slate-500">{format(item.remaining_l)} L · {format(item.remaining_kg)} kg remaining</div></div>
            <div className="mt-2 text-xs text-slate-600">{item.reasons.join(' · ')}</div>
            <button className="button-primary mt-3" disabled={busy || session.kind === 'demo'} onClick={() => allocate(selected, selectedLot, item.shelf_id)}>{session.kind === 'demo' ? 'Staff confirmation required' : 'Confirm allocation'}</button>
          </article>) : <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">No shelf satisfies the product’s storage profile, category, dimensions, capacity, and role requirements.</div>}</div>
        </section>
      </div>}
      {conditionLot && <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/55 p-4" role="dialog" aria-modal="true">
        <form onSubmit={saveCondition} className="w-full max-w-md rounded-xl bg-white p-5 shadow-2xl">
          <div className="eyebrow">Inventory disposition</div><h2 className="mt-1 text-xl font-semibold">{conditionLot.product_name} · {conditionLot.lot_code}</h2>
          <p className="mb-4 mt-1 text-sm text-slate-500">Changing condition is recorded in the audit history. Any later move must satisfy the configured stream rules.</p>
          <Field label="Condition"><select className="input" value={condition} onChange={(event) => setCondition(event.target.value)}><option value="USEFUL">Still useful / reusable</option><option value="WASTE">Waste</option></select></Field>
          {condition === 'WASTE' && <Field label="Waste stream"><select className="input mt-1" required value={wasteCategory} onChange={(event) => setWasteCategory(event.target.value)}><option value="">Choose stream</option>{data?.waste_categories.map((item) => <option key={item}>{item}</option>)}</select></Field>}
          <div className="mt-5 flex justify-end gap-2"><button type="button" className="button" onClick={() => setConditionLot(null)}>Cancel</button><button className="button-primary" disabled={busy}>Save condition</button></div>
        </form>
      </div>}
    </div>
  )
}

function Overview({ metrics, data, lots, onNavigate }: { metrics: { products: number; locations: number; units: number; quarantine: number }; data: Bootstrap | null; lots: Lot[]; onNavigate: (page: string) => void }) {
  const soon = lots.filter((lot) => lot.expiry && lot.expiry <= new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10))
  return <div className="space-y-5">
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Metric label="Catalog products" value={metrics.products.toLocaleString()} footnote="Generated and received records" />
      <Metric label="Locations" value={metrics.locations.toLocaleString()} footnote="Across selected warehouses" />
      <Metric label="On-hand units" value={metrics.units.toLocaleString()} footnote="Tracked lots and quantities" />
      <Metric label="Quarantined units" value={metrics.quarantine.toLocaleString()} footnote="Excluded from ordinary placement" />
    </div>
    <div className="grid gap-4 xl:grid-cols-[1.3fr_.7fr]">
      <section className="panel overflow-hidden"><div className="flex items-center justify-between border-b border-slate-100 p-4"><div><h2 className="font-semibold">Inventory attention</h2><p className="mt-1 text-xs text-slate-500">Expiry review and stock awaiting putaway</p></div><button className="button px-3 py-1.5 text-xs" onClick={() => onNavigate('Products')}>Open products</button></div><div className="overflow-x-auto"><table className="data-table"><thead><tr><th>Product / lot</th><th>Expiry</th><th>Quantity</th><th>Location</th><th>Status</th></tr></thead><tbody>{[...soon, ...lots.filter((lot) => !lot.shelf_id && !lot.quarantined)].slice(0, 8).map((lot) => <tr key={lot.id}><td><strong>{lot.product_name}</strong><small>{lot.lot_code}</small></td><td>{lot.expiry || '—'}</td><td>{lot.qty}</td><td>{lot.shelf_id || 'Awaiting allocation'}</td><td><Pill tone={lot.quarantined ? 'red' : lot.disposition === 'WASTE' ? 'amber' : 'blue'}>{lot.quarantined ? 'Quarantine' : lot.disposition === 'WASTE' ? 'Waste' : lot.expiry ? 'Expiry review' : 'Unallocated'}</Pill></td></tr>)}</tbody></table></div></section>
      <section className="panel p-4"><div className="eyebrow">Warehouse portfolio</div><h2 className="mt-1 font-semibold">Storage by facility</h2><div className="mt-3 space-y-2">{data?.warehouses.map((warehouse) => <div key={warehouse.id} className="flex items-center justify-between rounded-lg bg-slate-50 p-3"><div><div className="text-sm font-semibold text-slate-700">{warehouse.name}</div><div className="text-xs text-slate-500">{warehouse.id}</div></div><div className="text-xs text-slate-500">{warehouse.location_count} locations</div></div>)}</div></section>
    </div>
    <section className="panel overflow-hidden"><div className="border-b border-slate-100 p-4"><h2 className="font-semibold">Recent activity</h2></div><DataTable headers={['When', 'Type', 'Product', 'Lot', 'Quantity', 'From → to', 'Actor']} rows={(data?.movements || []).slice(0, 8).map((move) => [formatDate(move.created_at), move.kind, move.product_id, move.lot_code, move.qty, `${move.source_shelf || 'Receiving'} → ${move.dest_shelf || '—'}`, move.actor])} /></section>
  </div>
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block text-xs font-semibold text-slate-600"><span className="mb-1.5 block">{label}</span>{children}</label>
}

function TextField({ label, value, onChange, placeholder, required = false, type = 'text' }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string; required?: boolean; type?: string }) {
  return <Field label={label}><input className="input" type={type} required={required} min={type === 'number' ? '0.01' : undefined} step={type === 'number' ? 'any' : undefined} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} /></Field>
}

function Notice({ error, success }: { error: string; success: string }) {
  if (!error && !success) return null
  return <div className={`mb-4 rounded-lg border px-3 py-2 text-sm ${error ? 'border-red-200 bg-red-50 text-red-800' : 'border-emerald-200 bg-emerald-50 text-emerald-800'}`}>{error || success}</div>
}

function Metric({ label, value, footnote }: { label: string; value: string; footnote: string }) {
  return <div className="panel p-4"><div className="text-xs font-medium text-slate-500">{label}</div><div className="mt-1 text-3xl font-semibold tracking-tight text-[#1e4d70]">{value}</div><div className="mt-1 text-[11px] text-slate-400">{footnote}</div></div>
}

function Pill({ children, tone = 'blue' }: { children: ReactNode; tone?: 'blue' | 'green' | 'red' | 'amber' }) {
  const classes = { blue: 'bg-sky-50 text-sky-800', green: 'bg-emerald-50 text-emerald-800', red: 'bg-red-50 text-red-800', amber: 'bg-amber-50 text-amber-800' }
  return <span className={`inline-flex rounded-full px-2 py-1 text-[10px] font-semibold ${classes[tone]}`}>{children}</span>
}

function DataTable({ headers, rows }: { headers: string[]; rows: unknown[][] }) {
  return <div className="overflow-x-auto"><table className="data-table"><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{rows.length ? rows.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex} className="max-w-72 truncate">{String(cell ?? '—')}</td>)}</tr>) : <tr><td colSpan={headers.length} className="py-10 text-center text-slate-400">No activity recorded.</td></tr>}</tbody></table></div>
}

function format(value: number) {
  return Number(value || 0).toLocaleString(undefined, { maximumFractionDigits: 1 })
}

function formatDate(value?: string) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function pageDescription(page: string) {
  const descriptions: Record<string, string> = {
    Overview: 'Monitor your inventory, warehouse capacity, and receiving attention.',
    Products: 'Search the catalog, receive products, and request explainable shelf recommendations.',
    Locations: 'Review warehouse shelves, storage profiles, availability, and remaining capacity.',
    Transfers: 'Move stock between shelves with transit tracking and destination receipt confirmation.',
    Movements: 'Record dispatch and inventory control actions with an audit trail.',
    'Audit log': 'Review the append-only history of staff actions and inventory changes.',
    Assistant: 'Ask for grounded help with product allocation and warehouse workflows.',
  }
  return descriptions[page] || ''
}
