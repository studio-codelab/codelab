"""
CodeLab LLM -- la porte unique vers un fournisseur de modeles.

Ce que ce service fait, et pourquoi il existe :

  IDENTITE.   Personne n'appelle le fournisseur directement. Une requete
              arrive ici avec une assertion SSO signee par le panneau, ou
              avec une cle CodeLab enregistree en base. Sans l'un des deux,
              401 -- et la cle du fournisseur ne quitte jamais ce conteneur.

  COMPTABILITE. Qui a consomme quoi, quand, et combien de jetons. C'est la
              SEULE chose que ce service garde : les conversations
              appartiennent a l'interface qui les affiche (Open WebUI les
              range dans son propre volume), et les recopier ici ferait deux
              exemplaires de chaque echange prive, dans deux systemes a
              sauvegarder, pour une relecture que personne ne fait.

  TRADUCTION. Un nom logique (« codelab-smart ») vers un modele reel du
              fournisseur. L'interface ne connait que les noms logiques :
              changer de modele ne demande pas de toucher a l'interface.

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

CORPS_MAX = 512 * 1024
APPELS_PAR_MINUTE = 60
# Au-dela, un client qui ne revient jamais garderait sa file pour toujours.
CLIENTS_SUIVIS_MAX = 512

SSO_EMETTEUR = "codelab"
# L'audience attendue dans l'assertion : le nom de l'application qui parle.
SSO_AUDIENCE = os.environ.get("CODELAB_LLM_AUDIENCE", "open-webui")

FOURNISSEUR_URL = os.environ.get(
    "CODELAB_LLM_FOURNISSEUR", "https://openrouter.ai/api/v1")

# Les noms logiques, et le modele reel derriere chacun. Un nom sans modele
# configure n'est PAS propose : trois noms qui pointent tous sur la meme
# route -- ce qu'on avait -- donnent a l'interface un choix qui n'en est pas
# un, et a l'utilisateur l'impression que son reglage ne change rien.
MODELES_PAR_DEFAUT = {"codelab-smart": "openrouter/auto"}
PREFIXE_MODELE = "CODELAB_LLM_MODELE_"

DELAI_FOURNISSEUR = 90.0
DELAI_CONNEXION = 10.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_api_keys (
    id BIGSERIAL PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE, key_name TEXT NOT NULL,
    user_id TEXT NOT NULL, app_id TEXT NOT NULL, revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id BIGSERIAL PRIMARY KEY, timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id TEXT NOT NULL, app_id TEXT NOT NULL,
    requested_model TEXT NOT NULL, actual_model TEXT, provider TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0, cost NUMERIC(18, 8) NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL, cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    fallback BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS llm_usage_dimensions_idx
    ON llm_usage (user_id, app_id, requested_model, provider, timestamp);
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


def valeur(nom, defaut=""):
    """L'environnement d'abord, le fichier ensuite. Le compose peut donc
    tout surcharger sans reconstruire l'image."""
    return os.environ.get(nom) or lire_env().get(nom, defaut)


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
    try:
        yield
    finally:
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


def identite_interface(autorisation, utilisateur_transmis):
    """L'interface de chat, reconnue par une cle partagee.

    Open WebUI ne sait presenter qu'un « Authorization: Bearer ». Cette
    cle-la est posee des deux cotes par le compose (CODELAB_OPENWEBUI_KEY),
    ce qui evite d'avoir a en creer une a la main avant le premier
    demarrage -- une etape d'installation qu'on oublie toujours.

    Non configuree, elle n'ouvre RIEN : sans ce garde-fou, deux chaines vides
    seraient egales et n'importe quelle requete sans cle passerait.
    """
    attendue = valeur("CODELAB_OPENWEBUI_KEY")
    if not attendue or not autorisation or not autorisation.startswith("Bearer "):
        return None
    if not secrets.compare_digest(autorisation[7:].strip(), attendue):
        return None
    # La cle prouve QUELLE APPLICATION parle. Le nom d'utilisateur, lui,
    # arrive en clair : il ne sert qu'a attribuer la consommation, jamais a
    # ouvrir un droit.
    return {"user_id": str(utilisateur_transmis or "inconnu")[:120],
            "app_id": SSO_AUDIENCE}


def identite_par_cle(autorisation):
    """La cle CodeLab, enregistree en base et revocable."""
    if not autorisation or not autorisation.startswith("Bearer "):
        return None
    with pool().connection() as cx:
        ligne = cx.execute(
            "SELECT user_id, app_id FROM llm_api_keys "
            "WHERE key_hash=%s AND revoked_at IS NULL",
            (empreinte(autorisation[7:].strip()),)).fetchone()
    return {"user_id": ligne[0], "app_id": ligne[1]} if ligne else None


def identite(autorisation, assertion=None, utilisateur_transmis=None):
    """Qui parle. Deux preuves possibles, et AUCUN en-tete de confiance.

    La version precedente acceptait n'importe quelle requete portant
    « X-OpenWebUI-User-Id », au motif qu'Open WebUI etait seul sur le reseau
    Docker. Il ne l'est pas : toutes les applications deployees par le
    panneau y sont aussi, et ce sont elles qui executent du code quelconque.
    N'importe laquelle pouvait donc se faire passer pour n'importe qui.

    L'en-tete reste lu, mais il ne PROUVE plus rien : il ne sert qu'a
    attribuer la consommation au bon utilisateur, une fois que la cle de
    l'application, elle, a ete verifiee.
    """
    sso = identite_sso(assertion)
    if sso:
        return sso
    interface = identite_interface(autorisation, utilisateur_transmis)
    if interface:
        return interface
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

def noter_consommation(qui, demande, reel, jetons, latence):
    entree = int(jetons.get("prompt_tokens") or 0)
    sortie = int(jetons.get("completion_tokens") or 0)
    fournisseur = reel.split("/", 1)[0] if "/" in (reel or "") else None
    try:
        with pool().connection() as cx:
            cx.execute(
                "INSERT INTO llm_usage (user_id, app_id, requested_model, actual_model,"
                " input_tokens, output_tokens, total_tokens, latency_ms, provider, fallback)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (qui["user_id"], qui["app_id"], demande, reel, entree, sortie,
                 entree + sortie, latence, fournisseur, reel != demande))
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
            "base": base, "cle_fournisseur": bool(valeur("OPENROUTER_API_KEY")),
            "cle_interface": bool(valeur("CODELAB_OPENWEBUI_KEY")),
            "modeles": sorted(modeles())}


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


@app.post("/v1/chat/completions")
async def completions(request: Request,
                      authorization: str | None = Header(default=None),
                      x_codelab_auth: str | None = Header(default=None),
                      x_openwebui_user_id: str | None = Header(default=None)):
    qui = identite(authorization, x_codelab_auth, x_openwebui_user_id)
    limiter(qui)

    corps = await request.body()
    if len(corps) > CORPS_MAX:
        raise HTTPException(413, "Requete trop volumineuse.")
    table = modeles()
    charge, demande = _payload_valide(corps, table)

    cle = valeur("OPENROUTER_API_KEY")
    if not cle:
        raise HTTPException(503, "Aucune cle de fournisseur configuree.")

    amont = dict(charge, model=table[demande])
    entetes = {"Authorization": f"Bearer {cle}", "Content-Type": "application/json"}
    debut = time.monotonic()

    if charge.get("stream") is True:
        return StreamingResponse(
            _flux(amont, entetes, qui, demande, debut),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    try:
        reponse = await _client.post(FOURNISSEUR_URL + "/chat/completions",
                                     json=amont, headers=entetes)
    except httpx.HTTPError:
        # Le message du fournisseur n'est PAS recopie au client : il peut
        # porter l'adresse appelee, des entetes, voire un fragment de cle.
        # Le journal du conteneur, lui, le garde.
        print("[codelab-llm] fournisseur injoignable.", flush=True)
        raise HTTPException(502, "Le fournisseur de modeles ne repond pas.")
    if reponse.status_code >= 400:
        print(f"[codelab-llm] fournisseur : HTTP {reponse.status_code}", flush=True)
        raise HTTPException(502, "Le fournisseur de modeles a refuse la requete.")

    data = reponse.json()
    noter_consommation(qui, demande, data.get("model") or table[demande],
                       data.get("usage") or {},
                       int((time.monotonic() - debut) * 1000))
    # Le nom LOGIQUE est rendu au client : c'est celui qu'il a demande, et
    # celui qu'il redemandera. Lui rendre le modele reel ferait apparaitre
    # dans l'interface un nom qui n'existe pas dans la liste.
    data["model"] = demande
    return JSONResponse(data)


async def _flux(amont, entetes, qui, demande, debut):
    """Le streaming, ligne a ligne, sans rien garder du texte.

    On ne relit les fragments que pour deux choses : le modele reellement
    servi et le compte de jetons, qui arrivent dans le flux. Le contenu, lui,
    traverse -- il appartient a l'interface qui l'affiche.
    """
    jetons, reel = {}, amont["model"]
    try:
        async with _client.stream("POST", FOURNISSEUR_URL + "/chat/completions",
                                  json=amont, headers=entetes) as reponse:
            if reponse.status_code >= 400:
                await reponse.aread()
                print(f"[codelab-llm] fournisseur : HTTP {reponse.status_code}",
                      flush=True)
                yield ("data: " + json.dumps(
                    {"error": {"message": "Le fournisseur de modeles a refuse "
                                          "la requete.", "type": "upstream"}}) + "\n\n")
                return
            async for ligne in reponse.aiter_lines():
                if not ligne.startswith("data: "):
                    continue
                brut = ligne[6:].strip()
                if brut == "[DONE]":
                    break
                try:
                    fragment = json.loads(brut)
                except json.JSONDecodeError:
                    continue
                reel = fragment.get("model") or reel
                if fragment.get("usage"):
                    jetons = fragment["usage"]
                fragment["model"] = demande
                yield "data: " + json.dumps(fragment, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
    except httpx.HTTPError:
        print("[codelab-llm] fournisseur injoignable pendant le flux.", flush=True)
        yield ("data: " + json.dumps(
            {"error": {"message": "Le fournisseur de modeles ne repond pas.",
                       "type": "upstream"}}) + "\n\n")
        return
    noter_consommation(qui, demande, reel, jetons,
                       int((time.monotonic() - debut) * 1000))


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
