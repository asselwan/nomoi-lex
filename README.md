# Lex — Presubmission Claim Validation

Streamlit UI for validating UAE inpatient DRG claims before payer submission.

## Local Development

```bash
# Install dependencies (requires uv)
uv pip install -e ".[dev]"

# Run the app
streamlit run app.py

# Run tests
python -m pytest tests/ -v
```

The validator engine is expected as a sibling directory at `../ip-claim-validator` and installed via editable reference in `pyproject.toml`.

## Deployment

### Validator Engine Vendoring

The validator engine lives in a separate repo (`ip-claim-validator`). For Docker builds, it is vendored into `vendor/ip-claim-validator/` so the image build context is self-contained.

**Re-vendor after validator changes:**

```bash
./scripts/vendor-validator.sh            # uses ../ip-claim-validator by default
./scripts/vendor-validator.sh /path/to/ip-claim-validator   # or specify path
```

The vendored copy is committed to git so Coolify (and any CI) can build without access to the sibling repo.

### Build Locally

```bash
# Vendor the validator (if not already done)
./scripts/vendor-validator.sh

# Build the image
docker build -t lex:test .
```

### Run via Docker Compose

Create a `.env` file (optional — the app runs without Supabase credentials):

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
```

```bash
docker compose up        # foreground
docker compose up -d     # detached
```

The app will be available at `http://localhost:8501`.

### Deploy to Coolify (lex.nomoi.ai)

1. **Connect the repo** — In Coolify, create a new service and point it at this repository. Set the build pack to **Dockerfile**.

2. **Set environment variables** in the Coolify service settings:

   | Variable | Required | Description |
   |----------|----------|-------------|
   | `LEX_ENV` | No | Set automatically to `production` in compose |
   | `SUPABASE_URL` | No | Supabase project URL for audit logging |
   | `SUPABASE_SERVICE_KEY` | No | Supabase service-role key for audit logging |

   The app starts without Supabase credentials — audit logging is skipped with a warning.

3. **Domain** — Coolify picks up the label `coolify.fqdn=lex.nomoi.ai` from `docker-compose.yml`. Verify the domain is configured in Coolify's Traefik settings.

4. **Deploy** — Push to `main`. Coolify will build and deploy automatically.

### Apply SQL Migrations (First Deploy)

Before the first deploy with audit logging enabled, run the two migrations against the `nomoi-core` Supabase project:

```bash
# Connect to Supabase SQL editor or use psql
psql "$SUPABASE_DB_URL" -f src/lex/audit/migrations/001_lex_validation_runs.sql
psql "$SUPABASE_DB_URL" -f src/lex/audit/migrations/002_lex_claim_outcomes.sql
```

These create the `lex` schema with `validation_runs` and `claim_outcomes` tables. No PHI is stored — only hashed claim IDs, aggregate counts, and rule IDs.
