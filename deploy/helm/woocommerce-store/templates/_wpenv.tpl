{{/* Environment shared by the WordPress container and the setup Job */}}
{{- define "store.wpEnv" -}}
- name: WORDPRESS_DB_HOST
  value: {{ include "store.fullname" . }}-mariadb:3306
- name: WORDPRESS_DB_NAME
  value: wordpress
- name: WORDPRESS_DB_USER
  value: wordpress
- name: WORDPRESS_DB_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ include "store.secretName" . }}, key: mariadb-password } }
# Pin the public URL (so internal hostnames never leak into links) and trust the
# X-Forwarded-Proto header set by the Ingress controller / the MCP server.
- name: WORDPRESS_CONFIG_EXTRA
  value: |
    define('WP_HOME', '{{ include "store.url" . }}');
    define('WP_SITEURL', '{{ include "store.url" . }}');
    if (isset($_SERVER['HTTP_X_FORWARDED_PROTO']) && strpos($_SERVER['HTTP_X_FORWARDED_PROTO'], 'https') !== false) { $_SERVER['HTTPS'] = 'on'; }
    define('DISALLOW_FILE_EDIT', true);
{{- end -}}
