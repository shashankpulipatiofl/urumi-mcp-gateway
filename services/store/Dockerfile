# WordPress + WooCommerce + WP-CLI, with our idempotent setup script.
# The WordPress files are copied from /usr/src/wordpress to the persistent volume on first
# start by the official entrypoint, so WooCommerce ships inside that copy.
FROM wordpress:6.8-php8.3-apache

ARG WOOCOMMERCE_VERSION=10.2.2
ARG WP_CLI_VERSION=2.11.0

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl unzip less; \
    rm -rf /var/lib/apt/lists/*; \
    curl -fsSL -o /usr/local/bin/wp \
      "https://github.com/wp-cli/wp-cli/releases/download/v${WP_CLI_VERSION}/wp-cli-${WP_CLI_VERSION}.phar"; \
    chmod +x /usr/local/bin/wp; \
    curl -fsSL -o /tmp/woocommerce.zip \
      "https://github.com/woocommerce/woocommerce/releases/download/${WOOCOMMERCE_VERSION}/woocommerce.zip"; \
    unzip -q /tmp/woocommerce.zip -d /usr/src/wordpress/wp-content/plugins; \
    rm /tmp/woocommerce.zip; \
    chown -R www-data:www-data /usr/src/wordpress/wp-content/plugins/woocommerce

COPY setup-store.sh /usr/local/bin/setup-store
COPY demo-images /opt/urumi/demo-images
RUN chmod 0755 /usr/local/bin/setup-store && chmod -R a+rX /opt/urumi
