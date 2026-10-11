import Database from 'better-sqlite3'
import { parse } from 'csv-parse/sync'
import { randomBytes, pbkdf2Sync } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { mkdtempSync, rmSync } from 'node:fs'

export const rootDir = process.cwd()
export const dbPath = resolve(process.env.NORWARE_DB_PATH || resolve(rootDir, 'norware.sqlite3'))
export type Db = Database.Database

export const wasteStreams: Array<{ category: string; storage: string; restricted: boolean; roles: string[] }> = (() => {
  const configPath = resolve(process.env.NORWARE_WASTE_CONFIG || resolve(rootDir, 'waste_config.json'))
  const parsed = JSON.parse(readFileSync(configPath, 'utf8')) as { waste_streams?: typeof wasteStreams }
  if (!Array.isArray(parsed.waste_streams) || parsed.waste_streams.length === 0) {
    throw new Error('Waste configuration must define a non-empty waste_streams list.')
  }
  for (const stream of parsed.waste_streams) {
    if (!stream.category || !stream.storage || !Array.isArray(stream.roles)) {
      throw new Error('Each waste stream must define category, storage, and roles.')
    }
  }
  return parsed.waste_streams
})()

function passwordHash(password: string, salt = randomBytes(16)) {
  return `${salt.toString('hex')}:${pbkdf2Sync(password, salt, 240000, 32, 'sha256').toString('hex')}`
}

function csvRows<T>(name: string): T[] {
  const csvPath = resolve(rootDir, name)
  if (!existsSync(csvPath)) throw new Error(`Required sample file is missing: ${name}`)
  return parse(readFileSync(csvPath), { columns: true, skip_empty_lines: true }) as T[]
}

function initializeSchema(db: Db) {
  db.exec(`
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS warehouses(id TEXT PRIMARY KEY,name TEXT,country TEXT,city TEXT,profile TEXT,description TEXT);
    CREATE TABLE IF NOT EXISTS shelves(id TEXT PRIMARY KEY,warehouse_id TEXT NOT NULL REFERENCES warehouses(id),zone TEXT,category_codes TEXT,storage TEXT,max_length REAL,max_width REAL,max_height REAL,capacity_l REAL,max_weight REAL,level INTEGER,access_easy INTEGER,status TEXT DEFAULT 'Available',restricted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY,sku TEXT UNIQUE,barcode TEXT UNIQUE,name TEXT,category_code TEXT,category TEXT,uom TEXT,length REAL,width REAL,height REAL,weight REAL,storage TEXT,priority INTEGER,fragile INTEGER,hazard TEXT,source TEXT DEFAULT 'GENERATED_EXAMPLE');
    CREATE TABLE IF NOT EXISTS shelf_labels(shelf_id TEXT PRIMARY KEY REFERENCES shelves(id),barcode TEXT UNIQUE NOT NULL);
    CREATE TABLE IF NOT EXISTS lots(id INTEGER PRIMARY KEY AUTOINCREMENT,product_id TEXT REFERENCES products(id),lot_code TEXT,expiry TEXT,qty INTEGER NOT NULL DEFAULT 0,reserved INTEGER NOT NULL DEFAULT 0,shelf_id TEXT REFERENCES shelves(id),quarantined INTEGER NOT NULL DEFAULT 0,quarantine_reason TEXT,disposition TEXT NOT NULL DEFAULT 'USEFUL',waste_category TEXT);
    CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,product_id TEXT REFERENCES products(id),lot_code TEXT,qty INTEGER,source_shelf TEXT,dest_shelf TEXT,reason TEXT,actor TEXT,created_at TEXT,status TEXT NOT NULL DEFAULT 'COMPLETED',reference TEXT);
    CREATE TABLE IF NOT EXISTS transfer_orders(id TEXT PRIMARY KEY,movement_id INTEGER REFERENCES movements(id),lot_id INTEGER REFERENCES lots(id),product_id TEXT REFERENCES products(id),lot_code TEXT,qty INTEGER NOT NULL,source_shelf TEXT,dest_shelf TEXT,reference TEXT,state TEXT NOT NULL DEFAULT 'IN_TRANSIT',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,password_hash TEXT NOT NULL,role TEXT NOT NULL,force_change INTEGER NOT NULL DEFAULT 1,active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT,action TEXT,entity TEXT,details TEXT,created_at TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'Audit records are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'Audit records are append-only'); END;
    CREATE INDEX IF NOT EXISTS lots_shelf_idx ON lots(shelf_id);
    CREATE INDEX IF NOT EXISTS products_barcode_idx ON products(barcode);
  `)
  const lotCols = new Set((db.pragma('table_info(lots)') as Array<{ name: string }>).map((col) => col.name))
  if (!lotCols.has('disposition')) db.exec("ALTER TABLE lots ADD COLUMN disposition TEXT NOT NULL DEFAULT 'USEFUL'")
  if (!lotCols.has('waste_category')) db.exec('ALTER TABLE lots ADD COLUMN waste_category TEXT')
  const movementCols = new Set((db.pragma('table_info(movements)') as Array<{ name: string }>).map((col) => col.name))
  if (!movementCols.has('status')) db.exec("ALTER TABLE movements ADD COLUMN status TEXT NOT NULL DEFAULT 'COMPLETED'")
  if (!movementCols.has('reference')) db.exec('ALTER TABLE movements ADD COLUMN reference TEXT')
  db.exec(`UPDATE lots SET waste_category=(SELECT category FROM products WHERE products.id=lots.product_id)
    WHERE disposition='WASTE' AND waste_category IS NULL
    AND EXISTS (SELECT 1 FROM products WHERE products.id=lots.product_id AND products.category_code='WASTE')`)
}

function seedDatabase(db: Db) {
  const warehouses = db.prepare('SELECT count(*) AS n FROM warehouses').get() as { n: number }
  if (warehouses.n === 0) {
    const seedWarehouse = db.prepare('INSERT OR IGNORE INTO warehouses VALUES(?,?,?,?,?,?)')
    const seeds = [
      ['WH001','Ambient General','US','Chicago','AMBIENT','General ambient goods'],
      ['WH002','Food & Cold Chain','NL','Rotterdam','CHILLED','Food and temperature controlled'],
      ['WH003','Electronics & Secure','SG','Singapore','DRY_SECURE','Electronics and secure dry goods'],
      ['WH004','Hazardous Materials','DE','Hamburg','HAZMAT','Restricted compatible materials'],
      ['WH005','Building Materials','IN','Mumbai','AMBIENT','Building and bulk goods'],
      ['WH006','Healthcare Supply','JP','Osaka','CONTROLLED','Healthcare supply inventory'],
      ['WH007','Waste','IN','Mumbai','WASTE','Separated waste streams; configurable controls'],
    ]
    for (const row of seeds) seedWarehouse.run(...row)
    for (const w of csvRows<Record<string, string>>('warehouses.csv')) {
      seedWarehouse.run(w.warehouse_id, w.warehouse_name, w.country_code, w.city, w.storage_profile, 'Imported Smart Slots sample warehouse')
    }
    const coverage: Record<string, string> = {
      WH001: 'ALL', WH002: 'FOOD,PHAR', WH003: 'ELEC,PHAR',
      WH004: 'CHEM', WH005: 'BUILD,METAL,AUTO,AGRI', WH006: 'PHAR,FOOD',
    }
    const addShelf = db.prepare('INSERT OR IGNORE INTO shelves VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
    for (const loc of csvRows<Record<string, string>>('locations.csv')) {
      addShelf.run(loc.location_id, loc.warehouse_id, loc.zone_code, coverage[loc.warehouse_id] || 'ALL',
        loc.storage_profile, 300, 300, 300, Number(loc.location_capacity_m3) * 1000, 100000,
        Number(loc.level), Number(loc.level) <= 2 ? 1 : 0, 'Available', loc.warehouse_id === 'WH004' ? 1 : 0)
    }
    wasteStreams.forEach((stream, index) => addShelf.run(
      `WH007-W${String(index + 1).padStart(2, '0')}`, 'WH007', stream.category, stream.category,
      stream.storage, 150, 150, 180, 250000, 50000, 1, 0, 'Available', Number(stream.restricted),
    ))
    addShelf.run('WH002-FROZEN-01','WH002','Frozen reserve','FOOD','FROZEN',120,120,100,320000,30000,1,1,'Available',0)
    const addProduct = db.prepare(`INSERT OR IGNORE INTO products
      (id,sku,barcode,name,category_code,category,uom,length,width,height,weight,storage,priority,fragile,hazard,source)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`)
    for (const p of csvRows<Record<string, string>>('products.csv')) {
      const parent = p.category_code.match(/^[A-Z]+/)?.[0] || p.category_code
      addProduct.run(p.product_id, p.sku, `990${p.product_id.slice(2)}`, p.product_name, parent,
        p.material_category, p.unit_of_measure, Number(p.length_cm), Number(p.width_cm), Number(p.height_cm),
        Number(p.weight_kg), p.storage_condition, Number(p.access_priority), Number(p.fragile === 'true'),
        p.hazard_level, p.data_origin)
    }
  }
  const addLabel = db.prepare('INSERT OR IGNORE INTO shelf_labels(shelf_id,barcode) VALUES(?,?)')
  for (const row of db.prepare('SELECT id FROM shelves').all() as Array<{ id: string }>) {
    addLabel.run(row.id, `NWLOC${row.id.replace(/[^A-Za-z0-9]/g, '').toUpperCase()}`)
  }
  if (!(db.prepare('SELECT count(*) AS n FROM users').get() as { n: number }).n) {
    const username = process.env.NORWARE_DEMO_USER || 'staff.demo'
    const password = process.env.NORWARE_DEMO_PASSWORD || '123'
    db.prepare('INSERT INTO users(username,password_hash,role,force_change) VALUES(?,?,?,1)')
      .run(username, passwordHash(password), 'manager')
  }
}

export function openDatabase(path = dbPath) {
  const db = new Database(path, { timeout: 15000 })
  db.pragma('foreign_keys = ON')
  initializeSchema(db)
  seedDatabase(db)
  return db
}

export function createDemoDatabase() {
  const source = openDatabase()
  const dir = mkdtempSync(resolve(tmpdir(), 'smart-slots-demo-'))
  const demoPath = resolve(dir, 'demo.sqlite3')
  source.backup(demoPath)
  source.close()
  return { dir, db: openDatabase(demoPath) }
}

export function removeDemoDatabase(dir: string) {
  rmSync(dir, { recursive: true, force: true })
}
