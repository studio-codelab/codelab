"""codelab_auth -- se connecter avec CodeLab, sans connexions dans tous les sens.

Depose par « codelab create », tenu a jour par « codelab update ». Aucune dependance : la signature Ed25519 est
verifiee par la bibliotheque « cryptography » si elle est installee, sinon
par l'implementation de reference (RFC 8032) incluse plus bas.

LA CONNEXION SILENCIEUSE, dans cet ordre, a chaque requete :
  1. la personne a deja une session dans l'application : on continue ;
  2. sinon, elle est connectee a CodeLab (jeton signe X-CodeLab-Auth,
     verifie ici) : on retrouve ou on cree son compte local, et on ouvre
     la session de l'application -- sans formulaire ;
  3. sinon : le formulaire classique de l'application (connexion ou
     creation de compte). Rien ne change pour ces visiteurs.

Exemple (Flask) :

    from flask import Flask, request, session, redirect
    import codelab_auth

    @app.before_request
    def reconnaitre():
        codelab_auth.connexion_silencieuse(session, request.headers, trouver_ou_creer)

    def trouver_ou_creer(u):
        # u = {"sub", "nom", "role", "email", "email_verified"}
        # Retrouver par u["sub"] (colonne codelab_sub). Ne relier un compte
        # local existant par l'adresse QUE si u["email_verified"] est vrai.
        ...
        return id_utilisateur_local

    # Le bouton « Se connecter avec CodeLab » : une route de l'application
    # qui leve la pause d'apres-deconnexion, puis part vers CodeLab.
    @app.get("/connexion-codelab")
    def connexion_codelab():
        codelab_auth.reprendre(session)
        return redirect(codelab_auth.lien_connexion(request.headers, "/"))
    # Se deconnecter de l'application seulement :
    #   codelab_auth.deconnecter(session)
    # ... et de CodeLab aussi :
    #   redirect(codelab_auth.lien_deconnexion(request.headers, "/"))

TROIS REGLES, deja tenues par ce module :
  - ne jamais croire X-CodeLab-User seul : seul le jeton signe compte ;
  - le jeton ne vit que 60 secondes : c'est un laissez-passer, pas une
    session -- l'application ouvre la sienne ;
  - apres une deconnexion de l'application, ne pas reconnecter aussitot.
"""

import base64
import hashlib
import json
import os
import time
import urllib.parse

CLE_SESSION = "utilisateur"
PAUSE = "codelab_pause"


# ------------------------------- le jeton -------------------------------

def _b64(texte):
    return base64.urlsafe_b64decode(texte + "=" * (-len(texte) % 4))


def _entete(entetes, nom):
    """Lit un en-tete dans un objet Flask/Django/Starlette ou un dict."""
    if entetes is None:
        return ""
    v = entetes.get(nom) if hasattr(entetes, "get") else None
    if v is None and isinstance(entetes, dict):
        bas = nom.lower()
        for k, val in entetes.items():
            if str(k).lower() == bas:
                return val or ""
    return v or ""


def utilisateur(entetes):
    """La personne connectee a CodeLab, ou None.

    Verifie la signature, l'emetteur, l'application visee et l'expiration.
    Renvoie {"sub", "nom", "role", "email", "email_verified"}.
    """
    jeton = _entete(entetes, "X-CodeLab-Auth")
    publique = os.environ.get("CODELAB_SSO_PUBLIC_KEY", "")
    if not jeton or not publique:
        return None
    try:
        h, p, s = jeton.split(".")
        if not _verifier(_b64(publique), (h + "." + p).encode("ascii"), _b64(s)):
            return None
        c = json.loads(_b64(p))
    except (ValueError, TypeError):
        return None
    if c.get("iss") != os.environ.get("CODELAB_SSO_ISSUER", "codelab"):
        return None
    if c.get("aud") != os.environ.get("CODELAB_SSO_AUDIENCE"):
        return None
    if int(c.get("exp", 0)) < int(time.time()):
        return None
    return {"sub": c.get("sub"), "nom": c.get("preferred_username") or c.get("sub"),
            "role": c.get("role", ""), "email": c.get("email", ""),
            "email_verified": bool(c.get("email_verified"))}


# ------------------------- la connexion silencieuse -------------------------

def connexion_silencieuse(session, entetes, trouver_ou_creer, cle=CLE_SESSION):
    """Ouvre la session de l'application d'apres l'identite CodeLab.

    session          : la session de l'application (dict-like : Flask...).
    trouver_ou_creer : fonction(u) -> identifiant local (ou None pour refuser).
    Renvoie l'identifiant local de la session, ou None (visiteur anonyme :
    l'application montre alors son formulaire classique).
    """
    if session.get(cle) is not None:
        return session.get(cle)
    if session.get(PAUSE):
        return None
    u = utilisateur(entetes)
    if u is None:
        return None
    ident = trouver_ou_creer(u)
    if ident is not None:
        session[cle] = ident
    return ident


def deconnecter(session, cle=CLE_SESSION):
    """Ferme la session de l'APPLICATION, sans reconnecter aussitot.

    CodeLab reste connecte : sans la pause, l'etape 2 rouvrirait la session
    a la requete suivante. Le lien « Se connecter avec CodeLab » la leve.
    """
    session.pop(cle, None)
    session[PAUSE] = True


def reprendre(session):
    """A appeler quand la personne clique « Se connecter avec CodeLab »."""
    session.pop(PAUSE, None)


# --------------------------------- liens ---------------------------------

def _panneau(entetes):
    """L'adresse du panneau, posee par le proxy de CodeLab (jamais par le
    navigateur : le proxy retire tout en-tete X-CodeLab-* venu du client)."""
    p = _entete(entetes, "X-CodeLab-Panneau")
    if p.startswith(("http://", "https://", "/")):
        return p if p.endswith("/") else p + "/"
    return "/"


def _chemin(chemin):
    return urllib.parse.quote(str(chemin or "/").lstrip("/"), safe="/")


def lien_connexion(entetes, chemin="/"):
    """Le lien « Se connecter avec CodeLab » : connexion, puis retour ici."""
    app = os.environ.get("CODELAB_SSO_AUDIENCE", "")
    suite = "/ouvrir.app/" + urllib.parse.quote(app, safe="") + "/" + _chemin(chemin)
    return _panneau(entetes) + "login?suite=" + urllib.parse.quote(suite, safe="/")


def lien_deconnexion(entetes, chemin="/"):
    """Se deconnecter de CodeLab aussi (confirmation demandee), puis retour ici."""
    app = os.environ.get("CODELAB_SSO_AUDIENCE", "")
    return (_panneau(entetes) + "deconnexion.html?app=" + urllib.parse.quote(app, safe="")
            + "&chemin=" + urllib.parse.quote(_chemin(chemin), safe=""))


# ------------------------- Ed25519 (verification) -------------------------
#
# cryptography d'abord ; sinon l'implementation de reference de la RFC 8032
# (section 6). Lente (quelques millisecondes), mais elle ne sert qu'a
# l'ouverture d'une session, pas a chaque requete.

def _verifier(publique, message, signature):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return _ed25519_verifier(publique, message, signature)
    try:
        Ed25519PublicKey.from_public_bytes(publique).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_RACINE_M1 = pow(2, (_P - 1) // 4, _P)


def _ajouter(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F, G * H, F * G, E * H)


def _multiplier(s, pt):
    r = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            r = _ajouter(r, pt)
        pt = _ajouter(pt, pt)
        s >>= 1
    return r


def _egaux(a, b):
    return ((a[0] * b[2] - b[0] * a[2]) % _P == 0
            and (a[1] * b[2] - b[1] * a[2]) % _P == 0)


def _retrouver_x(y, signe):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if signe else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _RACINE_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != signe:
        x = _P - x
    return x


def _decompresser(octets):
    if len(octets) != 32:
        return None
    y = int.from_bytes(octets, "little")
    signe = y >> 255
    y &= (1 << 255) - 1
    x = _retrouver_x(y, signe)
    return None if x is None else (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _retrouver_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _ed25519_verifier(publique, message, signature):
    if len(publique) != 32 or len(signature) != 64:
        return False
    a = _decompresser(publique)
    r = _decompresser(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + publique + message).digest(), "little") % _Q
    return _egaux(_multiplier(s, _G), _ajouter(r, _multiplier(h, a)))
