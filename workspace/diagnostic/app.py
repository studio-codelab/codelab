"""
CodeLab -- application de diagnostic.

Tourne dans le conteneur codelab-app-manager, lancee par le panneau, et
fait l'etat des lieux de l'installation. Deux questions, pas une :

  EST-CE QUE CA MARCHE ?   les cinq services se parlent-ils ?
  EST-CE QUE C'EST FERME ? le panneau exige-t-il une session, les
                           applications vivent-elles dans une autre origine,
                           sont-elles isolees les unes des autres, et ce qui
                           doit etre pose quand la stack sort du reseau local
                           l'est-il ?

La seconde question ne se repond pas en lisant le code : elle depend de la
configuration REELLE -- une variable oubliee, un port non publie, une garde
active en developpement et pas en service. C'est pour cela qu'elle se pose
ici, depuis l'interieur d'une installation qui tourne.

Les sondes du premier groupe :

  config      credentials.env lisible -> volume config monte
  workspace   /workspace/definitions.py visible -> volume partage
  postgres    pilote present, connexion avec le mot de passe du fichier partage
  dagster     http://codelab-dagster:3000 joignable sur le reseau codelab
  dev         codelab-dev:22 accepte une connexion (banniere SSH), et
              authorized_keys est lisible par l'utilisateur SSH

Et surtout : la table codelab_diagnostic contient des lignes ecrites par
CETTE application ET par l'asset Dagster. Voir les deux sources dans le
meme tableau est la preuve que la chaine complete fonctionne.

    Commande de build     : pip install --target vendor "psycopg[binary]"
    Commande de lancement : python3 app.py

Ce fichier tourne dans codelab-app-manager, une image qui contient Flask mais
PAS Dagster : il ne doit donc jamais importer dagster, sous peine de ne plus
demarrer du tout (le panneau le relance alors en boucle). Le code Dagster du
projet -- l'asset et le capteur d'alerte -- vit dans definitions.py, execute
par l'autre conteneur. Le code commun aux deux vit dans checks.py, qui
n'importe ni flask ni dagster.
"""
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

# psycopg n'est pas dans l'image app-manager : la commande de build l'installe
# dans ./vendor, a cote du code, sans toucher au conteneur.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

from flask import Flask, Response, request  # noqa: E402  (present dans l'image app-manager)

import checks  # noqa: E402

app = Flask(__name__)
SOURCE = "app-manager"

# Le theme du panneau : le diagnostic le CHARGE, il ne le recopie pas. Une
# palette recopiee dans une deuxieme page est une palette qui divergera, et
# le diagnostic finirait par annoncer une stack saine dans des couleurs qui
# ne sont plus celles de la stack.
#
# Deux liens vers LE MEME fichier, et c'est voulu.
#
#   "/theme.css"  celui du panneau, a la racine de l'origine des
#                 applications. C'est le chemin normal.
#   "theme.css"   RELATIF, donc servi par cette application-ci, derriere son
#                 propre prefixe. Il ne depend d'aucune garde d'origine.
#
# Le premier suffisait tant que le port des applications servait la feuille.
# Il ne la servait plus : la garde qui separe les deux origines ne laissait
# passer que le proxy et la sonde de sante, "/theme.css" repondait 404 sur ce
# port, et la page s'affichait SANS UN SEUL JETON -- fond blanc, texte brut,
# pastilles invisibles. Exactement le "tres mauvais rendu" constate.
#
# La garde est corrigee, et ce second lien fait qu'une page nue ne peut plus
# revenir : il faudrait que les DEUX chemins tombent. Les deux servent le
# meme fichier, donc rien ne diverge -- et surtout, aucune couleur n'est
# recopiee ici.
THEME = ('<link rel="stylesheet" href="/theme.css">'
         '<link rel="stylesheet" href="theme.css">')

# LA MARQUE : le trace d'un moniteur cardiaque. Ecrit UNE fois et utilise
# deux fois -- la barre du haut de la page, et l'icone de l'onglet du
# navigateur. Deux dessins recopies divergent au premier retouchage, et c'est
# celui qu'on ne regarde pas qui reste en arriere.
#
# Le panneau porte le meme dessin pour la tuile du hub (ICONE_DIAGNOSTIC dans
# app-manager/app/app.py) : les deux services ne peuvent pas partager de code,
# mais ils partagent la forme, et un test verifie qu'aucun des deux ne l'a
# perdue.
MARQUE_TRACE = "M3 12h4l2.5-7 4 14 2.5-7h5"

# L'icone de l'onglet, dessinee dans l'adresse elle-meme : pas de requete de
# plus, et rien a servir. Le fond reprend le turquoise de CodeLab -- la page
# n'a pas encore charge son theme quand le navigateur lit cette ligne.
FAVICON = (
    '<link rel="icon" href="data:image/svg+xml,'
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
    "%3Crect width='24' height='24' rx='6' fill='%230e7c86'/%3E"
    "%3Cpath d='" + MARQUE_TRACE.replace(" ", "%20") + "' fill='none' "
    "stroke='white' stroke-width='2' stroke-linecap='round' "
    "stroke-linejoin='round'/%3E%3C/svg%3E\">")

# Le choix clair / sombre fait dans le panneau vit dans le localStorage de
# son origine. Les applications sont servies sur une AUTRE origine (le port
# 9002), qui a son propre stockage : le choix n'y etait donc pas lisible, et
# le diagnostic suivait le reglage du systeme -- en sombre chez quelqu'un qui
# avait choisi le clair, et l'inverse.
#
# Le panneau le recopie maintenant dans un COOKIE, qui appartient a l'HOTE et
# ignore le port. Ce qu'il porte est une preference d'affichage, pas un
# secret : c'est bien pour cela qu'on peut la partager, la ou le cookie de
# session est justement retire avant d'atteindre une application. La
# separation des origines n'y perd rien.
#
# Applique avant le premier rendu, donc sans scintillement.
SUIVRE_LE_THEME = r"""<script>
/* Le choix clair / sombre arrive par un COOKIE, pose par le panneau. Il
   appartient a l'HOTE et ignore le port : c'est ce qui lui permet de
   traverser la separation des origines, la ou le localStorage du panneau
   reste inaccessible d'ici -- et doit le rester. Le localStorage local sert
   de repli pour une page ouverte directement sur le port du panneau. */
try{var m=document.cookie.match(/(?:^|;\s*)codelab-theme=([^;]*)/);
 var t=m?decodeURIComponent(m[1]):localStorage.getItem('codelab-theme');
 if(t&&t!=='auto')document.documentElement.setAttribute('data-theme',t);}catch(e){}
</script>"""

# Ce qui reste ici : la mise en page propre a cette page. Aucune couleur en
# dur -- tout passe par les jetons de theme.css, et un test le verifie. Les
# seuls replis qui restent sont des TAILLES : un repli de taille degrade la
# mise en page, un repli de couleur mentirait sur un etat.
CSS = """
/* Les memes composants que le panneau, ecrits avec ses jetons : barre du
   haut, cartes posees sur un fond plus soutenu, pastilles teintees, bandeau
   de severite au bord, chiffres alignes. Le diagnostic est une PAGE DE PLUS
   de CodeLab, pas une application invitee -- il doit se lire comme le reste.

   La page repond dans cet ordre, et la mise en page suit :
     1. est-ce que tout va bien ?   -> le cockpit, en haut, seul sur sa ligne
     2. qu'est-ce qui cloche ?      -> la rubrique qui porte la couleur
     3. qu'est-ce que je fais ?     -> le bouton de cette rubrique */
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);
 font:var(--t-b,14px)/1.55 var(--sans,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif);
 -webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums;
 letter-spacing:-.006em;accent-color:var(--accent);padding-bottom:64px}
.wrap{max-width:1180px;margin:0 auto;padding:0 22px}
svg{fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
em{font-style:normal}

/* ---------------------------- la barre du haut ----------------------------
   Celle du panneau, a l'identique : meme hauteur, meme fond, meme filet, et
   aucun trait vertical. Une application ouverte depuis le hub ne doit pas
   donner l'impression d'avoir quitte CodeLab. */
.barre{background:var(--nav);color:var(--nav-txt);border-bottom:1px solid var(--nav-line);
 position:sticky;top:0;z-index:30}
.barre .wrap{display:flex;align-items:center;gap:12px;height:52px}
.marque{display:flex;align-items:center;gap:10px;font-size:var(--t-l,16px);
 font-weight:700;letter-spacing:-.015em}
.marque .jeton{width:26px;height:26px;border-radius:7px;background:var(--accent);
 color:var(--sur-accent);display:grid;place-items:center;flex:none}
.marque .jeton svg{width:15px;height:15px;stroke-width:2.2}
.marque em{font-weight:600;color:var(--nav-dim)}
.barre .spacer{flex:1}
.barre .quand{color:var(--nav-dim);font-size:var(--t-s,12px)}

/* ------------------------------- le cockpit -------------------------------
   Une seule carte, et elle repond a la seule question qu'on se pose en
   arrivant. L'anneau donne la proportion de sondes vertes, sa couleur donne
   le PIRE rang -- les deux ensemble, parce qu'aucun des deux ne suffit :
   quinze sondes sur seize, ce n'est pas la meme chose selon que la seizieme
   soit une alerte de disque ou Postgres a terre. */
.cockpit{display:flex;align-items:center;gap:28px;flex-wrap:wrap;
 background:var(--surface);border:1px solid var(--line);
 border-left:3px solid var(--line2);border-radius:var(--r,8px);
 box-shadow:0 1px 2px var(--shadow);padding:22px 26px;margin:24px 0 26px}
.cockpit.ok{border-left-color:var(--ok)}
.cockpit.warn{border-left-color:var(--warn)}
.cockpit.ko{border-left-color:var(--err)}
.cockpit .colonne{flex:1;min-width:300px}
.cockpit h1{font-size:var(--t-xl,22px);font-weight:800;letter-spacing:-.03em;
 line-height:1.2;margin:9px 0 7px}
.cockpit .phrase{color:var(--dim);font-size:var(--t-m,13px);max-width:68ch}
.cockpit .phrase b{color:var(--txt);font-weight:700}
.cockpit .actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:16px}
.cockpit .actions .sep{color:var(--dim2);font-size:var(--t-s,12px)}

/* L'ANNEAU. Le trait se remplit a la proportion de sondes vertes ; sa
   couleur est celle du pire rang. Statique : rien ne tourne ici, il n'y a
   rien a attendre. */
.anneau{position:relative;width:128px;height:128px;flex:none}
.anneau svg{width:128px;height:128px;transform:rotate(-90deg)}
.anneau circle{fill:none;stroke-width:9;stroke-linecap:round}
.anneau .fond{stroke:var(--surface2)}
.anneau.ok .part{stroke:var(--ok)}
.anneau.warn .part{stroke:var(--warn)}
.anneau.ko .part{stroke:var(--err)}
.anneau.neutre .part{stroke:var(--line2)}
.anneau .dedans{position:absolute;inset:0;display:grid;place-items:center;
 text-align:center;line-height:1.15}
.anneau .dedans b{display:block;font-size:27px;font-weight:800;letter-spacing:-.035em}
.anneau .dedans span{display:block;font-size:var(--t-xs,11px);color:var(--dim);
 margin-top:4px;letter-spacing:.02em}

/* Le mot d'etat : OK, ATTENTION, ERREUR. Les memes trois mots que les
   pastilles des lignes, pour que l'oeil fasse le lien sans effort. */
.etat-mot{display:inline-flex;align-items:center;gap:7px;padding:4px 11px;
 border-radius:999px;font-size:var(--t-xs,11px);font-weight:800;
 letter-spacing:.08em;text-transform:uppercase}
.etat-mot::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.etat-mot.ok{color:var(--ok);background:var(--ok-bg)}
.etat-mot.warn{color:var(--warn);background:var(--warn-bg)}
.etat-mot.ko{color:var(--err);background:var(--err-bg)}
.etat-mot.neutre{color:var(--dim2);background:var(--surface2)}

/* Les quatre compteurs. Un rang a zero s'efface au lieu de disparaitre :
   "aucune erreur" est une information, et une colonne qui change de largeur
   d'une visite a l'autre se relit a chaque fois. */
.compteurs{display:grid;grid-template-columns:repeat(2,minmax(122px,1fr));
 gap:9px;flex:none;width:min(292px,100%)}
.compteur{display:flex;align-items:baseline;gap:8px;padding:9px 13px;
 border-radius:var(--r-s,6px);border:1px solid var(--line);
 background:var(--surface2);min-width:124px}
.compteur b{font-size:var(--t-l,16px);font-weight:800;letter-spacing:-.02em}
.compteur span{font-size:var(--t-xs,11px);color:var(--dim);font-weight:700;
 letter-spacing:.06em;text-transform:uppercase}
.compteur.ok b{color:var(--ok)}
.compteur.warn b{color:var(--warn)}
.compteur.ko b{color:var(--err)}
.compteur.neutre b{color:var(--dim2)}
.compteur.warn{border-color:var(--warn-border);background:var(--warn-bg)}
.compteur.ko{border-color:var(--err-border);background:var(--err-bg)}
.compteur.vide{opacity:.5;border-color:var(--line);background:var(--surface2)}
.compteur.vide b{color:var(--dim2)}

h2{font-size:10px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;
 color:var(--dim2);margin:0 0 10px}

/* ------------------------------ les rubriques -----------------------------
   Seize sondes a la file, c'est une liste qu'on parcourt sans savoir ce
   qu'on cherche. Une carte par sujet, son etat en pastille, son propre
   bouton : on lit la rubrique qui cloche, et on approfondit celle-la.

   Deux colonnes des qu'il y a la place -- cinq cartes l'une sous l'autre
   faisaient defiler pour rien, et le cockpit sortait de l'ecran des la
   deuxieme. « dense » pour que la rubrique large ne laisse pas de trou
   derriere elle : sans lui, la carte pleine largeur poussait la suivante a
   la ligne et laissait une demi-page vide au milieu de la grille. */
.grille{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(440px,1fr));
 grid-auto-flow:row dense;align-items:start}
@media (max-width:820px){.grille{grid-template-columns:1fr}}
.rubrique{background:var(--surface);border:1px solid var(--line);
 border-left:3px solid var(--line2);border-radius:var(--r,8px);
 box-shadow:0 1px 2px var(--shadow);overflow:hidden}
/* Le bandeau de severite au bord de la carte : on repere la rubrique en
   cause sans lire une seule ligne. Quatre rangs, quatre bandeaux -- "disque
   a 87 pour cent" ne doit pas porter le rouge de "Postgres ne repond pas". */
.rubrique.ok{border-left-color:var(--ok)}
.rubrique.warn{border-left-color:var(--warn)}
.rubrique.ko{border-left-color:var(--err)}
.rubrique.neutre{border-left-color:var(--line2)}
/* La rubrique qui porte ses propres tableaux prend la largeur : ses colonnes
   ne tiennent pas dans une demi-page. */
.rubrique.large{grid-column:1/-1}
.rub-tete{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:14px 16px;
 border-bottom:1px solid var(--line)}
.rub-titre{min-width:0;flex:1}
.rub-titre b{font-size:var(--t-b,14px);font-weight:700;letter-spacing:-.015em;display:block}
.rub-titre span{font-size:var(--t-s,12px);color:var(--dim);display:block;margin-top:2px}

/* Une sonde par bloc, pas par cellule de tableau : le detail d'une sonde est
   une phrase, parfois longue, et une troisieme colonne la cassait en
   accordeon. Le nom et l'etat sur une ligne, la phrase dessous. */
.sonde{padding:11px 16px;border-top:1px solid var(--line)}
.sonde:first-child{border-top:none}
.sonde:hover{background:var(--surface2)}
.sonde .ligne{display:flex;align-items:center;gap:10px}
.sonde .nom{font-weight:650;letter-spacing:-.012em;flex:1;min-width:0}
.sonde .det{color:var(--dim);font-size:var(--t-s,12px);margin-top:4px;
 line-height:1.55;word-break:break-word;white-space:pre-wrap}

/* La pastille d'etat, comme dans le panneau : fond teinte, coins pleins,
   point devant. Le meme objet dans la page et dans la fenetre. */
.st span,.pastille{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;
 border-radius:999px;font-size:var(--t-xs,11px);font-weight:700;white-space:nowrap}
.st span::before,.pastille::before{content:"";width:6px;height:6px;border-radius:50%;
 background:currentColor;flex:none}
.st.ok span,.pastille.ok{color:var(--ok);background:var(--ok-bg)}
.st.warn span,.pastille.warn{color:var(--warn);background:var(--warn-bg)}
.st.neutre span,.pastille.neutre{color:var(--dim2);background:var(--surface2)}
.st.ko span,.pastille.ko{color:var(--err);background:var(--err-bg)}
.st{white-space:nowrap;width:1%}

/* --------------------------- tableaux et details -------------------------- */
.rub-corps{padding:16px;border-top:1px solid var(--line);background:var(--surface2)}
.rub-corps h2{margin-top:18px}
.rub-corps h2:first-child{margin-top:0}
.tbl-wrap{background:var(--surface);border:1px solid var(--line);
 border-radius:var(--r-s,6px);overflow:hidden}
table{width:100%;border-collapse:collapse;background:var(--surface)}
th{font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
 color:var(--dim2);text-align:left;padding:9px 14px;background:var(--surface2);
 border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 14px;border-top:1px solid var(--line);vertical-align:top;
 font-size:var(--t-m,13px)}
tr:first-child td{border-top:none}
tbody tr:hover{background:var(--surface2)}
.nom{font-weight:650;letter-spacing:-.012em}
/* Le monospace ne sert qu'a ce qui EST du code : un chemin, un port, un
   extrait de configuration. Une phrase entiere en monospace se lit deux fois
   moins vite, et c'est ce que faisait cette page sur toutes ses lignes. */
.det{color:var(--dim);font-size:var(--t-s,12px);word-break:break-word}
.src{font-weight:700}
code{font-family:var(--mono,ui-monospace,Menlo,monospace);background:var(--surface2);
 border:1px solid var(--line);border-radius:var(--r-xs,4px);padding:1px 5px;
 font-size:var(--t-s,12px)}
.note{color:var(--dim);font-size:var(--t-s,12px);line-height:1.6}
.rub-corps .note{margin-top:10px}
.mod-corps .note{margin-top:13px}
.pied{margin-top:18px;color:var(--dim);font-size:var(--t-s,12px);line-height:1.6;
 max-width:88ch}
.err{background:var(--err-bg);border:1px solid var(--err-border);color:var(--err-txt);
 padding:12px 14px;border-radius:var(--r-s,6px);
 font-family:var(--mono,ui-monospace,Menlo,monospace);font-size:var(--t-s,12px);
 white-space:pre-wrap}

/* Le bandeau de verdict de la FENETRE : elle n'a pas de cockpit, et il lui
   faut sa propre conclusion en une ligne. */
.verdict{display:flex;align-items:center;gap:10px;padding:12px 15px;
 border-radius:var(--r-s,6px);font-weight:650;margin-bottom:16px;line-height:1.45;
 border:1px solid var(--line);background:var(--surface2)}
.verdict::before{content:"";width:8px;height:8px;border-radius:50%;flex:none}
.verdict.ok{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-border)}
.verdict.ok::before{background:var(--ok)}
.verdict.warn{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-border)}
.verdict.warn::before{background:var(--warn)}
.verdict.ko{color:var(--err);background:var(--err-bg);border-color:var(--err-border)}
.verdict.ko::before{background:var(--err)}

/* -------------------------------- boutons -------------------------------- */
.btn{display:inline-flex;align-items:center;gap:7px;padding:8px 14px;
 border-radius:var(--r-s,6px);border:1px solid transparent;cursor:pointer;
 font:inherit;font-size:var(--t-m,13px);font-weight:650;white-space:nowrap}
.btn svg{width:15px;height:15px}
.btn-primaire{background:var(--accent);color:var(--sur-accent)}
.btn-primaire:hover{background:var(--accent-h)}
.btn-simple{background:var(--surface);color:var(--txt);border-color:var(--line2)}
.btn-simple:hover{border-color:var(--accent);color:var(--accent)}
.btn-sm{padding:5px 10px;font-size:var(--t-s,12px)}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.btn:disabled{opacity:.55;cursor:default}
.case{display:flex;align-items:center;gap:7px;font-size:var(--t-m,13px);
 color:var(--txt);cursor:pointer}
.case input{width:auto;margin:0;accent-color:var(--accent)}

/* ---------------- la verification approfondie, en fenetre ----------------
   Elle ne quitte plus la page : on la lance depuis la rubrique qu'on vient
   de lire, et on revient a elle en fermant. La fenetre annonce ses lignes
   AVANT de les jouer, et les remplit une par une -- une fenetre qui reste
   blanche trois minutes ne dit ni ou elle en est, ni si elle avance. */
.voile{position:fixed;inset:0;background:rgba(0,0,0,.5);display:none;
 align-items:center;justify-content:center;padding:20px;z-index:50}
.voile.ouvert{display:flex}
.modale{background:var(--bg);border:1px solid var(--line);border-radius:var(--r,8px);
 box-shadow:0 16px 48px var(--shadow-2);width:min(880px,100%);
 max-height:min(88vh,920px);display:flex;flex-direction:column}
.mod-tete{display:flex;align-items:center;gap:14px;padding:15px 18px;
 border-bottom:1px solid var(--line);background:var(--surface);
 border-radius:var(--r,8px) var(--r,8px) 0 0}
.mod-tete b{font-size:var(--t-b,14px);font-weight:700;display:block}
.mod-tete span{font-size:var(--t-s,12px);color:var(--dim);display:block;margin-top:2px}
.mod-tete .spacer{flex:1}
.fermer{background:none;border:0;color:var(--dim);cursor:pointer;font-size:20px;
 line-height:1;padding:4px 9px;border-radius:var(--r-s,6px)}
.fermer:hover{background:var(--surface2);color:var(--txt)}
.mod-corps{padding:18px;overflow:auto}
.mod-pied{padding:13px 18px;border-top:1px solid var(--line);display:flex;
 align-items:center;gap:10px;flex-wrap:wrap;background:var(--surface);
 border-radius:0 0 var(--r,8px) var(--r,8px)}
.mod-pied .spacer{flex:1}

/* LA ROUE. Un cercle SVG dont on fait varier le trait : il avance quand un
   test FINIT, donc il ne peut pas mentir. Le compte au centre dit la meme
   chose en chiffres -- une roue seule ne dit pas combien il en reste. */
.roue-zone{display:flex;align-items:center;gap:18px;margin-bottom:18px}
.roue{width:84px;height:84px;flex:none;transform:rotate(-90deg)}
.roue circle{fill:none;stroke-width:8}
.roue .fond{stroke:var(--surface2)}
.roue .part{stroke:var(--accent);stroke-linecap:round;
 transition:stroke-dashoffset .3s ease}
.roue-boite{position:relative;width:84px;height:84px;flex:none}
.roue-boite .dedans{position:absolute;inset:0;display:grid;place-items:center;
 font-size:var(--t-m,13px);font-weight:800}
/* Pendant la verification, la roue est turquoise : elle dit l'avancement,
   pas l'etat. Une fois finie, elle prend la couleur du verdict -- sinon
   elle affiche cent pour cent en turquoise a cote d'un bandeau rouge, et
   dit deux choses a la fois. */
.roue-boite.ok .part{stroke:var(--ok)}
.roue-boite.ko .part{stroke:var(--err)}
.roue-zone .chiffres b{font-size:23px;font-weight:800;letter-spacing:-.025em;
 display:block;line-height:1.1}
.roue-zone .chiffres span{font-size:var(--t-s,12px);color:var(--dim)}

.st.attente span{color:var(--dim2);background:var(--surface2)}
.st.encours span{color:var(--accent);background:var(--accent-soft)}
/* Le point de la ligne en cours bat : c'est le seul endroit de la page ou
   une animation apprend quelque chose. */
.st.encours span::before{animation:bat 1s ease-in-out infinite}
@keyframes bat{0%,100%{opacity:1}50%{opacity:.25}}
/* Dans la fenetre, la severite se lit au bord de la LIGNE : c'est un
   tableau, il n'y a pas de carte a border. */
td:first-child{box-shadow:inset 3px 0 0 var(--line2);padding-left:17px}
tr.ok td:first-child{box-shadow:inset 3px 0 0 var(--ok)}
tr.warn td:first-child{box-shadow:inset 3px 0 0 var(--warn)}
tr.neutre td:first-child{box-shadow:inset 3px 0 0 var(--line2)}
tr.ko td:first-child{box-shadow:inset 3px 0 0 var(--err)}
tr.encours td:first-child{box-shadow:inset 3px 0 0 var(--accent)}
.duree{color:var(--dim2);font-size:var(--t-xs,11px);white-space:nowrap}
"""

# Le script de la fenetre de verification. Hors f-string : il est plein
# d'accolades, et les doubler rendrait le code illisible pour la seule
# commodite du formatage.
SCRIPT = """
const $ = i => document.getElementById(i);
/* Les tests, leur rang et leur rubrique, poses par le serveur : la fenetre
   n'a pas a redemander ce que la page connait deja. */
const TESTS = window.CODELAB_TESTS;
const RUBRIQUES = window.CODELAB_RUBRIQUES;
let enCours = false;

function choisis(cle, avecSuite){
  return TESTS.filter(t => (cle === null || t.theme === cle)
                        && (avecSuite || t.theme !== 'code'));
}

function ouvrir(cle){
  if(enCours) return;
  const titre = cle === null ? 'Toutes les rubriques' : (RUBRIQUES[cle] || cle);
  $('mod-sujet').textContent = titre;
  $('voile').classList.add('ouvert');
  $('mod-verdict').style.display = 'none';
  lancer(cle);
}

function fermer(){
  if(enCours) return;
  $('voile').classList.remove('ouvert');
}

function roue(fait, total){
  const r = 32, c = 2 * Math.PI * r;
  const part = total ? fait / total : 0;
  $('roue-part').style.strokeDasharray = c;
  $('roue-part').style.strokeDashoffset = c * (1 - part);
  $('roue-txt').textContent = Math.round(part * 100) + '%';
  $('roue-compte').textContent = fait + ' / ' + total;
}

async function lancer(cle){
  const avecSuite = $('suite').checked;
  const liste = choisis(cle, avecSuite);
  enCours = true;
  $('roue-boite').className = 'roue-boite';
  $('relancer').disabled = true;
  $('suite').disabled = true;
  $('mod-etat').textContent = 'Vérification en cours...';
  $('mod-lignes').innerHTML = liste.map((t, r) =>
    '<tr id="l' + r + '"><td class="st attente"><span>en attente</span></td>'
    + '<td class="nom"></td><td class="det" id="d' + r + '">—</td>'
    + '<td class="duree" id="ms' + r + '"></td></tr>').join('');
  liste.forEach((t, r) => {
    $('l' + r).querySelector('.nom').textContent = t.nom;
  });
  roue(0, liste.length);

  let reussis = 0;
  for(let r = 0; r < liste.length; r++){
    const ligne = $('l' + r), st = ligne.querySelector('.st');
    ligne.className = 'encours';
    st.className = 'st encours';
    st.firstElementChild.textContent = 'en cours';
    let d;
    try{
      d = await (await fetch('api/test/' + liste[r].i
                             + (avecSuite ? '' : '?suite=0'))).json();
    }catch(e){
      d = {ok: false, detail: "La page n'a pas pu joindre le diagnostic : " + e};
    }
    /* Le rang vient du serveur quand il l'envoie : un test « sans objet »
       -- la suite de regressions dans une image sans pytest -- n'est ni
       vert ni rouge, et le peindre en rouge ferait chercher une panne. */
    const classe = d.classe || (d.ok ? 'ok' : 'ko');
    ligne.className = classe;
    st.className = 'st ' + classe;
    st.firstElementChild.textContent = d.etat || (d.ok ? 'OK' : 'ÉCHEC');
    $('d' + r).textContent = d.detail || '';
    $('ms' + r).textContent = (d.ms != null) ? d.ms + ' ms' : '';
    if(d.ok) reussis++;
    roue(r + 1, liste.length);
  }

  const n = liste.length, v = $('mod-verdict');
  const classe = reussis === n ? 'ok' : 'ko';
  $('roue-boite').className = 'roue-boite ' + classe;
  v.className = 'verdict ' + classe;
  v.textContent = reussis === n
    ? reussis + ' test' + (reussis > 1 ? 's' : '') + ' sur ' + n + ' : tout passe.'
    : (n - reussis) + ' test' + (n - reussis > 1 ? 's' : '') + ' en échec sur '
      + n + ' — chaque ligne rouge porte son détail.';
  v.style.display = 'flex';
  $('mod-etat').textContent = 'Terminé.';
  enCours = false;
  $('relancer').disabled = false;
  $('suite').disabled = false;
  $('relancer').dataset.cle = cle === null ? '' : cle;
}

document.addEventListener('click', e => {
  const b = e.target.closest('[data-verifier]');
  if(b) ouvrir(b.dataset.verifier || null);
});
$('fermer').addEventListener('click', fermer);
$('voile').addEventListener('click', e => { if(e.target === $('voile')) fermer(); });
document.addEventListener('keydown', e => { if(e.key === 'Escape') fermer(); });
$('relancer').addEventListener('click', () => {
  const cle = $('relancer').dataset.cle;
  lancer(cle ? cle : null);
});
"""

# La fenetre elle-meme. Posee dans la page des le premier rendu : une
# fenetre construite en JavaScript au moment du clic n'existe pas tant qu'on
# n'a pas clique, donc elle ne se teste pas et elle ne se lit pas.
MODALE = """
<div class="voile" id="voile" role="dialog" aria-modal="true" aria-labelledby="mod-sujet">
  <div class="modale">
    <div class="mod-tete">
      <div><b>Vérification approfondie</b>
        <span id="mod-sujet">Toutes les rubriques</span></div>
      <div class="spacer"></div>
      <button class="fermer" id="fermer" aria-label="Fermer">&times;</button>
    </div>
    <div class="mod-corps">
      <div class="roue-zone">
        <div class="roue-boite" id="roue-boite">
          <svg class="roue" viewBox="0 0 76 76">
            <circle class="fond" cx="38" cy="38" r="32"></circle>
            <circle class="part" id="roue-part" cx="38" cy="38" r="32"></circle>
          </svg>
          <div class="dedans" id="roue-txt">0%</div>
        </div>
        <div class="chiffres"><b id="roue-compte">0 / 0</b>
          <span id="mod-etat">En attente.</span></div>
      </div>
      <div class="verdict" id="mod-verdict" style="display:none"></div>
      <div class="tbl-wrap">
        <table><tr><th>État</th><th>Test</th><th>Détail</th><th>Durée</th></tr>
        <tbody id="mod-lignes"></tbody></table>
      </div>
      <div class="note">Ces tests <b>agissent</b> : ils écrivent en base, traversent le
        reverse proxy et laissent une trace dans les journaux. Ils ne modifient rien
        d'autre &mdash; aucune application, aucun compte, aucun réglage.</div>
    </div>
    <div class="mod-pied">
      <label class="case"><input type="checkbox" id="suite" checked>
        Inclure la suite de régressions du panneau
        <span class="duree">(la plus longue)</span></label>
      <div class="spacer"></div>
      <button class="btn btn-simple" id="relancer">Relancer</button>
    </div>
  </div>
</div>
"""


def esc(v):
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _barre(quand):
    """La barre du haut du panneau, reprise a l'identique.

    Elle appartient au CADRE, pas a la page : une deuxieme page du
    diagnostic la reprend sans la reecrire. Aucun trait vertical a cote de
    la marque -- c'est le cadre du panneau, pas une application invitee qui
    se presente.
    """
    return ('<div class="barre"><div class="wrap">'
            '<div class="marque"><span class="jeton">'
            f'<svg viewBox="0 0 24 24"><path d="{MARQUE_TRACE}"/></svg>'
            '</span>CodeLab <em>Diagnostic</em></div>'
            '<div class="spacer"></div>'
            f'<div class="quand">{esc(quand)}</div></div></div>')


def page(titre, corps, script_final="", quand=""):
    """Le gabarit commun : theme du panneau, meme police, meme fond.

    Un seul gabarit pour toute l'application -- deux divergent au premier
    changement, et le diagnostic se mettrait a ressembler a deux applications
    differentes selon la page ouverte.
    """
    return ("<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\">"
            + THEME + FAVICON + SUIVRE_LE_THEME
            + "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            + f"<title>{esc(titre)}</title><style>{CSS}</style></head><body>"
            + _barre(quand) + f"<div class=\"wrap\">{corps}</div>{MODALE}"
            + f"<script>{script_final}</script>"
            + f"<script>{SCRIPT}</script></body></html>")


@app.get("/health")
def health():
    return {"ok": True}


# Le theme du panneau, tel qu'il est sur le disque -- jamais recopie.
#
# Trois endroits, dans cet ordre : ce que l'exploitant impose, l'image du
# panneau (le cas normal : cette application tourne DANS ce conteneur), puis
# l'arborescence du depot, pour la machine de developpement.
THEMES_POSSIBLES = (
    os.environ.get("CODELAB_THEME_CSS", ""),
    "/opt/codelab/app-manager/app/theme.css",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "app-manager", "app", "theme.css"),
)


def fichier_du_theme():
    """Le premier chemin qui existe, ou une chaine vide."""
    for chemin in THEMES_POSSIBLES:
        if chemin and os.path.isfile(chemin):
            return chemin
    return ""


@app.get("/theme.css")
def theme_local():
    """La feuille du panneau, servie par l'application elle-meme.

    Elle ne double pas le panneau pour le plaisir : le lien relatif de la
    page passe par ici, donc l'apparence ne depend plus de ce que le port
    des applications veut bien servir a la racine. Le fichier est le MEME --
    il n'y a toujours qu'une seule palette dans le depot.
    """
    chemin = fichier_du_theme()
    if not chemin:
        return Response("", status=404, mimetype="text/css")
    with open(chemin, encoding="utf-8") as f:
        contenu = f.read()
    return Response(contenu, mimetype="text/css",
                    headers={"Cache-Control": "public, max-age=60"})


@app.get("/polices/<nom>")
def police_locale(nom):
    """La police que le theme reclame, prise a cote de lui.

    Liste blanche explicite : sans elle, ce chemin deviendrait une lecture de
    fichier arbitraire des qu'un nom contient deux points. Werkzeug refuse
    deja les segments de ce genre, mais la garde ne doit pas dependre d'un
    detail du routeur.
    """
    theme = fichier_du_theme()
    if not theme or not nom.endswith(".woff2") or "/" in nom or ".." in nom:
        return Response("", status=404, mimetype="text/plain")
    chemin = os.path.join(os.path.dirname(theme), "polices", nom)
    if not os.path.isfile(chemin):
        return Response("", status=404, mimetype="text/plain")
    with open(chemin, "rb") as f:
        octets = f.read()
    return Response(octets, mimetype="font/woff2",
                    headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/api/test/<int:indice>")
def api_test(indice):
    """Un test, et un seul. C'est ce qui permet a la fenetre d'avancer.

    Le rang vient de l'URL : il se borne ici plutot que de faire confiance a
    ce qu'on recoit -- un indice hors liste leverait une IndexError et
    rendrait une page d'erreur au milieu d'une verification.
    """
    avec_suite = request.args.get("suite") != "0"
    noms = checks.noms_des_tests(avec_suite)
    if not 0 <= indice < len(noms):
        return {"ok": False, "nom": "", "detail": "Test inconnu."}, 404
    debut = time.time()
    etat, nom, detail = checks.run_test(indice, avec_suite)
    # "ok" reste, et reste un booleen : c'est le contrat que la fenetre lit
    # pour compter les reussites. Le rang s'ajoute a cote, pour la pastille --
    # un test qui ne s'applique pas ici n'est ni vert ni rouge.
    return {"ok": bool(etat), "etat": etat.libelle, "classe": etat.classe,
            "nom": nom, "detail": detail,
            "ms": int((time.time() - debut) * 1000)}


def _lignes_sondes(resultats):
    """Une sonde par bloc, teintee par son RANG.

    La classe CSS et le libelle viennent tous deux de checks.Etat : une
    seule source de verite pour la couleur de la pastille et le mot affiche
    -- sans quoi les deux divergent au premier rang ajoute.

    Le detail passe SOUS le nom, en pleine largeur. En troisieme colonne, il
    se cassait en accordeon des qu'il depassait quelques mots -- et il les
    depasse presque toujours : c'est une phrase, pas une valeur.
    """
    return "".join(
        f'<div class="sonde"><div class="ligne">'
        f'<span class="nom">{esc(nom)}</span>'
        f'<span class="pastille {etat.classe}">{etat.libelle}</span></div>'
        f'<div class="det">{esc(det)}</div></div>'
        for etat, nom, det in resultats)


def _rubrique(cle, titre, description, resultats, extra=""):
    """Une rubrique : son etat au bord, ses sondes, son propre bouton.

    La pastille compte les sondes VERIFIEES et prend la couleur du pire rang
    de la rubrique, comme le bandeau de la carte. Une rubrique qui ne porte
    qu'une alerte est orange, pas rouge : c'est ce qui permet de voir d'un
    coup d'oeil laquelle demande un geste maintenant, et laquelle attend
    qu'on passe.
    """
    total = len(resultats)
    passees = sum(1 for etat, _, _ in resultats if etat is checks.Etat.OK)
    rang = checks.pire(resultats).classe
    pastille = (f'<span class="pastille {rang}">'
                f'{passees} sur {total}</span>')
    bouton = (f'<button class="btn btn-simple btn-sm" data-verifier="{esc(cle)}">'
              f'Vérifier</button>')
    large = " large" if extra else ""
    return (f'<section class="rubrique {rang}{large}" id="rub-{esc(cle)}">'
            f'<div class="rub-tete"><div class="rub-titre"><b>{esc(titre)}</b>'
            f'<span>{esc(description)}</span></div>'
            f'{pastille}{bouton}</div>'
            f'<div>{_lignes_sondes(resultats)}</div>'
            f'{extra}</section>')


def _anneau(verts, applicables, rang):
    """L'anneau du cockpit : la proportion de sondes vertes, la couleur du
    pire rang.

    Le denominateur ecarte les sondes SANS OBJET -- un anneau qui n'arrive
    jamais au bout parce que trois sondes ne s'appliquent pas ici fait
    chercher une panne qui n'existe pas.
    """
    rayon, tour = 56, 2 * 3.141592653589793 * 56
    part = (verts / applicables) if applicables else 1.0
    return (f'<div class="anneau {rang}">'
            f'<svg viewBox="0 0 128 128" aria-hidden="true">'
            f'<circle class="fond" cx="64" cy="64" r="{rayon}"></circle>'
            f'<circle class="part" cx="64" cy="64" r="{rayon}" '
            f'stroke-dasharray="{tour:.1f}" '
            f'stroke-dashoffset="{tour * (1 - part):.1f}"></circle></svg>'
            f'<div class="dedans"><div><b>{verts}/{applicables}</b>'
            f'<span>sondes vertes</span></div></div></div>')


def _compteur(nombre, libelle, classe):
    """Un compteur du cockpit. A zero, il s'efface au lieu de disparaitre :
    « aucune erreur » est une information, et une rangee qui change de
    largeur d'une visite a l'autre se relit a chaque fois."""
    return (f'<div class="compteur {classe if nombre else "vide"}">'
            f'<b>{nombre}</b><span>{libelle}</span></div>')


@app.get("/tests")
def tests():
    """L'ancienne page dediee : elle ouvre desormais la fenetre, sur tout.

    Le lien a circule -- il est dans les journaux, dans les favoris, et dans
    la page d'accueil de versions precedentes. Il continue de mener la ou il
    a toujours mene : a la verification approfondie, simplement dans sa
    fenetre et devant l'etat des lieux plutot qu'a la place.
    """
    return index(ouvrir_verification=True)


@app.get("/")
def index(ouvrir_verification=False):
    resultats = checks.run_all()

    # Battement de coeur : cette page ecrit sa propre ligne a chaque visite,
    # l'asset Dagster ecrit les siennes. Les deux sources doivent apparaitre.
    ecriture, par_source, recentes, erreur_db = None, [], [], None
    try:
        conn = checks.connect_pg()
        try:
            ecriture = f"ligne #{checks.write_heartbeat(conn, SOURCE, 'visite de la page')} insérée"
            par_source, recentes = checks.read_heartbeats(conn)
        finally:
            conn.close()
    except checks.PiloteAbsent as e:
        erreur_db = str(e)
    except Exception:
        erreur_db = traceback.format_exc(limit=3)

    # Le verdict suit la HIERARCHIE, il ne compte pas des booleens. Une page
    # entierement rouge des qu'une ligne n'est pas verte n'apprend rien : ce
    # qu'on veut savoir en arrivant, c'est s'il faut agir maintenant, plus
    # tard, ou pas du tout.
    rangs = checks.noms_par_rang(resultats)
    critiques, alertes = rangs[checks.Etat.ECHEC], rangs[checks.Etat.ALERTE]
    sans_objet, verts = rangs[checks.Etat.SANS_OBJET], rangs[checks.Etat.OK]
    dagster_a_ecrit = "dagster" in {s for s, _, _ in par_source}

    # L'erreur de base de donnees compte comme un echec : la page la montre
    # plus bas, mais le cockpit doit deja la porter -- personne ne descend
    # lire une rubrique quand le haut de page annonce que tout va bien.
    rang = checks.Etat.ECHEC if (critiques or erreur_db) else (
        checks.Etat.ALERTE if (alertes or not dagster_a_ecrit) else checks.Etat.OK)

    if critiques or erreur_db:
        combien = len(critiques) + (1 if erreur_db else 0)
        titre = (f'{combien} vérification{"s" if combien > 1 else ""} en échec')
        phrase = ('À traiter maintenant. La rubrique en cause porte sa couleur '
                  'ci-dessous&nbsp;: ' + (f'<b>{esc(", ".join(critiques))}</b>.'
                                          if critiques else
                                          "l'accès à la base de données."))
    elif alertes:
        titre = f'{len(alertes)} alerte{"s" if len(alertes) > 1 else ""}, rien de critique'
        phrase = (f'<b>{esc(", ".join(alertes))}</b> &mdash; à regarder quand tu passes. '
                  'La stack rend son service.')
    elif not dagster_a_ecrit:
        titre = "Toutes les sondes passent"
        phrase = ('Mais aucune ligne écrite par Dagster &mdash; c\'est le seul maillon '
                  'encore non vérifié. Elle arrive toute seule au prochain passage du '
                  'planning.')
    else:
        titre = "Chaîne complète vérifiée"
        phrase = ('Les cinq services communiquent, et Postgres contient des écritures '
                  'de l\'application <em>et</em> de Dagster.')

    applicables = len(resultats) - len(sans_objet)
    cockpit = (
        f'<section class="cockpit {rang.classe}">'
        + _anneau(len(verts), applicables, rang.classe)
        + f'<div class="colonne">'
        f'<span class="etat-mot {rang.classe}">{rang.libelle}</span>'
        f'<h1>{esc(titre)}</h1><div class="phrase">{phrase}</div>'
        '<div class="actions">'
        '<button class="btn btn-primaire" data-verifier="">'
        '<svg viewBox="0 0 24 24"><path d="M5 12l5 5L20 7"/></svg>'
        'Lancer la vérification approfondie</button>'
        + (f'<span class="sep">{esc(ecriture)}</span>' if ecriture else '')
        + '</div></div>'
        '<div class="compteurs">'
        + _compteur(len(critiques), "erreurs", "ko")
        + _compteur(len(alertes), "attention", "warn")
        + _compteur(len(verts), "au vert", "ok")
        + _compteur(len(sans_objet), "sans objet", "neutre")
        + '</div></section>')

    if erreur_db:
        bloc_db = f'<div class="err">{esc(erreur_db)}</div>'
    elif par_source:
        bloc_db = ('<table><tr><th>Source</th><th>Lignes</th><th>Dernière écriture</th></tr>'
                   + "".join(f'<tr><td class="src">{esc(s)}</td><td>{n}</td>'
                             f'<td class="det">{esc(d)}</td></tr>' for s, n, d in par_source)
                   + '</table>')
        if not dagster_a_ecrit:
            bloc_db += ('<div class="note"><b>Aucune ligne <code>dagster</code> :</b> c\'est le '
                        'seul maillon encore non vérifié. Elle arrive toute seule &mdash; le '
                        'planning <code>diagnostic_toutes_les_quinze_minutes</code> matérialise '
                        'l\'asset <code>diagnostic_codelab</code> quatre fois par heure, et la '
                        'première ligne paraît ici au prochain passage. Si rien ne vient au bout '
                        'd\'une demi-heure, Dagster ne tourne pas : la sonde '
                        '<code>codelab-dagster</code> le dit, et son interface est sur le '
                        'port 3000.</div>')
    else:
        bloc_db = ('<div class="note">Table vide : elle vient d\'être créée. Recharge la page &mdash; '
                   'cette visite y écrit déjà sa ligne, et Dagster ajoutera la sienne au prochain '
                   'passage du planning.</div>')

    recent = ("".join(
        f'<tr><td class="det">#{i}</td><td class="src">{esc(s)}</td>'
        f'<td class="det">{esc(d or "")}</td><td class="det">{esc(t)}</td></tr>'
        for i, s, d, t in recentes)
        or '<tr><td colspan="4" class="det">aucune ligne</td></tr>')

    # La rubrique « Données » porte en plus ce que la base contient : c'est
    # la meme question, et la separer en deux sections eloignait la preuve de
    # ce qu'elle prouve.
    detail_donnees = (
        f'<div class="rub-corps">'
        f'<h2>Table {esc(checks.TABLE)} &mdash; qui a écrit</h2>'
        f'{bloc_db}'
        f'<h2>Dernières écritures</h2>'
        f'<div class="tbl-wrap"><table>'
        f'<tr><th>Id</th><th>Source</th><th>Détail</th><th>Date</th></tr>{recent}</table></div>'
        f'<div class="note">Cette page écrit une ligne <code>app-manager</code> à chaque '
        f'rechargement. Les lignes <code>dagster</code> viennent de l\'asset '
        f'<code>diagnostic_codelab</code> de <code>/workspace/definitions.py</code>.</div>'
        f'</div>')

    rubriques = "".join(
        _rubrique(cle, titre, desc, sondes,
                  detail_donnees if cle == "donnees" else "")
        for cle, titre, desc, sondes in checks.sondes_par_theme(resultats))

    quand = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    corps = (
        cockpit
        + '<h2>Rubriques</h2>'
        + f'<div class="grille">{rubriques}</div>'
        + '<div class="pied">Ces sondes ne font que <b>regarder</b>, et tournent à chaque '
          'affichage. La vérification approfondie, elle, <b>agit</b> : elle écrit en base, '
          'traverse le proxy et vérifie que le journal enregistre. Lance-la sur tout depuis '
          'le bouton du haut, ou sur la seule rubrique qui cloche depuis son propre '
          'bouton.</div>')

    # Le rang des tests et leur rubrique, poses pour la fenetre. Ecrits en
    # JSON par le serveur : la page ne devine rien de ce que le module sait.
    par_theme = checks.indices_par_rubrique()
    noms = checks.noms_des_tests()
    tests_json = json.dumps(
        [{"i": i, "nom": noms[i], "theme": cle}
         for cle, indices in par_theme.items() for i in indices],
        ensure_ascii=False)
    libelles = dict((cle, titre) for cle, titre, _ in checks.THEMES)
    libelles["code"] = "Suite de régressions du panneau"
    donnees = (f"window.CODELAB_TESTS={tests_json};"
               f"window.CODELAB_RUBRIQUES={json.dumps(libelles, ensure_ascii=False)};")
    if ouvrir_verification:
        # Le lien /tests ouvre la fenetre de lui-meme, sur tout.
        donnees += "window.addEventListener('load',function(){ouvrir(null);});"
    return page("CodeLab · état de santé", corps, donnees,
                quand=f"Exécuté dans codelab-app-manager · {quand}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
