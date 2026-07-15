#!/usr/bin/env bash
#
# mobius-user — Cloud Run deploy driver.
#
# Usage:
#     scripts/deploy.sh <env>                # build image + deploy + smoke
#     scripts/deploy.sh <env> --dry-run      # print commands, don't run
#     scripts/deploy.sh <env> --skip-build   # redeploy previous image
#     scripts/deploy.sh <env> --skip-smoke   # skip post-deploy smoke probe
#
# <env> is a label matching deploy/<env>.env (dev | prod).
#
# Patterned after mobius-chat/scripts/deploy.sh, simplified because
# mobius-user doesn't vendor sibling repos.

set -euo pipefail

ENV_LABEL="${1:-}"
if [[ -z "${ENV_LABEL}" ]]; then
    echo "usage: $0 <env> [--dry-run] [--skip-build] [--skip-smoke]" >&2
    exit 64
fi

DRY_RUN=0
SKIP_BUILD=0
SKIP_SMOKE=0
shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)    DRY_RUN=1; shift ;;
        --skip-build) SKIP_BUILD=1; shift ;;
        --skip-smoke) SKIP_SMOKE=1; shift ;;
        *) echo "unknown flag: $1" >&2; exit 64 ;;
    esac
done

# ── Paths ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

ENV_FILE="${REPO_DIR}/deploy/${ENV_LABEL}.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "error: config file not found: ${ENV_FILE}" >&2
    exit 66
fi

# ── Load config ─────────────────────────────────────────────────────
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

for required in GCP_PROJECT GCP_REGION SERVICE_NAME SERVICE_ACCOUNT \
                IMAGE_BASE CLOUDSQL_INSTANCE RUN_MEMORY RUN_CPU \
                RUN_CONCURRENCY RUN_TIMEOUT RUN_MIN_INSTANCES \
                RUN_MAX_INSTANCES; do
    if [[ -z "${!required:-}" ]]; then
        echo "error: ${required} missing from ${ENV_FILE}" >&2
        exit 65
    fi
done

# ── Image tag ───────────────────────────────────────────────────────
GIT_SHA="$(git -C "${REPO_DIR}" rev-parse --short=10 HEAD 2>/dev/null || echo nogit)"
BUILD_TS="$(date -u +%Y%m%d-%H%M%S)"
IMAGE_TAG="${IMAGE_BASE}:${BUILD_TS}-${GIT_SHA}"

GIT_BRANCH="$(git -C "${REPO_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
GIT_DIRTY="$(git -C "${REPO_DIR}" diff --quiet 2>/dev/null && git -C "${REPO_DIR}" diff --cached --quiet 2>/dev/null && echo clean || echo DIRTY)"
echo "▸ Deploying from HEAD: ${GIT_SHA}  (branch: ${GIT_BRANCH}, working tree: ${GIT_DIRTY})"
echo "▸ Repo path: ${REPO_DIR}"
if [[ "${GIT_DIRTY}" == "DIRTY" ]]; then
    echo "  warn: working tree has uncommitted changes — image will reflect committed HEAD only" >&2
fi

run() {
    echo "+ $*"
    if [[ "${DRY_RUN}" -eq 0 ]]; then
        "$@"
    fi
}

# Build a `^;^k=v;k=v;...` list for --set-env-vars / --set-secrets so
# values containing commas (CORS_ALLOW_ORIGINS) parse correctly.
csv_env() {
    printf '^;^%s' "$(printf '%s\n' "$@" | paste -sd';' -)"
}

# ── Build ───────────────────────────────────────────────────────────
if [[ "${SKIP_BUILD}" -eq 0 ]]; then
    echo "── Building ${IMAGE_TAG} ──"
    run gcloud builds submit "${REPO_DIR}" \
        --project="${GCP_PROJECT}" \
        --region="${GCP_REGION}" \
        --config="${REPO_DIR}/deploy/cloudbuild.yaml" \
        --ignore-file="${REPO_DIR}/deploy/.gcloudignore" \
        --substitutions="_IMAGE=${IMAGE_TAG},_IMAGE_BASE=${IMAGE_BASE}" \
        --timeout=30m \
        || {
            echo "error: gcloud builds submit failed. Check the build log URL above." >&2
            exit 70
        }
else
    IMAGE_TAG="$(gcloud artifacts docker images list \
        "${IMAGE_BASE}" --project="${GCP_PROJECT}" \
        --include-tags --format='value(IMAGE,TAGS)' \
        --sort-by="~UPDATE_TIME" --limit=1 | awk '{print $1":"$2}' | awk -F, '{print $1}')"
    if [[ -z "${IMAGE_TAG}" ]]; then
        echo "error: --skip-build set but no prior image found in ${IMAGE_BASE}" >&2
        exit 71
    fi
    echo "── Reusing previous image ${IMAGE_TAG} ──"
fi

# ── Env vars + secrets ──────────────────────────────────────────────
SET_ENV_VARS=(
    "USER_DATABASE_URL=${USER_DATABASE_URL}"
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES=${JWT_ACCESS_TOKEN_EXPIRE_MINUTES:-60}"
    "JWT_REFRESH_TOKEN_EXPIRE_DAYS=${JWT_REFRESH_TOKEN_EXPIRE_DAYS:-7}"
    "DEFAULT_TENANT_ID=${DEFAULT_TENANT_ID}"
    "DEFAULT_TENANT_NAME=${DEFAULT_TENANT_NAME}"
    "GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}"
    "MOBIUS_EMAIL_SKILL_URL=${MOBIUS_EMAIL_SKILL_URL:-}"
    "MOBIUS_ROSTER_URL=${MOBIUS_ROSTER_URL:-}"
    "MOBIUS_SET_PASSWORD_URL_BASE=${MOBIUS_SET_PASSWORD_URL_BASE:-}"
    "CORS_ALLOW_ORIGINS=${CORS_ALLOW_ORIGINS:-*}"
    "MOBIUS_USER_ADMIN_EMAILS=${MOBIUS_USER_ADMIN_EMAILS:-}"
)

# Secrets — Cloud Run mounts each as an env var. ``name:latest`` pins to
# whatever the current secret version is at deploy time.
SET_SECRETS=(
    "JWT_SECRET=jwt-secret:latest"
    "DB_PASSWORD=db-password:latest"
    "MOBIUS_USER_INTERNAL_KEY=mobius-user-internal-key:latest"
)

# ── Deploy ──────────────────────────────────────────────────────────
echo "── Deploying ${SERVICE_NAME} to ${GCP_PROJECT}/${GCP_REGION} ──"
DEPLOY_FLAGS=(
    --project="${GCP_PROJECT}"
    --region="${GCP_REGION}"
    --image="${IMAGE_TAG}"
    --service-account="${SERVICE_ACCOUNT}"
    --platform=managed
    --allow-unauthenticated
    --memory="${RUN_MEMORY}"
    --cpu="${RUN_CPU}"
    --concurrency="${RUN_CONCURRENCY}"
    --timeout="${RUN_TIMEOUT}"
    --min-instances="${RUN_MIN_INSTANCES}"
    --max-instances="${RUN_MAX_INSTANCES}"
    --port=8080
    --add-cloudsql-instances="${CLOUDSQL_INSTANCE}"
    --set-env-vars="$(csv_env "${SET_ENV_VARS[@]}")"
    --set-secrets="$(csv_env "${SET_SECRETS[@]}")"
    --execution-environment=gen2
)
if [[ -n "${RUN_VPC_CONNECTOR:-}" ]]; then
    DEPLOY_FLAGS+=(--vpc-connector="${RUN_VPC_CONNECTOR}")
fi
if [[ -n "${RUN_VPC_EGRESS:-}" ]]; then
    DEPLOY_FLAGS+=(--vpc-egress="${RUN_VPC_EGRESS}")
fi

run gcloud run deploy "${SERVICE_NAME}" "${DEPLOY_FLAGS[@]}"

echo
echo "✓ Deploy complete: ${IMAGE_TAG}"
SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" \
    --project="${GCP_PROJECT}" --region="${GCP_REGION}" \
    --format='value(status.url)' 2>/dev/null || echo '')"
if [[ -n "${SERVICE_URL}" ]]; then
    echo "  Service URL: ${SERVICE_URL}"
else
    echo "  Service URL: (not queryable in dry-run)"
fi
echo

# ── Post-deploy smoke ───────────────────────────────────────────────
if [[ "${DRY_RUN}" -eq 1 ]]; then
    echo "(dry-run: skipping post-deploy smoke)"
elif [[ "${SKIP_SMOKE}" -eq 1 ]]; then
    echo "⚠ --skip-smoke: post-deploy smoke bypassed."
elif [[ -z "${SERVICE_URL}" ]]; then
    echo "⚠ Could not resolve service URL; skipping post-deploy smoke."
else
    echo "── Running post-deploy smoke ──"
    fail=0
    probe() {
        local name="$1"; shift
        local expected="$1"; shift
        local code
        code="$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$@" || echo 000)"
        if [[ "${code}" == "${expected}" ]]; then
            echo "  ✓ ${name} → ${code}"
        else
            echo "  ✗ ${name} → ${code} (expected ${expected})"
            fail=1
        fi
    }
    probe "/health" 200 "${SERVICE_URL}/health"
    probe "/api/v1/public-config" 200 "${SERVICE_URL}/api/v1/public-config"
    probe "POST /api/v1/auth/google (no body)" 400 -X POST -H "Content-Type: application/json" -d '{}' "${SERVICE_URL}/api/v1/auth/google"
    probe "POST /api/v1/auth/check-email" 200 -X POST -H "Content-Type: application/json" -d '{"email":"smoke-probe@example.com"}' "${SERVICE_URL}/api/v1/auth/check-email"
    if [[ "${fail}" -ne 0 ]]; then
        echo "✗ Post-deploy smoke failed." >&2
        exit 72
    fi
    echo "── Smoke OK ──"
fi
