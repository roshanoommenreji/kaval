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

{{/*
Labels every resource in this chart carries, for `kubectl get -l` and `helm uninstall`.
`replace "+" "_"` on the chart version (KAV-51, found live): Flux's helm-controller
packages a HelmChart with valuesFiles set under an appended `+<n>` semver build-metadata
suffix (source-controller's own documented behaviour — each distinct values combination
needs a distinct artifact revision), and `+` isn't a legal Kubernetes label-value
character. `helm install`/`upgrade` run directly (every environment before prod) never
produces that suffix, so this was latent until Flux's packaging path exercised it. The
underscore substitution is Helm's own standard scaffold convention for this exact case.
*/}}
{{- define "kaval.labels" -}}
app.kubernetes.io/part-of: kaval
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/* Per-component selector labels — the stable identity a Service/Deployment pair matches on. */}}
{{- define "kaval.selectorLabels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Full image reference, with the registry a GitOps environment injects at reconcile time
prepended when set (KAV-51). `global.imageRegistry` stays empty for local/k3d, where
`repository` alone (e.g. "kaval/gateway") already resolves against the node's own image
store — no registry ever gets committed here, because an ECR registry hostname embeds the
AWS account ID (CLAUDE.md: no real account IDs in git); prod's Flux HelmRelease merges it
in from a ConfigMap the node's cloud-init writes from its own instance identity document.
Call as: {{ include "kaval.image" (dict "root" $ "repository" .Values.gateway.image.repository "tag" .Values.gateway.image.tag) }}
*/}}
{{- define "kaval.image" -}}
{{- if .root.Values.global.imageRegistry -}}
{{- .root.Values.global.imageRegistry }}/{{ .repository }}:{{ .tag -}}
{{- else -}}
{{- .repository }}:{{ .tag -}}
{{- end -}}
{{- end -}}

{{/*
Pod-level imagePullSecrets, only when global.imagePullSecretName is set (KAV-51). Empty
for local/k3d, where images are loaded directly and nothing is ever pulled from a registry
that needs auth; prod's HelmRelease merges the name in the same way it merges
global.imageRegistry. Call as: {{- include "kaval.imagePullSecrets" . | nindent 6 }}
*/}}
{{- define "kaval.imagePullSecrets" -}}
{{- if .Values.global.imagePullSecretName }}
imagePullSecrets:
  - name: {{ .Values.global.imagePullSecretName }}
{{- end -}}
{{- end -}}
