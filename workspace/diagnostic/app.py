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
# 9002), qui a son propre stockage : le choix n'y est pas lisible, et la page
# suit alors le reglage du systeme. C'est le comportement correct, pas un
# defaut -- la separation des origines est ce qui empeche une application de
# lire ce que le panneau a en memoire.
#
# Le script reste utile : servi a travers le panneau lui-meme, il retrouve le
# choix et l'applique avant le premier rendu, sans scintillement.
SUIVRE_LE_THEME = """<script>
try{var t=localStorage.getItem('codelab-theme');
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
 font-feature-settings:"tnum";letter-spacing:-.006em}
.wrap{max-width:960px;margin:0 auto}

h1{font-size:var(--t-xl,22px);font-weight:800;letter-spacing:-.03em;line-height:1.2}
.sub{color:var(--dim);font-size:var(--t-m,13px);margin:4px 0 20px;max-width:76ch}

/* Le verdict est la premiere chose lue : c'est la tuile de tete du cockpit. */
.verdict{display:flex;align-items:baseline;gap:10px;padding:13px 16px;
 border-radius:var(--r,8px);font-weight:700;margin-bottom:18px;
 border:1px solid var(--line);background:var(--surface);
 box-shadow:0 1px 2px var(--shadow);line-height:1.45}
.verdict::before{content:"";width:8px;height:8px;border-radius:50%;flex:none;
 align-self:center}
.verdict.ok::before{background:var(--ok)}
.verdict.warn::before{background:var(--warn)}
.verdict.ko::before{background:var(--err)}
.verdict.ok{color:var(--ok)}
.verdict.warn{color:var(--warn)}
.verdict.ko{color:var(--err)}

h2{font-size:10px;font-weight:650;letter-spacing:.08em;text-transform:uppercase;
 color:var(--dim2);margin:24px 0 9px}

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
/* Trois rangs, trois bandeaux : l'oeil trie la page avant de la lire, et il
   ne doit surtout pas lire "disque a 87 pour cent" du meme rouge que
   "Postgres ne repond pas". */
tr.warn td:first-child{box-shadow:inset 3px 0 0 var(--warn)}
tr.neutre td:first-child{box-shadow:inset 3px 0 0 var(--line2)}
tr.ok td:first-child{box-shadow:inset 3px 0 0 var(--ok)}

/* La pastille d'etat, comme dans le panneau : fond teinte, coins pleins,
   point devant. */
.st{white-space:nowrap;width:1%}
.st span{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;
 border-radius:999px;font-size:var(--t-xs,11px);font-weight:700}
.st span::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.st.ok span{color:var(--ok);background:var(--ok-bg)}
.st.warn span{color:var(--warn);background:var(--warn-bg)}
.st.neutre span{color:var(--dim2);background:var(--surface2)}
.st.ko span{color:var(--err);background:var(--err-bg)}

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

/* ---------------- la verification approfondie, en direct ----------------
   La page annonce ses lignes AVANT de les jouer, et les remplit une par
   une. Ce qui manquait : une page qui reste blanche trois minutes ne dit ni
   ou elle en est, ni si elle avance encore. */
.barre-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
 margin-bottom:16px}
.btn{display:inline-flex;align-items:center;gap:7px;padding:8px 14px;
 border-radius:var(--r-s,6px);border:1px solid transparent;cursor:pointer;
 font:inherit;font-size:var(--t-m,13px);font-weight:600}
.btn-primaire{background:var(--accent);color:var(--sur-accent)}
.btn-primaire:hover{filter:brightness(1.07)}
.btn-simple{background:var(--surface);color:var(--txt);border-color:var(--line2)}
.btn-simple:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.55;cursor:default;filter:none}
.case{display:flex;align-items:center;gap:7px;font-size:var(--t-m,13px);
 color:var(--txt);cursor:pointer}
.case input{width:auto;margin:0}
/* La barre d'avancement : deux divs, aucune animation qui tourne dans le
   vide -- elle avance quand un test finit, donc elle ne ment jamais. */
.avance{height:4px;border-radius:999px;background:var(--surface2);
 overflow:hidden;margin-bottom:16px}
.avance span{display:block;height:100%;width:0;background:var(--accent);
 transition:width .25s ease}
.st.attente span{color:var(--dim2);background:var(--surface2)}
.st.encours span{color:var(--accent);background:var(--accent-soft)}
/* Le point de la ligne en cours bat : c'est le seul endroit de la page ou
   une animation apprend quelque chose. */
.st.encours span::before{animation:bat 1s ease-in-out infinite}
@keyframes bat{0%,100%{opacity:1}50%{opacity:.25}}
tr.encours td:first-child{box-shadow:inset 3px 0 0 var(--accent)}
.duree{color:var(--dim2);font-size:var(--t-xs,11px);white-space:nowrap}
"""


def esc(v):
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/tests")
def tests():
    """La verification approfondie, a la demande -- et EN DIRECT.

    Separee de la page d'accueil et pas jouee automatiquement -- ni par le
    planning Dagster, qui ne prend que les sondes de l'asset : ces tests
    AGISSENT franchement. Ils ecrivent en base, traversent le proxy, lancent
    la suite de regressions du panneau, et laissent des traces dans les
    journaux. Les jouer quatre fois par heure remplirait ces journaux de
    traces que personne n'a demandees.

    CE QUI A CHANGE, ET POURQUOI. Cette page jouait tout avant de repondre :
    on cliquait, et le navigateur restait blanc jusqu'a trois minutes -- le
    temps de la suite de regressions -- sans dire ou il en etait, ni meme
    s'il avancait encore. On rechargeait, ce qui relancait tout.

    Elle annonce maintenant ses lignes AVANT de les jouer, et les remplit une
    par une : chaque test est demande separement, et la page montre celui qui
    tourne. La barre d'avancement ne bouge que quand un test se termine --
    elle ne peut donc pas mentir.
    """
    noms = checks.noms_des_tests()
    lignes = "".join(
        f'<tr id="t{i}"><td class="st attente"><span>en attente</span></td>'
        f'<td class="nom">{esc(nom)}</td>'
        f'<td class="det" id="d{i}">—</td>'
        f'<td class="duree" id="ms{i}"></td></tr>'
        for i, nom in enumerate(noms))
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">{THEME}{SUIVRE_LE_THEME}
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CodeLab &middot; vérification approfondie</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>Vérification approfondie</h1>
<div class="sub">Ces tests <b>agissent</b> : ils écrivent en base, traversent le reverse proxy
et laissent une trace dans les journaux. Ils ne modifient rien d'autre &mdash; aucune
application, aucun compte, aucun réglage.</div>

<div class="barre-actions">
  <button class="btn btn-primaire" id="lancer">Lancer la vérification</button>
  <label class="case"><input type="checkbox" id="suite" checked>
    Inclure la suite de régressions du panneau <span class="duree">(la plus longue)</span></label>
</div>
<div class="avance"><span id="jauge"></span></div>
<div class="verdict" id="verdict" style="display:none"></div>

<div class="tbl-wrap">
<table><tr><th>État</th><th>Test</th><th>Détail</th><th>Durée</th></tr>{lignes}</table>
</div>
<div class="note">Ce ne sont pas les tests du panneau : ceux-là vérifient du <b>code</b> avant
qu'il ne parte en image, dans des dossiers temporaires. Ceux-ci vérifient une
<b>installation</b> qui tourne. <a href="./">Retour au diagnostic</a></div>

<script>
/* LE NOM DE LA FONCTION NE PEUT PAS ETRE CELUI DU BOUTON. Un element qui
   porte un id devient une propriete globale du meme nom : une fonction
   « lancer » etait purement et simplement masquee par le bouton
   id="lancer", et le clic ne faisait rien -- sans la moindre erreur dans la
   console, puisque l'ecouteur recevait bien un objet. */
const total = {len(noms)};
const $ = i => document.getElementById(i);

function etat(i, classe, texte){{
  const tr = $('t' + i);
  /* La ligne porte la meme classe que sa pastille : ok, warn, neutre, ko.
     Une seule source de verite pour la couleur du bandeau et celle du
     texte -- sans quoi les deux divergent au premier rang ajoute. */
  tr.className = classe;
  const st = tr.querySelector('.st');
  st.className = 'st ' + classe;
  st.firstElementChild.textContent = texte;
}}

async function lancerVerification(){{
  const bouton = $('lancer'), avecSuite = $('suite').checked;
  bouton.disabled = true; bouton.textContent = 'Vérification en cours...';
  $('verdict').style.display = 'none';
  const nb = avecSuite ? total : total - 1;
  /* Les lignes non jouees repartent a zero : relancer sans la suite ne doit
     pas laisser le resultat precedent de la suite affiche comme s'il venait
     d'etre obtenu. */
  for(let i = 0; i < total; i++){{
    etat(i, 'attente', i < nb ? 'en attente' : 'non jouée');
    $('d' + i).textContent = '—'; $('ms' + i).textContent = '';
  }}
  $('jauge').style.width = '0%';

  let reussis = 0;
  for(let i = 0; i < nb; i++){{
    etat(i, 'encours', 'en cours');
    let d;
    try{{
      d = await (await fetch('api/test/' + i + (avecSuite ? '' : '?suite=0'))).json();
    }}catch(e){{
      d = {{ok: false, detail: "La page n'a pas pu joindre le diagnostic : " + e}};
    }}
    etat(i, d.classe || (d.ok ? 'ok' : 'ko'), d.etat || (d.ok ? 'OK' : 'ÉCHEC'));
    $('d' + i).textContent = d.detail || '';
    $('ms' + i).textContent = (d.ms != null) ? d.ms + ' ms' : '';
    if(d.ok) reussis++;
    $('jauge').style.width = Math.round((i + 1) / nb * 100) + '%';
  }}

  const v = $('verdict');
  v.className = 'verdict ' + (reussis === nb ? 'ok' : 'ko');
  v.textContent = reussis === nb
    ? reussis + ' test' + (reussis > 1 ? 's' : '') + ' sur ' + nb + ' : tout passe.'
    : (nb - reussis) + ' test' + (nb - reussis > 1 ? 's' : '') + ' en échec sur '
      + nb + ' — le détail est dans la colonne de droite.';
  v.style.display = 'flex';
  bouton.disabled = false; bouton.textContent = 'Relancer la vérification';
}}
$('lancer').addEventListener('click', lancerVerification);
</script>
</div></body></html>"""


@app.get("/api/test/<int:indice>")
def api_test(indice):
    """Un test, et un seul. C'est ce qui permet a la page d'avancer.

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
    # "ok" reste, et reste un booleen : c'est le contrat que la page lit pour
    # compter les reussites. Le rang s'ajoute a cote, pour la pastille --
    # un test qui ne s'applique pas ici n'est ni vert ni rouge.
    return {"ok": bool(etat), "etat": etat.libelle, "classe": etat.classe,
            "nom": nom, "detail": detail,
            "ms": int((time.time() - debut) * 1000)}


@app.get("/")
def index():
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

    lignes = "".join(
        f'<tr class="{etat.classe}">'
        f'<td class="st {etat.classe}"><span>{etat.libelle}</span></td>'
        f'<td class="nom">{esc(nom)}</td><td class="det">{esc(det)}</td></tr>'
        for etat, nom, det in resultats)

    # Le verdict suit la HIERARCHIE, il ne compte pas des booleens. Une page
    # entierement rouge des qu'une seule ligne n'est pas verte n'apprend
    # rien : ce qu'on veut savoir en arrivant, c'est s'il faut agir
    # maintenant, plus tard, ou pas du tout.
    rangs = checks.noms_par_rang(resultats)
    critiques = rangs[checks.Etat.ECHEC]
    alertes = rangs[checks.Etat.ALERTE]
    dagster_a_ecrit = "dagster" in {s for s, _, _ in par_source}

    def _verdict(classe, texte):
        return f'<div class="verdict {classe}">{texte}</div>'

    if critiques or erreur_db:
        combien = len(critiques) + (1 if erreur_db else 0)
        verdict = _verdict("ko", f'{combien} vérification{"s" if combien > 1 else ""} '
                                 f'en échec &mdash; à traiter maintenant, le détail est dans '
                                 f'la colonne de droite.')
    elif alertes:
        verdict = _verdict("warn", f'Rien de critique. {len(alertes)} '
                                   f'alerte{"s" if len(alertes) > 1 else ""} '
                                   f'&mdash; <b>{esc(", ".join(alertes))}</b> : à regarder quand '
                                   f'tu passes, la stack rend son service.')
    elif not dagster_a_ecrit:
        verdict = _verdict("warn", 'Toutes les sondes passent, mais aucune ligne écrite '
                                   'par Dagster &mdash; c\'est le seul maillon encore non '
                                   'vérifié.')
    else:
        verdict = _verdict("ok", 'Chaîne complète vérifiée &mdash; les cinq services '
                                 'communiquent, et Postgres contient des écritures de '
                                 'l\'application <em>et</em> de Dagster.')

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
                        '<code>codelab-dagster</code> ci-dessus le dit, et son interface est sur '
                        'le port 3000.</div>')
    else:
        bloc_db = ('<div class="note">Table vide : elle vient d\'être créée. Recharge la page &mdash; '
                   'cette visite y écrit déjà sa ligne, et Dagster ajoutera la sienne au prochain '
                   'passage du planning.</div>')

    recent = ("".join(
        f'<tr><td class="det">#{i}</td><td class="src">{esc(s)}</td>'
        f'<td class="det">{esc(d or "")}</td><td class="det">{esc(t)}</td></tr>'
        for i, s, d, t in recentes)
        or '<tr><td colspan="4" class="det">aucune ligne</td></tr>')

    quand = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">{THEME}{SUIVRE_LE_THEME}
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CodeLab &middot; diagnostic</title><style>{CSS}</style></head><body><div class="wrap">
<h1>Diagnostic CodeLab</h1>
<div class="sub">Exécuté dans <code>codelab-app-manager</code> &mdash; {esc(quand)}
{f' &mdash; {esc(ecriture)}' if ecriture else ''}</div>
{verdict}
<h2>Vérifications</h2>
<div class="tbl-wrap"><table><tr><th>État</th><th>Cible</th><th>Détail</th></tr>{lignes}</table></div>
<div class="note">Ces sondes ne font que <b>regarder</b>, et tournent à chaque affichage.
Pour aller plus loin &mdash; écrire en base, traverser le proxy, vérifier que le journal
enregistre &mdash; ouvre la <a href="tests">vérification approfondie</a> : elle joue ses
tests un par un, sous tes yeux.</div>
<h2>Table {checks.TABLE} &mdash; qui a écrit</h2>
{bloc_db}
<h2>Dernières écritures</h2>
<div class="tbl-wrap"><table><tr><th>Id</th><th>Source</th><th>Détail</th><th>Date</th></tr>{recent}</table></div>
<div class="note">Cette page écrit une ligne <code>app-manager</code> à chaque rechargement.
Les lignes <code>dagster</code> viennent de l'asset <code>diagnostic_codelab</code> de
<code>/workspace/definitions.py</code>.</div>
</div></body></html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
