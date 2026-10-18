/* CodeLab -- panneau/04-apercu.js : La vue d'ensemble, le chargement des donnees et les actions sur une application.
   Une partie du script du panneau, assemblee par app.py (PANNEAU_MODULES)
   dans l'ordre des numeros : un seul script, une seule portee. */
/* ---------------- vue d'ensemble ---------------- */
function barChart(list, key, unit, maxHint){
  /* Les services externes sont ecartes : le panneau ne les lance pas, donc
     il n'a pas leur pid et ne mesure RIEN. Une barre a 0.0 % a cote des
     applications qu'il mesure vraiment se lit comme « ce service ne
     consomme rien », ce qui est faux -- on ne sait pas. */
  const items=list.filter(a=>a.running && !a.externe);
  if(!items.length) return '<div class="chart-empty">Aucune application en ligne pour le moment.</div>';
  const max=Math.max(maxHint, ...items.map(a=>a[key]||0));
  return items.map(a=>{
    const v=a[key]||0, pct=max?Math.min(100, v/max*100):0;
    return `<div class="bar-row"><div class="bar-name">${esc(nomAffiche(a))}</div>
      <div class="bar-track"><span style="width:${pct}%"></span></div>
      <div class="bar-val">${v.toFixed(1)}${unit}</div></div>`;
  }).join('');
}

function renderOverview(){
  const total=apps.length, running=apps.filter(a=>a.running).length;
  const failed=apps.filter(a=>a.failed).length;
  const stopped=total-running-failed;
  $('ov-total').textContent=total;
  $('ov-run').textContent=running;
  $('ov-off').textContent=stopped<0?0:stopped;

  const pct=n=>total?(n/total*100):0;
  $('hb-on').style.width=pct(running)+'%';
  $('hb-off').style.width=pct(stopped<0?0:stopped)+'%';
  $('hb-err').style.width=pct(failed)+'%';

  const cpuSum=apps.reduce((s,a)=>s+(a.cpu_percent||0),0);
  const memSum=apps.reduce((s,a)=>s+(a.memory_mb||0),0);
  $('res-cpu-val').textContent=cpuSum.toFixed(1)+'%';
  $('res-cpu-bar').style.width=Math.min(100,cpuSum)+'%';
  $('res-mem-val').textContent=Math.round(memSum)+' Mo';
  $('res-mem-bar').style.width=Math.min(100, memSum/2048*100)+'%';

  remplir($('ov-chart-cpu'), barChart(apps, 'cpu_percent', '%', 10));
  remplir($('ov-chart-mem'), barChart(apps, 'memory_mb', ' Mo', 64));
}

/* ---------------- donnees ---------------- */
let apps=[];
/* Les archivees : seule la page des applications les montre (statut
   « Archivee »). « apps » reste la liste des applications actives, celle que
   lisent la vue d'ensemble, les widgets et les droits des comptes. */
let appsArchivees=[];
async function refresh(){
  /* Reservee a l'administrateur : /api/apps repond 403 a un compte
     utilisateur, et l'appeler en boucle ne produirait que des refus. */
  if(!estAdmin) return;
  try{
    const r=await fetch('/api/apps');
    if(r.status===401){ location.href='/login'; return; }
    if(!r.ok) throw new Error('HTTP '+r.status);
    const d=await r.json();
    if(!Array.isArray(d.apps)) throw new Error('Réponse /api/apps invalide.');
    apps=d.apps;
    appsArchivees=Array.isArray(d.archivees) ? d.archivees : [];
    $('side-count').textContent=apps.length;
  }catch(e){
    /* Une coupure réseau ne doit pas casser la fiche ni produire une
       promesse rejetée toutes les 5 secondes. On conserve la dernière liste
       connue et le rendu courant. */
    console.warn('[app-manager] Impossible de rafraîchir /api/apps:', e);
    return;
  }
  render();
  renderOverview();
  if(ficheNom) peindreFiche();
  /* Les cases a cocher des comptes listent les applications : une application
     ajoutee ou supprimee pendant que la page Parametres est ouverte doit s'y
     refleter. Redessine seulement quand la liste des noms a change -- sinon on
     effacerait une case qu'on vient de cocher, toutes les 5 secondes. */
  const noms=apps.map(a=>a.name).join('|');
  if(noms!==dernierNoms && $('sec-users').style.display!=='none') chargerUtilisateurs();
  dernierNoms=noms;
}
let dernierNoms='';

/* ---------------- les applications : recherche, filtres, tri ----------------
   Deux affichages : des CARTES (par defaut), qui montrent l'etat, la charge
   et les gestes d'un coup d'oeil ; une LISTE dont chaque colonne se trie d'un
   clic sur son titre. La recherche, les filtres et le tri valent pour les
   deux, et sont retenus par ce navigateur. */
const apPrefs=(()=>{ try{ return JSON.parse(localStorage.getItem('codelab-apps-vue')||'{}'); }catch(e){ return {}; } })();
let apVueCourante = apPrefs.vue==='liste' ? 'liste' : 'cartes';
let apTriCle = apPrefs.tri || 'nom', apTriSens = apPrefs.sens===-1 ? -1 : 1, apEtat='';
function apRetenir(){
  try{ localStorage.setItem('codelab-apps-vue', JSON.stringify({vue:apVueCourante, tri:apTriCle, sens:apTriSens})); }catch(e){}
}
const AP_ETATS=[['','Toutes'],['on','En ligne'],['off','Arrêtées'],['souci','À surveiller'],['arch','Archivées']];
const AP_COLONNES=[['nom','Application'],['etat','État'],['visibilite','Visibilité'],['categorie','Catégorie'],
  ['port','Port'],['cpu','CPU'],['memoire','Mémoire']];
const RANG_ETAT={err:0, warn:1, on:2, off:3, arch:4};
/* Sans categorie : en fin de liste. */
const FIN_DU_TRI='\uffff';
function apValeur(a, cle){
  if(cle==='nom') return nomAffiche(a).toLowerCase();
  if(cle==='etat') return RANG_ETAT[etatApp(a)[0]] ?? 5;
  if(cle==='visibilite') return a.visibility||'';
  if(cle==='categorie') return a.categorie ? a.categorie.toLowerCase() : FIN_DU_TRI;
  if(cle==='port') return Number(a.port)||0;
  if(cle==='cpu') return a.externe ? -1 : (a.cpu_percent||0);
  if(cle==='memoire') return a.externe ? -1 : (a.memory_mb||0);
  return '';
}
function apFiltrer(){ render(); }
function apTrier(cle){
  if(apTriCle===cle) apTriSens=-apTriSens; else { apTriCle=cle; apTriSens = ['cpu','memoire'].includes(cle) ? -1 : 1; }
  apRetenir(); render();
}
function apVue(v){ apVueCourante = v==='liste' ? 'liste' : 'cartes'; apRetenir(); render(); }
function apChoisirEtat(e){ apEtat=e; render(); }
function apListe(){
  const q=($('ap-filtre').value||'').trim().toLowerCase();
  const vis=$('ap-vis').value, cat=$('ap-cat').value;
  return toutesLesApps().filter(a=>{
    const c=etatApp(a)[0];
    if(apEtat==='on' && c!=='on') return false;
    if(apEtat==='off' && c!=='off') return false;
    if(apEtat==='souci' && !['err','warn'].includes(c)) return false;
    if(apEtat==='arch' && c!=='arch') return false;
    if(vis && (a.visibility||'privee')!==vis) return false;
    if(cat && (a.categorie||'')!==(cat==='-' ? '' : cat)) return false;
    return !q || (a.name+' '+nomAffiche(a)+' '+(a.description||'')+' '+(a.categorie||'')+' '+a.port).toLowerCase().includes(q);
  }).sort((x,y)=>{
    const a=apValeur(x,apTriCle), b=apValeur(y,apTriCle);
    const r = typeof a==='number' && typeof b==='number' ? a-b : String(a).localeCompare(String(b),'fr');
    return r*apTriSens || nomAffiche(x).localeCompare(nomAffiche(y),'fr');
  });
}
function toutesLesApps(){ return apps.concat(appsArchivees); }
function apOutils(){
  const toutes=toutesLesApps();
  remplir($('ap-etats'), AP_ETATS.map(([v,t])=>{
    /* Pas de puce « Archivees » a zero : rien a filtrer. */
    if(v==='arch' && !appsArchivees.length && apEtat!=='arch') return '';
    const n = v==='' ? toutes.length : toutes.filter(a=>{ const c=etatApp(a)[0];
      return v==='souci' ? ['err','warn'].includes(c) : c===v; }).length;
    return `<button aria-pressed="${apEtat===v}" data-agir="ap-etat" data-cible="${v}">${t} <b>${n}</b></button>`;
  }).join(''));
  const cats=[...new Set(toutes.map(a=>a.categorie||''))].sort((a,b)=>a.localeCompare(b,'fr'));
  const sel=$('ap-cat'), choisi=sel.value;
  (sel.closest('.dl')||sel).style.display = cats.some(Boolean) ? '' : 'none';
  remplir(sel, '<option value="">Toutes catégories</option>'
    + cats.map(c=>`<option value="${c ? esc(c) : '-'}">${c ? esc(c) : 'Sans catégorie'}</option>`).join(''));
  sel.value=choisi;
  $('ap-tri').value=apTriCle;
  document.querySelectorAll('.ap-vues button').forEach(b=>b.classList.toggle('active', b.dataset.v===apVueCourante));
}
const SVG_PAUSE='<svg viewBox="0 0 24 24"><rect x="7" y="6" width="3.5" height="12" rx="1"/><rect x="13.5" y="6" width="3.5" height="12" rx="1"/></svg>';
const SVG_LECTURE='<svg viewBox="0 0 24 24"><path d="M8 5.5l10 6.5-10 6.5z"/></svg>';
function apBascule(a){
  return (a.externe || a.archivee || a.name === 'diagnostic') ? '' : `<button class="icon-btn ${a.running?'stop':'ok'}" title="${a.running?'Arrêter':'Démarrer'}"
    aria-label="${a.running?'Arrêter':'Démarrer'} ${esc(nomAffiche(a))}" data-agir="app-basculer" data-cible="${esc(a.name)}">${a.running?SVG_PAUSE:SVG_LECTURE}</button>`;
}
/* Les gestes de gestion, sur la carte et la ligne : tout se gere depuis la
   page des applications (vecu : « Je ne vois pas de bouton pour archiver. Je
   veux tout pouvoir gerer depuis l'onglet Applications »). Une application
   active s'archive ; une archivee se desarchive ou se supprime. Rien pour
   un service de la stack ni pour le diagnostic, qui ne s'archivent pas. */
const SVG_ARCHIVER='<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4"/></svg>';
const SVG_RESSORTIR='<svg viewBox="0 0 24 24"><path d="M9 14l-4-4 4-4"/><path d="M5 10h9a5 5 0 0 1 0 10h-2"/></svg>';
const SVG_SUPPRIMER='<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg>';
function apGestes(a){
  if(a.externe || a.par_defaut || a.name === 'diagnostic') return '';
  const nom=esc(nomAffiche(a)), cible=esc(a.name);
  if(a.archivee) return `<button class="icon-btn" title="Désarchiver" aria-label="Désarchiver ${nom}"
      data-agir="app-desarchiver" data-cible="${cible}">${SVG_RESSORTIR}</button>
    <button class="icon-btn danger" title="Supprimer" aria-label="Supprimer ${nom}"
      data-agir="app-supprimer" data-cible="${cible}">${SVG_SUPPRIMER}</button>`;
  return `<button class="icon-btn" title="Archiver" aria-label="Archiver ${nom}"
      data-agir="app-archiver" data-cible="${cible}">${SVG_ARCHIVER}</button>`;
}
function apJauge(v, max, texte){
  const pc=Math.max(2, Math.min(100, max ? v/max*100 : 0));
  return `<div class="ap-jauge"><span>${texte}</span><i><b style="width:${pc}%"></b></i></div>`;
}
function render(){
  apOutils();
  if(!toutesLesApps().length){
    remplir($('tbl-projets'), `<div class="empty">
      <div class="empty-icon"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/>
      <rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/>
      <rect x="14" y="14" width="7" height="7" rx="1.5"/></svg></div>
      <h3>Aucune application déclarée</h3>
      <p>Déclare un dossier de /workspace et il sera en ligne en quelques secondes.</p>
      <button class="btn btn-primary" onclick="openAdd()">Nouvelle application</button>
    </div>`);
    return;
  }
  const liste=apListe();
  if(!liste.length){
    remplir($('tbl-projets'), '<div class="ap-aucune"><b>Aucune application ne correspond</b>Change la recherche ou les filtres.</div>');
    return;
  }
  const memMax=Math.max(256, ...liste.map(a=>a.max_memory_mb||a.memory_mb||0));
  if(apVueCourante==='cartes'){
    remplir($('tbl-projets'), `<div class="ap-grille">${liste.map(a=>{
      const c=etatApp(a)[0];
      return `<article class="ap-carte ${c}" data-agir="app-ouvrir" data-cible="${esc(a.name)}">
        <div class="ap-carte-haut"><img src="${icone(a.name)}" alt="">
          <div class="ap-carte-titre"><b>${esc(nomAffiche(a))}</b><span>${esc(a.description||a.name)}</span></div></div>
        <div class="ap-carte-pills">${badgeEtat(a)}${badgeVis(a)}${a.categorie?`<span class="pill neutre"><i></i>${esc(a.categorie)}</span>`:''}</div>
        ${a.externe ? '<div class="hint">Service de la stack : non mesuré.</div>'
          : a.archivee ? `<div class="hint">${esc(texteArchive(a))}</div>`
          : `<div class="ap-jauges">${apJauge(a.cpu_percent||0, 100, 'CPU '+(a.cpu_percent||0).toFixed(1)+' %')}
              ${apJauge(a.memory_mb||0, a.max_memory_mb||memMax, 'Mémoire '+Math.round(a.memory_mb||0)+' Mo')}</div>`}
        <div class="ap-carte-pied"><span class="tbl-num">${a.port ? ':'+esc(String(a.port)) : '—'}</span><div class="spacer"></div>
          ${apBascule(a)}${apGestes(a)}
          <button class="icon-btn" title="Ouvrir la fiche" aria-label="Ouvrir la fiche de ${esc(nomAffiche(a))}"
            data-agir="app-ouvrir" data-cible="${esc(a.name)}"><svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg></button></div>
      </article>`;
    }).join('')}</div>`);
    return;
  }
  const avecCat=toutesLesApps().some(a=>a.categorie);
  const cols=AP_COLONNES.filter(([k])=>k!=='categorie' || avecCat);
  remplir($('tbl-projets'), `<div class="tbl-wrap"><div class="tbl-scroll"><table class="tbl ap-tbl">
    <thead><tr>${cols.map(([k,t])=>`<th aria-sort="${apTriCle===k ? (apTriSens>0?'ascending':'descending') : 'none'}">
      <button class="ap-th" data-agir="ap-trier" data-cible="${k}">${t}<span aria-hidden="true">${apTriCle===k ? (apTriSens>0?'▲':'▼') : '↕'}</span></button></th>`).join('')}<th></th>
    </tr></thead><tbody>
    ${liste.map(a=>`<tr data-agir="app-ouvrir" data-cible="${esc(a.name)}">
      <td><div class="tbl-nom"><img src="${icone(a.name)}" alt="">
        <div style="min-width:0"><div class="n">${esc(nomAffiche(a))}</div>
        ${a.description?`<div class="d">${esc(a.description)}</div>`:''}</div></div></td>
      <td>${badgeEtat(a)}</td>
      <td>${badgeVis(a)}</td>
      ${avecCat ? `<td>${a.categorie ? esc(a.categorie) : '<span class="hint">—</span>'}</td>` : ''}
      <td class="tbl-num">${a.port ? esc(String(a.port)) : '—'}</td>
      <td class="tbl-num">${a.externe ? '—' : (a.cpu_percent||0).toFixed(1)+'%'}</td>
      <td class="tbl-num">${a.externe ? '—' : Math.round(a.memory_mb||0)+' Mo'}</td>
      <td class="tbl-act"><div>${apBascule(a)}${apGestes(a)}
        <button class="icon-btn" title="Ouvrir la fiche"
          data-agir="app-ouvrir" data-cible="${esc(a.name)}">
          <svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg></button>
      </div></td></tr>`).join('')}
    </tbody></table></div></div>`);
}

/* ---------------- actions ---------------- */
/* TOUTES LES ACTIONS LISENT LEUR REPONSE, ET AUCUNE NE SE RABAT SUR UNE
   PHRASE VIDE. « L'application n'a pas demarre » n'apprend rien : ce qu'on
   veut savoir, c'est le port deja pris, la commande introuvable, ou la
   session expiree. Le serveur le dit desormais dans tous les cas ; quand il
   ne dit rien du tout -- panneau redemarre en plein clic, reponse qui n'est
   pas du JSON -- on rend au moins le code HTTP, qui distingue un refus (403)
   d'une panne (500) et vaut mieux qu'un haussement d'epaules. */
/* TOUJOURS DIRE QUOI FAIRE ENSUITE. « (reponse 500) » n'apprenait rien.
   Une panne du serveur porte une reference (en-tete X-CodeLab-Ref), ecrite
   aussi dans le journal du conteneur : l'administrateur la cherche dans
   « docker logs », un compte utilisateur la lui transmet. */
async function echec(r, defaut){
  let d={}; try{ d=await r.json(); }catch(e){}
  const ref = r.headers && r.headers.get('X-CodeLab-Ref');
  const suite = !ref ? '' : estAdmin
    ? ` Référence ${ref} : cherche-la avec « docker logs codelab-app-manager ».`
    : ` Référence ${ref} : donne-la à l'administrateur.`;
  /* Une erreur 5xx est technique : son texte ne regarde que
     l'administrateur. Un compte utilisateur lit la phrase simple, et la
     reference a transmettre. */
  if(r.status>=500 && !estAdmin) return defaut + ' Réessaie dans un instant.' + suite;
  if(d && d.error) return d.error + (r.status>=500 ? ' Réessaie dans un instant.' : '') + suite;
  if(r.status===401) return 'Session expirée : reconnecte-toi.';
  if(r.status===403) return "Action refusée. Recharge la page, puis réessaie.";
  if(r.status===404) return defaut + ' Introuvable : recharge la page.';
  if(r.status===413) return defaut + ' Le fichier est trop lourd.';
  if(r.status===429) return defaut + ' Trop de tentatives : patiente une minute, puis réessaie.';
  if(r.status>=500) return defaut + ' Réessaie dans un instant.' + suite;
  return defaut + ' Recharge la page, puis réessaie.';
}

/* ET LE REPLI DIT LE BON SENS. Celui d'echec() est ecrit pour le demarrage :
   un arret qui echouait s'annoncait donc « L'application n'a pas demarre
   (reponse 500) », ce qui envoie chercher exactement a l'oppose de la panne.
   La page connait l'etat qu'elle vient d'afficher -- c'est lui qui choisit
   la phrase quand le serveur ne dit rien du tout. Quand il parle, son
   message gagne : il nomme l'action tentee. */
/* UNE ACTION A LA FOIS. Un double clic sur « Demarrer » demarrait puis
   arretait l'application : la seconde requete partait avant que la page ait
   vu la premiere aboutir. Pendant qu'une action tourne, ses boutons sont
   desactives, et un clic de plus ne part pas. Le serveur tient la meme
   regle de son cote (409), pour deux onglets ou deux administrateurs. */
const actionsEnCours = new Set();
async function uneAction(n, fn){
  if(actionsEnCours.has(n)) return;
  actionsEnCours.add(n);
  const boutons=[...document.querySelectorAll('[data-cible]')].filter(b=>b.dataset.cible===n);
  /* L'etat d'avant est garde : « Redemarrer » est deja grise sur une
     application arretee, et doit le rester apres. Un rafraichissement qui
     repeint les boutons entre-temps ne rouvre rien : le Set ci-dessus
     refuse le clic tant que l'action n'est pas finie. */
  const avant=boutons.map(b=>b.disabled);
  boutons.forEach(b=>{ b.disabled=true; b.setAttribute('aria-busy','true'); });
  try{ await fn(); }
  finally{
    actionsEnCours.delete(n);
    boutons.forEach((b,i)=>{ b.disabled=avant[i]; b.removeAttribute('aria-busy'); });
  }
}
async function tg(n){ return uneAction(n, async()=>{
  const arret=(apps.find(a=>a.name===n)||{}).running===true;
  const r=await fetch('/api/toggle/'+n,{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action: arret ? 'arret' : 'demarrage'})});
  if(!r.ok) notifier(await echec(r, arret ? "L'application n'a pas pu être arrêtée."
                                          : "L'application n'a pas démarré."), 'err');
  setTimeout(refresh,400);
}); }
/* Le redemarrage jetait sa reponse sans la regarder : une application qui ne
   repartait pas laissait le bouton silencieux et la pastille sur
   « Arretee », sans un mot pour dire pourquoi. */
async function restartApp(n){ return uneAction(n, async()=>{
  const r=await fetch('/api/restart/'+n,{method:'POST'});
  if(!r.ok) notifier(await echec(r, "L'application n'a pas redémarré."), 'err');
  else notifier('« '+n+' » a redémarré.');
  setTimeout(refresh,500);
}); }
async function buildApp(n){ return uneAction(n, async()=>{
  ouvrirFiche(n); ficheOnglet('journal');
  const r=await fetch('/api/build/'+n,{method:'POST'});
  if(!r.ok) notifier(await echec(r, 'Le build a échoué.'), 'err');
}); }
async function basculerVisibilite(n){
  const r=await fetch('/api/visibility/'+n,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const d=await r.json();
  if(!r.ok){ notifier(d.error||'Changement impossible.', 'err'); return; }
  refresh();
}
async function rm(n){
  if(!await demander('Supprimer « ' + n + ' » ?',
    "L'application disparaît du panneau. Le dossier du projet dans /workspace n'est pas touché.",
    'Supprimer', true)) return;
  const r=await fetch('/api/app/'+n,{method:'DELETE'});
  if(!r.ok){
    /* Le serveur peut refuser -- le projet de diagnostic, par exemple. Sans
       ce test, le panneau se contentait de refermer la fiche comme si la
       suppression avait eu lieu. */
    let d={}; try{ d=await r.json(); }catch(e){}
    notifier(d.error || 'Suppression impossible.', 'err');
    return;
  }
  if(ficheNom===n){ ficheNom=null; showSection('apps'); }
  refresh();
}
/* « Archivee le 18 octobre · configuration effacee dans 30 jours », ou
   « configuration effacee » une fois le delai passe. */
function texteArchive(a){
  const le = a.archivee_le ? 'Archivée le '+new Date(a.archivee_le*1000).toLocaleDateString('fr-FR',{day:'numeric',month:'long'}) : 'Archivée';
  if(a.configuration_effacee) return le+' · configuration effacée';
  if(!a.effacement_le) return le;
  const j=Math.max(0, Math.ceil((a.effacement_le-Date.now()/1000)/86400));
  return le+' · configuration effacée '+(j===0 ? "aujourd'hui" : j===1 ? 'demain' : 'dans '+j+' jours');
}
/* L'archive : arretee, hors du hub, et sa configuration gardee trente jours
   (voir « l'archivage d'une application » cote serveur). */
async function archiver(n){
  if(!await demander('Archiver « ' + n + ' » ?',
    "Elle s'arrête, quitte le hub et passe au statut « Archivée ». Sa configuration est gardée "
    + "30 jours : tu peux la désarchiver d'ici là. Ensuite, sa configuration est effacée ; "
    + "l'application reste, archivée. Le dossier du projet dans /workspace n'est pas touché.",
    'Archiver', false)) return;
  const r=await fetch('/api/app/'+encodeURIComponent(n)+'/archiver',{method:'POST'});
  if(!r.ok){ notifier(await echec(r, 'Archivage impossible.'), 'err'); return; }
  notifier('« '+n+' » est archivée.');
  await refresh();
}
async function desarchiver(n){
  const r=await fetch('/api/app/'+encodeURIComponent(n)+'/desarchiver',{method:'POST'});
  if(!r.ok){ notifier(await echec(r, 'Désarchivage impossible.'), 'err'); return; }
  const d=await r.json().catch(()=>({}));
  notifier(d.a_reconfigurer ? '« '+n+' » est de retour : vérifie ses commandes avant de la démarrer.'
    : '« '+n+' » est de retour, arrêtée.');
  await refresh();
}
