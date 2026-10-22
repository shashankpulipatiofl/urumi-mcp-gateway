{{/* Base name for every resource of this store, e.g. "urumi-store" */}}
{{- define "store.fullname" -}}
{{- printf "%s-%s" .Release.Name .Values.nameSuffix | trunc 50 | trimSuffix "-" -}}
{{- end -}}

{{- define "store.labels" -}}
app.kubernetes.io/part-of: urumi-platform
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
urumi.dev/store: {{ include "store.fullname" . }}
{{- end -}}

{{- define "store.selector" -}}
urumi.dev/store: {{ include "store.fullname" . }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "store.secretName" -}}
{{- default (printf "%s-secrets" (include "store.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{- define "store.url" -}}
{{- printf "%s://%s" .Values.store.scheme .Values.store.host -}}
{{- end -}}

{{/*
Reuse a value from the live Secret if it exists (so upgrades never rotate passwords by
accident), otherwise use the provided value, otherwise generate a random one.
Usage: include "store.secretValue" (dict "ctx" . "key" "x" "value" "" "length" 32 "prefix" "")
*/}}
{{- define "store.secretValue" -}}
{{- $name := printf "%s-secrets" (include "store.fullname" .ctx) -}}
{{- $existing := lookup "v1" "Secret" .ctx.Release.Namespace $name -}}
{{- if and $existing $existing.data (hasKey $existing.data .key) -}}
{{- index $existing.data .key -}}
{{- else if .value -}}
{{- .value | b64enc -}}
{{- else -}}
{{- printf "%s%s" (default "" .prefix) (randAlphaNum (int (default 32 .length))) | b64enc -}}
{{- end -}}
{{- end -}}

{{- define "store.podSecurity" -}}
automountServiceAccountToken: false
{{- end -}}

{{- define "store.restrictedContainer" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: ["ALL"] }
  seccompProfile: { type: RuntimeDefault }
{{- end -}}
