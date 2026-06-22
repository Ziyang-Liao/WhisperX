#!/bin/bash
# Put CloudFront in front of the EC2 origin and lock the instance so it only
# accepts traffic from CloudFront. Idempotent-ish; safe to read before running.
#
# Layers of protection (both required — neither is sufficient alone):
#   1. Security group: ingress on APP_PORT only from CloudFront's managed
#      origin-facing prefix list (pl-...). Blocks direct public TCP.
#   2. Origin secret header: CloudFront injects X-Origin-Secret; the app rejects
#      requests without it (the prefix list is shared across all CF customers).
#
# HARD RULE: temp-account only (account id asserted).
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env
[ -f deploy-state.env ] && source ./deploy-state.env

EXPECTED_ACCOUNT="ACCOUNT_ID_REDACTED"
CF_PREFIX_LIST="pl-3b927c52"   # com.amazonaws.global.cloudfront.origin-facing (us-east-1)
say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
aws() { command aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

ACCT=$(aws sts get-caller-identity --query Account --output text)
[ "$ACCT" = "$EXPECTED_ACCOUNT" ] || { echo "REFUSING: account $ACCT != $EXPECTED_ACCOUNT"; exit 1; }

: "${INSTANCE_ID:?run deploy.sh first (need INSTANCE_ID in deploy-state.env)}"
: "${SG_ID:?need SG_ID in deploy-state.env}"

say "Resolving instance public DNS (CloudFront origin)"
ORIGIN_DNS=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicDnsName' --output text)
echo "origin: $ORIGIN_DNS:$APP_PORT"

say "Generating origin secret"
ORIGIN_SECRET=$(openssl rand -hex 24)

say "Creating CloudFront distribution"
DIST_CONFIG=$(cat <<JSON
{
  "CallerReference": "${STACK}-$(date +%s 2>/dev/null || echo ref)",
  "Comment": "${STACK} front door",
  "Enabled": true,
  "Origins": {
    "Quantity": 1,
    "Items": [{
      "Id": "ec2-origin",
      "DomainName": "${ORIGIN_DNS}",
      "CustomOriginConfig": {
        "HTTPPort": ${APP_PORT},
        "HTTPSPort": 443,
        "OriginProtocolPolicy": "http-only",
        "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
        "OriginReadTimeout": 60,
        "OriginKeepaliveTimeout": 5
      },
      "CustomHeaders": {
        "Quantity": 1,
        "Items": [{"HeaderName": "X-Origin-Secret", "HeaderValue": "${ORIGIN_SECRET}"}]
      }
    }]
  },
  "DefaultCacheBehavior": {
    "TargetOriginId": "ec2-origin",
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": {
      "Quantity": 7,
      "Items": ["GET","HEAD","OPTIONS","PUT","POST","PATCH","DELETE"],
      "CachedMethods": {"Quantity": 2, "Items": ["GET","HEAD"]}
    },
    "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
    "OriginRequestPolicyId": "216adef6-5c7f-47e4-b989-5492eafa07d3"
  },
  "PriceClass": "PriceClass_100"
}
JSON
)
# CachePolicyId = CachingDisabled; OriginRequestPolicyId = AllViewer (forwards
# all headers incl. our secret + bodies). These are AWS-managed policy ids.
OUT=$(aws cloudfront create-distribution --distribution-config "$DIST_CONFIG")
DIST_ID=$(echo "$OUT" | python3 -c 'import json,sys;print(json.load(sys.stdin)["Distribution"]["Id"])')
DIST_DOMAIN=$(echo "$OUT" | python3 -c 'import json,sys;print(json.load(sys.stdin)["Distribution"]["DomainName"])')
echo "distribution: $DIST_ID  ($DIST_DOMAIN)"

say "Pushing ORIGIN_SECRET to the instance + restarting app"
B64SEC=$(printf '%s' "$ORIGIN_SECRET" | base64)
CMD=$(aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name "AWS-RunShellScript" \
  --parameters "commands=[\"grep -q ORIGIN_SECRET /etc/whisperx.env || echo ORIGIN_SECRET=\$(echo $B64SEC | base64 -d) >> /etc/whisperx.env\",\"echo CORS_ALLOW_ORIGIN=https://$DIST_DOMAIN >> /etc/whisperx.env\",\"systemctl restart whisperx\"]" \
  --query 'Command.CommandId' --output text)
echo "ssm command: $CMD (give it ~10s)"
sleep 12

say "Locking security group: allow APP_PORT only from CloudFront prefix list"
# Remove the open-to-world rule, add the CloudFront prefix-list rule.
aws ec2 revoke-security-group-ingress --group-id "$SG_ID" \
  --protocol tcp --port "$APP_PORT" --cidr 0.0.0.0/0 2>/dev/null || echo "(no 0.0.0.0/0 rule to remove)"
aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
  --ip-permissions "IpProtocol=tcp,FromPort=${APP_PORT},ToPort=${APP_PORT},PrefixListIds=[{PrefixListId=${CF_PREFIX_LIST}}]" \
  2>/dev/null || echo "(prefix-list rule may already exist)"

say "Setting S3 CORS so the browser can presigned-PUT from the CloudFront origin"
cat > /tmp/s3-cors.json <<JSON
{
  "CORSRules": [{
    "AllowedHeaders": ["*"],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedOrigins": ["https://${DIST_DOMAIN}"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3000
  }]
}
JSON
aws s3api put-bucket-cors --bucket "$BUCKET" --cors-configuration file:///tmp/s3-cors.json \
  && echo "S3 CORS set for https://${DIST_DOMAIN}"

cat >> deploy-state.env <<EOF
# --- front door (front-door.sh) ---
CLOUDFRONT_DIST_ID=$DIST_ID
CLOUDFRONT_DOMAIN=$DIST_DOMAIN
ORIGIN_SECRET=$ORIGIN_SECRET
EOF

say "DONE"
echo "CloudFront:  https://$DIST_DOMAIN   (deploying — 5-10 min to propagate)"
echo "Origin:      $ORIGIN_DNS:$APP_PORT (now reachable ONLY via CloudFront)"
echo "SG:          $SG_ID locked to prefix list $CF_PREFIX_LIST"
echo "Direct hit to the instance IP will now hang/timeout (by design)."
