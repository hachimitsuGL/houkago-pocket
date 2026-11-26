#!/usr/bin/env bash
# Azure Cloud Shell (Bash), Azure public cloud.
set -euo pipefail
cd -- "$(dirname -- "$0")"

die() { printf '%s\n' "$*" >&2; exit 1; }
usage() {
  cat <<'USAGE'
Set APP to a unique name for this assignment and LOCATION to an allowed Azure region.
  bash deploy.sh web      Create the Linux plan/web app and deploy the current source.
  bash deploy.sh storage  Add Table Storage + a private Blob container (metered).
  bash deploy.sh ci       Link a public GitHub repo to this web app (requires REPO=owner/name).
Optional: RG (defaults to rg-$APP), SKU (F1 by default; B1 is paid).
USAGE
}
mode=${1:-}
case "$mode" in web|storage|ci) ;; *) usage; exit 2 ;; esac
: "${APP:?Set APP to your project web app name first.}"
: "${LOCATION:?Set LOCATION to your allowed Azure region first.}"
[[ "$APP" =~ ^[a-z][a-z0-9-]{1,38}[a-z0-9]$ ]] || die "Use 3–40 lowercase letters, numbers or hyphens for APP."
rg=${RG:-rg-$APP}
plan="plan-$APP"
sku=${SKU:-F1}
[[ "$sku" == F1 || "$sku" == B1 ]] || die "Choose SKU=F1 (free) or SKU=B1 (paid)."

sub=$(az account show --query id -o tsv)
echo "Subscription=$sub  Resource group=$rg  App=$APP  Region=$LOCATION  Command=$mode"

# Only re-use resources created for this project's APP name.
resource_state() {
  local kind=$1 name=$2 item
  item=$(az resource list -g "$rg" --resource-type "$kind" --name "$name" --query '[0]' -o json) || return 1
  if [[ -z "$item" || "$item" == null ]]; then
    echo new
  elif jq -e --arg app "$APP" '.tags.sk21Pocket == $app' <<< "$item" >/dev/null; then
    echo existing
  else
    die "An unrelated resource named $name exists. Choose a different project name."
  fi
}

if [[ "$mode" == web ]]; then
  test -f app.py && test -f requirements.txt && test -f .deployment && test -f static/index.html || die "Extract the complete project ZIP first."
  runtimes=$(az webapp list-runtimes --os linux -o json)
  if ! jq -e 'any(.[]; if type == "object" then
      ((.runtime // "" | ascii_upcase) == "PYTHON" and (.version | tostring) == "3.12") or
      ((.config // "" | ascii_upcase | gsub("[|]"; ":")) == "PYTHON:3.12")
    else (ascii_upcase | gsub("[|]"; ":")) == "PYTHON:3.12" end)' <<< "$runtimes" >/dev/null; then
    die "Python 3.12 is unavailable in the runtime list. Check: az webapp list-runtimes --os linux -o table"
  fi
  group_exists=$(az group exists -n "$rg" -o json)
  if [[ "$group_exists" == false ]]; then
    az group create -g "$rg" -l "$LOCATION" --tags "sk21Pocket=$APP" -o none
  fi
  plan_state=$(resource_state Microsoft.Web/serverfarms "$plan")
  if [[ "$plan_state" == new ]]; then
    echo "Creating Linux App Service plan ($sku)..."
    az appservice plan create -g "$rg" -n "$plan" -l "$LOCATION" --sku "$sku" --is-linux --tags "sk21Pocket=$APP" -o none
  else
    current_sku=$(az appservice plan show -g "$rg" -n "$plan" --query sku.name -o tsv)
    [[ "$current_sku" == "$sku" ]] || die "Existing plan uses $current_sku. Check your plan before retrying with that SKU."
  fi
  app_state=$(resource_state Microsoft.Web/sites "$APP")
  if [[ "$app_state" == new ]]; then
    echo "Creating Python 3.12 web app..."
    az webapp create -g "$rg" -p "$plan" -n "$APP" --runtime PYTHON:3.12 \
      --https-only true --basic-auth Disabled --tags "sk21Pocket=$APP" -o none
  fi
  az webapp config appsettings set -g "$rg" -n "$APP" --settings SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true -o none
  az webapp config set -g "$rg" -n "$APP" --startup-file "gunicorn --bind=0.0.0.0:8000 --timeout 120 app:app" -o none
  temp_dir=$(mktemp -d)
  trap 'rm -f -- "$temp_dir/app.zip"; rmdir -- "$temp_dir"' EXIT
  zip -qr "$temp_dir/app.zip" .deployment ./*.py requirements.txt static
  echo "Uploading source and installing dependencies on Azure..."
  az webapp deployment source config-zip -g "$rg" -n "$APP" --src "$temp_dir/app.zip" -o none
  host=$(az webapp show -g "$rg" -n "$APP" --query defaultHostName -o tsv)
  echo "Site: https://$host/"
  curl -fsS --retry 5 --retry-delay 5 --max-time 45 "https://$host/api/posts" | jq -e '.posts | type == "array"' >/dev/null
  echo "The bulletin board API responded successfully."
  exit 0
fi

if [[ "${POCKET_USE_ASSIGNED_APP:-0}" != 1 ]]; then
  app_state=$(resource_state Microsoft.Web/sites "$APP")
  [[ "$app_state" == existing ]] || die "Deploy the app first. For a school-assigned app, see README."
fi
app_id=$(az webapp show -g "$rg" -n "$APP" --query id -o tsv)

# Role lookup makes reruns safe. Limit access to this assignment's resources.
grant_role() {
  local principal=$1 role=$2 scope=$3 assignments
  assignments=$(az role assignment list --scope "$scope" --query "[?principalId=='$principal' && roleDefinitionName=='$role'].id" -o tsv)
  if [[ -z "$assignments" ]]; then
    az role assignment create --assignee-object-id "$principal" --assignee-principal-type ServicePrincipal \
      --role "$role" --scope "$scope" -o none
  fi
}

if [[ "$mode" == storage ]]; then
  suffix=$(printf '%s' "$sub/$rg/$APP" | sha256sum | cut -c1-15)
  store="pocket$suffix"
  store_state=$(resource_state Microsoft.Storage/storageAccounts "$store")
  if [[ "$store_state" == new ]]; then
    echo "Creating Standard_LRS storage account $store..."
    az storage account create -g "$rg" -n "$store" -l "$LOCATION" --sku Standard_LRS --kind StorageV2 \
      --min-tls-version TLS1_2 --allow-blob-public-access false --allow-shared-key-access false \
      --tags "sk21Pocket=$APP" -o none
  fi
  store_id=$(az storage account show -g "$rg" -n "$store" --query id -o tsv)
  table_id="$store_id/tableServices/default/tables/pocketposts"
  az rest --method put --url "https://management.azure.com$table_id?api-version=2023-05-01" --body '{"properties":{}}' -o none
  az storage container-rm create -g "$rg" --storage-account "$store" -n pocketimages --public-access off -o none
  principal=$(az webapp identity assign -g "$rg" -n "$APP" --query principalId -o tsv)
  grant_role "$principal" 'Storage Table Data Contributor' "$table_id"
  grant_role "$principal" 'Storage Blob Data Contributor' "$store_id/blobServices/default/containers/pocketimages"
  az webapp config appsettings set -g "$rg" -n "$APP" \
    --settings "STORAGE_ACCOUNT_NAME=$store" TABLE_NAME=pocketposts BLOB_CONTAINER=pocketimages -o none
  echo "Storage=$store  Table=pocketposts  Container=pocketimages"
  echo "After permissions take effect, submit a new post with an image and verify it in a second browser."
  exit 0
fi

: "${REPO:?Set REPO to the public GitHub owner/repository, then retry.}"
[[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "Use the format REPO=owner/repository."
repo_api="https://api.github.com/repos/$REPO"
meta=$(curl -fsS "$repo_api")
[[ "$(jq -r '.default_branch' <<< "$meta")" == main ]] || die "The supplied workflow deploys the main branch. Use main for this repository."
legacy_prefix=$(jq -er '"repo:" + .owner.login + "/" + .name' <<< "$meta")
immutable_prefix=$(jq -er '"repo:\(.owner.login)@\(.owner.id)/\(.name)@\(.id)"' <<< "$meta")
subject=${OIDC_SUBJECT:-}
if [[ -z "$subject" ]]; then
  template=$(curl -fsS "$repo_api/actions/oidc/customization/sub") ||
    die "Read the repository's OIDC configuration or set OIDC_SUBJECT to the exact subject for this repo on main."
  if ! jq -e '.use_default == true or .include_claim_keys == ["repo","context"] or .include_claim_keys == ["repo","ref"]' <<< "$template" >/dev/null; then
    die "This repository uses a custom or inherited subject template. Set OIDC_SUBJECT to the exact subject issued for this repo on main."
  fi
  # The current prefix captures old repositories, opt-ins, renames and transfers.
  prefix=$(jq -r '.sub_claim_prefix // empty' <<< "$template")
  if [[ -z "$prefix" ]]; then
    if jq -e '.use_immutable_subject == true' <<< "$template" >/dev/null; then
      prefix=$immutable_prefix
    elif jq -e '.use_immutable_subject == false' <<< "$template" >/dev/null; then
      prefix=$legacy_prefix
    elif jq -e '.created_at > "2026-07-15T23:59:59Z"' <<< "$meta" >/dev/null; then
      prefix=$immutable_prefix
    else
      die "This older repository does not report its subject format. Set OIDC_SUBJECT from its main-branch OIDC settings or login log."
    fi
  fi
  [[ "$prefix" == "$immutable_prefix" || "$prefix" == "$legacy_prefix" ]] ||
    die "Unexpected repository subject prefix. Use the exact repo and main-branch subject from GitHub in OIDC_SUBJECT."
  subject="$prefix:ref:refs/heads/main"
fi
identity="ci-$APP"
identity_state=$(resource_state Microsoft.ManagedIdentity/userAssignedIdentities "$identity")
if [[ "$identity_state" == new ]]; then
  az identity create -g "$rg" -n "$identity" -l "$LOCATION" --tags "sk21Pocket=$APP" -o none
fi
deploy_principal=$(az identity show -g "$rg" -n "$identity" --query principalId -o tsv)
grant_role "$deploy_principal" 'Website Contributor' "$app_id"
credential_count=$(az identity federated-credential list -g "$rg" --identity-name "$identity" \
  --query "length([?name=='github-main'])" -o tsv)
if [[ "$credential_count" == 0 ]]; then
  credential_cmd=create
else
  credential_cmd=update
fi
az identity federated-credential "$credential_cmd" -g "$rg" --identity-name "$identity" -n github-main \
  --issuer https://token.actions.githubusercontent.com --subject "$subject" --audiences api://AzureADTokenExchange -o none
client_id=$(az identity show -g "$rg" -n "$identity" --query clientId -o tsv)
tenant_id=$(az account show --query tenantId -o tsv)
cat <<VARIABLES
In GitHub, add these repository Actions variables:
AZURE_CLIENT_ID=$client_id
AZURE_TENANT_ID=$tenant_id
AZURE_SUBSCRIPTION_ID=$sub
AZURE_RESOURCE_GROUP=$rg
AZURE_WEBAPP_NAME=$APP
Trusted branch subject: $subject
VARIABLES
