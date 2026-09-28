#!/bin/sh
# Sonde de sante de codelab-redis : un PING authentifie.
#
# Un script plutot qu'une commande dans le docker-compose : le mot de passe
# vit dans la configuration generee, et le lire depuis le compose demanderait
# un "$" que Compose et CasaOS reecrivent. REDISCLI_AUTH plutot que "-a" : le
# mot de passe ne passe pas sur la ligne de commande, visible par ps.
set -eu
CONF="${CODELAB_REDIS_CONF:-/etc/codelab-redis/redis.conf}"
REDISCLI_AUTH="$(sed -n 's/^user default on >\([^ ]*\) .*/\1/p' "$CONF")"
export REDISCLI_AUTH
[ "$(redis-cli -h 127.0.0.1 -p "${CODELAB_REDIS_PORT:-6379}" ping)" = "PONG" ]
