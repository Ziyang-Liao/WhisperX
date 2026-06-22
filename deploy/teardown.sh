#!/bin/bash
# Tear down everything deploy.sh created in temp-account. Safe to re-run.
set -uo pipefail
cd "$(dirname "$0")"
source ./config.env

EXPECTED_ACCOUNT="ACCOUNT_ID_REDACTED"
aws() { command aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }
say() { printf '\n\033[1;33m== %s\033[0m\n' "$*"; }

ACCT=$(aws sts get-caller-identity --query Account --output text)
[ "$ACCT" = "$EXPECTED_ACCOUNT" ] || { echo "REFUSING: account $ACCT is not $EXPECTED_ACCOUNT"; exit 1; }

[ -f deploy-state.env ] && source ./deploy-state.env

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
