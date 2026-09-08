FROM nginx:1.27-alpine

# htpasswd (apache2-utils) pour fabriquer le fichier d'authentification, et
# openssl pour tirer un mot de passe au hasard au premier demarrage.
RUN apk add --no-cache apache2-utils openssl

# Les deux fichiers atterrissent dans conf.d, donc dans le contexte "http"
# de nginx : c'est le seul endroit ou une directive "map" est acceptee.
COPY dagster/proxy/map-upgrade.conf /etc/nginx/conf.d/00-map-upgrade.conf
COPY dagster/proxy/nginx.conf /etc/nginx/conf.d/default.conf
COPY dagster/proxy/entrypoint.sh /opt/codelab/dagster-proxy/entrypoint.sh
RUN chmod +x /opt/codelab/dagster-proxy/entrypoint.sh

# Validation de la configuration au BUILD : une accolade oubliee ou une
# directive mal placee echoue ici, en CI, plutot qu'au demarrage du conteneur
# sur le serveur.
#
# Ce test ne passe que parce que l'adresse amont est donnee par une variable
# (voir nginx.conf) : ecrite en clair dans proxy_pass, nginx tenterait de la
# resoudre des le chargement de la configuration et echouerait ici sur
# "host not found in upstream".
RUN nginx -t

EXPOSE 3000

# nginx reste le processus principal : il recoit le SIGTERM de "docker stop"
# et ses journaux d'acces arrivent dans "docker logs".
ENTRYPOINT ["/opt/codelab/dagster-proxy/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
