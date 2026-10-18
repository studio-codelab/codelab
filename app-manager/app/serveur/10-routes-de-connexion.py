# CodeLab -- LE PANNEAU, ROUTES DE CONNEXION
# ---------------------------------------------------------------------------
# ROLE        les routes de connexion, des cles d'acces et des invitations.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   routes d'auth, routes des cles d'acces, invitations.
#

# ------------------------------ auth --------------------------------

def admin_2fa_obligatoire():
    """Second facteur obligatoire pour l'administrateur (par defaut : oui).

    APP_MANAGER_ADMIN_2FA=0 dans le compose est la marche arriere, le jour
    ou le telephone qui portait le facteur est perdu : elle ne passe pas par
    le panneau, donc reste possible quand on en est enferme dehors.
    """
    return (os.environ.get("APP_MANAGER_ADMIN_2FA", "1").strip().lower()
            not in ("0", "non", "false", "no"))


def _ouvrir_session_admin():
    session.pop("inscription_depuis", None)
    session.pop("totp_candidat", None)
    session.pop("totp_inscription", None)
    session.pop("totp_uri", None)
    session.permanent = True
    session["authed"] = True
    session["ouverte"] = int(time.time())
    jeton_session()
    session["role"] = ROLE_ADMIN
    session["utilisateur"] = NOM_ADMIN
    journaliser("connexion", qui=NOM_ADMIN, role=ROLE_ADMIN, ip=_adresse_client())
    signaler_connexion_admin()
    return jsonify({"ok": True, "role": ROLE_ADMIN})


def _inscrire_totp_admin(candidat):
    """Fin de l'inscription forcee du facteur de l'administrateur."""
    global _totp_secret
    # Le verrou reseau, de nouveau : entre les deux etapes, on a pu changer
    # de reseau, et c'est ici que la session nait.
    hors = refus_admin_hors_reseau()
    if hors:
        return jsonify({"error": hors}), 403
    if totp_actif():
        return jsonify({"error": "Un second facteur a déjà été enregistré. "
                                 "Recommence la connexion."}), 409
    if not ecrire_bloc_panneau(admin_password(), flask_app.secret_key, candidat):
        return jsonify({"error": "credentials.env n'a pas pu être écrit : rien "
                                 "n'a été enregistré."}), 500
    _totp_secret = candidat
    return _ouvrir_session_admin()


# ALERTE DE CONNEXION. Une connexion administrateur depuis une adresse jamais
# vue part par mail a l'adresse d'alerte : si ce n'est pas toi, tu le sais
# tout de suite, et pas au prochain coup d'oeil sur le journal. Les adresses
# deja vues (les 20 dernieres) ne declenchent rien, pour ne pas en faire du
# bruit qu'on finit par ignorer.
ADMIN_IPS_CONNUES_MAX = 20


def signaler_connexion_admin():
    ip = _client_ip()
    try:
        connues = list(reglage("ips_admin_connues", []) or [])
    except Exception:                                             # noqa: BLE001
        return
    if ip in connues:
        return
    premiere = not connues
    connues = (connues + [ip])[-ADMIN_IPS_CONNUES_MAX:]
    try:
        ecrire_reglage("ips_admin_connues", connues)
    except (OSError, RuntimeError):
        return
    destinataires = alertes_admin()
    if premiere or not destinataires:
        # La toute premiere connexion n'a rien d'inhabituel : c'est
        # l'installation.
        return
    quand = time.strftime("%d/%m/%Y %H:%M", time.localtime())
    threading.Thread(target=envoyer_avec_repli, daemon=True, args=(
        "[CodeLab] connexion administrateur depuis une nouvelle adresse",
        f"Une session d'administration vient de s'ouvrir le {quand}, depuis {ip}, "
        "une adresse jamais vue jusqu'ici.\n\n"
        "Si c'est toi, rien a faire. Sinon, change tout de suite le mot de passe "
        "d'administration (credentials.env), retire les cles d'acces inconnues et "
        "regarde le journal des connexions.\n\n"
        "-- CodeLab, panneau de gestion des applications", destinataires),
        kwargs={"sensible": True}).start()


@flask_app.post("/login")
def login_submit():
    """La connexion par mot de passe, puis le second facteur.

    Une seule route pour toutes les etapes : la page renvoie le meme
    formulaire, complete du code ou du moyen choisi, jusqu'a ce que la
    reponse porte « ok ». Les deux parcours different trop pour se partager
    le code : l'administrateur (_connexion_admin) n'existe pas dans la table
    des comptes et ne se connecte que du reseau local ; un compte utilisateur
    (_connexion_utilisateur, puis _second_facteur_utilisateur) a toujours un
    second facteur.

    REGLES (SECURITE.md) : 1 et 5 (jamais sur le seul mot de passe), 6 (tout
    echec passe par register_failed_attempt, que rate_limited lit ici), 9
    (compte suspendu), 10 (meme reponse pour un compte inconnu).
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    d = request.get_json(force=True, silent=True) or request.form
    pw = (d.get("password") or "").strip()
    nom = (d.get("nom") or "").strip().lower()

    # L'identifiant est obligatoire, administrateur compris : « admin » se
    # tape comme n'importe quel nom. Un champ vide qui ouvrait le compte le
    # plus puissant etait un raccourci a deviner pour tout le monde.
    if not nom:
        return jsonify({"error": "Renseigne ton identifiant."}), 400
    if nom == NOM_ADMIN:
        return _connexion_admin(d, pw)
    return _connexion_utilisateur(d, nom, pw)


def _connexion_admin(d, pw):
    """Le compte administrateur : mot de passe de credentials.env, reseau
    local seulement, second facteur obligatoire (sauf APP_MANAGER_ADMIN_2FA=0)."""
    # Avant meme de regarder le mot de passe : rien ne sert de laisser
    # essayer -- et surtout, aucune session d'administration ne doit
    # naitre hors du reseau local, pas meme une seconde.
    hors = refus_admin_hors_reseau()
    if hors:
        # Compte comme une tentative ratee, au meme titre qu'un mot de
        # passe faux. Sans cela le refus serait gratuit : on pourrait le
        # marteler sans jamais etre limite, et chaque appel ecrivant une
        # ligne de journal, la rotation finirait par chasser l'historique
        # reel -- une facon discrete d'effacer ses traces.
        register_failed_attempt()
        journaliser("refus", qui=NOM_ADMIN, motif="hors reseau local",
                    ip=_adresse_client())
        return jsonify({"error": hors}), 403
    real = admin_password()
    # compare_digest plutot que "==" : la comparaison de chaines s'arrete
    # au premier caractere different, et la duree de la reponse renseigne
    # alors sur la longueur du prefixe correct.
    if not (real and pw and secrets.compare_digest(pw, real)):
        register_failed_attempt()
        journaliser("echec", qui=NOM_ADMIN, motif="mot de passe", ip=_adresse_client())
        return jsonify({"error": avec_essais_restants("Identifiants incorrects.")}), 401

    # Le code a six chiffres, quand la double authentification est active.
    # Une tentative ratee ici compte comme une tentative ratee tout court :
    # sinon le second facteur serait forcable sans limite une fois le mot
    # de passe connu, ce qui le viderait de son sens.
    # SECOND FACTEUR OBLIGATOIRE pour l'administrateur : son compte
    # ouvre tout, Dagster (donc l'execution de code) et Mailpit (donc
    # tous les codes et liens envoyes) compris. Sans facteur enregistre,
    # le mot de passe juste mene a l'inscription d'une application, pas a
    # une session. Avec Face ID seulement, le mot de passe seul est
    # refuse : l'inscription d'une application le contournerait.
    cles_admin = passkeys_du_compte(NOM_ADMIN)
    if admin_2fa_obligatoire() and not totp_actif() and not cles_admin:
        candidat = totp_nouveau_secret()
        session["totp_candidat"] = candidat
        session["totp_inscription"] = NOM_ADMIN
        session["totp_uri"] = totp_uri(candidat)
        session["inscription_depuis"] = int(time.time())
        # Face ID d'abord quand le serveur le permet : un geste, rien a
        # recopier. Le code a six chiffres reste propose a cote.
        return jsonify({"inscription": True, "secret": candidat,
                        "uri": session["totp_uri"], "compte": NOM_ADMIN,
                        "qr": "/qr/totp.svg",
                        "passkey": passkeys_disponibles()
                        and not passkey_contexte()[2]})

    # LE SECOND FACTEUR, SUR UNE ETAPE A PART. Le mot de passe est juste :
    # la page passe a l'etape suivante -- Face ID si une cle existe, sinon
    # le code de l'application. Le code n'est plus demande a cote du mot
    # de passe.
    if totp_actif() or (admin_2fa_obligatoire() and cles_admin):
        moyens = moyens_admin()
        code = d.get("code")
        if not code:
            if not moyens:
                return jsonify({"error": "Face ID n'est pas utilisable ici : ouvre le "
                                         "panneau en HTTPS, par son nom de domaine."}), 403
            attendre_second_facteur(NOM_ADMIN)
            moyen = d.get("moyen") if d.get("moyen") in moyens else moyens[0]
            if moyen == MOYEN_EMAIL:
                return _code_admin_par_mail(moyens)
            return jsonify({"totp": True, "second_facteur": True,
                            "moyen": moyen, "moyens": moyens}), 401
        par_appli = totp_actif() and totp_verifie(_totp_secret, code)
        if not (par_appli or verifier_code_admin(code)):
            register_failed_attempt()
            journaliser("echec", qui=NOM_ADMIN, motif="second facteur", ip=_adresse_client())
            return jsonify({"error": avec_essais_restants("Code de vérification incorrect."),
                            "totp": True, "moyens": moyens}), 401
        noter_dernier_moyen(NOM_ADMIN, MOYEN_TOTP if par_appli else MOYEN_EMAIL)

    session.permanent = True
    session["authed"] = True
    session["ouverte"] = int(time.time())
    # Le jeton nait avec la session, jamais apres : une session
    # authentifiee sans jeton ferait de verifier_jeton une passoire.
    jeton_session()
    session["role"] = ROLE_ADMIN
    session["utilisateur"] = NOM_ADMIN
    journaliser("connexion", qui=NOM_ADMIN, role=ROLE_ADMIN, ip=_adresse_client())
    signaler_connexion_admin()
    return jsonify({"ok": True, "role": ROLE_ADMIN})


def _connexion_utilisateur(d, nom, pw):
    """Un compte de la table : mot de passe, puis etat du compte."""
    comptes = lire_utilisateurs()
    compte = comptes.get(nom)
    # Meme message et meme chemin qu'un mot de passe faux : distinguer
    # "ce compte n'existe pas" de "mauvais mot de passe" donne la liste des
    # comptes valides a qui essaie. Meme duree aussi (_COMPTE_FANTOME).
    reconnu = verifie_mot_de_passe(compte or _COMPTE_FANTOME, pw or "")
    if not (compte and pw and reconnu):
        register_failed_attempt()
        # Le nom tel qu'il a ete tape, meme s'il ne correspond a aucun
        # compte : c'est ce qui distingue une faute de frappe d'un balayage.
        journaliser("echec", qui=nom, motif="mot de passe", ip=_adresse_client())
        return jsonify({"error": avec_essais_restants("Identifiants incorrects.")}), 401
    # Le mot de passe vient d'etre reconnu : c'est le seul moment ou l'on
    # peut recalculer une empreinte d'avant les 600 000 iterations.
    if not empreinte_a_jour(compte):
        try:
            compte["hash"] = derive_mot_de_passe(pw, compte["sel"])
            ecrire_utilisateurs(comptes)
        except Exception:                                         # noqa: BLE001
            pass

    # Suspendu : le mot de passe vient d'etre reconnu, donc le dire ne
    # revele rien -- et se taire enverrait chercher une panne la ou il y a
    # une decision.
    if compte_suspendu(compte):
        journaliser("echec", qui=nom, motif="compte suspendu", ip=_adresse_client())
        return jsonify({"error": "Ce compte est suspendu. Demande à "
                                 "l'administrateur de le réactiver."}), 403

    # Compte cree librement dont l'adresse n'a jamais ete confirmee. Le
    # message est explicite : ici, il ne revele rien qu'on ne sache deja --
    # le mot de passe vient d'etre reconnu.
    if compte.get("attente_email"):
        return jsonify({"error": "Confirme d'abord ton adresse mail : un code "
                                 "t'a été envoyé à l'inscription.",
                        "attente_email": True}), 403

    return _second_facteur_utilisateur(d, nom, compte)


def _second_facteur_utilisateur(d, nom, compte):
    """Le mot de passe d'un compte utilisateur est juste : verifier le code,
    ou dire lequel attendre (et l'envoyer), ou faire inscrire un facteur.

    Le second facteur n'est pas optionnel pour un compte utilisateur. Ces
    comptes existent pour etre distribues -- a un collegue, a un client --
    donc leur mot de passe circule par un canal qu'on ne maitrise pas, et
    sera reutilise ailleurs. C'est exactement le cas ou un seul secret ne
    suffit pas. L'administrateur, lui, garde le choix : lui imposer le
    second facteur d'office pourrait l'enfermer hors de son propre panneau.
    """
    secret = compte.get("totp") or ""
    moyens = moyens_second_facteur(compte)
    # Face ID en dernier : il n'existe qu'en HTTPS, derriere « Essayer une
    # autre methode » -- sauf s'il est le dernier moyen utilise.
    if passkeys_du_compte(nom) and passkey_utilisable():
        moyens = moyens + [MOYEN_PASSKEY]
    moyens = dernier_moyen_en_tete(moyens, compte.get("dernier_moyen"))
    code = d.get("code")
    # Sans aucun moyen (ni secret, ni code envoye), un code tape ne prouve
    # rien : on passe a l'inscription du facteur, plus bas.
    if code and (moyens or compte.get("code_connexion")):
        ok = bool(secret) and totp_verifie(secret, code)
        moyen_utilise = MOYEN_TOTP if ok else MOYEN_EMAIL
        if not ok and compte.get("code_connexion"):
            comptes = lire_utilisateurs()
            ok = verifier_code_connexion(comptes[nom], code)
            try:
                # Ecrit dans les deux cas : les essais consommes doivent
                # survivre a la requete, sinon la limite ne limite rien.
                ecrire_utilisateurs(comptes)
            except OSError:
                pass
        if not ok:
            register_failed_attempt()
            journaliser("echec", qui=nom, motif="second facteur", ip=_adresse_client())
            return jsonify({"error": avec_essais_restants("Code de vérification incorrect."),
                            "totp": True, "moyens": moyens}), 401
        _lever_suspension_code_mail(nom)
        noter_dernier_moyen(nom, moyen_utilise)
        return _ouvrir_session_utilisateur(nom)

    if moyens:
        # Pas encore de code : on dit lequel attendre, et on l'envoie s'il
        # doit partir par mail. Le moyen demande n'est retenu que
        # s'il est dans la liste du compte -- la destination, elle, ne vient
        # jamais de la requete.
        moyen = d.get("moyen") if d.get("moyen") in moyens else moyens[0]
        if moyen == MOYEN_PASSKEY:
            # Rien ne part : la page demande Face ID a l'appareil.
            attendre_second_facteur(nom)
            return jsonify({"totp": True, "second_facteur": True,
                            "moyen": moyen, "moyens": moyens}), 401
        reponse = {"totp": True, "moyen": moyen, "moyens": moyens}
        if moyen == MOYEN_TOTP:
            # Rien ne part : choisir l'application ne coute pas d'essai. Vecu :
            # « Essayer une autre methode » apres un code par mail consommait
            # un second essai sur cinq sans rien envoyer.
            reponse["error"] = "Saisis le code de ton application d'authentification."
            return jsonify(reponse), 401
        # Compte comme une tentative : sans cela, ce formulaire enverrait
        # des mails en boucle a qui connait un mot de passe.
        register_failed_attempt()
        comptes = lire_utilisateurs()
        cible = comptes[nom]
        en_cours = cible.get("code_connexion") or {}
        reponse["destination"] = masquer_destination(moyen, cible.get("email"))
        recent = (en_cours.get("moyen") == moyen
                  and int(time.time()) - int(en_cours.get("envoye") or 0) < CODE_EMAIL_DELAI)
        if not recent:
            code_envoye = poser_code_connexion(cible, moyen)
            try:
                envoyer_code_connexion(cible, nom, moyen, code_envoye)
                ecrire_utilisateurs(comptes)
            except Exception as e:                                # noqa: BLE001
                journaliser("echec", qui=nom, motif=f"envoi du code ({moyen})",
                            detail=f"{type(e).__name__}", ip=_adresse_client())
                reponse["error"] = "Le code n'a pas pu partir par mail."
                reponse["envoi_impossible"] = True
                return jsonify(reponse), 401
        reponse["error"] = ("Un code vient de t'être envoyé par mail à "
                            + reponse["destination"] + ".")
        reponse["envoye"] = True
        return jsonify(reponse), 401

    if (compte.get("email_verifie") or compte.get("code_mail_suspendu")
            or passkeys_du_compte(nom)):
        # Ce compte a deja un facteur (adresse verifiee, Face ID) qu'on ne
        # peut pas utiliser en ce moment : serveur d'envoi en panne, ou code
        # par mail suspendu. Laisser enregistrer une application ici, sur le
        # seul mot de passe, contournerait ce facteur.
        register_failed_attempt()
        journaliser("echec", qui=nom, motif="aucun second facteur utilisable",
                    ip=_adresse_client())
        return jsonify({"error": "Aucun code ne peut être envoyé pour le moment. "
                                 "Connecte-toi avec Face ID, ou demande de l'aide à "
                                 "ton administrateur."}), 403

    if not secret:
        # Premier acces : inscription obligatoire avant toute session. Le
        # secret candidat vit dans le cookie signe -- rien n'est enregistre
        # tant qu'un code valide n'a pas ete fourni, donc une cle mal
        # recopiee ne peut pas enfermer dehors, et deux personnes peuvent
        # s'inscrire en meme temps sans se marcher dessus.
        candidat = totp_nouveau_secret()
        session["totp_candidat"] = candidat
        session["totp_inscription"] = nom
        session["totp_uri"] = totp_uri(candidat, nom)
        return jsonify({"inscription": True, "secret": candidat,
                        "uri": session["totp_uri"], "compte": nom,
                        "qr": "/qr/totp.svg"})

    # Inatteignable : un compte qui a un secret a au moins le moyen TOTP,
    # et la branche "moyens" ci-dessus a deja repondu. Garde par prudence.
    register_failed_attempt()
    return jsonify({"error": avec_essais_restants("Code de vérification incorrect."), "totp": True}), 401


def passkey_utilisable():
    return passkeys_disponibles() and not passkey_contexte()[2]


# LA METHODE PROPOSEE D'OFFICE : LA DERNIERE UTILISEE. Vecu : « retenir la
# derniere methode utilisee pour l'utilisateur comme methode par defaut (pas
# parametrable). Possibilite de cliquer sur Essayer une autre methode ». Les
# methodes restent decorrelees : chacune se suffit, et en choisir une
# n'envoie ni ne demande rien a l'autre (un code ne part par mail que si le
# mail est choisi).
# POURQUOI la liste reste celle du compte : la methode retenue ne fait que
#   passer devant. Une methode retiree depuis (adresse non verifiee, Face ID
#   hors HTTPS) n'est jamais proposee pour autant.
def dernier_moyen_en_tete(moyens, dernier):
    if dernier in moyens:
        return [dernier] + [m for m in moyens if m != dernier]
    return moyens


def noter_dernier_moyen(qui, moyen):
    """Retient la methode qui vient d'ouvrir la session. Jamais d'exception :
    la connexion est faite, le souvenir n'est qu'un confort."""
    if moyen not in (MOYEN_EMAIL, MOYEN_TOTP, MOYEN_PASSKEY):
        return
    try:
        if qui == NOM_ADMIN:
            if reglage("dernier_moyen_admin") != moyen:
                ecrire_reglage("dernier_moyen_admin", moyen)
            return
        comptes = lire_utilisateurs()
        compte = comptes.get(qui)
        if compte is not None and compte.get("dernier_moyen") != moyen:
            compte["dernier_moyen"] = moyen
            ecrire_utilisateurs(comptes)
    except Exception:                                             # noqa: BLE001
        pass


def moyens_admin():
    """Les seconds facteurs de l'administrateur : Face ID, le mail, l'application.

    Le code par mail part a l'adresse d'alerte, jamais par Mailpit (un mail
    de securite) : il faut donc un vrai serveur d'envoi.
    """
    moyens = []
    if smtp_utilisable()[1] and smtp_utilisable()[0].get("host") and alertes_admin():
        moyens.append(MOYEN_EMAIL)
    if totp_actif():
        moyens.append(MOYEN_TOTP)
    # Face ID en dernier : il n'existe qu'en HTTPS, derriere « Essayer une
    # autre methode » -- sauf s'il est le dernier moyen utilise.
    if passkeys_du_compte(NOM_ADMIN) and passkey_utilisable():
        moyens.append(MOYEN_PASSKEY)
    return dernier_moyen_en_tete(moyens, reglage("dernier_moyen_admin"))


def _code_admin_par_mail(moyens):
    """Envoie (au plus une fois par minute) le code de l'administrateur.

    Le code vit au coffre, en empreinte, comme celui d'un compte : jamais
    dans le cookie, ou six chiffres se retrouveraient par force brute.
    """
    destinataires = alertes_admin()
    reponse = {"totp": True, "second_facteur": True, "moyen": MOYEN_EMAIL, "moyens": moyens,
               "destination": masquer_destination(MOYEN_EMAIL, destinataires[0])}
    en_cours = reglage("code_connexion_admin") or {}
    if int(time.time()) - int(en_cours.get("envoye") or 0) >= CODE_EMAIL_DELAI:
        # Compte comme une tentative : sinon, des mails en boucle.
        register_failed_attempt()
        porteur = {}
        code = poser_code_connexion(porteur, MOYEN_EMAIL)
        try:
            # Range d'abord : un code parti mais jamais enregistre ne
            # pourrait pas etre verifie.
            ecrire_reglage("code_connexion_admin", porteur["code_connexion"])
            envoyer_code_connexion(porteur, NOM_ADMIN, MOYEN_EMAIL, code,
                                   destinataires=destinataires)
        except Exception as e:                                    # noqa: BLE001
            journaliser("echec", qui=NOM_ADMIN, motif="envoi du code (email)",
                        detail=f"{type(e).__name__}", ip=_adresse_client())
            reponse.update(error="Le code n'a pas pu partir par mail.", envoi_impossible=True)
            return jsonify(reponse), 401
    reponse.update(error="Un code vient de t'être envoyé par mail à "
                         + reponse["destination"] + ".", envoye=True)
    return jsonify(reponse), 401


def verifier_code_admin(code):
    """Le code par mail de l'administrateur : un essai consomme, le code au succes."""
    en_cours = reglage("code_connexion_admin")
    if not en_cours:
        return False
    porteur = {"code_connexion": en_cours}
    ok = verifier_code_connexion(porteur, code)
    try:
        ecrire_reglage("code_connexion_admin", porteur.get("code_connexion"))
    except (OSError, RuntimeError):
        pass
    return ok


def attendre_second_facteur(nom):
    """Le mot de passe de « nom » vient d'etre reconnu, dans CE navigateur.

    Rien d'autre : pas de session. C'est ce qui autorise, pendant dix
    minutes, a confirmer avec Face ID sans renvoyer le mot de passe.
    """
    session["facteur_attente"] = {"nom": nom, "depuis": int(time.time())}


def nom_en_attente():
    attente = session.get("facteur_attente") or {}
    if int(time.time()) - int(attente.get("depuis") or 0) > SECOND_FACTEUR_VALIDITE:
        return None
    return attente.get("nom") or None


@flask_app.post("/login/second-facteur/passkey/options")
def login_facteur_passkey_options():
    """Le defi Face ID de l'etape 2, limite aux cles du compte en attente."""
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    nom = nom_en_attente()
    if not nom:
        return jsonify({"error": "Recommence la connexion."}), 400
    refus = _refus_passkey()
    if refus:
        return refus
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement
    rp_id, _, _ = passkey_contexte()
    options = generate_authentication_options(
        rp_id=rp_id, allow_credentials=_descripteurs(nom),
        user_verification=UserVerificationRequirement.REQUIRED)
    session["passkey_defi"] = base64.b64encode(options.challenge).decode()
    return Response(options_to_json(options), mimetype="application/json")


@flask_app.post("/login/second-facteur/passkey")
def login_facteur_passkey():
    """Face ID confirme le mot de passe : la session s'ouvre."""
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    nom = nom_en_attente()
    if not nom:
        return jsonify({"error": "Recommence la connexion."}), 400
    refus = _refus_passkey()
    if refus:
        return refus
    proprietaire, erreur = _verifier_cle(nom_attendu=nom)
    if erreur:
        return erreur
    session.pop("facteur_attente", None)
    return _ouvrir_session_par_cle(proprietaire)


def _lever_suspension_code_mail(nom):
    """Une connexion par un facteur fort rend le code par mail."""
    comptes = lire_utilisateurs()
    if comptes.get(nom, {}).get("code_mail_suspendu"):
        comptes[nom].pop("code_mail_suspendu", None)
        try:
            ecrire_utilisateurs(comptes)
        except (OSError, RuntimeError):
            pass


def _ouvrir_session_utilisateur(nom):
    """Ouvre la session d'un compte utilisateur, une fois tout verifie."""
    session.pop("totp_candidat", None)
    session.pop("totp_inscription", None)
    session.pop("totp_uri", None)
    session.permanent = True
    session["authed"] = True
    # La date d'OUVERTURE, posee ici et jamais repoussee : c'est elle qui
    # fait la limite absolue des trente jours. Le cookie, lui, tient
    # l'inactivite de son cote.
    session["ouverte"] = int(time.time())
    # Le jeton nait avec la session, jamais apres : une session
    # authentifiee sans jeton ferait de verifier_jeton une passoire.
    jeton_session()
    session["role"] = ROLE_UTILISATEUR
    session["utilisateur"] = nom
    journaliser("connexion", qui=nom, role=ROLE_UTILISATEUR, ip=_adresse_client())
    return jsonify({"ok": True, "role": ROLE_UTILISATEUR})


@flask_app.post("/login/second-facteur")
def login_second_facteur():
    """Confirme l'inscription au second facteur, et ouvre la session.

    Etape distincte de /login : entre les deux, la session ne vaut rien --
    elle ne porte pas "authed", donc elle n'ouvre aucune page ni aucune
    application. Le mot de passe seul ne suffit jamais a entrer.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    nom = session.get("totp_inscription")
    candidat = session.get("totp_candidat")
    if not (nom and candidat):
        return jsonify({"error": "Recommence la connexion : aucune inscription en attente."}), 400

    code = (request.get_json(force=True, silent=True) or {}).get("code")
    if not totp_verifie(candidat, code):
        register_failed_attempt()
        return jsonify({"error": "Code incorrect. Vérifie l'heure de ton téléphone."}), 400

    if nom == NOM_ADMIN:
        return _inscrire_totp_admin(candidat)

    comptes = lire_utilisateurs()
    compte = comptes.get(nom)
    if not compte:
        return jsonify({"error": "Compte inconnu."}), 404
    # Course possible : l'administrateur a pu inscrire un secret entre-temps
    # (une autre session du meme compte). Le premier enregistre gagne, plutot
    # que d'ecraser un facteur deja en service sur un autre telephone.
    if compte.get("totp"):
        return jsonify({"error": "Un second facteur a déjà été enregistré. "
                                 "Recommence la connexion."}), 409
    compte["totp"] = candidat
    compte["dernier_moyen"] = MOYEN_TOTP
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Second facteur non enregistré : {e}"}), 500

    session.pop("totp_candidat", None)
    session.pop("totp_inscription", None)
    session.pop("totp_uri", None)
    session.permanent = True
    session["authed"] = True
    # La date d'OUVERTURE, posee ici et jamais repoussee : c'est elle qui
    # fait la limite absolue des trente jours. Le cookie, lui, tient
    # l'inactivite de son cote.
    session["ouverte"] = int(time.time())
    # Le jeton nait avec la session, jamais apres : une session
    # authentifiee sans jeton ferait de verifier_jeton une passoire.
    jeton_session()
    session["role"] = ROLE_UTILISATEUR
    session["utilisateur"] = nom
    journaliser("connexion", qui=nom, role=ROLE_UTILISATEUR, ip=_adresse_client())
    return jsonify({"ok": True, "role": ROLE_UTILISATEUR})


def _mon_compte():
    """Le compte de la session, ou None pour l'administrateur.

    Le compte d'administration ne vit pas dans utilisateurs.json : son mot
    de passe est dans credentials.env, et il n'a pas d'adresse mail a lui.
    """
    if est_admin():
        return None
    return lire_utilisateurs().get(utilisateur_courant())


# ------------------------- routes des cles d'acces -------------------------

@flask_app.get("/api/passkeys/etat")
def api_passkeys_etat():
    """Ce que la page de connexion et les parametres ont besoin de savoir.

    Publique : la page de connexion doit pouvoir demander si le bouton a un
    sens avant que quiconque soit authentifie. Elle ne revele ni compte ni
    cle -- seulement si le serveur est en etat d'en utiliser.
    """
    if not passkeys_disponibles():
        return jsonify({"possible": False,
                        "empechement": "La bibliotheque webauthn n'est pas installee "
                                       "sur ce serveur."})
    _, _, empechement = passkey_contexte()
    return jsonify({"possible": not empechement, "empechement": empechement})


def _refus_passkey():
    """(reponse, code) si les cles d'acces ne sont pas utilisables ici."""
    if not passkeys_disponibles():
        return jsonify({"error": "La bibliothèque webauthn n'est pas installée."}), 501
    _, _, empechement = passkey_contexte()
    if empechement:
        return jsonify({"error": empechement}), 400
    return None


@flask_app.post("/api/mon-compte/passkeys/options")
@require_auth
def api_passkey_options():
    """Prepare l'enregistrement d'une cle pour le compte connecte."""
    refus = _refus_passkey() or refus_authentification_ancienne()
    if refus:
        return refus
    return _options_enregistrement(utilisateur_courant())


def _options_enregistrement(nom):
    """Le defi d'enregistrement d'une cle pour le compte nom."""
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                          ResidentKeyRequirement,
                                          UserVerificationRequirement)
    rp_id, _, _ = passkey_contexte()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="CodeLab",
        user_name=nom,
        # L'identifiant d'utilisateur est le nom du compte : il ne quitte
        # jamais ce serveur, et deux comptes ne portent jamais le meme nom.
        user_id=nom.encode(),
        user_display_name=nom,
        # Deja enregistrees : le navigateur propose alors d'en ajouter une
        # autre plutot que de remplacer celle qu'on a sous la main.
        exclude_credentials=_descripteurs(nom),
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Decouvrable : c'est ce qui permet de se connecter sans taper
            # son nom -- le navigateur sait deja de qui il s'agit.
            resident_key=ResidentKeyRequirement.PREFERRED,
            # Exigee : une cle qui ne verifie pas la personne (ni empreinte,
            # ni code d'appareil) ne serait qu'un facteur de possession, et
            # ne pourrait pas remplacer mot de passe ET second facteur.
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    session["passkey_defi"] = base64.b64encode(options.challenge).decode()
    return Response(options_to_json(options), mimetype="application/json")


@flask_app.post("/api/mon-compte/passkeys")
@require_auth
def api_passkey_enregistrer():
    """Verifie la reponse du navigateur et range la cle."""
    refus = _refus_passkey() or refus_authentification_ancienne()
    if refus:
        return refus
    return _enregistrer_cle(utilisateur_courant()) or jsonify({"ok": True})


def _enregistrer_cle(nom):
    """Verifie la reponse du navigateur et range la cle de nom.

    None si la cle est enregistree, sinon la reponse d'erreur.
    """
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url
    defi = session.get("passkey_defi")
    if not defi:
        return jsonify({"error": "Recommence : aucun enregistrement en attente."}), 400
    d = request.get_json(force=True, silent=True) or {}
    rp_id, origine, _ = passkey_contexte()
    try:
        verifiee = verify_registration_response(
            credential=d.get("credential"),
            expected_challenge=base64.b64decode(defi),
            expected_rp_id=rp_id,
            expected_origin=origine,
            require_user_verification=True,
        )
    except Exception as e:
        return jsonify({"error": detail_pour_admin("Clé refusée. Réessaie depuis cet appareil.", e)}), 400

    tout = lire_passkeys()
    liste = tout.setdefault(nom, [])
    identifiant = bytes_to_base64url(verifiee.credential_id)
    if any(k["id"] == identifiant for k in liste):
        return jsonify({"error": "Cette clé est déjà enregistrée."}), 400
    liste.append({
        "id": identifiant,
        "cle_publique": bytes_to_base64url(verifiee.credential_public_key),
        "compteur": verifiee.sign_count,
        "nom": description_propre(d.get("nom"))[:PASSKEY_NOM_MAX] or "Cle d'acces",
        "cree": int(time.time()),
        "dernier": 0,
    })
    try:
        ecrire_passkeys(tout)
    except OSError as e:
        return jsonify({"error": f"Clé non enregistrée : {e}"}), 500
    session.pop("passkey_defi", None)
    journaliser("passkey", qui=nom, action="ajout", ip=_adresse_client())
    return None


# PREMIERE CONNEXION DE L'ADMINISTRATEUR : FACE ID AU LIEU D'UN CODE A
# RECOPIER. Le mot de passe vient d'etre reconnu par /login, qui a ouvert
# une inscription en attente (sans session). Enregistrer une cle d'acces ici
# vaut exactement ce que vaut le code a six chiffres a la meme etape : le
# second facteur est lie au navigateur qui a prouve le mot de passe. La cle
# exige la verification de la personne (Face ID, Touch ID, code de
# l'appareil), comme partout ailleurs.
#
# Les gardes : une inscription admin en attente et recente (10 min), aucun
# facteur deja enregistre (cette porte ne sert qu'une fois), le verrou du
# reseau local, la limite de tentatives.
INSCRIPTION_VALIDITE = 600


def _refus_inscription_cle():
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    depuis = session.get("inscription_depuis") or 0
    if (session.get("totp_inscription") != NOM_ADMIN
            or time.time() - depuis > INSCRIPTION_VALIDITE):
        return jsonify({"error": "Recommence la connexion : aucune inscription en attente."}), 400
    hors = refus_admin_hors_reseau()
    if hors:
        return jsonify({"error": hors}), 403
    if totp_actif() or passkeys_du_compte(NOM_ADMIN):
        return jsonify({"error": "Un second facteur a déjà été enregistré. "
                                 "Recommence la connexion."}), 409
    return _refus_passkey()


@flask_app.post("/login/inscription/passkey/options")
def login_inscription_passkey_options():
    return _refus_inscription_cle() or _options_enregistrement(NOM_ADMIN)


@flask_app.post("/login/inscription/passkey")
def login_inscription_passkey():
    refus = _refus_inscription_cle()
    if refus:
        return refus
    erreur = _enregistrer_cle(NOM_ADMIN)
    if erreur:
        register_failed_attempt()
        return erreur
    session.pop("inscription_depuis", None)
    return _ouvrir_session_admin()


@flask_app.get("/api/mon-compte/passkeys")
@require_auth
def api_passkey_liste():
    """Les cles du compte connecte, sans leur cle publique.

    Elle n'apprend rien a l'interface et n'a pas a trainer dans
    l'historique du navigateur.
    """
    return jsonify({"passkeys": [
        {"id": k["id"], "nom": k.get("nom") or "Cle d'acces",
         "cree": k.get("cree"), "dernier": k.get("dernier")}
        for k in passkeys_du_compte(utilisateur_courant())]})


@flask_app.delete("/api/mon-compte/passkeys")
@require_auth
def api_passkey_tout_retirer():
    """Desactive Face ID / Touch ID : retire toutes les cles du compte.

    Le meme droit que retirer une cle a la fois, en un geste. Pour
    l'administrateur sans application de codes, la prochaine connexion
    redemande l'enregistrement d'un second facteur (voir login_submit).
    """
    nom = utilisateur_courant()
    tout = lire_passkeys()
    nombre = len(tout.get(nom, []))
    if nombre:
        tout[nom] = []
        try:
            ecrire_passkeys(tout)
        except OSError as e:
            return jsonify({"error": f"Clés non retirées : {e}"}), 500
        journaliser("passkey", qui=nom, action="retrait", ip=_adresse_client())
    return jsonify({"ok": True, "retirees": nombre})


@flask_app.delete("/api/mon-compte/passkeys/<path:identifiant>")
@require_auth
def api_passkey_supprimer(identifiant):
    nom = utilisateur_courant()
    tout = lire_passkeys()
    liste = tout.get(nom, [])
    restantes = [k for k in liste if k["id"] != identifiant]
    if len(restantes) == len(liste):
        return jsonify({"error": "Clé inconnue."}), 404
    tout[nom] = restantes
    try:
        ecrire_passkeys(tout)
    except OSError as e:
        return jsonify({"error": f"Clé non supprimée : {e}"}), 500
    journaliser("passkey", qui=nom, action="retrait", ip=_adresse_client())
    return jsonify({"ok": True})


@flask_app.post("/login/passkey/options")
def login_passkey_options():
    """Prepare une connexion par cle d'acces.

    Sans nom de compte, la demande porte sur les cles decouvrables : c'est
    le navigateur qui sait de qui il s'agit, et le serveur ne revele donc
    aucune liste de comptes.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    refus = _refus_passkey()
    if refus:
        return refus
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement
    rp_id, _, _ = passkey_contexte()
    nom = (request.get_json(force=True, silent=True) or {}).get("nom") or ""
    nom = (nom or "").strip().lower()
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=_descripteurs(nom) if nom else None,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    session["passkey_defi"] = base64.b64encode(options.challenge).decode()
    return Response(options_to_json(options), mimetype="application/json")


@flask_app.post("/login/passkey")
def login_passkey():
    """Ouvre la session si la signature est bonne.

    Une cle d'acces vaut le mot de passe ET le second facteur : la personne
    a prouve la possession de l'appareil, et l'appareil a verifie que c'est
    bien elle (empreinte ou code). C'est pour cela que l'enregistrement
    exige la verification d'utilisateur -- sans elle, ce ne serait qu'une
    moitie, et ouvrir une session sur cette moitie serait un recul.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    refus = _refus_passkey()
    if refus:
        return refus
    proprietaire, erreur = _verifier_cle()
    if erreur:
        return erreur
    return _ouvrir_session_par_cle(proprietaire)


def _verifier_cle(nom_attendu=None):
    """Verifie la signature d'une cle d'acces.

    Rend (proprietaire, None), ou (None, reponse d'erreur). Avec nom_attendu
    (etape 2 de la connexion), la cle doit appartenir a ce compte.
    """
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes
    defi = session.get("passkey_defi")
    if not defi:
        return None, (jsonify({"error": "Recommence : aucune demande en attente."}), 400)
    d = request.get_json(force=True, silent=True) or {}
    credential = d.get("credential") or {}
    identifiant = credential.get("id") or ""

    # A qui appartient cette cle ? On cherche par identifiant, jamais par le
    # nom annonce par le client : c'est la signature qui fait foi.
    proprietaire, enregistree = None, None
    for compte, cles in lire_passkeys().items():
        for k in cles:
            if k["id"] == identifiant:
                proprietaire, enregistree = compte, k
                break
        if proprietaire:
            break
    if not enregistree:
        register_failed_attempt()
        journaliser("echec", qui="", motif="cle d'acces inconnue", ip=_adresse_client())
        return None, (jsonify({"error": "Clé d'accès inconnue."}), 401)

    # A l'etape 2, la cle doit etre celle du compte dont le mot de passe
    # vient d'etre reconnu : la cle d'un autre compte ne confirme rien.
    if nom_attendu and proprietaire != nom_attendu:
        register_failed_attempt()
        journaliser("echec", qui=nom_attendu, motif="cle d'acces d'un autre compte",
                    ip=_adresse_client())
        return None, (jsonify({"error": "Cette clé n'appartient pas à ce compte."}), 401)

    # Des que l'on sait a qui est la cle, et avant toute cryptographie : une
    # cle d'acces est un excellent second facteur, mais elle voyage avec son
    # porteur et ne dit rien de l'endroit d'ou l'on appelle. Sans ce verrou
    # ici, il suffirait de passer par cette porte-ci plutot que par le mot de
    # passe.
    if proprietaire == NOM_ADMIN:
        hors = refus_admin_hors_reseau()
        if hors:
            register_failed_attempt()   # meme raison que ci-dessus
            journaliser("refus", qui=NOM_ADMIN, motif="hors reseau local",
                        moyen="cle d'acces", ip=_adresse_client())
            return None, (jsonify({"error": hors}), 403)

    rp_id, origine, _ = passkey_contexte()
    try:
        verifiee = verify_authentication_response(
            credential=credential,
            expected_challenge=base64.b64decode(defi),
            expected_rp_id=rp_id,
            expected_origin=origine,
            credential_public_key=base64url_to_bytes(enregistree["cle_publique"]),
            credential_current_sign_count=enregistree.get("compteur", 0),
            require_user_verification=True,
        )
    except Exception as e:
        register_failed_attempt()
        journaliser("echec", qui=proprietaire, motif="cle d'acces", ip=_adresse_client())
        return None, (jsonify({"error": detail_pour_admin(
            "Clé refusée. Réessaie, ou connecte-toi avec ton mot de passe.", e)}), 401)

    # Le compteur ne doit jamais reculer : une cle clonee se trahit la.
    tout = lire_passkeys()
    for k in tout.get(proprietaire, []):
        if k["id"] == identifiant:
            k["compteur"] = verifiee.new_sign_count
            k["dernier"] = int(time.time())
    try:
        ecrire_passkeys(tout)
    except OSError:
        pass

    return proprietaire, None


def _ouvrir_session_par_cle(proprietaire):
    """Ouvre la session du proprietaire d'une cle verifiee."""
    est_administrateur = proprietaire == NOM_ADMIN
    if not est_administrateur:
        compte = lire_utilisateurs().get(proprietaire)
        if not compte:
            return jsonify({"error": "Compte inconnu."}), 401
        if compte.get("attente_email"):
            return jsonify({"error": "Confirme d'abord ton adresse mail."}), 403
        if compte_suspendu(compte):
            journaliser("echec", qui=proprietaire, motif="compte suspendu",
                        moyen="cle d'acces", ip=_adresse_client())
            return jsonify({"error": "Ce compte est suspendu. Demande à "
                                     "l'administrateur de le réactiver."}), 403
        _lever_suspension_code_mail(proprietaire)

    session.pop("passkey_defi", None)
    session.permanent = True
    session["authed"] = True
    # La date d'OUVERTURE, posee ici et jamais repoussee : c'est elle qui
    # fait la limite absolue des trente jours. Le cookie, lui, tient
    # l'inactivite de son cote.
    session["ouverte"] = int(time.time())
    # Le jeton nait avec la session, jamais apres : une session
    # authentifiee sans jeton ferait de verifier_jeton une passoire.
    jeton_session()
    session["role"] = ROLE_ADMIN if est_administrateur else ROLE_UTILISATEUR
    session["utilisateur"] = proprietaire
    journaliser("connexion", qui=proprietaire, role=session["role"],
                moyen="cle d'acces", ip=_adresse_client())
    noter_dernier_moyen(proprietaire, MOYEN_PASSKEY)
    if est_administrateur:
        signaler_connexion_admin()
    return jsonify({"ok": True, "role": session["role"]})


@flask_app.get("/api/activite")
@require_admin
def api_activite():
    """Le journal des acces, et son resume.

    Reserve a l'administrateur : c'est le seul role a qui la question « qui a
    ouvert quoi » se pose, et la reponse contient des adresses IP.

    Postgres d'abord quand il repond : il garde tout, la ou la base locale
    est plafonnee et oublie le plus ancien. Il ne remplace jamais la base
    locale -- un Postgres eteint rend simplement la vue plus courte, et la
    reponse dit d'ou viennent les lignes.
    """
    app_ = request.args.get("app") or None
    qui = request.args.get("qui")
    if pg_disponible():
        try:
            # Le resume vient de la base LUI AUSSI. Calcule sur le fichier, il
            # comptait sur une fenetre glissante d'un megaoctet : le total
            # d'ouvertures d'un projet diminuait tout seul a mesure que le
            # journal tournait.
            return jsonify({"evenements": pg_lire_acces(app=app_, qui=qui),
                            "resume": pg_resume_acces(), "source": "postgres",
                            "pg": _pg_etat["pret"]})
        except Exception as e:
            _pg_etat["erreur"] = f"{type(e).__name__}: {e}"
    return jsonify({"evenements": lire_acces(app=app_, qui=qui),
                    "resume": resume_acces(), "source": "fichier",
                    "pg": False, "pg_erreur": _pg_etat["erreur"]})


@flask_app.get("/api/activite/carte")
@require_admin
def api_activite_carte():
    """La carte de chaleur : une case par jour, une annee en un coup d'oeil.

    Meme role que le calendrier de contributions de GitHub, et pour la meme
    raison : une liste d'evenements dit ce qui s'est passe, une carte dit
    QUAND -- les periodes creuses, les week-ends, le projet qu'on n'a plus
    ouvert depuis six semaines. Aucune liste ne montre cela.

    Les filtres sont ceux qu'on se pose : une personne, une application, ou
    les deux.
    """
    qui = request.args.get("qui")
    app_ = request.args.get("app") or None
    # L'annee demandee arrive par l'URL : elle se borne, elle ne se croit
    # pas. Illisible ou farfelue, on retombe sur l'annee en cours plutot que
    # de rendre une carte vide sans expliquer pourquoi.
    courante = annee_courante()
    brut = request.args.get("annee")
    try:
        annee = int(brut) if brut else courante
    except ValueError:
        annee = courante
    if not 1970 <= annee <= courante + 1:
        annee = courante

    if pg_disponible():
        try:
            basse, haute = pg_annees_activite()
            return jsonify({"jours": pg_carte_activite(annee, qui=qui, app=app_),
                            "annee": annee,
                            "annees": annees_proposees(basse, haute),
                            "fuseau": FUSEAU_JOURNAL,
                            "source": "postgres"})
        except Exception as e:                                    # noqa: BLE001
            _pg_etat["erreur"] = f"{type(e).__name__}: {e}"
    # Repli : le fichier oublie au-dela d'un megaoctet, donc la carte est plus
    # courte. Le dire plutot que d'afficher des jours vides qui laisseraient
    # croire a une inactivite.
    basse, haute = annees_activite()
    return jsonify({"jours": carte_activite(annee, qui=qui, app=app_),
                    "annee": annee,
                    "annees": annees_proposees(basse, haute),
                    "fuseau": FUSEAU_JOURNAL,
                    "source": "fichier", "pg_erreur": _pg_etat["erreur"]})


@flask_app.get("/api/mon-compte")
@require_auth
def api_mon_compte():
    """Ce que la session peut dire d'elle-meme, et rien de plus."""
    compte = _mon_compte()
    _, smtp_ok = smtp_utilisable()
    return jsonify({
        "nom": utilisateur_courant(),
        "role": role_courant(),
        "email": (compte or {}).get("email") or "",
        "email_verifie": bool((compte or {}).get("email_verifie")),
        "passkeys": len(passkeys_du_compte(utilisateur_courant())),
        "profil": lire_profil(),
        # Sans serveur d'envoi, la page n'affiche pas un bouton qui echouera.
        "smtp": smtp_ok,
    })


# ------------------ l'application d'authentification d'un compte ------------------
#
# Vecu : deux methodes au choix, le code par mail ou celui de l'application
# d'authentification, decorrelees. Un compte qui a son adresse n'avait
# aucun moyen d'ajouter l'application : elle ne s'inscrivait qu'au premier
# acces, sans adresse. Il la gere desormais ici (Parametres > Securite).
#
# POURQUOI la connexion recente : ajouter un facteur, c'est installer un
#   acces durable au compte. Un cookie de session vole ne doit pas y
#   suffire (regle 8, comme une cle d'acces ou une adresse).
# POURQUOI un code pour retirer : comme pour l'administrateur, une session
#   volee ne doit pas pouvoir retirer le facteur. Et le dernier ne se retire
#   pas : un compte garde toujours un second facteur (regle 1).
# DEPEND DE : moyens_second_facteur (05-comptes), qui propose l'application
#   des qu'un secret est enregistre ; qr_totp, qui lit totp_uri en session.
def _autre_facteur(nom, compte):
    """Le compte garde-t-il un facteur sans son application ?"""
    return bool((compte.get("email") and compte.get("email_verifie"))
                or passkeys_du_compte(nom))


@flask_app.get("/api/mon-compte/totp")
@require_auth
def api_mon_totp():
    compte = _mon_compte()
    if compte is None:
        return jsonify({"error": "Le compte d'administration règle son application "
                                 "dans « Second facteur »."}), 400
    return jsonify({"actif": bool(compte.get("totp")),
                    "retirable": _autre_facteur(utilisateur_courant(), compte)})


@flask_app.post("/api/mon-compte/totp/preparer")
@require_auth
def api_mon_totp_preparer():
    compte = _mon_compte()
    if compte is None:
        return jsonify({"error": "Réservé aux comptes utilisateurs."}), 400
    refus = refus_authentification_ancienne()
    if refus:
        return refus
    if compte.get("totp"):
        return jsonify({"error": "Ton application d'authentification est déjà enregistrée."}), 400
    nom = utilisateur_courant()
    candidat = totp_nouveau_secret()
    session["totp_candidat"] = candidat
    session["totp_uri"] = totp_uri(candidat, nom)
    return jsonify({"secret": candidat, "uri": session["totp_uri"], "compte": nom,
                    "qr": "/qr/totp.svg"})


@flask_app.post("/api/mon-compte/totp/activer")
@require_auth
def api_mon_totp_activer():
    nom = utilisateur_courant()
    comptes = lire_utilisateurs()
    compte = None if est_admin() else comptes.get(nom)
    candidat = session.get("totp_candidat")
    if compte is None or not candidat:
        return jsonify({"error": "Recommence : aucune inscription en attente."}), 400
    refus = refus_authentification_ancienne()
    if refus:
        return refus
    code = (request.get_json(force=True, silent=True) or {}).get("code")
    if not totp_verifie(candidat, code):
        return jsonify({"error": "Code incorrect. Vérifie l'heure de ton téléphone."}), 400
    compte["totp"] = candidat
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Non enregistré : {e}"}), 500
    session.pop("totp_candidat", None)
    session.pop("totp_uri", None)
    journaliser("compte", qui=nom, action="application d'authentification ajoutée",
                ip=_adresse_client())
    return jsonify({"ok": True})


@flask_app.post("/api/mon-compte/totp/retirer")
@require_auth
def api_mon_totp_retirer():
    nom = utilisateur_courant()
    comptes = lire_utilisateurs()
    compte = None if est_admin() else comptes.get(nom)
    if compte is None or not compte.get("totp"):
        return jsonify({"ok": True})
    if not _autre_facteur(nom, compte):
        return jsonify({"error": "C'est ton seul second facteur : vérifie d'abord ton "
                                 "adresse mail, ou ajoute Face ID."}), 400
    code = (request.get_json(force=True, silent=True) or {}).get("code")
    if not totp_verifie(compte["totp"], code):
        register_failed_attempt()
        return jsonify({"error": "Code incorrect."}), 400
    compte.pop("totp", None)
    if compte.get("dernier_moyen") == MOYEN_TOTP:
        compte.pop("dernier_moyen", None)
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Non enregistré : {e}"}), 500
    journaliser("compte", qui=nom, action="application d'authentification retirée",
                ip=_adresse_client())
    return jsonify({"ok": True})


# ----------------------------- le profil d'un compte -----------------------------
#
# Vecu : « Rend parametrable le nom (facultatif) et prenom du compte ». Le
# bonjour du hub et le nom en haut a droite disaient l'identifiant de
# connexion (« admin », « marie ») ; le prenom le remplace des qu'il est
# donne. L'identifiant, lui, ne change pas : c'est lui qui designe le compte
# partout (droits, journal, applications).
#
# Range dans les PREFERENCES du compte (table preferences). La suppression
# d'un compte les emporte.
# PAS POUR L'ADMINISTRATEUR. Vecu : « Pas de modification du nom/prenom pour
# l'admin ». Le compte d'administration reste « admin » partout : la route le
# refuse, lire_profil ne lui rend rien, et la page ne montre pas la carte.
PROFIL_MAX = 40


def nom_affiche_propre(valeur):
    """Un prenom ou un nom tel qu'on l'affiche : texte seul, espaces resserres,
    sans caractere de controle, PROFIL_MAX signes au plus. "" sinon.

    POURQUOI : il est rendu dans la page (bonjour, en-tete, liste des
    comptes). Les pages l'ecrivent en texte ; ce nettoyage garde en plus la
    valeur courte et lisible, quoi que le client envoie.
    """
    if not isinstance(valeur, str):
        return ""
    v = "".join(c for c in valeur if unicodedata.category(c)[0] != "C")
    return " ".join(v.split())[:PROFIL_MAX].strip()


def lire_profil(qui=None):
    if (qui or utilisateur_courant() or NOM_ADMIN) == NOM_ADMIN:
        return {"prenom": "", "nom": ""}
    p = lire_preference("profil", qui) or {}
    return {"prenom": nom_affiche_propre(p.get("prenom")),
            "nom": nom_affiche_propre(p.get("nom"))}


@flask_app.put("/api/mon-compte/profil")
@require_auth
def api_mon_profil():
    if est_admin():
        return jsonify({"error": "Le compte d'administration garde son nom."}), 403
    d = request.get_json(force=True, silent=True) or {}
    profil = {"prenom": nom_affiche_propre(d.get("prenom")),
              "nom": nom_affiche_propre(d.get("nom"))}
    try:
        ecrire_preference("profil", profil if profil["prenom"] or profil["nom"] else None)
    except Exception as e:                                        # noqa: BLE001
        return jsonify({"error": detail_pour_admin("Profil non enregistré.", e)}), 500
    return jsonify({"ok": True, **profil})


@flask_app.post("/api/mon-compte/email")
@require_auth
def api_mon_email():
    """Declare ou change sa propre adresse, et envoie le code de suite.

    Reserve aux comptes utilisateurs : l'administrateur n'en a pas -- son
    compte n'est pas dans le registre, et les alertes ont deja leur
    destinataire.
    """
    compte = _mon_compte()
    if compte is None:
        return jsonify({"error": "Le compte d'administration n'a pas d'adresse "
                                 "propre : règle les destinataires des alertes."}), 400
    refus = refus_authentification_ancienne()
    if refus:
        return refus
    adresse = email_valide((request.get_json(force=True, silent=True) or {}).get("email"))
    if not adresse:
        return jsonify({"error": "Adresse mail invalide."}), 400

    comptes = lire_utilisateurs()
    nom = utilisateur_courant()
    if nom not in comptes:
        return jsonify({"error": "Compte inconnu."}), 404
    comptes[nom]["email"] = adresse
    comptes[nom]["email_verifie"] = False
    code = poser_code_email(comptes[nom])
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Adresse non enregistrée : {e}"}), 500
    try:
        envoyer_code_email(adresse, nom, code)
    except Exception as e:
        # L'adresse est enregistree, le mail n'est pas parti : le dire tel
        # quel, plutot que de laisser attendre un code qui ne viendra pas.
        return jsonify({"error": detail_pour_admin(
            "Adresse enregistrée, mais le mail n'est pas parti. Réessaie dans un instant.", e)}), 502
    return jsonify({"ok": True, "envoye": True})


@flask_app.post("/api/mon-compte/email/code")
@require_auth
def api_mon_email_code():
    """Renvoie un code a l'adresse deja declaree."""
    compte = _mon_compte()
    if compte is None or not compte.get("email"):
        return jsonify({"error": "Déclare d'abord une adresse."}), 400
    en_cours = compte.get("email_code") or {}
    attente = CODE_EMAIL_DELAI - (int(time.time()) - en_cours.get("envoye", 0))
    if attente > 0:
        # Un bouton qui renvoie sans limite est un moyen d'inonder une boite
        # mail que la personne ne possede peut-etre pas.
        return jsonify({"error": f"Un code vient d'être envoyé. Attends "
                                 f"{attente} seconde{'s' if attente > 1 else ''}."}), 429

    comptes = lire_utilisateurs()
    nom = utilisateur_courant()
    code = poser_code_email(comptes[nom])
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Code non enregistré : {e}"}), 500
    try:
        envoyer_code_email(comptes[nom]["email"], nom, code)
    except Exception as e:
        return jsonify({"error": detail_pour_admin(
            "Le mail n'est pas parti. Réessaie dans un instant.", e)}), 502
    return jsonify({"ok": True})


@flask_app.post("/api/mon-compte/email/confirmer")
@require_auth
def api_mon_email_confirmer():
    """Confirme l'adresse avec le code recu."""
    compte = _mon_compte()
    if compte is None:
        return jsonify({"error": "Rien à confirmer."}), 400
    comptes = lire_utilisateurs()
    nom = utilisateur_courant()
    code = (request.get_json(force=True, silent=True) or {}).get("code")
    ok, message = verifier_code_email(comptes[nom], code)
    try:
        # Ecrit dans les deux cas : le compteur d'essais et l'expiration
        # consommee doivent survivre a la requete, sinon la limite ne limite
        # rien.
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"État non enregistré : {e}"}), 500
    if not ok:
        return jsonify({"error": message}), 400
    return jsonify({"ok": True})


@flask_app.post("/mot-de-passe/oubli")
def mot_de_passe_oubli():
    """Envoie un code de reinitialisation, si tout s'y prete.

    LA REPONSE EST TOUJOURS LA MEME, quoi qu'il arrive : compte inconnu,
    adresse absente, adresse non verifiee, envoi impossible. Repondre
    autrement transformerait ce formulaire en annuaire -- on y taperait des
    noms jusqu'a trouver ceux qui existent.

    Le compte d'administration n'a pas d'adresse : son mot de passe est celui
    de credentials.env, et il ne se reinitialise pas d'ici. La page le dit,
    et c'est une information publique -- elle ne revele l'existence de
    personne.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    nom = nom_utilisateur_valide((request.get_json(force=True, silent=True) or {}).get("nom"))
    register_failed_attempt()   # compte dans la limite : un envoi de mail coute

    comptes = lire_utilisateurs()
    compte = comptes.get(nom) if nom else None
    # OPTION A : la reinitialisation par mail n'est ouverte qu'aux comptes qui
    # ont un facteur HORS de la boite mail (application ou Face ID). Sinon,
    # la boite mail seule suffirait a prendre le compte : elle donnerait le
    # mot de passe ET le code de connexion. Ces comptes passent par
    # l'administrateur.
    if (compte and compte.get("email") and compte.get("email_verifie")
            and facteur_fort(nom, compte)):
        en_cours = compte.get("reinit_code") or {}
        # Pas plus d'un envoi par minute et par compte : sans cela, ce
        # formulaire devient un robinet a mails vers l'adresse de quelqu'un
        # d'autre.
        if int(time.time()) - int(en_cours.get("envoye") or 0) >= CODE_EMAIL_DELAI:
            code = preparer_code_reinit(compte)
            try:
                envoyer_code_reinit(compte["email"], nom, code)
                ecrire_utilisateurs(comptes)
                journaliser("mot-de-passe", qui=nom, action="code envoye",
                            ip=_adresse_client())
            except Exception as e:                                # noqa: BLE001
                # On ne le dit pas au visiteur -- ce serait lui apprendre que
                # le compte existe -- mais l'administrateur doit le voir.
                journaliser("mot-de-passe", qui=nom,
                            action=f"envoi impossible ({type(e).__name__})",
                            ip=_adresse_client())
    return jsonify({"ok": True})


@flask_app.post("/mot-de-passe/reinitialiser")
def mot_de_passe_reinitialiser():
    """Pose le nouveau mot de passe, code en main.

    N'ouvre pas de session : on se reconnecte ensuite, second facteur
    compris. Un code recu par mail prouve qu'on releve l'adresse, pas qu'on
    est la personne -- le second facteur, lui, reste exige.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    d = request.get_json(force=True, silent=True) or {}
    nom = nom_utilisateur_valide(d.get("nom"))
    nouveau = (d.get("nouveau") or "").strip()
    if len(nouveau) < MDP_MIN:
        return jsonify({"error": f"Mot de passe : {MDP_MIN} caractères au minimum."}), 400

    comptes = lire_utilisateurs()
    compte = comptes.get(nom) if nom else None
    if not compte:
        register_failed_attempt()
        # Message identique a celui d'un code faux : meme raison qu'au-dessus.
        return jsonify({"error": "Code incorrect."}), 400

    ok, message = verifier_code_reinit(compte, d.get("code"))
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"État non enregistré : {e}"}), 500
    if not ok:
        register_failed_attempt()
        return jsonify({"error": message}), 400

    sel = secrets.token_hex(16)
    compte["sel"] = sel
    compte["hash"] = derive_mot_de_passe(nouveau, sel)
    # Le mail vient de servir : il ne sera pas, en plus, le second facteur.
    compte["code_mail_suspendu"] = True
    fermer_les_autres_sessions(compte, nom=nom)
    compte.pop("code_connexion", None)
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Mot de passe non enregistré : {e}"}), 500
    journaliser("mot-de-passe", qui=nom, action="reinitialise par mail",
                ip=_adresse_client())
    return jsonify({"ok": True})


# ------------------------------ invitations ------------------------------

def _invitation_publique(empreinte, inv):
    return {"id": empreinte, "email": inv.get("email") or "",
            "projets": sorted(inv.get("projets") or []),
            "cree": inv.get("cree"), "expire": inv.get("expire")}


def _envoyer_et_rendre(empreinte, jeton, adresse):
    """Envoie le lien et rend la reponse. Le lien est TOUJOURS rendu a
    l'administrateur : si le mail ne part pas (pas de serveur d'envoi,
    Mailpit en mode test), il peut le transmettre lui-meme."""
    lien = lien_invitation(jeton)
    envoye, detail = True, ""
    try:
        envoyer_invitation(adresse, lien)
    except Exception as e:                                        # noqa: BLE001
        envoye, detail = False, f"{type(e).__name__}: {e}"
    _, relais = smtp_utilisable()
    return jsonify({"ok": True, "id": empreinte, "lien": lien, "envoye": envoye,
                    # Remis a Mailpit sans serveur d'envoi : garde, pas parti.
                    "relaye": envoye and relais, "detail": detail})


@flask_app.get("/api/invitations")
@require_admin
def api_invitations():
    return jsonify({"invitations": sorted(
        (_invitation_publique(k, v) for k, v in lire_invitations().items()),
        key=lambda i: -(i["cree"] or 0))})


@flask_app.post("/api/invitations")
@require_admin
def api_invitation_creer():
    refus = _refus_sans_smtp()
    if refus:
        return refus
    d = request.get_json(force=True, silent=True) or {}
    adresse = email_valide(d.get("email"))
    if not adresse:
        return jsonify({"error": "Adresse mail invalide."}), 400
    if any((c.get("email") or "").lower() == adresse.lower()
           for c in lire_utilisateurs().values() if isinstance(c, dict)):
        return jsonify({"error": "Un compte utilise déjà cette adresse."}), 400
    try:
        empreinte, jeton = creer_invitation(adresse, _projets_valides(d.get("projets"), load()))
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Invitation non enregistrée : {e}"}), 500
    journaliser("compte", qui=utilisateur_courant() or NOM_ADMIN, cible=adresse,
                action="invitation", ip=_adresse_client())
    return _envoyer_et_rendre(empreinte, jeton, adresse)


@flask_app.post("/api/invitations/<empreinte>/renvoyer")
@require_admin
def api_invitation_renvoyer(empreinte):
    """Un nouveau lien : l'ancien, peut-etre perdu dans un mail, ne vaut plus
    rien. Le delai repart de zero."""
    refus = _refus_sans_smtp()
    if refus:
        return refus
    inv = lire_invitations().get(empreinte)
    if not inv:
        return jsonify({"error": "Invitation inconnue ou expirée."}), 404
    try:
        nouvelle, jeton = creer_invitation(inv["email"], inv.get("projets") or [])
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Invitation non enregistrée : {e}"}), 500
    return _envoyer_et_rendre(nouvelle, jeton, inv["email"])


@flask_app.delete("/api/invitations/<empreinte>")
@require_admin
def api_invitation_annuler(empreinte):
    invitations = lire_invitations()
    if invitations.pop(empreinte, None) is None:
        return jsonify({"error": "Invitation inconnue ou expirée."}), 404
    try:
        ecrire_invitations(invitations)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Invitation non annulée : {e}"}), 500
    return jsonify({"ok": True})


@flask_app.get("/invitation/<jeton>")
def invitation_page(jeton):
    """La page de connexion, en mode invitation : c'est son script qui lit
    le jeton dans l'adresse. Une seule page a maintenir."""
    return login_page(invitation=True)


@flask_app.get("/api/invitation/<jeton>")
def api_invitation_etat(jeton):
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    _, inv = invitation_par_jeton(jeton)
    if not inv:
        register_failed_attempt()
        return jsonify({"valide": False}), 404
    return jsonify({"valide": True, "email": inv["email"]})


@flask_app.post("/invitation/<jeton>")
def invitation_accepter(jeton):
    """Cree le compte et ouvre la session.

    Le lien recu par mail tient lieu de second facteur pour CETTE premiere
    connexion : il prouve qu'on releve l'adresse, il ne sert qu'une fois et
    il expire. Les connexions suivantes demandent le mot de passe et un code
    -- ou Face ID, que la page propose d'activer aussitot.
    """
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    empreinte, inv = invitation_par_jeton(jeton)
    if not inv:
        register_failed_attempt()
        return jsonify({"error": "Ce lien d'invitation n'est plus valable. "
                                 "Demande-en un nouveau à ton administrateur."}), 404
    d = request.get_json(force=True, silent=True) or {}
    nom = nom_utilisateur_valide(d.get("nom"))
    mdp = (d.get("mot_de_passe") or "").strip()
    if not nom:
        return jsonify({"error": "Nom invalide : 2 à 32 caractères, "
                                 "minuscules, chiffres, tiret ou souligné."}), 400
    if nom == NOM_ADMIN:
        return jsonify({"error": "Ce nom est réservé."}), 400
    if len(mdp) < MDP_MIN:
        return jsonify({"error": f"Mot de passe : {MDP_MIN} caractères au minimum."}), 400

    comptes = lire_utilisateurs()
    if nom in comptes:
        return jsonify({"error": "Ce nom est déjà pris."}), 400
    sel = secrets.token_hex(16)
    comptes[nom] = {
        "sel": sel,
        "hash": derive_mot_de_passe(mdp, sel),
        "projets": _projets_valides(inv.get("projets"), load()),
        "email": inv["email"],
        # Le lien est arrive a cette adresse et en est revenu : elle est
        # verifiee, sans code a recopier.
        "email_verifie": True,
        "cree": int(time.time()),
    }
    invitations = lire_invitations()
    invitations.pop(empreinte, None)
    try:
        # L'invitation d'abord : un lien qui survivrait au compte cree
        # permettrait d'en creer un second.
        ecrire_invitations(invitations)
        ecrire_utilisateurs(comptes)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Compte non enregistré : {e}"}), 500
    journaliser("compte", qui=nom, action="invitation acceptée", ip=_adresse_client())
    return _ouvrir_session_utilisateur(nom)


@flask_app.get("/qr/totp.svg")
def qr_totp():
    """Le QR code de l'inscription en cours, et de rien d'autre.

    L'adresse otpauth vient de la session signee, jamais de l'URL : un
    secret dans une adresse se retrouve dans l'historique du navigateur,
    dans les journaux d'acces et dans le referer de la page suivante.

    Pas d'authentification a exiger ici -- une session qui porte une
    inscription en attente n'est pas encore authentifiee, c'est justement
    l'etape ou on se trouve. Ce que la route revele, c'est le secret que le
    serveur vient de tirer pour CETTE session.
    """
    uri = session.get("totp_uri") or ""
    svg = qr_svg(uri) if uri else ""
    if not svg:
        # 404 et non 500 : sans bibliotheque QR, la page affiche la cle a
        # saisir a la main et l'image manquante se masque d'elle-meme.
        return Response("", status=404)
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@flask_app.get("/api/categories")
@require_auth
def api_categories():
    """Lisible par tous les comptes : le hub s'en sert pour se ranger.

    « verrouillees » : celles que la page montre sans les laisser modifier.
    """
    return jsonify({"categories": categories_affichees(), "verrouillees": [CATEGORIE_CODELAB]})


@flask_app.put("/api/categories")
@require_admin
def api_categories_enregistrer():
    """Remplace la liste entiere, dans l'ordre recu.

    Renvoie le nombre de projets qui perdent leur rangement, pour que la page
    puisse le dire : supprimer une categorie ne casse rien, mais cela deplace
    des projets, et cela doit se voir.
    """
    brut = (request.get_json(force=True, silent=True) or {}).get("categories")
    if not isinstance(brut, list):
        return jsonify({"error": "Liste de catégories attendue."}), 400

    propres, vues = [], set()
    for x in brut:
        c = categorie_propre(x)
        # CodeLab revient tel quel avec la liste (la page l'affiche) : il
        # n'est pas enregistre, il est pose d'office.
        if est_categorie_codelab(c):
            continue
        # Insensible a la casse pour les doublons : "Outils" et "outils"
        # seraient deux tiroirs pour la meme chose.
        if c and c.lower() not in vues:
            propres.append(c)
            vues.add(c.lower())
    if len(propres) > CATEGORIES_MAX:
        return jsonify({"error": f"{CATEGORIES_MAX} catégories au maximum."}), 400

    apps = load()
    orphelins = [n for n, a in apps.items()
                 if (a.get("categorie") or "") and a["categorie"] not in propres]
    try:
        ecrire_categories(propres)
    except OSError as e:
        return jsonify({"error": f"Catégories non enregistrées : {e}"}), 500

    # Les projets d'une categorie disparue redeviennent non ranges, tout de
    # suite : un champ qui pointe vers un tiroir inexistant se rappellerait a
    # nous plus tard, au pire moment.
    if orphelins:
        for n in orphelins:
            apps[n]["categorie"] = ""
        save(apps)
    return jsonify({"ok": True, "categories": [CATEGORIE_CODELAB] + propres,
                    "verrouillees": [CATEGORIE_CODELAB], "declasses": len(orphelins)})


@flask_app.get("/api/securite")
@require_admin
def api_securite():
    """L'etat des reglages de securite, pour la page Parametres."""
    return jsonify({
        "totp": totp_actif(),
        # Deux choses distinctes, qu'il ne faut pas confondre dans la page :
        #
        #   https_constate : ce que la requete en cours montre reellement ;
        #   https          : ce que le panneau a ete REGLE a croire.
        #
        # Les afficher separement, c'est repondre a la seule question utile
        # quand rien ne marche : est-ce le TLS qui manque, ou le reglage ?
        "https_constate": request.headers.get("X-Forwarded-Proto", "").lower() == "https"
                          or request.scheme == "https",
        "https": https_actif(),
        "cookie_secure": https_actif(),
        "adresse_publique": adresse_publique(),
        # Toujours vrai : plus un reglage. Garde pour la page et le diagnostic.
        "admin_reseau_local": admin_limite_au_reseau_local(),
        "client_local": client_est_local(),
        "client_ip": _client_ip(),
        # Les applications sont-elles servies dans une autre origine que le
        # panneau ? C'est ce qui empeche une XSS dans l'une d'elles d'atteindre
        # le panneau, et c'est invisible sans le dire.
        # Le nombre de cles d'acces du compte connecte : le bilan de securite
        # en a besoin, et un second appel pour un entier serait du gaspillage.
        "passkeys": len(passkeys_du_compte(utilisateur_courant() or NOM_ADMIN)),
        "origines_separees": origines_separees(),
        "adresse_applications": modele_adresse_application(),
        "port_applications": APPS_PORT,
    })


@flask_app.post("/api/compte/mot-de-passe")
@require_auth
def api_changer_mot_de_passe():
    """Change le mot de passe du compte connecte, le sien seulement.

    Pourquoi cela n'existait pas, et pourquoi c'est un manque : le mot de
    passe d'administration ne se changeait qu'en editant credentials.env sur
    le serveur, c'est-a-dire en s'y connectant en SSH. Un secret qu'on ne
    peut pas changer facilement est un secret qu'on ne change jamais -- et
    celui-la donne l'execution de commandes sur la machine.

    L'ANCIEN MOT DE PASSE EST EXIGE, meme pour une session deja ouverte. Une
    session volee ne doit pas pouvoir verrouiller le compte de son
    proprietaire : sans cette verification, un cookie capture suffirait a
    prendre la place de quelqu'un definitivement.
    """
    d = request.get_json(force=True, silent=True) or {}
    ancien = (d.get("ancien") or "").strip()
    nouveau = (d.get("nouveau") or "").strip()

    if len(nouveau) < MDP_MIN:
        return jsonify({"error": f"Mot de passe : {MDP_MIN} caractères au minimum."}), 400
    if nouveau == ancien:
        return jsonify({"error": "Le nouveau mot de passe est identique à l'ancien."}), 400

    if est_admin():
        # LE COMPTE D'ADMINISTRATION NE CHANGE PAS SON MOT DE PASSE ICI.
        #
        # Le sien est celui de credentials.env, et ce fichier est la source :
        # c'est lui qu'on lit pour se depanner quand le panneau ne repond
        # plus, lui qu'on copie en changeant de machine, lui que le
        # diagnostic controle. Laisser le panneau le reecrire, c'etait
        # accepter deux sources pour un meme secret -- et decouvrir laquelle
        # fait foi le jour ou elles divergent, c'est-a-dire au pire moment.
        return jsonify({
            "error": "Le mot de passe d'administration se change dans "
                     "credentials.env, sur le serveur — c'est lui qui fait "
                     "foi. Le panneau ne le réécrit pas."}), 403

    nom = utilisateur_courant()
    comptes = lire_utilisateurs()
    compte = comptes.get(nom)
    if not compte:
        return jsonify({"error": "Compte introuvable."}), 404
    if not verifie_mot_de_passe(compte, ancien):
        register_failed_attempt()
        journaliser("echec", qui=nom, motif="changement de mot de passe",
                    ip=_adresse_client())
        return jsonify({"error": "Ancien mot de passe incorrect."}), 403
    compte["sel"] = secrets.token_hex(16)
    compte["hash"] = derive_mot_de_passe(nouveau, compte["sel"])
    fermer_les_autres_sessions(compte, garder_celle_ci=True, nom=nom)
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Non enregistré : {e}"}), 500
    journaliser("mot-de-passe", qui=nom, ip=_adresse_client())
    return jsonify({"ok": True})


@flask_app.post("/api/securite/totp/preparer")
@require_admin
def api_totp_preparer():
    """Tire un secret candidat, sans rien enregistrer.

    Rien n'est persiste tant qu'un code valide n'a pas ete fourni : un secret
    mal recopie dans l'application d'authentification enfermerait dehors des
    le prochain retour sur la page de connexion.
    """
    if totp_actif():
        return jsonify({"error": "La double authentification est déjà activé."}), 400
    candidat = totp_nouveau_secret()
    session["totp_candidat"] = candidat
    session["totp_uri"] = totp_uri(candidat)
    return jsonify({"secret": candidat, "uri": session["totp_uri"],
                    "compte": TOTP_COMPTE, "qr": "/qr/totp.svg"})


@flask_app.post("/api/securite/totp/activer")
@require_admin
def api_totp_activer():
    global _totp_secret
    candidat = session.get("totp_candidat")
    if not candidat:
        return jsonify({"error": "Recommence la préparation : aucun secret en attente."}), 400
    code = (request.get_json(force=True, silent=True) or {}).get("code")
    if not totp_verifie(candidat, code):
        return jsonify({"error": "Code incorrect. Vérifie l'heure de ton téléphone."}), 400
    if not ecrire_bloc_panneau(admin_password(), flask_app.secret_key, candidat):
        return jsonify({"error": "credentials.env n'a pas pu être écrit : rien n'a été activé."}), 500
    _totp_secret = candidat
    session.pop("totp_candidat", None)
    session.pop("totp_uri", None)
    return jsonify({"ok": True})


@flask_app.post("/api/securite/totp/desactiver")
@require_admin
def api_totp_desactiver():
    """Desactivation protegee par un code valide.

    La session admin seule ne suffit pas : une session volee pourrait sinon
    retirer le second facteur, ce qui reviendrait a ne pas en avoir.
    """
    global _totp_secret
    if not totp_actif():
        return jsonify({"ok": True})
    code = (request.get_json(force=True, silent=True) or {}).get("code")
    if not totp_verifie(_totp_secret, code):
        register_failed_attempt()
        return jsonify({"error": "Code incorrect."}), 400
    if not ecrire_bloc_panneau(admin_password(), flask_app.secret_key, ""):
        return jsonify({"error": "credentials.env n'a pas pu être écrit."}), 500
    _totp_secret = ""
    return jsonify({"ok": True})


@flask_app.get("/api/alertes")
@require_admin
def api_alertes():
    """L'etat des alertes et des deux configurations d'envoi.

    Aucun mot de passe n'est renvoye -- seulement le fait qu'il existe. Un
    champ de mot de passe pre-rempli est une valeur qu'on renvoie sans le
    vouloir a chaque enregistrement, et un secret qui traine dans une page
    ouverte.
    """
    reglages = lire_alertes()
    par_app = reglage("alertes_par_app", {}) or {}
    origine = smtp_origine()
    personnalisee = lire_smtp_personnalise()
    effective, manquants = config_smtp()

    def _vue(cfg):
        if cfg is None:
            return None
        return {"host": cfg["host"], "port": cfg["port"], "tls": cfg["tls"],
                "user": cfg["user"], "expediteur": cfg["expediteur"],
                "mot_de_passe_defini": bool(cfg["password"])}

    return jsonify({
        "actif": reglages["actif"],
        "admin": alertes_admin(),
        # Les deux configurations, cote a cote : la page doit pouvoir dire
        # laquelle sert et laquelle attend en repli.
        "origine": _vue(origine),
        "origine_utilisable": not smtp_incomplet(origine),
        "personnalisee": _vue(personnalisee),
        "source": "personnalisee" if personnalisee else "origine",
        "manquants": manquants,
        "smtp_ok": not smtp_incomplet(effective),
        "incidents": sorted(_alertes_en_cours),
        # Les destinataires propres a chaque application, pour la page.
        "par_application": {nom: _adresses(par_app.get(nom))
                            for nom in sorted(load())},
    })


@flask_app.post("/api/alertes")
@require_admin
def api_alertes_enregistrer():
    """Enregistre l'interrupteur, l'adresse d'administration, et -- si le
    formulaire en apporte une -- une configuration d'envoi personnalisee.

    CELLE-CI DOIT FAIRE SES PREUVES AVANT D'ETRE ECRITE. On se connecte
    reellement au serveur, on chiffre, on s'authentifie. C'est ce qui
    remplace l'ancien bouton "envoyer un mail de test" : un test qu'il
    fallait penser a lancer, et dont l'oubli ne se voyait pas. Ici, une
    configuration qui ne marche pas n'entre tout simplement pas.

    La configuration d'origine (credentials.env) n'est jamais touchee : elle
    reste le repli.
    """
    d = request.get_json(force=True, silent=True) or {}

    admin = _adresses(d.get("admin"))
    actif = bool(d.get("actif"))
    if actif and not admin:
        return jsonify({"error": "Une adresse d'alerte de l'administrateur est "
                                 "nécessaire pour activer les alertes : c'est "
                                 "elle qui reçoit ce qu'aucune application "
                                 "n'a pris en charge."}), 400

    # --- la configuration personnalisee -----------------------------------
    if "smtp" in d:
        smtp = d.get("smtp")
        if not smtp or not str((smtp or {}).get("host") or "").strip():
            # Champ serveur vide = revenir a la configuration d'origine.
            # Toujours permis : c'est la marche arriere.
            effacer_smtp_personnalise()
        else:
            candidate = _normalise_smtp(smtp)
            # Mot de passe vide = inchange. Le formulaire ne le pre-remplit
            # pas : sans cette regle, tout enregistrement l'effacerait.
            if not candidate["password"]:
                ancienne = lire_smtp_personnalise()
                if ancienne and ancienne["host"] == candidate["host"] \
                        and ancienne["user"] == candidate["user"]:
                    candidate["password"] = ancienne["password"]
            ok, detail = verifier_smtp(candidate)
            if not ok:
                return jsonify({
                    "error": "Ce serveur d'envoi n'a pas repondu comme attendu, "
                             "rien n'a été enregistré. La configuration "
                             "d'origine continue de servir.",
                    "detail": detail}), 400
            try:
                ecrire_smtp_personnalise(candidate)
            except (OSError, RuntimeError) as e:
                return jsonify({"error": f"Configuration non enregistrée : {e}"}), 500

        # Mailpit relaie par ce meme serveur : il suit le changement.
        publier_relais_mail()

    # --- l'adresse d'administration, dans le bloc partage ------------------
    try:
        ecrire_reglage("alertes_admin", admin or None)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Adresse non enregistrée : {e}"}), 500

    try:
        ecrire_alertes(actif, admin)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Réglages non enregistrés : {e}"}), 500

    _, manquants = config_smtp()
    return jsonify({"ok": True, "manquants": manquants,
                    "source": "personnalisee" if lire_smtp_personnalise() else "origine"})


@flask_app.put("/api/alertes/application/<name>")
@require_admin
def api_alertes_application(name):
    """Les destinataires propres a une application.

    Une application de facturation ne previent pas les memes personnes qu'un
    site vitrine. L'adresse d'administration s'ajoute toujours a celles-ci --
    une application dont on a oublie de remplir la liste ne tombe donc jamais
    en silence.
    """
    apps = load()
    if name not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    d = request.get_json(force=True, silent=True) or {}
    adresses = _adresses(d.get("alertes"))
    par_app = reglage("alertes_par_app", {}) or {}
    if adresses:
        par_app[name] = adresses
    else:
        par_app.pop(name, None)
    try:
        ecrire_reglage("alertes_par_app", par_app or None)
    except (OSError, RuntimeError) as e:
        return jsonify({"error": f"Non enregistré : {e}"}), 500
    return jsonify({"ok": True, "alertes": adresses,
                    "destinataires": destinataires_alerte(name, apps)})


@flask_app.get("/api/utilisateurs")
@require_admin
def api_utilisateurs():
    """Les comptes, sans rien qui ressemble a un mot de passe.

    Ni le hash ni le sel ne sortent d'ici : les afficher n'aide personne et
    les met dans l'historique du navigateur.
    """
    comptes = lire_utilisateurs()
    return jsonify({"utilisateurs": [
        {"nom": nom,
         "projets": sorted(p for p in c.get("projets", []) if not est_app_par_defaut(p)),
         # Pas le secret, seulement le fait qu'il existe : "en attente"
         # signale un compte cree mais jamais utilise, ce qui se voit d'un
         # coup d'oeil et se corrige en relancant la personne.
         "totp": bool(c.get("totp")),
         # Le nombre de cles, pas les cles : l'administrateur doit pouvoir
         # constater qu'un compte en a (et les retirer si l'appareil est
         # perdu), pas les lire.
         "passkeys": len(lire_passkeys().get(nom, [])),
         "email": c.get("email") or "",
         "email_verifie": bool(c.get("email_verifie")),
         # Un compte cree librement qui n'a pas encore confirme son adresse
         # n'ouvre aucune session : le dire evite de chercher pourquoi.
         "attente_email": bool(c.get("attente_email")),
         # Suspendu : le compte existe, garde tout, et n'ouvre plus rien.
         "suspendu": compte_suspendu(c),
         # Suppression demandee par le compte lui-meme : la date ou il sera
         # efface. L'administrateur le voit, et ne peut ni l'avancer ni
         # l'annuler a sa place -- c'est la decision de la personne.
         "suppression": suppression_prevue(c),
         "profil": lire_profil(nom),
         "cree": c.get("cree")}
        for nom, c in sorted(comptes.items())]})


def _projets_valides(brut, apps):
    """Ne garde que des projets qui existent vraiment.

    Un projet supprime puis recree sous le meme nom rendrait sinon un droit
    qu'on croyait perdu -- et la liste se remplirait de noms morts.
    """
    # LES APPLICATIONS RESERVEES NE S'ACCORDENT PAS. Le proxy les refusait
    # deja a un compte utilisateur, mais la fiche du compte les laissait
    # cocher et enregistrait le droit : l'administrateur croyait avoir
    # partage ce qui restait ferme. On ne garde que ce qui peut l'etre.
    return sorted({p for p in (brut or []) if isinstance(p, str) and p in apps
                   and not est_app_par_defaut(p)})


@flask_app.post("/api/utilisateurs")
@require_admin
def api_utilisateur_creer():
    refus = _refus_sans_smtp()
    if refus:
        return refus
    d = request.get_json(force=True, silent=True) or {}
    nom = nom_utilisateur_valide(d.get("nom"))
    mdp = (d.get("mot_de_passe") or "").strip()
    email = email_valide(d.get("email"))

    if d.get("email") and not email:
        return jsonify({"error": "Adresse mail invalide."}), 400
    if not nom:
        return jsonify({"error": "Nom invalide : 2 à 32 caractères, "
                                 "minuscules, chiffres, tiret ou souligné."}), 400
    if nom == NOM_ADMIN:
        return jsonify({"error": "Ce nom est celui du compte d'administration."}), 400
    if len(mdp) < MDP_MIN:
        return jsonify({"error": f"Mot de passe : {MDP_MIN} caractères au minimum."}), 400

    comptes = lire_utilisateurs()
    if nom in comptes:
        return jsonify({"error": "Ce compte existe déjà."}), 400

    sel = secrets.token_hex(16)
    comptes[nom] = {
        "sel": sel,
        "hash": derive_mot_de_passe(mdp, sel),
        "projets": _projets_valides(d.get("projets"), load()),
        "email": email,
        # Une adresse posee par l'administrateur n'est pas verifiee pour
        # autant : c'est la personne, a sa premiere visite, qui confirme
        # qu'elle la releve vraiment.
        "email_verifie": False,
        "cree": int(time.time()),
    }
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Compte non enregistré : {e}"}), 500
    return jsonify({"ok": True, "nom": nom})


@flask_app.put("/api/utilisateurs/<nom>")
@require_admin
def api_utilisateur_modifier(nom):
    d = request.get_json(force=True, silent=True) or {}
    comptes = lire_utilisateurs()
    compte = comptes.get(nom)
    if not compte:
        return jsonify({"error": "Compte inconnu."}), 404

    # Champs absents = inchanges. Le formulaire des projets et celui du mot
    # de passe sont separes : envoyer l'un ne doit pas remettre l'autre a
    # zero.
    if "projets" in d:
        compte["projets"] = _projets_valides(d.get("projets"), load())
    # Suspendre / reactiver. Un booleen, et rien d'autre : les projets
    # autorises, l'adresse et les cles d'acces restent en place, c'est ce qui
    # distingue une suspension d'une suppression.
    if "suspendu" in d:
        if d.get("suspendu"):
            compte["suspendu"] = True
        else:
            compte.pop("suspendu", None)
        journaliser("compte", qui=utilisateur_courant() or NOM_ADMIN, cible=nom,
                    action="suspendu" if d.get("suspendu") else "réactivé",
                    ip=_adresse_client())
    mdp = (d.get("mot_de_passe") or "").strip()
    if mdp:
        if len(mdp) < MDP_MIN:
            return jsonify({"error": f"Mot de passe : {MDP_MIN} caractères au minimum."}), 400
        compte["sel"] = secrets.token_hex(16)
        compte["hash"] = derive_mot_de_passe(mdp, compte["sel"])
        fermer_les_autres_sessions(compte, nom=nom)

    # Telephone perdu ou remplace : on efface le secret, et la personne
    # s'inscrit de nouveau a sa prochaine connexion. C'est le seul moyen de
    # rendre l'acces sans jamais transmettre un secret par un canal tiers --
    # l'administrateur ne connait a aucun moment le facteur de quelqu'un
    # d'autre.
    if "email" in d:
        adresse = email_valide(d.get("email"))
        if d.get("email") and not adresse:
            return jsonify({"error": "Adresse mail invalide."}), 400
        if adresse != (compte.get("email") or ""):
            compte["email"] = adresse
            # Changer l'adresse annule la verification : sinon il suffirait
            # de remplacer une adresse verifiee par une autre pour heriter
            # de son statut.
            compte["email_verifie"] = False
            compte.pop("email_code", None)

    if d.get("reinitialiser_totp"):
        compte.pop("totp", None)

    # Appareil perdu : l'administrateur pouvait deja remettre le second
    # facteur a zero, mais pas retirer les cles d'acces -- le compte restait
    # ouvrable par un telephone egare. Meme geste, meme raison.
    if d.get("retirer_passkeys"):
        # Appareil perdu : la session qu'il porte peut-etre tombe aussi.
        fermer_les_autres_sessions(compte, nom=nom)
        tout = lire_passkeys()
        if tout.pop(nom, None) is not None:
            try:
                ecrire_passkeys(tout)
            except OSError as e:
                return jsonify({"error": f"Clés non retirées : {e}"}), 500
            journaliser("passkey", qui=nom, action="retrait par l'administrateur",
                        ip=_adresse_client())

    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Compte non enregistré : {e}"}), 500
    return jsonify({"ok": True})


@flask_app.get("/api/apps/<name>/acces")
@require_admin
def api_app_acces(name):
    """Qui accede a cette application.

    La meme information que dans la fiche d'un compte, prise par l'autre
    bout. On l'avait dans un seul sens : pour savoir qui ouvrait une
    application, il fallait ouvrir les fiches une par une -- et pour donner
    l'acces a cinq personnes, cinq allers-retours.
    """
    apps = load()
    if name not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    comptes = lire_utilisateurs()
    return jsonify({
        "application": name,
        "publique": (apps[name].get("visibility") or "privee") == "publique",
        "comptes": [{"nom": nom,
                     "acces": name in (c.get("projets") or []),
                     "email": c.get("email") or ""}
                    for nom, c in sorted(comptes.items())],
    })


@flask_app.put("/api/apps/<name>/acces")
@require_admin
def api_app_acces_modifier(name):
    """Donne ou retire l'acces a cette application, compte par compte.

    N'ecrit QUE cette application dans chaque fiche : les autres projets d'un
    compte ne sont pas touches. Envoyer la liste complete des projets aurait
    efface en silence ce qu'un autre onglet ouvert venait d'accorder.
    """
    # Une application par defaut ne s'accorde pas : elle est reservee a
    # l'administrateur, et le proxy le tient de son cote.
    refus = refus_configuration(name)
    if refus:
        return jsonify({"error": refus}), 403
    apps = load()
    if name not in apps:
        return jsonify({"error": "Application inconnue."}), 404
    d = request.get_json(force=True, silent=True) or {}
    voulus = {str(n).strip() for n in (d.get("utilisateurs") or []) if str(n).strip()}

    comptes = lire_utilisateurs()
    inconnus = sorted(voulus - set(comptes))
    if inconnus:
        return jsonify({"error": "Compte inconnu : " + ", ".join(inconnus)}), 400

    for nom, compte in comptes.items():
        projets = [p for p in (compte.get("projets") or []) if p != name]
        if nom in voulus:
            projets.append(name)
        compte["projets"] = sorted(set(projets))
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Non enregistré : {e}"}), 500
    return jsonify({"ok": True, "utilisateurs": sorted(voulus)})


@flask_app.delete("/api/utilisateurs/<nom>")
@require_admin
def api_utilisateur_supprimer(nom):
    comptes = lire_utilisateurs()
    if nom not in comptes:
        return jsonify({"error": "Compte inconnu."}), 404
    comptes.pop(nom)
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Compte non supprimé : {e}"}), 500

    # Les cles d'acces partent avec le compte. Les laisser serait pire qu'un
    # oubli de menage : recreer un compte du meme nom lui rendrait les cles
    # de l'ancien, et l'appareil de la personne partie rouvrirait la porte.
    cles = lire_passkeys()
    if cles.pop(nom, None) is not None:
        try:
            ecrire_passkeys(cles)
        except OSError as e:
            return jsonify({"error": f"Compte supprimé, mais ses clés d'accès "
                                     f"n'ont pas pu être retirées : {e}"}), 500
    # La session de ce compte, si elle existe, tombera d'elle-meme : chaque
    # controle relit le registre, et un compte absent n'autorise plus rien.
    return jsonify({"ok": True})


# ---------------------- supprimer son propre compte ----------------------
#
# Vecu : « proposer de supprimer le compte, avec suppression des donnees au
# bout de 30 jours si pas de changement d'avis (bien sur, l'admin n'a pas le
# droit de supprimer ce compte) ».
#
# TROIS TEMPS. La demande ferme tout de suite toutes les sessions du compte et
# n'efface rien. Pendant 30 jours, se reconnecter ne mene qu'a une page : la
# date, « Annuler la suppression » ou « Me deconnecter » (le panneau, les
# projets et l'API restent fermes). Passe ce delai, la purge quotidienne
# efface le compte et ses donnees.
#
# POURQUOI : une suppression immediate ne laisse aucun retour sur un clic de
#   trop, et une session volee pourrait effacer le compte de son
#   proprietaire. D'ou le mot de passe exige, comme pour en changer, et le
#   delai.
# REGLE : le compte d'administration ne se supprime pas (il vit dans
#   credentials.env, et sans lui personne n'administre plus rien). Refuse
#   ici, cote serveur ; la page ne fait que ne pas proposer le bouton.
# DEPEND DE / PROTEGE AUSSI : projets_autorises() (plus aucun projet pendant
#   le delai), refuser_un_compte_en_suppression() (avant chaque requete),
#   et la purge du journal (demarrer_la_purge_du_journal), qui appelle
#   purger_les_comptes_supprimes().
# ECARTE : annuler la suppression a la simple reconnexion. Une connexion par
#   habitude (cle d'acces enregistree) l'annulerait sans que la personne l'ait
#   voulu ; et prevenir sur la page de connexion dirait qu'un compte existe
#   (regle 10).
DELAI_SUPPRESSION_COMPTE = 30 * 86400


def suppression_prevue(compte):
    """L'instant ou le compte sera efface (secondes), ou None."""
    demande = (compte or {}).get("suppression_demandee")
    if not isinstance(demande, (int, float)) or isinstance(demande, bool):
        return None
    return int(demande) + DELAI_SUPPRESSION_COMPTE


def compte_en_suppression(compte):
    return suppression_prevue(compte) is not None


def _date_lisible(ts):
    mois = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre")
    d = datetime.datetime.fromtimestamp(ts)
    return f"{d.day} {mois[d.month - 1]} {d.year}"


def prevenir_de_la_suppression(compte, nom, prevue):
    """Un mail a l'adresse verifiee du compte, si l'envoi est regle. Jamais
    d'exception : la demande est enregistree, le mail n'est qu'un avis."""
    adresse = compte.get("email") if compte.get("email_verifie") else ""
    if not adresse:
        return
    try:
        cfg, ok = smtp_utilisable()
        if not ok:
            return
        envoyer_mail(cfg, "[CodeLab] suppression de ton compte",
                     f"La suppression du compte « {nom} » a ete demandee.\n\n"
                     f"Il sera efface le {_date_lisible(prevue)}, avec ses donnees. "
                     "D'ici la, reconnecte-toi au panneau pour annuler.\n\n"
                     "Si tu n'es pas a l'origine de cette demande, reconnecte-toi, "
                     "annule, puis change ton mot de passe.\n\n"
                     "-- CodeLab, panneau de gestion des applications",
                     destinataires=[adresse], sensible=True)
    except Exception as e:                                        # noqa: BLE001
        print(f"[app-manager] avis de suppression non envoye ({type(e).__name__}).",
              flush=True)


@flask_app.post("/api/mon-compte/suppression")
@require_auth
def api_mon_compte_supprimer():
    if est_admin():
        return jsonify({"error": "Le compte d'administration ne se supprime pas."}), 403
    d = request.get_json(force=True, silent=True) or {}
    mot_de_passe = (d.get("mot_de_passe") or "").strip()
    nom = utilisateur_courant()
    comptes = lire_utilisateurs()
    compte = comptes.get(nom)
    if not compte:
        return jsonify({"error": "Compte introuvable."}), 404
    # Le meme verrou que la connexion : un mot de passe devine ici serait un
    # mot de passe devine tout court.
    if rate_limited():
        return jsonify({"error": "Trop de tentatives. Réessaie dans quelques minutes."}), 429
    if not verifie_mot_de_passe(compte, mot_de_passe):
        register_failed_attempt()
        journaliser("echec", qui=nom, motif="suppression du compte", ip=_adresse_client())
        return jsonify({"error": "Mot de passe incorrect."}), 403
    compte["suppression_demandee"] = int(time.time())
    # Toutes les sessions tombent, celle-ci comprise : le compte se ferme
    # maintenant, sur chaque appareil.
    fermer_les_autres_sessions(compte, nom=nom)
    try:
        ecrire_utilisateurs(comptes)
    except OSError as e:
        return jsonify({"error": f"Demande non enregistrée : {e}"}), 500
    prevue = suppression_prevue(compte)
    journaliser("compte", qui=nom, action="suppression demandée", ip=_adresse_client())
    prevenir_de_la_suppression(compte, nom, prevue)
    session.clear()
    return jsonify({"ok": True, "le": prevue, "date": _date_lisible(prevue)})


@flask_app.delete("/api/mon-compte/suppression")
@require_auth
def api_mon_compte_garder():
    """Annule la demande : le compte reprend la ou il etait, droits compris."""
    if est_admin():
        return jsonify({"ok": True})
    nom = utilisateur_courant()
    comptes = lire_utilisateurs()
    compte = comptes.get(nom)
    if not compte:
        return jsonify({"error": "Compte introuvable."}), 404
    if compte.pop("suppression_demandee", None) is not None:
        try:
            ecrire_utilisateurs(comptes)
        except OSError as e:
            return jsonify({"error": f"Annulation non enregistrée : {e}"}), 500
        journaliser("compte", qui=nom, action="suppression annulée", ip=_adresse_client())
    return jsonify({"ok": True})


# Ce qu'un compte en attente de suppression peut encore faire : lire son
# etat, annuler. /logout n'est pas sous /api et reste ouvert.
API_PENDANT_LA_SUPPRESSION = {"api_mon_compte", "api_mon_compte_garder"}
PAGES_DU_COMPTE = {"index", "page_du_panneau", "page_messagerie", "ouvrir_dans_le_panneau"}


@flask_app.before_request
def refuser_un_compte_en_suppression():
    """Pendant le delai, le compte ne voit que sa page de suppression.

    Le controle est ICI, avant chaque requete, et pas dans la page : une
    session reprise sur un autre onglet, un appel d'API garde en memoire,
    tout passe par la. Voir « supprimer son propre compte ».
    """
    if session.get("authed") is not True or est_admin():
        return None
    # Le compte lu par fermer_les_sessions_suspendues, juste avant.
    compte = g.get("compte_de_la_session")
    prevue = suppression_prevue(compte)
    if prevue is None:
        return None
    if request.endpoint in PAGES_DU_COMPTE:
        page = (SUPPRESSION_PAGE
                .replace("__JETON__", _json_dans_un_script(jeton_session()))
                .replace("__DATE__", _html.escape(_date_lisible(prevue))))
        return Response(avec_style(page), mimetype="text/html")
    if request.path.startswith("/api/") and request.endpoint not in API_PENDANT_LA_SUPPRESSION:
        return jsonify({"error": "Ton compte est en cours de suppression.",
                        "suppression": prevue}), 403
    return None


def effacer_le_compte(nom):
    """Efface le compte et tout ce qui le designe, ici et dans Postgres.

    Ce qui part : la fiche et ses droits, l'adresse (coffre), les cles
    d'acces, les appareils, les abonnements aux notifications, les
    preferences, les applications masquees et vues, les messages envoyes ou
    recus et leurs pieces jointes, et ses lignes du journal. Ce qui reste :
    les taches et projets nes d'un de ses messages (le travail de
    l'administrateur), et une ligne de journal qui dit que le compte a ete
    efface.
    """
    comptes = lire_utilisateurs()
    if comptes.pop(nom, None) is not None:
        ecrire_utilisateurs(comptes)
    cles = lire_passkeys()
    if cles.pop(nom, None) is not None:
        ecrire_passkeys(cles)
    with stockage.transaction(DB_FILE) as cx:
        for table in ("masquees", "apps_vues", "preferences", "appareils", "abonnements_push"):
            cx.execute(f"DELETE FROM {table} WHERE compte = ?", (nom,))
        cx.execute("DELETE FROM pieces WHERE qui = ?", (nom,))
        cx.execute("DELETE FROM messages WHERE qui = ? OR cible = ?", (nom, nom))
        cx.execute("DELETE FROM journal WHERE qui = ?", (nom,))
    # Postgres peut ne pas repondre : le nom attend dans le coffre, et chaque
    # purge quotidienne retente jusqu'a ce que la base soit nettoyee.
    attente = set(reglage("comptes_a_effacer_pg", []) or [])
    attente.add(nom)
    ecrire_reglage("comptes_a_effacer_pg", sorted(attente))
    _effacer_dans_postgres()
    journaliser("compte", qui="", action=f"compte « {nom} » effacé à sa demande")


def _effacer_dans_postgres():
    attente = reglage("comptes_a_effacer_pg", []) or []
    if not attente or not pg_disponible():
        return
    try:
        with _pg_connexion(PG_BASE) as cx:
            cx.execute("DELETE FROM acces WHERE qui = ANY(%s)", (attente,))
            cx.execute("DELETE FROM messages WHERE qui = ANY(%s) OR cible = ANY(%s)",
                       (attente, attente))
    except Exception as e:                                        # noqa: BLE001
        _pg_etat["erreur"] = f"{type(e).__name__}: {e}"
        return
    ecrire_reglage("comptes_a_effacer_pg", None)


def purger_les_comptes_supprimes(maintenant=None):
    """Efface les comptes dont le delai est passe. Rend leurs noms ; ne leve
    jamais, comme la purge du journal qui l'appelle."""
    maintenant = time.time() if maintenant is None else maintenant
    effaces = []
    for nom, compte in lire_utilisateurs().items():
        prevue = suppression_prevue(compte)
        if prevue is not None and prevue <= maintenant:
            try:
                effacer_le_compte(nom)
                effaces.append(nom)
            except Exception as e:                                # noqa: BLE001
                print(f"[app-manager] compte {nom} non efface : {e}", flush=True)
    try:
        _effacer_dans_postgres()
    except Exception:                                             # noqa: BLE001
        pass
    return effaces


def decrire_agent(agent):
    """« Safari sur iPhone » plutot qu'une chaine User-Agent de 200 signes."""
    a = agent or ""
    if "Edg/" in a:
        nav = "Edge"
    elif "OPR/" in a or "Opera" in a:
        nav = "Opera"
    elif "Firefox/" in a or "FxiOS" in a:
        nav = "Firefox"
    elif "Chrome/" in a or "CriOS" in a:
        nav = "Chrome"
    elif "Safari/" in a:
        nav = "Safari"
    else:
        nav = "Navigateur"
    if "iPhone" in a:
        os_ = "iPhone"
    elif "iPad" in a:
        os_ = "iPad"
    elif "Android" in a:
        os_ = "Android"
    elif "Mac OS X" in a or "Macintosh" in a:
        os_ = "Mac"
    elif "Windows" in a:
        os_ = "Windows"
    elif "Linux" in a:
        os_ = "Linux"
    else:
        os_ = ""
    return nav + (" sur " + os_ if os_ else "")


def liste_des_appareils(compte, sid_courant=None):
    return [{"id": id_public_appareil(l["sid"]), "appareil": decrire_agent(l["agent"]),
             "ip": l["ip"], "ouverte": l["ouverte"], "vue": l["vue"],
             "courant": l["sid"] == sid_courant}
            for l in appareils_du_compte(compte)]
