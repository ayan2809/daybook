#!/usr/bin/env bash
# Deploy Daybook (plan 12.4, DB-27): storage stack → Lambda zip → app stack → PWA → invalidate.
#   infra/deploy.sh [all|backend|frontend]
# Env: DAYBOOK_SCHEDULES=true (reminder + nightly schedules; needs scheduler rights on the deployer)
#      DAYBOOK_ALERT_EMAIL=you@example.com (AWS Budgets alert; needs budgets rights)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export AWS_REGION="${AWS_REGION:-ap-south-1}" AWS_DEFAULT_REGION="${AWS_REGION:-ap-south-1}"
WHAT="${1:-all}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
DATA="daybook-data-$ACCOUNT"
WEB="daybook-web-$ACCOUNT"
step() { printf '\n==> %s\n' "$*"; }
out() {
  aws cloudformation describe-stacks --stack-name daybook \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

deploy_storage() {
  step "storage stack (S3 data + web buckets)"
  aws cloudformation deploy --stack-name daybook-storage --template-file infra/storage.yaml \
    --no-fail-on-empty-changeset
}

check_secrets() {
  local bad
  bad="$(aws ssm get-parameters --names /daybook/gemini_api_key /daybook/elevenlabs_api_key \
    /daybook/session_signing_key /daybook/origin_verify --query 'InvalidParameters' --output text)"
  if [ -n "$bad" ] && [ "$bad" != "None" ]; then
    echo "missing SSM parameters: $bad. Run: make secrets" >&2
    exit 1
  fi
}

deploy_backend() {
  check_secrets
  step "Lambda zip"
  local zip sha key ov
  zip="$(scripts/build_lambda_zip.sh)"
  sha="$(git rev-parse --short HEAD)"
  git diff --quiet HEAD -- backend data/seed || sha="$sha-dirty-$(date +%s)"
  key="artifacts/$sha.zip"
  aws s3 cp "$zip" "s3://$DATA/$key" --only-show-errors
  step "app stack (Lambda, Function URL, CloudFront, schedules=${DAYBOOK_SCHEDULES:-false})"
  ov="$(aws ssm get-parameter --name /daybook/origin_verify --with-decryption \
    --query Parameter.Value --output text)"
  aws cloudformation deploy --stack-name daybook --template-file infra/daybook.yaml \
    --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset \
    --parameter-overrides "CodeKey=$key" "OriginVerify=$ov" \
    "EnableSchedules=${DAYBOOK_SCHEDULES:-false}" "AlertEmail=${DAYBOOK_ALERT_EMAIL:-}"
  local site
  site="$(out SiteUrl)"
  if [ "$(aws ssm get-parameter --name /daybook/site_url --query Parameter.Value --output text 2>/dev/null)" != "$site" ]; then
    aws ssm put-parameter --name /daybook/site_url --type String --value "$site" --overwrite >/dev/null
    echo "site_url → $site (read at the next cold start)"
  fi
}

deploy_frontend() {
  step "PWA build + upload"
  (cd frontend && npm run build --silent)
  aws s3 sync frontend/dist/assets "s3://$WEB/assets" --delete --only-show-errors \
    --cache-control "public,max-age=31536000,immutable"
  aws s3 sync frontend/dist "s3://$WEB" --delete --only-show-errors --exclude "assets/*" \
    --exclude index.html --exclude sw.js --exclude manifest.webmanifest \
    --cache-control "public,max-age=86400"
  for f in index.html sw.js manifest.webmanifest; do
    aws s3 cp "frontend/dist/$f" "s3://$WEB/$f" --only-show-errors --cache-control "no-cache"
  done
  local dist
  dist="$(out DistributionId)"
  aws cloudfront create-invalidation --distribution-id "$dist" --paths "/*" \
    --query Invalidation.Id --output text >/dev/null
}

smoke() {
  local site
  site="$(out SiteUrl)"
  step "smoke: $site"
  curl -fsS --max-time 30 "$site/api/health" && echo
  curl -fsS --max-time 15 "$site/" | grep -q 'id="root"' && echo "PWA OK"
}

case "$WHAT" in
  all) deploy_storage; deploy_backend; deploy_frontend; smoke ;;
  backend) deploy_storage; deploy_backend; smoke ;;
  frontend) deploy_frontend; smoke ;;
  *) echo "usage: infra/deploy.sh [all|backend|frontend]" >&2; exit 2 ;;
esac
