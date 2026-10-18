/* CodeLab -- panneau/05-fiche.js : La fiche d'une application : acces, configuration, logo, metriques, journal ; la pop-up « nouvelle application ».
   Une partie du script du panneau, assemblee par app.py (PANNEAU_MODULES)
   dans l'ordre des numeros : un seul script, une seule portee. */
/* ---------------- fiche d'une application ---------------- */
/* Tout ce qui concerne une application au meme endroit : son etat, ses
   metriques, son journal, sa configuration et les actions. Avant, ces quatre
   choses vivaient dans trois pop-up differentes qu'il fallait ouvrir une par
   une pour se faire une idee. */
let ficheNom=null, ficheOngletActif='general';

function appParNom(n){ return apps.find(a=>a.name===n) || appsArchivees.find(a=>a.name===n); }

function ouvrirFiche(n){
  ficheNom=n;
  showSection('app');
  peindreFiche();
  chargerConfigFiche();
  ficheOnglet(ficheOngletActif);
}

function ficheOnglet(o){
  ficheOngletActif=o;
  if(sectionCourante==='app') majAdresse();
  document.querySelectorAll('.fiche-onglets button').forEach(b=>b.classList.toggle('active', b.dataset.o===o));
  document.querySelectorAll('.onglet').forEach(n=>n.classList.toggle('actif', n.id==='onglet-'+o));
  if(o==='journal') ouvrirLog(ficheNom); else fermerLog();
  if(o==='metriques') chargerMetriques(ficheNom);
  if(o==='activite') chargerActivite().then(peindreActiviteFiche);
  if(o==='acces') chargerAcces(ficheNom);
}

/* ---------------- acces a une application ---------------- */
/* L'etat vient du serveur a chaque ouverture de l'onglet, jamais d'un cache :
   une autorisation accordee depuis la fiche d'un compte, dans un autre
   onglet du navigateur, doit se voir ici. */
let acCourant='';

async function chargerAcces(nom){
  acCourant=nom;
  const zone=$('acces-liste'); zone.innerHTML='<div class="squelette" role="status" aria-label="Chargement"><i></i><i></i><i></i></div>';
  $('ac-err').style.display='none'; $('ac-msg').style.display='none';
  let d;
  try{
    const r=await fetch('/api/apps/'+encodeURIComponent(nom)+'/acces');
    d=await r.json();
    if(!r.ok) throw new Error(d.error||'');
  }catch(e){
    zone.innerHTML='';
    $('ac-err').textContent='Liste des accès indisponible.';
    $('ac-err').style.display='block';
    return;
  }
  const comptes=d.comptes||[];
  const nb=comptes.filter(c=>c.acces).length;
  const p=$('ac-etat');
  p.className='pill '+(nb?'on':'off');
  p.innerHTML='<i></i>'+(nb ? nb+' compte'+(nb>1?'s':'') : 'aucun compte');

  /* Une application publique s'ouvre sans compte : cocher quelqu'un ici n'y
     changerait rien, et laisser croire le contraire serait pire que de ne
     rien afficher. */
  const pub=$('ac-publique');
  pub.style.display = d.publique ? 'block' : 'none';
  pub.innerHTML = d.publique
    ? '<b>Cette application est publique</b> : elle s\'ouvre sans compte, y compris par '
      + 'quelqu\'un qui n\'en a pas. Ces autorisations ne reprendront effet que si tu la '
      + 'repasses en privée (onglet <b>Configuration</b>).'
    : '';

  if(!comptes.length){
    zone.innerHTML='<div class="hint">Aucun compte nomme n\'existe. Toi seul ouvres les '
      + 'projets privés — crée un compte depuis <b>Utilisateurs</b> pour en partager un.</div>';
    return;
  }
  /* Une ligne par compte, choisie ou non : la meme facon de designer que
     partout ailleurs dans le panneau. */
  /* Le compte voyage dans un attribut data-, pas dans l'id : « ac-total »
     existait deja ailleurs dans la page, et un compte nomme « total » aurait
     coche la mauvaise case. */
  zone.innerHTML = (comptes.length>6
      ? `<input class="acces-filtre" id="acces-filtre" type="search" placeholder="Rechercher un compte"
           aria-label="Rechercher un compte" oninput="acFiltrer()" style="margin-bottom:10px">` : '')
    + comptes.map(c=>
    `<label class="choix-ligne acces-ligne" data-cherche="${esc((c.nom+' '+(c.email||'')).toLowerCase())}">
       <input type="checkbox" data-compte="${esc(c.nom)}" ${c.acces?'checked':''}
         onchange="acBasculer()">
       <span class="acces-initiale" aria-hidden="true">${esc((c.nom||'?').slice(0,1).toUpperCase())}</span>
       <div class="proj-txt"><b>${esc(c.nom)}</b>
         ${c.email ? `<span>${esc(c.email)}</span>` : ''}</div>
     </label>`).join('');
}
function acFiltrer(){
  const q=($('acces-filtre').value||'').trim().toLowerCase();
  document.querySelectorAll('#acces-liste .acces-ligne').forEach(l=>
    l.style.display = !q || l.dataset.cherche.includes(q) ? '' : 'none');
}

async function acBasculer(){
  const cases=[...document.querySelectorAll('#acces-liste input[type=checkbox]')];
  const choisis=cases.filter(c=>c.checked).map(c=>c.dataset.compte);
  cases.forEach(c=>c.disabled=true);
  const m=$('ac-msg'), e=$('ac-err');
  e.style.display='none';
  try{
    const r=await fetch('/api/apps/'+encodeURIComponent(acCourant)+'/acces',
      {method:'PUT', headers:{'Content-Type':'application/json'},
       body:JSON.stringify({utilisateurs: choisis})});
    const d=await r.json();
    if(!r.ok){ e.textContent=d.error||'Enregistrement impossible.'; e.style.display='block'; }
    else{
      m.textContent = choisis.length
        ? choisis.join(', ')+' ouvre'+(choisis.length>1?'nt':'')+' cette application.'
        : 'Plus aucun compte n\'ouvre cette application.';
      m.style.display='block';
    }
  }catch(err){ e.textContent='Erreur réseau.'; e.style.display='block'; }
  finally{ cases.forEach(c=>c.disabled=false); }
  /* La liste des comptes affiche un compteur de projets : elle doit suivre. */
  await chargerAcces(acCourant);
  chargerUtilisateurs();
}

/* LES APPLICATIONS PAR DEFAUT : celles que CodeLab apporte avec lui.

   Elles se lisent comme les autres -- meme tuile, meme ligne, meme fiche --
   et c'est voulu : ce sont des applications. Ce qui change tient en une
   phrase : il n'y a rien a y configurer. On retire donc les onglets et les
   boutons qui proposent de le faire, plutot que de les laisser mener a un
   refus du serveur.

   Le serveur refuse quand meme, et c'est la vraie garde : masquer un bouton
   ne protege rien, la route reste appelable a la main. */
const ONGLETS_CONFIG = ['acces', 'config'];

/* La cause, en clair : les dernieres lignes du journal, celles qui disent
   pourquoi, avec un conseil selon l'etat. Relue au plus toutes les 10 s
   (la fiche se repeint souvent), et aussitot que l'etat change. */
const LIGNE_GRAVE=/error|erreur|exception|traceback|fatal|refused|denied|not found|introuvable|cannot|impossible|eaddrinuse|killed/i;
let causeVue={cle:'', quand:0};
async function peindreCauseFiche(a){
  const carte=$('fa-cause'); if(!carte) return;
  const etat = a.externe || a.par_defaut ? ''
    : a.crash_looping ? 'boucle'
    : a.failed ? 'arretee'
    : (!a.running && a.dernier_echec) ? 'lancement'
    : (a.running && a.listening===false) ? 'muette' : '';
  if(!etat){ carte.style.display='none'; causeVue={cle:'', quand:0}; return; }
  const TITRES={
    boucle:['Elle redémarre en boucle', "Elle plante à chaque lancement : la cause est en général dans les dernières lignes ci-dessous. Corrige-la dans le code, puis Redémarrer."],
    lancement:['Elle a planté au lancement', "Elle s'est arrêtée aussitôt lancée (code "+((a.dernier_echec||{}).code)+"). La cause est dans les dernières lignes ci-dessous ; une fois corrigée, Démarrer."],
    arretee:["Elle s'est arrêtée", "Elle devait tourner et s'est arrêtée. Les dernières lignes du journal disent pourquoi ; une fois corrigé, Démarrer."],
    muette:['Elle ne répond pas', "Elle tourne, mais rien n'écoute sur son port : elle doit écouter sur $PORT et 127.0.0.1. Si elle démarre encore, patiente quelques secondes."],
  };
  $('fa-cause-titre').textContent=TITRES[etat][0];
  $('fa-cause-conseil').textContent=TITRES[etat][1];
  carte.style.display='';
  const cle=a.name+':'+etat;
  if(cle===causeVue.cle && Date.now()-causeVue.quand<10000) return;
  causeVue={cle, quand:Date.now()};
  let lignes=[];
  try{
    const r=await fetch('/api/logs/'+encodeURIComponent(a.name));
    if(r.ok) lignes=((await r.json()).lines||[]).filter(l=>l.trim()).slice(-15);
  }catch(e){}
  if(ficheNom!==a.name) return;
  remplir($('fa-cause-lignes'), lignes.length
    ? lignes.map(l=>`<span${LIGNE_GRAVE.test(l) ? ' class="grave"' : ''}>${esc(l)}</span>`).join('\n')
    : 'Le journal est vide : l\'application n\'a rien écrit avant de s\'arrêter.');
}
function peindreFiche(){
  const a=appParNom(ficheNom);
  if(!a) return;
  const parDefaut = !!a.par_defaut, externe = !!a.externe;
  /* #fiche-onglets et non « .fiche-onglets » : la barre des REGLAGES porte
     la meme classe, et un selecteur global masquerait ses onglets a elle. */
  document.querySelectorAll('#fiche-onglets button').forEach(b=>{
    const cache = parDefaut && ONGLETS_CONFIG.includes(b.dataset.o);
    b.style.display = cache ? 'none' : '';
    /* Un onglet masque alors qu'il etait ouvert laisserait la fiche sur une
       page vide : on revient au premier. */
    if(cache && b.classList.contains('active')) ficheOnglet('general');
  });
  $('fa-defaut').style.display = parDefaut ? '' : 'none';
  $('mt-cpu-titre').textContent = externe ? 'Temps de réponse' : 'CPU maintenant';
  $('mt-mem-titre').textContent = externe ? 'Disponibilité' : 'Mémoire maintenant';
  $('mt-note').textContent = externe
    ? "Mesuré par le panneau, toutes les 10 secondes : une requête à " + nomAffiche(a) + " "
      + "et le temps qu'elle met à revenir. Le panneau ne voit ni le processeur "
      + "ni la mémoire d'un autre conteneur."
    : 'Sur les deux dernières minutes environ, relevé toutes les 5 secondes.';
  peindreCauseFiche(a);
  poserSource($('fa-icone'), icone(a.name));
  $('fa-nom').textContent=a.name;
  $('fa-sous').textContent=a.description || 'Aucune description';
  const [cl,txt]=etatApp(a);
  $('fa-etat').className='pill '+cl; $('fa-etat').innerHTML='<i></i>'+txt;
  const prive=a.visibility==='privee';
  $('fa-vis').className='pill '+(prive?'priv':'pub');
  $('fa-vis').innerHTML='<i></i>'+(prive?'Privé':'Public');

  $('fa-url').textContent = externe ? a.url : '/'+a.name+'/';
  /* Le texte dit ou le service VIT, le lien passe par la porte du panneau :
     ce ne sont pas la meme chose pour un service externe. */
  $('fa-url').href = lienOuverture(a);
  $('fa-port').textContent=a.port;
  /* Un service externe n'est jamais « arrêtée » : le panneau ne l'arrete
     pas, il constate seulement si le port repond. */
  $('fa-ecoute').textContent = a.archivee ? texteArchive(a) : externe
    ? (a.running ? 'Le port répond' : 'Le port ne répond pas')
    : (!a.running ? 'Arrêtée'
       : (a.listening===false ? 'Le port ne répond pas' : 'Le port répond'));
  $('fa-ecoute-large').style.display = (!externe && a.running && a.ecoute_large) ? '' : 'none';
  $('fa-vis-txt').textContent = prive
    ? 'Privée — session requise' : 'Publique — ouverte a tous';
  /* Un service externe n'a ni dossier ni commande : le panneau ne le lance
     pas. Mettre « — » partout serait dire qu'on ne sait pas, alors qu'il n'y
     a rien a savoir. */
  $('fa-carte-exec').style.display = externe ? 'none' : '';
  $('fa-path').textContent=a.path;
  $('fa-cmd').textContent=a.command;
  $('fa-build').textContent=a.build_command || '—';
  $('fa-mem').textContent=a.max_memory_mb ? a.max_memory_mb+' Mo' : 'aucune';
  $('fa-mesures').style.display = externe ? 'none' : '';
  peindrePresentationFiche(a);
  $('fa-cpu').textContent=(a.cpu_percent||0).toFixed(1)+'%';
  $('fa-ram').textContent=Math.round(a.memory_mb||0)+' Mo';

  peindreActionsFiche(a, prive, parDefaut);
}

/* Les boutons de la fiche, selon ce que l'application permet : un service
   externe s'ouvre seulement, le diagnostic se demarre et s'ouvre, une
   application se pilote. */
function peindreActionsFiche(a, prive, parDefaut){
  const externe = !!a.externe;
  const svgPlay='<svg viewBox="0 0 24 24"><path d="M8 5.5l10 6.5-10 6.5z"/></svg>';
  const svgStop='<svg viewBox="0 0 24 24"><rect x="6.5" y="6.5" width="11" height="11" rx="2"/></svg>';
  const svgRe='<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 10-2.3 5.7"/><path d="M20 5v6h-6"/></svg>';
  const svgBuild='<svg viewBox="0 0 24 24"><path d="M14.7 6.3a4 4 0 01-5.4 5.4L4 17v3h3l5.3-5.3a4 4 0 015.4-5.4z"/></svg>';
  const svgOuvrir='<svg viewBox="0 0 24 24"><path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 13v6a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h6"/></svg>';

  const b=[];
  /* Un service externe : le panneau ne le demarre pas, ne l'arrete pas, ne
     le deploie pas. Il ouvre, et c'est tout. */
  if(externe){
    b.push(`<a class="btn btn-primary" href="${lienOuverture(a)}">${svgOuvrir}Ouvrir</a>`);
    $('fa-actions').innerHTML=b.join('');
    return;
  }
  /* Archivee : un seul geste, la ressortir. Supprimer reste en bas de
     l'onglet Configuration. */
  if(a.archivee){
    b.push(`<button class="btn btn-demarrer" data-agir="app-desarchiver" data-cible="${esc(a.name)}">Désarchiver</button>`);
    $('fa-actions').innerHTML=b.join('');
    return;
  }
  if(a.name === 'diagnostic'){
    if(!a.running) b.push(`<button class="btn btn-demarrer" data-agir="app-basculer" data-cible="${esc(a.name)}">${svgPlay}Démarrer</button>`);
    if(a.running) b.push(`<a class="btn btn-ouvrir" href="${lienOuverture(a)}">${svgOuvrir}Ouvrir</a>`);
    $('fa-actions').innerHTML=b.join('');
    return;
  }
  b.push(`<button class="btn ${a.running?'btn-arreter':'btn-demarrer'}" data-agir="app-basculer" data-cible="${esc(a.name)}">
    ${a.running?svgStop+'Arrêter':svgPlay+'Démarrer'}</button>`);
  b.push(`<button class="btn btn-refaire" data-agir="app-redemarrer" data-cible="${esc(a.name)}" ${a.running?'':'disabled'}>
    ${svgRe}Redémarrer</button>`);
  if(a.has_build) b.push(`<button class="btn btn-refaire" data-agir="app-build" data-cible="${esc(a.name)}">${svgBuild}Build</button>`);
  if(!a.par_defaut) b.push(`<button class="btn btn-refaire" data-agir="app-archiver" data-cible="${esc(a.name)}">Archiver</button>`);
  /* « Rendre publique » n'est propose que si le serveur a une adresse
     publique declaree. Sinon le mot promet un partage qui n'existe pas :
     cela ne ferait que retirer l'authentification. Le chemin qui REFERME,
     lui, reste toujours ouvert. */
  if(parDefaut){
    /* Rien : une application par defaut reste privee. Le diagnostic rendu
       public offrirait a un visiteur anonyme l'etat des lieux complet de
       l'installation. */
  }else if(!prive || adressePublique){
    b.push(`<button class="btn btn-quiet" data-agir="app-visibilite" data-cible="${esc(a.name)}">
      ${prive?'Rendre publique':'Rendre privée'}</button>`);
  }
  if(a.running) b.push(`<a class="btn btn-ouvrir" href="${lienOuverture(a)}">${svgOuvrir}Ouvrir</a>`);
  $('fa-actions').innerHTML=b.join('');
}

/* ---------------- fiche : configuration ---------------- */
let editCur='';
function chargerConfigFiche(){
  const a=appParNom(ficheNom);
  if(!a) return;
  editCur=a.path;
  $('e-desc').value=a.description||''; compterDescription($('e-desc'));
  optionsCategorie('e-cat', a.categorie||'');
  $('e-cmd').value=a.command||'';
  $('e-build').value=a.build_command||'';
  $('e-alertes').value=(a.alertes||[]).join(', ');
  $('e-mem').value=a.max_memory_mb||'';
  $('e-path').textContent=a.path;
  setVisEdit(a.visibility||'publique');
  /* Meme regle que le bouton du haut : on n'offre pas d'ouvrir ce qu'on ne
     peut pas partager. Une application deja publique garde son choix, pour
     pouvoir la refermer. */
  $('e-vis-zone').style.display =
    (adressePublique || (a.visibility||'publique')==='publique') ? '' : 'none';
  $('e-err').style.display='none';
  $('e-logo-err').style.display='none';
  logoRafraichir();
  $('e-browse').style.display='none';
  /* Le bandeau de redemarrage ne survit pas a un rechargement de la fiche :
     il parle d'un enregistrement precis, pas d'un etat durable. */
  $('e-redemarrage').style.display='none';
  /* Le projet de diagnostic ne se supprime pas : c'est lui qui dit si cette
     installation va bien. Le bouton disparait plutot que d'echouer -- mais
     c'est le serveur qui refuse vraiment, la route reste appelable a la
     main. */
  $('e-supprimer').style.display = (a.name === 'diagnostic') ? 'none' : '';
  $('e-archiver').style.display = (a.name === 'diagnostic' || a.archivee) ? 'none' : '';
  /* Le nom, et ce qu'il change : l'adresse de l'application en fait partie. */
  $('e-nom').value = a.name;
  $('e-nom-url').textContent = urlApplication(a.name);
  $('e-nom-err').style.display='none';
  $('e-nom-ok').style.display='none';
  /* Le diagnostic ne se renomme pas plus qu'il ne se supprime : le panneau
     le retrouve par son nom, et son marqueur d'inscription porte dessus. */
  const fige = a.name === 'diagnostic';
  $('e-nom').disabled = fige;
  $('e-renommer').disabled = fige;
}

/* RENOMMER, c'est changer une CLE : le nom designe l'application dans
   apps.json, dans son adresse, dans son journal, dans les projets autorises
   de chaque compte et dans ce que chacun a masque de son hub. Le serveur fait
   le tour ; la page se contente de rouvrir la fiche sous le nouveau nom --
   sans quoi elle continuerait d'interroger une application qui n'existe
   plus. */
async function renommerApp(){
  const ancien = ficheNom, nouveau = ($('e-nom').value||'').trim();
  const err=$('e-nom-err'), ok=$('e-nom-ok'), bouton=$('e-renommer');
  err.style.display='none'; ok.style.display='none';
  if(!nouveau || nouveau===ancien) return;
  bouton.disabled=true; bouton.textContent='Renommage...';
  try{
    const r = await fetch('/api/app/'+encodeURIComponent(ancien)+'/renommer',
      {method:'POST', headers:{'Content-Type':'application/json'},
       body: JSON.stringify({nom:nouveau})});
    const d = await r.json().catch(()=>({}));
    if(!r.ok){
      err.textContent = d.error || "Le renommage a échoué.";
      err.style.display='block';
      return;
    }
    await refresh();
    ficheNom = d.nom;
    peindreFiche();
    chargerConfigFiche();
    ok.style.display='block';
    ok.innerHTML = 'Renommée en <b>'+esc(d.nom)+'</b>.'
      + (d.redemarree ? ' Elle est repartie.' : '')
      + (d.erreur_demarrage ? ' Elle n\'a pas redémarré : ' + esc(d.erreur_demarrage) : '')
      + (d.fichiers_repris ? ' ' + d.fichiers_repris
         + ' fichier' + (d.fichiers_repris>1?'s':'') + ' du dossier lui '
         + (d.fichiers_repris>1?'ont':'a') + ' été rendu'
         + (d.fichiers_repris>1?'s':'') + '.' : '');
  }catch(e){
    err.textContent = "Le panneau n'a pas répondu : " + e;
    err.style.display='block';
  }finally{
    bouton.disabled=false; bouton.textContent='Renommer';
  }
}
/* ---------------- le logo d'une application ---------------- */
function logoChoisir(){ $('e-logo-fichier').click(); }

function logoRafraichir(){
  /* Le parametre d'horodatage force le navigateur a redemander l'image :
     l'adresse ne change pas quand le fichier, lui, vient d'etre remplace. */
  $('e-logo-img').src = icone(ficheNom) + '?v=' + Date.now();
}

async function logoEnvoyer(entree){
  const f = entree.files && entree.files[0];
  entree.value = '';                    /* pour pouvoir renvoyer le meme fichier */
  if(!f) return;
  const e = $('e-logo-err'); e.style.display = 'none';
  if(f.size > 512 * 1024){
    e.textContent = 'Image trop lourde : ' + Math.round(f.size / 1024)
      + ' Ko pour 512 Ko au maximum.';
    e.style.display = 'block'; return;
  }
  const btn = $('e-logo-btn'); btn.disabled = true;
  try{
    const r = await fetch('/api/app/' + encodeURIComponent(ficheNom) + '/logo',
      {method:'PUT', headers:{'Content-Type': f.type || 'application/octet-stream'}, body:f});
    const d = await r.json();
    if(!r.ok){ e.textContent = d.error || 'Envoi impossible.'; e.style.display = 'block'; }
    else{ logoRafraichir(); await refresh(); peindreFiche(); notifier('Logo mis à jour.'); }
  }catch(err){ e.textContent = 'Erreur réseau.'; e.style.display = 'block'; }
  finally{ btn.disabled = false; }
}

async function logoRetirer(){
  const e = $('e-logo-err'); e.style.display = 'none';
  const r = await fetch('/api/app/' + encodeURIComponent(ficheNom) + '/logo', {method:'DELETE'});
  const d = await r.json().catch(()=>({}));
  if(!r.ok){ e.textContent = d.error || 'Suppression impossible.'; e.style.display = 'block'; return; }
  logoRafraichir(); await refresh(); peindreFiche();
  notifier((d.retires && d.retires.length) ? 'Logo retiré.'
    : "Aucun logo déposé par le panneau — l'icône vient du projet lui-même.");
}

async function redemarrerDepuisConfig(){
  const b=$('e-redemarrer');
  b.disabled=true; b.textContent='Redémarrage...';
  try{
    const r=await fetch('/api/restart/'+encodeURIComponent(ficheNom),{method:'POST'});
    if(!r.ok){
      const d=await r.json().catch(()=>({}));
      $('e-err').textContent=d.error||'Redémarrage impossible.';
      $('e-err').style.display='block';
    }else{
      $('e-redemarrage').style.display='none';
    }
  }catch(e){
    $('e-err').textContent='Erreur réseau.'; $('e-err').style.display='block';
  }finally{
    b.disabled=false; b.textContent='Redémarrer';
    await refresh(); peindreFiche();
  }
}
function toggleEditBrowse(){
  const z=$('e-browse');
  const ouvert = z.style.display!=='none';
  z.style.display = ouvert ? 'none' : 'block';
  if(!ouvert) browseEdit(editCur);
}
/* LES DEUX NAVIGATEURS DE DOSSIERS PASSENT PAR ICI, et ils construisent du
   DOM -- jamais une chaine de HTML.

   Ce qu'ils affichaient avant : un div dont le gestionnaire onclick
   appelait browse() avec le chemin interpole dans une chaine JavaScript.
   Le LIBELLE etait echappe, le GESTIONNAIRE ne l'etait pas. Un dossier nomme
   `truc'+alert(1)+'` refermait la chaine JavaScript et executait la suite --
   dans la page du panneau, celle qui detient le jeton CSRF et les boutons
   demarrer, arreter, deployer. Or /workspace est inscriptible depuis une
   session SSH ET par toute application deployee : n'importe laquelle pouvait
   poser le dossier, attendre un clic, et sortir de son uid restreint.
   Verifie : alert() s'executait au clic.

   Ici le chemin ne devient jamais du texte de programme. Il est capture par
   une fermeture, et le nom passe par textContent, qui n'interprete rien.
   Aucune regle d'echappement a retenir, donc aucune a oublier. */
function peuplerDossiers(zone, d, aller){
  zone.textContent = '';
  const ligne = (libelle, chemin) => {
    const n = document.createElement('div');
    n.textContent = libelle;
    n.onclick = () => aller(chemin);
    zone.appendChild(n);
  };
  if(d.parent) ligne('\u21a9 Dossier parent', d.parent);
  (d.dirs || []).forEach(nom => ligne('\u{1f4c1} ' + nom, d.path + '/' + nom));
  return zone.childElementCount;
}

function lignePlate(zone, texte, couleur){
  const n = document.createElement('div');
  n.textContent = texte;
  n.style.cursor = 'default';
  if(couleur) n.style.color = couleur;
  zone.appendChild(n);
}

async function browseEdit(p){
  const d=await (await fetch('/api/browse?path='+encodeURIComponent(p))).json();
  editCur=d.path; $('e-fb-c').textContent=d.label; $('e-path').textContent=d.path;
  /* Cette liste-ci lisait x.path et x.name sur des CHAINES : /api/browse rend
     des noms de dossiers, pas des objets. Elle affichait donc autant de
     lignes vides que de dossiers, toutes vers « undefined ». */
  if(!peuplerDossiers($('e-fb-l'), d, browseEdit))
    lignePlate($('e-fb-l'), 'Dossier vide', 'var(--dim)');
}
async function submitEdit(){
  const c=$('e-cmd').value.trim();
  const e=$('e-err');
  if(!c){ e.textContent='La commande de lancement est obligatoire.'; e.style.display='block'; return; }
  const body={path:editCur, command:c, description:$('e-desc').value.trim(),
    categorie:$('e-cat').value,
    build_command:$('e-build').value.trim(),
    max_memory_mb:$('e-mem').value ? parseInt($('e-mem').value,10) : null,
    visibility:visEdit};
  const r=await fetch('/api/app/'+ficheNom,{method:'PUT',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(!r.ok){ e.textContent=d.error||'Enregistrement impossible.'; e.style.display='block'; return; }
  /* Les destinataires d'alerte ont leur propre route -- ils ne sont pas une
     option d'execution, et une adresse mal ecrite ne doit pas faire echouer
     l'enregistrement de la commande. Le meme bouton les enregistre malgre
     tout : c'est le meme formulaire pour qui le remplit. */
  const ra=await fetch('/api/alertes/application/'+encodeURIComponent(ficheNom),
    {method:'PUT', headers:{'Content-Type':'application/json'},
     body:JSON.stringify({alertes: $('e-alertes').value})});
  if(!ra.ok){
    e.textContent=await echec(ra, "Les destinataires d'alerte n'ont pas été enregistrés.");
    e.style.display='block';
    return;
  }
  e.style.display='none';
  await refresh();
  chargerConfigFiche();
  /* C'est le serveur qui dit ce qui attend un redemarrage : lui seul sait ce
     qu'il relit a chaque requete et ce qu'il lit une fois pour toutes. */
  if(d.redemarrage_requis){
    const noms={path:'le dossier', command:'la commande de lancement',
                max_memory_mb:'la limite mémoire'};
    const liste=(d.champs_en_attente||[]).map(c=>noms[c]||c);
    $('e-redemarrage-txt').innerHTML='<b>Enregistré.</b> '
      + (liste.length===1 ? 'Le changement sur ' : 'Les changements sur ')
      + liste.join(' et ')
      + (liste.length>1 ? ' ne prendront' : ' ne prendra')+' effet qu\'au redémarrage : '
      + 'l\'application en cours tourne encore avec la configuration précédente.';
    $('e-redemarrage').style.display='flex';
  }else{
    notifier('Configuration enregistrée.');
  }
}

/* ---------------- fiche : metriques ---------------- */
function sparklineSvg(values){
  if(!values.length) return '<div class="chart-empty" style="padding:16px">Aucune mesure.</div>';
  const w=300,h=56,pad=4, max=Math.max(1, ...values);
  const step=values.length>1?(w-2*pad)/(values.length-1):0;
  const pts=values.map((v,i)=>{
    const x=pad+i*step, y=h-pad-(v/max)*(h-2*pad);
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
  const aire=`${pad},${h-pad} ${pts} ${(pad+step*(values.length-1)).toFixed(1)},${h-pad}`;
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
   <polygon points="${aire}" fill="var(--accent)" opacity=".12" stroke="none"/>
   <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2"
    stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
async function chargerMetriques(n){
  if(!n) return;
  const a=appParNom(n), externe=!!(a && a.externe);
  try{
    const r=await fetch('/api/metrics/'+encodeURIComponent(n));
    const d=await r.json();
    if(!r.ok) throw new Error(d.error||'Mesures indisponibles.');
    const cpu=d.points.map(p=>Number(p.cpu)||0), mem=d.points.map(p=>Number(p.mem)||0);
    $('mt-cpu').innerHTML=sparklineSvg(cpu);
    $('mt-mem').innerHTML=sparklineSvg(mem);
    const dernier=(l)=>l.length?l[l.length-1]:0;
    $('mt-cpu-val').textContent = externe
      ? Math.round(dernier(cpu)) + ' ms' : dernier(cpu).toFixed(1) + '%';
    $('mt-mem-val').textContent = externe
      ? (dernier(mem) >= 100 ? 'En ligne' : 'Ne répond pas')
      : Math.round(dernier(mem)) + ' Mo';
  }catch(e){
    $('mt-cpu').innerHTML='<div class="chart-empty" style="padding:16px">Mesures indisponibles.</div>';
    $('mt-mem').innerHTML='<div class="chart-empty" style="padding:16px">Mesures indisponibles.</div>';
    $('mt-cpu-val').textContent='—';
    $('mt-mem-val').textContent='—';
  }
}

/* ---------------- fiche : journal en direct ---------------- */
let logEs=null, logLines=[], logNom=null;
function ouvrirLog(n){
  if(logEs && logNom===n) return;   /* deja ouvert sur la bonne application */
  fermerLog();
  logNom=n; logLines=[];
  $('lg-body').textContent='Connexion au flux...';
  logEs=new EventSource('/api/logs/'+encodeURIComponent(n)+'/stream');
  let premier=true;
  logEs.onmessage=(e)=>{
    if(premier){ $('lg-body').textContent=''; premier=false; }
    logLines.push(e.data);
    if(logLines.length>2000) logLines.splice(0, logLines.length-2000);
    renderLogFilter();
  };
  /* Sans ce gestionnaire, un flux refuse laissait la fenetre sur
     « Connexion... » indefiniment, sans rien dire. Le refus le plus probable
     est le plafond de flux simultanes : le message le nomme, parce que
     « erreur » tout court n'aide personne a le resoudre. */
  logEs.onerror=()=>{
    if(!premier) return;   /* deja des lignes affichees : simple reconnexion */
    $('lg-body').textContent =
      "Le journal n'a pas pu être suivi.\n\n"
      + "Cause la plus fréquente : trop de journaux suivis en même temps, "
      + "depuis un autre onglet ou une autre machine. Ferme-en un et reviens ici.";
  };
}
function renderLogFilter(){
  const q=($('lg-search').value||'').toLowerCase();
  const lignes=q?logLines.filter(l=>l.toLowerCase().includes(q)):logLines;
  const b=$('lg-body');
  const enBas = b.scrollTop+b.clientHeight >= b.scrollHeight-40;
  b.textContent=lignes.join('\n');
  if(enBas) b.scrollTop=b.scrollHeight;
}
function viderLog(){ logLines=[]; renderLogFilter(); }
function fermerLog(){
  if(logEs){ logEs.close(); logEs=null; }
  logNom=null;
}

/* ---------------- pop-up : nouvelle application ---------------- */
let cur='', ROOT='';
function slug(s){ return (s||'').trim().toLowerCase().replace(/[^a-z0-9_-]/g,'-'); }

/* Les commandes codelab du poste : l'onglet Aide des parametres. */
function ouvrirAide(){ showSection('parametres'); paramOnglet('aide'); }

function openAdd(){
  $('a-e').style.display='none';
  ['f-name','f-cmd','f-build','f-mem','f-desc'].forEach(i=>$(i).value='');
  $('a-s').textContent=ROOT;
  optionsCategorie('f-cat', '');
  /* Sans adresse publique declaree, une application neuve nait privee, et le
     choix ne s'affiche pas : le serveur refuserait de toute facon. */
  $('f-vis-zone').style.display = adressePublique ? '' : 'none';
  setVisAdd(adressePublique ? 'publique' : 'privee');
  $('ov-add').classList.add('show');
  browse(ROOT);
  $('f-name').oninput=()=>{ $('f-url').textContent='/'+(slug($('f-name').value)||'nom')+'/'; };
  $('f-url').textContent='/nom/';
  setTimeout(()=>$('f-name').focus(), 60);
}

async function browse(p){
  const d=await (await fetch('/api/browse?path='+encodeURIComponent(p))).json();
  cur=d.path; $('fb-c').textContent=d.label;
  const zone=$('fb-l');
  if(!peuplerDossiers(zone, d, browse))
    lignePlate(zone, 'Dossier vide', 'var(--dim)');
  if(d.hasIndex)
    zone.insertBefore(Object.assign(document.createElement('div'),
      {textContent:'\u2713 index.html présent dans ce dossier',
       style:'color:var(--ok);cursor:default'}), zone.firstChild);
  {
    const dd=await (await fetch('/api/detect?path='+encodeURIComponent(cur))).json();
    if(dd.command){
      $('suggest').style.display='block';
      let h='Suggestion détectée &mdash; lancement : <b>'+esc(dd.command)+'</b>';
      if(dd.build_command) h+='<br>build : <b>'+esc(dd.build_command)+'</b>';
      $('suggest').innerHTML=h+' (cliquer pour appliquer)';
      $('suggest').dataset.cmd=dd.command;
      $('suggest').dataset.build=dd.build_command||'';
    } else { $('suggest').style.display='none'; }
  }
}

let visAdd='publique', visEdit='publique';
function peindreVis(id, valeur){
  document.querySelectorAll('#'+id+' button').forEach(b=>b.classList.toggle('on', b.dataset.v===valeur));
}
function setVisAdd(v){ visAdd=v; peindreVis('f-vis-toggle', v); }
function setVisEdit(v){ visEdit=v; peindreVis('e-vis-toggle', v); }

function applySuggestion(){ $('f-cmd').value=$('suggest').dataset.cmd||''; $('f-build').value=$('suggest').dataset.build||''; }
function err(m){ const e=$('a-e'); e.textContent=m; e.style.display='block'; }

async function submitAdd(){
  const name=$('f-name').value.trim(), c=$('f-cmd').value.trim();
  const build_command=$('f-build').value.trim();
  const description=$('f-desc').value.trim();
  const max_memory_mb=$('f-mem').value ? parseInt($('f-mem').value, 10) : null;
  if(!name) return err('Donne un nom au projet.');
  if(!c) return err('Indique la commande de lancement.');
  $('a-e').style.display='none';

  const r=await fetch('/api/add',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({name,path:cur,command:c,description,build_command,
     categorie:$('f-cat').value, max_memory_mb,visibility:visAdd})});
  const d=await r.json();
  if(!r.ok) return err(d.error);
  hide('ov-add'); refresh(); chargerHub();
}
