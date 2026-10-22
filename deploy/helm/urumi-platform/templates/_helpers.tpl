{{- define "urumi.name" -}}{{ .Release.Name }}{{- end -}}

{{- define "urumi.labels" -}}
app.kubernetes.io/part-of: urumi-platform
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "urumi.selector" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "urumi.secretName" -}}
{{- default (printf "%s-platform-secrets" .Release.Name) .Values.secrets.existingSecret -}}
{{- end -}}

{{- define "urumi.storeFullname" -}}
{{- printf "%s-%s" .Release.Name (default "store" .Values.woocommerce.nameSuffix) -}}
{{- end -}}

{{- define "urumi.storeSecretName" -}}
{{- default (printf "%s-secrets" (include "urumi.storeFullname" .)) .Values.woocommerce.secrets.existingSecret -}}
{{- end -}}

{{- define "urumi.publicUrl" -}}
{{- printf "%s://%s" .Values.gateway.scheme .Values.gateway.host -}}
{{- end -}}

{{/* generate-once secret value, reused across upgrades (see woocommerce-store chart for details) */}}
{{- define "urumi.secretValue" -}}
{{- $existing := lookup "v1" "Secret" .ctx.Release.Namespace (printf "%s-platform-secrets" .ctx.Release.Name) -}}
{{- if and $existing $existing.data (hasKey $existing.data .key) -}}
{{- index $existing.data .key -}}
{{- else if .value -}}
{{- .value | b64enc -}}
{{- else -}}
{{- randAlphaNum (int (default 40 .length)) | b64enc -}}
{{- end -}}
{{- end -}}

{{/* Hardened container defaults for our own images */}}
{{- define "urumi.restrictedContainer" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: {{ default 10001 .uid }}
  runAsGroup: {{ default 10001 .uid }}
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: ["ALL"] }
  seccompProfile: { type: RuntimeDefault }
{{- end -}}

{{/* Upstream servers the gateway registers on first start (JSON, no secrets) */}}
{{- define "urumi.seedServers" -}}
{{- $ns := .Release.Namespace -}}
{{- $store := include "urumi.storeFullname" . -}}
{{- $servers := list -}}
{{- if .Values.woocommerce.enabled -}}
{{- $servers = append $servers (dict "slug" "woocommerce" "display_name" "WooCommerce Store" "description" (printf "Products and orders of %s" .Values.woocommerce.store.title) "url" (printf "http://%s-mcp.%s.svc.cluster.local:8080/mcp" $store $ns) "auth_type" "bearer" "credential_env" "SEED_WOOCOMMERCE_TOKEN" "admin_only_tools" .Values.gateway.storeAdminOnlyTools) -}}
{{- end -}}
{{- if .Values.demoUpstreams.enabled -}}
{{- $servers = append $servers (dict "slug" "fx-rates" "display_name" "FX Rates" "description" "Currency conversion (ECB rates with offline fallback)" "url" (printf "http://%s-fx-rates.%s.svc.cluster.local:8080/mcp" .Release.Name $ns) "auth_type" "header" "auth_header_name" "X-API-Key" "credential_env" "SEED_FX_API_KEY") -}}
{{- $servers = append $servers (dict "slug" "team-notes" "display_name" "Team Notes" "description" "Shared team notebook" "url" (printf "http://%s-team-notes.%s.svc.cluster.local:8080/mcp" .Release.Name $ns) "auth_type" "none") -}}
{{- end -}}
{{- $servers | toJson -}}
{{- end -}}
