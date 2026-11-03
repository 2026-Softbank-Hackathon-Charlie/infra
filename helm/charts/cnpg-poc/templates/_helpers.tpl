{{- define "cnpg-poc.validate" -}}
{{- if ne .Release.Namespace "charlie-db" -}}
{{- fail "cnpg-poc may only be rendered in the workload charlie-db namespace" -}}
{{- end -}}
{{- $_ := required "backup.bucketName must be supplied from the CNPG Terraform output" .Values.backup.bucketName -}}
{{- end -}}

{{- define "cnpg-poc.labels" -}}
app.kubernetes.io/name: cnpg-poc
app.kubernetes.io/part-of: charlie
app.kubernetes.io/managed-by: Helm
{{- end -}}
