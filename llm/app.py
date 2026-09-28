"""
CodeLab LLM -- la porte unique vers un fournisseur de modeles.

Ce que ce service fait, et pourquoi il existe :

  IDENTITE.   Personne n'appelle le fournisseur directement. Une requete
              arrive ici avec une assertion SSO signee par le panneau, ou
              avec une cle CodeLab enregistree en base. Sans l'un des deux,
              401 -- et la cle du fournisseur ne quitte jamais ce conteneur.

  COMPTABILITE. Qui a consomme quoi, quand, et combien de jetons. C'est la
              SEULE chose que ce service garde : les echanges appartiennent
              au client qui les mene, et les recopier ici ferait deux
              exemplaires de chaque conversation privee, dans deux systemes
              a sauvegarder, pour une relecture que personne ne fait.

  TRADUCTION. Un nom logique (« codelab-coding ») vers une CHAINE de
              modeles reels chez plusieurs fournisseurs, essayes dans
              l'ordre : quand la part gratuite de l'un est epuisee, le
              suivant repond.

POUR LES APPLICATIONS SEULEMENT. Codex, l'agent de developpement, se
connecte avec un compte ChatGPT dans le conteneur dev : un agent envoie des
dizaines de requetes par tache, et aucune part gratuite n'y suffit.

POURQUOI PLUS DE LITELLM. La version precedente importait LiteLLM -- une
bibliotheque qui parle a cent fournisseurs -- pour appeler UN fournisseur
deja compatible OpenAI. Cent dependances transitives, plusieurs centaines de
megaoctets dans l'image, et une surface a suivre en securite, pour ce que
fait `httpx` en quarante lignes. C'est le meme raisonnement que le TOTP du
panneau, ecrit a la main plutot qu'importe : on ajoute une dependance quand
elle porte un vrai travail, pas quand elle porte un POST.

    Commande : uvicorn app:app --host 0.0.0.0 --port 8080
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

import httpx
import psycopg
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg_pool import ConnectionPool

# Le fichier de secrets de CE service, et de lui seul : le mot de passe de
# son role Postgres et la cle du fournisseur. credentials.env -- qui porte le
# mot de passe d'administration du panneau, la cle privee SSO et le mot de
# passe SMTP -- n'est plus monte ici : ce service embarque du code tiers et
# parle a l'exterieur, il n'a aucune raison de les voir.
ENV_FILE = os.environ.get("CODELAB_LLM_ENV_FILE", "/var/lib/codelab/llm/llm.env")

# Une conversation renvoie tout son historique a chaque tour : la limite
# laisse de la place a une longue discussion, et bloque ce qui n'en est pas.
CORPS_MAX = 4 * 1024 * 1024
APPELS_PAR_MINUTE = 60
# Au-dela, un client qui ne revient jamais garderait sa file pour toujours.
CLIENTS_SUIVIS_MAX = 512

SSO_EMETTEUR = "codelab"
# L'audience attendue dans l'assertion : le nom de l'application qui parle.
# VIDE PAR DEFAUT, et c'est volontaire : aucune application n'est autorisee
# tant qu'on ne l'a pas nommee. Un defaut qui designe une application
# particuliere la rend autorisee sur une installation qui ne l'a jamais
# demandee.
SSO_AUDIENCE = os.environ.get("CODELAB_LLM_AUDIENCE", "")
if SSO_AUDIENCE.startswith("${"):
    # Une variable du compose que CasaOS n'a pas substituee : pas un nom.
    SSO_AUDIENCE = ""

# LES FOURNISSEURS : des services compatibles OpenAI, appeles EN DIRECT.
#
# Il y avait un routeur (codelab-litellm) entre ce service et le fournisseur.
# Il est retire : sur une petite machine, il coutait un conteneur, plus d'un
# Go de disque et une cle de liaison de plus.
#
# PLUSIEURS FOURNISSEURS, POUR LEUR PART GRATUITE. Chacun offre un quota
# gratuit (requetes par minute, par jour, jetons par mois), vite atteint
# par une application un peu sollicitee. Un nom logique designe donc une
# CHAINE :
# le premier fournisseur dont la cle est posee est essaye, et s'il refuse --
# quota epuise (429), credit insuffisant (402), modele retire (404), panne --
# le suivant prend la requete. Le client n'en sait rien.
#
# Ce service garde ce qui lui appartient : identite CodeLab, quota,
# comptabilite. Les cles des fournisseurs vivent dans SON fichier (llm.env),
# que ni les applications ni le conteneur de developpement ne voient.
#
# Tous parlent /chat/completions, le seul protocole que sert ce service.
#
# « base » d'OpenRouter : CODELAB_LLM_FOURNISSEUR la remplace, pour un autre
# service compatible (OpenAI par exemple) avec la meme cle.
FOURNISSEUR_PAR_DEFAUT = "https://openrouter.ai/api/v1"
FOURNISSEURS = {
    "gemini": {"base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "variable": "GEMINI_API_KEY"},
    "groq": {"base": "https://api.groq.com/openai/v1",
             "variable": "GROQ_API_KEY"},
    # Mistral n'accepte que des identifiants d'appel d'outil de 9 caracteres
    # alphanumeriques : ceux venus d'un autre fournisseur sont reecrits.
    "mistral": {"base": "https://api.mistral.ai/v1",
                "variable": "MISTRAL_API_KEY", "ids_courts": True},
    "openrouter": {"base": None, "variable": "OPENROUTER_API_KEY"},
}


def base_de(fid):
    """L'adresse d'un fournisseur, relue a chaque appel comme les cles."""
    base = FOURNISSEURS[fid]["base"]
    if fid == "openrouter":
        base = valeur("CODELAB_LLM_FOURNISSEUR") or FOURNISSEUR_PAR_DEFAUT
    return base.rstrip("/")


def fournisseur_url():
    """L'adresse d'OpenRouter (ou de son remplacant) -- gardee pour les
    diagnostics qui l'appellent encore."""
    return base_de("openrouter")


def cle_de(fid):
    return valeur(FOURNISSEURS[fid]["variable"])


# Les noms logiques, et la chaine de modeles reels derriere chacun. C'est le
# contrat avec les clients : une application demande « codelab-coding »,
# jamais un nom de fournisseur.
#
# Une chaine : « fournisseur:modele » separes par des virgules, essayes dans
# l'ordre. Un element sans fournisseur connu va a OpenRouter -- c'est ainsi
# qu'une ancienne valeur (« anthropic/claude-sonnet-5 ») se lit encore.
# CODELAB_LLM_MODELE_<NOM> dans llm.env remplace une chaine sans rien
# reconstruire.
#
# QUE DES PARTS GRATUITES, et des alias « -latest » la ou le fournisseur en
# a : un identifiant date disparait, un alias suit. Un identifiant devenu
# faux n'arrete rien -- le fournisseur repond 404 et le suivant prend la
# requete -- mais il se lit dans « docker logs codelab-llm ».
MODELES_PAR_DEFAUT = {
    "codelab-coding": ("gemini:gemini-flash-latest, mistral:devstral-medium-latest,"
                       " groq:openai/gpt-oss-120b, openrouter:gratuits"),
    "codelab-fast": ("groq:openai/gpt-oss-20b, gemini:gemini-flash-lite-latest,"
                     " mistral:mistral-small-latest, openrouter:gratuits"),
    "codelab-reasoning": ("gemini:gemini-pro-latest, mistral:magistral-medium-latest,"
                          " groq:openai/gpt-oss-120b, openrouter:gratuits"),
}

# « openrouter:gratuits » : LES MODELES GRATUITS DU MOMENT, pas un nom fige.
#
# La liste des modeles « :free » d'OpenRouter change chaque semaine : un nom
# ecrit ici (« qwen/qwen3-coder:free ») est devenu payant, et la chaine
# tombait sur « This model is unavailable for free ». La passerelle lit donc
# le catalogue public d'OpenRouter, garde les modeles gratuits qui acceptent
# les outils (une application qui en appelle ne doit pas tomber sur un
# modele qui les ignore), les plus grands contextes d'abord, et essaie les
# premiers. Le catalogue est garde une heure.
GRATUITS = "gratuits"
GRATUITS_ESSAYES = 4
GRATUITS_DUREE = 3600
_gratuits = {"quand": 0.0, "liste": []}
PREFIXE_MODELE = "CODELAB_LLM_MODELE_"

DELAI_FOURNISSEUR = 90.0
DELAI_CONNEXION = 10.0

# COMBIEN DE TEMPS ON GARDE LA CONSOMMATION. llm_usage prend une ligne par
# requete et n'en perdait aucune : sur un serveur personnel qui tourne des
# annees, c'est la seule table de ce service qui grossit sans fin, en silence
# et sans plafond.
#
# 400 jours et pas 365 : le panneau ne sait afficher qu'une fenetre de 365
# jours au maximum (consommation_llm la plafonne), donc au-dela d'un an ces
# lignes ne sont plus lisibles nulle part -- elles n'occupent que le disque.
# La marge de 35 jours garantit qu'aucune ligne qu'une fenetre affichable
# pourrait encore atteindre ne soit supprimee.
#
# Mettre 0 desactive la purge, pour qui veut tout garder.
RETENTION_USAGE_JOURS = int(os.environ.get("CODELAB_LLM_RETENTION_JOURS", "400"))
PURGE_INTERVALLE = 24 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_api_keys (
    id BIGSERIAL PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE, key_name TEXT NOT NULL,
    user_id TEXT NOT NULL, app_id TEXT NOT NULL, revoked_at TIMESTAMPTZ,
    -- A QUOI ELLE SERT, en une phrase. Facultative : une cle sans
    -- description reste une cle, et forcer un texte ne ferait que produire
    -- des « test » et des « cle ».
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id BIGSERIAL PRIMARY KEY, timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id TEXT NOT NULL, app_id TEXT NOT NULL,
    -- QUELLE CLE A PAYE. Sans cette colonne, la consommation ne se rattachait
    -- qu'au couple (utilisateur, application) : deux cles du meme poste
    -- etaient indistinguables, et « suivre l'utilisation de chacune » etait
    -- impossible. NULL pour une identite SSO, qui n'a pas de cle.
    key_id BIGINT,
    requested_model TEXT NOT NULL, actual_model TEXT, provider TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0, cost NUMERIC(18, 8) NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL, cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    fallback BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS llm_usage_dimensions_idx
    ON llm_usage (user_id, app_id, requested_model, provider, timestamp);
-- Les installations d'avant la colonne : CREATE TABLE IF NOT EXISTS ne
-- touche pas une table qui existe deja, et sans cette ligne la consommation
-- continuerait de s'enregistrer sans cle, en silence.
ALTER TABLE llm_api_keys ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS key_id BIGINT;
CREATE INDEX IF NOT EXISTS llm_usage_cle_idx ON llm_usage (key_id, timestamp);
"""

_appels = defaultdict(deque)
_pool = None
_client = None


def lire_env(chemin=None):
    """Les valeurs du fichier de secrets. Illisible = dictionnaire vide.

    Jamais d'exception : ce service doit demarrer meme sans son fichier --
    il repond alors 503 en le disant, ce qui se diagnostique, alors qu'un
    conteneur qui refuse de demarrer ne dit rien a personne.
    """
    valeurs = {}
    try:
        with open(chemin or ENV_FILE, encoding="utf-8") as flux:
            for ligne in flux:
                ligne = ligne.strip()
                if ligne and not ligne.startswith("#") and "=" in ligne:
                    cle, _, valeur = ligne.partition("=")
                    valeurs[cle.strip()] = valeur.strip()
    except OSError:
        pass
    return valeurs


def pas_un_gabarit(v):
    """Une valeur « ${...} » n'en est pas une : c'est une variable du compose
    que CasaOS n'a pas substituee. Envoyee telle quelle comme cle, OpenRouter
    repondait « Missing Authentication header »."""
    return "" if (v or "").startswith("${") else (v or "")


def valeur(nom, defaut=""):
    """L'environnement d'abord, le fichier ensuite. Le compose peut donc
    tout surcharger sans reconstruire l'image."""
    return (pas_un_gabarit(os.environ.get(nom))
            or pas_un_gabarit(lire_env().get(nom)) or defaut)


def cle_amont():
    """La cle d'OpenRouter. CODELAB_LLM_AMONT_KEY -- la cle de liaison avec le
    routeur retire -- n'est PLUS lue : l'envoyer a OpenRouter donnerait 401."""
    return cle_de("openrouter")


def chaine(demande, table=None):
    """Les essais pour un nom logique : [(fournisseur, modele)], dans l'ordre,
    limites aux fournisseurs dont la cle est posee."""
    brut = (table or modeles()).get(demande, "")
    essais = []
    for element in brut.split(","):
        element = element.strip()
        if not element:
            continue
        fid, sep, modele = element.partition(":")
        if not (sep and fid in FOURNISSEURS):
            fid, modele = "openrouter", element
        if modele and cle_de(fid):
            essais.append((fid, modele))
    return essais


def modeles():
    """Les noms logiques configures, et leur modele reel.

    CODELAB_LLM_MODELE_<NOM> pose ou change une entree : le nom logique est
    le suffixe en minuscules, les soulignes deviennent des tirets. Vider la
    valeur retire le modele de la liste.
    """
    table = dict(MODELES_PAR_DEFAUT)
    sources = dict(lire_env())
    sources.update(os.environ)
    for cle, reel in sources.items():
        if not cle.startswith(PREFIXE_MODELE):
            continue
        nom = cle[len(PREFIXE_MODELE):].lower().replace("_", "-")
        if reel.strip():
            table[nom] = reel.strip()
        else:
            table.pop(nom, None)
    return table


def url_base_donnees():
    v = lire_env()
    return os.environ.get("DATABASE_URL") or (
        "postgresql://{}:{}@{}:{}/{}".format(
            os.environ.get("POSTGRES_USER") or v.get("POSTGRES_USER", "codelab_llm"),
            os.environ.get("POSTGRES_PASSWORD") or v.get("POSTGRES_PASSWORD", ""),
            os.environ.get("POSTGRES_HOST") or v.get("POSTGRES_HOST", "codelab-postgres"),
            os.environ.get("POSTGRES_PORT") or v.get("POSTGRES_PORT", "5432"),
            os.environ.get("POSTGRES_DB") or v.get("POSTGRES_DB", "codelab_llm")))


def pool():
    """Le pool de connexions, ouvert une fois.

    Avant, chaque appel ouvrait puis fermait sa connexion : cinq poignees de
    main TCP et cinq authentifications par message echange. Un pool les
    garde ouvertes -- c'est exactement ce pour quoi il existe.
    """
    global _pool
    if _pool is None:
        _pool = ConnectionPool(url_base_donnees(), min_size=1, max_size=4,
                               timeout=10, open=True)
    return _pool


def preparer_schema():
    with pool().connection() as cx:
        cx.execute(SCHEMA)


def purger_la_consommation_ancienne():
    """Supprime les lignes de llm_usage au-dela de la retention. Renvoie le
    nombre de lignes supprimees."""
    if RETENTION_USAGE_JOURS <= 0:
        return 0
    with pool().connection() as cx:
        # make_interval(days => %s) et pas une chaine d'intervalle assemblee :
        # l'argument reste un entier lie, jamais du texte concatene dans le SQL.
        r = cx.execute("DELETE FROM llm_usage WHERE timestamp < now()"
                       " - make_interval(days => %s)", (RETENTION_USAGE_JOURS,))
        return r.rowcount


async def boucle_de_purge():
    """Une passe au demarrage -- c'est elle qui rattrape une base deja grosse
    -- puis une par jour. Le conteneur peut tourner des mois sans redemarrer :
    purger seulement au demarrage ne plafonnerait rien entre deux."""
    while True:
        try:
            # to_thread : psycopg est synchrone, et un DELETE sur une grosse
            # table bloquerait la boucle d'evenements le temps de s'executer.
            n = await asyncio.to_thread(purger_la_consommation_ancienne)
            if n:
                print(f"[codelab-llm] consommation purgee : {n} ligne(s) de"
                      f" plus de {RETENTION_USAGE_JOURS} jours", flush=True)
        except Exception as e:                                    # noqa: BLE001
            # Une purge ratee ne doit pas emporter le service : le routage
            # continue, et la passe suivante reessaiera.
            print(f"[codelab-llm] purge impossible : {e}", flush=True)
        await asyncio.sleep(PURGE_INTERVALLE)


@asynccontextmanager
async def cycle_de_vie(_app):
    """Ouverture et fermeture propres. @app.on_event est deprecie depuis
    FastAPI 0.93 et disparaitra ; le contrat lifespan est son remplacant."""
    global _client
    _client = httpx.AsyncClient(
        timeout=httpx.Timeout(DELAI_FOURNISSEUR, connect=DELAI_CONNEXION))
    try:
        preparer_schema()
    except Exception as e:                                        # noqa: BLE001
        # Le service demarre quand meme : la sonde /health dira ce qui ne va
        # pas, et le panneau la lit. Refuser de demarrer ferait redemarrer le
        # conteneur en boucle sans jamais afficher la raison.
        print(f"[codelab-llm] schema non prepare : {e}", flush=True)
    purge = asyncio.create_task(boucle_de_purge())
    try:
        yield
    finally:
        purge.cancel()
        await _client.aclose()
        if _pool is not None:
            _pool.close()


app = FastAPI(title="CodeLab LLM", docs_url=None, redoc_url=None,
              lifespan=cycle_de_vie)


def empreinte(valeur_brute):
    return hashlib.sha256(valeur_brute.encode()).hexdigest()


# ----------------------------- identite -----------------------------

def _b64(valeur_brute):
    return base64.urlsafe_b64decode(valeur_brute + "=" * (-len(valeur_brute) % 4))


def identite_sso(assertion):
    """Verifie l'assertion Ed25519 signee par le panneau.

    Rend l'identite, ou None. Aucune exception ne sort d'ici : une assertion
    malformee est une assertion refusee, pas une erreur 500.
    """
    if not assertion:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        publique = valeur("CODELAB_SSO_PUBLIC_KEY")
        morceaux = assertion.split(".")
        if not publique or len(morceaux) != 3:
            return None
        entete = json.loads(_b64(morceaux[0]).decode())
        charge = json.loads(_b64(morceaux[1]).decode())
        if entete.get("alg") != "EdDSA" or entete.get("typ") != "JWT":
            return None
        # Sans audience configuree, AUCUNE assertion n'est acceptee : deux
        # chaines vides seraient egales, et une assertion sans destinataire
        # ouvrirait le service. Une audience vide n'autorise personne.
        if not SSO_AUDIENCE:
            return None
        if charge.get("iss") != SSO_EMETTEUR or charge.get("aud") != SSO_AUDIENCE:
            return None
        if not charge.get("sub"):
            return None
        # Une tolerance d'horloge, dans les deux sens : ces conteneurs n'ont
        # pas de NTP commun, et un decalage de quelques secondes refusait des
        # assertions parfaitement valides.
        maintenant = int(time.time())
        if int(charge.get("exp", 0)) + 30 <= maintenant:
            return None
        if int(charge.get("iat", 0)) - 30 > maintenant:
            return None
        Ed25519PublicKey.from_public_bytes(_b64(publique)).verify(
            _b64(morceaux[2]), (morceaux[0] + "." + morceaux[1]).encode("ascii"))
        return {"user_id": str(charge["sub"]), "app_id": SSO_AUDIENCE}
    except (ImportError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def identite_par_cle(autorisation):
    """La cle CodeLab, enregistree en base et revocable."""
    if not autorisation or not autorisation.startswith("Bearer "):
        return None
    with pool().connection() as cx:
        ligne = cx.execute(
            "SELECT user_id, app_id, id FROM llm_api_keys "
            "WHERE key_hash=%s AND revoked_at IS NULL",
            (empreinte(autorisation[7:].strip()),)).fetchone()
    if not ligne:
        return None
    # key_id voyage avec l'identite jusqu'a l'ecriture de la consommation :
    # c'est le seul endroit ou l'on sait encore QUELLE cle a ete presentee.
    return {"user_id": ligne[0], "app_id": ligne[1], "key_id": ligne[2]}


def identite(autorisation, assertion=None, utilisateur_transmis=None):
    """Qui parle. Deux preuves possibles, et AUCUN en-tete de confiance.

    Une version anterieure acceptait n'importe quelle requete portant un
    en-tete d'utilisateur, au motif que l'interface de chat etait seule sur
    le reseau Docker. Elle ne l'etait pas : toutes les applications deployees
    par le panneau y sont aussi, et ce sont elles qui executent du code
    quelconque. N'importe laquelle pouvait donc se faire passer pour
    n'importe qui.

    L'en-tete reste lu, mais il ne PROUVE plus rien : il ne sert qu'a
    attribuer la consommation au bon utilisateur, une fois que la cle de
    l'application, elle, a ete verifiee.
    """
    sso = identite_sso(assertion)
    if sso:
        return sso
    par_cle = identite_par_cle(autorisation)
    if not par_cle:
        raise HTTPException(401, "Identite CodeLab requise (assertion SSO ou cle).")
    if utilisateur_transmis:
        par_cle = dict(par_cle, user_id=str(utilisateur_transmis)[:120])
    return par_cle


def limiter(qui):
    """Une cadence par couple (utilisateur, application).

    Le suivi est BORNE : un dictionnaire qui grandit a chaque nouvel
    utilisateur et ne se vide jamais est une fuite de memoire, lente mais
    certaine, dans un service cense tourner des mois.
    """
    maintenant = time.monotonic()
    cle = (qui["user_id"], qui["app_id"])
    file = _appels[cle]
    while file and file[0] <= maintenant - 60:
        file.popleft()
    if len(file) >= APPELS_PAR_MINUTE:
        raise HTTPException(429, "Trop de requetes : reessaie dans une minute.")
    file.append(maintenant)
    if len(_appels) > CLIENTS_SUIVIS_MAX:
        for autre in [c for c, f in _appels.items() if not f][:CLIENTS_SUIVIS_MAX]:
            _appels.pop(autre, None)


# ---------------------------- comptabilite ----------------------------

def noter_consommation(qui, demande, reel, jetons, latence, fournisseur=None,
                       repli=None):
    # Deux noms pour la meme chose : /chat/completions compte en
    # prompt_tokens / completion_tokens, /responses en input_tokens /
    # output_tokens. Lire un seul des deux jeux ferait une comptabilite a
    # zero sur la moitie du trafic -- et une consommation a zero ne se
    # remarque pas, elle se croit.
    entree = int(jetons.get("prompt_tokens") or jetons.get("input_tokens") or 0)
    sortie = int(jetons.get("completion_tokens") or jetons.get("output_tokens") or 0)
    if fournisseur is None:
        fournisseur = reel.split("/", 1)[0] if "/" in (reel or "") else None
    if repli is None:
        repli = reel != demande
    try:
        with pool().connection() as cx:
            cx.execute(
                "INSERT INTO llm_usage (user_id, app_id, key_id, requested_model,"
                " actual_model, input_tokens, output_tokens, total_tokens,"
                " latency_ms, provider, fallback)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (qui["user_id"], qui["app_id"], qui.get("key_id"), demande, reel,
                 entree, sortie, entree + sortie, latence, fournisseur,
                 repli))
    except Exception as e:                                        # noqa: BLE001
        # La comptabilite ne doit jamais faire perdre une reponse deja
        # obtenue : on le dit dans le journal, et l'utilisateur garde son
        # texte.
        print(f"[codelab-llm] consommation non enregistree : {e}", flush=True)


# ------------------------------- routes -------------------------------

@app.get("/health")
def sante():
    """Ce que le panneau et le diagnostic viennent lire.

    « ok » ne veut rien dire tout seul : on dit aussi si la cle du
    fournisseur est posee et si la base repond, parce que ce sont les deux
    seules pannes possibles de ce service.
    """
    base = True
    try:
        with pool().connection() as cx:
            cx.execute("SELECT 1")
    except Exception:                                             # noqa: BLE001
        base = False
    return {"status": "ok" if base else "degrade", "service": "codelab-llm",
            "base": base,
            "cle_fournisseur": any(cle_de(f) for f in FOURNISSEURS),
            "fournisseurs": sorted(f for f in FOURNISSEURS if cle_de(f)),
            "modeles": sorted(modeles())}



def raison_amont(corps, cle=""):
    """Ce que le fournisseur reproche a la requete, lisible et sans secret.

    Sans elle, un refus du fournisseur -- modele inconnu, credit epuise,
    cle invalide -- arrivait dans l'application sans un mot sur la cause. On ne garde que « error.message » du JSON
    renvoye, borne en longueur, et la cle du fournisseur en est retiree si
    jamais elle y figurait.
    """
    def message_de(doc):
        # OpenAI/OpenRouter/Groq : {"error": {"message"}} ; Gemini : parfois
        # une liste de ces objets ; Mistral : {"message"} ou {"detail"}.
        if isinstance(doc, list):
            return message_de(doc[0]) if doc else ""
        if not isinstance(doc, dict):
            return ""
        erreur = doc.get("error")
        if isinstance(erreur, dict):
            return erreur.get("message") or ""
        if isinstance(erreur, str):
            return erreur
        return doc.get("message") or (str(doc["detail"]) if doc.get("detail") else "")

    try:
        texte = message_de(json.loads(corps))
    except ValueError:
        texte = ""
    texte = " ".join(str(texte).split())
    # TOUTES les cles connues, pas seulement celle du fournisseur appele :
    # ce message part vers le client. Avant la coupe, qui pourrait sinon
    # laisser passer la moitie d'une cle.
    for secret in {cle, *(cle_de(f) for f in FOURNISSEURS)}:
        if secret:
            texte = texte.replace(secret, "***")
    return texte[:300] or "raison non precisee"


@app.get("/v1/models")
def liste_modeles():
    return {"object": "list", "data": [
        {"id": nom, "object": "model", "owned_by": "codelab"}
        for nom in sorted(modeles())]}


def _payload_valide(corps, table):
    try:
        charge = json.loads(corps)
    except json.JSONDecodeError as e:
        raise HTTPException(400, "Corps JSON invalide.") from e
    modele = charge.get("model")
    if modele not in table:
        raise HTTPException(400, f"Modele inconnu : {modele}.")
    messages = charge.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "« messages » doit etre une liste non vide.")
    return charge, modele


# ------------------------ la chaine de fournisseurs ------------------------
#
# Un fournisseur qui vient de dire « quota epuise » (429) ou « credit
# insuffisant » (402) est mis AU REPOS le temps qu'il indique (Retry-After),
# une minute sinon : redemander a chaque appel a celui qui vient de refuser ajouterait son aller-retour a chaque reponse.
# Si toute la chaine est au repos, on la retente quand meme : mieux vaut un
# refus de plus qu'un refus sans avoir essaye.
REPOS_PAR_DEFAUT = 60
REPOS_MAX = 3600
_repos = {}


def _mettre_au_repos(fid, reponse):
    if reponse.status_code not in (402, 429):
        return
    try:
        attente = float(reponse.headers.get("retry-after") or REPOS_PAR_DEFAUT)
    except ValueError:
        attente = REPOS_PAR_DEFAUT
    _repos[fid] = time.monotonic() + min(max(attente, 5), REPOS_MAX)


def _au_repos(fid):
    return _repos.get(fid, 0) > time.monotonic()


async def modeles_gratuits():
    """Les modeles gratuits d'OpenRouter qui acceptent les outils, les plus
    grands contextes d'abord. Catalogue relu au plus une fois par heure ;
    s'il est injoignable, la derniere liste connue sert encore."""
    if _gratuits["liste"] and time.monotonic() - _gratuits["quand"] < GRATUITS_DUREE:
        return _gratuits["liste"][:GRATUITS_ESSAYES]
    try:
        reponse = await _client.get(base_de("openrouter") + "/models",
                                    headers={"Authorization": f"Bearer {cle_de('openrouter')}"})
        donnees = reponse.json().get("data") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        print("[codelab-llm] catalogue OpenRouter illisible.", flush=True)
        return _gratuits["liste"][:GRATUITS_ESSAYES]
    gratuits = [m for m in donnees if isinstance(m, dict)
                and str(m.get("id", "")).endswith(":free")
                and "tools" in (m.get("supported_parameters") or [])]
    gratuits.sort(key=lambda m: -(m.get("context_length") or 0))
    if gratuits:
        _gratuits.update(quand=time.monotonic(), liste=[m["id"] for m in gratuits])
    return _gratuits["liste"][:GRATUITS_ESSAYES]


async def _ouvrir(demande, table, fabrique):
    """Essaie la chaine de « demande » jusqu'au premier fournisseur qui
    accepte. Renvoie (fournisseur, modele, reponse ouverte en flux, repli).

    fabrique(fournisseur, modele) donne (chemin, corps JSON). La reponse est
    TOUJOURS ouverte en flux : c'est ce qui permet de lire le code HTTP avant
    de s'engager, et donc de passer au suivant sans avoir rien envoye au
    client.
    """
    essais = []
    for fid, modele in chaine(demande, table):
        if fid == "openrouter" and modele == GRATUITS:
            essais += [(fid, m) for m in await modeles_gratuits()]
        else:
            essais.append((fid, modele))
    if not essais:
        raise HTTPException(503, f"Aucune cle de fournisseur posee pour {demande} : "
                                 "en ajouter une dans le panneau (Cles et secrets).")
    actifs = [e for e in essais if not _au_repos(e[0])] or essais
    raisons = []
    # Un fournisseur qui dit « quota epuise » (429) ou « credit insuffisant »
    # (402) le dit pour TOUS ses modeles : les suivants de la chaine chez lui
    # sont sautes dans cette requete, au lieu d'echouer un par un.
    epuises = set()
    for fid, modele in actifs:
        if fid in epuises:
            continue
        cle = cle_de(fid)
        chemin, corps = fabrique(fid, modele)
        requete = _client.build_request(
            "POST", base_de(fid) + chemin, json=corps,
            headers={"Authorization": f"Bearer {cle}",
                     "Content-Type": "application/json"})
        try:
            reponse = await _client.send(requete, stream=True)
        except httpx.HTTPError:
            print(f"[codelab-llm] {fid} injoignable.", flush=True)
            raisons.append(f"{fid} : injoignable")
            continue
        if reponse.status_code >= 400:
            raison = raison_amont(await reponse.aread(), cle)
            await reponse.aclose()
            _mettre_au_repos(fid, reponse)
            if reponse.status_code in (402, 429):
                epuises.add(fid)
            print(f"[codelab-llm] {fid} ({modele}) : HTTP {reponse.status_code} : "
                  f"{raison}", flush=True)
            raisons.append(f"{fid} : HTTP {reponse.status_code} : {raison}")
            continue
        return fid, modele, reponse, bool(raisons)
    raise HTTPException(502, "Aucun fournisseur n'a accepte la requete -- "
                             + " | ".join(raisons))


# ---------------------------- /v1/chat/completions ----------------------------
#
# Les applications. Meme chaine, meme repli ; le document traverse tel quel,
# seul le modele est remplace -- et le nom LOGIQUE est rendu au client :
# c'est celui qu'il a demande, et celui qu'il redemandera.
@app.post("/v1/chat/completions")
async def completions(request: Request,
                      authorization: str | None = Header(default=None),
                      x_codelab_auth: str | None = Header(default=None),
                      x_codelab_user_id: str | None = Header(default=None)):
    qui = identite(authorization, x_codelab_auth, x_codelab_user_id)
    limiter(qui)

    corps = await request.body()
    if len(corps) > CORPS_MAX:
        raise HTTPException(413, "Requete trop volumineuse.")
    table = modeles()
    charge, demande = _payload_valide(corps, table)
    debut = time.monotonic()

    fid, modele, reponse, repli = await _ouvrir(
        demande, table,
        lambda fid, modele: ("/chat/completions", dict(charge, model=modele)))

    if charge.get("stream") is not True:
        try:
            data = json.loads(await reponse.aread())
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, f"Reponse illisible de {fid}.")
        finally:
            await reponse.aclose()
        noter_consommation(qui, demande, modele, data.get("usage") or {},
                           int((time.monotonic() - debut) * 1000),
                           fournisseur=fid, repli=repli)
        data["model"] = demande
        return JSONResponse(data)

    async def flux():
        """Le streaming, ligne a ligne, sans rien garder du texte : on ne
        relit les fragments que pour le compte de jetons."""
        jetons = {}
        try:
            async for ligne in reponse.aiter_lines():
                if not ligne.startswith("data:"):
                    continue
                brut = ligne[5:].strip()
                if brut == "[DONE]":
                    break
                try:
                    fragment = json.loads(brut)
                except json.JSONDecodeError:
                    continue
                if isinstance(fragment, dict):
                    jetons = (fragment.get("usage")
                              or (fragment.get("x_groq") or {}).get("usage") or jetons)
                    fragment["model"] = demande
                yield "data: " + json.dumps(fragment, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
        except httpx.HTTPError:
            print(f"[codelab-llm] {fid} : flux interrompu.", flush=True)
            yield ("data: " + json.dumps(
                {"error": {"message": f"Le fournisseur ({fid}) a coupe la reponse.",
                           "type": "upstream"}}, ensure_ascii=False) + "\n\n")
        finally:
            await reponse.aclose()
            noter_consommation(qui, demande, modele, jetons,
                               int((time.monotonic() - debut) * 1000),
                               fournisseur=fid, repli=repli)

    return StreamingResponse(flux(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/v1/usage")
def consommation(authorization: str | None = Header(default=None),
                 x_codelab_auth: str | None = Header(default=None)):
    qui = identite(authorization, x_codelab_auth)
    with pool().connection() as cx:
        lignes = cx.execute(
            "SELECT requested_model, actual_model, provider, SUM(input_tokens),"
            " SUM(output_tokens), SUM(total_tokens), COUNT(*) FROM llm_usage"
            " WHERE user_id=%s AND app_id=%s"
            " GROUP BY requested_model, actual_model, provider"
            " ORDER BY requested_model, provider",
            (qui["user_id"], qui["app_id"])).fetchall()
    return {"user_id": qui["user_id"], "app_id": qui["app_id"], "items": [
        {"requested_model": l[0], "actual_model": l[1], "provider": l[2],
         "input_tokens": l[3], "output_tokens": l[4], "total_tokens": l[5],
         "requests": l[6]} for l in lignes]}


# ------------------------- gestion des cles -------------------------

def _gerer():
    """Creer et revoquer les cles, depuis « docker exec ».

    Une cle n'est affichee qu'une fois : la base n'en garde que l'empreinte,
    donc personne -- pas meme l'administrateur -- ne peut la relire ensuite.
    """
    analyseur = argparse.ArgumentParser(description="Cles API de CodeLab LLM")
    commandes = analyseur.add_subparsers(dest="commande", required=True)
    creer = commandes.add_parser("create-key")
    creer.add_argument("--name", required=True)
    creer.add_argument("--user", required=True)
    creer.add_argument("--app", required=True)
    revoquer = commandes.add_parser("revoke-key")
    revoquer.add_argument("key")
    args = analyseur.parse_args()
    preparer_schema()
    with pool().connection() as cx:
        if args.commande == "create-key":
            brut = "cl_" + secrets.token_urlsafe(32)
            cx.execute("INSERT INTO llm_api_keys (key_hash, key_name, user_id, app_id)"
                       " VALUES (%s,%s,%s,%s)",
                       (empreinte(brut), args.name, args.user, args.app))
            print(brut)
        else:
            r = cx.execute("UPDATE llm_api_keys SET revoked_at=now() WHERE key_hash=%s",
                           (empreinte(args.key),))
            print("revoquee" if r.rowcount else "inconnue")


if __name__ == "__main__":
    _gerer()
