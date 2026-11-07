{{- define "db-network.proxyLabels" -}}
tailscale.com/managed: "true"
tailscale.com/parent-resource: {{ .name | quote }}
tailscale.com/parent-resource-ns: {{ .namespace | quote }}
tailscale.com/parent-resource-type: svc
{{- end }}

{{- define "db-network.controlEgress" -}}
- to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
      podSelector:
        matchLabels:
          k8s-app: kube-dns
  ports:
    - {protocol: UDP, port: 53}
    - {protocol: TCP, port: 53}
- to:
    - ipBlock: {cidr: 0.0.0.0/0}
  ports:
    - {protocol: TCP, port: 443}
    {{- with .Values.kubeApiServerPort }}{{ if ne (int .) 443 }}
    - {protocol: TCP, port: {{ int . }}}
    {{- end }}{{ end }}
# GKE NodeLocal DNSCache answers the kube-dns ClusterIP from a hostNetwork pod,
# which the kube-dns podSelector above never matches (ADR 0052).
- ports:
    - {protocol: UDP, port: 53}
    - {protocol: TCP, port: 53}
{{- end }}
