#!/bin/bash
# Tear down everything deploy.sh created in temp-account. Safe to re-run.
set -uo pipefail
cd "$(dirname "$0")"
source ./config.env

# Set DEPLOY_ACCOUNT_ID to enable the wrong-account guard (never hardcode it).
EXPECTED_ACCOUNT="${DEPLOY_ACCOUNT_ID:-}"
aws() { command aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }
say() { printf '\n\033[1;33m== %s\033[0m\n' "$*"; }

ACCT=$(aws sts get-caller-identity --query Account --output text)
if [ -n "$EXPECTED_ACCOUNT" ] && [ "$ACCT" != "$EXPECTED_ACCOUNT" ]; then
  echo "REFUSING: active account $ACCT does not match DEPLOY_ACCOUNT_ID"; exit 1
fi

[ -f deploy-state.env ] && source ./deploy-state.env

say "Disabling + deleting CloudFront distribution"
if [ -n "${CLOUDFRONT_DIST_ID:-}" ]; then
  # CloudFront must be disabled, fully propagated, then deleted (needs ETag).
  ETAG=$(aws cloudfront get-distribution-config --id "$CLOUDFRONT_DIST_ID" --query ETag --output text 2>/dev/null || echo "")
  if [ -n "$ETAG" ]; then
    aws cloudfront get-distribution-config --id "$CLOUDFRONT_DIST_ID" \
      --query DistributionConfig --output json > /tmp/cf.json 2>/dev/null || true
    python3 -c "import json;d=json.load(open('/tmp/cf.json'));d['Enabled']=False;json.dump(d,open('/tmp/cf.json','w'))" 2>/dev/null || true
    aws cloudfront update-distribution --id "$CLOUDFRONT_DIST_ID" \
      --distribution-config file:///tmp/cf.json --if-match "$ETAG" >/dev/null 2>&1 || true
    echo "disable requested; waiting for deployed state (can take ~10-15 min)..."
    aws cloudfront wait distribution-deployed --id "$CLOUDFRONT_DIST_ID" 2>/dev/null || true
    NEWTAG=$(aws cloudfront get-distribution-config --id "$CLOUDFRONT_DIST_ID" --query ETag --output text 2>/dev/null || echo "")
    aws cloudfront delete-distribution --id "$CLOUDFRONT_DIST_ID" --if-match "$NEWTAG" 2>/dev/null \
      && echo "deleted $CLOUDFRONT_DIST_ID" || echo "delete CF manually if still 'InProgress'"
  fi
fi

say "Terminating EC2 instance"
if [ -n "${INSTANCE_ID:-}" ]; then
  aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null 2>&1 || true
  aws ec2 wait instance-terminated --instance-ids "$INSTANCE_ID" 2>/dev/null || true
  echo "terminated $INSTANCE_ID"
fi

say "Deleting security group"
[ -n "${SG_ID:-}" ] && aws ec2 delete-security-group --group-id "$SG_ID" 2>/dev/null && echo "deleted $SG_ID" || echo "sg skip"

say "Deleting IAM instance profile + role"
aws iam remove-role-from-instance-profile --instance-profile-name "$INSTANCE_PROFILE" --role-name "$ROLE_NAME" 2>/dev/null || true
aws iam delete-instance-profile --instance-profile-name "$INSTANCE_PROFILE" 2>/dev/null || true
aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "${STACK}-perms" 2>/dev/null || true
aws iam delete-role --role-name "$ROLE_NAME" 2>/dev/null || true
echo "iam removed"

say "Emptying + deleting S3 bucket"
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3 rm "s3://$BUCKET" --recursive >/dev/null 2>&1 || true
  aws s3api delete-bucket --bucket "$BUCKET" 2>/dev/null && echo "deleted $BUCKET" || echo "bucket delete failed"
fi

say "Teardown complete"
rm -f deploy-state.env
