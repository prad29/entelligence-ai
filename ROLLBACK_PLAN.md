# Rollback Plan — `stage` → `main` promotion (Mode B / Vespa / claude-sandbox)

This covers how to revert if the `stage`→`main` merge, the resulting
CodePipeline deploy, or the EC2 instance resize goes badly, so `main`/prod
can be restored to its current known-good state.

**Key fact: CodeDeploy has no auto-rollback configured**
(`aws deploy get-deployment-group --application-name amenity-app
--deployment-group-name amenity-prod` → `autoRollbackConfiguration: null`).
A failed deploy stays failed. Every recovery path below is manual.

---

## Quick reference (known-good state, captured before this migration)

| Item | Value |
|---|---|
| `main` HEAD commit | `998c1ef` |
| `stage` HEAD commit (being merged) | `1c51f78` |
| Commits `stage` has that `main` doesn't | 97 |
| EC2 instance | `i-08750418333070cfa` (tag `amenity-app`), type `t4g.medium`, AZ `us-east-1a` |
| Root EBS volume | `vol-053f0ef69a3f68b17` (20GB, `/dev/sda1`) |
| Vespa data EBS volume | `vol-0954103ef05531f9a` (50GB, mounted at `/data/vespa`) |
| RDS instance | `amenity-db`, `db.t4g.micro`, Postgres 16.13 |
| Last migration on `main` | `2153535df54c_initial_tables` (only one) |
| `main`'s `docker-compose.prod.yml` | `redis` + `backend` + `frontend` only |
| `main`'s `BEDROCK_MODEL_ID` (in `setup_env.sh`) | `mistral.mistral-large-2407-v1:0` (stale/retired — see note below) |
| Last successful CodeDeploy deployment (pre-migration) | `d-XKHRUPXHK`, succeeded 2026-07-10T21:36:29+05:30 |
| RDS automated snapshots available | Daily, 2026-07-08 through 2026-07-16 (9 total) |

---

## Pre-flight safety net (do before merging)

Run these before merging `stage`→`main`, so a rollback has fresh, exact
restore points instead of relying only on daily automated snapshots.

```bash
# 1. Snapshot both EBS volumes
aws ec2 create-snapshot --volume-id vol-053f0ef69a3f68b17 \
  --description "pre-mode-b-merge root volume backup" --region us-east-1

aws ec2 create-snapshot --volume-id vol-0954103ef05531f9a \
  --description "pre-mode-b-merge vespa data volume backup" --region us-east-1

# 2. Trigger a fresh RDS snapshot (don't rely only on the nightly one)
aws rds create-db-snapshot \
  --db-instance-identifier amenity-db \
  --db-snapshot-identifier amenity-db-pre-mode-b-merge \
  --region us-east-1
```

**Also check for a real (if unlikely) data-loss edge case before merging:**
one of the 6 new migrations (`a1b2c3d4e5f6_drop_circuitoverride_table`) drops
the `circuitoverride` table. Tracing it back to commit `07df881`, this was a
deliberate cleanup — a bug fix moved circuit-specific mapping data from
`circuitoverride` into `AmenityMapping.circuit_name`, and no code on `stage`
references `CircuitOverride` anymore. **This is very likely safe** — but only
if every row currently in `main`'s live `circuitoverride` table was captured
by that re-seed. If anyone edited `circuitoverride` directly in prod after
`stage` diverged from `07df881`, those specific rows would be lost (the
migration's `downgrade()` only recreates an empty table, it can't restore
data). Recommend a quick read-only check against prod RDS before merging:

```sql
SELECT COUNT(*) FROM circuitoverride;
-- If 0, or if every row matches something already in amenitymapping
-- (compare keyword/circuit_name/screen_format), the migration is confirmed safe.
```

If that check turns up unexpected rows, hold off on merging until reviewed —
recovery for that specific case is the RDS snapshot from this section, not
`alembic downgrade`.

---

## Scenario A — Pipeline Test or Build stage fails

No prod impact. The Deploy stage never runs, so `main`'s running containers
are untouched. Fix the issue on `stage`, push again (or push directly to
`main` if already merged and re-running).

---

## Scenario B — Deploy stage starts but fails partway

E.g. new services (`vespa`, `claude-sandbox`, celery workers) never become
healthy, or `validate_service.sh` fails its health check loop. CodeDeploy
marks the deployment failed but does **not** revert what's already running.

SSH into the instance and manually restore the last-good compose shape:

```bash
cd /app

# Stop whatever partial state was left behind
docker compose -f docker-compose.prod.yml down --remove-orphans

# Check out main's PREVIOUS (pre-merge) docker-compose.prod.yml — redis+backend+frontend only
git -C /path/to/checked-out/repo show 998c1ef:docker-compose.prod.yml > docker-compose.prod.yml

# Re-pull the last-good image tag (from the last successful deployment, d-XKHRUPXHK)
# and re-run the equivalent of start_containers.sh against the reverted compose file.
```

If `/app` on the instance isn't a full git checkout (likely — CodeDeploy just
copies `docker-compose.prod.yml` + `codedeploy/**` + `appspec.yml` per
`appspec.yml`'s `files:` block), the simplest path is: fetch `main`'s
pre-merge version of `docker-compose.prod.yml` from GitHub directly and
overwrite `/app/docker-compose.prod.yml` with it, then re-run
`start_containers.sh` manually.

---

## Scenario C — Deploy succeeds but the app is broken or misbehaving

E.g. Bedrock calls fail, batch matching errors out, elevated error rates
discovered after the fact — not caught by `validate_service.sh`'s narrow
health check.

**Fastest recovery — redeploy the last-good image tags directly**, without
waiting for a new pipeline run:
```bash
# On the instance
cd /app
echo "<last-good 8-char tag from deployment d-XKHRUPXHK>" > image_tag.txt
# Re-fetch main's pre-merge docker-compose.prod.yml (see Scenario B), then:
bash codedeploy/scripts/start_containers.sh
```

**Proper git-level fix** (do this regardless, so the next pipeline run
doesn't reintroduce the same issue):
```bash
git revert -m 1 <merge-commit-sha>   # preferred — keeps history, safe default
# OR, only with explicit confirmation (rewrites main's history):
# git reset --hard 998c1ef && git push --force origin main
```
Prefer `git revert` unless there's a specific reason to force-push — resetting
`main` and force-pushing rewrites shared history and needs explicit sign-off
per this project's git safety rules.

---

## Scenario D — Migrations need reverting

Run against RDS from the instance (or anywhere with `DATABASE_URL` pointed at
`amenity-db`):

```bash
cd /app  # or wherever the backend code + alembic.ini live
alembic downgrade 2153535df54c
```

5 of the 6 new migrations are purely additive and downgrade cleanly with
zero data loss:
- `b2c3d4e5f6a1_add_movie_format_tables` — new tables only
- `c3d4e5f6a1b2_add_audit_mode_to_jobs` — `ADD COLUMN`/`DROP COLUMN` on
  `detectionjob`/`movieformatjob`, new nullable-with-default column, safe
- `d4e5f6a1b2c3_add_movie_master_tables` — new tables only
- `e5f6a1b2c3d4_add_movietitlebatchjob_table` — new table only
- `f6a1b2c3d4e5_add_trgm_search_support` — extensions + index only, no tables

**One exception**: `a1b2c3d4e5f6_drop_circuitoverride_table`'s `downgrade()`
only recreates an empty `circuitoverride` table — it cannot restore dropped
rows. If the pre-flight check above found prod-only data, recovery is
restoring the RDS snapshot taken in the pre-flight step (new RDS instance
from snapshot, then repoint `DATABASE_URL`), not `alembic downgrade`.

---

## Scenario E — EC2 resize (t4g.medium → r7g.large) goes wrong

E.g. the instance doesn't boot cleanly on the new type, or something else
breaks during that maintenance window.

```bash
# Stop the instance
aws ec2 stop-instances --instance-ids i-08750418333070cfa --region us-east-1

# Revert the instance type
aws ec2 modify-instance-attribute --instance-id i-08750418333070cfa \
  --instance-type t4g.medium --region us-east-1

# Start it again
aws ec2 start-instances --instance-ids i-08750418333070cfa --region us-east-1
```

Both EBS volumes (root + Vespa data) stay attached across this — no data
impact in either direction, since a type change doesn't touch volume
contents. If the instance fails to come up at all, restore from the
pre-flight EBS snapshots onto a fresh volume/instance as a last resort.

---

## What NOT to bother reverting

Everything below was added **without modifying or removing anything that
already existed** on `main`'s infrastructure — safe to leave in place even
after a full rollback of the git/deploy side. Removing them is optional
cleanup, not required for `main`/prod to work again:

- IAM inline policies: `BatchStorageS3Access`, `SerperSecretAccess` on
  `amenity-ec2-role`; the extended `ECRPull` and `CodeBuildPolicy` statements
  (they just added an extra repo ARN, nothing removed)
- ECR repository `amenity-claude-sandbox`
- SSM parameter `/amenity/ecr_claude_sandbox`
- CodeBuild project `amenity-build-claude-sandbox`
- The `BuildClaudeSandbox` action in the pipeline's Build stage
- EBS volume `vol-0954103ef05531f9a` and its mount at `/data/vespa`
- Secrets Manager secret `amenity/serper-api-key`

---

## Known pre-existing issue (unrelated to this migration, found along the way)

`setup_env.sh` on **both** `main` and `stage` (before this session's fix)
hardcoded `BEDROCK_MODEL_ID=mistral.mistral-large-2407-v1:0`, a model that's
no longer available on Bedrock. A prior hotfix (PR #36) updated
`config.py`'s Python default and `.env.example` to
`mistral.mistral-large-3-675b-instruct`, but never this script — which
overrides the Python default via `env_file`. This means **AI Layer 2
(Bedrock Mistral detection) has likely been broken in prod already**,
independent of this migration. This session's changes to `setup_env.sh`
(on `stage`, not yet on `main`) fix it. Worth confirming this is actually
the case in prod once diagnosing any Bedrock-related issue during rollback
triage — it may explain symptoms unrelated to anything in this migration.
