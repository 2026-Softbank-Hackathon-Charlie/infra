{{- define "observability-boundary.queryConfig" -}}
worker_processes 1;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 128; }
http {
  access_log off;
  client_body_temp_path /tmp/client;
  proxy_temp_path /tmp/proxy;
  fastcgi_temp_path /tmp/fastcgi;
  uwsgi_temp_path /tmp/uwsgi;
  scgi_temp_path /tmp/scgi;
  server {
    listen 8443 ssl;
    ssl_certificate /etc/query/tls/tls.crt;
    ssl_certificate_key /etc/query/tls/tls.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    server_tokens off;
    client_max_body_size 1m;
    location = /healthz { return 200; }
    location ~ ^/api/v1/(query|query_range|query_exemplars|labels|metadata|series|label/[a-zA-Z_:][a-zA-Z0-9_:]*/values)$ {
      if ($request_method !~ ^(GET|POST)$) { return 405; }
      auth_basic "metrics";
      auth_basic_user_file /etc/query/auth/htpasswd;
      proxy_set_header Authorization "";
      proxy_set_header Cookie "";
      proxy_connect_timeout 3s;
      proxy_read_timeout 20s;
      proxy_pass http://monitoring-prometheus.observability.svc:9090;
    }
    location / { return 404; }
  }
}
{{- end -}}
