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
import traceback
from datetime import datetime, timezone

# psycopg n'est pas dans l'image app-manager : la commande de build l'installe
# dans ./vendor, a cote du code, sans toucher au conteneur.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

from flask import Flask  # noqa: E402  (present dans l'image app-manager)

import checks  # noqa: E402

app = Flask(__name__)
SOURCE = "app-manager"

CSS = """
:root{--bg:#f6f7f9;--surface:#fff;--surface2:#f0f1f3;--line:#e2e4e8;--txt:#1c2129;--dim:#5b6472;
 --dim2:#8891a0;--ok:#1a7f37;--ok-bg:rgba(26,127,55,.1);--ok-bd:rgba(26,127,55,.3);
 --err:#cf222e;--err-bg:rgba(207,34,46,.08);--err-bd:rgba(207,34,46,.28);--accent:#316dca}
@media(prefers-color-scheme:dark){:root{--bg:#0d1117;--surface:#161b22;--surface2:#1c2129;
 --line:#262c36;--txt:#e6edf3;--dim:#8b949e;--dim2:#6e7681;--ok:#3fb950;--ok-bg:rgba(63,185,80,.12);
 --ok-bd:rgba(63,185,80,.35);--err:#f85149;--err-bg:rgba(248,81,73,.12);--err-bd:rgba(248,81,73,.35);
 --accent:#4c8eff}}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.55 -apple-system,BlinkMacSystemFont,
 "Segoe UI",Roboto,sans-serif;padding:34px 20px;-webkit-font-smoothing:antialiased}
.wrap{max-width:880px;margin:0 auto}
h1{font-size:19px;font-weight:650;margin-bottom:4px}
.sub{color:var(--dim);font-size:13px;margin-bottom:24px}
.verdict{padding:14px 17px;border-radius:12px;font-weight:600;margin-bottom:24px;
 border:1px solid transparent;line-height:1.5}
.verdict.ok{background:var(--ok-bg);color:var(--ok);border-color:var(--ok-bd)}
.verdict.ko{background:var(--err-bg);color:var(--err);border-color:var(--err-bd)}
h2{font-size:10.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--dim2);
 margin:28px 0 10px}
table{width:100%;border-collapse:collapse;background:var(--surface);border:1px solid var(--line);
 border-radius:12px;overflow:hidden}
th{font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--dim2);
 text-align:left;padding:10px 14px;background:var(--surface2);border-bottom:1px solid var(--line)}
td{padding:11px 14px;border-top:1px solid var(--line);vertical-align:top}
tr:first-child td{border-top:none}
.st{font-weight:700;white-space:nowrap;width:1%}
.st.ok{color:var(--ok)} .st.ko{color:var(--err)}
.nom{font-weight:600;white-space:nowrap}
.det{color:var(--dim);font-size:12.5px;font-family:ui-monospace,Menlo,monospace;word-break:break-word}
.src{font-weight:600}
code{font-family:ui-monospace,Menlo,monospace;background:var(--bg);border:1px solid var(--line);
 border-radius:5px;padding:1px 5px;font-size:12px}
.note{color:var(--dim);font-size:12.5px;margin-top:12px;line-height:1.6}
.err{background:var(--err-bg);border:1px solid var(--err-bd);color:var(--err);padding:12px 14px;
 border-radius:10px;font-family:ui-monospace,Menlo,monospace;font-size:12px;white-space:pre-wrap}
"""


def esc(v):
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@app.get("/health")
def health():
    return {"ok": True}


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
        f'<tr><td class="st {"ok" if ok else "ko"}">{"OK" if ok else "ECHEC"}</td>'
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
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CodeLab &middot; diagnostic</title><style>{CSS}</style></head><body><div class="wrap">
<h1>Diagnostic CodeLab</h1>
<div class="sub">Execute dans <code>codelab-app-manager</code> &mdash; {esc(quand)}
{f' &mdash; {esc(ecriture)}' if ecriture else ''}</div>
{verdict}
<h2>Verifications</h2>
<table><tr><th>Etat</th><th>Cible</th><th>Detail</th></tr>{lignes}</table>
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
