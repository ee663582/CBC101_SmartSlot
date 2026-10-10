# NORWARE Warehouse Operations

NORWARE is a local warehouse management application with a BIOS-inspired entry screen, temporary demo ID, staff login, dark blue warehouse blueprint styling, and seven warehouse choices including an **All warehouses** view. The local app uses Python 3 and SQLite with no required third-party packages. It imports the 10,000-row generated product catalog and the original six warehouses/120 sample locations, then adds a seventh Waste warehouse with dedicated sections and a freezer shelf.

The application has product and shelf barcode lookup, printable Code 39 barcode labels, manual entry, storage/category/dimension/capacity matching, ranked location suggestions, staff-confirmed allocation, capacity tracking, receiving/putaway/transfer/dispatch/adjustment, expiry alerts, quarantine controls, append-only audit records, and a warehouse assistant. The assistant uses current NORWARE data and rules locally; when `OPENAI_API_KEY` is configured it can optionally explain results through the OpenAI Responses API. It never performs warehouse actions. If the API is not configured or unavailable, it falls back to deterministic guidance.

The sample product **Plastic Bottle** is searchable/scannable by barcode `8908006727421`. Staff can change a lot's condition between reusable and waste in its product record; waste requires a configured stream (for example, Plastics), is audit logged, and receives allocation suggestions only in that permitted Waste stream. Demo users can try this in the temporary demo database, which never changes staff records and resets when the server stops.

## Run locally

From this `outputs` folder:

```sh
python3 norware_app.py
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The first run creates `norware.sqlite3` in this folder and imports the sample product catalog. Keep the server bound to localhost; this starter app is not a public internet deployment.

## Entry options

- **Local demo:** choose “Continue without login.” NORWARE creates a temporary DEMO ID and a 30-minute session. Demo inventory is held in a separate temporary database and is discarded when the server stops; demo mode cannot change persistent staff inventory or approve placements. Product condition edits are simulated only in that temporary database.
- **Staff:** username `staff.demo`; local-only temporary password `123`. The first sign-in requires a password change. The staff database stores a PBKDF2-SHA256 salted password hash; the password is not in the browser UI or server logs. Override the seed password with `NORWARE_DEMO_PASSWORD` before the first launch. This credential is for the localhost demo only and must not be used for a production deployment.
- Add staff accounts interactively with `python3 norware_app.py add-user USERNAME operator|manager|admin`. New staff must change their initial password after first sign-in. Managers can add/disable shelves and manage restricted waste streams; operators can receive stock and use normal allocation/movement workflows.

For a new database, set `NORWARE_DEMO_USER` and/or `NORWARE_DEMO_PASSWORD` before the first launch to choose the seeded account. Existing databases keep their current password hash except the bundled legacy demo hash, which is migrated to the configured password or `123` and still requires a first-login change. Staff change passwords through the application. Configure optional assistant access by setting `OPENAI_API_KEY` on the server and, optionally, `NORWARE_AI_MODEL` (default `gpt-6-astra`). Never put API keys in the HTML or commit them to source control.

Waste streams, required storage profile, restricted status, and allowed staff roles are configured in `waste_config.json` (or the file named by `NORWARE_WASTE_CONFIG`). Review this against local policies before use. New configured streams receive their own location on startup; changing an existing stream's physical storage requirement may require a new facility/location plan and must not be treated as a compliance determination.

## Barcode workflow

Most USB and Bluetooth barcode scanners type into the focused field and send Enter. Open **Scan product / shelf**, scan or type a product barcode, review the product and lot, then scan a location label. A scanned location is checked against the same suitability rules as a manually selected shelf. Scanning alone never confirms an allocation or movement. Print product and shelf labels from their record/location view; shelf labels use generated `NWLOC…` codes. To classify an existing lot, open its product record, choose **Still useful / reusable** or **Waste**, select a configured waste stream when needed, and save. The change is recorded in the audit trail; after changing the condition, use the guided transfer workflow to move already-shelved stock to a location compatible with the new condition.

## Data, configuration, and limitations

- All 10,000 catalog records are generated examples, not sourced product listings. The six original sample warehouses and 120 locations are retained; the app adds Waste and new stream locations. The CSV inventory file is **not** loaded as live shelf stock because its generated shelf placements are not physically verified. Five clearly marked demo lots show the operational flows instead.
- The app is a local single-process SQLite starter. Sessions are held in server memory and expire after 30 minutes. Demo sample stock is isolated from persistent staff records and reset when the process exits. It does not yet have production-grade deployment controls, TLS, multi-instance session storage, backups, scanner hardware integration, or an administrator UI for permission policies. Do not expose it to a shared or public network without a production security review.
- Waste stream names and restrictions are configurable examples, not legal classifications. Confirm local rules, safety data, storage compatibility, and authorized handling with qualified staff. Capacity, sample dimensions, priorities, and demo warehouse data must be replaced with verified facility information before operational use.
- `schema.sql`, `load.sql`, and the CSVs remain available as the PostgreSQL dataset deliverable. The interactive local app uses SQLite for a no-dependency local run; it does not connect to PostgreSQL yet.

## Regenerate and verify

Regenerate the deterministic import data and run its CSV checks:

```sh
python3 generate_data.py
python3 validate_data.py
```

Run the end-to-end application checks against an isolated temporary database:

```sh
python3 verify_norware.py
```

The integration verification covers startup and its three-second timeout, temporary demo ID/read-only mode, staff login/password reset, seven warehouses and waste sections, product and shelf barcode lookup, matching and capacity rejection, shelf availability, staff-confirmed placement, transfer, dispatch, quarantine exclusion/release, audit records, and assistant fallback.

## Files

- `norware_app.py`, `norware_web.html` — local server, SQLite data layer, application screens, barcode support, and assistant.
- `waste_config.json` — example local waste streams, storage profiles, restrictions, and role permissions.
- `verify_norware.py` — isolated end-to-end workflow checks.
- `schema.sql`, `load.sql`, `generate_data.py`, `validate_data.py` — PostgreSQL schema and reproducible 10,000-row CSV data tools.
- `products.csv`, `taxonomy.csv`, `warehouses.csv`, `locations.csv`, `inventory.csv`, `data_sources.csv` — import-ready dataset.
