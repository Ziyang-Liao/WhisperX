#!/bin/bash
# Deploy the WhisperX subtitle platform to the temp-account AWS account.
#
# HARD RULE: temp-account profile only. The script asserts the account id.
# Creates: S3 bucket, IAM role+instance-profile, security group, CPU EC2 instance.
# Re-runnable: existing resources are reused where detection is cheap.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env

EXPECTED_ACCOUNT="ACCOUNT_ID_REDACTED"
say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

aws() { command aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

say "Verifying account is temp-account ($EXPECTED_ACCOUNT)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
[ "$ACCT" = "$EXPECTED_ACCOUNT" ] || { echo "REFUSING: account $ACCT is not $EXPECTED_ACCOUNT"; exit 1; }
echo "OK: $ACCT"

say "Creating S3 bucket: $BUCKET"
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "bucket exists"
else
  # us-east-1 must NOT pass a LocationConstraint.
  aws s3api create-bucket --bucket "$BUCKET" >/dev/null
fi
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-tagging --bucket "$BUCKET" \
  --tagging "TagSet=[{Key=$TAG_KEY,Value=$TAG_VAL}]" || true
echo "bucket ready: $BUCKET"

say "Packaging and uploading app code"
TARBALL=$(mktemp /tmp/app.XXXXXX.tar.gz)
# Ship backend + docs only; exclude caches and local data.
tar -czf "$TARBALL" -C .. \
  --exclude='backend/data' --exclude='**/__pycache__' --exclude='**/.pytest_cache' \
  --exclude='**/.hypothesis' backend
aws s3 cp "$TARBALL" "s3://$BUCKET/deploy/app.tar.gz" >/dev/null
rm -f "$TARBALL"
echo "app uploaded to s3://$BUCKET/deploy/app.tar.gz"

say "Creating IAM role + instance profile: $ROLE_NAME"
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document file://iam-trust-policy.json \
    --tags Key=$TAG_KEY,Value=$TAG_VAL >/dev/null
fi
sed "s/__BUCKET__/$BUCKET/g" iam-permissions-policy.json.tmpl > /tmp/perms.json
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name "${STACK}-perms" --policy-document file:///tmp/perms.json
if ! aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$INSTANCE_PROFILE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$INSTANCE_PROFILE" --role-name "$ROLE_NAME"
fi
echo "iam ready: role=$ROLE_NAME profile=$INSTANCE_PROFILE"

say "Creating security group: $SG_NAME"
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SG_ID=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$SG_NAME \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID=$(aws ec2 create-security-group --group-name "$SG_NAME" \
    --description "WhisperX subtitle app" --vpc-id "$VPC" --query GroupId --output text)
  # Expose the API port. Restrict to your IP in production; open here for testing.
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
    --protocol tcp --port "$APP_PORT" --cidr 0.0.0.0/0 >/dev/null
fi
echo "sg ready: $SG_ID (vpc $VPC)"

say "Resolving latest Amazon Linux 2023 AMI"
AMI=$(aws ssm get-parameters \
  --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query 'Parameters[0].Value' --output text)
echo "ami: $AMI"

say "Rendering user-data"
sed -e "s/__BUCKET__/$BUCKET/g" -e "s/__REGION__/$AWS_REGION/g" \
    -e "s#__MODEL__#$BEDROCK_MODEL_ID#g" -e "s/__PORT__/$APP_PORT/g" \
    user-data.sh.tmpl > /tmp/user-data.sh

say "Launching EC2 instance ($INSTANCE_TYPE)"
INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "$AMI" --instance-type "$INSTANCE_TYPE" --count 1 \
  --iam-instance-profile Name="$INSTANCE_PROFILE" \
  --security-group-ids "$SG_ID" \
  --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=60,VolumeType=gp3}' \
  --user-data file:///tmp/user-data.sh \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME},{Key=$TAG_KEY,Value=$TAG_VAL}]" \
  --query 'Instances[0].InstanceId' --output text)
echo "instance: $INSTANCE_ID"

aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
PUBIP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

cat > deploy-state.env <<EOF
# Written by deploy.sh $(date -u). Used by teardown.sh.
BUCKET=$BUCKET
ROLE_NAME=$ROLE_NAME
INSTANCE_PROFILE=$INSTANCE_PROFILE
SG_ID=$SG_ID
INSTANCE_ID=$INSTANCE_ID
PUBLIC_IP=$PUBIP
APP_URL=http://$PUBIP:$APP_PORT
EOF

say "DONE"
echo "Instance:  $INSTANCE_ID ($INSTANCE_TYPE)"
echo "App URL:   http://$PUBIP:$APP_PORT  (health: /health, docs: /docs)"
echo "Bucket:    s3://$BUCKET"
echo "The instance is installing deps + the WhisperX model (~5-10 min)."
echo "State written to deploy/deploy-state.env  ->  run teardown.sh to remove everything."
