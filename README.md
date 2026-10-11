# Smart Slots Warehouse Operations

Smart Slots is a local warehouse operations demo. The active app uses **React**, **Tailwind CSS**, **TanStack Start**, and **SQLite**. It supports inventory receiving, product search, shelf-fit suggestions, staff-confirmed allocation, warehouse capacity views, stock movements, guided transfers, and an audit log.

The app is designed for local demonstration and development, not public or production deployment.

## Run the app

Requirements: Node.js 20+ and npm.

From this project directory:

```bash
npm install
npm run dev
```

Open the local URL printed by Vite, usually:

```text
http://127.0.0.1:5173
```

The first start initializes `norware.sqlite3` and imports the sample catalog and warehouse locations. Existing SQLite data is retained. To use a separate database, set `NORWARE_DB_PATH` before starting the server.

To create and run a production build:

```bash
npm run build
npm start
```

## Sign in

- **Staff demo:** username `staff.demo`, temporary password `123`. The first staff sign-in requires a new password of at least 12 characters.
- **Local demo:** choose **Continue without login**. This uses a temporary copy of the SQLite database and is discarded when the server exits. Demo sessions cannot confirm stock allocations.

To configure the initial staff user on a new database, set `NORWARE_DEMO_USER` and `NORWARE_DEMO_PASSWORD` before first start. Existing password hashes are not replaced by changing these variables.

## Main workflows

### Receive and allocate a product

1. Open **Products** and select **Receive product**.
2. Enter a unique product ID, SKU, barcode, product name, category, storage profile, dimensions, weight, quantity, and lot code. Add an expiry date if applicable.
3. Select **Receive & save lot**. The lot appears as awaiting allocation.
4. Select **Suggest** beside that lot. Recommendations are ranked by fit and explain the checks passed.
5. As staff, select **Confirm allocation**. Demo sessions can inspect suggestions but cannot finalize placement.

The allocator applies hard checks for storage profile, category coverage, dimensions, available volume and weight, shelf availability, and restricted-role permissions. A mismatch is rejected rather than overridden.

### Transfers and movements

- **Transfers** moves stock from an existing shelf through an in-transit state and requires confirmation at the destination shelf. The destination is revalidated at receipt.
- **Movements** records dispatch, quarantine, quarantine release, and disposal actions.
- **Locations** shows shelf profiles and capacity. Managers and admins can add a location.
- **Audit log** records inventory actions.
- **Assistant** provides deterministic, rule-based workflow and allocation guidance; it does not perform actions.

### Storage profile examples

| Profile | Example use |
| --- | --- |
| `AMBIENT` | General dry goods |
| `CHILLED` | Chilled goods |
| `FROZEN` | Frozen stock |
| `DRY_SECURE` | Secure dry storage |
| `HAZMAT` | Configured hazardous-material storage |
| `CONTROLLED` | Controlled storage |
| `WASTE` | Configured waste streams |

Waste streams, restrictions, storage profiles, and allowed roles are configured in [waste_config.json](./waste_config.json). Review local policies before using or changing these examples.

## Configuration

The app listens on loopback in development. Common environment variables:

```bash
export NORWARE_DB_PATH=/path/to/norware.sqlite3
export NORWARE_DEMO_USER=staff.demo
export NORWARE_DEMO_PASSWORD=use-a-new-temporary-password
export NORWARE_WASTE_CONFIG=/path/to/waste_config.json
```

The assistant provides local deterministic responses and does not require an API key.

## Sample data and PostgreSQL deliverable

The application uses the SQLite database for live local workflows. The CSV catalog contains synthetic generated examples; it is not real supplier inventory. The generated CSV data can be rebuilt and checked with:

```bash
python3 generate_data.py
python3 validate_data.py
```

The repository also includes a separate PostgreSQL dataset deliverable. With PostgreSQL 14+ and a database created:

```bash
psql -d YOUR_DATABASE -f schema.sql
psql -d YOUR_DATABASE -f load.sql
```

This PostgreSQL sample deliverable is not the database connection used by the TanStack app.

## Dataset tooling and verification

- `generate_data.py` regenerates the deterministic sample CSV data.
- `validate_data.py` validates the generated dataset.
- `npm run typecheck` checks the TypeScript app.
- `npm run build` validates and builds the TanStack Start app.

## Limitations

- This is a local, single-process demo. Keep it bound to loopback; do not expose it to a shared or public network.
- Sample products, dimensions, capacity, waste-stream policies, and safety attributes are synthetic examples. Validate all operational information with qualified staff.
- Waste configuration does not certify regulatory classification or safe handling.
- The app does not currently provide production deployment controls, TLS, multi-instance session storage, backups, or a production permissions administration workflow.
