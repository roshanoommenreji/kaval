{{/*
Release-qualified name, so two installs of this chart (e.g. staging and prod sharing a
cluster, or a dev chart alongside someone else's) never collide on resource names.
*/}}
{{- define "kaval.fullname" -}}
{{- if .Values.nameOverride -}}
{{- .Values.nameOverride -}}
{{- else -}}
{{- .Release.Name -}}
{{- end -}}
{{- end -}}

{{/* Labels every resource in this chart carries, for `kubectl get -l` and `helm uninstall`. */}}
{{- define "kaval.labels" -}}
app.kubernetes.io/part-of: kaval
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{/* Per-component selector labels — the stable identity a Service/Deployment pair matches on. */}}
{{- define "kaval.selectorLabels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}
