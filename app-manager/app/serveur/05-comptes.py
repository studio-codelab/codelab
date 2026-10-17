# CodeLab -- LE PANNEAU, COMPTES
# ---------------------------------------------------------------------------
# ROLE        les comptes, le coffre, l'adresse mail, le mot de passe oublie, le code envoye, les invitations, les cles d'acces.
# CHARGE PAR  app.py (MODULES_DU_SERVEUR), dans SA portee : ce fichier n'est
#             pas un module. Ses noms sont ceux de app, il lit et definit
#             les memes globales que le reste du panneau.
# CONTRAINTES Pas d'import de ce fichier : il n'a de sens qu'execute a son
#             rang, apres les morceaux qui le precedent. Une frontiere ne
#             coupe jamais une fonction ; une section nouvelle va dans le
#             morceau de son sujet.
# STRUCTURE   utilisateurs, coffre, adresse mail, mot de passe oublie, second facteur par code, invitations, cles d'acces.
#

# ------------------------------ utilisateurs ------------------------------
#
# Deux espaces, pas deux mots de passe pour la meme personne :
#
#   ADMINISTRATEUR -- le compte du panneau, celui dont le mot de passe est
#     genere au premier demarrage. Il voit tout et peut tout : declarer un
#     projet, le deployer, changer sa visibilite, gerer les comptes,
#     configurer les alertes, ouvrir Dagster.
#   UTILISATEUR -- un compte nomme, cree depuis le panneau, qui n'a acces
#     qu'aux projets qu'on lui a explicitement autorises, et seulement pour
#     les OUVRIR. Ni demarrage, ni arret, ni configuration, ni Dagster --
#     Dagster permet d'executer du code arbitraire, ce qui en fait un droit
#     d'administrateur deguise.
#
# Les mots de passe sont derives, jamais stockes : le fichier vit dans le
# dossier d'etat, a cote de apps.json, et une copie de sauvegarde ne doit pas
# etre une liste de mots de passe.
# ------------------------------- LE COFFRE -------------------------------
#
# Ce qui se regle DEPUIS LE PANNEAU et qui dit quelque chose de quelqu'un --
# les adresses e-mail des comptes et des alertes, le serveur d'envoi et son
# mot de passe, l'adresse de surveillance -- ne vit plus dans des fichiers
# en clair. Chaque valeur est chiffree (Fernet : AES-128-CBC + HMAC-SHA256)
# avec une cle rangee dans credentials.env (APP_MANAGER_CHIFFREMENT_CLE),
# puis :
#
#   - ecrite dans Postgres, table « reglages » de la base du panneau. C'est
#     la qu'elle vit, et elle est sauvegardee avec les autres bases ;
#   - recopiee, chiffree elle aussi, dans la base locale du panneau (table
#     reglages_chiffres de codelab.db). Une alerte doit partir PRECISEMENT
#     quand quelque chose est tombe -- Postgres compris. Le panneau ne depend
#     donc jamais de Postgres pour LIRE ses reglages : il le tient a jour, et
#     s'y reprend si la copie locale est vide.
#
# Sans la cle, ni la base ni la copie ne se lisent, et c'est le but : un dump
# de la base ou une archive de sauvegarde ne livrent aucune adresse. La
# perdre, c'est perdre ces reglages (pas les comptes, pas les applications) :
# elle se sauvegarde avec credentials.env.
_cle_chiffrement = ""
_coffre = {"donnees": None}
_coffre_verrou = threading.RLock()


def _fernet():
    from cryptography.fernet import Fernet
    if not _cle_chiffrement:
        raise RuntimeError("cle de chiffrement absente (APP_MANAGER_CHIFFREMENT_CLE)")
    return Fernet(_cle_chiffrement.encode())


def chiffrer(valeur):
    return _fernet().encrypt(json.dumps(valeur, ensure_ascii=False).encode())


def dechiffrer(jeton):
    return json.loads(_fernet().decrypt(bytes(jeton)).decode())


def _coffre_donnees():
    """Le contenu dechiffre, charge une fois. Jamais d'exception.

    Tant que la cle n'est pas chargee, rien n'est mis en cache : une lecture
    trop tot ne doit pas figer un coffre vide qu'une ecriture viendrait
    ensuite poser par-dessus le vrai.
    """
    with _coffre_verrou:
        if _coffre["donnees"] is not None:
            return _coffre["donnees"]
        if not _cle_chiffrement:
            return {}
        donnees = {}
        try:
            lignes = stockage.lire(DB_FILE, "SELECT cle, valeur FROM reglages_chiffres")
        except Exception as e:                                    # noqa: BLE001
            print(f"[app-manager] reglages illisibles ({type(e).__name__}: {e}).",
                  flush=True)
            return {}
        for ligne in lignes:
            try:
                donnees[ligne["cle"]] = dechiffrer(ligne["valeur"])
            except Exception as e:                                # noqa: BLE001
                # Cle changee ou valeur abimee : la ligne reste en base, pour
                # qu'une cle retrouvee la rende lisible ; elle est seulement
                # ignoree ici.
                print(f"[app-manager] reglage « {ligne['cle']} » illisible "
                      f"({type(e).__name__}) : la cle APP_MANAGER_CHIFFREMENT_CLE "
                      f"de credentials.env a-t-elle change ?", flush=True)
        _coffre["donnees"] = donnees
        return _coffre["donnees"]


def _coffre_ecrire(cle, valeur):
    """Ecrit une valeur chiffree en base (None = retire). Leve si l'ecriture
    echoue."""
    with stockage.transaction(DB_FILE) as cx:
        if valeur is None:
            cx.execute("DELETE FROM reglages_chiffres WHERE cle = ?", (cle,))
        else:
            cx.execute("INSERT OR REPLACE INTO reglages_chiffres (cle, valeur) "
                       "VALUES (?, ?)", (cle, chiffrer(valeur)))


def reglage(cle, defaut=None):
    """Une valeur du coffre, dechiffree. Une COPIE : la modifier ne change rien."""
    with _coffre_verrou:
        return copy.deepcopy(_coffre_donnees().get(cle, defaut))


def ecrire_reglage(cle, valeur):
    """Ecrit (None = retire) une valeur. Leve si elle ne s'ecrit pas : c'est
    a l'appelant de dire que rien n'a ete enregistre."""
    with _coffre_verrou:
        if not _cle_chiffrement:
            raise RuntimeError("cle de chiffrement absente (APP_MANAGER_CHIFFREMENT_CLE)")
        donnees = _coffre_donnees()
        try:
            _coffre_ecrire(cle, valeur)
        except Exception as e:                                    # noqa: BLE001
            raise OSError(f"reglage non enregistre : {e}") from e
        if valeur is None:
            donnees.pop(cle, None)
        else:
            donnees[cle] = copy.deepcopy(valeur)
    _pg_deposer(("reglage", cle))


# Nom reserve : le compte d'administration n'est pas dans la table des comptes, son mot
# de passe vit dans credentials.env. Laisser creer un utilisateur "admin"
# donnerait deux comptes pour un seul nom, et l'un masquerait l'autre.
NOM_ADMIN = "admin"

ROLE_ADMIN = "admin"
ROLE_UTILISATEUR = "utilisateur"

# PBKDF2-HMAC-SHA256. Pas de dependance a ajouter (hashlib est dans la
# bibliotheque standard), et un cout de calcul qui rend une liste de mots de
# passe voles inexploitable en pratique. 600 000 iterations : la valeur que
# recommande l'OWASP pour cet algorithme. L'empreinte porte son nombre
# d'iterations (« pbkdf2-sha256$600000$... ») : le jour ou il monte encore,
# chaque compte est recalcule a sa connexion suivante, sans rien casser. Une
# empreinte sans prefixe date de l'epoque des 200 000.
PBKDF2_ITERATIONS = 600_000
PBKDF2_ANCIEN = 200_000

# Le meme minimum partout ou un mot de passe nait : invitation, inscription,
# reinitialisation, administrateur, changement. Il etait de 8 a la creation
# et de 12 au changement -- le plus faible des deux faisait la regle.
MDP_MIN = 12


CHAMPS_COMPTE = (
    ("sel", "sel", "txt"), ("hash", "empreinte_mdp", "txt"),
    ("email_verifie", "email_verifie", "bool"), ("attente_email", "attente_email", "bool"),
    ("suspendu", "suspendu", "bool"), ("totp", "totp", "txt"), ("cree", "cree", "int"),
    ("sessions_depuis", "sessions_depuis", "int"),
    ("code_mail_suspendu", "code_mail_suspendu", "bool"),
)


def lire_utilisateurs():
    """Les comptes, tables comptes et acces_applications. Jamais d'exception :
    une base illisible ne doit pas empecher l'administrateur de se connecter
    pour la reparer.

    L'adresse e-mail de chaque compte vit dans le COFFRE, chiffree : elle est
    rattachee ici, pour que tout le reste du panneau lise un compte d'un seul
    tenant.
    """
    try:
        lignes = stockage.lire(DB_FILE, "SELECT * FROM comptes ORDER BY nom")
        droits = stockage.lire(DB_FILE, "SELECT compte, application FROM "
                                        "acces_applications ORDER BY rowid")
    except Exception as e:                                        # noqa: BLE001
        print(f"[app-manager] comptes illisibles ({type(e).__name__}: {e}).", flush=True)
        return {}
    projets = {}
    for ligne in droits:
        projets.setdefault(ligne["compte"], []).append(ligne["application"])
    emails = reglage("emails_comptes", {}) or {}
    comptes = {}
    for ligne in lignes:
        compte = stockage.depuis_ligne(ligne, CHAMPS_COMPTE, ligne["options"])
        compte["projets"] = projets.get(ligne["nom"], [])
        if emails.get(ligne["nom"]):
            compte["email"] = emails[ligne["nom"]]
        comptes[ligne["nom"]] = compte
    return comptes


def ecrire_utilisateurs(comptes):
    """Remplace l'ensemble des comptes, en une transaction."""
    # Les adresses partent au coffre, chiffrees ; la table n'en garde
    # aucune. D'abord le coffre : s'il echoue, les comptes ne sont pas
    # touches et rien ne se perd.
    emails = {nom: c["email"] for nom, c in comptes.items()
              if isinstance(c, dict) and c.get("email")}
    ecrire_reglage("emails_comptes", emails or None)
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM acces_applications")
        cx.execute("DELETE FROM comptes")
        for nom, c in comptes.items():
            if not isinstance(c, dict):
                continue
            reste = {k: v for k, v in c.items() if k not in ("email", "projets")}
            ligne, options = stockage.vers_ligne(reste, CHAMPS_COMPTE)
            ligne["nom"] = nom
            stockage.inserer(cx, "comptes", ligne, options)
            # Dans l'ordre donne, sans doublon : l'ordre est celui que
            # l'interface affiche.
            for application in dict.fromkeys(c.get("projets") or []):
                cx.execute("INSERT INTO acces_applications (compte, application) "
                           "VALUES (?, ?)", (nom, application))
    # APRES l'ecriture, jamais avant : le miroir relit les comptes pour les
    # recopier, et prendrait sinon l'etat d'avant la modification.
    _pg_deposer(("utilisateurs", None))


def _pbkdf2(mot_de_passe, sel, iterations):
    return hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode(),
                               bytes.fromhex(sel), iterations).hex()


def derive_mot_de_passe(mot_de_passe, sel):
    return f"pbkdf2-sha256${PBKDF2_ITERATIONS}${_pbkdf2(mot_de_passe, sel, PBKDF2_ITERATIONS)}"


def verifie_mot_de_passe(compte, mot_de_passe):
    try:
        attendu = compte["hash"]
        if attendu.startswith("pbkdf2-sha256$"):
            _, iterations, empreinte = attendu.split("$")
            calcule = _pbkdf2(mot_de_passe, compte["sel"], int(iterations))
        else:
            empreinte = attendu
            calcule = _pbkdf2(mot_de_passe, compte["sel"], PBKDF2_ANCIEN)
    except (KeyError, TypeError, ValueError):
        return False
    return secrets.compare_digest(calcule, empreinte)


def empreinte_a_jour(compte):
    return str((compte or {}).get("hash") or "").startswith(f"pbkdf2-sha256${PBKDF2_ITERATIONS}$")


# Le meme calcul qu'un vrai compte, quand le nom n'existe pas : sinon la
# reponse arrivait plus vite pour un nom inconnu, et la duree seule donnait la
# liste des comptes valides.
_COMPTE_FANTOME = {"sel": "00" * 16, "hash": "pbkdf2-sha256$%d$00" % PBKDF2_ITERATIONS}


# ---------------------- adresse mail d'un compte ----------------------
#
# L'adresse relie un compte a quelqu'un de joignable : c'est par elle qu'un
# mot de passe se recupere, et c'est elle qui rend une inscription libre
# defendable -- sans verification, n'importe qui creerait n'importe quoi.
#
# La verification est un code a six chiffres envoye a l'adresse. Volontairement
# le meme geste que le second facteur : la personne connait deja ce parcours.
# Il ne remplace pas le second facteur et n'ouvre aucune session -- il atteste
# seulement que l'adresse existe et qu'elle appartient bien a qui la declare.
EMAIL_MAX = 254                 # la limite de la RFC 5321
CODE_EMAIL_VALIDITE = 900       # 15 minutes : le temps d'aller lire son mail
CODE_EMAIL_ESSAIS = 5           # au-dela, il faut en redemander un autre
CODE_EMAIL_DELAI = 60           # pas plus d'un envoi par minute et par compte


def email_valide(brut):
    """Une adresse plausible, ou "".

    Volontairement permissif : la seule verification qui vaille est d'y
    envoyer un code et d'attendre qu'il revienne. Une expression reguliere
    stricte refuse des adresses parfaitement valides et n'arrete personne.
    """
    adresse = re.sub(r"\s+", "", str(brut or ""))[:EMAIL_MAX]
    return adresse if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", adresse) else ""


def _empreinte_code(code):
    return hashlib.sha256(str(code).encode()).hexdigest()


def poser_code_email(compte):
    """Tire un code a six chiffres, le range, et le renvoie en clair.

    Range sous forme d'empreinte : le code n'a pas a etre lisible dans
    utilisateurs.json, ou il resterait apres coup a cote du nom du compte.
    """
    code = f"{secrets.randbelow(1000000):06d}"
    compte["email_code"] = {"empreinte": _empreinte_code(code),
                            "expire": int(time.time()) + CODE_EMAIL_VALIDITE,
                            "essais": 0,
                            "envoye": int(time.time())}
    return code


def verifier_code_email(compte, code):
    """(ok, message). Consomme un essai, et le code au premier succes."""
    en_cours = compte.get("email_code") or {}
    if not en_cours:
        return False, "Aucun code en attente. Demandes-en un nouveau."
    if int(time.time()) > en_cours.get("expire", 0):
        compte.pop("email_code", None)
        return False, "Ce code a expire. Demandes-en un nouveau."
    if en_cours.get("essais", 0) >= CODE_EMAIL_ESSAIS:
        compte.pop("email_code", None)
        return False, "Trop d'essais. Demandes-en un nouveau."
    en_cours["essais"] = en_cours.get("essais", 0) + 1
    propose = re.sub(r"\D", "", str(code or ""))
    if not (propose and secrets.compare_digest(_empreinte_code(propose),
                                               en_cours.get("empreinte", ""))):
        return False, "Code incorrect."
    compte.pop("email_code", None)
    compte["email_verifie"] = True
    return True, ""


def envoyer_code_email(adresse, nom, code):
    """Envoie le code, ou leve. L'appelant traduit l'echec."""
    cfg, ok = smtp_utilisable()
    if not ok:
        raise RuntimeError("Aucun serveur d'envoi configure.")
    envoyer_mail(cfg, "[CodeLab] verification de ton adresse",
                 f"Code de verification pour le compte « {nom} » : {code}\n\n"
                 f"Il est valable {CODE_EMAIL_VALIDITE // 60} minutes.\n\n"
                 "Si tu n'es pas a l'origine de cette demande, ignore ce "
                 "message : sans ce code, rien ne change.\n\n"
                 "-- CodeLab, panneau de gestion des applications",
                 destinataires=[adresse], sensible=True)


# ---------------------- mot de passe oublie ----------------------
#
# Par l'ADRESSE MAIL quand il y en a une de verifiee, et par
# l'administrateur sinon. Les deux chemins existent parce qu'aucun des deux
# ne suffit : sans adresse, personne ne peut prouver a distance qui il est ;
# et si le seul recours etait l'administrateur, il faudrait le deranger pour
# chaque oubli.
#
# UN CODE A PART, et non celui de la verification d'adresse : verifier une
# adresse rend un compte utilisable, reinitialiser un mot de passe le rend
# ACCESSIBLE. Le premier code, consomme, marque l'adresse comme verifiee --
# ce qui n'a aucun sens ici, et surtout melangerait deux pouvoirs tres
# differents dans un seul jeton.
CODE_REINIT_VALIDITE = 900      # 15 minutes, comme la verification d'adresse
CODE_REINIT_ESSAIS = 5


def preparer_code_reinit(compte):
    code = f"{secrets.randbelow(1000000):06d}"
    compte["reinit_code"] = {"empreinte": _empreinte_code(code),
                             "expire": int(time.time()) + CODE_REINIT_VALIDITE,
                             "essais": 0,
                             "envoye": int(time.time())}
    return code


def verifier_code_reinit(compte, code):
    """(ok, message). Consomme un essai, et le code au premier succes."""
    en_cours = compte.get("reinit_code") or {}
    if not en_cours:
        return False, "Aucune réinitialisation en cours. Recommence."
    if int(time.time()) > en_cours.get("expire", 0):
        compte.pop("reinit_code", None)
        return False, "Ce code a expiré. Demandes-en un nouveau."
    if en_cours.get("essais", 0) >= CODE_REINIT_ESSAIS:
        compte.pop("reinit_code", None)
        return False, "Trop d'essais. Demandes-en un nouveau."
    en_cours["essais"] = en_cours.get("essais", 0) + 1
    propose = re.sub(r"\D", "", str(code or ""))
    if not (propose and secrets.compare_digest(_empreinte_code(propose),
                                               en_cours.get("empreinte", ""))):
        return False, "Code incorrect."
    compte.pop("reinit_code", None)
    return True, ""


def envoyer_code_reinit(adresse, nom, code):
    cfg, ok = smtp_utilisable()
    if not ok:
        raise RuntimeError("Aucun serveur d'envoi configure.")
    envoyer_mail(cfg, "[CodeLab] reinitialisation de ton mot de passe",
                 f"Code de reinitialisation pour le compte « {nom} » : {code}\n\n"
                 f"Il est valable {CODE_REINIT_VALIDITE // 60} minutes, et ne sert "
                 f"qu'une fois.\n\n"
                 "Si tu n'es pas a l'origine de cette demande, ignore ce message : "
                 "sans ce code, ton mot de passe ne change pas.\n\n"
                 "-- CodeLab, panneau de gestion des applications",
                 destinataires=[adresse], sensible=True)


# ---------------------- second facteur par code envoye ----------------------
#
# LE SECOND FACTEUR SANS APPLICATION A INSTALLER. Scanner un QR code avec une
# application d'authentification, c'etait l'etape ou l'on perdait les gens :
# il faut l'application, comprendre ce qu'elle fait, et la retrouver le jour
# ou l'on change de telephone. Un code a six chiffres recu par mail, au
# moment de se connecter, ne demande rien de tout cela -- et Face ID (une cle
# d'acces) dispense meme du code.
#
# Il reste un VRAI second facteur : le mot de passe est verifie d'abord, et
# le code ne part que vers l'adresse verifiee du compte -- jamais vers une
# destination fournie par la requete.
#
# Ordre de preference : Face ID / Touch ID (rien a saisir), puis le code par
# mail, puis l'application d'authentification. La personne peut toujours
# basculer de l'un a l'autre.
CODE_CONNEXION_VALIDITE = 600   # 10 minutes
CODE_CONNEXION_ESSAIS = 5
MOYEN_TOTP, MOYEN_EMAIL = "totp", "email"
# Face ID / Touch ID en second facteur : apres le mot de passe, la cle
# d'acces de CE compte confirme que c'est bien la personne.
MOYEN_PASSKEY = "passkey"
SECOND_FACTEUR_VALIDITE = 600


def masquer_destination(moyen, valeur):
    """Ce qu'on peut afficher de la destination sans la reveler : la page de
    connexion est publique, et le mot de passe seul ne doit pas suffire a
    lire l'adresse de quelqu'un."""
    valeur = str(valeur or "")
    if moyen == MOYEN_EMAIL and "@" in valeur:
        local, domaine = valeur.split("@", 1)
        return local[:1] + "•••@" + domaine
    return ""


def moyens_second_facteur(compte):
    """Les moyens utilisables pour ce compte, dans l'ordre de preference."""
    moyens = []
    # Apres une reinitialisation du mot de passe PAR MAIL, le mail a deja
    # servi de preuve : le reprendre comme second facteur reviendrait a
    # ouvrir le compte a qui tient la boite. Le code par mail reste suspendu
    # jusqu'a une connexion par l'application ou par Face ID.
    if (compte.get("email") and compte.get("email_verifie")
            and not compte.get("code_mail_suspendu") and smtp_utilisable()[1]):
        moyens.append(MOYEN_EMAIL)
    # Le code par mail d'abord : rien a installer, il arrive la ou l'on
    # regarde deja. L'application d'authentification reste a un clic.
    if compte.get("totp"):
        moyens.append(MOYEN_TOTP)
    return moyens


def facteur_fort(nom, compte):
    """Le compte a-t-il un facteur qui ne passe PAS par la boite mail ?"""
    return bool(compte.get("totp") or passkeys_du_compte(nom))


def poser_code_connexion(compte, moyen):
    code = f"{secrets.randbelow(1000000):06d}"
    compte["code_connexion"] = {"empreinte": _empreinte_code(code),
                                "expire": int(time.time()) + CODE_CONNEXION_VALIDITE,
                                "essais": 0, "moyen": moyen,
                                "envoye": int(time.time())}
    return code


def verifier_code_connexion(compte, code):
    """Consomme un essai, et le code au premier succes. Rend True ou False."""
    en_cours = compte.get("code_connexion") or {}
    if not en_cours:
        return False
    if (int(time.time()) > en_cours.get("expire", 0)
            or en_cours.get("essais", 0) >= CODE_CONNEXION_ESSAIS):
        compte.pop("code_connexion", None)
        return False
    en_cours["essais"] = en_cours.get("essais", 0) + 1
    propose = re.sub(r"\D", "", str(code or ""))
    if propose and secrets.compare_digest(_empreinte_code(propose),
                                          en_cours.get("empreinte", "")):
        compte.pop("code_connexion", None)
        return True
    return False


def envoyer_code_connexion(compte, nom, moyen, code, destinataires=None):
    """Envoie le code par mail, ou leve.

    Le texte pour tous les lecteurs de mail ; une version mise en page, le
    code en grand, pour ceux qui l'affichent.
    """
    validite = CODE_CONNEXION_VALIDITE // 60
    cfg, _ = smtp_utilisable()
    chiffres = "".join(
        f'<span style="display:inline-block;width:40px;height:52px;line-height:52px;'
        f'margin:0 3px;border-radius:10px;background:#f1f3f6;font-size:28px;'
        f'font-weight:700;color:#11151c">{c}</span>' for c in code)
    page = (
        '<div style="background:#eef0f3;padding:32px 12px;font-family:-apple-system,'
        'BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
        '<div style="max-width:440px;margin:0 auto;background:#fff;border-radius:20px;'
        'padding:32px 28px;text-align:center;color:#11151c">'
        '<div style="font-size:15px;font-weight:700;letter-spacing:-.01em">CodeLab</div>'
        '<h1 style="font-size:21px;margin:22px 0 6px">Ton code de connexion</h1>'
        f'<p style="margin:0 0 22px;color:#5b6472;font-size:14px">Pour le compte '
        f'<b>{_html.escape(nom)}</b>. Saisis-le sur la page de connexion.</p>'
        f'<div style="white-space:nowrap">{chiffres}</div>'
        f'<p style="margin:22px 0 0;color:#5b6472;font-size:13px">Valable {validite} minutes, '
        'il ne sert qu\'une fois.</p>'
        '<p style="margin:18px 0 0;padding-top:16px;border-top:1px solid #e6e8ec;'
        'color:#8a92a0;font-size:12px">Pas toi ? Ton mot de passe est connu de quelqu\'un '
        "d'autre : change-le, et préviens ton administrateur.</p>"
        '</div></div>')
    envoyer_mail(cfg, f"[CodeLab] {code} est ton code de connexion",
                 f"Code de connexion pour le compte « {nom} » : {code}\n\n"
                 f"Il est valable {validite} minutes, et ne sert qu'une fois.\n\n"
                 "Si tu n'es pas à l'origine de cette connexion, ton mot de passe "
                 "est connu de quelqu'un d'autre : change-le, et préviens ton "
                 "administrateur.\n\n"
                 "-- CodeLab, panneau de gestion des applications",
                 destinataires=destinataires or [compte["email"]], sensible=True, html=page)


# ------------------------------ invitations ------------------------------
#
# CREER UN COMPTE, C'EST DONNER UNE ADRESSE MAIL. L'administrateur n'invente
# plus ni nom ni mot de passe a transmettre par un canal qu'il ne maitrise
# pas : la personne recoit un lien, choisit elle-meme son nom et son mot de
# passe, et son adresse est verifiee du meme coup -- c'est en cliquant sur le
# lien recu qu'elle prouve qu'elle la releve.
#
# Le lien porte un jeton de 256 bits. Seule son EMPREINTE est rangee (au
# coffre) : qui lit le coffre ne peut pas s'en servir. Il ne sert qu'une
# fois et expire au bout de sept jours.
INVITATION_VALIDITE = 7 * 86400


def _empreinte_jeton(jeton):
    return hashlib.sha256(str(jeton or "").encode()).hexdigest()


def lire_invitations():
    """Les invitations encore valables, les expirees en moins."""
    maintenant = int(time.time())
    tout = reglage("invitations", {}) or {}
    return {k: v for k, v in tout.items()
            if isinstance(v, dict) and v.get("expire", 0) > maintenant}


def ecrire_invitations(invitations):
    ecrire_reglage("invitations", invitations or None)


def invitation_par_jeton(jeton):
    """(empreinte, invitation) ou (None, None)."""
    if not jeton or len(jeton) > 100:
        return None, None
    empreinte = _empreinte_jeton(jeton)
    invitation = lire_invitations().get(empreinte)
    return (empreinte, invitation) if invitation else (None, None)


def lien_invitation(jeton):
    """L'adresse publique du panneau si elle est connue -- l'administrateur
    invite souvent depuis le reseau local, ou l'adresse qu'il a sous les
    yeux ne s'ouvre pas de l'exterieur."""
    base = adresse_publique() or request.host_url.rstrip("/")
    return f"{base}/invitation/{jeton}"


def envoyer_invitation(adresse, lien):
    """Envoie le lien, ou leve. En direct, jamais par Mailpit : le lien
    ouvre la creation d'un compte."""
    cfg, _ = smtp_utilisable()
    envoyer_mail(cfg, "[CodeLab] ton invitation",
                 "Bonjour,\n\n"
                 "Un compte CodeLab t'attend. Ouvre ce lien pour choisir ton nom "
                 "d'utilisateur et ton mot de passe :\n\n"
                 f"{lien}\n\n"
                 f"Le lien est valable {INVITATION_VALIDITE // 86400} jours et ne sert "
                 "qu'une fois.\n\n"
                 "Si tu ne t'attendais pas à ce message, ignore-le : sans ce lien, "
                 "aucun compte n'est créé.\n\n"
                 "-- CodeLab, panneau de gestion des applications",
                 destinataires=[adresse], sensible=True)


def creer_invitation(adresse, projets):
    """Range une invitation neuve et rend (empreinte, jeton). Une invitation
    deja en attente pour la meme adresse est remplacee : son ancien lien ne
    vaut plus rien."""
    invitations = {k: v for k, v in lire_invitations().items()
                   if v.get("email") != adresse}
    jeton = secrets.token_urlsafe(32)
    empreinte = _empreinte_jeton(jeton)
    invitations[empreinte] = {"email": adresse, "projets": projets,
                              "cree": int(time.time()),
                              "expire": int(time.time()) + INVITATION_VALIDITE}
    ecrire_invitations(invitations)
    return empreinte, jeton


# --------------------------- cles d'acces (passkeys) ---------------------------
#
# Une cle d'acces remplace le mot de passe ET le code a six chiffres : le
# telephone (ou l'ordinateur) prouve la possession, et l'empreinte ou le code
# de l'appareil prouve la personne. Rien a retenir, rien a recopier, et rien
# a hameconner -- la cle ne signe que pour le domaine qui l'a enregistree,
# donc un faux site n'en tire rien.
#
# TROIS CONTRAINTES QUE LE NAVIGATEUR IMPOSE, et qu'il faut annoncer plutot
# que subir :
#
#   1. contexte securise. Le navigateur refuse WebAuthn hors HTTPS (sauf sur
#      localhost). Sur http://192.168.1.x:9001, le bouton ne peut pas
#      marcher : le panneau le dit au lieu de l'afficher pour rien ;
#   2. un vrai nom de domaine. Le "rp_id" ne peut pas etre une adresse IP.
#      Il faut donc un nom -- celui par lequel on ouvrira toujours le
#      panneau, puisque les cles sont liees a lui ;
#   3. le meme nom a chaque fois. Une cle enregistree sur codelab.exemple.fr
#      ne fonctionne pas sur 192.168.1.20, et c'est voulu.
#
# La bibliotheque webauthn fait la cryptographie. Ecrire soi-meme la
# verification d'une signature ECDSA et le decodage CBOR d'une attestation,
# c'est exactement le genre de code ou une erreur discrete ne se voit jamais
# -- sauf de celui qui la cherche. Import optionnel, comme le QR code : sans
# elle, les cles d'acces sont simplement indisponibles.
PASSKEY_NOM_MAX = 40


def passkeys_disponibles():
    try:
        import webauthn  # noqa: F401
    except ImportError:
        return False
    return True


def _hote_et_schema():
    """(hote sans port, schema, https_annonce_sans_confiance).

    X-Forwarded-Proto n'est croyable que derriere un proxy declare de
    confiance : n'importe quel client peut le poser. On ne s'en sert donc
    que si trust_proxy() est actif -- mais on retient qu'il annoncait HTTPS,
    parce que c'est exactement le cas ou la marche a suivre n'est pas
    « mets du TLS » mais « declare ton proxy ».
    """
    hote = (request.host or "").split(":")[0]
    # Le schema annonce par un proxy suffit ici : la signature d'une cle
    # d'acces porte l'origine que le navigateur a REELLEMENT vue, et ne
    # concordera pas avec un schema invente.
    annonce = (request.headers.get("X-Forwarded-Proto") or "").lower()
    if annonce in ("http", "https"):
        return hote, annonce, False
    return hote, request.scheme, False


def passkey_contexte():
    """(rp_id, origine, empechement).

    empechement vaut "" quand tout est reuni. Sinon c'est la phrase a
    afficher : le navigateur, lui, se contenterait d'une erreur illisible.
    """
    hote, schema, https_non_cru = _hote_et_schema()
    origine = f"{schema}://{request.host}"
    if not hote:
        return "", "", "Hote inconnu."
    local = hote in ("localhost", "127.0.0.1", "::1")
    if schema != "https" and not local:
        if https_non_cru:
            return "", "", ("Un proxy annonce HTTPS, mais ce panneau ne le croit pas : "
                            "coche « Proxy de confiance » dans Exposition.")
        return "", "", ("Les clés d'accès exigent une connexion HTTPS : le navigateur "
                        "refuse de les créer en clair. Mets le TLS en place, puis "
                        "reviens ici.")
    # Une adresse IP ne peut pas servir de "relying party id" : la norme
    # exige un nom de domaine. C'est la meme exigence que le certificat.
    if re.fullmatch(r"[0-9.]+|\[[0-9a-fA-F:]+\]", hote) and not local:
        return "", "", ("Les clés d'accès exigent un nom de domaine, pas une adresse IP. "
                        "Ouvre le panneau par son nom (celui du certificat).")
    return hote, origine, ""


def lire_passkeys():
    """Les cles d'acces, par compte : {compte: [cle, ...]}."""
    try:
        lignes = stockage.lire(DB_FILE, "SELECT * FROM cles_acces ORDER BY cree, id")
    except Exception:                                             # noqa: BLE001
        return {}
    tout = {}
    for ligne in lignes:
        cle = {"id": ligne["id"], "cle_publique": ligne["cle_publique"],
               "compteur": ligne["compteur"] or 0}
        for champ in ("nom", "cree", "dernier"):
            if ligne[champ] is not None:
                cle[champ] = ligne[champ]
        tout.setdefault(ligne["compte"], []).append(cle)
    return tout


def ecrire_passkeys(tout):
    with stockage.transaction(DB_FILE) as cx:
        cx.execute("DELETE FROM cles_acces")
        for compte, cles in tout.items():
            for k in cles or []:
                cx.execute("INSERT OR REPLACE INTO cles_acces (id, compte, cle_publique, "
                           "compteur, nom, cree, dernier) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (k["id"], compte, k.get("cle_publique") or "",
                            int(k.get("compteur") or 0), k.get("nom"),
                            k.get("cree"), k.get("dernier")))
    # Le nombre de cles d'un compte fait partie de ce que le miroir recopie.
    # Depose apres l'ecriture, pour la meme raison qu'au-dessus.
    _pg_deposer(("utilisateurs", None))


def passkeys_du_compte(nom):
    return lire_passkeys().get(nom, [])


def _descripteurs(nom):
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor
    return [PublicKeyCredentialDescriptor(id=base64url_to_bytes(k["id"]))
            for k in passkeys_du_compte(nom)]
