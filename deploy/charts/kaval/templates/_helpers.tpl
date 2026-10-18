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

{{/*
The Secret a service reads its POSTGRES_* env from (KAV-32). Local/dev/CI leave
postgres.secretNames.<service> empty, which resolves to the chart's own postgres.yaml
Secret (today's behaviour, unchanged) — the in-cluster Postgres everyone shares. An
environment with its own database server (ADR-0008) sets each service's name to a real
out-of-band Secret, created the same way as global.imagePullSecretName/ecr-cred and
gateway.slackSecretName — never a password committed here. Each Secret, chart-managed or
out-of-band, always carries POSTGRES_HOST/PORT/DB/USER/PASSWORD[/SSLMODE] itself, so a
plain envFrom is enough and no template needs its own hardcoded POSTGRES_HOST line.
Call as: {{ include "kaval.postgresSecretName" (dict "root" $ "service" "gateway") }}
*/}}
{{- define "kaval.postgresSecretName" -}}
{{- $name := index .root.Values.postgres.secretNames .service -}}
{{- if $name -}}
{{- $name -}}
{{- else -}}
{{- include "kaval.fullname" .root }}-postgres
{{- end -}}
{{- end -}}

{{/*
The services that show health numbers on :9100 (KAV-78, ADR-0042): one scrape job and one headless
Service each (metrics.yaml). The poller is only there when Prometheus is, so it is listed here only
then. A JSON list because a Helm helper can only return text.
*/}}
{{- define "kaval.metricsComponents" -}}
{{- $c := list "gateway" "agent" "executor" "collector" -}}
{{- if .Values.prometheus.enabled -}}{{- $c = append $c "collector-prometheus" -}}{{- end -}}
{{- if .Values.diagnoser.enabled -}}{{- $c = append $c "diagnoser" -}}{{- end -}}
{{- if .Values.costReader.enabled -}}{{- $c = append $c "collector-cost" -}}{{- end -}}
{{- toJson $c -}}
{{- end -}}

{{/*
Readiness for a background program (KAV-83, ADR-0051). Each one opens its health page on :9100 only
after it has started up and reached its loop (kaval_shared/metrics.py: the page is opened after
the database and cluster set-up), so "the page answers" means "this copy got as far as working".
Without a check, Kubernetes counts a copy ready the moment its process exists, so a bad release
that dies a few seconds later has already replaced the good copy: the bad-release game day found
this. With it, a rollout waits for the new copy and the old one keeps working until it is ready.
Readiness only, deliberately no liveness check: a restart because of a slow database would make
a bad hour worse, and a stalled loop is the alert's job (KavalLoopStalled), not the pod's.
Needs `KAVAL_METRICS_PORT` left at its default; the chart never sets it.
*/}}
{{- define "kaval.workerReadiness" -}}
readinessProbe:
  tcpSocket:
    port: metrics
  initialDelaySeconds: 10
  periodSeconds: 10
  failureThreshold: 3
{{- end -}}
