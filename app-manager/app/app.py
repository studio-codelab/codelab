"""
CodeLab -- LE PANNEAU : authentification, hub, applications, proxy, mails.
---------------------------------------------------------------------------
ROLE        Un seul service Flask, servi par waitress (voir « serveur HTTP ») :
              :9001  le panneau, son API, /login, /health (sonde Docker) ;
              :9002  les applications, sous /<nom>/, sur une ORIGINE A PART
                     (APP_MANAGER_APPS_PORT ; 0 les remet sur 9001).
            Il authentifie (mot de passe, second facteur, cles d'acces),
            declare, construit, lance et surveille les applications de
            /workspace, les sert par reverse proxy, envoie mails et
            notifications, et sauvegarde son etat.
            Il n'ECRIT JAMAIS dans /workspace : un projet est cree par
            l'utilisateur (SSH, VS Code, git), puis declare ici.
CHARGE PAR  le conteneur codelab-app-manager (entrypoint.sh) ; importe par la
            suite de tests (workspace/diagnostic/test_regressions.py), qui
            appelle les fonctions par leur nom : un renommage se reporte dans
            les tests.
CONTRAINTES Les regles numerotees de SECURITE.md, et la carte des dependances
            d'AGENTS.md (racine du depot). En particulier : le controle des
            droits est ici, jamais seulement dans la page ; une route qui
            modifie passe par verifier_jeton() ; une application ne recoit
            jamais le cookie ni les secrets du panneau.
            Chemins par variables d'environnement, alignes sur
            docker-compose.yml : APP_MANAGER_STATE (etat), APP_MANAGER_ROOT
            (/workspace), APP_MANAGER_SHARED_CONFIG (credentials.env, 0600).
STRUCTURE   Ce fichier : les imports, les chemins et constantes partages,
            le chargeur des morceaux, puis main. Le reste vit dans
            serveur/, un fichier par groupe de sections titrees
            « # ---- titre ---- », dans cet ordre (MODULES_DU_SERVEUR) :
              01 pages servies, secrets
              02 SSO des applications, second facteur, session, origine
              03 Dagster
              04 retour apres connexion, appareils, CSP, CSRF
              05 comptes, coffre, invitations, cles d'acces
              06 messages, journal des acces, miroir Postgres, analyses
              07 persistance, privileges, isolation, cycle de vie
              08 sauvegardes, surveillance externe, version, alertes
              09 build, metriques, icones, categories, visibilite
              10 routes de connexion, cles d'acces, invitations
              11 notifications Web Push, API, renommage
              12 pages, videos, taches, export
              13 hub, proxy, application fermee, diagnostic
              14 serveur HTTP
CHOIX       - Le cadre /ouvrir.html est un fichier a marqueurs, rempli en un
              seul passage (ouvrir_dans_le_panneau) : sa page et son script,
              ecrits en chaines Python, ne se relisaient plus.
            - Des morceaux executes dans la portee de app, pas des modules
              importes : voir « le serveur, en morceaux », plus bas.
            - Les processus des applications survivent a un redemarrage du
              panneau (section « les processus survivent au panneau »).
            - Les applications sur une origine distincte du panneau : un
              script d'application ne lit pas le cookie de session.
            Les pages et scripts servis vivent a cote, lus une fois a
            l'import (_lire_ressource) : dashboard.html, login.html,
            messages.html, ouvrir.html (a marqueurs), mode.js, theme.css,
            environnement-app.js (injecte par le proxy).
A AMELIORER - Fonctions encore longues (60 a 75 lignes de code), a decouper
              avec la meme prudence que login_submit : _proxy,
              _verifier_cle, api_message, _pg_preparer.
            - Generer la table de STRUCTURE ci-dessus depuis les titres de
              section, pour qu'elle ne vieillisse plus.
"""
import base64
import copy
import datetime
import fcntl
import html as _html
import io
import functools
import gzip
import queue
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import resource
import secrets
import select
import shutil
import signal
import smtplib
import tarfile
import socket
import ssl
import stat as stat_mod
import struct
import subprocess
import threading
import time
import unicodedata
import zlib
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import deque
from email.message import EmailMessage

import psutil
from flask import (Flask, Response, g, has_request_context, jsonify, redirect,
                   request, session, send_file, stream_with_context)

# Le module de stockage vit a cote de ce fichier, et se charge PAR SON
# CHEMIN. app.py est parfois charge lui-meme par son chemin (le diagnostic,
# les tests) ; ajouter son dossier au chemin d'import ferait alors passer ce
# fichier-ci pour le « app » d'un autre projet -- celui du diagnostic.
import importlib.util as _importlib_util
import sys
_spec_stockage = _importlib_util.spec_from_file_location(
    "codelab_stockage", os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockage.py"))
stockage = sys.modules.get("codelab_stockage") or _importlib_util.module_from_spec(_spec_stockage)
if "codelab_stockage" not in sys.modules:
    sys.modules["codelab_stockage"] = stockage
    _spec_stockage.loader.exec_module(stockage)

STATE_DIR = os.environ.get("APP_MANAGER_STATE", "/var/lib/codelab/app-manager")
# LA BASE DU PANNEAU. Applications, comptes, droits, cles d'acces, journal,
# messages et reglages y vivent, chacun dans sa table -- voir stockage.py et
# la section « Donnees » du README.
DB_FILE = os.path.join(STATE_DIR, "codelab.db")
LOG_DIR = os.path.join(STATE_DIR, "logs")
ROOT = os.environ.get("APP_MANAGER_ROOT", "/workspace")
PORT_MIN, PORT_MAX = 9101, 9140
HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade",
       "proxy-authenticate", "proxy-authorization", "te", "trailers"}

SHARED_CONFIG_DIR = os.environ.get("APP_MANAGER_SHARED_CONFIG", "/var/lib/codelab/config")
# Source de verite unique pour tous les secrets CodeLab. Pas de nom en "."
# (fichier cache sous Unix) : les navigateurs de fichiers web n'offrent pas
# tous une option pour les afficher.
SHARED_ENV_FILE = os.path.join(SHARED_CONFIG_DIR, "credentials.env")

# LE REPLI DES SECRETS : un fichier par secret, ecrit SEULEMENT quand
# credentials.env n'est pas accessible en ecriture -- sinon un redemarrage
# regenererait un mot de passe administrateur different. Supprimes des que
# credentials.env s'ecrit de nouveau (voir bootstrap_secrets).
REPLI_ADMIN_PASSWORD_FILE = os.path.join(STATE_DIR, "admin_password")
REPLI_SECRET_KEY_FILE = os.path.join(STATE_DIR, "flask_secret_key")
SSO_PRIVATE_FILE = os.path.join(STATE_DIR, "codelab_sso_private.key")

_admin_password = None   # valeur courante, chargee par bootstrap_secrets()
_totp_secret = ""        # vide = double authentification desactivee
TOTP_COMPTE = "admin"    # le nom affiche dans l'application d'authentification


# ------------------------- le serveur, en morceaux -------------------------
#
# Le panneau tient dans les fichiers de serveur/, executes ICI, dans l'ordre
# de MODULES_DU_SERVEUR, dans la portee de ce module. Comme si le code
# etait encore ecrit a la suite : une seule portee, les memes globales, et
# « app.<nom> » designe toujours la fonction que le panneau appelle. C'est le
# principe des scripts du panneau (PANNEAU_MODULES) applique au serveur.
# Un fichier ajoute a serveur/ n'est execute que s'il est dans la liste (un
# test le verifie).
#
# Vecu : « Decoupe app.py ». 14 300 lignes en un fichier ne se relisaient
# plus par sujet.
# ESSAYE ET ECARTE : de vrais modules importes, avec un app.py qui
#   reexporte leurs noms. Une fonction deplacee lirait alors les globales de
#   SON module : les 269 remplacements « monkeypatch.setattr(app, ...) » des
#   tests, et la reprise d'un reglage par une autre section, ne
#   l'atteindraient plus, sans aucune erreur. Et les sections se citent
#   entre elles par milliers : les separer demanderait de reecrire leurs
#   dependances une par une.
# Les erreurs et inspect.getsource() citent le vrai fichier et la vraie
# ligne : chaque morceau est compile sous son propre chemin.
MODULES_DU_SERVEUR = (
    "01-pages-et-secrets.py",
    "02-sessions.py",
    "03-dagster.py",
    "04-requetes.py",
    "05-comptes.py",
    "06-messages-et-journal.py",
    "07-processus.py",
    "08-sauvegardes-et-alertes.py",
    "09-applications.py",
    "10-routes-de-connexion.py",
    "11-notifications-et-api.py",
    "12-pages-et-taches.py",
    "13-hub-et-proxy.py",
    "14-serveur-http.py",
)
DOSSIER_DU_SERVEUR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serveur")


def _charger_le_serveur():
    # Dans une fonction : ses variables (nom, chemin) n'entrent pas dans la
    # portee commune, ou elles pourraient masquer un nom d'un morceau.
    for nom in MODULES_DU_SERVEUR:
        chemin = os.path.join(DOSSIER_DU_SERVEUR, nom)
        with open(chemin, encoding="utf-8") as f:
            exec(compile(f.read(), chemin, "exec"), globals())


_charger_le_serveur()


# -------------------------------- main --------------------------------

if __name__ == "__main__":
    bootstrap_secrets()
    bootstrap_sso()
    signaler_adresses_en_conflit()
    publier_relais_mail()
    os.makedirs(LOG_DIR, exist_ok=True)
    inscrit = amorcer_diagnostic()
    # AVANT resume() : les applications survivent a l'arret du panneau, et
    # relancer par-dessus une application deja en vie ferait mourir la
    # nouvelle sur un port occupe -- sans que personne ne comprenne pourquoi.
    adopter_processus_survivants()
    resume()
    if inscrit:
        threading.Thread(target=_preparer_diagnostic, args=(inscrit,),
                         daemon=True).start()
    start_monitor_thread()
    demarrer_miroir_pg()
    demarrer_les_sauvegardes()
    demarrer_la_surveillance_externe()
    demarrer_la_purge_du_journal()
    servir(int(os.environ.get("MANAGER_PORT", "9001")))
