"""
CodeLab -- L'APPLICATION « MESSAGES »
---------------------------------------------------------------------------
ROLE        Les messages laisses depuis le hub, et le travail qu'on en tire :
            boite de reception, planning, feuille de route, projets. Une
            application a part entiere, comme le Diagnostic : son dossier
            (/workspace/messages), son processus, sa base Postgres, servie
            par le proxy sur le port des applications.
            Ne fait PAS : envoyer un mail ou une notification (le panneau le
            fait, il sait joindre l'administrateur), ni dire qui a le droit
            d'ecrire sur quelle application (le panneau le sait, et le
            verifie avant de transmettre).
CHARGE PAR  le panneau (application par defaut, lancee a chaque demarrage :
            « python3 app.py »), et la suite de tests du depot.
CONTRAINTES - TOUTE requete porte l'identite signee du panneau
              (X-CodeLab-Auth, verifiee par codelab_auth) : sans elle, 403.
              Trois portes, chacune son role :
                /, /api/...    l'administrateur (par le proxy) ;
                /compte/...    un compte du panneau, pour SES messages (le
                               panneau relaie le hub) ;
                /interne/...   le panneau lui-meme (role « panneau »).
            - N'ecoute que 127.0.0.1 : seul le panneau, dans le meme
              conteneur, la joint.
            - Ne jamais importer flask ailleurs qu'ici : donnees.py se teste
              seul.
STRUCTURE   l'identite, les erreurs, la page, l'API de l'administrateur, le
            relais du hub (/compte), le dialogue avec le panneau (/interne),
            le demarrage.
CHOIX       Le panneau parle a l'application par HTTP, avec un jeton signe
            comme celui du proxy, plutot que par la base : chacun garde ses
            donnees, et aucun des deux n'a le mot de passe de l'autre.
A AMELIORER Une file d'attente cote panneau pour un message ecrit pendant
            que l'application redemarre (aujourd'hui : « reessaie »).
"""
import json
import os
import sys
import urllib.parse

ICI = os.path.dirname(os.path.abspath(__file__))
# psycopg n'est pas dans l'image app-manager : le build l'installe dans
# ./vendor, a cote du code, sans toucher au conteneur.
sys.path.insert(0, os.path.join(ICI, "vendor"))
sys.path.insert(0, ICI)

from flask import Flask, Response, g, jsonify, request  # noqa: E402

import codelab_auth  # noqa: E402
import donnees  # noqa: E402
from donnees import Refus  # noqa: E402

app = Flask(__name__)
# Le plafond d'une requete : la reprise des anciennes donnees (jusqu'a 5 000
# messages de 2 000 caracteres) passe en un seul envoi. Une piece jointe a sa
# propre limite (donnees.PIECE_MAX), et seuls le panneau et l'administrateur
# atteignent ce port.
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

ROLE_ADMIN = "admin"
ROLES_COMPTE = ("admin", "utilisateur")
ROLE_PANNEAU = "panneau"
_etat = {"prete": False}


# -------------------------------- l'identite --------------------------------
#
# POURQUOI : ces donnees sont des mots adresses a l'administrateur, signes de
#   leur auteur. Une application voisine qui joindrait ce port directement,
#   ou un compte utilisateur qui en devinerait l'adresse, ne doit rien lire.
# REGLE : 12 et 19 de SECURITE.md (applications par defaut reservees a
#   l'administrateur ; l'identite ne passe que par le jeton signe).
# DEPEND DE : jeton_sso et jeton_du_panneau (panneau, 02-sessions.py), qui
#   signent pour l'audience « messages » ; peut_voir, qui ferme deja le proxy
#   a un compte utilisateur. Ce controle-ci tient meme si le proxy est
#   contourne.
# ECARTE : croire X-CodeLab-User ou X-CodeLab-Role, que n'importe qui peut
#   ecrire ; une liste d'adresses IP permises (toutes les applications
#   partagent 127.0.0.1).

def _porte(chemin):
    if chemin.startswith("/interne/"):
        return "interne"
    if chemin.startswith("/compte/"):
        return "compte"
    return "admin"


@app.before_request
def verifier_l_identite():
    u = codelab_auth.utilisateur(request.headers)
    porte = _porte(request.path)
    permis = (u is not None and bool(u.get("sub")) and (
        (porte == "interne" and u["role"] == ROLE_PANNEAU)
        or (porte == "compte" and u["role"] in ROLES_COMPTE)
        or (porte == "admin" and u["role"] == ROLE_ADMIN)))
    if not permis:
        return jsonify({"error": "Accès refusé."}), 403
    # POURQUOI : une page d'un autre site ne doit pas faire agir
    #   l'administrateur ici (CSRF). Un formulaire sait poster du texte brut,
    #   que get_json(force=True) lirait ; il ne sait pas poser un en-tete a
    #   lui sans demander la permission au serveur (CORS), qui ne la donne pas.
    # DEPEND DE : appel() et le fetch de index.html, qui posent cet en-tete.
    if (porte == "admin" and request.method not in ("GET", "HEAD")
            and request.headers.get("X-Messages") != "1"):
        return jsonify({"error": "Requête refusée : recharge la page, puis réessaie."}), 403
    g.qui = u["sub"]
    g.role = u["role"]
    # La base se prepare a la premiere requete, pas au lancement : un
    # Postgres qui redemarre en meme temps que la stack ne doit pas faire
    # tomber l'application (le panneau la relancerait en boucle).
    if not _etat["prete"]:
        try:
            donnees.preparer()
        except Exception as e:                                    # noqa: BLE001
            print(f"[messages] base injoignable : {type(e).__name__}: {e}", flush=True)
            return jsonify({"error": "La base de Messages ne répond pas. Réessaie dans "
                                     "un instant."}), 503
        _etat["prete"] = True
    return None


@app.errorhandler(Refus)
def refus(e):
    return jsonify({"error": str(e)}), e.code


def _corps():
    return request.get_json(force=True, silent=True) or {}


# ---------------------------------- la page ----------------------------------

@app.get("/")
def page():
    with open(os.path.join(ICI, "index.html"), encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


@app.get("/icon.svg")
def icone():
    with open(os.path.join(ICI, "icon.svg"), encoding="utf-8") as f:
        return Response(f.read(), mimetype="image/svg+xml",
                        headers={"X-Content-Type-Options": "nosniff"})


# ------------------------- l'API de l'administrateur -------------------------

@app.get("/api/apps")
def api_apps():
    return jsonify({"apps": donnees.lire_applications()})


@app.get("/api/messages")
def api_messages():
    """« apres » rend ce qui est arrive DEPUIS une date : la page le demande
    toutes les vingt secondes pour annoncer un message nouveau."""
    try:
        apres = int(request.args.get("apres") or 0)
    except ValueError:
        apres = 0
    messages = donnees.lire_messages(apres=apres)
    # Les messages retenus d'abord : on les retient pour ne pas avoir a les
    # rechercher.
    messages.sort(key=lambda m: (not m["retenu"], -m["ts"]))
    non_lus, boite = donnees.compter_messages()
    return jsonify({"messages": messages, "non_lus": non_lus, "boite": boite})


@app.post("/api/messages/lus")
def api_messages_lus():
    return jsonify({"ok": True, "lus": donnees.marquer_lus()})


@app.put("/api/messages/ordre")
def api_messages_ordre():
    donnees.ordonner_messages(_corps().get("ordre"))
    return jsonify({"ok": True})


@app.post("/api/messages/grouper")
def api_messages_grouper():
    d = _corps()
    projet = donnees.grouper_messages(d.get("ids"), d.get("projet"), d.get("nouveau_projet"))
    return jsonify({"ok": True, "projet": projet})


@app.put("/api/messages/<ident>")
def api_message_modifier(ident):
    d = _corps()
    donnees.modifier_message(ident, {k: d[k] for k in ("statut", "retenu", "archive", "projet")
                                     if k in d})
    return jsonify({"ok": True})


@app.put("/api/messages/<ident>/lu")
def api_message_lu(ident):
    donnees.modifier_message(ident, {"lu": True})
    return jsonify({"ok": True})


@app.delete("/api/messages/<ident>")
def api_message_supprimer(ident):
    donnees.supprimer_message(ident)
    # Supprimer un mot qui vous est adresse se trace : le journal de
    # l'application (sa fiche, dans le panneau) le garde.
    print(f"[messages] message {ident} supprime par {g.qui}", flush=True)
    return jsonify({"ok": True})


@app.post("/api/messages/<ident>/accepter")
def api_message_accepter(ident):
    tache, projet = donnees.accepter_message(ident, _corps())
    return jsonify({"ok": True, "tache": tache, "projet": projet})


def _servir_piece(p):
    reponse = Response(p["donnees"], mimetype=p["type"])
    reponse.headers["Content-Disposition"] = (
        "inline; filename*=UTF-8''" + urllib.parse.quote(p["nom"]))
    reponse.headers["X-Content-Type-Options"] = "nosniff"
    reponse.headers["Cache-Control"] = "private, max-age=86400"
    # Une image n'execute rien ; on le dit quand meme au navigateur. Un PDF
    # garde son lecteur.
    if p["type"].startswith("image/"):
        reponse.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; sandbox"
    return reponse


@app.get("/api/pieces/<ident>")
def api_piece(ident):
    p = donnees.lire_piece(ident)
    if not p:
        raise Refus("Pièce introuvable.", 404)
    return _servir_piece(p)


@app.get("/api/taches")
def api_taches():
    return jsonify({"taches": donnees.lire_taches()})


@app.post("/api/taches")
def api_tache_creer():
    return jsonify({"ok": True, "id": donnees.creer_tache(_corps())})


@app.put("/api/taches/plan")
def api_taches_plan():
    donnees.planifier(_corps().get("ordre"))
    return jsonify({"ok": True})


@app.put("/api/taches/<ident>")
def api_tache_modifier(ident):
    donnees.modifier_tache(ident, _corps())
    return jsonify({"ok": True})


@app.post("/api/taches/<ident>/scinder")
def api_tache_scinder(ident):
    return jsonify({"ok": True, "taches": donnees.scinder_tache(ident, _corps().get("titres"))})


@app.delete("/api/taches/<ident>")
def api_tache_supprimer(ident):
    donnees.supprimer_tache(ident)
    return jsonify({"ok": True})


@app.post("/api/taches/<ident>/sous-taches")
def api_sous_tache_creer(ident):
    return jsonify({"ok": True, "id": donnees.ajouter_sous_tache(ident, _corps().get("titre"))})


@app.put("/api/taches/<ident>/ordre")
def api_sous_taches_ordre(ident):
    donnees.ordonner_sous_taches(ident, _corps().get("ordre"))
    return jsonify({"ok": True})


@app.put("/api/sous-taches/<sid>")
def api_sous_tache_modifier(sid):
    donnees.modifier_sous_tache(sid, _corps())
    return jsonify({"ok": True})


@app.delete("/api/sous-taches/<sid>")
def api_sous_tache_supprimer(sid):
    donnees.supprimer_sous_tache(sid)
    return jsonify({"ok": True})


@app.get("/api/projets")
def api_projets():
    return jsonify({"projets": donnees.lire_projets()})


@app.post("/api/projets")
def api_projet_creer():
    return jsonify({"ok": True, "id": donnees.creer_projet(_corps())})


@app.put("/api/projets/<ident>")
def api_projet_modifier(ident):
    donnees.modifier_projet(ident, _corps())
    return jsonify({"ok": True})


@app.delete("/api/projets/<ident>")
def api_projet_supprimer(ident):
    donnees.supprimer_projet(ident)
    return jsonify({"ok": True})


@app.get("/api/projets/<cle>/export")
def api_projet_export(cle):
    octets, nom = donnees.exporter_projet(cle)
    return Response(octets, mimetype="application/zip", headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(nom)})


# ------------------------- le relais du hub (/compte) -------------------------
#
# Le hub du panneau ecrit ici au nom du compte connecte : le panneau a deja
# verifie le sujet (une application que ce compte peut ouvrir), le delai entre
# deux messages et la taille du fichier. Le jeton dit QUI ; un compte ne lit
# jamais que ce qu'il a ecrit.

@app.post("/compte/messages")
def compte_message():
    d = _corps()
    m = donnees.nouveau_message(g.qui, d.get("cible"), d.get("app"), d.get("texte"),
                                d.get("pieces") or [])
    return jsonify({"ok": True, "message": m})


@app.post("/compte/pieces")
def compte_piece_deposer():
    return jsonify(donnees.deposer_piece(g.qui, request.headers.get("X-Nom-Fichier"),
                                         request.get_data(cache=False)))


@app.get("/compte/pieces/<ident>")
def compte_piece_lire(ident):
    """A qui l'a deposee, ou a l'administrateur. Une piece d'un autre compte
    repond comme une piece qui n'existe pas."""
    p = donnees.lire_piece(ident)
    if not p or (g.role != ROLE_ADMIN and p["qui"] != g.qui):
        raise Refus("Pièce introuvable.", 404)
    return _servir_piece(p)


@app.get("/compte/messages")
def compte_messages():
    return jsonify({"messages": donnees.messages_suivis(g.qui)})


# ----------------------- le dialogue avec le panneau -----------------------

@app.post("/interne/synchroniser")
def interne_synchroniser():
    """Le panneau donne la liste des applications, et reprend ce qu'il
    annonce : le compte des non lus (la tuile du hub) et l'etape de chaque
    message (les notifications de suivi)."""
    d = _corps()
    if isinstance(d.get("applications"), list):
        donnees.ecrire_applications(d["applications"])
    non_lus, boite = donnees.compter_messages()
    suivis = {}
    for m in donnees.messages_suivis():
        suivis.setdefault(m["qui"], []).append(
            {"id": m["id"], "etape": m["suivi"]["etape"],
             "extrait": (m["texte"] or "").strip().replace("\n", " ")[:120]})
    return jsonify({"non_lus": non_lus, "boite": boite, "suivis": suivis})


@app.get("/interne/nouveaux")
def interne_nouveaux():
    """Ce qui est arrive depuis une date : le panneau l'annonce dans son coin."""
    try:
        apres = int(request.args.get("apres") or 0)
    except ValueError:
        apres = 0
    return jsonify({"messages": [{"id": m["id"], "ts": m["ts"], "qui": m["qui"]}
                                 for m in donnees.lire_messages(apres=apres)]})


@app.post("/interne/oublier")
def interne_oublier():
    return jsonify({"ok": True, "effaces": donnees.oublier_comptes(_corps().get("comptes"))})


@app.post("/interne/importer")
def interne_importer():
    return jsonify({"presents": donnees.importer(_corps())})


@app.post("/interne/importer/piece")
def interne_importer_piece():
    try:
        meta = json.loads(request.headers.get("X-Piece") or "{}")
    except ValueError:
        meta = {}
    if not all(isinstance(meta.get(k), str) and meta.get(k) for k in ("id", "nom", "type")):
        raise Refus("Pièce mal décrite.")
    donnees.importer_piece(meta, request.get_data(cache=False))
    return jsonify({"ok": True})


# -------------------------------- demarrage --------------------------------

if __name__ == "__main__":
    # 127.0.0.1 : le panneau relaie depuis le meme conteneur, et rien d'autre
    # ne doit joindre le port interne (voir ECOUTE_LOCALE dans le panneau).
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), threaded=True)
