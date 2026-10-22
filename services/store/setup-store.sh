#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# One-shot, idempotent store bootstrap. Run by a Kubernetes Job (Helm hook)
# after every install/upgrade. Safe to run many times.
#
#  1. wait for the database
#  2. install WordPress core tables (only if not installed yet)
#  3. activate WooCommerce, skip the onboarding wizard, disable "coming soon"
#  4. enable Cash on Delivery so checkout works without a real payment gateway
#  5. create/rotate the REST API key used by the WooCommerce MCP server
#  6. seed a few demo products (only on an empty store)
#
# All inputs come from environment variables (filled from a ConfigMap + Secret).
# -----------------------------------------------------------------------------
set -euo pipefail

WP_PATH="${WP_PATH:-/var/www/html}"
DEMO_IMAGES="${DEMO_IMAGES:-/opt/urumi/demo-images}"
: "${STORE_URL:?STORE_URL is required}"
: "${WP_ADMIN_USER:?}" "${WP_ADMIN_PASSWORD:?}" "${WP_ADMIN_EMAIL:?}"
: "${WC_CONSUMER_KEY:?}" "${WC_CONSUMER_SECRET:?}"
STORE_TITLE="${STORE_TITLE:-Urumi Demo Store}"
STORE_TIMEZONE="${STORE_TIMEZONE:-UTC}"
STORE_CURRENCY="${STORE_CURRENCY:-INR}"
STORE_COUNTRY="${STORE_COUNTRY:-IN}"
SEED_PRODUCTS="${SEED_PRODUCTS:-true}"

WP=(wp --path="$WP_PATH")
if [ "$(id -u)" = "0" ]; then WP+=(--allow-root); fi
log() { echo "[setup-store] $*"; }
# Set an option; a no-op update (same value) is not an error for us.
opt() { "${WP[@]}" option update "$@" >/dev/null 2>&1 || log "note: option $1 unchanged or not updatable"; }

# The Job mounts the WordPress volume at $WP_PATH, so everything it writes (uploads, product
# images, placeholder) lands on the same disk the storefront serves. The WordPress container
# copies the core files there on its first start; wait for that.
for i in $(seq 1 90); do
  [ -f "$WP_PATH/wp-includes/version.php" ] && [ -f "$WP_PATH/wp-config.php" ] && break
  [ "$i" = 90 ] && { log "WordPress files not found in $WP_PATH"; exit 1; }
  log "waiting for WordPress files in $WP_PATH ..."; sleep 2
done
cd "$WP_PATH"

log "waiting for database ${WORDPRESS_DB_HOST:-?} ..."
for i in $(seq 1 90); do
  if php -r '$h=explode(":", getenv("WORDPRESS_DB_HOST")); $m=@new mysqli($h[0], getenv("WORDPRESS_DB_USER"), getenv("WORDPRESS_DB_PASSWORD"), getenv("WORDPRESS_DB_NAME"), (int)($h[1] ?? 3306)); exit($m->connect_errno ? 1 : 0);'; then
    log "database is reachable"; break
  fi
  [ "$i" = 90 ] && { log "database not reachable after 180s"; exit 1; }
  sleep 2
done

if ! "${WP[@]}" core is-installed 2>/dev/null; then
  log "installing WordPress core"
  "${WP[@]}" core install --url="$STORE_URL" --title="$STORE_TITLE" \
    --admin_user="$WP_ADMIN_USER" --admin_password="$WP_ADMIN_PASSWORD" \
    --admin_email="$WP_ADMIN_EMAIL" --skip-email
else
  log "WordPress already installed"
fi

opt home "$STORE_URL"
opt siteurl "$STORE_URL"
opt blogname "$STORE_TITLE"
opt timezone_string "$STORE_TIMEZONE"
"${WP[@]}" rewrite structure '/%postname%/' >/dev/null   # pretty permalinks -> /wp-json/ works
"${WP[@]}" rewrite flush >/dev/null

log "activating WooCommerce"
"${WP[@]}" plugin is-active woocommerce || "${WP[@]}" plugin activate woocommerce

log "configuring WooCommerce"
opt woocommerce_currency "$STORE_CURRENCY"
opt woocommerce_default_country "$STORE_COUNTRY"
opt woocommerce_coming_soon "no"            # storefront visible to guests
opt woocommerce_store_pages_only "no"
opt woocommerce_task_list_hidden "yes"
opt woocommerce_onboarding_profile '{"skipped":true}' --format=json
opt woocommerce_ship_to_countries "disabled"  # no shipping step needed
opt woocommerce_calc_taxes "no"
opt woocommerce_enable_guest_checkout "yes"
# WooCommerce uploads its placeholder image during activation, but this Job's filesystem is
# not the WordPress volume, so that file would 404. 0 = use the plugin's bundled placeholder.
opt woocommerce_placeholder_image 0
opt woocommerce_cod_settings \
  '{"enabled":"yes","title":"Cash on delivery","description":"Pay with cash upon delivery (test-friendly).","instructions":"Pay with cash upon delivery.","enable_for_methods":[],"enable_for_virtual":"yes"}' \
  --format=json
"${WP[@]}" wc tool run install_pages --user="$WP_ADMIN_USER" >/dev/null 2>&1 || true

log "creating REST API key for the MCP server"
"${WP[@]}" eval '
  global $wpdb;
  $table = $wpdb->prefix . "woocommerce_api_keys";
  $user  = get_user_by("login", getenv("WP_ADMIN_USER"));
  $ck    = getenv("WC_CONSUMER_KEY");
  $cs    = getenv("WC_CONSUMER_SECRET");
  // Remove any previous key we created (rotation-friendly), then insert the current one.
  $wpdb->delete($table, array("description" => "urumi-mcp-server"));
  $wpdb->insert($table, array(
    "user_id"         => $user->ID,
    "description"     => "urumi-mcp-server",
    "permissions"     => "read_write",
    "consumer_key"    => wc_api_hash($ck),   // WooCommerce stores the key hashed
    "consumer_secret" => $cs,
    "truncated_key"   => substr($ck, -7),
  ));
  echo "api key id: " . $wpdb->insert_id . PHP_EOL;
'

if [ "$SEED_PRODUCTS" = "true" ] && [ "$("${WP[@]}" post list --post_type=product --format=count)" = "0" ]; then
  log "seeding demo products"
  "${WP[@]}" wc product create --user="$WP_ADMIN_USER" --name="Classic Cotton T-Shirt" --regular_price="499" \
    --short_description="Soft 100% cotton tee." --status=publish >/dev/null
  "${WP[@]}" wc product create --user="$WP_ADMIN_USER" --name="Denim Jeans" --regular_price="1299" \
    --short_description="Slim-fit stretch denim." --status=publish >/dev/null
  "${WP[@]}" wc product create --user="$WP_ADMIN_USER" --name="Canvas Tote Bag" --regular_price="349" \
    --short_description="Everyday carry-all." --status=publish >/dev/null
fi

# Product photos for the demo products (only if a product has no image yet)
attach_image() {  # $1 = product name, $2 = image file
  local id
  id=$("${WP[@]}" post list --post_type=product --title="$1" --field=ID --posts_per_page=1 2>/dev/null | head -1)
  [ -n "$id" ] && [ -f "$DEMO_IMAGES/$2" ] || return 0
  if [ -z "$("${WP[@]}" post meta get "$id" _thumbnail_id 2>/dev/null)" ]; then
    "${WP[@]}" media import "$DEMO_IMAGES/$2" --post_id="$id" --featured_image --title="$1" --porcelain >/dev/null \
      && log "image attached to '$1'"
  fi
}
attach_image "Classic Cotton T-Shirt" classic-cotton-tshirt.jpg
attach_image "Denim Jeans" denim-jeans.jpg
attach_image "Canvas Tote Bag" canvas-tote-bag.jpg

log "done. Storefront: $STORE_URL"
