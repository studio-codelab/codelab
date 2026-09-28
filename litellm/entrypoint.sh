#!/bin/sh
# codelab-litellm -- lit les cles des fournisseurs, puis lance LiteLLM.
#
# POURQUOI CE SCRIPT, ET PLUS « env_file: » DANS LE COMPOSE. Compose resout
# un env_file au chargement du projet : absent, c'est la stack ENTIERE qui
# refuse de partir. La forme tolerante (« required: false ») est refusee par
# le validateur de CasaOS. Tant que ce service vivait derriere un profil, un
# premier « up » ne le lisait pas ; sans profil -- CasaOS ne sait pas en
# activer un -- il faut qu'un fichier absent ne bloque rien. Le fichier est
# donc monte, et lu ICI, s'il existe.
#
# LU, PAS EXECUTE. « . fichier » executerait ce qu'il contient ; on ne
# reprend que des lignes NOM=valeur, NOM en majuscules. Le fichier est ecrit
# par codelab-postgres et par le panneau, jamais a la main -- mais un secret
# n'a aucune raison d'etre interprete par un shell.
#
# Une cle posee depuis le panneau prend donc effet au prochain demarrage de
# ce conteneur, comme avant.
FICHIER=/var/lib/codelab/litellm/fournisseurs.env

if [ -f "$FICHIER" ] && [ -r "$FICHIER" ]; then
    while IFS= read -r ligne || [ -n "$ligne" ]; do
        case "$ligne" in
            ''|'#'*) continue ;;
        esac
        nom="${ligne%%=*}"
        valeur="${ligne#*=}"
        case "$nom" in
            *[!A-Z0-9_]*|'') continue ;;
        esac
        # Une valeur vide n'est pas exportee : LiteLLM traiterait « cle
        # vide » comme une cle posee, et le message d'erreur du fournisseur
        # serait moins clair que « absente ».
        [ -n "$valeur" ] && export "$nom=$valeur"
    done < "$FICHIER"
else
    echo "[codelab-litellm] $FICHIER absent : aucune cle de fournisseur." >&2
fi

# Le point d'entree de l'image officielle, s'il est la (il vit sous
# /app/docker, WORKDIR de l'image) : c'est lui qu'elle lancerait sans nous.
# Sinon, le programme directement.
if [ -x /app/docker/prod_entrypoint.sh ]; then
    exec /app/docker/prod_entrypoint.sh "$@"
fi
exec litellm "$@"
