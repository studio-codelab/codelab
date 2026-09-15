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

from flask import Flask, request  # noqa: E402  (present dans l'image app-manager)

import checks  # noqa: E402

app = Flask(__name__)
SOURCE = "app-manager"

# Le theme du panneau, servi par lui sur la MEME origine que les
# applications : le diagnostic peut donc le charger, et il suit alors les
# memes couleurs, les memes rayons, la meme police que le reste de CodeLab.
#
# Il ne les recopie surtout pas : une palette recopiee dans une deuxieme page
# est une palette qui divergera, et le diagnostic finirait par annoncer une
# stack saine dans des couleurs qui ne sont plus celles de la stack.
THEME = '<link rel="stylesheet" href="/theme.css">'

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
# dur -- tout passe par les jetons de theme.css.
CSS = """
/* Les memes composants que le panneau, ecrits avec ses jetons : carte,
   pastille d'etat, bandeau de severite au bord de la ligne, chiffres
   alignes. Le diagnostic est une PAGE DE PLUS de CodeLab, pas une
   application invitee -- il doit se lire comme le reste. */
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);
 font:var(--t-b,14px)/1.5 var(--sans,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif);
 padding:28px 20px 56px;-webkit-font-smoothing:antialiased;
 font-feature-settings:"tnum";letter-spacing:-.006em;accent-color:var(--accent)}
.wrap{max-width:1000px;margin:0 auto}

.tete{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.tete .spacer{flex:1}
h1{font-size:var(--t-xl,22px);font-weight:800;letter-spacing:-.03em;line-height:1.2}
.sub{color:var(--dim);font-size:var(--t-m,13px);margin-top:4px;max-width:76ch}

/* Le verdict est la premiere chose lue : c'est la tuile de tete du cockpit. */
.verdict{display:flex;align-items:baseline;gap:10px;padding:13px 16px;
 border-radius:var(--r,8px);font-weight:700;margin-bottom:22px;
 border:1px solid var(--line);background:var(--surface);
 box-shadow:0 1px 2px var(--shadow);line-height:1.45}
.verdict::before{content:"";width:8px;height:8px;border-radius:50%;flex:none;
 align-self:center}
.verdict.ok::before{background:var(--ok)}
.verdict.ko::before{background:var(--err)}
.verdict.ok{color:var(--ok)}
.verdict.ko{color:var(--err)}

h2{font-size:10px;font-weight:650;letter-spacing:.08em;text-transform:uppercase;
 color:var(--dim2);margin:24px 0 9px}

/* ------------------------- une rubrique -------------------------
   Seize sondes a la file, c'est une liste qu'on parcourt sans savoir ce
   qu'on cherche. Une carte par sujet, son etat en pastille et son propre
   bouton de verification : on lit la rubrique qui cloche, et on approfondit
   celle-la. */
.rubrique{background:var(--surface);border:1px solid var(--line);
 border-radius:var(--r,8px);box-shadow:0 1px 2px var(--shadow);
 margin-bottom:14px;overflow:hidden}
.rub-tete{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
 padding:14px 16px;border-bottom:1px solid var(--line)}
.rub-titre b{font-size:var(--t-b,14px);font-weight:700;letter-spacing:-.015em;
 display:block}
.rub-titre span{font-size:var(--t-s,12px);color:var(--dim);display:block;margin-top:2px}
.rub-tete .spacer{flex:1}
.rubrique table{width:100%;border-collapse:collapse}
.rubrique td{padding:9px 14px;border-top:1px solid var(--line);vertical-align:top;
 font-size:var(--t-m,13px)}
.rubrique tr:first-child td{border-top:none}
.rubrique tbody tr:hover{background:var(--surface2)}

.tbl-wrap{background:var(--surface);border:1px solid var(--line);
 border-radius:var(--r,8px);overflow:hidden;box-shadow:0 1px 2px var(--shadow)}
table{width:100%;border-collapse:collapse}
th{font-size:10px;font-weight:650;letter-spacing:.08em;text-transform:uppercase;
 color:var(--dim2);text-align:left;padding:9px 14px;background:var(--surface);
 border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 14px;border-top:1px solid var(--line);vertical-align:top;
 font-size:var(--t-m,13px)}
tr:first-child td{border-top:none}
tbody tr:hover{background:var(--surface2)}

/* Le bandeau de severite du panneau : on repere une sonde en echec sans
   lire une seule ligne. */
td:first-child{box-shadow:inset 3px 0 0 var(--line2);padding-left:17px}
tr.ko td:first-child{box-shadow:inset 3px 0 0 var(--err)}
tr.ok td:first-child{box-shadow:inset 3px 0 0 var(--ok)}

/* La pastille d'etat, comme dans le panneau : fond teinte, coins pleins,
   point devant. */
.st{white-space:nowrap;width:1%}
.st span,.pastille{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;
 border-radius:999px;font-size:var(--t-xs,11px);font-weight:700}
.st span::before,.pastille::before{content:"";width:6px;height:6px;border-radius:50%;
 background:currentColor}
.st.ok span,.pastille.ok{color:var(--ok);background:var(--ok-bg)}
.st.ko span,.pastille.ko{color:var(--err);background:var(--err-bg)}

.nom{font-weight:700;white-space:nowrap;letter-spacing:-.012em}
.det{color:var(--dim);font-size:var(--t-s,12px);
 font-family:var(--mono,ui-monospace,Menlo,monospace);word-break:break-word}
.src{font-weight:700}
code{font-family:var(--mono,ui-monospace,Menlo,monospace);background:var(--surface2);
 border:1px solid var(--line);border-radius:var(--r-xs,4px);padding:1px 5px;
 font-size:var(--t-s,12px)}
.note{color:var(--dim);font-size:var(--t-s,12px);margin-top:12px;line-height:1.6}
.err{background:var(--err-bg);border:1px solid var(--err-border);color:var(--err-txt);
 /* Pas de couleur de repli : une valeur ecrite ici est une valeur qui
    divergera le jour ou le panneau changera la sienne. Les replis de
    TAILLE, eux, restent -- ils ne peuvent pas mentir sur un etat. */
 padding:12px 14px;border-radius:var(--r,8px);
 font-family:var(--mono,ui-monospace,Menlo,monospace);font-size:var(--t-s,12px);
 white-space:pre-wrap}

.btn{display:inline-flex;align-items:center;gap:7px;padding:8px 14px;
 border-radius:var(--r-s,6px);border:1px solid transparent;cursor:pointer;
 font:inherit;font-size:var(--t-m,13px);font-weight:600}
.btn-primaire{background:var(--accent);color:var(--sur-accent)}
.btn-primaire:hover{filter:brightness(1.07)}
.btn-simple{background:var(--surface);color:var(--txt);border-color:var(--line2)}
.btn-simple:hover{border-color:var(--accent);color:var(--accent)}
.btn-sm{padding:5px 10px;font-size:var(--t-s,12px)}
.btn:disabled{opacity:.55;cursor:default;filter:none}
.case{display:flex;align-items:center;gap:7px;font-size:var(--t-m,13px);
 color:var(--txt);cursor:pointer}
.case input{width:auto;margin:0;accent-color:var(--accent)}

/* ---------------- la verification approfondie, en fenetre ----------------
   Elle ne quitte plus la page : on la lance depuis la rubrique qu'on vient
   de lire, et on revient a elle en fermant. La fenetre annonce ses lignes
   AVANT de les jouer, et les remplit une par une -- une fenetre qui reste
   blanche trois minutes ne dit ni ou elle en est, ni si elle avance. */
.voile{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;
 align-items:center;justify-content:center;padding:20px;z-index:50}
.voile.ouvert{display:flex}
.modale{background:var(--bg);border:1px solid var(--line);border-radius:var(--r,8px);
 box-shadow:0 12px 40px rgba(0,0,0,.28);width:min(820px,100%);
 max-height:min(86vh,900px);display:flex;flex-direction:column}
.mod-tete{display:flex;align-items:center;gap:14px;padding:16px 18px;
 border-bottom:1px solid var(--line)}
.mod-tete b{font-size:var(--t-b,14px);font-weight:700;display:block}
.mod-tete span{font-size:var(--t-s,12px);color:var(--dim);display:block;margin-top:2px}
.mod-tete .spacer{flex:1}
.fermer{background:none;border:0;color:var(--dim);cursor:pointer;font-size:20px;
 line-height:1;padding:4px 8px;border-radius:var(--r-s,6px)}
.fermer:hover{background:var(--surface2);color:var(--txt)}
.mod-corps{padding:16px 18px;overflow:auto}
.mod-pied{padding:14px 18px;border-top:1px solid var(--line);display:flex;
 align-items:center;gap:10px;flex-wrap:wrap}
.mod-pied .spacer{flex:1}

/* LA ROUE. Un cercle SVG dont on fait varier le trait : il avance quand un
   test FINIT, donc il ne peut pas mentir. Le compte au centre dit la meme
   chose en chiffres -- une roue seule ne dit pas combien il en reste. */
.roue-zone{display:flex;align-items:center;gap:16px;margin-bottom:16px}
.roue{width:76px;height:76px;flex:none;transform:rotate(-90deg)}
.roue circle{fill:none;stroke-width:7}
.roue .fond{stroke:var(--surface2)}
.roue .part{stroke:var(--accent);stroke-linecap:round;
 transition:stroke-dashoffset .3s ease}
.roue-zone .chiffres b{font-size:22px;font-weight:800;letter-spacing:-.02em;
 display:block;line-height:1.1}
.roue-zone .chiffres span{font-size:var(--t-s,12px);color:var(--dim)}
.roue-boite{position:relative;width:76px;height:76px;flex:none}
.roue-boite .dedans{position:absolute;inset:0;display:grid;place-items:center;
 font-size:var(--t-m,13px);font-weight:700;font-variant-numeric:tabular-nums}

.st.attente span{color:var(--dim2);background:var(--surface2)}
.st.encours span{color:var(--accent);background:var(--accent-soft)}
/* Le point de la ligne en cours bat : c'est le seul endroit de la page ou
   une animation apprend quelque chose. */
.st.encours span::before{animation:bat 1s ease-in-out infinite}
@keyframes bat{0%,100%{opacity:1}50%{opacity:.25}}
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
    ligne.className = d.ok ? 'ok' : 'ko';
    st.className = 'st ' + (d.ok ? 'ok' : 'ko');
    st.firstElementChild.textContent = d.ok ? 'OK' : 'ÉCHEC';
    $('d' + r).textContent = d.detail || '';
    $('ms' + r).textContent = (d.ms != null) ? d.ms + ' ms' : '';
    if(d.ok) reussis++;
    roue(r + 1, liste.length);
  }

  const n = liste.length, v = $('mod-verdict');
  v.className = 'verdict ' + (reussis === n ? 'ok' : 'ko');
  v.textContent = reussis === n
    ? reussis + ' test' + (reussis > 1 ? 's' : '') + ' sur ' + n + ' : tout passe.'
    : (n - reussis) + ' test' + (n - reussis > 1 ? 's' : '') + ' en échec sur '
      + n + ' — le détail est dans la colonne de droite.';
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
        <div class="roue-boite">
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


def page(titre, corps, script_final=""):
    """Le gabarit commun : theme du panneau, meme police, meme fond.

    Un seul gabarit pour toute l'application -- deux divergent au premier
    changement, et le diagnostic se mettrait a ressembler a deux applications
    differentes selon la page ouverte.
    """
    return ("<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\">"
            + THEME + SUIVRE_LE_THEME
            + "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            + f"<title>{esc(titre)}</title><style>{CSS}</style></head><body>"
            + f"<div class=\"wrap\">{corps}</div>{MODALE}"
            + f"<script>{script_final}</script>"
            + f"<script>{SCRIPT}</script></body></html>")


@app.get("/health")
def health():
    return {"ok": True}


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
    ok, nom, detail = checks.run_test(indice, avec_suite)
    return {"ok": bool(ok), "nom": nom, "detail": detail,
            "ms": int((time.time() - debut) * 1000)}


def _lignes_sondes(resultats):
    return "".join(
        f'<tr class="{"ok" if ok else "ko"}">'
        f'<td class="st {"ok" if ok else "ko"}">'
        f'<span>{"OK" if ok else "ÉCHEC"}</span></td>'
        f'<td class="nom">{esc(nom)}</td><td class="det">{esc(det)}</td></tr>'
        for ok, nom, det in resultats)


def _rubrique(cle, titre, description, resultats, extra=""):
    """Une rubrique : son etat en pastille, ses sondes, son propre bouton."""
    total = len(resultats)
    passees = sum(1 for ok, _, _ in resultats if ok)
    saine = passees == total
    pastille = (f'<span class="pastille {"ok" if saine else "ko"}">'
                f'{passees} sur {total}</span>')
    bouton = (f'<button class="btn btn-simple btn-sm" data-verifier="{esc(cle)}">'
              f'Vérifier cette rubrique</button>')
    return (f'<section class="rubrique" id="rub-{esc(cle)}">'
            f'<div class="rub-tete"><div class="rub-titre"><b>{esc(titre)}</b>'
            f'<span>{esc(description)}</span></div><div class="spacer"></div>'
            f'{pastille}{bouton}</div>'
            f'<table><tbody>{_lignes_sondes(resultats)}</tbody></table>'
            f'{extra}</section>')


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

    sondes_ok = all(ok for ok, _, _ in resultats)
    dagster_a_ecrit = "dagster" in {s for s, _, _ in par_source}
    complet = sondes_ok and erreur_db is None and dagster_a_ecrit

    if complet:
        verdict = ('<div class="verdict ok">Chaîne complète vérifiée &mdash; les cinq services '
                   'communiquent, et Postgres contient des écritures de l\'application '
                   '<em>et</em> de Dagster.</div>')
    elif sondes_ok and erreur_db is None:
        verdict = ('<div class="verdict ko">Toutes les sondes passent, mais aucune ligne écrite '
                   'par Dagster &mdash; c\'est le seul maillon encore non vérifié.</div>')
    else:
        verdict = ('<div class="verdict ko">Au moins une vérification échoue &mdash; la rubrique '
                   'en cause porte sa pastille rouge ci-dessous.</div>')

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
        f'<div class="mod-corps" style="border-top:1px solid var(--line)">'
        f'<h2 style="margin-top:0">Table {esc(checks.TABLE)} &mdash; qui a écrit</h2>'
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

    quand = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    corps = (
        '<div class="tete"><div><h1>État de santé de CodeLab</h1>'
        f'<div class="sub">Exécuté dans <code>codelab-app-manager</code> &mdash; {esc(quand)}'
        + (f' &mdash; {esc(ecriture)}' if ecriture else '')
        + '</div></div><div class="spacer"></div>'
        '<button class="btn btn-primaire" data-verifier="">'
        'Vérification approfondie</button></div>'
        + verdict
        + '<h2>Rubriques</h2>'
        + rubriques
        + '<div class="note">Ces sondes ne font que <b>regarder</b>, et tournent à chaque '
          'affichage. La vérification approfondie, elle, <b>agit</b> : elle écrit en base, '
          'traverse le proxy et vérifie que le journal enregistre. Lance-la sur tout, ou sur '
          'la seule rubrique qui cloche.</div>')

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
    return page("CodeLab · état de santé", corps, donnees)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
