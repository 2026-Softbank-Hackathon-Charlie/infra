{{- define "hopcharlie.labels" -}}
app.kubernetes.io/part-of: hopcharlie
app.kubernetes.io/component: {{ . }}
{{- end }}

{{/* Pod security for the platform image (uid 10001) and PostgreSQL (uid 70). */}}
{{- define "hopcharlie.podSecurity" -}}
runAsNonRoot: true
runAsUser: {{ . }}
runAsGroup: {{ . }}
fsGroup: {{ . }}
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "hopcharlie.containerSecurity" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: [ALL]
{{- end }}

{{/*
Init container that copies this pod's settings from SSM Parameter Store into
the "settings" volume. Call with (dict "root" $ "fetch" "api|gateway|postgres").
*/}}
{{- define "hopcharlie.fetchSettings" -}}
- name: fetch-settings
  image: {{ .root.Values.images.awsCli | quote }}
  command: ["/bin/bash", "/scripts/fetch-settings.sh"]
  env:
    - {name: AWS_REGION, value: {{ .root.Values.awsRegion | quote }}}
    - {name: PREFIX, value: {{ .root.Values.parameterPrefix | quote }}}
    - {name: FETCH, value: {{ .fetch | quote }}}
    - {name: GITHUB_APP_ID, value: {{ .root.Values.github.appId | quote }}}
    - {name: HOME, value: /tmp}
  securityContext:
    {{- include "hopcharlie.containerSecurity" . | nindent 4 }}
  resources:
    requests: {cpu: 50m, memory: 64Mi}
    limits: {cpu: 200m, memory: 256Mi}
  volumeMounts:
    - {name: settings, mountPath: /config}
    - {name: scripts, mountPath: /scripts, readOnly: true}
{{- end }}

{{- define "hopcharlie.settingsVolumes" -}}
- name: settings
  emptyDir:
    medium: Memory
    sizeLimit: 8Mi
- name: scripts
  configMap:
    name: hopcharlie-fetch-settings
{{- end }}
