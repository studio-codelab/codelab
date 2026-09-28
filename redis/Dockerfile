# Image Redis de CodeLab : l'officielle, plus un entrypoint versionne.
#
# Meme raison d'etre que l'image Postgres : le mot de passe se genere au
# premier demarrage et s'ecrit dans credentials.env, et ce shell ne peut pas
# vivre dans le docker-compose -- Compose et les interfaces d'installation
# reecrivent les "$VAR" d'un script inline avant que le shell ne les voie.
#
# Tag majeur et non digest : les correctifs de securite arrivent a la
# reconstruction hebdomadaire (voir .github/workflows/build-images.yml).
FROM redis:8-alpine

COPY redis/entrypoint.sh /usr/local/bin/codelab-entrypoint.sh
COPY redis/sante.sh /usr/local/bin/codelab-redis-sante
RUN chmod +x /usr/local/bin/codelab-entrypoint.sh /usr/local/bin/codelab-redis-sante \
    && mkdir -p /etc/codelab-redis \
    && chown redis:redis /etc/codelab-redis \
    && chmod 700 /etc/codelab-redis

ENTRYPOINT ["/usr/local/bin/codelab-entrypoint.sh"]
CMD ["redis-server", "/etc/codelab-redis/redis.conf"]
