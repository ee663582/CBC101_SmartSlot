import { randomBytes, pbkdf2Sync, timingSafeEqual } from 'node:crypto'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { openDatabase, removeDemoDatabase, rootDir, wasteStreams, type Db } from './db'

type Session = {
  kind: 'staff' | 'demo'
  user: string
  role: string
  force_change?: boolean
  demo_id?: string
  expires_at: number
  db?: Db
  demoDir?: string
}

const sessions = new Map<string, Session>()
const staffDb = openDatabase()
const ttlSeconds = 30 * 60

process.once('exit', () => {
  for (const session of sessions.values()) {
    session.db?.close()
    if (session.demoDir) removeDemoDatabase(session.demoDir)
  }
  staffDb.close()
})

function json(data: unknown, status = 200, headers: Record<string, string> = {}) {
  return Response.json(data, { status, headers: { 'Cache-Control': 'no-store', ...headers } })
}

function fail(message: string, status = 400) {
  return json({ error: message }, status)
}

function newSession(session: Omit<Session, 'expires_at'>) {
  const id = randomBytes(32).toString('base64url')
  sessions.set(id, { ...session, expires_at: Math.floor(Date.now() / 1000) + ttlSeconds })
  return id
}

function cookieHeader(id: string, maxAge = ttlSeconds) {
  return `nw_session=${id}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${maxAge}`
}

function sessionFor(request: Request) {
  const id = request.headers.get('cookie')?.match(/(?:^|;\s*)nw_session=([^;]+)/)?.[1]
  if (!id) return { id: undefined, session: undefined }
  const session = sessions.get(id)
  if (!session || session.expires_at <= Date.now() / 1000) {
    sessions.delete(id)
    if (session?.demoDir) {
      session.db?.close()
      removeDemoDatabase(session.demoDir)
    }
    return { id, session: undefined }
  }
  return { id, session }
}

function dbFor(session: Session) {
  return session.db || staffDb
}

function actorName(session: Session) {
  return session.kind === 'demo' ? session.user : session.user
}

function audit(db: Db, actor: string, action: string, entity: string, details: unknown) {
  db.prepare('INSERT INTO audit(actor,action,entity,details,created_at) VALUES(?,?,?,?,?)')
    .run(actor, action, entity, JSON.stringify(details), new Date().toISOString())
}

function pwHash(password: string) {
  const salt = randomBytes(16)
  return `${salt.toString('hex')}:${pbkdf2Sync(password, salt, 240000, 32, 'sha256').toString('hex')}`
}

function pwCheck(password: string, encoded: string) {
  try {
    const [saltHex, expectedHex] = encoded.split(':')
    const actual = pbkdf2Sync(password, Buffer.from(saltHex, 'hex'), 240000, 32, 'sha256')
    const expected = Buffer.from(expectedHex, 'hex')
    return actual.length === expected.length && timingSafeEqual(actual, expected)
  } catch {
    return false
  }
}

function rows<T>(db: Db, sql: string, ...params: unknown[]): T[] {
  return db.prepare(sql).all(...params) as T[]
}

function one<T>(db: Db, sql: string, ...params: unknown[]): T | undefined {
  return db.prepare(sql).get(...params) as T | undefined
}

function shelfLoad(db: Db, shelfId: string, excludeTransfer?: string) {
  const result = one<{ vl: number; kg: number }>(db, `SELECT
    coalesce(sum(p.length*p.width*p.height*(l.qty-l.reserved)/1000.0),0) +
      coalesce((SELECT sum(p2.length*p2.width*p2.height*t.qty/1000.0) FROM transfer_orders t JOIN products p2 ON p2.id=t.product_id WHERE t.dest_shelf=? AND t.state='IN_TRANSIT' AND (? IS NULL OR t.id<>?)),0) vl,
    coalesce(sum(p.weight*(l.qty-l.reserved)),0) +
      coalesce((SELECT sum(p2.weight*t.qty) FROM transfer_orders t JOIN products p2 ON p2.id=t.product_id WHERE t.dest_shelf=? AND t.state='IN_TRANSIT' AND (? IS NULL OR t.id<>?)),0) kg
    FROM lots l JOIN products p ON p.id=l.product_id WHERE l.shelf_id=?`,
  shelfId, excludeTransfer || null, excludeTransfer || null, shelfId, excludeTransfer || null, excludeTransfer || null, shelfId)
  return { liters: Number(result?.vl || 0), kg: Number(result?.kg || 0) }
}

function effectiveProduct(product: Record<string, any>, lot: Record<string, any>) {
  if (lot.disposition !== 'WASTE') return product
  const stream = wasteStreams.find((item) => item.category === (lot.waste_category || product.category))
  if (!stream) throw new Error('Choose a configured waste stream for this lot before allocating it.')
  return { ...product, category_code: 'WASTE', category: stream.category, storage: stream.storage }
}

function locationAllowed(shelf: Record<string, any>, category: string, role: string) {
  if (shelf.warehouse_id === 'WH007') {
    const stream = wasteStreams.find((item) => item.category === category)
    return Boolean(stream && stream.roles.includes(role))
  }
  return !shelf.restricted || ['manager', 'admin'].includes(role)
}

function validateDestination(db: Db, product: Record<string, any>, shelfId: string, qty: number, role: string, excludeTransfer?: string) {
  const shelf = one<Record<string, any>>(db, "SELECT * FROM shelves WHERE id=? AND status='Available'", shelfId)
  if (!shelf) throw new Error('Destination shelf is unavailable.')
  if (!locationAllowed(shelf, product.category, role)) throw new Error('Configured waste-stream permissions do not authorize this movement.')
  if (shelf.warehouse_id === 'WH007' && (product.category_code !== 'WASTE' || !wasteStreams.some((w) => w.category === product.category))) {
    throw new Error('Product category is not a configured waste stream.')
  }
  if (shelf.warehouse_id !== 'WH007' && product.category_code === 'WASTE') throw new Error('Waste stock must stay in the Waste warehouse.')
  const coverage = shelf.category_codes || 'ALL'
  if (coverage !== 'ALL' && !coverage.split(',').includes(product.category_code) && !coverage.split(',').includes(product.category)) {
    throw new Error('Destination shelf category coverage does not match.')
  }
  if (product.storage !== shelf.storage) throw new Error('Storage profile mismatch.')
  if (product.length > shelf.max_length || product.width > shelf.max_width || product.height > shelf.max_height) {
    throw new Error('Product dimensions exceed shelf limits.')
  }
  const used = shelfLoad(db, shelfId, excludeTransfer)
  if (product.length * product.width * product.height * qty / 1000 > shelf.capacity_l - used.liters ||
      product.weight * qty > shelf.max_weight - used.kg) {
    throw new Error('Destination shelf lacks sufficient remaining capacity.')
  }
  return shelf
}

function suggestions(db: Db, lot: Record<string, any>, role: string) {
  const shelves = rows<Record<string, any>>(db, `SELECT sh.*,w.name warehouse_name FROM shelves sh
    JOIN warehouses w ON w.id=sh.warehouse_id WHERE sh.status='Available'`)
  const found = []
  for (const shelf of shelves) {
    const coverage = shelf.category_codes || 'ALL'
    if (shelf.storage !== lot.storage) continue
    if (shelf.warehouse_id === 'WH007' && (lot.category_code !== 'WASTE' || !wasteStreams.some((w) => w.category === lot.category))) continue
    if (shelf.warehouse_id !== 'WH007' && lot.category_code === 'WASTE') continue
    if (coverage !== 'ALL' && !coverage.split(',').includes(lot.category_code) && !coverage.split(',').includes(lot.category)) continue
    if (lot.length > shelf.max_length || lot.width > shelf.max_width || lot.height > shelf.max_height) continue
    if (!locationAllowed(shelf, lot.category, role)) continue
    const used = shelfLoad(db, shelf.id)
    const remainingL = shelf.capacity_l - used.liters - lot.length * lot.width * lot.height * lot.qty / 1000
    const remainingKg = shelf.max_weight - used.kg - lot.weight * lot.qty
    if (remainingL < 0 || remainingKg < 0) continue
    const easy = Boolean(shelf.access_easy && shelf.level <= 2)
    const fillAfter = shelf.capacity_l ? remainingL / shelf.capacity_l : 0
    const score = (lot.priority >= 4 && easy ? 35 : 0) + Math.trunc(fillAfter * 24) +
      Math.trunc((used.kg + lot.weight * lot.qty) / shelf.max_weight * 8)
    found.push({
      shelf_id: shelf.id, warehouse_id: shelf.warehouse_id, warehouse_name: shelf.warehouse_name,
      zone: shelf.zone, level: shelf.level, easy_access: easy,
      remaining_l: Math.round(remainingL * 10) / 10, remaining_kg: Math.round(remainingKg * 10) / 10,
      score, reasons: ['storage compatible', 'category compatible', 'dimensions fit', 'volume and weight capacity available',
        ...(lot.priority >= 4 && easy ? ['easy-access low shelf for frequent picking'] : [])],
    })
  }
  return found.sort((a, b) => b.score - a.score || a.remaining_l - b.remaining_l)
}

function getLot(db: Db, data: Record<string, any>) {
  const lot = data.lot_id
    ? one<Record<string, any>>(db, `SELECT l.*,p.storage,p.category_code,p.category,p.length,p.width,p.height,p.weight,p.priority,p.name
      FROM lots l JOIN products p ON p.id=l.product_id WHERE l.product_id=? AND l.id=?`, data.product_id, Number(data.lot_id))
    : one<Record<string, any>>(db, `SELECT l.*,p.storage,p.category_code,p.category,p.length,p.width,p.height,p.weight,p.priority,p.name
      FROM lots l JOIN products p ON p.id=l.product_id WHERE l.product_id=? AND l.lot_code=? ORDER BY l.id LIMIT 1`, data.product_id, data.lot)
  return lot
}

function bootstrap(db: Db, session: Session) {
  const warehouses = rows(db, `SELECT w.*,count(sh.id) location_count FROM warehouses w
    LEFT JOIN shelves sh ON sh.warehouse_id=w.id GROUP BY w.id ORDER BY w.id`)
  const shelves = rows(db, `SELECT sh.*,w.name warehouse_name,lab.barcode shelf_barcode,
    coalesce(sum(l.qty-l.reserved),0) stock_units,
    coalesce(sum(p.length*p.width*p.height*(l.qty-l.reserved)/1000.0),0) +
      coalesce((SELECT sum(ip.length*ip.width*ip.height*t.qty/1000.0) FROM transfer_orders t JOIN products ip ON ip.id=t.product_id WHERE t.dest_shelf=sh.id AND t.state='IN_TRANSIT'),0) used_l,
    coalesce(sum(p.weight*(l.qty-l.reserved)),0) +
      coalesce((SELECT sum(ip.weight*t.qty) FROM transfer_orders t JOIN products ip ON ip.id=t.product_id WHERE t.dest_shelf=sh.id AND t.state='IN_TRANSIT'),0) used_kg
    FROM shelves sh JOIN warehouses w ON w.id=sh.warehouse_id
    LEFT JOIN shelf_labels lab ON lab.shelf_id=sh.id LEFT JOIN lots l ON l.shelf_id=sh.id
    LEFT JOIN products p ON p.id=l.product_id GROUP BY sh.id ORDER BY sh.warehouse_id,sh.id`)
  const products = rows(db, `SELECT p.id,p.sku,p.barcode,p.name,p.category_code,p.category,p.uom,p.length,p.width,p.height,
    p.weight,p.storage,p.priority,p.fragile,p.hazard,p.source,coalesce(sum(l.qty),0) stock,
    coalesce(sum(CASE WHEN l.quarantined=1 THEN l.qty ELSE 0 END),0) quarantined
    FROM products p LEFT JOIN lots l ON l.product_id=p.id GROUP BY p.id
    ORDER BY CASE WHEN p.id LIKE 'DEMO-%' THEN 0 ELSE 1 END,p.name LIMIT 160`)
  const lots = rows(db, `SELECT l.*,p.name product_name,p.barcode,p.storage,p.category,s.warehouse_id,s.id current_shelf
    FROM lots l JOIN products p ON p.id=l.product_id LEFT JOIN shelves s ON s.id=l.shelf_id ORDER BY l.id DESC LIMIT 250`)
  return {
    session: { kind: session.kind, user: session.user, role: session.role, force_change: session.force_change, demo_id: session.demo_id, expires_at: session.expires_at },
    warehouses, shelves, products, product_count: one<{ n: number }>(db, 'SELECT count(*) n FROM products')?.n || 0,
    lots, movements: rows(db, 'SELECT * FROM movements ORDER BY id DESC LIMIT 500'),
    transfers: rows(db, "SELECT * FROM transfer_orders WHERE state='IN_TRANSIT' ORDER BY created_at DESC"),
    audit: rows(db, 'SELECT * FROM audit ORDER BY id DESC LIMIT 40'),
    waste_categories: wasteStreams.map((stream) => stream.category),
  }
}

export async function handleApi(request: Request) {
  const url = new URL(request.url)
  const path = url.pathname.replace(/^\/api\/?/, '/')
  const method = request.method
  const data = method === 'POST' ? await request.json().catch(() => null) : null
  if (method === 'POST' && !data) return fail('Invalid request body.')
  const body = (data || {}) as Record<string, any>
  const { id: sessionId, session } = sessionFor(request)

  if (path === '/session' && method === 'GET') {
    return json({ session: session ? {
      kind: session.kind, user: session.user, role: session.role,
      force_change: session.force_change, demo_id: session.demo_id, expires_at: session.expires_at,
    } : null })
  }
  if (path === '/entry/demo' && method === 'POST') {
    const dir = resolve(mkdtempPath())
    mkdirSync(dir, { recursive: true })
    const demoDbPath = resolve(dir, 'demo.sqlite3')
    await staffDb.backup(demoDbPath)
    const demoDb = openDatabase(demoDbPath)
    const demoId = `DEMO-${randomBytes(3).toString('hex').toUpperCase()}`
    const newId = newSession({ kind: 'demo', user: 'Local Demo', role: 'demo', demo_id: demoId, db: demoDb, demoDir: dir })
    const current = sessions.get(newId)!
    return json({ session: { kind: current.kind, user: current.user, role: current.role, demo_id: demoId, expires_at: current.expires_at } }, 200, {
      'Set-Cookie': cookieHeader(newId),
    })
  }
  if (path === '/login' && method === 'POST') {
    const username = String(body.username || '')
    const password = String(body.password || '')
    const user = one<{ username: string; password_hash: string; role: string; force_change: number }>(
      staffDb, 'SELECT username,password_hash,role,force_change FROM users WHERE username=? AND active=1', username,
    )
    if (!user || !pwCheck(password, user.password_hash)) {
      if (user) audit(staffDb, username, 'LOGIN_FAILED', 'user', {})
      return fail('Username or password is incorrect.', 401)
    }
    audit(staffDb, username, 'LOGIN', 'user', { role: user.role })
    const newId = newSession({ kind: 'staff', user: username, role: user.role, force_change: Boolean(user.force_change) })
    const current = sessions.get(newId)!
    return json({ session: { kind: current.kind, user: current.user, role: current.role, force_change: current.force_change, expires_at: current.expires_at } }, 200, {
      'Set-Cookie': cookieHeader(newId),
    })
  }
  if (path === '/logout' && method === 'POST') {
    if (session && sessionId) {
      sessions.delete(sessionId)
      if (session.kind === 'staff') audit(staffDb, session.user, 'LOGOUT', 'session', { reason: 'staff exit' })
      if (session.demoDir) {
        session.db?.close()
        removeDemoDatabase(session.demoDir)
      }
    }
    return json({ ok: true }, 200, { 'Set-Cookie': cookieHeader('', 0) })
  }

  if (!session) return fail('Sign in or start a local demo session first.', 401)
  if (session.force_change && path !== '/password') return fail('Change the temporary staff password before continuing.', 403)
  const db = dbFor(session)
  const actor = actorName(session)
  const role = session.role

  try {
    if (path === '/password' && method === 'POST') {
      if (session.kind !== 'staff') throw new Error('Staff login is required to change a password.')
      const password = String(body.password || '')
      if (password.length < 12) throw new Error('Use at least 12 characters for the new password.')
      db.prepare('UPDATE users SET password_hash=?,force_change=0 WHERE username=?').run(pwHash(password), actor)
      audit(db, actor, 'PASSWORD_CHANGED', 'user', {})
      session.force_change = false
      return json({ ok: true })
    }
    if (path === '/bootstrap' && method === 'GET') return json(bootstrap(db, session))
    if (path === '/search' && method === 'GET') {
      const q = url.searchParams.get('q') || ''
      const products = rows(db, `SELECT p.*,coalesce(sum(l.qty),0) stock,
        coalesce(sum(CASE WHEN l.quarantined=1 THEN l.qty ELSE 0 END),0) quarantined
        FROM products p LEFT JOIN lots l ON l.product_id=p.id
        WHERE p.barcode=? OR p.sku=? OR p.id=? OR p.name LIKE ? OR p.category LIKE ?
        GROUP BY p.id ORDER BY CASE WHEN p.id LIKE 'DEMO-%' THEN 0 ELSE 1 END,p.name LIMIT 30`,
      q, q, q, `%${q}%`, `%${q}%`)
      const shelves = rows(db, `SELECT s.*,l.barcode shelf_barcode FROM shelves s LEFT JOIN shelf_labels l ON l.shelf_id=s.id
        WHERE s.id=? OR l.barcode=?`, q, q)
      return json({ products, shelves })
    }
    if (path === '/audit' && method === 'GET') return json({ audit: rows(db, 'SELECT * FROM audit ORDER BY id DESC LIMIT 100') })
    if (path === '/products' && method === 'POST') {
      const required = ['id','sku','barcode','name','category','length','width','height','weight','storage','priority','quantity','lot']
      if (required.some((key) => body[key] === undefined || body[key] === null || body[key] === '')) {
        throw new Error('Complete all required product and lot fields.')
      }
      const disposition = String(body.disposition || 'USEFUL').toUpperCase()
      if (!['USEFUL', 'WASTE'].includes(disposition)) throw new Error('Choose whether the product is still useful or is waste.')
      let categoryCode = body.category
      let categoryName = body.category_name || body.category
      let storage = body.storage
      const wasteCategory = body.waste_category || null
      if (disposition === 'WASTE') {
        const stream = wasteStreams.find((item) => item.category === wasteCategory)
        if (!stream) throw new Error('Choose a configured waste stream for this product.')
        categoryCode = 'WASTE'
        categoryName = stream.category
        storage = stream.storage
      } else if (categoryCode === 'WASTE') {
        throw new Error('Choose Waste as the product condition and select a waste stream.')
      }
      const qty = Number(body.quantity)
      if (!Number.isInteger(qty) || qty <= 0) throw new Error('Quantity must be a positive whole number.')
      if (Number(body.priority) < 1 || Number(body.priority) > 5) throw new Error('Priority must be between 1 and 5.')
      if (one(db, 'SELECT 1 FROM products WHERE id=? OR sku=? OR barcode=?', body.id, body.sku, body.barcode)) {
        throw new Error('Product ID, SKU, or barcode already exists.')
      }
      const tx = db.transaction(() => {
        db.prepare(`INSERT INTO products(id,sku,barcode,name,category_code,category,uom,length,width,height,weight,storage,priority,fragile,hazard)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
          body.id, body.sku, body.barcode, body.name, categoryCode, categoryName, body.uom || 'each',
          Number(body.length), Number(body.width), Number(body.height), Number(body.weight), storage,
          Number(body.priority), Number(Boolean(body.fragile)), body.hazard || 'LOW',
        )
        const result = db.prepare(`INSERT INTO lots(product_id,lot_code,expiry,qty,quarantined,quarantine_reason,disposition,waste_category)
          VALUES(?,?,?,?,?,?,?,?)`).run(body.id, body.lot, body.expiry || null, qty, Number(Boolean(body.quarantined)),
          body.quarantine_reason || null, disposition, disposition === 'WASTE' ? wasteCategory : null)
        const kind = body.quarantined ? 'QUARANTINE' : 'RECEIVING'
        db.prepare('INSERT INTO movements(kind,product_id,lot_code,qty,reason,actor,created_at) VALUES(?,?,?,?,?,?,?)')
          .run(kind, body.id, body.lot, qty, body.quarantine_reason || 'Product received into unallocated stock', actor, new Date().toISOString())
        audit(db, actor, 'PRODUCT_RECEIVED', body.id, { quantity: qty, lot: body.lot, quarantined: Boolean(body.quarantined), disposition })
        return result.lastInsertRowid
      })
      return json({ ok: true, lot_id: tx() })
    }
    if (path === '/suggest' && method === 'POST') {
      const lot = getLot(db, body)
      if (!lot) throw new Error('Product lot not found.')
      if (lot.quarantined) throw new Error('Quarantined stock cannot be allocated normally.')
      if (lot.expiry && lot.expiry <= new Date().toISOString().slice(0, 10)) throw new Error('Expired stock cannot be allocated. Review authorized disposition.')
      const product = effectiveProduct(lot, lot)
      const matching = suggestions(db, { ...lot, ...product }, role)
      return json({
        suggestions: matching.slice(0, 8), eligible_count: matching.length,
        message: matching.length ? null : 'No shelf passes storage, category, dimensions, capacity, availability, and access rules.',
      })
    }
    if (path === '/allocate' && method === 'POST') {
      if (session.kind === 'demo') throw new Error('Local demo sessions cannot confirm stock placement.')
      const lot = getLot(db, body)
      if (!lot || lot.quarantined) throw new Error('Lot missing or quarantined.')
      const product = effectiveProduct(lot, lot)
      const shelf = validateDestination(db, product, String(body.shelf_id || ''), Number(lot.qty), role)
      const tx = db.transaction(() => {
        db.prepare('UPDATE lots SET shelf_id=? WHERE id=?').run(shelf.id, lot.id)
        db.prepare(`INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,dest_shelf,reason,actor,created_at)
          VALUES('PUTAWAY',?,?,?,?,?,?,?,?)`).run(lot.product_id, lot.lot_code, lot.qty, lot.shelf_id, shelf.id, 'Staff-confirmed allocation', actor, new Date().toISOString())
        audit(db, actor, 'ALLOCATION_CONFIRMED', lot.product_id, { lot: lot.lot_code, quantity: lot.qty, source: lot.shelf_id, destination: shelf.id })
      })
      tx()
      return json({ ok: true })
    }
    if (path === '/transfer/start' && method === 'POST') {
      const lot = getLot(db, body)
      const qty = Number(body.qty)
      const dest = String(body.dest_shelf || '')
      if (!lot || lot.quarantined) throw new Error('Choose a valid, non-quarantined lot.')
      if (!Number.isInteger(qty) || qty <= 0 || qty > lot.qty - lot.reserved) throw new Error('Transfer quantity must be within available lot stock.')
      if (!lot.shelf_id) throw new Error('This lot is not on a shelf. Use receiving or putaway instead of transfer.')
      if (lot.shelf_id === dest) throw new Error('Choose a different destination shelf.')
      if (lot.expiry && lot.expiry <= new Date().toISOString().slice(0, 10)) throw new Error('Expired stock cannot be transferred as usable stock.')
      const product = effectiveProduct(lot, lot)
      validateDestination(db, product, dest, qty, role)
      const transferId = `TR-${randomBytes(5).toString('hex').toUpperCase()}`
      const now = new Date().toISOString()
      const tx = db.transaction(() => {
        let transitLotId = lot.id
        if (qty === lot.qty) {
          db.prepare('UPDATE lots SET shelf_id=NULL WHERE id=?').run(lot.id)
        } else {
          db.prepare('UPDATE lots SET qty=qty-? WHERE id=?').run(qty, lot.id)
          const inserted = db.prepare(`INSERT INTO lots(product_id,lot_code,expiry,qty,shelf_id,quarantined,disposition,waste_category)
            VALUES(?,?,?, ?,NULL,0,?,?)`).run(lot.product_id, lot.lot_code, lot.expiry, qty, lot.disposition, lot.waste_category)
          transitLotId = Number(inserted.lastInsertRowid)
        }
        const movement = db.prepare(`INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,dest_shelf,reason,actor,created_at,status,reference)
          VALUES('TRANSFER',?,?,?,?,?,?,?,?,?,?)`).run(
          lot.product_id, lot.lot_code, qty, lot.shelf_id, dest, body.reason || 'Transfer started',
          actor, now, 'IN_TRANSIT', String(body.reference || '').trim(),
        )
        db.prepare(`INSERT INTO transfer_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?)`).run(
          transferId, movement.lastInsertRowid, transitLotId, lot.product_id, lot.lot_code, qty,
          lot.shelf_id, dest, String(body.reference || '').trim(), 'IN_TRANSIT', now, now,
        )
        audit(db, actor, 'TRANSFER_STARTED', transferId, {
          product: lot.product_id, lot: lot.lot_code, quantity: qty, source: lot.shelf_id,
          destination: dest, reference: body.reference || '',
        })
      })
      tx()
      return json({ ok: true, transfer_id: transferId })
    }
    if (path === '/transfer/receive' && method === 'POST') {
      const transfer = one<Record<string, any>>(db,
        "SELECT * FROM transfer_orders WHERE id=? AND state='IN_TRANSIT'", body.transfer_id)
      if (!transfer) throw new Error('Transfer is not awaiting receipt.')
      if (String(body.shelf_id || '') !== transfer.dest_shelf) throw new Error('Scan or enter the assigned destination shelf before confirming receipt.')
      const lot = one<Record<string, any>>(db, 'SELECT * FROM lots WHERE id=?', transfer.lot_id)
      if (!lot) throw new Error('In-transit lot record is missing; contact a manager.')
      const product = one<Record<string, any>>(db, 'SELECT * FROM products WHERE id=?', transfer.product_id)
      if (!product) throw new Error('Transfer product record is missing.')
      const effective = effectiveProduct(product, lot)
      validateDestination(db, effective, transfer.dest_shelf, transfer.qty, role, transfer.id)
      const now = new Date().toISOString()
      const tx = db.transaction(() => {
        db.prepare('UPDATE lots SET shelf_id=? WHERE id=?').run(transfer.dest_shelf, lot.id)
        db.prepare("UPDATE transfer_orders SET state='RECEIVED',updated_at=? WHERE id=?").run(now, transfer.id)
        db.prepare("UPDATE movements SET status='COMPLETED' WHERE id=?").run(transfer.movement_id)
        db.prepare(`INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,dest_shelf,reason,actor,created_at,status,reference)
          VALUES('TRANSFER_RECEIPT',?,?,?,?,?,?,?,?,?,?)`).run(
          transfer.product_id, transfer.lot_code, transfer.qty, transfer.source_shelf,
          transfer.dest_shelf, body.reason || 'Destination receipt confirmed', actor, now, 'COMPLETED', transfer.reference,
        )
        audit(db, actor, 'TRANSFER_RECEIVED', transfer.id, { quantity: transfer.qty, destination: transfer.dest_shelf, reference: transfer.reference })
      })
      tx()
      return json({ ok: true })
    }
    if (path === '/transfer/cancel' && method === 'POST') {
      const transfer = one<Record<string, any>>(db,
        "SELECT * FROM transfer_orders WHERE id=? AND state='IN_TRANSIT'", body.transfer_id)
      if (!transfer) throw new Error('Transfer is not awaiting receipt.')
      const now = new Date().toISOString()
      const tx = db.transaction(() => {
        db.prepare("UPDATE transfer_orders SET state='CANCELLED',updated_at=? WHERE id=?").run(now, transfer.id)
        db.prepare("UPDATE movements SET status='CANCELLED' WHERE id=?").run(transfer.movement_id)
        audit(db, actor, 'TRANSFER_CANCELLED', transfer.id, {
          quantity: transfer.qty, source: transfer.source_shelf, stock: 'returned to unallocated stock',
        })
      })
      tx()
      return json({ ok: true })
    }
    if (path === '/shelf' && method === 'POST') {
      if (!['manager', 'admin'].includes(role)) throw new Error('Manager role required to add shelves.')
      const label = `NWLOC${String(body.id || '').replace(/[^A-Za-z0-9]/g, '').toUpperCase()}`
      const tx = db.transaction(() => {
        db.prepare(`INSERT INTO shelves VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
          body.id, body.warehouse_id, body.zone, body.category_codes || 'ALL', body.storage, Number(body.length),
          Number(body.width), Number(body.height), Number(body.capacity_l), Number(body.max_weight),
          Number(body.level), Number(Boolean(body.access_easy)), 'Available', Number(Boolean(body.restricted)),
        )
        db.prepare('INSERT INTO shelf_labels(shelf_id,barcode) VALUES(?,?)').run(body.id, label)
        audit(db, actor, 'SHELF_ADDED', body.id, { warehouse: body.warehouse_id, shelf_barcode: label })
      })
      tx()
      return json({ ok: true, shelf_barcode: label })
    }
    if (path === '/shelf/status' && method === 'POST') {
      if (!['manager', 'admin'].includes(role)) throw new Error('Manager role required to change shelf availability.')
      const shelf = one<Record<string, any>>(db, 'SELECT * FROM shelves WHERE id=?', body.shelf_id)
      if (!shelf) throw new Error('Shelf not found.')
      if (!['Available', 'Unavailable'].includes(body.status)) throw new Error('Invalid shelf status.')
      if (body.status === 'Unavailable') {
        if (one(db, "SELECT 1 FROM transfer_orders WHERE dest_shelf=? AND state='IN_TRANSIT' LIMIT 1", shelf.id)) {
          throw new Error('This shelf has an inbound transfer awaiting receipt.')
        }
        if (one(db, 'SELECT 1 FROM lots WHERE shelf_id=? AND qty>0 LIMIT 1', shelf.id)) {
          throw new Error('Move or resolve all shelf stock before marking it unavailable.')
        }
      }
      db.prepare('UPDATE shelves SET status=? WHERE id=?').run(body.status, shelf.id)
      audit(db, actor, 'SHELF_STATUS', shelf.id, { status: body.status })
      return json({ ok: true })
    }
    if (path === '/lot/disposition' && method === 'POST') {
      const lot = one<Record<string, any>>(db, 'SELECT * FROM lots WHERE id=?', Number(body.lot_id))
      if (!lot) throw new Error('Product lot not found.')
      const disposition = String(body.disposition || '').toUpperCase()
      if (!['USEFUL', 'WASTE'].includes(disposition)) throw new Error('Choose whether the product is still useful or is waste.')
      const stream = disposition === 'WASTE' ? wasteStreams.find((item) => item.category === body.waste_category) : undefined
      if (disposition === 'WASTE' && !stream) throw new Error('Choose a configured waste stream for this lot.')
      if (stream && !stream.roles.includes(session.kind === 'staff' ? role : 'operator')) throw new Error('Your role is not authorized for this waste stream.')
      if (one(db, "SELECT 1 FROM transfer_orders WHERE lot_id=? AND state='IN_TRANSIT' LIMIT 1", lot.id)) {
        throw new Error('Receive or cancel the in-transit lot before changing its condition.')
      }
      const category = stream?.category || null
      const tx = db.transaction(() => {
        db.prepare('UPDATE lots SET disposition=?,waste_category=? WHERE id=?').run(disposition, category, lot.id)
        const details = { lot: lot.lot_code, disposition, waste_category: category }
        audit(db, actor, 'LOT_DISPOSITION_CHANGED', lot.product_id, details)
        db.prepare(`INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,reason,actor,created_at,status)
          VALUES('DISPOSITION_CHANGE',?,?,?,?,?,?,?,'COMPLETED')`).run(
          lot.product_id, lot.lot_code, lot.qty, lot.shelf_id,
          `Lot condition changed to ${disposition}${category ? ` · ${category}` : ''}`, actor, new Date().toISOString(),
        )
      })
      tx()
      return json({ ok: true })
    }
    if (path === '/movement' && method === 'POST') {
      const allowed = ['RECEIVING','PUTAWAY','DISPATCH','ADJUSTMENT','QUARANTINE','QUARANTINE_RELEASE','DISPOSAL']
      const kind = String(body.kind || '')
      if (!allowed.includes(kind)) throw new Error('Choose a valid movement type.')
      if (['QUARANTINE_RELEASE', 'DISPOSAL'].includes(kind) && !['manager', 'admin'].includes(role)) {
        throw new Error('Manager role required for quarantine release or disposal.')
      }
      const lot = getLot(db, body)
      const qty = Number(body.qty)
      if (!lot || !Number.isInteger(qty) || qty <= 0) throw new Error('Valid product lot and positive quantity are required.')
      if (lot.quarantined && !['QUARANTINE_RELEASE','DISPOSAL'].includes(kind)) throw new Error('Quarantined stock must be released by an authorized manager before normal movement.')
      if (lot.expiry && lot.expiry <= new Date().toISOString().slice(0, 10) && kind === 'DISPATCH') {
        throw new Error('Expired stock cannot be dispatched as usable stock; record an authorized disposal.')
      }
      const source = body.source_shelf || lot.shelf_id || null
      const dest = body.dest_shelf || null
      if (['PUTAWAY','RECEIVING'].includes(kind) && dest) {
        const product = effectiveProduct(lot, lot)
        validateDestination(db, product, dest, qty, role)
      }
      const tx = db.transaction(() => {
        if (kind === 'QUARANTINE') db.prepare('UPDATE lots SET quarantined=1,quarantine_reason=? WHERE id=?').run(body.reason || 'Quarantine pending review', lot.id)
        else if (kind === 'QUARANTINE_RELEASE') db.prepare('UPDATE lots SET quarantined=0,quarantine_reason=NULL WHERE id=?').run(lot.id)
        else if (kind === 'DISPATCH' || kind === 'DISPOSAL') {
          if (qty > lot.qty - lot.reserved) throw new Error('Quantity exceeds available lot stock.')
          db.prepare('UPDATE lots SET qty=qty-? WHERE id=?').run(qty, lot.id)
        } else if (kind === 'ADJUSTMENT') {
          const delta = Number(body.delta || 0)
          const nextQty = lot.qty + delta
          if (!delta || nextQty < lot.reserved) throw new Error('Adjustment quantity is invalid or would reduce stock below the reserved quantity.')
          db.prepare('UPDATE lots SET qty=? WHERE id=?').run(nextQty, lot.id)
        } else if (kind === 'PUTAWAY') {
          if (!dest) throw new Error('Select a destination shelf.')
          db.prepare('UPDATE lots SET shelf_id=? WHERE id=?').run(dest, lot.id)
        } else if (kind === 'RECEIVING') {
          db.prepare('UPDATE lots SET qty=qty+?,shelf_id=coalesce(?,shelf_id) WHERE id=?').run(qty, dest, lot.id)
        }
        audit(db, actor, kind, lot.product_id, { lot: lot.lot_code, quantity: qty, source, destination: dest, reason: body.reason || '' })
        db.prepare(`INSERT INTO movements(kind,product_id,lot_code,qty,source_shelf,dest_shelf,reason,actor,created_at,status,reference)
          VALUES(?,?,?,?,?,?,?,?,?,'COMPLETED',?)`).run(kind, lot.product_id, lot.lot_code, qty, source, dest,
          body.reason || '', actor, new Date().toISOString(), body.reference || null)
      })
      tx()
      return json({ ok: true })
    }
    if (path === '/assistant' && method === 'POST') {
      const query = String(body.question || '').toLowerCase()
      const productId = String(body.product_id || '')
      const product = productId ? one<Record<string, any>>(db,
        'SELECT * FROM products WHERE id=? OR barcode=? OR sku=?', productId, productId, productId) : undefined
      const advice = []
      if (product) {
        advice.push(`${product.name} requires ${product.storage} storage; category ${product.category}; priority ${product.priority}/5.`)
        const lots = rows<Record<string, any>>(db, `SELECT l.*,p.storage,p.category_code,p.category,p.length,p.width,p.height,p.weight,p.priority
          FROM lots l JOIN products p ON p.id=l.product_id WHERE l.product_id=? AND l.qty>0 ORDER BY l.expiry,l.lot_code`, product.id)
        const lot = lots.find((item) => !item.quarantined && (!item.expiry || item.expiry > new Date().toISOString().slice(0, 10)))
        if (lot) {
          const actual = effectiveProduct(lot, lot)
          const ranked = suggestions(db, { ...lot, ...actual }, role)
          advice.push(ranked.length ? `Best eligible location is ${ranked[0].shelf_id} in ${ranked[0].warehouse_name}; ${ranked[0].remaining_l} L and ${ranked[0].remaining_kg} kg remain after placement.` : 'No location currently passes storage, category, dimensions, capacity, availability, and role rules.')
        } else advice.push('No eligible unexpired, non-quarantined stock lot was found.')
      }
      if (query.includes('capacity') || query.includes('space')) advice.push('Capacity checks include product dimensions, remaining volume, and remaining weight.')
      if (query.includes('expiry') || query.includes('expir')) advice.push('Review expiry dates and use FEFO (first expiry, first out) where local policy allows.')
      if (query.includes('waste') || query.includes('battery')) advice.push('Waste storage follows the configured stream and role rules; confirm local handling requirements.')
      if (!advice.length) advice.push('Ask about a product ID, SKU, barcode, storage profile, shelf capacity, expiry, or waste stream.')
      return json({ answer: advice.join(' '), mode: 'rule-based Smart Slots assistant', references: product ? [`product:${product.id}`] : [] })
    }
    return fail('Not found.', 404)
  } catch (error) {
    return fail(error instanceof Error ? error.message : 'Request failed.')
  }
}

function mkdtempPath() {
  return resolve(tmpdir(), `smart-slots-demo-${randomBytes(8).toString('hex')}`)
}

export function closeDatabases() {
  staffDb.close()
}

export function getAppRoot() {
  return rootDir
}
