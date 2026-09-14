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
.verdict.ko::before{background:var(--err)}
.verdict.ok{color:var(--ok)}
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
tr.ok td:first-child{box-shadow:inset 3px 0 0 var(--ok)}

/* La pastille d'etat, comme dans le panneau : fond teinte, coins pleins,
   point devant. */
.st{white-space:nowrap;width:1%}
.st span{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;
 border-radius:999px;font-size:var(--t-xs,11px);font-weight:700}
.st span::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.st.ok span{color:var(--ok);background:var(--ok-bg)}
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
"""


def esc(v):
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/tests")
def tests():
    """La verification approfondie, a la demande.

    Separee de la page d'accueil et pas jouee automatiquement -- ni par le
    planning Dagster, qui ne prend que les sondes de l'asset : ces tests
    AGISSENT franchement. Ils ecrivent en base, traversent le proxy, lancent
    la suite de regressions du panneau, et laissent des traces dans les
    journaux. Les jouer quatre fois par heure remplirait ces journaux de
    traces que personne n'a demandees.

    Les sondes de l'accueil, elles, se contentent de LIRE l'etat des
    services -- a une exception pres, et elle est volontaire : l'accueil
    ecrit son propre battement de coeur, parce que c'est precisement ce
    qu'il verifie (la base repond, et l'ecriture aboutit). C'est une ligne,
    pas un effet de bord sur le reste de la stack.
    """
    debut = time.time()
    resultats = checks.run_tests()
    # La suite de regressions du panneau, en dernier : c'est la plus longue,
    # et on veut voir les tests d'installation d'abord.
    if request.args.get("suite") != "0":
        resultats.append(checks.lancer_suite_du_panneau())
    duree = int((time.time() - debut) * 1000)
    lignes = "".join(
        f'<tr class="{"ok" if ok else "ko"}">'
        f'<td class="st {"ok" if ok else "ko"}">'
        f'<span>{"OK" if ok else "ECHEC"}</span></td>'
        f'<td class="nom">{esc(nom)}</td><td class="det">{esc(det)}</td></tr>'
        for ok, nom, det in resultats)
    reussis = sum(1 for ok, _, _ in resultats if ok)
    tous = len(resultats)
    verdict = (f'<div class="verdict {"ok" if reussis == tous else "ko"}">'
               f'{reussis} test{"s" if reussis > 1 else ""} sur {tous} '
               f'{"passent" if reussis == tous else "passent"} &mdash; {duree} ms.'
               + ('' if reussis == tous else
                  ' Le detail est dans la colonne de droite.') + '</div>')
    quand = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">{THEME}{SUIVRE_LE_THEME}
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CodeLab &middot; verification approfondie</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>Verification approfondie</h1>
<div class="sub">Ces tests AGISSENT : ils ecrivent en base, traversent le reverse proxy et
laissent une trace dans les journaux. Ils ne modifient rien d'autre &mdash; aucune application,
aucun compte, aucun reglage. &mdash; {esc(quand)}</div>
{verdict}
<table><tr><th>Etat</th><th>Test</th><th>Detail</th></tr>{lignes}</table>
<div class="note">Ce ne sont pas les tests du panneau : ceux-la verifient du CODE avant qu'il
ne parte en image, dans des dossiers temporaires. Ceux-ci verifient une INSTALLATION qui
tourne. <a href="./">Retour au diagnostic</a></div>
</div></body></html>"""


@app.get("/")
def index():
    resultats = checks.run_all()

    # Battement de coeur : cette page ecrit sa propre ligne a chaque visite,
    # l'asset Dagster ecrit les siennes. Les deux sources doivent apparaitre.
    ecriture, par_source, recentes, erreur_db = None, [], [], None
    try:
        conn = checks.connect_pg()
        try:
            ecriture = f"ligne #{checks.write_heartbeat(conn, SOURCE, 'visite de la page')} inseree"
            par_source, recentes = checks.read_heartbeats(conn)
        finally:
            conn.close()
    except checks.PiloteAbsent as e:
        erreur_db = str(e)
    except Exception:
        erreur_db = traceback.format_exc(limit=3)

    lignes = "".join(
        f'<tr class="{"ok" if ok else "ko"}">'
        f'<td class="st {"ok" if ok else "ko"}">'
        f'<span>{"OK" if ok else "ECHEC"}</span></td>'
        f'<td class="nom">{esc(nom)}</td><td class="det">{esc(det)}</td></tr>'
        for ok, nom, det in resultats)

    sondes_ok = all(ok for ok, _, _ in resultats)
    dagster_a_ecrit = "dagster" in {s for s, _, _ in par_source}
    complet = sondes_ok and erreur_db is None and dagster_a_ecrit

    if complet:
        verdict = ('<div class="verdict ok">Chaine complete verifiee &mdash; les cinq services '
                   'communiquent, et Postgres contient des ecritures de l\'application '
                   '<em>et</em> de Dagster.</div>')
    elif sondes_ok and erreur_db is None:
        verdict = ('<div class="verdict ko">Toutes les sondes passent, mais aucune ligne ecrite par '
                   'Dagster. Materialise l\'asset <code>diagnostic_codelab</code> depuis '
                   'http://&lt;IP&gt;:3000, puis recharge cette page.</div>')
    else:
        verdict = ('<div class="verdict ko">Au moins une verification echoue &mdash; le detail est '
                   'dans la colonne de droite.</div>')

    if erreur_db:
        bloc_db = f'<div class="err">{esc(erreur_db)}</div>'
    elif par_source:
        bloc_db = ('<table><tr><th>Source</th><th>Lignes</th><th>Derniere ecriture</th></tr>'
                   + "".join(f'<tr><td class="src">{esc(s)}</td><td>{n}</td>'
                             f'<td class="det">{esc(d)}</td></tr>' for s, n, d in par_source)
                   + '</table>')
        if not dagster_a_ecrit:
            bloc_db += ('<div class="note">Aucune ligne <code>dagster</code> : c\'est le seul '
                        'maillon encore non verifie.</div>')
    else:
        bloc_db = ('<div class="note">Table vide. Elle vient d\'etre creee ; recharge la page, '
                   'puis materialise l\'asset Dagster.</div>')

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
<div class="sub">Execute dans <code>codelab-app-manager</code> &mdash; {esc(quand)}
{f' &mdash; {esc(ecriture)}' if ecriture else ''}</div>
{verdict}
<h2>Verifications</h2>
<table><tr><th>Etat</th><th>Cible</th><th>Detail</th></tr>{lignes}</table>
<div class="note">Ces sondes ne font que regarder, et tournent a chaque affichage.
Pour aller plus loin &mdash; ecrire en base, traverser le proxy, verifier que le journal
enregistre &mdash; lance la <a href="tests">verification approfondie</a>.</div>
<h2>Table {checks.TABLE} &mdash; qui a ecrit</h2>
{bloc_db}
<h2>Dernieres ecritures</h2>
<table><tr><th>Id</th><th>Source</th><th>Detail</th><th>Date</th></tr>{recent}</table>
<div class="note">Cette page ecrit une ligne <code>app-manager</code> a chaque rechargement.
Les lignes <code>dagster</code> viennent de l'asset <code>diagnostic_codelab</code> de
<code>/workspace/definitions.py</code>.</div>
</div></body></html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True)
