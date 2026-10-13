/* ===========================================================================
   CodeLab -- LE SCRIPT DU PANNEAU (dashboard.html)
   ---------------------------------------------------------------------------
   ROLE        Tout ce que fait la page du panneau : navigation entre les
               rubriques, hub et widgets, vue d'ensemble, fiches, comptes,
               reglages. Il n'AUTORISE rien : chaque action passe par l'API,
               qui verifie le droit (SECURITE.md, regle 15).
   CHARGE PAR  dashboard.html, en fin de page, apres le petit script en ligne
               qui pose window.CODELAB_PAGE : les six valeurs que le serveur
               ecrit a chaque affichage (page_du_panneau dans app.py) --
               jeton anti-falsification, modele d'adresse des applications,
               racine, role, nom du compte, envoi des mails regle.
   SERVI PAR   /panneau.js (app.py), a une session seulement, avec
               l'empreinte du fichier dans l'adresse (?v=...) : le navigateur
               le garde en cache jusqu'au deploiement suivant.
   CONTRAINTES - Aucune valeur propre a la session ici : ce fichier est le
                 meme pour tous, et il est mis en cache. Une valeur nouvelle
                 passe par CODELAB_PAGE.
               - Les tests lisent ce fichier comme s'il etait encore dans la
                 page (voir _page_panneau dans test_regressions.py).
   STRUCTURE   theme, colonne de gauche, menu du compte, navigation, listes
               deroulantes, onglets des parametres, etats, categories, hub et
               widgets, vue d'ensemble, donnees, actions, fiche (acces,
               configuration, logo, metriques, journal), pop-up, cles
               d'acces, journal des acces, carte de chaleur, comptes,
               confirmation et notifications, invitations, adresse mail,
               alertes, securite, sauvegardes, version, preferences.
   CHOIX       - Sorti de la page (il y tenait en ligne) : mis en cache entre
                 deux deploiements, colore par les editeurs, verifie par
                 node --check sans extraction.
   A AMELIORER - Le decouper en modules (hub, fiche, comptes, reglages) :
                 demanderait de remplacer les fonctions globales appelees
                 par les attributs onclick/oninput du balisage.
   =========================================================================== */
/* ------------------------------ jeton CSRF ------------------------------
   Injecte par le serveur au moment de servir la page, et rattache ICI a
   fetch une fois pour toutes plutot qu'a la trentaine d'appels qui ecrivent.
   Un appel ajoute demain porte le jeton sans que personne y pense -- c'est
   la seule facon de ne pas en oublier un.
   Les GET n'en ont pas besoin : ils ne changent rien, et c'est exactement
   la ligne que trace le serveur. */
const JETON=CODELAB_PAGE.jeton;

/* Ou vivent les applications, « {nom} » a remplacer : un sous-domaine
   (https://blog.toi.duckdns.org/) quand le panneau est ouvert par son nom de
   domaine, le port des applications en local, ou un chemin du panneau.
   Pose par le serveur (modele_adresse_application). */
const APPS_MODELE=CODELAB_PAGE.appsModele;
const urlApplication = nom => APPS_MODELE.replace('{nom}', encodeURIComponent(nom));

/* OUVRIR UNE APPLICATION : une seule adresse, pour tout le panneau.
   La tuile du hub, le bouton « Ouvrir » de la fiche et la ligne « Adresse »
   menaient au meme endroit par trois expressions recopiees -- et le jour ou
   l'une a change, les autres sont restees. Elles passent maintenant par ici.

   Un service externe passe par la PORTE DU PANNEAU (/<nom>/) et non par sa
   propre adresse : cette porte verifie le droit d'entrer, journalise
   l'ouverture, puis redirige. Y aller en direct, c'est court-circuiter les
   deux et tomber sur la page d'erreur nue du service quand le droit manque.
   Une application, elle, vit sur l'origine des applications. */
/* Dans le panneau INSTALLE (ecran d'accueil), une application ouverte
   directement sortirait de l'app : elle vit sur une autre origine, et iOS
   l'afficherait dans son navigateur integre. Elle s'ouvre donc encadree dans
   une page du panneau (/ouvrir.html, voir ouvrir_dans_le_panneau). */
/* Le mode a ete lu une fois, en tete de page (mode.js) : on ne le redevine pas. */
const enModeApp = window.CODELAB_MODE_APP === true;
/* Une tuile « interne » (Messages) est une page du panneau : elle s'ouvre a
   son adresse, sans passer par le sous-domaine d'une application. */
const lienOuverture = a => a.interne ? a.url
  : a.externe
  ? '/' + encodeURIComponent(a.name) + '/'
  : (enModeApp ? '/ouvrir.html?app=' + encodeURIComponent(a.name)
               : urlApplication(a.name));
const fetchNu = window.fetch.bind(window);

/* Le jeton ne part QUE vers ce panneau. Aujourd'hui tous les appels de cette
   page sont relatifs, donc la question ne se pose pas -- mais une enveloppe
   qui ajoute l'en-tete sans regarder la cible n'est juste que par accident :
   le jour ou quelqu'un ecrit fetch('https://...', {method:'POST'}), le jeton
   de session part chez un tiers. Un en-tete non standard declenche certes un
   pre-vol CORS, mais un serveur hostile n'a qu'a l'autoriser. */
function memeOrigine(cible){
  try{
    const url = (typeof Request !== 'undefined' && cible instanceof Request)
      ? cible.url : String(cible);
    return new URL(url, location.href).origin === location.origin;
  }catch(e){
    /* Cible illisible : on n'ajoute rien. Ne pas envoyer le jeton fait au
       pire echouer un appel, l'envoyer au mauvais endroit le divulgue. */
    return false;
  }
}

window.fetch = function(cible, options){
  options = options || {};
  const m = (options.method || 'GET').toUpperCase();
  if(m==='GET' || m==='HEAD' || !memeOrigine(cible)) return fetchNu(cible, options);
  /* Headers peut arriver en objet simple, en tableau de paires ou en
     Headers : on normalise avant d'ajouter, sinon on ecrase le
     Content-Type que l'appelant vient de poser. */
  const h = new Headers(options.headers || {});
  h.set('X-CodeLab-Jeton', JETON);
  return fetchNu(cible, Object.assign({}, options, {headers: h}));
};

/* SERVEUR INJOIGNABLE. Rien ne reagissait a une perte de connexion : les
   boutons echouaient un par un, sans dire pourquoi. Une requete du panneau
   qui n'aboutit pas (reseau coupe, serveur arrete, 502/504 d'un relais
   sans reference du panneau) allume un bandeau ; la page garde ce qu'elle
   affiche, retente toutes les 5 s, et se remet a jour quand le serveur
   revient. */
const fetchAvecJeton = window.fetch;
let horsLigneDepuis=0, horsLigneMinuterie=0, horsLigneCompte=0;
window.fetch = function(cible, options){
  const p = fetchAvecJeton(cible, options);
  if(!memeOrigine(cible)) return p;
  return p.then(r=>{
    if((r.status===502 || r.status===504) && !r.headers.get('X-CodeLab-Ref')) passerHorsLigne();
    else if(horsLigneDepuis) revenirEnLigne();
    return r;
  }, e=>{
    if(!e || e.name!=='AbortError') passerHorsLigne();
    throw e;
  });
};
function passerHorsLigne(){
  if(horsLigneDepuis) return;
  horsLigneDepuis=Date.now();
  $('hors-ligne').hidden=false;
  compterAvantEssai();
}
function compterAvantEssai(){
  clearTimeout(horsLigneMinuterie);
  horsLigneCompte=5;
  const tic=()=>{
    if(!horsLigneDepuis) return;
    if(horsLigneCompte<=0){ essayerConnexion(); return; }
    $('hors-ligne-texte').textContent='Serveur injoignable, nouvel essai dans '+horsLigneCompte+' s.';
    horsLigneCompte--;
    horsLigneMinuterie=setTimeout(tic, 1000);
  };
  tic();
}
async function essayerConnexion(){
  clearTimeout(horsLigneMinuterie);
  $('hors-ligne-texte').textContent='Connexion…';
  try{
    const r=await fetchNu('/health', {cache:'no-store'});
    if(r.ok){ revenirEnLigne(); return; }
  }catch(e){}
  compterAvantEssai();
}
function revenirEnLigne(){
  if(!horsLigneDepuis) return;
  horsLigneDepuis=0; clearTimeout(horsLigneMinuterie);
  $('hors-ligne').hidden=true;
  notifier('Connexion rétablie.');
  /* Ce qui a pu changer pendant la coupure : on relit la vue affichee. */
  if(sectionCourante==='hub') chargerHub();
  else if(estAdmin && ['overview','apps','app'].includes(sectionCourante)) refresh();
}
/* L'en-tete colle de la fiche prend un trait quand le contenu passe dessous. */
function majEnteteCollee(){
  if(sectionCourante!=='app') return;
  const t=document.querySelector('#sec-app .fiche-tete');
  if(t) t.classList.toggle('collee', t.getBoundingClientRect().top<=53 && defileur().scrollTop>0);
}
window.addEventListener('scroll', majEnteteCollee, {passive:true});
document.getElementById('defile').addEventListener('scroll', majEnteteCollee, {passive:true});
window.addEventListener('offline', passerHorsLigne);
window.addEventListener('online', ()=>{ if(horsLigneDepuis) essayerConnexion(); });

/* LES ERREURS DU NAVIGATEUR. Une exception JavaScript dans cette page ne se
   voyait nulle part : ni dans les journaux du panneau, ni ailleurs -- un
   bouton cessait de repondre, sans trace. Elles partent maintenant au
   serveur, qui les ecrit dans « docker logs codelab-app-manager ». Dix au
   plus par chargement de page, sans doublon : une erreur dans une boucle de
   rafraichissement ne doit pas inonder le journal. */
const erreursVues = new Set();
function remonterErreur(message, source, ligne, colonne, pile){
  const cle = message + '|' + source + '|' + ligne;
  if(erreursVues.has(cle) || erreursVues.size >= 10) return;
  erreursVues.add(cle);
  try{
    fetch('/api/erreur-navigateur', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message:String(message||'').slice(0,500),
        source:String(source||'').slice(0,300), ligne:ligne||0, colonne:colonne||0,
        pile:String(pile||'').slice(0,2000), page:location.pathname+location.hash})
    }).catch(()=>{});
  }catch(e){}
}
window.addEventListener('error', ev=>{
  remonterErreur(ev.message, ev.filename, ev.lineno, ev.colno, ev.error && ev.error.stack);
});
window.addEventListener('unhandledrejection', ev=>{
  const r = ev.reason;
  remonterErreur('Promesse rejetée : ' + ((r && r.message) || r), '', 0, 0, r && r.stack);
});

const $=i=>document.getElementById(i), hide=i=>$(i).classList.remove("show");
/* CE QUI DEFILE. Au doigt, c'est #defile, pas la page : iOS ne laisse
   masquer la barre de defilement que d'un bloc interieur, jamais celle de la
   page entiere (theme.css). A la souris, la page, comme d'habitude. */
const DEFILE_INTERNE=matchMedia('(pointer:coarse)');
const defileur=()=>DEFILE_INTERNE.matches ? $('defile') : document.scrollingElement;
/* Echappe le TEXTE et les VALEURS D'ATTRIBUT, et rien d'autre.
   L'apostrophe est ajoutee pour les attributs ecrits entre apostrophes.

   Ce qu'elle ne fait PAS, et ne peut pas faire : proteger une chaine de
   PROGRAMME construite dans un attribut, comme onclick="f('...')". Le
   navigateur decode les entites AVANT de lire le JavaScript : &#39; y
   redevient une apostrophe, qui referme la chaine. La seule parade est de
   ne pas fabriquer de programme a partir d'une donnee -- voir ACTIONS plus
   bas, et le navigateur de dossiers, qui construit du DOM. */
/* LE NOM QU'ON LIT, ET LA CLE QUI DESIGNE. Depuis que le nom accepte les
   majuscules et les espaces, les deux ne sont plus la meme chaine : « Mon
   Site » s'affiche ainsi et se sert sur /mon-site/. Tout ce qui DESIGNE une
   application -- data-cible, une URL, une case a cocher -- garde la cle ;
   seul le texte affiche passe par ici. Une inscription d'avant la separation
   n'a pas de titre, et sa cle fait office de nom : rien a migrer. */
const nomAffiche = a => (a && a.titre) || (a && a.name) || '';

const esc=s=>(s||"").replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',
                                             '"':'&quot;',"'":'&#39;'}[c]));
const icone=n=>'/api/icon/'+encodeURIComponent(n);

/* ---------------- les actions du HTML genere ----------------
   UN seul ecouteur, et zero gestionnaire en ligne portant une donnee.

   Avant, chaque bouton d'une liste s'ecrivait
   un onclick appelant tg() avec le nom interpole entre apostrophes : la
   donnee traversait deux analyseurs (le
   HTML, puis le JavaScript), et il fallait que les deux echappements soient
   justes. C'est ce qui a laisse passer une execution de code par un nom de
   dossier -- avec une fonction esc() qui pourtant echappait bien ce qu'elle
   annoncait.

   Ici la donnee reste une DONNEE : elle voyage dans un attribut data-, que
   le navigateur ne lit jamais comme du programme, et le nom de l'action est
   choisi dans une table fermee. Un nom d'application avec une apostrophe, un
   chevron ou une balise entiere ne peut plus rien declencher. */
const ACTIONS = {
  'app-basculer':    n => tg(n),
  'app-ouvrir':      n => ouvrirFiche(n),
  'ap-etat':         n => apChoisirEtat(n),
  'ap-trier':        n => apTrier(n),
  'bw-sujet':        n => { hubMessageCible=n; peindreHub(); },
  'presentation':    n => ouvrirPresentation(n),
  'menu-app':        (n, d, bouton) => ouvrirMenuApp(n, bouton),
  'menu-ouvrir':     n => actionMenuApp('ouvrir', n),
  'menu-presentation': n => actionMenuApp('presentation', n),
  'menu-copier':     n => actionMenuApp('copier', n),
  'menu-reglages':   n => actionMenuApp('reglages', n),
  'menu-ranger':     n => actionMenuApp('ranger', n),
  'pj-retirer':      (cle, d) => { const p=piecesBrouillon[cle] && piecesBrouillon[cle][Number(d.rang)];
                                   if(!p) return;
                                   piecesBrouillon[cle].splice(Number(d.rang),1); peindrePieces(cle); },
  'hub-retirer':     n => hubRetirer(n),
  'hub-deplacer':    (n,d) => hubDeplacer(n, d.sens),
  'hub-taille':      (n,d) => hubTailleWidget(n, d.taille),
  'galerie-choisir': n => { galerieChoix=n; peindreGalerie(); },
  'galerie-ajouter': (n,d) => galerieAjouter(n, d.taille, d.app),
  'app-redemarrer':  n => restartApp(n),
  'app-build':       n => buildApp(n),
  'app-visibilite':  n => basculerVisibilite(n),
  'passkey-retirer': id => pkSupprimer(id),
  'compte-ouvrir':   n => usOuvrir(n),
  'invitation-renvoyer': id => invRenvoyer(id),
  'invitation-annuler':  id => invAnnuler(id),
  'invitation-copier':   (_, d) => copierLien(d.lien),
  'param-onglet':    o => paramOnglet(o),
  'appareil-deconnecter': (id, d) => apDeconnecter(id, d.courant==='1'),
  'ver-copier':      () => copierLien(COMMANDES_MAJ, 'Commandes copiées.'),
};

document.addEventListener('click', e => {
  /* closest() trouve le noeud le PLUS PROCHE : un bouton pose dans une ligne
     cliquable gagne sur la ligne, et l'ancien event.stopPropagation() de
     chaque bouton disparait avec. */
  const n = e.target.closest('[data-agir]');
  const agir = n && ACTIONS[n.dataset.agir];
  if(!agir) return;
  e.preventDefault();
  agir(n.dataset.cible || '', n.dataset, n);
});

/* Une ligne cliquable (role="button") repond aussi au clavier. */
document.addEventListener('keydown', e => {
  if(e.key!=='Enter' && e.key!==' ') return;
  const n = e.target.closest && e.target.closest('[data-agir][role="button"]');
  if(!n || n!==e.target) return;
  e.preventDefault(); n.click();
});

document.addEventListener('change', e => {
  const n = e.target.closest('[data-changer]');
  if(n && n.dataset.changer === 'app-masquer')
    basculerMasquage(n.dataset.cible, !e.target.checked);
  if(n && n.dataset.changer === 'presentation-visible')
    basculerPresentation(n.dataset.cible, e.target.checked);
  if(n && n.dataset.changer === 'presentation-fichier')
    choisirVideo(n.dataset.cible, e.target.value);
});

/* ---------------- theme ---------------- */
function applyTheme(t){
  if(t==='auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  document.querySelectorAll('#theme-toggle button').forEach(b=>b.classList.toggle('active', b.dataset.t===t));
  setTimeout(majBarreEtat, 0);
}
/* LE CHOIX DOIT SORTIR DE CETTE ORIGINE. Les applications sont servies sur
   un autre port, donc une autre origine, donc un autre localStorage : le
   diagnostic -- qui est une page de CodeLab, pas une application invitee --
   ne pouvait pas savoir si tu avais choisi le clair ou le sombre, et suivait
   le reglage du systeme.

   Un COOKIE, lui, appartient a l'HOTE et ignore le port : il traverse la
   separation des origines sans rien lui retirer. Ce qu'il porte est une
   preference d'affichage, pas un secret -- et c'est bien pour cela qu'on
   peut le partager, la ou le cookie de session est justement retire avant
   d'atteindre une application. */
function noterThemePartage(t){
  try{ document.cookie='codelab-theme='+encodeURIComponent(t)
    + ';path=/;max-age=31536000;SameSite=Lax'; }catch(e){}
}
function setTheme(t){
  localStorage.setItem('codelab-theme', t);
  noterThemePartage(t);
  applyTheme(t);
}
const themeChoisi = localStorage.getItem('codelab-theme')||'auto';
applyTheme(themeChoisi);
/* Pose aussi au chargement : une installation deja en place a son choix dans
   le localStorage et n'a aucune raison de le refaire pour que les
   applications le voient. */
noterThemePartage(themeChoisi);

/* ---------------- les reglages du hub ----------------
   Chacun est un attribut de <html>, pose par le serveur des le premier
   octet (cookie codelab-hub, voir REGLAGES_HUB dans app.py) : rien ne
   clignote au chargement. La premiere valeur de chaque reglage est celle
   par defaut. */
const REGLAGES_HUB = {tuiles:['cartes','icones'], defilement:['horizontal','vertical'],
  widgets:['melanges','haut'], couleurs:['lagon','ocean','foret','prune','corail','ardoise']};
function reglageHub(cle){
  const v = document.documentElement.getAttribute('data-'+cle);
  return REGLAGES_HUB[cle].includes(v) ? v : REGLAGES_HUB[cle][0];
}
function majReglagesHub(){
  document.querySelectorAll('#hub-reglages [data-r]').forEach(b=>
    b.setAttribute('aria-pressed', reglageHub(b.dataset.r)===b.dataset.v));
}
function appliquerReglagesHub(reglages){
  const racine = document.documentElement;
  for(const [cle, v] of Object.entries(reglages))
    if(REGLAGES_HUB[cle] && REGLAGES_HUB[cle].includes(v)) racine.setAttribute('data-'+cle, v);
  const valeurs = Object.keys(REGLAGES_HUB).map(reglageHub).join('.');
  try{ document.cookie='codelab-hub='+valeurs+';path=/;max-age=31536000;SameSite=Lax'; }catch(e){}
  majSensDuHub();
  majReglagesHub();
  setTimeout(majBarreEtat, 0);
  /* Le hub change de FORME : il se redessine -- une fois charge seulement,
     pour ne pas afficher « aucune application ». */
  if(hubApps.length || hubServices.length) peindreHub();
}
function setHub(cle, valeur){ appliquerReglagesHub({[cle]: valeur}); }
majReglagesHub();

/* ---------------- colonne de gauche ---------------- */
function applySidebar(state){ $('sidebar').classList.toggle('collapsed', state==='collapsed'); }
const ecranMobile = () => window.matchMedia('(max-width:760px)').matches;
function toggleSidebar(){
  /* Sur un telephone, le menu s'ouvre par-dessus la page au lieu de se
     reduire a une colonne d'icones. */
  if(ecranMobile()){
    const ouvert=!$('sidebar').classList.contains('ouvert');
    $('sidebar').classList.toggle('ouvert', ouvert);
    $('voile-menu').classList.toggle('show', ouvert);
    return;
  }
  const next = $('sidebar').classList.contains('collapsed') ? 'expanded' : 'collapsed';
  try{ localStorage.setItem('codelab-sidebar', next); }catch(e){}
  applySidebar(next);
}
function fermerMenuMobile(){
  $('sidebar').classList.remove('ouvert');
  $('voile-menu').classList.remove('show');
}
applySidebar(localStorage.getItem('codelab-sidebar')||'expanded');

/* ---------------- menu du compte ---------------- */
function toggleAcctMenu(){ $('acct-menu').classList.toggle('show'); }
function allerA(section){ $('acct-menu').classList.remove('show'); showSection(section); }
document.addEventListener('click', e=>{
  const wrap=document.querySelector('.acct-wrap');
  if(wrap && !wrap.contains(e.target)) $('acct-menu').classList.remove('show');
});

/* ---------------- navigation ---------------- */
/* Le hub est l'accueil de tout le monde, administrateur compris : c'est la
   page qui lance les applications. Le reste -- vue d'ensemble, applications,
   configuration -- s'ouvre depuis le menu lateral ou le menu du compte. */
function showSection(name){
  /* Un compte utilisateur n'a que le hub et ses parametres personnels. Ce
     garde-fou n'est pas une protection -- les routes verifient le role de leur
     cote -- c'est ce qui evite qu'un clic egare mene a une page vide et a des
     appels refuses. */
  if(!estAdmin && name!=='hub' && name!=='parametres' && name!=='message') name='hub';
  /* L'administrateur lit les messages dans l'application Messages. */
  if(estAdmin && name==='message'){ location.href='/messages'; return; }
  fermerMenuMobile();
  document.querySelectorAll('.section').forEach(s=>{ s.style.display = (s.id==='sec-'+name) ? '' : 'none'; });
  /* La fiche d'une application appartient a "Applications" : l'entree du menu
     doit rester allumee quand on y descend. */
  const surligne = (name==='app') ? 'apps' : name;
  document.querySelectorAll('.side-item').forEach(t=>t.classList.toggle('active', t.dataset.sec===surligne));
  $('acct-btn').classList.toggle('active', name==='parametres');

  /* Un flux de journal occupe une place cote serveur : on le ferme des qu'on
     quitte la fiche, sans attendre que le navigateur s'en apercoive. */
  if(name!=='app') fermerLog();
  if(name==='hub'){ chargerHub(); if(hubDisposition===null) chargerDisposition(); }
  if(name!=='hub') hubEdition=false;
  document.documentElement.classList.toggle('hub-actif', name==='hub');
  if(name==='overview' || name==='apps') refresh();
  if(name==='overview') chargerCarte();
  /* L'etat de securite est relu a chaque ouverture : il peut avoir change
     depuis un autre onglet, ou au redemarrage du service. */
  if(name==='users'){ chargerUtilisateurs(); chargerInvitations(); chargerActivite(); }
  /* Chaque onglet des parametres relit ce qu'il affiche a son ouverture : ces
     etats changent depuis un autre onglet du navigateur, ou au redemarrage du
     service. On ne recharge que l'onglet regarde. */
  if(name==='parametres') paramOnglet(paramOngletActif);
  /* La liste des applications vient du hub : on la relit, sinon le choix
     « D'une application » est vide pour qui arrive directement ici. */
  if(name==='message'){ chargerHub(); suiviConsulte(); $('msg-err').style.display='none';
                        $('msg-ok').style.display='none'; plierLesCartes(); }
  sectionCourante=name;
  majAdresse();
}

/* ---------------- adresses propres ----------------
   Chaque rubrique a son adresse : /parametres/securite, /applications/site,
   /utilisateurs... Recharger la page y ramene, « Precedent » y revient, et
   un lien se partage. Le serveur sert la meme page pour toutes ces adresses
   (page_du_panneau) ; c'est ici qu'on lit l'adresse et qu'on ouvre la bonne
   rubrique. */
const ADRESSES={hub:'/', overview:'/apercu', apps:'/applications', users:'/utilisateurs',
                parametres:'/parametres', message:'/messages'};
let sectionCourante='hub', routageEnCours=false;

function adresseCourante(){
  if(sectionCourante==='app' && ficheNom)
    return '/applications/'+encodeURIComponent(ficheNom)
      + (ficheOngletActif && ficheOngletActif!=='general' ? '/'+ficheOngletActif : '');
  if(sectionCourante==='parametres') return '/parametres/'+paramOngletActif;
  return ADRESSES[sectionCourante]||'/';
}
function majAdresse(){
  if(routageEnCours) return;
  const a=adresseCourante();
  if(a!==location.pathname) history.pushState(null, '', a);
}
async function routerDepuisAdresse(){
  routageEnCours=true;
  try{
    const m=location.pathname.split('/').filter(Boolean).map(x=>{
      try{ return decodeURIComponent(x); }catch(e){ return ''; }
    });
    if(m[0]==='parametres'){
      if(m[1]) paramOngletActif=m[1];
      showSection('parametres');
    }else if(m[0]==='applications' && m[1] && estAdmin){
      if(!apps.length) await refresh();
      ficheOngletActif = m[2] || 'general';
      if(appParNom(m[1])) ouvrirFiche(m[1]); else showSection('apps');
    }else{
      const sec=Object.keys(ADRESSES).find(k=>ADRESSES[k]==='/'+(m[0]||''));
      showSection(sec||'hub');
    }
  }finally{ routageEnCours=false; }
  /* Une adresse inconnue, ou refusee a ce compte, est corrigee sans
     ajouter d'etape a l'historique. */
  const a=adresseCourante();
  if(a!==location.pathname) history.replaceState(null, '', a);
}
window.addEventListener('popstate', ()=>routerDepuisAdresse());

/* ---------------- les cartes de reglages se plient ----------------
   Une seule fonction pour toutes les cartes de tous les onglets, present et
   a venir : ecrire la structure repliable a la main dans chaque carte aurait
   demande de ne jamais l'oublier dans la suivante.

   Ce qu'elle fait d'une carte : le titre devient la poignee, tout ce qui le
   suit part dans un corps qu'on montre ou qu'on cache. Repliee par defaut ;
   l'etat est retenu par navigateur, comme le theme et la colonne de gauche.

   La cle de memorisation est le TEXTE du titre, et non un numero de
   position : reordonner les cartes ne doit pas rouvrir celles qu'on avait
   fermees. */
function pliCle(carte){
  const b=carte.querySelector('.card-name b');
  return 'codelab.pli.'+((b&&b.textContent)||'').trim().slice(0,60);
}

function pliBasculer(carte){
  const ouverte=!carte.classList.contains('ouverte');
  carte.classList.toggle('ouverte', ouverte);
  carte.querySelector('.card-top').setAttribute('aria-expanded', ouverte?'true':'false');
  try{ localStorage.setItem(pliCle(carte), ouverte?'1':'0'); }catch(e){}
}

function plierLesCartes(){
  /* .sans-pli : une carte que son interrupteur resume, rien a deplier. */
  document.querySelectorAll('.onglet-p .settings-card:not(.pliable):not(.sans-pli)').forEach(carte=>{
    const tete=carte.querySelector('.card-top');
    if(!tete) return;
    /* UNE CARTE SEULE DANS SON ONGLET NE SE REPLIE PAS. Le pli sert a passer
       devant ce qu'on ne vient pas regler ; quand il n'y a rien d'autre, il
       ne fait que cacher la seule chose de la page derriere un clic. */
    const onglet=carte.closest('.onglet-p');
    if(onglet && onglet.querySelectorAll('.settings-card').length < 2) return;
    /* Tout ce qui suit le titre devient le corps. On deplace les noeuds
       plutot que de recopier du HTML : les champs gardent leur valeur, et
       les gestionnaires poses dessus restent attaches. */
    const corps=document.createElement('div');
    corps.className='card-corps';
    while(tete.nextSibling) corps.appendChild(tete.nextSibling);
    carte.appendChild(corps);

    /* La pastille d'etat remonte a cote du titre : c'est ce qu'on vient
       verifier avant meme de decider d'ouvrir. */
    const chev=document.createElement('span');
    chev.className='chevron';
    chev.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
      +' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
      +'<path d="M9 18l6-6-6-6"/></svg>';
    tete.appendChild(chev);

    carte.classList.add('pliable');
    tete.setAttribute('role','button');
    tete.setAttribute('tabindex','0');
    tete.addEventListener('click', ()=>pliBasculer(carte));
    /* Au clavier comme a la souris : une poignee qui ne repond qu'au pointeur
       enferme dehors ceux qui naviguent en tabulation. */
    tete.addEventListener('keydown', e=>{
      if(e.key==='Enter' || e.key===' '){ e.preventDefault(); pliBasculer(carte); }
    });

    let ouverte=false;
    try{ ouverte = localStorage.getItem(pliCle(carte))==='1'; }catch(e){}
    carte.classList.toggle('ouverte', ouverte);
    tete.setAttribute('aria-expanded', ouverte?'true':'false');
  });
}

/* ==================== LES LISTES DEROULANTES ====================
   Un habillage, pas un remplacement : chaque <select> garde sa valeur, ses
   options et son evenement « change ». On lui pose devant un bouton et une
   liste dessines avec les jetons du theme, et l'on retire le menu du
   systeme -- le seul morceau d'interface que CSS ne sait pas atteindre.

   La liste se RECONSTRUIT a chaque ouverture, depuis le <select> : les
   options arrivent souvent apres coup (les applications, les comptes, les
   annees du journal), et un habillage fige afficherait le contenu d'il y a
   trois secondes. */
const CHEVRON = '<svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>';

function dlEtiquette(select){
  const o = select.options[select.selectedIndex];
  return o ? o.textContent : '';
}

function dlFermerTout(sauf){
  document.querySelectorAll('.dl.ouvert').forEach(d=>{ if(d!==sauf) d.classList.remove('ouvert'); });
}

function dlPeindre(enveloppe){
  const select = enveloppe.querySelector('select');
  const liste = enveloppe.querySelector('.dl-liste');
  liste.innerHTML = [...select.options].map((o,i)=>
    `<div class="dl-opt" role="option" data-i="${i}"
      aria-selected="${i===select.selectedIndex}"
      aria-disabled="${o.disabled}">${esc(o.textContent)}</div>`).join('');
  enveloppe.querySelector('.dl-btn span').textContent = dlEtiquette(select);
  enveloppe.dataset.fige = select.disabled ? '1' : '0';
}

function dlChoisir(enveloppe, i){
  const select = enveloppe.querySelector('select');
  if(select.options[i] && !select.options[i].disabled){
    select.selectedIndex = i;
    /* L'evenement doit venir du <select> lui-meme : tout le script ecoute
       « change » sur lui, et les attributs onchange= deja poses dans la page
       ne connaissent rien d'autre. */
    select.dispatchEvent(new Event('change', {bubbles:true}));
  }
  enveloppe.classList.remove('ouvert');
  dlPeindre(enveloppe);
  enveloppe.querySelector('.dl-btn').focus();
}

function dlHabiller(select){
  if(select.dataset.dl==='1' || select.multiple) return;
  select.dataset.dl='1';
  const enveloppe = document.createElement('div');
  enveloppe.className = 'dl';
  select.parentNode.insertBefore(enveloppe, select);
  enveloppe.appendChild(select);
  enveloppe.insertAdjacentHTML('beforeend',
    '<button type="button" class="dl-btn" aria-haspopup="listbox" aria-expanded="false">'
    + '<span></span>' + CHEVRON + '</button>'
    + '<div class="dl-liste" role="listbox"></div>');

  const bouton = enveloppe.querySelector('.dl-btn');
  bouton.addEventListener('click', ()=>{
    if(select.disabled) return;
    const ouvrir = !enveloppe.classList.contains('ouvert');
    dlFermerTout(enveloppe);
    if(ouvrir) dlPeindre(enveloppe);           // toujours a jour a l'ouverture
    enveloppe.classList.toggle('ouvert', ouvrir);
    bouton.setAttribute('aria-expanded', ouvrir ? 'true' : 'false');
    /* Assez de place en dessous ? Sinon la liste s'ouvre vers le haut. */
    const bas = enveloppe.getBoundingClientRect().bottom;
    enveloppe.classList.toggle('vers-le-haut',
      ouvrir && window.innerHeight - bas < 200 && bas > 260);
  });
  enveloppe.querySelector('.dl-liste').addEventListener('click', e=>{
    const opt = e.target.closest('.dl-opt');
    if(opt && opt.getAttribute('aria-disabled')!=='true') dlChoisir(enveloppe, +opt.dataset.i);
  });
  /* Au clavier : les fleches parcourent, Entree choisit, Echap ferme. Sans
     cela, un habillage rendrait une liste inutilisable sans souris. */
  bouton.addEventListener('keydown', e=>{
    const ouvert = enveloppe.classList.contains('ouvert');
    if(e.key==='Escape'){ enveloppe.classList.remove('ouvert'); return; }
    if(e.key==='ArrowDown' || e.key==='ArrowUp'){
      e.preventDefault();
      if(!ouvert){ bouton.click(); return; }
      const opts=[...enveloppe.querySelectorAll('.dl-opt')];
      let i = opts.findIndex(o=>o.classList.contains('vise'));
      if(i<0) i = select.selectedIndex;
      i = Math.max(0, Math.min(opts.length-1, i + (e.key==='ArrowDown'?1:-1)));
      opts.forEach(o=>o.classList.remove('vise'));
      opts[i].classList.add('vise');
      opts[i].scrollIntoView({block:'nearest'});
    }
    if(e.key==='Enter' && ouvert){
      e.preventDefault();
      const vise = enveloppe.querySelector('.dl-opt.vise');
      dlChoisir(enveloppe, vise ? +vise.dataset.i : select.selectedIndex);
    }
  });
  /* Le script remplit souvent les options apres coup, ou pose .value a la
     main : l'etiquette doit suivre sans que chaque appel ait a y penser. */
  new MutationObserver(()=>dlPeindre(enveloppe)).observe(select, {childList:true});
  select.addEventListener('change', ()=>dlPeindre(enveloppe));
  dlPeindre(enveloppe);
}

function habillerLesListes(racine){
  (racine||document).querySelectorAll('select:not([data-dl])').forEach(dlHabiller);
}
document.addEventListener('click', e=>{ if(!e.target.closest('.dl')) dlFermerTout(null); });
document.addEventListener('keydown', e=>{ if(e.key==='Escape') dlFermerTout(null); });
/* Les listes nees d'un rendu ulterieur -- une fiche qu'on ouvre, un tableau
   repeint -- sont habillees des qu'elles paraissent. */
new MutationObserver(m=>{
  for(const x of m){
    for(const n of x.addedNodes){
      if(n.nodeType!==1) continue;
      if(n.tagName==='SELECT') dlHabiller(n);
      else habillerLesListes(n);
    }
  }
}).observe(document.body, {childList:true, subtree:true});
habillerLesListes(document);

/* ---------------- onglets des parametres ---------------- */
let paramOngletActif='compte';
/* Le sommaire des reglages d'un compte utilisateur : on descend jusqu'a la
   section, sans toucher a l'adresse. */
/* UN ONGLET PAR CATEGORIE, les memes pour tout le monde : Compte,
   Apparence, Mon hub, Securite -- et E-mail et alertes pour
   l'administrateur. Un compte utilisateur avait une longue page a sections,
   ou l'on cherchait sa rubrique en faisant defiler. */
const ONGLETS_UTILISATEUR=['compte','apparence','hub','securite'];
function paramOnglet(o){
  if(!estAdmin && !ONGLETS_UTILISATEUR.includes(o)) o='compte';
  /* Un onglet inconnu (un lien garde, une faute de frappe) ouvre le
     compte plutot qu'une page vide. */
  if(!$('param-'+o) || $('param-onglets').querySelector('[data-p="'+o+'"]')?.style.display==='none') o='compte';
  paramOngletActif=o;
  if(sectionCourante==='parametres') majAdresse();
  document.querySelectorAll('#param-onglets button')
    .forEach(b=>b.classList.toggle('active', b.dataset.p===o));
  document.querySelectorAll('.onglet-p')
    .forEach(n=>n.classList.toggle('actif', n.id==='param-'+o));
  plierLesCartes();
  if(o==='compte'){
    chargerMonCompte();
    if(estAdmin) chargerVersion();
  }
  if(o==='apparence'){ chargerNotifications(); peindreInstallation(); }
  if(o==='hub'){
    /* La liste des masquees vient du hub : on la relit, sinon on affiche
       celle d'un chargement precedent -- ou rien du tout si l'on arrive
       directement sur les reglages. */
    chargerHub();
    if(estAdmin) chargerCategories();
  }
  if(o==='securite'){
    $('mdp-err').style.display='none'; $('mdp-msg').style.display='none';
    chargerPasskeys();
    chargerAppareils();
    if(estAdmin) chargerSauvegardes();
    if(estAdmin) chargerSecurite();
  }
  /* La liste des applications vient du hub : on la relit, sinon le choix
     « D'une application » est vide pour qui arrive directement ici. */
  /* Les deux onglets lisent la meme reponse : le serveur d'envoi et les
     alertes vivent au meme endroit, le coffre chiffre du panneau. */
  if(o==='email' || o==='alertes') chargerAlertes();
}

/* ---------------- etats ---------------- */
/* Trois etats distincts : en ligne, demarree mais qui ne repond pas encore, et
   arretee. Les confondre ferait cliquer sur un lien qui ne mene nulle part. */
function etatApp(a){
  /* L'ETAT NE REGARDE QUE L'ADMINISTRATEUR. Un compte utilisateur ne sait
     jamais ce qui marche ou non : le serveur ne lui envoie pas l'etat, et
     la page ne le devine pas. Une application en panne s'ouvre sur une page
     « momentanement inaccessible ». */
  if(!estAdmin) return ['', ''];
  if(a.crash_looping) return ['err','En erreur'];
  /* « Arretee » supposerait qu'on l'a arretee : un service de la stack ne se
     demarre pas depuis ici, il repond ou il ne repond pas. Et un service qui
     ne repond pas est un souci (ambre), pas un arret volontaire (gris) :
     sinon le widget « Etat » le comptait parmi ce qui va bien. */
  if(!a.running) return a.externe ? ['warn','Injoignable'] : ['off','Arrêtée'];
  if(a.listening === false) return ['warn','Ne répond pas'];
  return ['on','En ligne'];
}
function badgeEtat(a){ const [c,t]=etatApp(a); return t ? `<span class="pill ${c}"><i></i>${t}</span>` : ''; }
function badgeVis(a){
  return a.visibility==='privee'
    ? '<span class="pill priv"><i></i>Privé</span>'
    : '<span class="pill pub"><i></i>Public</span>';
}



/* ---------------- categories ---------------- */
/* Un rangement, rien de plus : aucune categorie ne donne de droit. La liste
   est gardee en memoire pour peupler les deux menus deroulants (ajout et
   fiche) sans un aller-retour reseau a chaque ouverture. */
let categories=[];
/* « CodeLab » : la categorie des applications de la stack (Dagster, Mailpit,
   Messages, le Diagnostic), posee par le serveur. Montree en tete, jamais
   modifiable ni proposee pour un projet. */
let categoriesVerrouillees=['CodeLab'];
const categoriesLibres = () => categories.filter(c=>!categoriesVerrouillees.includes(c));

async function chargerCategories(){
  try{
    const d=await (await fetch('/api/categories')).json();
    categories=d.categories||[];
    if(Array.isArray(d.verrouillees)) categoriesVerrouillees=d.verrouillees;
  }catch(e){ return; }
  peindreCategories();
}

/* Les deux menus deroulants d'un projet. La categorie enregistree est
   proposee meme si elle vient de disparaitre de la liste : on ne veut pas
   qu'un simple passage sur la fiche la remplace en douce. */
function optionsCategorie(id, valeur){
  const libres=categoriesLibres();
  const liste = valeur && !libres.includes(valeur) && !categoriesVerrouillees.includes(valeur)
    ? libres.concat([valeur]) : libres;
  $(id).innerHTML = '<option value="">Sans catégorie</option>'
    + liste.map(c=>`<option value="${esc(c)}"${c===valeur?' selected':''}>${esc(c)}</option>`).join('');
}

/* La liste editable de Configuration. Elle vit dans le DOM : ce qu'on lit au
   moment d'enregistrer est exactement ce qui est affiche, ordre compris. */
function peindreCategories(){
  const libres=categoriesLibres();
  $('cat-liste').innerHTML = categoriesVerrouillees.map(c=>`<div class="cat-ligne cat-verrou">
        <svg class="cat-cadenas" viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>
        <div class="cat-verrou-txt"><b>${esc(c)}</b><span>Les applications de CodeLab réservées à l'administrateur : Dagster, Mailpit, Messages, Diagnostic. Posée d'office, non modifiable.</span></div>
      </div>`).join('')
    + (libres.length
    ? libres.map((c,i)=>`<div class="cat-ligne">
        <input value="${esc(c)}" maxlength="30" aria-label="Catégorie ${i+1}">
        <button class="icon-btn" title="Retirer" onclick="catRetirer(${i})">
          <svg viewBox="0 0 24 24"><path d="M6 6l12 12"/><path d="M18 6L6 18"/></svg></button>
      </div>`).join('')
    : '<div class="cat-vide">Aucune autre catégorie pour l’instant.</div>');
}
function catLues(){
  return Array.from(document.querySelectorAll('#cat-liste .cat-ligne:not(.cat-verrou) input'))
    .map(i=>i.value.trim()).filter(Boolean);
}
function catAjouter(){
  const v=$('cat-neuve').value.trim();
  if(!v) return;
  if(categoriesVerrouillees.some(c=>c.toLowerCase()===v.toLowerCase())){
    notifier('« '+v+' » est réservée aux applications de CodeLab.','err'); return; }
  categories = categoriesVerrouillees.concat(catLues(), [v]);
  $('cat-neuve').value='';
  peindreCategories();
}
function catRetirer(i){
  const l=catLues(); l.splice(i,1); categories=categoriesVerrouillees.concat(l); peindreCategories();
}
async function catEnregistrer(){
  const e=$('cat-err'), m=$('cat-msg');
  e.style.display='none'; m.style.display='none';
  $('cat-save').disabled=true;
  try{
    const r=await fetch('/api/categories',{method:'PUT',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({categories:catLues()})});
    const d=await r.json();
    if(!r.ok){ e.textContent=d.error||'Enregistrement impossible.'; e.style.display='block'; return; }
    categories=d.categories||[];
    if(Array.isArray(d.verrouillees)) categoriesVerrouillees=d.verrouillees;
    peindreCategories();
    /* Supprimer une categorie deplace des projets : le dire, plutot que de
       laisser decouvrir le changement au prochain passage sur le hub. */
    m.textContent = d.declasses
      ? `Enregistré. ${d.declasses} projet${d.declasses>1?'s':''} ${d.declasses>1?'passent':'passe'} sous « Autres ».`
      : 'Enregistre.';
    m.style.display='block';
    if(estAdmin) refresh();
  } finally { $('cat-save').disabled=false; }
}

/* ---------------- hub ---------------- */
let hubApps=[];
/* ---------------- les messages recus (administrateur) ----------------
   Ils se traitent dans l'application « Messages » (/messages) : boite de
   reception, tableau des taches, projets. Le panneau ne fait plus que les
   GUETTER, pour annoncer dans le coin celles qui arrivent pendant qu'on
   travaille ailleurs. */
let msgDernierVu=0, msgPremierChargement=true;

async function chargerMessages(){
  if(!estAdmin) return;
  let d;
  try{ d=await (await fetch('/api/messages')).json(); }catch(e){ return; }
  msgDernierVu = Math.max(msgDernierVu, ...(d.messages||[]).map(m=>m.ts||0), 0);
  msgPremierChargement=false;
}

/* UNE DEMANDE QUI ARRIVE SE VOIT, ou que l'on soit dans le panneau. La page
   demande toutes les vingt secondes ce qui est arrive DEPUIS ce qu'elle a
   deja vu -- pas la liste entiere -- et l'annonce dans le coin. */
async function guetterMessages(){
  if(!estAdmin || msgPremierChargement) return;
  let d;
  try{
    d=await (await fetch('/api/messages?apres='+encodeURIComponent(msgDernierVu))).json();
  }catch(e){ return; }
  const neufs=d.messages||[];
  if(!neufs.length) return;
  msgDernierVu = Math.max(msgDernierVu, ...neufs.map(m=>m.ts||0));
  const premier=neufs[0];
  notifierMessage(neufs.length===1
    ? 'Nouvelle demande de « ' + (premier.qui||'?') + ' ».'
    : neufs.length + ' nouveaux messages.');
  /* La tuile « Messages » du hub porte le compte : on la repeint. */
  if(sectionCourante==='hub') chargerHub();
}

/* ---------------- un mot a l'administrateur ---------------- */
/* Plus de bouton « Écrire » : la rubrique a un onglet a elle, et l'ouvrir
   EST le geste. Un repli de plus sous un onglet deja choisi ne cachait plus
   rien d'encombrant -- il ajoutait un clic. */
function msgCible(){
  /* La liste ne propose que les applications du hub de CE compte : on ne
     peut parler que de ce qu'on peut ouvrir. */
  const zone=$('msg-app-zone');
  if(!zone) return;
  const app = $('msg-cible').value==='application';
  zone.style.display = app ? '' : 'none';
  const noms=(hubApps||[]).map(a=>a.name).sort();
  $('msg-app').innerHTML = noms.length
    ? noms.map(n=>`<option value="${esc(n)}">${esc(nomAffiche(hubApps.find(a=>a.name===n)))}</option>`).join('')
    : '<option value="">aucune application accessible</option>';
}

async function msgEnvoyer(){
  const b=$('msg-envoyer'), e=$('msg-err'), ok=$('msg-ok');
  e.style.display='none'; ok.style.display='none';
  const pieces=piecesPretes('form'); if(pieces===null) return;
  const corps={cible:$('msg-cible').value, texte:$('msg-texte').value.trim(),
               app:$('msg-app').value||'', pieces};
  if(!corps.texte && !pieces.length){ e.textContent='Écris ton message.'; e.style.display='block'; return; }
  b.disabled=true; b.textContent='Envoi...';
  try{
    const r=await fetch('/api/messages',{method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(corps)});
    const d=await r.json();
    if(!r.ok){ e.textContent=d.error||'Envoi impossible.'; e.style.display='block'; }
    else{
      $('msg-texte').value=''; viderPieces('form');
      /* On dit ce qui s'est REELLEMENT passe : le message est range dans
         tous les cas, le mail depend d'un serveur d'envoi configure. */
      ok.textContent = d.envoye
        ? 'Message envoyé à ton administrateur. Merci.'
        : "Message enregistré. Aucun serveur d'envoi n'est configuré : ton "
          + 'administrateur le verra dans le panneau.';
      ok.style.display='block';
      chargerSuivi(true);
    }
  }catch(err){ e.textContent='Erreur réseau.'; e.style.display='block'; }
  finally{ b.disabled=false; b.textContent='Envoyer'; }
}

/* ---------------- les pieces jointes ----------------
   Une capture ou une maquette part avec le message. Rien a apprendre : le
   selecteur de fichiers du systeme, un collage (une capture d'ecran), ou un
   glisser sur le message. Chaque fichier se depose aussitot ; le message
   n'emporte que leurs identifiants. */
const PIECES_MAX=6, PIECE_MAX_OCTETS=10*1024*1024;
const piecesBrouillon={form:[], widget:[]};
const SVG_TROMBONE='<svg viewBox="0 0 24 24"><path d="M21 11.5l-8.6 8.6a5 5 0 01-7.1-7.1l8.6-8.6a3.3 3.3 0 014.7 4.7l-8.6 8.6a1.7 1.7 0 01-2.4-2.4l7.9-7.9"/></svg>';
function vignettePiece(p){
  const image=(p.type||'').startsWith('image/');
  /* La vignette vient du serveur une fois le fichier depose : la page
     n'affiche que des images de sa propre origine (CSP). */
  const src=p.id ? '/api/pieces/'+encodeURIComponent(p.id) : '';
  return image && src ? `<img src="${esc(src)}" alt="" loading="lazy">`
    : `<i class="pj-doc">${esc(((p.nom||'').split('.').pop()||'').toUpperCase().slice(0,4))}</i>`;
}
function piecesHtml(cle){
  return piecesBrouillon[cle].map((p,i)=>`<div class="pj${p.etat==='envoi'?' envoi':''}" title="${esc(p.nom)}">${vignettePiece(p)}
    <span title="${esc(p.nom)}">${esc(p.nom)}</span>
    <button type="button" aria-label="Retirer ${esc(p.nom)}" data-agir="pj-retirer" data-cible="${cle}" data-rang="${i}">×</button></div>`).join('');
}
function peindrePieces(cle){
  if(cle==='form') remplir($('pj-form'), piecesHtml('form'));
  else if(sectionCourante==='hub') peindreHub();
}
async function deposerPieces(cle, fichiers){
  for(const f of [...(fichiers||[])]){
    if(piecesBrouillon[cle].length>=PIECES_MAX){ notifier(PIECES_MAX+' pièces jointes au plus.','err'); break; }
    if(f.size>PIECE_MAX_OCTETS){ notifier(f.name+' : 10 Mo au maximum.','err'); continue; }
    const p={nom: f.name || 'capture.png', type: f.type};
    p.etat = 'envoi';
    piecesBrouillon[cle].push(p); peindrePieces(cle);
    try{
      const r=await fetch('/api/pieces',{method:'POST', body:f,
        headers:{'X-Nom-Fichier':encodeURIComponent(p.nom), 'Content-Type':f.type||'application/octet-stream'}});
      if(r.ok) Object.assign(p, await r.json(), {etat:'ok'});
      else{ notifier(await echec(r,'Pièce refusée.'),'err'); piecesBrouillon[cle]=piecesBrouillon[cle].filter(x=>x!==p); }
    }catch(e){ notifier('Envoi de la pièce impossible.','err'); piecesBrouillon[cle]=piecesBrouillon[cle].filter(x=>x!==p); }
    peindrePieces(cle);
  }
}
/* Ce qui part avec le message : null tant qu'un depot n'est pas fini. */
function piecesPretes(cle){
  if(piecesBrouillon[cle].some(p=>p.etat!=='ok')){ notifier('Une pièce est encore en cours d’envoi.','err'); return null; }
  return piecesBrouillon[cle].map(p=>p.id);
}
function viderPieces(cle){
  piecesBrouillon[cle]=[]; peindrePieces(cle);
}
/* Coller une capture, ou glisser des fichiers, sur le texte d'un message. */
document.addEventListener('paste', e=>{
  const cle=e.target && e.target.dataset && e.target.dataset.pieces; if(!cle) return;
  const fichiers=[...(e.clipboardData && e.clipboardData.files || [])];
  if(!fichiers.length) return;
  e.preventDefault(); deposerPieces(cle, fichiers);
});
document.addEventListener('dragover', e=>{
  const t=e.target && e.target.closest && e.target.closest('[data-pieces]'); if(!t) return;
  e.preventDefault(); t.classList.add('depot-survol');
});
document.addEventListener('dragleave', e=>{
  const t=e.target && e.target.closest && e.target.closest('[data-pieces]'); if(t) t.classList.remove('depot-survol');
});
document.addEventListener('drop', e=>{
  const t=e.target && e.target.closest && e.target.closest('[data-pieces]'); if(!t) return;
  e.preventDefault(); t.classList.remove('depot-survol');
  deposerPieces(t.dataset.pieces, e.dataTransfer && e.dataTransfer.files);
});

/* ---------------- le suivi de mes messages ----------------
   Ce que chaque message est devenu, deduit cote serveur de ce que
   l'administrateur en fait dans Messages. Un message qui a avance depuis la
   derniere visite porte une pastille ; ouvrir la liste les eteint. */
let mesMessages=null, mesNouveaux=0, suiviLu=0;
const NOM_ETAPE={envoye:'Envoyé', lu:'Lu', etude:'À l’étude', planifie:'Planifié',
                 en_cours:'En cours', fait:'Réalisé', refuse:'Non retenu'};
const RANG_ETAPE={envoye:0, lu:1, etude:1, planifie:2, en_cours:3, fait:4};
function sujetSuivi(m){
  if(m.cible==='application') return 'Application · '+m.app;
  return m.cible==='idee' ? 'Nouveau projet' : 'Le hub';
}
function dateCourte(iso){
  const d=new Date(typeof iso==='number' ? iso*1000 : iso+'T12:00:00');
  return isNaN(d) ? '' : d.toLocaleDateString('fr-FR',{day:'numeric', month:'long'});
}
async function chargerSuivi(force){
  if(estAdmin) return;
  if(!force && Date.now()-suiviLu<60000 && mesMessages) return;
  suiviLu=Date.now();
  try{ const d=await (await fetch('/api/mes-messages')).json();
       mesMessages=d.messages||[]; mesNouveaux=d.nouveaux||0; }catch(e){ return; }
  peindreSuivi();
  if(sectionCourante==='hub') peindreHub();
}
function etapesSuivi(etape){
  if(etape==='refuse') return '';
  const r=RANG_ETAPE[etape]||0;
  const noms=['Envoyé', etape==='etude' ? 'À l’étude' : 'Lu', 'Planifié', 'En cours', 'Réalisé'];
  return `<ol class="suivi-etapes" aria-label="Avancement">${noms.map((n,i)=>
    `<li class="${i<r ? 'passe' : i===r ? 'ici' : ''}" ${i===r ? 'aria-current="step"' : ''}>${n}</li>`).join('')}</ol>`;
}
function itemSuivi(m){
  const s=m.suivi, detail=[];
  if(s.projet) detail.push(`Projet <b>${esc(s.projet)}</b>`);
  if(s.total) detail.push(`Tâches faites : <b>${s.faites}/${s.total}</b>`);
  if(s.prevu) detail.push(`Prévu pour le <b>${dateCourte(s.prevu)}</b>`);
  if(s.fait_le) detail.push(`Réalisé le <b>${dateCourte(s.fait_le)}</b>`);
  return `<div class="suivi-item ${m.nouveau?'nouveau':''}">
    <div class="suivi-tete"><span class="suivi-sujet">${esc(sujetSuivi(m))}</span>
      <span class="suivi-date">${depuis(m.ts)}</span>
      ${m.nouveau ? '<span class="suivi-nouveau">Du nouveau</span>' : ''}
      <span class="suivi-etat e-${s.etape}">${NOM_ETAPE[s.etape]||''}</span></div>
    ${m.texte ? `<p class="suivi-texte">${esc(m.texte)}</p>` : ''}
    ${(m.pieces||[]).length ? `<div class="pj-liste">${m.pieces.map(p=>`<a class="pj" href="/api/pieces/${encodeURIComponent(p.id)}">${vignettePiece(p)}<span>${esc(p.nom)}</span></a>`).join('')}</div>` : ''}
    ${etapesSuivi(s.etape)}
    ${detail.length ? `<div class="suivi-detail">${detail.map(x=>`<span>${x}</span>`).join('')}</div>` : ''}
    ${s.taches.length ? `<ul class="suivi-taches">${s.taches.slice(0,6).map(t=>
      `<li class="${t.faite?'faite':''}"><i>${t.faite?'✓':'•'}</i>${esc(t.titre)}</li>`).join('')}</ul>` : ''}
  </div>`;
}
function peindreSuivi(){
  majPastilleIcone();
  const b=$('side-count-suivi');
  if(b){ b.textContent=mesNouveaux; b.style.display=mesNouveaux ? '' : 'none'; }
  if(!mesMessages) return;
  remplir($('suivi-liste'), mesMessages.length ? mesMessages.map(itemSuivi).join('')
    : '<p class="hint">Aucun message envoyé pour l’instant.</p>');
}
/* La liste est ouverte : ce qu'elle montre est vu. Les cartes gardent leur
   mise en avant jusqu'a la prochaine visite, la pastille s'eteint. */
async function suiviConsulte(){
  await chargerSuivi(true);
  if(!mesNouveaux) return;
  try{ await fetch('/api/mes-messages/vus',{method:'POST'}); }catch(e){ return; }
  mesNouveaux=0;
  const b=$('side-count-suivi'); if(b) b.style.display='none';
  if(sectionCourante==='hub') peindreHub();
}
function widgetSuivi(taille){
  const liste=mesMessages||[];
  const actifs=liste.filter(m=>!['fait','refuse'].includes(m.suivi.etape));
  const titre='<div class="bw-titre">Mes messages</div>';
  if(!liste.length) return titre+'<p class="bw-vide">Aucun message envoyé.</p>';
  const n=mesNouveaux;
  if(taille==='petit') return titre+`<div><div class="bento-chiffre">${n||actifs.length}</div>
    <p>${n ? (n>1 ? 'mises à jour' : 'mise à jour') : 'en cours'}</p></div>`;
  return titre+`<div class="bw-liste">${liste.slice(0,3).map(m=>`<div><span>${esc(sujetSuivi(m))}</span>
      <span class="suivi-etat e-${m.suivi.etape}">${NOM_ETAPE[m.suivi.etape]||''}</span></div>`).join('')}</div>
    <button type="button" class="bento-bouton" onclick="allerA('message')">${n ? n+' nouveauté'+(n>1?'s':'') : 'Tout voir'} ${SVG_FLECHE}</button>`;
}

/* ---------------- le hub, en grille et seulement en grille ----------------
   Il offrait deux tailles de tuile, retenues par navigateur. C'etait une
   decision de plus a prendre pour une page ou l'on ne vient que pour ouvrir
   une application : la grille d'icones montre d'un coup d'oeil ce qui est
   la, et l'etat d'une application se lit dans « Applications », pas ici. */

let hubMasquees=[], hubServices=[], hubServicesMasques=[];

/* Au-dela de ce nombre d'applications, le reglage d'affichage se replie
   derriere une ligne qu'on deroule. Huit : c'est a peu pres ce qui tient
   sous les yeux sans faire defiler la page. */
const MASQUAGE_SEUIL_DEROULANT = 12;

/* Une seule bascule pour les deux sens : cocher retire l'application du hub,
   decocher l'y remet. Le serveur ne connait que deux verbes sur la meme
   adresse, POST pour masquer et DELETE pour afficher. */
async function basculerMasquage(nom, masquer){
  try{
    const r=await fetch('/api/mes-apps/'+encodeURIComponent(nom)+'/masquer',
      {method: masquer ? 'POST' : 'DELETE'});
    if(!r.ok) throw new Error();
    await chargerHub();
    if(masquer) notifier('« '+nom+' » masquée de ton hub.', 'ok',
                         {libelle:'Annuler', faire:()=>basculerMasquage(nom, false)});
    else notifier('« '+nom+' » est de retour dans ton hub.');
  }catch(e){
    notifier("Impossible de changer l'affichage de cette application.",'err');
    /* La case a deja bouge sous le doigt : on redessine depuis le serveur
       plutot que de laisser un reglage affiche qui n'existe pas. */
    chargerHub();
  }
}

/* TOUTES les applications du compte, masquees comprises : c'est la liste
   entiere qui se coche et se decoche. N'afficher que les masquees obligeait
   a en retirer une depuis le hub avant de pouvoir la retrouver ici. */
function peindreMasquees(){
  const zone=$('masquees-liste');
  if(!zone) return;
  const cachees = new Set((hubMasquees||[]).concat(hubServicesMasques||[])
    .map(a=>a.name));
  /* Les services de la stack figurent ici comme les applications : c'est la
     liste de ce que MON hub affiche, et Dagster y occupe une tuile comme les
     autres. Ils restent hors de hubApps partout ailleurs -- le choix « de
     quelle application veux-tu parler » n'a toujours rien a en faire. */
  const toutes = (hubApps||[]).concat(hubMasquees||[])
    .concat(hubServices||[]).concat(hubServicesMasques||[])
    .slice().sort((a,b)=>nomAffiche(a).localeCompare(nomAffiche(b),'fr'));
  if(!toutes.length){
    zone.innerHTML='<div class="hint">Aucune application à ranger pour le moment.</div>';
    return;
  }
  /* RETENUE VEUT DIRE AFFICHEE, et pas l'inverse. La case cochee signifiait
     « masquee » : on selectionnait ce qu'on voulait faire disparaitre, et la
     ligne retenue etait celle qu'on ne verrait plus. En choisissant ce qu'on
     GARDE, la liste se lit comme le hub qu'elle decrit. */
  /* UNE LIGNE, UN NOM. Chaque ligne portait l'icone, le nom, la description
     complete et le mot « Affichée » ou « Masquée » : quatre informations pour
     un reglage qui en a une, et une liste deux fois plus haute que la carte
     qui la contient. La description est deja sur la tuile du hub, et l'etat
     est dans la case -- cochee veut dire affichee, c'est tout le propos. Il
     ne reste que ce qu'on vient chercher : le nom, et la case. */
  const lignes = toutes.map(a=>{
    const cachee = cachees.has(a.name);
    return `<label class="choix-ligne masquee-ligne">
    <input type="checkbox"${cachee?'':' checked'}
      data-changer="app-masquer" data-cible="${esc(a.name)}">
    <img src="${icone(a.name)}" alt="">
    <b class="masquee-txt">${esc(nomAffiche(a))}</b>
  </label>`;}).join('');

  /* AU-DELA DE HUIT, LA LISTE SE DEROULE. Quinze applications faisaient de
     ce reglage la moitie de la page des parametres, et repoussaient tout le
     reste hors de l'ecran -- pour une liste qu'on ouvre trois fois par an.
     Repliee, elle tient en une ligne qui dit l'essentiel : combien sont
     masquees. En dessous de huit, dérouler n'economise rien et ajoute un
     clic. */
  if(toutes.length <= MASQUAGE_SEUIL_DEROULANT){
    zone.innerHTML = lignes;
    return;
  }
  const nb = cachees.size;
  const resume = nb
    ? `${nb} masquée${nb>1?'s':''} sur ${toutes.length} applications`
    : `${toutes.length} applications, aucune masquée`;
  /* L'etat ouvert/ferme se garde d'un rafraichissement a l'autre : la liste
     est redessinee a chaque changement de case, et elle se refermerait sous
     le doigt a chaque clic. */
  const ouverte = zone.querySelector('details') ? zone.querySelector('details').open : false;
  zone.innerHTML = `<details class="masquee-repli"${ouverte?' open':''}>
    <summary><span>${esc(resume)}</span>
      <svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg></summary>
    <div class="masquee-corps">${lignes}</div></details>`;
}

async function chargerHub(){
  try{
    const d=await (await fetch('/api/mes-apps')).json();
    hubApps=d.apps||[];
    /* Les services de la stack -- Dagster, Mailpit -- vivent a part :
       ils se peignent dans la meme grille, mais ils ne sont pas des
       applications declarees. Les meler a hubApps les ferait entrer dans la
       liste de ce qu'on masque et dans le choix « de quelle application
       veux-tu parler », ou ils n'ont rien a faire. */
    hubServices=d.services||[];
    hubServicesMasques=d.services_masques||[];
    hubMasquees=d.masquees||[];
    peindreMasquees();
    chargerSuivi();
    msgCible();
    /* Les categories arrivent avec les projets : une seule requete, et le
       hub ne peut pas afficher un rangement decale d'un tour. */
    if(Array.isArray(d.categories)) categories=d.categories;
    hubSauvegardes=d.sauvegardes||null;
  }catch(e){ return; }
  peindreHub();
  chargerPouls();
}
/* NE REPEINDRE QUE CE QUI CHANGE. Le hub se recharge toutes les dix secondes ;
   remplacer le HTML a chaque tour, meme identique, recreait toutes les tuiles
   et leurs images : l'ecran clignotait. On compare au dernier HTML pose. */
const _dernierHtml = new WeakMap();
function remplir(el, html){
  if(!el || _dernierHtml.get(el) === html) return;
  _dernierHtml.set(el, html);
  el.innerHTML = html;
}

function peindreHub(){
  const filtre = ($('hub-filtre').value||'').trim().toLowerCase();
  const correspond = a => !filtre
    || (nomAffiche(a)+' '+(a.description||'')).toLowerCase().includes(filtre);

  /* Applications et services dans la meme grille : on vient au hub pour
     OUVRIR quelque chose, et ce qui l'ouvre ne change rien a l'endroit ou
     l'oeil le cherche. Dagster, Mailpit et Messages ont la meme tuile
     qu'une application. Dans l'ordre que le compte a compose (mode
     Modifier) ; les applications jamais rangees suivent, par nom. */
  const toutes = hubApps.concat(hubServices);
  const liste=toutes.filter(correspond)
    .sort((a,b)=>rangDansLeHub(a.name)-rangDansLeHub(b.name) || a.name.localeCompare(b.name,'fr'));
  /* Une liste silencieusement plus courte inquiete plus qu'elle ne
     simplifie : le hub dit ce qu'il cache, et ou le retrouver. */
  const nbCaches = (hubMasquees||[]).length;
  $('hub-masquees').style.display = nbCaches ? 'block' : 'none';
  if(nbCaches){
    remplir($('hub-masquees'), nbCaches + (nbCaches>1
      ? ' applications masquées — ' : ' application masquée — ')
      + '<a href="#" onclick="event.preventDefault();allerA(\'parametres\');'
      + 'paramOnglet(\'compte\')">à décocher dans tes paramètres</a> pour '
      + (nbCaches>1 ? 'les' : 'la') + ' revoir.');
  }
  peindreBento(liste, filtre);
}

/* Le nombre de messages non lus, sur la tuile « Messages ». */
const pastille = a => a.non_lues
  ? `<span class="hub-pastille" title="${a.non_lues} nouvelle${a.non_lues>1?'s':''} demande${a.non_lues>1?'s':''}">${a.non_lues}</span>` : '';

/* STYLE 4 : LA MOSAIQUE. Sur un ecran large, elle DEFILE A L'HORIZONTALE et
   jamais a la verticale : trois rangees qui tiennent dans la hauteur de la
   fenetre, des colonnes qui s'ajoutent sur la droite. Sur un telephone, elle
   redevient une colonne qu'on fait defiler vers le bas.

   ELLE SE COMPOSE, comme l'ecran d'accueil d'un telephone ou les widgets d'un
   Mac : « Modifier » fait trembler les tuiles, qu'on glisse ou qu'on deplace
   aux fleches ; « + » ouvre la galerie des widgets ; chaque widget change de
   taille sur place. La disposition est gardee par compte, cote serveur. */
let hubDisposition=null, hubCatalogue=[], hubEdition=false, hubMessageBrouillon='';
async function chargerDisposition(){
  try{
    const d=await (await fetch('/api/hub/disposition')).json();
    hubDisposition=d.elements||[]; hubCatalogue=d.catalogue||[];
  }catch(e){ if(hubDisposition===null) hubDisposition=[]; }
  if(hubApps.length || hubServices.length) peindreHub();
}
const cleElement = e => e.t==='a' ? 'a:'+e.id : 'w:'+e.id+':'+(e.app||'');
/* La disposition, completee des applications qu'on n'a pas encore rangees :
   elles viennent a la suite, par ordre alphabetique. */
function elementsDuHub(){
  const toutes=hubApps.concat(hubServices);
  const rangees=new Set((hubDisposition||[]).filter(e=>e.t==='a').map(e=>e.id));
  return (hubDisposition||[]).filter(e=>e.t!=='a' || toutes.some(a=>a.name===e.id))
    .concat(toutes.filter(a=>!rangees.has(a.name))
      .sort((x,y)=>nomAffiche(x).localeCompare(nomAffiche(y),'fr')).map(a=>({t:'a', id:a.name})));
}
/* Le rang d'une application dans la disposition : la recherche garde
   l'ordre que le compte a compose. */
function rangDansLeHub(nom){ const i=elementsDuHub().findIndex(e=>e.t==='a' && e.id===nom); return i<0 ? 1e6 : i; }
async function enregistrerDisposition(elements){
  hubDisposition=elements; peindreHub();
  const r=await fetch('/api/hub/disposition',{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({elements})});
  if(!r.ok){ notifier(await echec(r,'Disposition non enregistrée.'),'err'); chargerDisposition(); }
}

/* L'application « a la une » : la derniere ouverte par ce compte (le serveur
   le sait, d'un appareil a l'autre), sinon celle retenue par ce navigateur,
   sinon la premiere en ligne. */
function appALaUne(apps){
  const vues = apps.filter(a=>a.ouverte_le).sort((x,y)=>y.ouverte_le-x.ouverte_le);
  if(vues.length) return [vues[0], true];
  let d=null; try{ d=localStorage.getItem('codelab-hub-derniere'); }catch(e){}
  const locale = apps.find(a=>a.name===d);
  if(locale) return [locale, true];
  return [apps.find(a=>a.running) || apps[0] || null, false];
}
function depuis(ts){
  const s=Math.max(0, Date.now()/1000-(ts||0));
  if(s<60) return "à l'instant";
  if(s<3600) return 'il y a '+Math.floor(s/60)+' min';
  if(s<86400) return 'il y a '+Math.floor(s/3600)+' h';
  const j=Math.floor(s/86400); return j===1 ? 'hier' : 'il y a '+j+' jours';
}
/* « Nouvelle version » : l'application a ete deployee, rebuildee ou a recu
   un commit depuis la derniere fois que CE compte l'a ouverte. */
const badgeMaj = a => a.nouvelle_version
  ? '<span class="badge-maj" title="Nouvelle version depuis ta dernière ouverture">Nouvelle version</span>' : '';
const SVG_FLECHE='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';

/* ---- les widgets : peu, et tous sur le hub ou ses applications ---- */
function widgetApercu(a, taille, surtitre, enDirect=true){
  if(!a) return `<div class="bw-corps"><div class="bw-titre">${esc(surtitre)}</div>
    <p class="bw-vide">Ouvre une application : elle s'affichera ici.</p></div>`;
  /* L'APERCU est la page de l'application elle-meme, reduite et inerte : on
     la reconnait d'un coup d'oeil, et un clic l'ouvre en grand. Arretee, elle
     n'a rien a montrer : son icone la remplace. */
  /* Un compte utilisateur ne connait pas l'etat : l'apercu montre toujours
     la page (la page neutre si l'application ne repond pas). */
  /* « A la une », l'application qui a une video de presentation la joue en
     boucle, sans le son, comme une bande-annonce (videoALaUne). */
  const video = !enDirect && videoALaUne(a);
  const apercu = enDirect && (a.running || !estAdmin) && !a.externe && !a.interne
    ? `<iframe src="${esc(urlApplication(a.name))}" title="Aperçu de ${esc(nomAffiche(a))}"
         loading="lazy" tabindex="-1" aria-hidden="true" referrerpolicy="no-referrer"
         sandbox="allow-scripts allow-same-origin allow-forms"></iframe>`
    : `<img class="bw-une-icone" src="${icone(a.name)}" alt="">${video}`;
  return `<div class="bw-apercu${enDirect ? '' : ' sans-direct'}${video ? ' en-video' : ''}"${enDirect ? '' : ` data-teinte="${esc(a.name)}"`}
      data-garder="apercu-${esc(a.name)}-${a.running?1:0}-${taille}-${enDirect?1:0}-${video ? a.presentation.maj : 0}">${apercu}</div>
    <a class="bw-une-pied" href="${lienOuverture(a)}" data-app="${esc(a.name)}">
      <span class="bw-sur">${esc(surtitre)}</span>
      <span class="bw-une-ligne"><img src="${icone(a.name)}" alt=""><span class="bw-une-nom">${esc(nomAffiche(a))}</span>${badgeMaj(a)}</span></a>`;
}
/* La bande-annonce : la video de presentation, muette, en boucle, lancee
   seule (muted + playsinline : iOS l'accepte). Pas quand l'appareil demande
   moins d'animations ou economise ses donnees : l'icone reste. */
function videoALaUne(a){
  if(!a.presentation) return '';
  try{
    if(matchMedia('(prefers-reduced-motion: reduce)').matches) return '';
    if(navigator.connection && navigator.connection.saveData) return '';
  }catch(e){}
  const base='/api/presentations/'+encodeURIComponent(a.name)+'/', v=a.presentation.maj;
  return `<video class="bw-une-video" src="${base}video?v=${v}"${a.presentation.affiche ? ` poster="${base}affiche?v=${v}"` : ''}
    muted loop autoplay playsinline preload="metadata" disablepictureinpicture tabindex="-1" aria-hidden="true"></video>`;
}
/* LES COULEURS DE L'APPLICATION. Le widget « a la une » prend la couleur
   dominante de son icone au lieu de l'accent du panneau : chaque application
   a son ambiance. Mesuree une fois par icone (un canevas de 32 px), puis
   gardee : chaque repeint la repose sans attendre. Le texte passe au blanc ou
   au presque-noir, celui des deux qui contraste le plus. */
const teintesIcones=new Map();
function luminance([r,g,b]){
  const c=v=>{ v/=255; return v<=.03928 ? v/12.92 : Math.pow((v+.055)/1.055, 2.4); };
  return .2126*c(r)+.7152*c(g)+.0722*c(b);
}
function teinteDominante(img){
  const n=32, cv=document.createElement('canvas'); cv.width=cv.height=n;
  const cx=cv.getContext('2d', {willReadFrequently:true});
  cx.drawImage(img, 0, 0, n, n);
  const px=cx.getImageData(0, 0, n, n).data;
  /* Douze familles de teinte, chaque pixel pese sa saturation : un fond
     blanc ou un contour noir ne l'emporte pas sur la couleur du logo. */
  const familles=Array.from({length:12}, ()=>({poids:0, r:0, g:0, b:0}));
  for(let i=0; i<px.length; i+=4){
    if(px[i+3]<200) continue;
    const r=px[i], g=px[i+1], b=px[i+2], max=Math.max(r,g,b), min=Math.min(r,g,b);
    const sat = max ? (max-min)/max : 0;
    if(sat<.25 || max<40) continue;
    let h = max===min ? 0 : max===r ? (g-b)/(max-min) : max===g ? 2+(b-r)/(max-min) : 4+(r-g)/(max-min);
    h=((h*60)+360)%360;
    const f=familles[Math.floor(h/30)%12], w=sat*sat;
    f.poids+=w; f.r+=r*w; f.g+=g*w; f.b+=b*w;
  }
  const f=familles.reduce((x,y)=>y.poids>x.poids ? y : x);
  if(f.poids<1) return null;               // une icone en gris : l'accent reste
  return [f.r/f.poids, f.g/f.poids, f.b/f.poids].map(Math.round);
}
function poserTeinte(bloc, t){
  if(!t){ bloc.style.removeProperty('--une-fond'); bloc.style.removeProperty('--une-fond-h'); bloc.style.removeProperty('--une-txt'); return; }
  const [r,g,b]=t, L=luminance(t);
  const clair = (1.05)/(L+.05) >= (L+.05)/(luminance([16,20,26])+.05);
  const h=t.map(v=>Math.round(v*.88));
  bloc.style.setProperty('--une-fond', `rgb(${r},${g},${b})`);
  bloc.style.setProperty('--une-fond-h', `rgb(${h.join(',')})`);
  bloc.style.setProperty('--une-txt', clair ? '#ffffff' : '#10141a');
}
function teinterALaUne(racine){
  racine.querySelectorAll('[data-teinte]').forEach(n=>{
    const bloc=n.closest('.bw'), nom=n.dataset.teinte;
    if(!bloc) return;
    if(teintesIcones.has(nom)){ poserTeinte(bloc, teintesIcones.get(nom)); return; }
    const img=new Image();
    img.onload=()=>{
      let t=null; try{ t=teinteDominante(img); }catch(e){}
      teintesIcones.set(nom, t);
      const ici=racine.querySelector(`[data-teinte="${CSS.escape(nom)}"]`);
      if(ici && ici.closest('.bw')) poserTeinte(ici.closest('.bw'), t);
    };
    img.onerror=()=>teintesIcones.set(nom, null);
    img.src=icone(nom);
  });
}
function widgetEtat(taille){
  const liste=hubApps.concat(hubServices);
  const compte=c=>liste.filter(a=>etatApp(a)[0]===c).length;
  const enLigne=compte('on'), arretees=compte('off');
  /* LE BILAN DIT TOUT CE QUI NE VA PAS, pas seulement le premier souci :
     « Tout répond » s'affichait alors que deux services ne repondaient pas. */
  const soucis=liste.filter(a=>['err','warn'].includes(etatApp(a)[0]));
  const nErr=compte('err'), nWarn=soucis.length-nErr;
  const bilan=[nErr ? `${nErr} en erreur` : '', nWarn ? `${nWarn} à surveiller` : ''].filter(Boolean).join(' · ');
  const lien = soucis.length===1
    ? `<a href="${lienOuverture(soucis[0])}">${esc(nomAffiche(soucis[0]))} : ${esc(etatApp(soucis[0])[1].toLowerCase())}</a>`
    : soucis.length ? `<a href="/applications">${esc(bilan)}</a>` : '';
  return `<div class="bw-titre">État</div>
    <div><div class="bento-chiffre">${chiffre('etat-en-ligne', enLigne)}<span>/${liste.length}</span></div><p>en ligne${arretees ? ` · ${arretees} arrêtée${arretees>1?'s':''}` : ''}</p></div>
    <div class="bento-pied"><div class="bento-barres" aria-hidden="true" data-apparait="etat-barres">${liste.map((a,i)=>`<i class="${etatApp(a)[0]}" style="--i:${i}"></i>`).join('')}</div>
    ${lien || '<p>Tout répond.</p>'}</div>`;
}
/* Les sauvegardes, pour l'administrateur : la derniere, et l'alerte tant
   qu'aucune copie n'est sortie du serveur. Le detail est dans Parametres >
   Securite. */
let hubSauvegardes=null;
function widgetSauvegardes(taille){
  const d=hubSauvegardes;
  const lien=`<a class="bento-bouton" href="/parametres/securite">Voir ${SVG_FLECHE}</a>`;
  if(!d || !d.actives) return `<div class="bw-titre">Sauvegardes</div><p class="bw-vide">Désactivées sur ce serveur.</p>${taille!=='petit' ? lien : ''}`;
  if(!d.derniere) return `<div class="bw-titre">Sauvegardes</div><p class="bw-vide">Aucune pour le moment : la première arrive bientôt.</p>`;
  return `<div class="bw-titre">Sauvegardes</div>
    <div><div class="sg-widget-quand">${esc(ilYA(d.derniere.date))}</div>
    <p>${esc(tailleLisible(d.derniere.taille))}${d.alerte_hors_serveur ? ' · <b class="sg-widget-alerte">sur ce serveur seulement</b>' : ''}</p></div>
    ${taille!=='petit' ? lien : ''}`;
}
/* ---------------- le mouvement ----------------
   LES CHIFFRES DEFILENT, LES GRAPHIQUES SE DESSINENT. Un nombre porte
   data-anim="nombre", une jauge data-anim="anneau" : chacun a sa cle
   (data-cle) et sa valeur finale (data-val), deja ecrite dans le HTML --
   sans script, ou quand l'appareil demande moins de mouvement, la valeur
   juste s'affiche d'emblee. La page retient la derniere valeur montree pour
   chaque cle : un repeint qui ne change rien ne rejoue rien, et une valeur
   qui change part de l'ancienne, pas de zero.
   Un bloc data-apparait (barres, courbe) ne joue son entree qu'a sa
   premiere apparition : le hub se repeint toutes les dix secondes, et un
   graphique qui se redessinerait a chaque tour fatiguerait l'oeil. */
const moinsDeMouvement = () => { try{ return matchMedia('(prefers-reduced-motion: reduce)').matches; }catch(e){ return false; } };
const valeursVues=new Map(), dejaApparus=new Set();
const nombreFr = (v, dec=0) => Number(v).toLocaleString('fr-FR', {minimumFractionDigits:dec, maximumFractionDigits:dec});
const chiffre = (cle, v, dec=0) =>
  `<span data-anim="nombre" data-cle="${esc(cle)}" data-val="${Number(v)||0}" data-dec="${dec}">${nombreFr(Number(v)||0, dec)}</span>`;
function poserAnim(n, v){
  if(n.dataset.anim==='nombre') n.textContent=nombreFr(v, +(n.dataset.dec||0));
  else if(n.dataset.anim==='anneau') n.style.strokeDashoffset=String(1 - Math.max(0, Math.min(100, v))/100);
  else if(n.dataset.anim==='largeur') n.style.width=Math.max(0, Math.min(100, v))+'%';
}
function animerChiffres(racine){
  if(!racine) return;
  const sobre=moinsDeMouvement();
  racine.querySelectorAll('[data-apparait]').forEach(n=>{
    const cle=n.dataset.apparait;
    if(dejaApparus.has(cle)) return;
    dejaApparus.add(cle);
    if(!sobre) n.classList.add('apparait');
  });
  racine.querySelectorAll('[data-anim]').forEach(n=>{
    const cle=n.dataset.cle, fin=parseFloat(n.dataset.val);
    if(!cle || !isFinite(fin)) return;
    const avant = valeursVues.has(cle) ? valeursVues.get(cle) : 0;
    valeursVues.set(cle, fin);
    /* Deja en route vers cette valeur : on ne coupe pas son elan. */
    if(n._vers===fin) return;
    if(sobre || avant===fin){ poserAnim(n, fin); return; }
    n._vers=fin;
    const debut=performance.now(), duree=n.dataset.anim==='nombre' ? 1200 : 1400;
    poserAnim(n, avant);
    const pas=t=>{
      if(n._vers!==fin) return;
      const k=Math.min(1, (t-debut)/duree), e=1-Math.pow(1-k, 4);
      poserAnim(n, avant+(fin-avant)*e);
      if(k<1) requestAnimationFrame(pas); else n._vers=undefined;
    };
    requestAnimationFrame(pas);
  });
}

/* ---------------- le pouls : Serveur et Activite ----------------
   Deux widgets de l'administrateur, nourris par /api/hub/pouls. Leur corps
   n'est PAS dans le HTML du hub : la tuile n'y porte qu'un emplacement
   garde (data-garder), rempli a part. Sans cela, le processeur qui change
   d'un point toutes les dix secondes repeignait le hub entier. */
let hubPouls={}, poulsEnCours=false, poulsActiviteLe=0, poulsEssaiLe=0;
function widgetsPoses(){ return new Set((hubDisposition||[]).filter(e=>e.t==='w').map(e=>e.id)); }
async function chargerPouls(){
  if(!estAdmin || poulsEnCours) return;
  const poses=widgetsPoses(), quoi=[];
  if(poses.has('serveur')) quoi.push('serveur');
  if(poses.has('activite') && Date.now()-poulsActiviteLe > 60000) quoi.push('activite');
  if(!quoi.length) return;
  poulsEnCours=true; poulsEssaiLe=Date.now();
  try{
    const r=await fetch('/api/hub/pouls?quoi='+quoi.join(','));
    if(!r.ok) return;
    const d=await r.json();
    if(d.activite) poulsActiviteLe=Date.now();
    Object.assign(hubPouls, d);
  }catch(e){ return; }
  finally{ poulsEnCours=false; }
  peindrePouls();
}
const emplacementPouls = (id, taille) =>
  `<div class="bw-pouls" data-pouls="${id}" data-taille="${taille}" data-garder="pouls-${id}-${taille}"></div>`;
function peindrePouls(){
  let manque=false;
  document.querySelectorAll('#hub-liste [data-pouls]').forEach(n=>{
    const id=n.dataset.pouls, taille=n.dataset.taille, d=hubPouls[id];
    if(!d){ manque=true; remplir(n, `<div class="bw-titre">${id==='serveur' ? 'Serveur' : 'Activité'}</div><p class="bw-vide">Mesure en cours…</p>`); return; }
    remplir(n, id==='serveur' ? corpsServeur(d, taille) : corpsActivite(d, taille));
    animerChiffres(n);
  });
  if(manque && Date.now()-poulsEssaiLe > 10000) chargerPouls();
}
/* AU-DELA DE 80 %, A SURVEILLER ; AU-DELA DE 92 %, SATURE. La jauge change
   de couleur ET le dit en toutes lettres : jamais la couleur seule. */
const niveauCharge = v => v>=92 ? ['err','saturé'] : v>=80 ? ['warn','élevé'] : ['',''];
function jauge(cle, v, nom, detail){
  const p=Math.round(Math.max(0, Math.min(100, v||0)));
  const [c, mot]=niveauCharge(p);
  return `<div class="jauge ${c}" role="img" aria-label="${esc(nom)} : ${p} %${mot ? ', '+mot : ''}">
    <div class="jauge-cadran"><svg viewBox="0 0 48 48" aria-hidden="true"><circle class="jauge-rail" cx="24" cy="24" r="20"/>
      <circle class="jauge-arc" cx="24" cy="24" r="20" pathLength="1" stroke-dasharray="1"
        stroke-dashoffset="${1-p/100}" data-anim="anneau" data-cle="${esc(cle)}-arc" data-val="${p}"/></svg>
      <div class="jauge-val">${chiffre(cle, p)}<small>%</small></div></div>
    <div class="jauge-nom">${esc(nom)}${mot ? ` <b>${mot}</b>` : ''}</div>
    ${detail ? `<div class="jauge-detail">${esc(detail)}</div>` : ''}</div>`;
}
function dureeCourte(s){
  if(s==null) return '';
  const j=Math.floor(s/86400), h=Math.floor(s%86400/3600), m=Math.floor(s%3600/60);
  return j ? j+' j '+h+' h' : h ? h+' h '+m+' min' : m+' min';
}
function corpsServeur(d, taille){
  const mem=d.memoire||{}, dq=d.disque;
  const allume=d.allume_depuis!=null ? `<span class="bw-sous">allumé depuis ${dureeCourte(d.allume_depuis)}</span>` : '';
  const tete=`<div class="bw-tete"><div class="bw-titre"><i class="pouls-point" aria-hidden="true"></i>Serveur</div>${taille!=='petit' ? allume : ''}</div>`;
  if(taille==='petit'){
    return tete + jauge('srv-cpu', d.cpu, 'Processeur')
      + `<p class="bw-ligne">Mémoire <b>${chiffre('srv-mem-p', Math.round(mem.pct||0))} %</b>${dq ? ` · Disque <b>${chiffre('srv-dq-p', Math.round(dq.pct))} %</b>` : ''}</p>`;
  }
  const jauges=`<div class="jauges">${jauge('srv-cpu', d.cpu, 'Processeur', d.coeurs+' cœur'+(d.coeurs>1?'s':''))}
    ${jauge('srv-mem', mem.pct, 'Mémoire', mem.total ? tailleLisible(mem.utilise)+' / '+tailleLisible(mem.total) : '')}
    ${dq ? jauge('srv-dq', dq.pct, 'Disque', tailleLisible(dq.utilise)+' / '+tailleLisible(dq.total)) : ''}</div>`;
  return tete + jauges + (taille==='grand' ? courbeServeur(d.historique||[]) : '');
}
/* LA COURBE DES DIX DERNIERES MINUTES : processeur et memoire sur la meme
   echelle (0 a 100 %), donc un seul axe. Le processeur porte l'accent et
   une aire ; la memoire, un trait neutre. Une legende nomme les deux, et le
   survol donne la valeur exacte. */
function courbeServeur(h){
  if(h.length<2) return `<p class="bw-vide">La courbe se dessine : un point toutes les dix secondes.</p>`;
  const W=300, H=90, n=h.length;
  const x=i=>(i/(n-1))*W, y=v=>H-2-(Math.max(0, Math.min(100, v))/100)*(H-4);
  const ligne=k=>h.map((p,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(p[k]).toFixed(1)).join('');
  const aire=ligne('cpu')+`L${W} ${H}L0 ${H}Z`;
  const pts=esc(JSON.stringify(h.map(p=>[p.t, p.cpu, p.mem])));
  return `<div class="courbe" data-points="${pts}" data-apparait="srv-courbe">
    <div class="courbe-legende"><span><i class="l-cpu"></i>Processeur</span><span><i class="l-mem"></i>Mémoire</span>
      <span class="courbe-duree">${Math.round((h[n-1].t-h[0].t)/60)||1} dernières min</span></div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id="courbe-degrade" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="var(--accent)" stop-opacity=".32"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
      <line class="courbe-grille" x1="0" x2="${W}" y1="${y(50)}" y2="${y(50)}"/>
      <g class="courbe-traits"><path class="courbe-aire" d="${aire}"/>
      <path class="courbe-mem" d="${ligne('mem')}"/>
      <path class="courbe-cpu" d="${ligne('cpu')}"/></g>
      <line class="courbe-curseur" x1="0" x2="0" y1="0" y2="${H}"/>
    </svg><div class="courbe-bulle" hidden></div></div>`;
}
const JOURS_COURTS=['dim.','lun.','mar.','mer.','jeu.','ven.','sam.'];
function corpsActivite(d, taille){
  const jours=d.jours||[], total=d.total||0, avant=d.precedent||0;
  const max=Math.max(1, ...jours.map(j=>j.ouvertures));
  const tendance = avant ? Math.round((total-avant)/avant*100) : null;
  const badge = tendance===null ? '' : `<span class="tendance ${tendance>=0 ? 'hausse' : 'baisse'}"
      title="Par rapport aux ${jours.length} jours d'avant (${avant})">${tendance>=0 ? '▲' : '▼'} ${chiffre('act-tendance', Math.abs(tendance))} %</span>`;
  const barres=jours.map((j,i)=>{
    const dt=new Date(j.jour+'T12:00:00');
    const nom=JOURS_COURTS[dt.getDay()]+' '+dt.getDate();
    return `<i style="--h:${(j.ouvertures/max).toFixed(3)};--i:${i}" class="${i===jours.length-1 ? 'auj' : ''}${j.ouvertures ? '' : ' nul'}"
      title="${esc(nom)} : ${j.ouvertures} ouverture${j.ouvertures>1?'s':''}"></i>`;
  }).join('');
  const debut=jours[0] ? new Date(jours[0].jour+'T12:00:00') : null;
  const axe = debut ? `<div class="barres-axe"><span>${debut.getDate()} ${debut.toLocaleDateString('fr-FR',{month:'short'})}</span><span>aujourd'hui</span></div>` : '';
  const apps = taille==='grand' && (d.apps||[]).length ? `<div class="act-apps">${d.apps.map((a,i)=>`
      <a class="act-app" href="${appDuHub(a.name) ? lienOuverture(appDuHub(a.name)) : '/applications/'+encodeURIComponent(a.name)}" data-app="${esc(a.name)}"><img src="${icone(a.name)}" alt="">
        <span class="act-app-nom">${esc(a.titre||a.name)}</span><b>${chiffre('act-app-'+a.name, a.ouvertures)}</b>
        <span class="act-app-rail"><i data-anim="largeur" data-cle="act-rail-${esc(a.name)}" data-val="${Math.round(a.ouvertures/d.apps[0].ouvertures*100)}"
          style="width:${Math.round(a.ouvertures/d.apps[0].ouvertures*100)}%"></i></span></a>`).join('')}</div>` : '';
  return `<div class="bw-tete"><div class="bw-titre">Activité</div>${badge}</div>
    <div><div class="bento-chiffre act-chiffre">${chiffre('act-total', total)}</div>
      <p class="bw-ligne">ouverture${total>1?'s':''} en ${jours.length} jours</p></div>
    ${total ? `<div class="barres-jours" data-apparait="act-barres" role="img"
      aria-label="Ouvertures par jour, sur ${jours.length} jours : jusqu'à ${max} par jour">${barres}</div>${axe}` : '<p class="bw-vide">Aucune ouverture ces deux dernières semaines.</p>'}
    ${apps}`;
}
/* Le survol de la courbe : un curseur vertical et la valeur du point le
   plus proche. */
document.addEventListener('pointermove', e=>{
  const c=e.target.closest && e.target.closest('.courbe');
  if(!c) return;
  const svg=c.querySelector('svg'), bulle=c.querySelector('.courbe-bulle'), curseur=c.querySelector('.courbe-curseur');
  let pts; try{ pts=JSON.parse(c.dataset.points); }catch(err){ return; }
  const r=svg.getBoundingClientRect(); if(!r.width || pts.length<2) return;
  const k=Math.max(0, Math.min(1, (e.clientX-r.left)/r.width)), i=Math.round(k*(pts.length-1)), p=pts[i];
  const xs=i/(pts.length-1)*300;
  curseur.setAttribute('x1', xs); curseur.setAttribute('x2', xs); c.classList.add('survolee');
  bulle.hidden=false;
  bulle.innerHTML=`<b>${new Date(p[0]*1000).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'})}</b>
    <span><i class="l-cpu"></i>Processeur ${nombreFr(p[1],1)} %</span><span><i class="l-mem"></i>Mémoire ${nombreFr(p[2],1)} %</span>`;
  const bx=Math.min(r.width-bulle.offsetWidth, Math.max(0, e.clientX-r.left-bulle.offsetWidth/2));
  bulle.style.left=bx+'px';
});
document.addEventListener('pointerout', e=>{
  const c=e.target.closest && e.target.closest('.courbe');
  if(!c || (e.relatedTarget && c.contains(e.relatedTarget))) return;
  c.classList.remove('survolee'); const b=c.querySelector('.courbe-bulle'); if(b) b.hidden=true;
});

function widgetMessages(taille){
  const d=hubServices.find(s=>s.name==='messages');
  const n=d ? (d.non_lues||0) : 0;
  return `<div class="bw-titre">Messages</div>
    <div><div class="bento-chiffre">${chiffre('messages', n)}</div><p>${n ? (n>1 ? 'nouveaux messages' : 'nouveau message') : 'Rien de nouveau.'}</p></div>
    ${taille!=='petit' ? `<a class="bento-bouton" href="/messages">Ouvrir ${SVG_FLECHE}</a>` : ''}`;
}
function widgetNouveautes(taille){
  const maj=hubApps.filter(a=>a.nouvelle_version);
  if(!maj.length) return `<div class="bw-titre">Nouvelles versions</div><p class="bw-vide">Tout est à jour.</p>`;
  return `<div class="bw-titre">Nouvelles versions</div>
    ${taille==='petit' ? `<div><div class="bento-chiffre">${chiffre('nouveautes', maj.length)}</div><p>application${maj.length>1?'s':''} à redécouvrir</p></div>`
      : `<div class="bw-liste">${maj.slice(0,4).map(a=>`<a href="${lienOuverture(a)}" data-app="${esc(a.name)}"><img src="${icone(a.name)}" alt=""><span>${esc(nomAffiche(a))}</span></a>`).join('')}</div>`}`;
}
/* Les trois sujets d'un message : ce que l'administrateur en fera depend
   d'abord de cela -- une amelioration du hub, un nouveau projet, ou le
   travail sur une application qui existe deja. */
const SUJETS_MESSAGE=[['hub','Le hub'],['idee','Nouveau projet'],['application','Une application']];
let hubMessageCible='hub', hubMessageApp='';
function widgetMessage(taille){
  const apps=hubApps.slice().sort((x,y)=>nomAffiche(x).localeCompare(nomAffiche(y),'fr'));
  if(!hubMessageApp && apps.length) hubMessageApp=apps[0].name;
  return `<div class="bw-titre">Un mot à l'admin ?</div>
    <div class="bw-sujets" role="radiogroup" aria-label="C'est pour">${SUJETS_MESSAGE.map(([v,t])=>
      `<button type="button" role="radio" aria-checked="${hubMessageCible===v}" data-agir="bw-sujet" data-cible="${v}">${t}</button>`).join('')}</div>
    ${hubMessageCible==='application' ? `<label class="sr" for="bw-message-app">Quelle application</label>
      <select id="bw-message-app" onchange="hubMessageApp=this.value">${apps.map(a=>
        `<option value="${esc(a.name)}" ${a.name===hubMessageApp?'selected':''}>${esc(nomAffiche(a))}</option>`).join('')}</select>` : ''}
    <label class="sr" for="bw-message-texte">Ton message</label>
    <textarea id="bw-message-texte" data-garder="message" data-pieces="widget" maxlength="2000"
      placeholder="Une idée, un souci, une demande d'accès."
      oninput="hubMessageBrouillon=this.value">${esc(hubMessageBrouillon)}</textarea>
    <div class="bw-bas">
      <label class="joindre" title="Joindre une image ou un PDF"><span class="sr">Joindre une image ou un PDF</span>
        <input type="file" accept="image/png,image/jpeg,image/webp,image/gif,application/pdf" multiple
               onchange="deposerPieces('widget', this.files); this.value=''">${SVG_TROMBONE}</label>
      <div class="pj-liste">${piecesHtml('widget')}</div>
      <button class="btn btn-primary" onclick="bwEnvoyer(this)">Envoyer</button></div>`;
}
async function bwEnvoyer(b){
  const t=$('bw-message-texte'), texte=(t.value||'').trim();
  const pieces=piecesPretes('widget'); if(pieces===null) return;
  if(!texte && !pieces.length){ t.focus(); return; }
  b.disabled=true;
  try{
    const r=await fetch('/api/messages',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({cible:hubMessageCible, texte, pieces, app: hubMessageCible==='application' ? hubMessageApp : ''})});
    if(!r.ok){ notifier(await echec(r,'Envoi impossible.'),'err'); return; }
    t.value=''; hubMessageBrouillon=''; viderPieces('widget'); notifier('Message envoyé.'); chargerSuivi(true);
  }finally{ b.disabled=false; }
}
/* ---------------- le menu d'une application ----------------
   La tuile ouvre l'application, et rien d'autre. Le reste -- la video de
   presentation, copier le lien, ranger hors du hub, les reglages -- passe
   par un menu : trois points dans le coin de la tuile a la souris, un appui
   long au doigt (comme sur l'ecran d'accueil d'un telephone), ou le clic
   droit. */
const SVG_POINTS='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>';
const SVG_MENU={
  ouvrir:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  video:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l6-3.5z"/></svg>',
  lien:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>',
  ranger:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 5.1A9.8 9.8 0 0 1 12 5c6 0 9.5 7 9.5 7a17 17 0 0 1-2.7 3.6M6.3 6.3C3.8 8 2.5 12 2.5 12S6 19 12 19a9 9 0 0 0 4.2-1"/></svg>',
  reglages:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
};
function boutonMenuApp(a){
  if(!a) return '';
  return `<button type="button" class="btn-menu-app" data-agir="menu-app" data-cible="${esc(a.name)}"
    aria-haspopup="menu" aria-expanded="false" aria-label="Options de ${esc(nomAffiche(a))}" title="Options">${SVG_POINTS}</button>`;
}
const appDuHub = nom => hubApps.find(x=>x.name===nom) || hubServices.find(x=>x.name===nom);
let menuAppAncre=null;
/* ou : le bouton (menu sous lui), {x, y} (clic droit : menu au pointeur), ou
   'feuille' (appui long : en bas de l'ecran). */
function ouvrirMenuApp(nom, ou){
  const a=appDuHub(nom); if(!a) return;
  const item=(agir, svg, texte, extra='') =>
    `<button type="button" role="menuitem" class="menu-app-item" data-agir="${agir}" data-cible="${esc(a.name)}" ${extra}>${svg}<span>${texte}</span></button>`;
  remplir($('menu-app-panneau'), `<div class="menu-app-tete"><img src="${icone(a.name)}" alt=""><b id="menu-app-nom">${esc(nomAffiche(a))}</b></div>
    ${item('menu-ouvrir', SVG_MENU.ouvrir, "Ouvrir")}
    ${a.presentation ? item('menu-presentation', SVG_MENU.video, 'Voir la présentation') : ''}
    ${item('menu-copier', SVG_MENU.lien, 'Copier le lien')}
    ${estAdmin && !a.externe && !a.interne ? item('menu-reglages', SVG_MENU.reglages, "Réglages de l'application") : ''}
    ${item('menu-ranger', SVG_MENU.ranger, 'Masquer du hub')}`);
  const menu=$('menu-app'), panneau=$('menu-app-panneau');
  menu.classList.toggle('feuille', ou==='feuille');
  panneau.style.left=panneau.style.top='';
  menu.hidden=false;
  if(menuAppAncre) menuAppAncre.setAttribute('aria-expanded','false');
  menuAppAncre = ou instanceof Element ? ou : null;
  if(ou!=='feuille'){
    /* Sous le bouton, aligne sur son bord droit ; au pointeur pour le clic
       droit. Retourne au-dessus, ou vers la gauche, s'il deborderait. */
    const r = ou instanceof Element ? ou.getBoundingClientRect()
      : {left:ou.x, right:ou.x, top:ou.y, bottom:ou.y};
    const w=panneau.offsetWidth, h=panneau.offsetHeight, marge=8;
    let x = ou instanceof Element ? r.right-w : r.left;
    let y = r.bottom+6;
    if(x+w > innerWidth-marge) x=innerWidth-marge-w;
    if(y+h > innerHeight-marge) y=Math.max(marge, r.top-6-h);
    panneau.style.left=Math.max(marge, x)+'px'; panneau.style.top=y+'px';
  }
  if(menuAppAncre) menuAppAncre.setAttribute('aria-expanded','true');
  const premier=panneau.querySelector('.menu-app-item'); if(premier) premier.focus({preventScroll:true});
}
function fermerMenuApp(rendreFocus){
  const menu=$('menu-app'); if(menu.hidden) return;
  menu.hidden=true;
  if(menuAppAncre){ menuAppAncre.setAttribute('aria-expanded','false'); if(rendreFocus) menuAppAncre.focus(); }
  menuAppAncre=null;
}
function actionMenuApp(quoi, nom){
  fermerMenuApp(false);
  const a=appDuHub(nom); if(!a) return;
  if(quoi==='ouvrir'){
    try{ localStorage.setItem('codelab-hub-derniere', nom); }catch(err){}
    if(hubApps.some(x=>x.name===nom))
      fetch('/api/mes-apps/'+encodeURIComponent(nom)+'/ouverte', {method:'POST', keepalive:true}).catch(()=>{});
    location.href=lienOuverture(a);
  }
  if(quoi==='presentation') ouvrirPresentation(nom);
  if(quoi==='copier') copierLien(new URL(lienOuverture(a), location.href).href);
  if(quoi==='reglages') location.href='/applications/'+encodeURIComponent(nom);
  if(quoi==='ranger') basculerMasquage(nom, true);
}
document.addEventListener('keydown', e=>{
  const menu=$('menu-app'); if(menu.hidden) return;
  if(e.key==='Escape'){ e.preventDefault(); e.stopImmediatePropagation(); fermerMenuApp(true); return; }
  if(e.key==='Tab'){ fermerMenuApp(false); return; }
  if(e.key!=='ArrowDown' && e.key!=='ArrowUp') return;
  e.preventDefault();
  const items=[...menu.querySelectorAll('.menu-app-item')], i=items.indexOf(document.activeElement);
  const j=(i + (e.key==='ArrowDown' ? 1 : -1) + items.length) % items.length;
  items[j].focus();
}, true);
window.addEventListener('resize', ()=>fermerMenuApp(false));
$('hub-liste').addEventListener('scroll', ()=>fermerMenuApp(false), {passive:true});

/* L'appui long, au doigt : 450 ms sans bouger. Un doigt qui glisse (le
   defilement du hub) annule tout. Au relachement, le navigateur emet -- ou
   pas, selon l'appareil -- un clic sur ce qui est sous le doigt : le fond du
   menu qui vient de s'ouvrir. Ce clic-la seul est avale, et seulement dans
   l'instant : un drapeau qui attendrait un clic jamais venu avalerait le
   toucher suivant, celui qui doit refermer le menu. */
let appuiLong=null, avalerClicJusqua=0;
$('hub-liste').addEventListener('pointerdown', e=>{
  if(e.pointerType!=='touch' || hubEdition) return;
  const t=e.target.closest('[data-app]'); if(!t || !appDuHub(t.dataset.app)) return;
  appuiLong={x:e.clientX, y:e.clientY, fait:false, minuterie:setTimeout(()=>{
    appuiLong.fait=true;
    if(navigator.vibrate) try{ navigator.vibrate(10); }catch(err){}
    ouvrirMenuApp(t.dataset.app, 'feuille');
  }, 450)};
});
document.addEventListener('pointermove', e=>{
  if(appuiLong && !appuiLong.fait && Math.hypot(e.clientX-appuiLong.x, e.clientY-appuiLong.y) > 10){
    clearTimeout(appuiLong.minuterie); appuiLong=null; }
});
['pointerup','pointercancel'].forEach(t=>document.addEventListener(t, ()=>{
  if(!appuiLong) return;
  clearTimeout(appuiLong.minuterie);
  if(appuiLong.fait) avalerClicJusqua=performance.now()+400;
  appuiLong=null;
}));
document.addEventListener('click', e=>{
  if(performance.now() > avalerClicJusqua) return;
  avalerClicJusqua=0; e.preventDefault(); e.stopImmediatePropagation();
}, true);
/* Le fond du menu a son propre ecouteur : Safari sur iOS n'envoie pas le
   toucher d'un simple <div> a un ecouteur pose sur le document. */
document.querySelector('#menu-app .menu-app-fond').addEventListener('click', ()=>fermerMenuApp(false));
/* Le clic droit (et l'appui long d'Android, qui le declenche aussi). */
$('hub-liste').addEventListener('contextmenu', e=>{
  /* En mode Modifier, l'appui long saisit la tuile : pas de menu du
     navigateur par-dessus. */
  if(hubEdition){ e.preventDefault(); return; }
  const t=e.target.closest('[data-app]'); if(!t || !appDuHub(t.dataset.app)) return;
  e.preventDefault();
  if(!$('menu-app').hidden) return;
  ouvrirMenuApp(t.dataset.app, e.pointerType==='touch' || appuiLong ? 'feuille' : {x:e.clientX, y:e.clientY});
});

/* ---------------- la video de presentation ----------------
   Ouverte depuis le menu de l'application (« Voir la présentation ») quand
   le projet a une video (video/presentation.mp4, produite par « codelab
   video ») : une feuille avec le lecteur du navigateur. */
function ouvrirPresentation(nom){
  const a=hubApps.find(x=>x.name===nom) || (typeof appParNom==='function' && appParNom(nom));
  if(!a || !a.presentation) return;
  const v=a.presentation.maj, base='/api/presentations/'+encodeURIComponent(nom)+'/';
  const video=$('pr-video');
  video.poster = a.presentation.affiche ? base+'affiche?v='+v : '';
  video.src = base+'video?v='+v;
  $('pr-icone').src=icone(nom);
  $('pr-nom').textContent=nomAffiche(a);
  $('pr-desc').textContent=a.description||'';
  $('pr-ouvrir').href=lienOuverture(a);
  $('pr-ouvrir').style.display = a.externe ? 'none' : '';
  $('ov-presentation').classList.add('show');
  video.play().catch(()=>{});
}
function peindrePresentationFiche(a){
  const carte=$('fa-presentation'); if(!carte) return;
  carte.style.display = a.externe ? 'none' : '';
  const p=a.presentation;
  $('fa-pr-sous').textContent = p
    ? (a.presentation_fichier ? a.presentation_fichier+' · ' : '')
      + 'modifiée le '+new Date(p.maj*1000).toLocaleString('fr-FR',{dateStyle:'long', timeStyle:'short'})
    : 'Aucune vidéo pour l’instant.';
  const base='/api/presentations/'+encodeURIComponent(a.name)+'/';
  remplir($('fa-pr-corps'), (p
    ? `<video class="fiche-video" controls playsinline preload="metadata" src="${base}video?v=${p.maj}"
         ${p.affiche ? `poster="${base}affiche?v=${p.maj}"` : ''}></video>
       <label class="choix-ligne" style="margin-top:12px"><input type="checkbox" data-changer="presentation-visible"
         data-cible="${esc(a.name)}" ${a.presentation_masquee ? '' : 'checked'}> Visible dans le hub</label>`
    : `<p class="hint" style="margin:6px 0 0">Dans le terminal du poste : <code>codelab video ${esc(a.name)}</code>,
       ou choisis ci-dessous une vidéo déjà dans le projet (un rendu Remotion, par exemple).</p>`)
    + `<label class="fa-pr-choix">Fichier vidéo
       <select data-changer="presentation-fichier" data-cible="${esc(a.name)}">${optionsVideos(a)}</select></label>`);
  chargerVideosProjet(a.name);
}
/* LE FICHIER DE LA PRESENTATION : video/presentation.mp4 par defaut, ou
   toute autre video du dossier du projet (le serveur en dresse la liste,
   et refuse ce qui n'y est pas). La liste se charge a l'ouverture de la
   fiche, une fois par application. */
const videosProjet={};
function optionsVideos(a){
  const v=videosProjet[a.name];
  const choisi=a.presentation_fichier||'';
  const autres=(v ? v.fichiers : (choisi ? [choisi] : [])).filter(f=>f!==(v ? v.defaut : 'video/presentation.mp4'));
  return `<option value="">video/presentation.mp4 (par défaut)</option>`
    + autres.map(f=>`<option value="${esc(f)}" ${f===choisi ? 'selected' : ''}>${esc(f)}</option>`).join('')
    + (v ? '' : '<option disabled>Recherche des vidéos…</option>');
}
async function chargerVideosProjet(nom, forcer){
  if(videosProjet[nom] && !forcer) return;
  try{
    const r=await fetch('/api/apps/'+encodeURIComponent(nom)+'/videos');
    if(!r.ok) return;
    videosProjet[nom]=await r.json();
  }catch(e){ return; }
  const a=appParNom(nom); if(a) peindrePresentationFiche(a);
}
async function choisirVideo(nom, fichier){
  const r=await fetch('/api/apps/'+encodeURIComponent(nom)+'/presentation',{method:'PUT',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({fichier})});
  if(!r.ok){ notifier(await echec(r,'Vidéo non retenue.'),'err'); return; }
  notifier(fichier ? 'La présentation montre '+fichier+'.' : 'La présentation reprend video/presentation.mp4.');
  refresh(); chargerHub();
}
async function basculerPresentation(nom, visible){
  const r=await fetch('/api/apps/'+encodeURIComponent(nom)+'/presentation',{method:'PUT',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({visible})});
  if(!r.ok){ notifier(await echec(r,'Modification impossible.'),'err'); return; }
  notifier(visible ? 'Présentation visible dans le hub.' : 'Présentation retirée du hub.');
  refresh(); chargerHub();
}
function fermerPresentation(){
  const video=$('pr-video');
  video.pause(); video.removeAttribute('src'); video.load();
  $('ov-presentation').classList.remove('show');
}
$('ov-presentation').addEventListener('click', e=>{ if(e.target===e.currentTarget) fermerPresentation(); });
document.addEventListener('keydown', e=>{
  if(e.key==='Escape' && $('ov-presentation').classList.contains('show')) fermerPresentation();
});

const CLASSE_WIDGET={serveur:'bw-serveur', activite:'bw-activite', une:'bw-une', apercu:'bw-une', etat:'bento-etat', messages:'bw-messages', nouveautes:'bw-nouveautes', message:'bw-message', suivi:'bw-suivi', sauvegardes:'bw-sauvegardes'};
function corpsWidget(e){
  if(e.id==='une'){ const [a, vue]=appALaUne(hubApps); return widgetApercu(a, e.taille, (vue ? 'Dernière ouverte' : 'À la une')+(a && a.ouverte_le ? ' · '+depuis(a.ouverte_le) : ''), false); }
  if(e.id==='apercu') return widgetApercu(hubApps.find(a=>a.name===e.app), e.taille, 'Aperçu');
  if(e.id==='etat') return widgetEtat(e.taille);
  if(e.id==='messages') return widgetMessages(e.taille);
  if(e.id==='nouveautes') return widgetNouveautes(e.taille);
  if(e.id==='sauvegardes') return widgetSauvegardes(e.taille);
  if(e.id==='message') return widgetMessage(e.taille);
  if(e.id==='suivi') return widgetSuivi(e.taille);
  if(e.id==='serveur' || e.id==='activite') return emplacementPouls(e.id, e.taille);
  return '';
}
const TAILLES_NOM={petit:'Petit', moyen:'Moyen', grand:'Grand'};
/* En mode Modifier, chaque tuile porte ses gestes : la retirer, la deplacer
   d'un cran (les fleches valent le glisser, au clavier et au doigt), et pour
   un widget, changer de taille. */
function gestesEdition(e, nom){
  const cle=esc(cleElement(e));
  const def=e.t==='w' ? hubCatalogue.find(c=>c.id===e.id) : null;
  return `<button type="button" class="ed-retirer" data-agir="hub-retirer" data-cible="${cle}"
      aria-label="${e.t==='a' ? 'Masquer' : 'Retirer'} « ${esc(nom)} »" title="${e.t==='a' ? 'Masquer du hub' : 'Retirer le widget'}">−</button>
    <div class="ed-barre">
      <button type="button" data-agir="hub-deplacer" data-cible="${cle}" data-sens="-1" aria-label="Avancer « ${esc(nom)} »">←</button>
      ${def && def.tailles.length>1 ? def.tailles.map(t=>`<button type="button" data-agir="hub-taille" data-cible="${cle}" data-taille="${t}"
        aria-pressed="${e.taille===t}" title="${TAILLES_NOM[t]}">${TAILLES_NOM[t].charAt(0)}</button>`).join('') : ''}
      <button type="button" data-agir="hub-deplacer" data-cible="${cle}" data-sens="1" aria-label="Reculer « ${esc(nom)} »">→</button>
    </div>`;
}
/* Un nom long prend un corps plus petit (voir .bento-carte-nom.long). */
const classeNomLong = n => n.length > 34 ? ' tres-long' : n.length > 20 ? ' long' : '';
/* Ce que montre la tuile du hub : deux lignes de description, soit environ
   DESCRIPTION_VISIBLE caracteres sur un ordinateur (moins au telephone). La
   fiche le dit sous le champ, compteur compris. */
const DESCRIPTION_VISIBLE=45, DESCRIPTION_MAX=140;
function compterDescription(champ){
  const aide=$(champ.id+'-compte'); if(!aide) return;
  const n=champ.value.trim().length;
  aide.textContent = n
    ? `${n} / ${DESCRIPTION_MAX} caractères — ${n<=DESCRIPTION_VISIBLE ? 'tout se voit sur la tuile.' : `les ${DESCRIPTION_VISIBLE} premiers environ se voient sur la tuile, le reste au survol.`}`
    : `Deux lignes sous le nom, dans le hub : environ ${DESCRIPTION_VISIBLE} caractères se voient, ${DESCRIPTION_MAX} au plus.`;
  aide.classList.toggle('desc-au-dela', n>DESCRIPTION_VISIBLE);
}
function peindreBento(liste, filtre){
  const toutes=hubApps.concat(hubServices);
  const desc = a => esc(a.description || (a.externe ? 'Outil de la stack' : 'Application'));
  const balise = hubEdition ? 'div' : 'a';
  const lien = a => hubEdition ? '' : `href="${lienOuverture(a)}"`;
  /* Une application : sa carte (nom, etat, description), ou son icone seule
     -- reglage « Applications ». Dans les deux cas, le meme lien, le meme
     menu, les memes gestes en mode Modifier. service-stack = la plaque
     claire sous un logo transparent (Dagster). */
  const carte = reglageHub('tuiles')==='icones'
    ? (a, e) => { const [c]=etatApp(a);
        return `<${balise} class="hub-icone ${c}${a.nouvelle_version?' maj':''}${a.externe && a.plaque ? ' service-stack' : ''}" ${lien(a)} data-app="${esc(a.name)}" data-el="${esc(cleElement(e))}"
          title="${esc(nomAffiche(a))}${a.description ? ' — '+esc(a.description) : ''}">
          <img src="${icone(a.name)}" alt=""><span class="hub-icone-nom">${esc(nomAffiche(a))}</span>${pastille(a)}${hubEdition ? gestesEdition(e, nomAffiche(a)) : boutonMenuApp(a)}</${balise}>`; }
    : (a, e) => { const [c]=etatApp(a);
        return `<${balise} class="bento-carte ${c}${a.nouvelle_version?' maj':''}" ${lien(a)} data-app="${esc(a.name)}" data-el="${esc(cleElement(e))}">
          <div class="bento-carte-haut"><img src="${icone(a.name)}" alt="">${badgeEtat(a)}</div>
          <div class="bento-carte-nom${classeNomLong(nomAffiche(a))}">${esc(nomAffiche(a))}</div>
          ${badgeMaj(a)}<div class="bento-carte-desc" title="${desc(a)}">${desc(a)}</div>${pastille(a)}${hubEdition ? gestesEdition(e, nomAffiche(a)) : boutonMenuApp(a)}</${balise}>`; };
  const tuile = e => {
    if(e.t==='w') return widgetDuHub(e);
    const a=toutes.find(x=>x.name===e.id); return a ? carte(a, e) : '';
  };
  const elements=elementsDuHub();
  const aucune = !toutes.length ? (estAdmin
      ? '<div class="hub-vide bento-vide"><b>Aucune application déclarée</b>Ouvre « Applications » dans le menu de gauche pour en ajouter une.</div>'
      : '<div class="hub-vide bento-vide"><b>Aucune application pour le moment</b>Ton administrateur ne t\'a pas encore donné accès à une application.</div>') : '';
  /* Widgets EN HAUT : deux grilles, l'une sous l'autre (ou l'une apres
     l'autre, a l'horizontale) ; chaque tuile se reorganise dans la sienne.
     La recherche ne montre que les applications qui correspondent. */
  const enZones = !filtre && reglageHub('widgets')==='haut';
  let html;
  if(filtre)
    html = liste.map(a=>carte(a, {t:'a', id:a.name})).join('')
      + (liste.length ? '' : '<div class="hub-vide bento-vide"><b>Aucune application ne correspond</b>Essaie un autre mot.</div>');
  else if(enZones){
    const widgets=elements.filter(e=>e.t==='w');
    html = (widgets.length ? `<div class="hub-grille hub-zone-widgets">${widgets.map(tuile).join('')}</div>` : '')
      + `<div class="hub-grille hub-zone-apps">${elements.filter(e=>e.t==='a').map(tuile).join('')}${aucune}</div>`;
  } else html = elements.map(tuile).join('') + aucune;
  const el=$('hub-liste');
  /* Pas de repeint sous la main : le rechargement periodique attendra. */
  if(glisse && glisse.parti) return;
  el.className='bento '+(enZones ? 'zones' : 'hub-grille')+(hubEdition ? ' edition' : '');
  remplirEnGardant(el, html);
  teinterALaUne(el);
  animerChiffres(el);
  peindrePouls();
  entreeDuHub(el);
  peindreResumeHub();
  majBoutonsComposer();
  ajusterBento();
}
/* L'ENTREE DU HUB : a la premiere peinture, les tuiles montent l'une apres
   l'autre, en cascade. Une seule fois par visite de la page -- le repeint
   periodique ne la rejoue pas. */
let hubEntre=false;
function entreeDuHub(el){
  if(hubEntre || !el.querySelector('[data-el]')) return;
  hubEntre=true;
  if(moinsDeMouvement()) return;
  el.querySelectorAll('[data-el]').forEach((n,i)=>n.style.setProperty('--i', Math.min(i, 16)));
  el.classList.add('entree');
  setTimeout(()=>el.classList.remove('entree'), 1800);
}
/* SOUS LE BONJOUR, LA JOURNEE EN UNE LIGNE : ce qui tourne, ce qui attend.
   Les chiffres defilent comme ceux des widgets. */
function peindreResumeHub(){
  majPastilleIcone();
  const r=$('hub-resume'); if(!r) return;
  const toutes=hubApps.concat(hubServices);
  if(!toutes.length){ remplir(r, ''); return; }
  let morceaux;
  if(estAdmin){
    const enLigne=toutes.filter(a=>etatApp(a)[0]==='on').length;
    const soucis=toutes.filter(a=>['err','warn'].includes(etatApp(a)[0])).length;
    const msg=(hubServices.find(s=>s.name==='messages')||{}).non_lues||0;
    morceaux=[`<b>${chiffre('resume-en-ligne', enLigne)}</b> sur ${toutes.length} en ligne`,
      soucis ? `<b class="resume-souci">${chiffre('resume-soucis', soucis)}</b> à regarder` : '',
      msg ? `<b>${chiffre('resume-msg', msg)}</b> message${msg>1?'s':''} à lire` : ''];
  }else{
    const maj=hubApps.filter(a=>a.nouvelle_version).length;
    morceaux=[`<b>${chiffre('resume-apps', hubApps.length)}</b> application${hubApps.length>1?'s':''} pour toi`,
      maj ? `<b>${chiffre('resume-maj', maj)}</b> mise${maj>1?'s':''} à jour` : ''];
  }
  remplir(r, morceaux.filter(Boolean).map(m=>`<span>${m}</span>`).join('<i aria-hidden="true">·</i>'));
  animerChiffres(r);
}
/* LE RELIEF AU SURVOL, a la souris seulement : la tuile se souleve et un
   reflet discret suit le pointeur. Plus d'inclinaison : en perspective, une
   tuile devenait un trapeze, ses bords penchaient, et l'oeil y lisait un
   defaut d'affichage. Ni au doigt, ni en mode Modifier,
   ni quand l'appareil demande moins de mouvement. */
(()=>{
  let fine=false; try{ fine=matchMedia('(hover:hover) and (pointer:fine)').matches; }catch(e){}
  if(!fine) return;
  let tuile=null;
  const lacher=()=>{ if(tuile) tuile.classList.remove('survol'); tuile=null; };
  $('hub-liste').addEventListener('pointermove', e=>{
    if(hubEdition || moinsDeMouvement() || e.pointerType!=='mouse'){ lacher(); return; }
    const t=e.target.closest('.bw, .bento-carte');
    if(t!==tuile){ lacher(); tuile=t; }
    if(!t) return;
    const r=t.getBoundingClientRect(), px=(e.clientX-r.left)/r.width, py=(e.clientY-r.top)/r.height;
    t.style.setProperty('--mx', (px*100).toFixed(1)+'%');
    t.style.setProperty('--my', (py*100).toFixed(1)+'%');
    t.classList.add('survol');
  });
  $('hub-liste').addEventListener('pointerleave', lacher);
})();
function widgetDuHub(e){
  const def=hubCatalogue.find(c=>c.id===e.id);
  const nom=e.id==='apercu' ? 'Aperçu de '+nomAffiche(hubApps.find(a=>a.name===e.app)||{name:e.app}) : (def ? def.titre : e.id);
  return `<section class="bw bw-${e.taille} ${CLASSE_WIDGET[e.id]||''}" aria-label="${esc(nom)}" data-el="${esc(cleElement(e))}">
    ${corpsWidget(e)}${hubEdition ? gestesEdition(e, nom) : ''}</section>`;
}
function majBoutonsComposer(){
  $('hub-modifier').innerHTML = hubEdition ? 'Terminé' : SVG_REORGANISER;
  $('hub-modifier').classList.toggle('actif', hubEdition);
  $('hub-ajouter-widget').style.display = hubEdition ? '' : 'none';
}
/* Ce qui doit SURVIVRE au repeint -- l'apercu (sinon il se recharge), le
   texte en cours de frappe -- est mis de cote puis remis a sa place. */
function remplirEnGardant(el, html){
  const gardes={};
  el.querySelectorAll('[data-garder]').forEach(n=>{ gardes[n.dataset.garder]=n; });
  const actif=document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.garder : null;
  const debut=actif && gardes[actif] ? gardes[actif].selectionStart : null;
  const avant=el.innerHTML;
  remplir(el, html);
  if(el.innerHTML!==avant){
    el.querySelectorAll('[data-garder]').forEach(n=>{
      const ancien=gardes[n.dataset.garder];
      if(ancien && ancien!==n) n.replaceWith(ancien);
    });
    /* preventScroll : rendre le focus au champ ne doit pas ramener la page
       jusqu'a lui -- c'etait un saut a chaque repeint, ou qu'on soit. */
    if(actif && gardes[actif]){ gardes[actif].focus({preventScroll:true}); if(debut!=null) try{ gardes[actif].setSelectionRange(debut, debut); }catch(e){} }
  }
}
/* A l'horizontale, la mosaique tient dans la fenetre : sa hauteur se
   calcule depuis son haut. En icones, autant de rangees qu'il en tient (au
   moins quatre : la hauteur d'un grand widget). A la verticale, et sur
   telephone, CSS reprend la main : la page defile. */
function horizontal(){ return !ecranMobile() && !document.documentElement.classList.contains('hub-vertical'); }
function ajusterBento(){
  const el=$('hub-liste');
  if(!el.classList.contains('bento')) return;
  if(!horizontal()){ el.style.removeProperty('--bento-h'); el.style.removeProperty('--rangees'); return; }
  const d=defileur();
  const haut=el.getBoundingClientRect().top + d.scrollTop;
  el.style.setProperty('--bento-h', Math.max(420, window.innerHeight - haut - 28) + 'px');
  const exces=d.scrollHeight - d.clientHeight;
  if(exces>0) el.style.setProperty('--bento-h', Math.max(420, el.clientHeight - exces) + 'px');
  const interieur=el.clientHeight - 14;
  /* 112 px la case, 22 px entre deux rangees (CSS, mode icones). */
  el.style.setProperty('--rangees', Math.max(4, Math.floor((interieur + 22)/(112 + 22))));
}
window.addEventListener('resize', ()=>{ majSensDuHub(); ajusterBento(); });
/* La molette verticale fait defiler la mosaique a l'horizontale : on n'a pas
   tous une souris qui sait aller de cote. Pas dans un champ de texte.
   SANS A-COUP : un cran de molette (100 px d'un coup, ou des lignes) ne
   saute pas, il glisse -- une cible, que la mosaique rejoint en quelques
   images -- et ne va qu'aux deux tiers d'un cran vertical. Un pave tactile
   envoie deja un flot de petits pas : ils passent tels quels. Aucun aimant
   (scroll-snap) : la mosaique s'arrete la ou on la lache. */
const MOLETTE_PAS=2/3;
let molette=null;
function glisserMolette(){
  const m=molette, el=$('hub-liste');
  const avant=el.scrollLeft, reste=m.cible-avant;
  /* Au moins un pixel par image : le navigateur arrondit scrollLeft, et un
     pas plus petit ne bougerait plus rien. */
  el.scrollLeft = Math.abs(reste)<=1 ? m.cible : avant + Math.sign(reste)*Math.max(1, Math.abs(reste)*.2);
  if(Math.abs(reste)<=1 || el.scrollLeft===avant){ molette=null; return; }
  m.raf=requestAnimationFrame(glisserMolette);
}
$('hub-liste').addEventListener('wheel', e=>{
  const el=e.currentTarget;
  if(!el.classList.contains('bento') || !horizontal() || e.target.closest('textarea')) return;
  if(Math.abs(e.deltaY) <= Math.abs(e.deltaX) || el.scrollWidth <= el.clientWidth) return;
  e.preventDefault();
  const pas = e.deltaMode===1 ? e.deltaY*40 : e.deltaMode===2 ? e.deltaY*el.clientWidth : e.deltaY;
  const cran = e.deltaMode!==0 || (Math.abs(pas)>=50 && Number.isInteger(pas));
  if(!cran && !molette){ el.scrollLeft += pas; return; }
  const max=el.scrollWidth-el.clientWidth;
  const depart = molette ? molette.cible : el.scrollLeft;
  const cible=Math.max(0, Math.min(max, depart + pas*(cran ? MOLETTE_PAS : 1)));
  if(molette){ molette.cible=cible; return; }
  molette={cible}; molette.raf=requestAnimationFrame(glisserMolette);
}, {passive:false});
/* Un doigt ou une barre qui reprend la main arrete le glissement. */
['pointerdown','touchstart'].forEach(t=>$('hub-liste').addEventListener(t, ()=>{
  if(molette){ cancelAnimationFrame(molette.raf); molette=null; }
}, {passive:true}));

/* ---- composer : modifier, deplacer, retirer, changer de taille ---- */
function basculerEdition(){
  hubEdition=!hubEdition;
  if(hubEdition) $('hub-filtre').value='';
  peindreHub();
}
function hubRetirer(cle){
  const elements=elementsDuHub();
  const e=elements.find(x=>cleElement(x)===cle); if(!e) return;
  if(e.t==='a'){
    /* Une application se MASQUE (elle se retrouve dans Parametres > Compte),
       elle ne quitte pas le serveur. */
    fetch('/api/mes-apps/'+encodeURIComponent(e.id)+'/masquer',{method:'POST'}).then(r=>{
      if(r.ok){ notifier('Masquée du hub — à retrouver dans tes paramètres.', 'ok',
                         {libelle:'Annuler', faire:()=>basculerMasquage(e.id, false)}); chargerHub(); }
    });
    return;
  }
  /* Retirer un widget se rattrape : la disposition d'avant est remise. */
  enregistrerDisposition(elements.filter(x=>x!==e)).then(()=>
    notifier('Widget retiré.', 'ok', {libelle:'Annuler', faire:()=>enregistrerDisposition(elements)}));
}
function hubDeplacer(cle, sens){
  const elements=elementsDuHub();
  const i=elements.findIndex(x=>cleElement(x)===cle);
  let j=i+Number(sens);
  /* Widgets en haut : chacun reste dans sa grille -- un widget passe au
     widget voisin, une application a l'application voisine. */
  if(i>=0 && reglageHub('widgets')==='haut')
    while(j>=0 && j<elements.length && elements[j].t!==elements[i].t) j+=Number(sens);
  if(i<0 || j<0 || j>=elements.length) return;
  [elements[i], elements[j]]=[elements[j], elements[i]];
  enregistrerDisposition(elements).then(()=>{
    const b=document.querySelector(`[data-el="${CSS.escape(cle)}"] [data-sens="${sens}"]`); if(b) b.focus(); });
}
function hubTailleWidget(cle, taille){
  enregistrerDisposition(elementsDuHub().map(x=>cleElement(x)===cle ? {...x, taille} : x));
}
/* GLISSER UNE TUILE, COMME SUR UN IPHONE. En mode edition, on attrape une
   tuile : elle suit le pointeur (souris ou doigt), et les autres glissent
   pour lui faire place a mesure qu'elle passe sur elles. Pres d'un bord,
   la mosaique defile toute seule. Au lacher, la tuile se pose a sa place et
   la disposition part au serveur. */
const SVG_REORGANISER='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><path d="M17 14v6M14 17h6"/></svg>';
var glisse=null;   // var : lu par peindreBento, qui peut tourner avant cette ligne
/* L'ordre des tuiles a l'ecran, d'une grille ou des deux. */
const ordreDesTuiles = () => [...$('hub-liste').querySelectorAll('[data-el]')].map(x=>x.dataset.el);
function positionsTuiles(){
  const m=new Map();
  $('hub-liste').querySelectorAll('[data-el]').forEach(n=>m.set(n, n.getBoundingClientRect()));
  return m;
}
/* Les tuiles qui ont change de place partent de l'ancienne et glissent vers
   la nouvelle (FLIP). Le tremblement passe par « rotate » : « transform »
   reste libre pour ce glissement. */
function glisserLesAutres(avant, sauf){
  for(const [n, r] of avant){
    if(n===sauf || !n.isConnected) continue;
    const r2=n.getBoundingClientRect(), dx=r.left-r2.left, dy=r.top-r2.top;
    if(!dx && !dy) continue;
    n.style.transition='none'; n.style.transform=`translate(${dx}px,${dy}px)`;
    requestAnimationFrame(()=>requestAnimationFrame(()=>{
      n.style.transition='transform .24s cubic-bezier(.2,.8,.2,1)'; n.style.transform=''; }));
  }
}
function suivrePointeur(){
  const g=glisse; if(!g || !g.parti) return;
  g.t.style.transform='none';
  const r=g.t.getBoundingClientRect();
  g.t.style.transform=`translate(${g.x-g.dx-r.left}px,${g.y-g.dy-r.top}px)`;
}
function chercherPlace(){
  const g=glisse; if(!g || !g.parti || performance.now()<g.calme) return;
  const sous=document.elementFromPoint(g.x, g.y);
  const cible=sous && sous.closest && sous.closest('#hub-liste [data-el]');
  /* Une tuile ne quitte pas sa grille (widgets en haut : widgets d'un
     cote, applications de l'autre). */
  if(!cible || cible===g.t || cible.parentNode!==g.t.parentNode) return;
  const enfants=[...g.t.parentNode.children];
  const avant=positionsTuiles();
  if(enfants.indexOf(g.t) < enfants.indexOf(cible)) cible.after(g.t); else cible.before(g.t);
  glisserLesAutres(avant, g.t);
  /* Le temps que les autres se posent : sinon la tuile survolee, qui vient
     de bouger, serait aussitot reprise pour cible, et tout oscillerait. */
  g.calme=performance.now()+200;
  suivrePointeur();
}
function defilerPresDesBords(){
  const g=glisse; if(!g || !g.parti) return;
  const liste=$('hub-liste'), r=liste.getBoundingClientRect(), bord=70;
  let v=0;
  if(g.x < r.left+bord) v=-Math.ceil((r.left+bord-g.x)/5);
  else if(g.x > r.right-bord) v=Math.ceil((g.x-(r.right-bord))/5);
  if(v && horizontal()){ liste.scrollLeft+=v; suivrePointeur(); chercherPlace(); }
  /* A la verticale, c'est la page qui defile, pres du haut et du bas. */
  if(!horizontal()){
    const h=window.innerHeight; let w=0;
    if(g.y < bord) w=-Math.ceil((bord-g.y)/5); else if(g.y > h-bord) w=Math.ceil((g.y-(h-bord))/5);
    if(w){ defileur().scrollBy(0, w); suivrePointeur(); chercherPlace(); }
  }
  g.raf=requestAnimationFrame(defilerPresDesBords);
}
/* AU DOIGT, on tient avant de deplacer : un appui de 350 ms « saisit » la
   tuile (elle se souleve, le telephone vibre), et seulement alors elle suit
   le doigt. Un doigt qui glisse tout de suite fait defiler la page, comme
   partout ailleurs -- sans cela, le mode Modifier empechait de faire
   defiler le hub. A la souris, rien ne change : on attrape et on tire. */
const SAISIE_TACTILE_MS = 350;
$('hub-liste').addEventListener('pointerdown', e=>{
  if(!hubEdition || e.button>0 || e.target.closest('button,select,textarea,input,label')) return;
  const t=e.target.closest('#hub-liste [data-el]'); if(!t) return;
  const r=t.getBoundingClientRect();
  const tactile = e.pointerType==='touch';
  glisse={t, x:e.clientX, y:e.clientY, x0:e.clientX, y0:e.clientY, dx:e.clientX-r.left, dy:e.clientY-r.top,
          parti:false, calme:0, raf:0, avant:ordreDesTuiles().join('|'),
          tactile, saisie:!tactile, minuterie:0};
  if(tactile) glisse.minuterie=setTimeout(()=>{
    const g=glisse; if(!g || g.t!==t) return;
    g.saisie=true; t.classList.add('saisie');
    if(navigator.vibrate) try{ navigator.vibrate(12); }catch(err){}
  }, SAISIE_TACTILE_MS);
});
/* Une fois la tuile saisie, le doigt ne fait plus defiler : c'est le
   premier touchmove annule qui l'empeche (touch-action laisse defiler tant
   que rien n'est saisi). */
document.addEventListener('touchmove', e=>{
  if(glisse && glisse.tactile && glisse.saisie) e.preventDefault();
}, {passive:false});
document.addEventListener('pointermove', e=>{
  const g=glisse; if(!g) return;
  g.x=e.clientX; g.y=e.clientY;
  if(!g.saisie){
    /* Le doigt a bouge avant la saisie : il fait defiler, on le laisse. */
    if(Math.hypot(g.x-g.x0, g.y-g.y0) > 8){ clearTimeout(g.minuterie); glisse=null; }
    return;
  }
  if(!g.parti){
    /* Quelques pixels avant de partir : un simple appui reste un appui. */
    if(Math.hypot(g.x-g.x0, g.y-g.y0) < 6) return;
    g.parti=true;
    g.t.classList.add('glissee'); $('hub-liste').classList.add('en-glisse');
    g.raf=requestAnimationFrame(defilerPresDesBords);
  }
  e.preventDefault();
  suivrePointeur(); chercherPlace();
}, {passive:false});
function finirGlisse(){
  const g=glisse; glisse=null; if(!g) return;
  clearTimeout(g.minuterie); g.t.classList.remove('saisie');
  cancelAnimationFrame(g.raf);
  $('hub-liste').classList.remove('en-glisse');
  if(!g.parti) return;
  /* La tuile se pose a sa place, puis la disposition est enregistree. */
  g.t.style.transition='transform .2s cubic-bezier(.2,.8,.2,1)';
  g.t.classList.remove('glissee');
  g.t.style.transform='';
  const ordre=ordreDesTuiles();
  setTimeout(()=>{
    g.t.style.transition='';
    if(ordre.join('|')===g.avant) return;
    const parCle=Object.fromEntries(elementsDuHub().map(x=>[cleElement(x), x]));
    enregistrerDisposition(ordre.map(k=>parCle[k]).filter(Boolean));
  }, 220);
}
document.addEventListener('pointerup', finirGlisse);
document.addEventListener('pointercancel', finirGlisse);
/* Echap termine la reorganisation. */
document.addEventListener('keydown', e=>{
  if(e.key==='Escape' && hubEdition && !document.querySelector('.ov.show')) basculerEdition();
});

/* ---- la galerie des widgets, comme celle d'un Mac : a gauche ce qu'on
   peut poser (le hub, puis chaque application), a droite les tailles,
   dessinees a leur proportion ; un clic pose le widget en tete du hub. ---- */
let galerieChoix='une';
function ouvrirGalerie(){ peindreGalerie(); $('ov-galerie').classList.add('show'); }
function peindreGalerie(){
  const hub=hubCatalogue.filter(c=>c.portee==='hub');
  const parApp=hubCatalogue.filter(c=>c.portee==='app');
  const apps=hubApps.slice().sort((x,y)=>nomAffiche(x).localeCompare(nomAffiche(y),'fr'));
  const items=hub.map(c=>({cle:c.id, titre:c.titre, icone:''}))
    .concat(parApp.length ? apps.map(a=>({cle:'app:'+a.name, titre:nomAffiche(a), icone:icone(a.name)})) : []);
  if(!items.some(i=>i.cle===galerieChoix)) galerieChoix=items[0] ? items[0].cle : '';
  const choisi=items.find(i=>i.cle===galerieChoix);
  const estApp=galerieChoix.startsWith('app:');
  const def=estApp ? parApp[0] : hubCatalogue.find(c=>c.id===galerieChoix);
  const appNom=estApp ? galerieChoix.slice(4) : null;
  const deja=elementsDuHub().filter(e=>e.t==='w' && e.id===(def&&def.id) && (e.app||null)===appNom);
  $('galerie-liste').innerHTML=`<div class="gl-titre">Hub</div>`
    + items.filter(i=>!i.cle.startsWith('app:')).map(i=>`<button type="button" class="gl-item" aria-pressed="${i.cle===galerieChoix}" data-agir="galerie-choisir" data-cible="${esc(i.cle)}">${esc(i.titre)}</button>`).join('')
    + (items.some(i=>i.cle.startsWith('app:')) ? `<div class="gl-titre">Applications</div>` : '')
    + items.filter(i=>i.cle.startsWith('app:')).map(i=>`<button type="button" class="gl-item" aria-pressed="${i.cle===galerieChoix}" data-agir="galerie-choisir" data-cible="${esc(i.cle)}"><img src="${i.icone}" alt="">${esc(i.titre)}</button>`).join('');
  $('galerie-detail').innerHTML = !def ? '' : `<h4>${esc(estApp ? def.titre+' — '+choisi.titre : def.titre)}</h4>
    <p class="hint">${esc(def.description)}</p>
    <div class="gl-tailles">${def.tailles.map(t=>`<button type="button" class="gl-taille gl-${t}" data-agir="galerie-ajouter" data-cible="${esc(def.id)}" data-taille="${t}" ${appNom ? `data-app="${esc(appNom)}"` : ''}
        aria-label="Ajouter « ${esc(def.titre)} » en ${TAILLES_NOM[t].toLowerCase()}">
        <span class="gl-forme">${estApp ? `<img src="${icone(appNom)}" alt="">` : ''}<b>${esc(estApp ? choisi.titre : def.titre)}</b></span>
        <span class="gl-nom">${TAILLES_NOM[t]}</span></button>`).join('')}</div>
    ${deja.length ? '<p class="hint">Déjà sur ton hub : l\'ajouter remplace sa taille.</p>' : ''}`;
}
function galerieAjouter(id, taille, app){
  const nouveau={t:'w', id, taille}; if(app) nouveau.app=app;
  const cle=cleElement(nouveau);
  enregistrerDisposition([nouveau].concat(elementsDuHub().filter(e=>cleElement(e)!==cle)));
  hide('ov-galerie'); notifier('Widget ajouté.');
  $('hub-liste').scrollLeft=0;
}

/* L'application ouverte : retenue par ce navigateur, et signalee au serveur
   -- qui note la version vue (indicateur « Nouvelle version ») et la date
   (widget « A la une »). keepalive : la page s'en va aussitot. */
$('hub-liste').addEventListener('click', e=>{
  const t = e.target.closest('[data-app]');
  /* Les trois points sont DANS la tuile : les ouvrir n'ouvre pas l'application. */
  if(!t || hubEdition || e.target.closest('.btn-menu-app')) return;
  try{ localStorage.setItem('codelab-hub-derniere', t.dataset.app); }catch(err){}
  if(hubApps.some(a=>a.name===t.dataset.app))
    fetch('/api/mes-apps/'+encodeURIComponent(t.dataset.app)+'/ouverte', {method:'POST', keepalive:true}).catch(()=>{});
});

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
const AP_ETATS=[['','Toutes'],['on','En ligne'],['off','Arrêtées'],['souci','À surveiller']];
const AP_COLONNES=[['nom','Application'],['etat','État'],['visibilite','Visibilité'],['categorie','Catégorie'],
  ['port','Port'],['cpu','CPU'],['memoire','Mémoire']];
const RANG_ETAT={err:0, warn:1, on:2, off:3};
/* Sans categorie : en fin de liste. */
const FIN_DU_TRI='\uffff';
function apValeur(a, cle){
  if(cle==='nom') return nomAffiche(a).toLowerCase();
  if(cle==='etat') return RANG_ETAT[etatApp(a)[0]] ?? 4;
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
  return apps.filter(a=>{
    const c=etatApp(a)[0];
    if(apEtat==='on' && c!=='on') return false;
    if(apEtat==='off' && c!=='off') return false;
    if(apEtat==='souci' && !['err','warn'].includes(c)) return false;
    if(vis && (a.visibility||'privee')!==vis) return false;
    if(cat && (a.categorie||'')!==(cat==='-' ? '' : cat)) return false;
    return !q || (a.name+' '+nomAffiche(a)+' '+(a.description||'')+' '+(a.categorie||'')+' '+a.port).toLowerCase().includes(q);
  }).sort((x,y)=>{
    const a=apValeur(x,apTriCle), b=apValeur(y,apTriCle);
    const r = typeof a==='number' && typeof b==='number' ? a-b : String(a).localeCompare(String(b),'fr');
    return r*apTriSens || nomAffiche(x).localeCompare(nomAffiche(y),'fr');
  });
}
function apOutils(){
  remplir($('ap-etats'), AP_ETATS.map(([v,t])=>{
    const n = v==='' ? apps.length : apps.filter(a=>{ const c=etatApp(a)[0];
      return v==='souci' ? ['err','warn'].includes(c) : c===v; }).length;
    return `<button aria-pressed="${apEtat===v}" data-agir="ap-etat" data-cible="${v}">${t} <b>${n}</b></button>`;
  }).join(''));
  const cats=[...new Set(apps.map(a=>a.categorie||''))].sort((a,b)=>a.localeCompare(b,'fr'));
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
  return (a.externe || a.name === 'diagnostic') ? '' : `<button class="icon-btn ${a.running?'stop':'ok'}" title="${a.running?'Arrêter':'Démarrer'}"
    aria-label="${a.running?'Arrêter':'Démarrer'} ${esc(nomAffiche(a))}" data-agir="app-basculer" data-cible="${esc(a.name)}">${a.running?SVG_PAUSE:SVG_LECTURE}</button>`;
}
function apJauge(v, max, texte){
  const pc=Math.max(2, Math.min(100, max ? v/max*100 : 0));
  return `<div class="ap-jauge"><span>${texte}</span><i><b style="width:${pc}%"></b></i></div>`;
}
function render(){
  apOutils();
  if(!apps.length){
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
          : `<div class="ap-jauges">${apJauge(a.cpu_percent||0, 100, 'CPU '+(a.cpu_percent||0).toFixed(1)+' %')}
              ${apJauge(a.memory_mb||0, a.max_memory_mb||memMax, 'Mémoire '+Math.round(a.memory_mb||0)+' Mo')}</div>`}
        <div class="ap-carte-pied"><span class="tbl-num">:${esc(String(a.port))}</span><div class="spacer"></div>
          ${apBascule(a)}
          <button class="icon-btn" title="Ouvrir la fiche" aria-label="Ouvrir la fiche de ${esc(nomAffiche(a))}"
            data-agir="app-ouvrir" data-cible="${esc(a.name)}"><svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg></button></div>
      </article>`;
    }).join('')}</div>`);
    return;
  }
  const avecCat=apps.some(a=>a.categorie);
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
      <td class="tbl-num">${a.port}</td>
      <td class="tbl-num">${a.externe ? '—' : (a.cpu_percent||0).toFixed(1)+'%'}</td>
      <td class="tbl-num">${a.externe ? '—' : Math.round(a.memory_mb||0)+' Mo'}</td>
      <td class="tbl-act"><div>${apBascule(a)}
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
    "L'application disparait du panneau. Le dossier du projet dans /workspace n'est pas touche.",
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
  ficheNom=null; showSection('apps'); refresh();
}

/* ---------------- fiche d'une application ---------------- */
/* Tout ce qui concerne une application au meme endroit : son etat, ses
   metriques, son journal, sa configuration et les actions. Avant, ces quatre
   choses vivaient dans trois pop-up differentes qu'il fallait ouvrir une par
   une pour se faire une idee. */
let ficheNom=null, ficheOngletActif='general';

function appParNom(n){ return apps.find(a=>a.name===n); }

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
    muette:['Elle ne répond pas', "Elle tourne, mais rien n'écoute sur son port : elle doit écouter sur $PORT et 0.0.0.0. Si elle démarre encore, patiente quelques secondes."],
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
  $('fa-icone').src=icone(a.name);
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
  $('fa-ecoute').textContent = externe
    ? (a.running ? 'Le port répond' : 'Le port ne répond pas')
    : (!a.running ? 'Arrêtée'
       : (a.listening===false ? 'Le port ne répond pas' : 'Le port répond'));
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


/* ---------------- cles d'acces ---------------- */
/* Le navigateur echange des ArrayBuffer, le serveur du base64url : ces deux
   fonctions sont toute la traduction. */
const b64urlVersOctets = s => Uint8Array.from(
  atob(s.replace(/-/g,'+').replace(/_/g,'/')), c=>c.charCodeAt(0));
const octetsVersB64url = b => btoa(String.fromCharCode(...new Uint8Array(b)))
  .replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');

/* ---------------- notifications et installation ----------------
   LES NOTIFICATIONS DE CET APPAREIL (Web Push). Le serveur donne sa cle
   publique ; le navigateur s'abonne aupres de son service de notification
   (Apple, Google, Mozilla) et rend une adresse, que le panneau garde pour
   CE compte et CETTE session. La permission se demande DANS le geste
   (l'interrupteur) : Safari refuse une demande qui n'en vient pas, d'ou la
   cle chargee a l'avance. */
let notifEtat=null;
const surIOS = () => /iP(hone|ad|od)/.test(navigator.userAgent)
  || (navigator.platform==='MacIntel' && navigator.maxTouchPoints>1);
function notifEmpechement(){
  if(!window.isSecureContext) return "Les notifications exigent une connexion HTTPS : le navigateur les refuse en clair. Mets le TLS en place, puis reviens ici.";
  if(!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window))
    return surIOS() && !enModeApp
      ? "Sur iPhone et iPad, installe d'abord CodeLab sur l'écran d'accueil (carte « Installer CodeLab »), puis active-les depuis l'icône."
      : "Ce navigateur ne sait pas recevoir de notifications.";
  if(Notification.permission==='denied') return "Bloquées pour ce site dans les réglages du navigateur : autorise-les, puis reviens ici.";
  if(notifEtat && !notifEtat.disponible) return "Le serveur n'a pas de clé de chiffrement (APP_MANAGER_CHIFFREMENT_CLE) : il ne peut pas signer ses notifications.";
  return '';
}
async function chargerNotifications(){
  try{ notifEtat=await (await fetch('/api/notifications')).json(); }catch(e){ return; }
  peindreNotifications();
}
function peindreNotifications(){
  const b=$('notif-bascule'); if(!b || !notifEtat) return;
  $('notif-quoi').textContent = estAdmin
    ? 'Sur cet appareil : les nouveaux messages, les applications qui tombent et reviennent.'
    : 'Sur cet appareil : l’avancement des messages que tu envoies.';
  const pourquoi=notifEmpechement();
  b.checked = !!notifEtat.abonne;
  b.disabled = !notifEtat.abonne && !!pourquoi;
  $('notif-empechement').style.display = pourquoi && !notifEtat.abonne ? 'block' : 'none';
  $('notif-empechement').innerHTML = pourquoi ? '<b>Indisponible ici.</b> '+esc(pourquoi) : '';
  $('notif-essai').style.display = notifEtat.abonne ? '' : 'none';
}
const octetsDeB64 = t => Uint8Array.from(atob(t.replace(/-/g,'+').replace(/_/g,'/') + '='.repeat((4-t.length%4)%4)), c=>c.charCodeAt(0));
async function notifBasculer(b){
  b.disabled=true;
  try{
    if(b.checked){
      const perm=await Notification.requestPermission();
      if(perm!=='granted'){ notifier("Notifications refusées par l'appareil.",'err'); return; }
      const reg=await navigator.serviceWorker.register('/sw.js');
      await navigator.serviceWorker.ready;
      let ab=await reg.pushManager.getSubscription();
      /* Un abonnement fait pour une autre cle (serveur reinstalle) ne
         recevrait rien : on le remplace. */
      const cle=octetsDeB64(notifEtat.cle);
      const sienne=ab && ab.options && ab.options.applicationServerKey;
      if(ab && sienne && new Uint8Array(sienne).join() !== cle.join()){ await ab.unsubscribe(); ab=null; }
      /* Le service de notification de l'appareil (Apple, Google, Mozilla)
         peut ne jamais repondre -- reseau filtre, navigateur sans ce service :
         au bout de 20 s, on le dit au lieu de laisser l'interrupteur fige. */
      ab = ab || await Promise.race([
        reg.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:cle}),
        new Promise((_, non)=>setTimeout(()=>non(new Error('delai')), 20000))]);
      const r=await fetch('/api/notifications/abonnement',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(ab.toJSON())});
      if(!r.ok){ notifier(await echec(r,'Abonnement refusé.'),'err'); return; }
      notifier('Notifications activées sur cet appareil.');
    }else{
      await fetch('/api/notifications/abonnement',{method:'DELETE'});
      const reg=await navigator.serviceWorker.getRegistration();
      const ab=reg && await reg.pushManager.getSubscription();
      if(ab) await ab.unsubscribe().catch(()=>{});
      notifier('Notifications coupées sur cet appareil.');
    }
  }catch(e){
    notifier(e && e.message==='delai'
      ? "Le service de notification de cet appareil ne répond pas : réessaie plus tard, ou depuis un autre réseau."
      : "Les notifications n'ont pas pu être réglées sur cet appareil.",'err');
  }finally{
    await chargerNotifications();
  }
}
async function notifEssai(bouton){
  bouton.disabled=true;
  try{
    const r=await fetch('/api/notifications/essai',{method:'POST'});
    notifier(r.ok ? 'Notification envoyée : elle arrive dans quelques secondes.' : await echec(r,'Envoi impossible.'), r.ok ? undefined : 'err');
  }finally{ bouton.disabled=false; }
}
/* LA PASTILLE SUR L'ICONE de l'application installee (et du Dock) : ce qui
   attend -- les messages a lire pour l'administrateur, les nouvelles d'un
   message pour un compte. Rien la ou le navigateur ne la connait pas. */
function majPastilleIcone(){
  if(!('setAppBadge' in navigator)) return;
  const n = estAdmin ? ((hubServices.find(s=>s.name==='messages')||{}).non_lues||0) : (mesNouveaux||0);
  try{ (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(()=>{}); }catch(e){}
}
/* INSTALLER CODELAB, depuis le navigateur. Chrome, Edge et Android le
   proposent eux-memes (l'evenement est garde par mode.js) ; Safari ne
   l'expose pas : la carte dit le geste. Installe, rien a proposer. */
function peindreInstallation(){
  const zone=$('zone-installer'); if(!zone) return;
  const proposee=!!window.CODELAB_INSTALLATION;
  const safari=/Safari/.test(navigator.userAgent) && !/Chrome|Chromium|CriOS|FxiOS|EdgiOS|Edg\//.test(navigator.userAgent);
  const consigne = proposee ? ''
    : surIOS() ? (safari ? 'Touche le bouton Partager <b>⬆︎</b>, puis « Sur l’écran d’accueil ».'
                         : 'Ouvre cette page dans Safari, puis Partager <b>⬆︎</b> › « Sur l’écran d’accueil ».')
    : safari ? 'Dans la barre des menus : Fichier › « Ajouter au Dock ».'
    : '';
  zone.hidden = enModeApp || (!proposee && !consigne);
  $('inst-bouton').style.display = proposee ? '' : 'none';
  $('inst-consigne').style.display = consigne ? '' : 'none';
  $('inst-consigne').innerHTML = consigne;
}
async function installerCodelab(){
  const invite=window.CODELAB_INSTALLATION; if(!invite) return;
  invite.prompt();
  try{ await invite.userChoice; }catch(e){}
  window.CODELAB_INSTALLATION=null;
  peindreInstallation();
}
window.addEventListener('codelab-installable', peindreInstallation);
window.addEventListener('appinstalled', ()=>{ window.CODELAB_INSTALLATION=null; peindreInstallation();
  notifier('CodeLab est installé : ouvre-le depuis son icône.'); });

async function chargerPasskeys(){
  let etat={}, liste={};
  try{
    etat=await (await fetch('/api/passkeys/etat')).json();
    liste=await (await fetch('/api/mon-compte/passkeys')).json();
  }catch(e){ return; }
  /* Le navigateur a aussi son mot a dire : sans PublicKeyCredential, meme
     un serveur pret ne peut rien faire. */
  const possible = etat.possible && !!window.PublicKeyCredential;
  const cles=liste.passkeys||[];
  const actif=cles.length>0;
  $('pk-titre').textContent = nomDuGeste().replace(/^ton empreinte$/,'Empreinte')
    .replace(/^une clé d'accès$/,'Clé d\'accès');
  const b=$('pk-bascule');
  b.checked=actif;
  /* Eteindre reste toujours possible ; allumer demande un serveur en HTTPS
     et un appareil qui sait verifier la personne. */
  b.disabled = !actif && !possible;
  $('pk-ajouter').style.display = (actif && possible) ? '' : 'none';
  $('pk-empechement').style.display = (possible || actif) ? 'none' : 'block';
  $('pk-empechement').innerHTML = possible ? '' :
    ('<b>Indisponible ici.</b> ' + esc(etat.empechement
      || 'Ce navigateur ne connait pas les clés d\'accès.'));

  $('pk-liste').innerHTML = cles.length ? cles.map(k=>`<div class="pk-ligne">
    <svg viewBox="0 0 24 24"><circle cx="8" cy="12" r="4"/><path d="M12 12h9"/>
    <path d="M17 12v3"/><path d="M20.5 12v2"/></svg>
    <div><b>${esc(k.nom)}</b>
      <div class="pk-quand">Ajoutée ${esc(ilYA(k.cree))}${
        k.dernier ? ' — dernière utilisation ' + esc(ilYA(k.dernier)) : ' — jamais utilisée'}</div></div>
    <div class="spacer"></div>
    <button class="btn btn-default btn-sm" data-agir="passkey-retirer" data-cible="${esc(k.id)}">Retirer</button>
  </div>`).join('') : '';
}

function pkMessage(texte, erreur){
  $('pk-err').style.display = erreur ? 'block' : 'none';
  $('pk-msg').style.display = (!erreur && texte) ? 'block' : 'none';
  (erreur ? $('pk-err') : $('pk-msg')).textContent = texte;
}

async function pkAjouter(){
  pkMessage('', false);
  try{
    const r=await fetch('/api/mon-compte/passkeys/options',{method:'POST'});
    const o=await r.json();
    if(!r.ok){ pkMessage(o.error||'Préparation impossible.', true); return; }
    /* Le serveur envoie du base64url ; l'API du navigateur veut des octets. */
    o.challenge=b64urlVersOctets(o.challenge);
    o.user.id=b64urlVersOctets(o.user.id);
    (o.excludeCredentials||[]).forEach(c=>{ c.id=b64urlVersOctets(c.id); });
    const cred=await navigator.credentials.create({publicKey:o});
    const envoi={
      id:cred.id, rawId:octetsVersB64url(cred.rawId), type:cred.type,
      response:{
        clientDataJSON:octetsVersB64url(cred.response.clientDataJSON),
        attestationObject:octetsVersB64url(cred.response.attestationObject),
      },
    };
    const r2=await fetch('/api/mon-compte/passkeys',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({credential:envoi, nom:$('pk-nom').value.trim() || nomDeCle()})});
    const d2=await r2.json();
    if(!r2.ok){ pkMessage(d2.error||'Enregistrement impossible.', true); return; }
    $('pk-nom').value='';
    await chargerPasskeys();
    pkMessage('', false);
    notifier(nomDeCle() + ' activé pour tes prochaines connexions.');
  }catch(e){
    /* Annulation de l'utilisateur comprise : ce n'est pas une panne. */
    pkMessage(e && e.name==='NotAllowedError'
      ? 'Enregistrement annule.' : ('Enregistrement impossible : ' + (e&&e.message||e)), true);
  }
}

function nomDeCle(){
  return nomDuGeste().replace(/^ton empreinte$/,'Empreinte').replace(/^une clé d'accès$/,'Clé d\'accès');
}

/* L'interrupteur. Allumer enregistre cet appareil ; eteindre retire TOUTES
   les cles du compte, apres confirmation. En cas d'echec ou de refus, il
   revient de lui-meme a l'etat reel (chargerPasskeys). */
async function pkBasculer(boite){
  pkMessage('', false);
  if(boite.checked){
    boite.disabled=true;
    await pkAjouter();
    boite.disabled=false;
    await chargerPasskeys();
    return;
  }
  if(!await demander('Désactiver ' + nomDeCle() + ' ?',
      "Les clés d'accès de ce compte sont retirées de tous tes appareils. Tu te connecteras "
      + "avec ton mot de passe et un code. Tu pourras le réactiver à tout moment.",
      'Désactiver', true)){
    boite.checked=true; return;
  }
  const r=await fetch('/api/mon-compte/passkeys',{method:'DELETE'});
  const d=await r.json().catch(()=>({}));
  if(!r.ok) pkMessage(d.error||'Désactivation impossible.', true);
  else notifier(nomDeCle() + ' désactivé.');
  chargerPasskeys();
}

async function pkSupprimer(id){
  if(!await demander("Retirer cette clé d'accès ?",
    "L'appareil qui la porte n'ouvrira plus ce compte. Tu peux en enregistrer une autre a tout moment.",
    'Retirer')) return;
  const r=await fetch('/api/mon-compte/passkeys/'+encodeURIComponent(id),{method:'DELETE'});
  const d=await r.json();
  if(!r.ok){ pkMessage(d.error||'Suppression impossible.', true); return; }
  chargerPasskeys();
}

/* ---------------- journal des acces ---------------- */
let activite={evenements:[], resume:{comptes:{}, apps:{}, echecs:0}};

/* Une date lisible sans reflechir : « il y a 3 min », « hier a 14 h ». Un
   horodatage complet demande a etre compare mentalement a l'heure qu'il est. */
function ilYA(ts){
  if(!ts) return 'jamais';
  const s=Math.max(0, Math.floor(Date.now()/1000)-ts);
  if(s<60) return "à l'instant";
  if(s<3600) return 'il y a '+Math.floor(s/60)+' min';
  if(s<86400) return 'il y a '+Math.floor(s/3600)+' h';
  if(s<2592000) return 'il y a '+Math.floor(s/86400)+' j';
  return new Date(ts*1000).toLocaleDateString();
}
function quandPrecis(ts){ return new Date(ts*1000).toLocaleString(); }

/* ---------------- carte de chaleur ---------------- */
/* Les paliers sont relatifs a la journee la plus chargee de la fenetre
   regardee : un seuil absolu ("4 ouvertures = fonce") afficherait une carte
   toute pale sur une installation calme et toute sombre sur une installation
   chargee. Ce qu'on veut lire, c'est le RELIEF. */
function carteNiveau(n, sommet){
  if(!n) return 0;
  if(sommet <= 1) return 4;
  const part = n / sommet;
  return part > .75 ? 4 : part > .5 ? 3 : part > .25 ? 2 : 1;
}

const CARTE_MOIS=['janv.','févr.','mars','avr.','mai','juin','juil.','août',
                  'sept.','oct.','nov.','déc.'];

/* Les annees offertes viennent du serveur : de la premiere utilisation a
   l'annee en cours. Les recalculer ici demanderait de connaitre tout
   l'historique, que la page n'a pas -- et une liste devinee finirait par
   proposer une annee vide ou en oublier une pleine. */
function peindreAnnees(d){
  const sel=$('carte-annee');
  const annees=(d.annees||[]).slice().sort((a,b)=>b-a);
  if(!annees.length) return;
  const voulue=String(d.annee||annees[0]);
  const deja=[...sel.options].map(o=>o.value).join('|');
  if(deja!==annees.map(String).join('|')){
    sel.innerHTML=annees.map(a=>`<option value="${a}">${a}</option>`).join('');
  }
  sel.value=voulue;
}

function carteJourCle(d){
  /* Date locale, et non toISOString() qui repasse en UTC : sur un fuseau a
     l'est de Greenwich, le 1er janvier redeviendrait le 31 decembre. */
  const m=String(d.getMonth()+1).padStart(2,'0'), j=String(d.getDate()).padStart(2,'0');
  return d.getFullYear()+'-'+m+'-'+j;
}

/* Les colonnes de la carte : une par semaine, du lundi qui precede le
   1er janvier au 31 decembre, chaque case portant son niveau et son titre.
   Rend aussi, pour chaque colonne, le mois auquel elle appartient. */
function carteColonnes(annee, parJour, sommet){
  const premierJanvier=new Date(annee,0,1); premierJanvier.setHours(12,0,0,0);
  const trenteEtUnDecembre=new Date(annee,11,31); trenteEtUnDecembre.setHours(12,0,0,0);
  const aujourdhui=new Date(); aujourdhui.setHours(12,0,0,0);
  /* On remonte au lundi qui precede le 1er janvier : sans cela, la premiere
     colonne serait tronquee et tous les jours de la semaine se decaleraient
     d'une ligne. */
  const depart=new Date(premierJanvier);
  depart.setDate(depart.getDate()-((premierJanvier.getDay()+6)%7));

  const cases=[]; const moisParColonne=[]; let colonne=[]; let moisCourant=null;
  for(let d0=new Date(depart); d0<=trenteEtUnDecembre; d0.setDate(d0.getDate()+1)){
    if(d0<premierJanvier){
      /* Avant le 1er janvier : la case garde la place sans rien affirmer. */
      colonne.push('<div class="carte-case vide"></div>');
    }else{
      if(moisCourant===null) moisCourant=d0.getMonth();
      const cle=carteJourCle(d0);
      const e=parJour[cle];
      const n=e?e.ouvertures:0;
      if(d0>aujourdhui){
        /* A VENIR. Une case grise dirait « aucune ouverture » d'un jour qui
           n'est pas arrive : c'est un mensonge, et il peint la fin de
           l'annee en creux. */
        colonne.push(`<div class="carte-case avenir" title="${esc(d0.toLocaleDateString())} — à venir"></div>`);
      }else{
        const titre = n
          ? `${n} ouverture${n>1?'s':''} le ${d0.toLocaleDateString()}`
            + (e.apps>1 ? ` — ${e.apps} applications` : '')
          : `Aucune ouverture le ${d0.toLocaleDateString()}`;
        colonne.push(`<div class="carte-case n${carteNiveau(n,sommet)}" title="${esc(titre)}"></div>`);
      }
    }
    if(colonne.length===7){
      cases.push(colonne.join('')); colonne=[];
      /* La colonne appartient au mois de son premier jour DANS l'annee :
         c'est ce qui fait tomber « janv. » au-dessus de la colonne ou
         l'annee commence. */
      moisParColonne.push(moisCourant===null?0:moisCourant);
      moisCourant=null;
    }
  }
  if(colonne.length){
    const dernier=moisCourant===null?11:moisCourant;
    while(colonne.length<7) colonne.push('<div class="carte-case vide"></div>');
    cases.push(colonne.join('')); moisParColonne.push(dernier);
  }
  return {cases, moisParColonne};
}

/* Le cote d'une case et l'ecart entre deux cases, pour que les colonnes
   tiennent dans la largeur mesuree (voir peindreCarte). */
function carteTaille(large, colonnes){
  /* L'ECART COMPTE AUTANT QUE LA CASE. Cinquante-deux ecarts de 3 px font
     156 px a eux seuls : sur une carte etroite, ils mangent toute la place
     et la grille deborde meme avec des cases minuscules. On resserre donc
     l'ecart avant de rapetisser la case, et on s'arrete au premier couple
     qui tient -- en visant six pixels de cote, en dessous desquels une case
     ne se distingue plus de son voisin. */
  let cote = 11, ecart = 3;
  if(large > 0){
    let trouve = false;
    for(const e of [3,2,1,0]){
      const c = Math.floor((large - (colonnes-1)*e) / colonnes);
      if(c >= 6){ cote = c; ecart = e; trouve = true; break; }
    }
    if(!trouve){
      /* Vraiment trop etroit : on prend ce qui rentre, jusqu'a deux pixels.
         Une carte reduite se lit encore ; une carte coupee, non. */
      ecart = 0;
      cote = Math.max(2, Math.floor(large / colonnes));
    }
  }
  return {cote, ecart};
}

function peindreCarte(d){
  derniereCarte=d;
  const parJour={}; let total=0, sommet=0;
  for(const j of (d.jours||[])){
    parJour[j.jour]=j; total+=j.ouvertures;
    if(j.ouvertures>sommet) sommet=j.ouvertures;
  }
  /* UNE ANNEE CIVILE, du 1er janvier au 31 decembre. La fenetre glissante de
     366 jours commencait un jour quelconque : les mois se decalaient d'un
     cran chaque jour, et deux cartes prises a deux dates ne se comparaient
     pas. Une annee est fixe -- et elle se choisit. */
  const annee = d.annee || (new Date()).getFullYear();
  const {cases, moisParColonne} = carteColonnes(annee, parJour, sommet);

  /* LA LIGNE DES MOIS PREND TOUTE LA LARGEUR DE LA CARTE. Une etiquette par
     colonne laissait onze mois muets et un mot de dix pixels de large : on
     ne lisait rien. Chaque mois occupe donc exactement ses colonnes, et les
     douze se partagent la grille. */
  const mois=[];
  for(let i=0;i<moisParColonne.length;i++){
    const m=moisParColonne[i];
    if(i && m===moisParColonne[i-1]){ mois[mois.length-1].n++; continue; }
    mois.push({m:m, n:1});
  }
  const colonnes=cases.length;
  /* LA TAILLE D'UNE CASE SE CALCULE, elle ne se declare pas.
     Onze pixels par semaine debordaient d'une carte etroite, et la moitie de
     l'annee partait derriere une barre de defilement -- qu'on ne fait jamais
     glisser. Des colonnes en « 1fr » ne marchent pas davantage : la hauteur
     d'une case viendrait alors de sa largeur (aspect-ratio), qui vient de la
     largeur de la grille, qui vient de la hauteur... le navigateur tranche
     ce cercle comme il peut, et rendait des cases de 54 px.
     On mesure donc la place disponible et on en deduit un cote, en pixels,
     pose sur les colonnes ET sur les lignes. */
  const large = $('carte-zone').clientWidth
    || $('carte-zone').getBoundingClientRect().width;
  const {cote, ecart} = carteTaille(large, colonnes);
  const gabarit=`gap:${ecart}px;grid-template-columns:repeat(${colonnes},${cote}px);`
    + `grid-template-rows:repeat(7,${cote}px)`;
  const gabaritMois=`gap:${ecart}px;grid-template-columns:repeat(${colonnes},${cote}px)`;
  $('carte-zone').innerHTML =
    `<div class="carte-mois" style="${gabaritMois}">`
    + mois.map(x=>`<span style="grid-column:span ${x.n}">${CARTE_MOIS[x.m]}</span>`).join('')
    + `</div>`
    + `<div class="carte-grille" style="${gabarit}">${cases.join('')}</div>`;

  const jours=Object.keys(parJour).length;
  $('carte-total').textContent = total
    ? `${total} ouverture${total>1?'s':''} sur ${jours} jour${jours>1?'s':''}`
      + (sommet>1 ? ` — au plus ${sommet} dans une journée.` : '.')
    : "Aucune ouverture d'application sur la période.";

  /* D'ou viennent les chiffres. La base locale du panneau ne garde que les
     50 000 derniers evenements : une carte batie sur elle peut montrer des
     cases vides qui ne prouvent aucune inactivite, et le taire serait mentir
     par omission. */
  const src=$('carte-source');
  if(d.source==='postgres'){ src.style.display='none'; }
  else{
    src.style.display='block';
    src.innerHTML="Historique lu dans la <b>base locale</b> du panneau, qui garde "
      + "les 50 000 derniers événements : les jours vides les plus anciens peuvent "
      + "simplement avoir été oubliés. Postgres garde tout.";
  }
}

/* La derniere reponse, gardee pour repeindre a l'identique : la taille des
   cases depend de la largeur de la fenetre, et une carte calculee pour 1400
   px deborde a 900. Repeindre ne redemande rien au serveur. */
let derniereCarte=null, repeintureCarte=null;
window.addEventListener('resize', ()=>{
  if(!derniereCarte) return;
  clearTimeout(repeintureCarte);
  repeintureCarte=setTimeout(()=>peindreCarte(derniereCarte), 120);
});

async function chargerCarte(){
  /* La liste des personnes vient du resume du journal. Il n'est charge qu'a
     l'ouverture de la page Utilisateurs : sans cette ligne, le filtre
     n'aurait aucun nom tant qu'on n'y serait pas passe. */
  if(!Object.keys(activite.resume.comptes||{}).length) await chargerActivite();
  /* Les deux listes deroulantes suivent ce qui existe, sans perdre le choix
     en cours. */
  const selQui=$('carte-qui'), selApp=$('carte-app');
  const gardeQui=selQui.value, gardeApp=selApp.value;
  const comptes=Object.keys((activite&&activite.resume&&activite.resume.comptes)||{})
    .filter(n=>n).sort();
  selQui.innerHTML='<option value="">Tout le monde</option>'
    + comptes.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
  selQui.value = comptes.includes(gardeQui) ? gardeQui : '';
  const noms=(apps||[]).map(a=>a.name).sort();
  selApp.innerHTML='<option value="">Toutes les applications</option>'
    + noms.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
  selApp.value = noms.includes(gardeApp) ? gardeApp : '';

  const p=new URLSearchParams();
  if(selQui.value) p.set('qui', selQui.value);
  if(selApp.value) p.set('app', selApp.value);
  /* L'annee choisie tient dans la page : le serveur renvoie la liste des
     annees proposees a chaque reponse, et c'est elle qui fait autorite. */
  if($('carte-annee').value) p.set('annee', $('carte-annee').value);
  try{
    const d=await (await fetch('/api/activite/carte?'+p.toString())).json();
    peindreAnnees(d);
    peindreCarte(d);
  }catch(e){
    $('carte-zone').innerHTML='<div class="hint">Carte indisponible.</div>';
  }
}

async function chargerActivite(){
  try{ activite=await (await fetch('/api/activite')).json(); }catch(e){ return; }
  peindreConnexions();
  peindreComptesActivite();
}
function ligneActivite(e){
  const qui = e.qui ? esc(e.qui) : 'visiteur anonyme';
  let quoi;
  if(e.genre==='connexion'){
    quoi = 's\'est connecte' + (e.role==='admin' ? ' (administrateur)' : '');
    /* Par quel moyen : un mot de passe vole ne produit pas la meme ligne
       qu'une cle d'acces, et c'est ce qu'on regarde en cas de doute. */
    if(e.moyen) quoi += ' par ' + esc(e.moyen);
  }
  else if(e.genre==='echec') quoi = '<b style="color:var(--err)">connexion refusée</b> — ' + esc(e.motif||'');
  else if(e.genre==='ouverture') quoi = 'a ouvert <b>' + esc(e.app||'') + '</b>';
  /* Les cles d'acces ajoutees ou retirees passaient par la branche des
     ouvertures : le journal affichait « a ouvert » suivi de rien. */
  else if(e.genre==='passkey') quoi = "clé d'accès — " + esc(e.action||'');
  else quoi = esc(e.genre||'');
  return `<tr>
    <td class="act-quand" title="${esc(quandPrecis(e.ts))}">${esc(ilYA(e.ts))}</td>
    <td class="act-qui">${qui}</td>
    <td class="act-quoi">${quoi}</td>
    <td class="act-ip">${esc(e.ip||'—')}</td></tr>`;
}
/* Le journal, en tableau. Vide, il rend la phrase qui dit ce qui le
   remplira -- pas un tableau a une seule case. */
function tableauActivite(evenements, vide){
  if(!evenements.length) return `<div class="act-vide">${vide}</div>`;
  return `<div class="tbl-wrap"><div class="tbl-scroll"><table class="tbl tbl-journal">
    <thead><tr><th>Quand</th><th>Qui</th><th>Evenement</th><th>Adresse</th></tr></thead>
    <tbody>${evenements.map(ligneActivite).join('')}</tbody></table></div></div>`;
}
/* Le journal des connexions, page par page.

   C'est LUI qui est long : il grandit a chaque visite, la ou une liste de
   comptes tient a l'ecran. Le reglage 25/50 etait pose sur les comptes, ou
   il ne servait a rien ; il est ici.

   Avant, une coupe seche a 40 lignes -- ni reglable, ni annoncee, et les
   lignes au-dela etaient simplement invisibles. */
let acPage=1;

/* Memorise par navigateur : c'est une preference d'affichage, pas un reglage
   du serveur. Une valeur illisible ou un stockage refuse (navigation privee)
   ne doit pas casser la page -- d'ou le try. */
function acParPage(){
  try{
    const v=parseInt(localStorage.getItem('codelab.journal.taille'), 10);
    if(v===10 || v===25 || v===50) return v;
  }catch(e){}
  return 10;
}

/* CE QU'ON REGARDE. Chaque ouverture d'application s'ajoutait aux
   connexions, et la liste debordait de lignes qu'on ne cherchait pas. Par
   defaut : qui est entre, et qui a ete refuse -- la question de securite.
   Les ouvertures sont a un choix. Memorise comme la taille. */
const AC_GENRES={connexions:['connexion','echec','refus'], ouvertures:['ouverture'], tout:null};
function acGenreCourant(){
  try{
    const v=localStorage.getItem('codelab.journal.genre');
    if(v && v in AC_GENRES) return v;
  }catch(e){}
  return 'connexions';
}
function acGenre(){
  try{ localStorage.setItem('codelab.journal.genre', $('ac-genre').value); }catch(e){}
  acPage=1;
  peindreConnexions();
}
/* Au plus deux cents evenements dans la page : au-dela, c'est une recherche,
   et le journal complet reste dans la base (et dans Postgres). */
const AC_MAX=200;

function acTaille(){
  const v=$('ac-taille').value;
  try{ localStorage.setItem('codelab.journal.taille', v); }catch(e){}
  acPage=1;
  peindreConnexions();
}

function acAllerA(n){
  acPage=n;
  peindreConnexions();
  $('ac-connexions').scrollIntoView({block:'nearest', behavior:'smooth'});
}

function peindreConnexions(){
  const genres=AC_GENRES[acGenreCourant()];
  const tous=(activite.evenements||[])
    .filter(e=>!genres || genres.includes(e.genre)).slice(0, AC_MAX);
  const gs=$('ac-genre'); if(gs) gs.value=acGenreCourant();
  const taille=acParPage();
  const pages=Math.max(1, Math.ceil(tous.length/taille));
  /* Rester sur une page qui n'existe plus -- le journal a ete vide, ou la
     taille a double -- donnerait un tableau vide et ferait croire a une
     panne. */
  if(acPage>pages) acPage=pages;
  if(acPage<1) acPage=1;
  const debut=(acPage-1)*taille;
  const tranche=tous.slice(debut, debut+taille);
  $('ac-connexions').innerHTML = tableauActivite(tranche,
    'Rien encore. Le journal se remplit à la première connexion.');

  const sel=$('ac-taille'); if(sel) sel.value=String(taille);
  const barre=$('ac-pagination'); if(!barre) return;
  barre.style.display = pages>1 ? 'flex' : 'none';
  $('ac-prec').disabled = acPage<=1;
  $('ac-suiv').disabled = acPage>=pages;
  $('ac-compte').textContent =
    `${debut+1}\u2013${debut+tranche.length} de ${tous.length} — page ${acPage} sur ${pages}`;
}
/* La derniere connexion de chaque compte, sous son nom : c'est ce qu'on
   cherche en ouvrant cette page, avant meme le journal complet. */
function peindreComptesActivite(){
  const r=(activite.resume||{}).comptes||{};
  document.querySelectorAll('#us-liste [data-activite]').forEach(n=>{
    const c=r[n.dataset.activite];
    /* c.derniere, SANS accent : c'est le nom du champ renvoye par
       /api/activite. En accentuant les textes de l'interface, l'accent
       avait ete pose ici aussi -- la lecture valait alors undefined,
       ilYA(undefined) repond "jamais", et tout compte deja connecte
       s'affichait comme ne s'etant jamais connecte. Un texte accentue se
       lit mieux ; un identifiant accentue ne correspond plus a rien, et se
       tait. Les noms de champs ne s'accentuent pas. */
    n.textContent = c && c.derniere
      ? ` — dernière connexion ${ilYA(c.derniere)}`
      : ' — jamais connecté';
  });
}

/* L'activite d'une application, dans sa fiche. */
function peindreActiviteFiche(){
  const a=((activite.resume||{}).apps||{})[ficheNom] || {ouvertures:0, derniere:0, qui:{}};
  $('ac-total').textContent=a.ouvertures;
  const gens=Object.keys(a.qui||{});
  $('ac-gens').textContent=gens.length;
  $('ac-derniere').textContent = a.derniere
    ? 'Dernière ouverture ' + ilYA(a.derniere) + '.'
    : 'Jamais ouverte depuis que le journal existe.';
  const paires=gens.map(q=>[q, a.qui[q]]).sort((x,y)=>y[1]-x[1]);
  $('ac-repartition').innerHTML = paires.length
    ? paires.map(([q,n])=>`<div class="kv"><span class="k">${q?esc(q):'Visiteurs anonymes'}</span>
        <span class="v">${n}</span></div>`).join('')
    : '<div class="act-vide">Personne pour l\'instant.</div>';
  const propres=(activite.evenements||[]).filter(e=>e.genre==='ouverture' && e.app===ficheNom).slice(0,20);
  $('ac-liste').innerHTML = tableauActivite(propres, 'Aucune ouverture enregistrée.');
}

/* ---------------- comptes utilisateurs ---------------- */
function usErreur(texte){
  const e=$('us-err');
  if(!texte){ e.style.display='none'; return; }
  e.textContent=texte; e.style.display='block';
}

/* La liste complete arrive du serveur, le filtrage se fait ici. C'est le bon
   compromis a cette echelle -- quelques dizaines de comptes sur une machine
   de salon -- et c'est deja ce que fait la recherche d'applications.

   PAS de pagination : elle etait ici, elle n'y servait a rien. Une liste de
   comptes tient a l'ecran, et les deux filtres au-dessus suffisent a s'y
   retrouver. C'est le JOURNAL des connexions qui est long -- il grandit a
   chaque visite -- et c'est lui qui a recu le reglage. */
let usTous=[];

function usFiltrer(){ usPeindreListe(); }

function usRetenus(){
  const q=($('us-q').value||'').trim().toLowerCase();
  /* Plus de filtre par application ici : il doublait l'onglet Acces de la
     fiche d'application, ou la question se REGLE au lieu de se poser. */
  return usTous.filter(c=>{
    if(!q) return true;
    return (c.nom+' '+(c.email||'')).toLowerCase().includes(q);
  });
}

function usLigne(c){
  /* Ce qui empeche quelqu'un d'entrer passe en premier, et en rouge : un
     compte qui n'a jamais confirme son adresse ne se connecte pas. */
  const badges=[];
  /* Suspendu passe avant tout le reste : c'est la seule chose a savoir sur
     ce compte tant qu'il l'est. */
  if(c.suspendu) badges.push('<span class="pill err"><i></i>Suspendu</span>');
  if(c.attente_email) badges.push('<span class="pill err"><i></i>Inscription a confirmer</span>');
  /* Une adresse verifiee suffit desormais : le code arrive par mail. Le
     second facteur n'est en attente que sans adresse, sans application et
     sans cle d'acces. */
  if(!c.totp && !c.email_verifie && !c.passkeys)
    badges.push('<span class="pill off"><i></i>2FA en attente</span>');
  if(c.passkeys) badges.push(`<span class="pill on"><i></i>${c.passkeys} clé${c.passkeys>1?'s':''}</span>`);
  const projets = c.projets.length
    ? c.projets.length + ' projet' + (c.projets.length>1?'s':'')
    : 'aucun projet';
  const adresse = c.email
    ? esc(c.email) + (c.email_verifie ? ' (vérifiée)' : ' (non vérifiée)')
    : 'sans adresse';
  /* La ligne entiere ouvre le compte : un bouton « Modifier » repete sur
     chaque ligne n'apprenait rien de plus que le chevron. */
  return `<div class="us-ligne${c.suspendu?' us-suspendu':''}" data-agir="compte-ouvrir"
    data-cible="${esc(c.nom)}" role="button" tabindex="0" title="Ouvrir le compte">
    <div class="us-rond">${esc((c.nom||'?').slice(0,1))}</div>
    <div class="us-corps">
      <div class="us-nom"><b>${esc(c.nom)}</b>${badges.join('')}</div>
      <div class="us-meta">${adresse} — ${projets}<span data-activite="${esc(c.nom)}"></span></div>
    </div>
    <span class="us-chevron"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
      stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg></span>
  </div>`;
}

function usPeindreListe(){
  const zone=$('us-liste'), barre=$('us-pagination');
  if(!usTous.length){
    zone.innerHTML='<div class="vide"><b>Aucun compte</b>'
      +'Toi seul peux ouvrir les projets privés. Crée un compte pour partager '
      +'un projet avec quelqu\'un sans lui donner l\'administration.</div>';
    barre.style.display='none';
    return;
  }
  const retenus=usRetenus();
  if(!retenus.length){
    /* Dire ce qui a ete cherche : "aucun resultat" tout court laisse penser
       que la liste est vide, alors que c'est le filtre qui l'a videe. */
    zone.innerHTML='<div class="vide"><b>Aucun compte ne correspond</b>'
      +'Ni au texte saisi, ni a l\'application choisie. Efface les filtres '
      +'pour retrouver les '+usTous.length+' comptes.</div>';
    barre.style.display='none';
    return;
  }
  zone.innerHTML=retenus.map(usLigne).join('');
  peindreComptesActivite();

  /* Le compte ne s'affiche que si un filtre agit : il dit alors combien de
     comptes ont ete ecartes. Sans filtre, la liste se compte a l'oeil. */
  const filtre = retenus.length !== usTous.length;
  barre.style.display = filtre ? 'flex' : 'none';
  $('us-compte').textContent =
    `${retenus.length} compte${retenus.length>1?'s':''} sur ${usTous.length}`;
}

async function chargerUtilisateurs(){
  let d;
  try{ d=await (await fetch('/api/utilisateurs')).json(); }catch(e){ return; }
  const comptes=d.utilisateurs||[];
  $('side-count-users').textContent=comptes.length;
  usTous=comptes;
  usComptes=comptes;

  usPeindreListe();
  /* La fiche ouverte suit : les projets ont pu changer pendant qu'elle
     l'etait, et les cases a cocher doivent rester justes. */
  if(usCourant && $('ov-user').classList.contains('show')) usPeindreFiche();
}

/* ---------------- confirmation et notifications ---------------- */
let confirmerResoudre=null;
/* Une promesse plutot qu'un rappel : le code appelant se lit comme avec
   confirm(), en une ligne, sans imbriquer une fonction. */
function demander(titre, texte, libelle, danger){
  $('cf-titre').textContent=titre;
  $('cf-texte').textContent=texte;
  const ok=$('cf-ok');
  ok.textContent=libelle||'Confirmer';
  ok.className='btn ' + (danger ? 'btn-default danger' : 'btn-primary');
  $('ov-confirm').classList.add('show');
  setTimeout(()=>ok.focus(), 40);
  return new Promise(r=>{ confirmerResoudre=r; });
}
function confirmerReponse(reponse){
  hide('ov-confirm');
  const r=confirmerResoudre; confirmerResoudre=null;
  if(r) r(reponse);
}

/* UNE ERREUR RESTE jusqu'a ce qu'on la ferme : elle disparaissait au bout
   de 7 s, souvent avant d'avoir ete lue -- et avec elle la reference a
   transmettre. Un succes, lui, s'en va seul.
   action : {libelle, faire} -- un bouton dans le message (« Annuler »),
   le temps de changer d'avis (8 s). */
function notifier(texte, genre, action){
  const err = genre==='err';
  const n=document.createElement('div');
  n.className='toast ' + (genre||'ok');
  n.setAttribute('role', err ? 'alert' : 'status');
  const icone = err
    ? '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5v.01"/></svg>'
    : '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>';
  n.innerHTML = icone + '<div class="toast-texte">' + esc(texte) + '</div>'
    + (action ? '<button type="button" class="toast-action">' + esc(action.libelle) + '</button>' : '')
    + (err ? '<button type="button" class="toast-fermer" aria-label="Fermer le message">×</button>' : '');
  if(action) n.querySelector('.toast-action').onclick=()=>{ n.remove(); action.faire(); };
  if(err) n.querySelector('.toast-fermer').onclick=()=>n.remove();
  /* Trois erreurs a l'ecran au plus : la plus ancienne laisse sa place. */
  const erreurs=$('toasts').querySelectorAll('.toast.err');
  if(err && erreurs.length>=3) erreurs[0].remove();
  $('toasts').appendChild(n);
  if(!err) setTimeout(()=>n.remove(), action ? 8000 : 4000);
}


function notifierMessage(texte){
  const precedent=$('toast-messages');
  if(precedent) precedent.remove();
  const n=document.createElement('div');
  n.id='toast-messages';
  n.className='toast toast-message ok';
  n.innerHTML='<svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
    +'<div style="flex:1"><b>'+esc(texte)+'</b><div class="hint">Clique pour ouvrir Messages.</div></div>'
    +'<button type="button">Voir</button>';
  n.onclick=()=>{ location.href='/messages'; };
  $('toasts').appendChild(n);
}

/* ---------------- creation d'un compte ---------------- */
function usFormulaire(ouvert){
  $('us-nouveau').style.display = ouvert ? 'block' : 'none';
  usErreur('');
  if(ouvert) setTimeout(()=>$('us-nom').focus(), 40);
}

/* ---------------- fiche d'un compte ---------------- */
let usComptes=[], usCourant='';

async function usOuvrir(nom){
  usCourant=nom;
  usPeindreFiche();
  $('ov-user').classList.add('show');
  /* « Projets autorises » se peint a partir de la liste des applications.
     Si elle n'est pas encore arrivee -- fiche ouverte dans la seconde qui
     suit le chargement de la page, ou premier passage par Utilisateurs --
     la rubrique restait vide, sans rien dire. On va la chercher, puis on
     repeint. */
  if(!apps.length){
    await refresh();
    if(usCourant===nom) usPeindreFiche();
  }
}

function usCompte(){ return usComptes.find(c=>c.nom===usCourant); }

function usPeindreFiche(){
  const c=usCompte();
  if(!c){ hide('ov-user'); return; }
  $('us-f-titre').textContent=c.nom;
  $('us-f-sous').textContent = c.cree
    ? 'Compte créé ' + ilYA(c.cree) + '.'
    : 'Compte utilisateur.';
  $('us-f-err').style.display='none';
  $('us-f-mail').value=c.email||'';
  $('us-f-mdp').value='';
  $('us-f-mail-etat').textContent = !c.email
    ? "Aucune adresse. La personne peut en déclarer une elle-même depuis ses paramètres."
    : (c.attente_email
      ? "Adresse jamais confirmée : ce compte ne peut pas se connecter tant qu'un code envoyé dessus n'est pas revenu."
      : (c.email_verifie
        ? "Adresse vérifiée par un code reçu dessus."
        : "Adresse non vérifiée : la personne la confirmera depuis ses paramètres."));

  /* Une LIGNE par projet declare, avec son icone, sa description et sa
     visibilite : la liste complete rend lisible ce qui est autorise ET ce
     qui ne l'est pas. Une rangee de cases nues ne disait ni de quelle
     application il s'agissait, ni lesquelles etaient publiques -- donc
     lesquelles cocher ne changeait rien. */
  const declares = apps.slice().sort((a,b)=>nomAffiche(a).localeCompare(nomAffiche(b),'fr'));
  $('us-f-projets').innerHTML = declares.length ? declares.map(a=>{
    /* Une application reservee a l'administrateur (Diagnostic...) reste
       dans la liste, pour qu'on sache pourquoi elle manque, mais sa case
       est grisee : le serveur refuserait de toute facon de l'accorder. */
    const reservee = !!a.par_defaut;
    const coche = !reservee && c.projets.includes(a.name) ? ' checked' : '';
    const prive = a.visibility==='privee';
    return `<label class="choix-ligne proj-ligne${prive?'':' publique'}${reservee?' reservee':''}">
      <input type="checkbox" value="${esc(a.name)}"${coche}${reservee?' disabled':''}>
      <img src="${icone(a.name)}" alt="">
      <div class="proj-txt"><b>${esc(nomAffiche(a))}</b>
        ${a.description?`<span>${esc(a.description)}</span>`:''}</div>
      ${reservee ? '<span class="pill priv"><i></i>Réservée à l\'administrateur</span>' : badgeVis(a)}</label>`;
  }).join('') : '<div class="hint">Aucun projet déclaré.</div>';

  const morceaux=[];
  const codes=[];
  if(c.totp) codes.push("son application d'authentification");
  if(c.email && c.email_verifie) codes.push('mail');
  morceaux.push(codes.length
    ? 'Code de connexion par ' + codes.join(', ') + '.'
    : "Second facteur en attente : il sera enregistré à la première connexion.");
  morceaux.push(c.passkeys
    ? `${c.passkeys} clé${c.passkeys>1?'s':''} d'accès enregistrée${c.passkeys>1?'s':''}.`
    : "Aucune clé d'accès.");
  morceaux.push("Tu ne connais jamais ces secrets : en cas d'appareil perdu, remets-les a zero "
    + "et la personne en enregistrera de nouveaux.");
  $('us-f-facteurs').textContent = morceaux.join(' ');
  $('us-f-totp').disabled = !c.totp;
  $('us-f-cles').disabled = !c.passkeys;
  usChargerAppareils(c.nom);

  /* L'etat de l'acces, dit en une phrase, et le bouton qui le change. */
  $('us-f-suspendu-etat').innerHTML = c.suspendu
    ? "<b>Accès suspendu.</b> Ce compte n'ouvre plus rien et ne peut plus se connecter."
    : "Accès ouvert : ce compte peut se connecter et ouvrir ses projets.";
  $('us-f-suspendre').textContent = c.suspendu ? 'Réactiver' : 'Suspendre';
  $('us-f-suspendre').classList.toggle('danger', !c.suspendu);
}

function usFicheErreur(texte){
  const e=$('us-f-err');
  e.textContent=texte||'';
  e.style.display = texte ? 'block' : 'none';
}

async function usEcrire(corps, message){
  usFicheErreur('');
  const r=await fetch('/api/utilisateurs/'+encodeURIComponent(usCourant),{method:'PUT',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(corps)});
  const d=await r.json();
  if(!r.ok){ usFicheErreur(d.error||'Modification impossible.'); return false; }
  await chargerUtilisateurs();
  if(message) usFicheErreur('');
  return true;
}

async function usEnregistrer(){
  const mdp=$('us-f-mdp').value;
  if(mdp && mdp.length<12) return usFicheErreur('Mot de passe : 12 caractères au minimum.');
  const projets=[...document.querySelectorAll('#us-f-projets input:checked')].map(i=>i.value);
  const corps={email:$('us-f-mail').value.trim(), projets};
  if(mdp) corps.mot_de_passe=mdp;
  $('us-f-save').disabled=true;
  try{
    if(await usEcrire(corps)) hide('ov-user');
  } finally { $('us-f-save').disabled=false; }
}

async function usCreer(){
  usErreur('');
  const nom=$('us-nom').value, mdp=$('us-mdp').value, email=$('us-mail').value.trim();
  const r=await fetch('/api/utilisateurs',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({nom, mot_de_passe:mdp, email, projets:[]})});
  const d=await r.json();
  if(!r.ok){ usErreur(d.error||'Création impossible.'); return; }
  $('us-nom').value=''; $('us-mdp').value=''; $('us-mail').value='';
  usFormulaire(false);
  await chargerUtilisateurs();
  notifier('Compte « ' + d.nom + ' » créé. Ouvre-le pour lui autoriser des projets.');
}

/* ---------------- invitations ---------------- */
function invFormulaire(ouvert){
  $('inv-nouveau').style.display = ouvert ? 'block' : 'none';
  $('inv-err').style.display='none';
  $('inv-resultat').style.display='none';
  if(ouvert){ usFormulaire(false); setTimeout(()=>$('inv-mail').focus(), 40); }
}

/* Le lien est TOUJOURS montre a l'administrateur : sans serveur d'envoi (ou
   Mailpit en mode test), le mail est garde sans partir, et c'est a lui de le
   transmettre. Il ne se relit plus ensuite -- seule son empreinte est gardee. */
function invMontrerResultat(d, adresse){
  const z=$('inv-resultat');
  const etat = !d.envoye
    ? `<b>Le mail n'est pas parti</b> (${esc(d.detail||'')}). Transmets ce lien toi-même :`
    : (d.relaye
      ? `Invitation envoyée à <b>${esc(adresse)}</b>. Le lien, si besoin :`
      : `Invitation remise à Mailpit, mais <b>aucun serveur d'envoi n'est réglé</b> : elle y est `
        + `gardée sans partir. Transmets ce lien toi-même :`);
  z.innerHTML = `<div class="hint">${etat}</div>
    <div class="ligne-champ" style="margin-top:6px">
      <input readonly value="${esc(d.lien)}" style="flex:1" onclick="this.select()">
      <button class="btn btn-default btn-sm" data-agir="invitation-copier" data-lien="${esc(d.lien)}">Copier</button>
    </div>`;
  z.style.display='block';
}

async function invEnvoyer(){
  const e=$('inv-err'); e.style.display='none';
  const adresse=$('inv-mail').value.trim();
  if(!adresse){ e.textContent='Saisis une adresse mail.'; e.style.display='block'; return; }
  $('inv-envoyer').disabled=true;
  try{
    const r=await fetch('/api/invitations',{method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:adresse})});
    const d=await r.json();
    if(!r.ok){ e.textContent=d.error||'Invitation impossible.'; e.style.display='block'; return; }
    $('inv-mail').value='';
    invMontrerResultat(d, adresse);
    chargerInvitations();
  }finally{ $('inv-envoyer').disabled=false; }
}

async function chargerInvitations(){
  let d;
  try{ d=await (await fetch('/api/invitations')).json(); }catch(e){ return; }
  const liste=d.invitations||[];
  $('inv-liste').innerHTML = liste.length
    ? '<div class="mb-titre">Invitations en attente</div>' + liste.map(i=>`<div class="us-ligne">
        <div class="us-rond">@</div>
        <div class="us-corps">
          <div class="us-nom"><b>${esc(i.email)}</b><span class="pill off"><i></i>Invitation envoyée</span></div>
          <div class="us-meta">Envoyée ${esc(ilYA(i.cree))} — le lien expire dans ${
            Math.max(1, Math.round((i.expire - Date.now()/1000)/86400))} j</div>
        </div>
        <button class="btn btn-default btn-sm" data-agir="invitation-renvoyer" data-cible="${esc(i.id)}">Renvoyer</button>
        <button class="btn btn-default btn-sm" data-agir="invitation-annuler" data-cible="${esc(i.id)}">Annuler</button>
      </div>`).join('')
    : '';
}

async function invRenvoyer(id){
  const r=await fetch('/api/invitations/'+encodeURIComponent(id)+'/renvoyer',{method:'POST'});
  const d=await r.json();
  if(!r.ok){ notifier(d.error||'Renvoi impossible.', 'err'); return; }
  invFormulaire(true);
  invMontrerResultat(d, '');
  if(d.envoye) notifier('Nouveau lien envoyé. L\'ancien ne vaut plus rien.');
  chargerInvitations();
}

async function invAnnuler(id){
  if(!await demander("Annuler l'invitation ?",
    "Le lien envoyé ne permettra plus de créer le compte.", 'Annuler l\'invitation', true)) return;
  const r=await fetch('/api/invitations/'+encodeURIComponent(id),{method:'DELETE'});
  if(!r.ok){ const d=await r.json(); notifier(d.error||'Annulation impossible.', 'err'); return; }
  chargerInvitations();
}

function copierLien(lien, message='Lien copié.'){
  navigator.clipboard?.writeText(lien).then(()=>notifier(message))
    .catch(()=>notifier('Copie impossible : sélectionne le lien à la main.', 'err'));
}

/* ---------------- proposer Face ID ---------------- */
/* Juste apres l'arrivee (invitation acceptee, ou connexion par mot de passe),
   si l'appareil sait verifier la personne et qu'aucune cle n'existe encore.
   « Plus tard » est retenu sur cet appareil : on ne repose pas la question a
   chaque page. */
function nomDuGeste(){
  const ua=navigator.userAgent||'';
  if(/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints>1)) return 'Face ID';
  if(/Macintosh/.test(ua)) return 'Touch ID';
  if(/Android/.test(ua)) return 'ton empreinte';
  if(/Windows/.test(ua)) return 'Windows Hello';
  return 'une clé d\'accès';
}
/* ---------------- serveur d'envoi requis ---------------- */
function majSmtpRequis(ok){
  SMTP_OK = ok;
  $('us-smtp-requis').style.display = ok ? 'none' : 'block';
  $('inv-ouvrir').disabled = !ok;
  if(!ok){ invFormulaire(false); usFormulaire(false); }
}
function smtpPlusTard(){
  try{ sessionStorage.setItem('codelab-smtp-plus-tard', '1'); }catch(e){}
  hide('ov-smtp');
}
function smtpRegler(){
  hide('ov-smtp');
  allerA('parametres');
  paramOnglet('email');
}
/* A la connexion de l'administrateur, tant que rien n'est regle. Une fois
   par session : « Plus tard » ne doit pas revenir a chaque page. */
function proposerSmtp(){
  if(!estAdmin || SMTP_OK) return false;
  try{ if(sessionStorage.getItem('codelab-smtp-plus-tard')) return false; }catch(e){}
  $('ov-smtp').classList.add('show');
  return true;
}

async function proposerFaceId(){
  const bienvenue = new URLSearchParams(location.search).has('bienvenue');
  if(bienvenue) history.replaceState(null, '', location.pathname);
  try{ if(!bienvenue && localStorage.getItem('codelab-faceid-plus-tard')) return; }catch(e){}
  if(!window.PublicKeyCredential || !PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable) return;
  try{
    if(!await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()) return;
    const etat=await (await fetch('/api/passkeys/etat')).json();
    if(!etat.possible) return;
    const liste=await (await fetch('/api/mon-compte/passkeys')).json();
    if((liste.passkeys||[]).length) return;
  }catch(e){ return; }
  const geste=nomDuGeste();
  $('fid-titre').textContent = (bienvenue ? 'Bienvenue ! ' : '') + 'Activer ' + geste + ' ?';
  $('fid-texte').textContent = 'La prochaine fois, connecte-toi avec ' + geste
    + ' : ni mot de passe, ni code à recopier. Tu pourras le retirer à tout moment dans '
    + 'Paramètres > Sécurité.';
  $('ov-faceid').classList.add('show');
}
function faceIdPlusTard(){
  try{ localStorage.setItem('codelab-faceid-plus-tard', '1'); }catch(e){}
  hide('ov-faceid');
}
async function faceIdActiver(){
  const e=$('fid-err'); e.style.display='none';
  $('fid-ok').disabled=true;
  /* Meme enregistrement que Parametres > Securite : on nomme la cle d'apres
     l'appareil, pour la reconnaitre dans la liste. */
  $('pk-nom').value = nomDuGeste().replace(/^ton /,'').replace(/^une clé d'accès$/,'Clé d\'accès');
  await pkAjouter();
  $('fid-ok').disabled=false;
  if($('pk-err').style.display==='block'){
    e.textContent=$('pk-err').textContent; e.style.display='block'; return;
  }
  hide('ov-faceid');
}

/* ---------------- mon adresse mail ---------------- */
let monCompte={};
async function chargerMonCompte(){
  try{ monCompte=await (await fetch('/api/mon-compte')).json(); }catch(e){ return; }
  /* Le compte d'administration n'a pas d'adresse propre : son mot de passe
     vit dans credentials.env, et les alertes ont deja leur destinataire. */
  if(monCompte.role==='admin'){ $('profil-mail').style.display='none'; return; }
  $('profil-mail').style.display='';
  $('mail-adresse').value = monCompte.email || '';
  const p=$('mail-etat');
  p.className = 'pill ' + (monCompte.email_verifie ? 'on' : 'off');
  p.innerHTML = '<i></i>' + (monCompte.email_verifie ? 'Verifiee'
    : (monCompte.email ? 'Non vérifiée' : 'Aucune adresse'));
  $('mail-sans-smtp').style.display = monCompte.smtp ? 'none' : 'block';
  $('mail-save').disabled = !monCompte.smtp;
  $('mail-renvoyer').style.display =
    (monCompte.smtp && monCompte.email && !monCompte.email_verifie) ? '' : 'none';
  $('mail-confirmation').style.display =
    (monCompte.email && !monCompte.email_verifie) ? 'block' : 'none';
}
function mailMessage(texte, erreur){
  $('mail-err').style.display = erreur ? 'block' : 'none';
  $('mail-msg').style.display = (!erreur && texte) ? 'block' : 'none';
  (erreur ? $('mail-err') : $('mail-msg')).textContent = texte;
}
async function mailEnregistrer(){
  mailMessage('', false);
  const r=await fetch('/api/mon-compte/email',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email:$('mail-adresse').value.trim()})});
  const d=await r.json();
  if(!r.ok){ mailMessage(d.error||'Enregistrement impossible.', true); chargerMonCompte(); return; }
  await chargerMonCompte();
  mailMessage('Un code vient de partir. Saisis-le ci-dessous.', false);
  $('mail-code').focus();
}
async function mailRenvoyer(){
  mailMessage('', false);
  const r=await fetch('/api/mon-compte/email/code',{method:'POST'});
  const d=await r.json();
  mailMessage(r.ok ? 'Un nouveau code vient de partir.' : (d.error||'Envoi impossible.'), !r.ok);
}
async function mailConfirmer(){
  mailMessage('', false);
  const r=await fetch('/api/mon-compte/email/confirmer',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:$('mail-code').value})});
  const d=await r.json();
  if(!r.ok){ mailMessage(d.error||'Confirmation impossible.', true); return; }
  $('mail-code').value='';
  await chargerMonCompte();
  mailMessage('Adresse vérifiée.', false);
}

async function usReinitTotp(){
  const nom=usCourant;
  if(!await demander('Réinitialiser le second facteur ?',
    `« ${nom} » enregistrera une nouvelle clé a sa prochaine connexion. A faire quand la personne `
    + `a change ou perdu son téléphone.`, 'Reinitialiser')) return;
  if(await usEcrire({reinitialiser_totp:true}))
    notifier('Second facteur remis a zero pour « ' + nom + ' ».');
}

/* Les appareils d'un compte, vus par l'administrateur. */
async function usChargerAppareils(nom){
  $('us-f-appareils').textContent='…'; $('us-f-deconnecter').disabled=true;
  let liste=[];
  try{ const r=await fetch('/api/utilisateurs/'+encodeURIComponent(nom)+'/appareils');
       if(r.ok) liste=(await r.json()).appareils||[]; }catch(e){}
  if(usCourant!==nom) return;
  $('us-f-appareils').textContent = liste.length
    ? liste.map(a=>a.appareil+' ('+ilYA(a.vue)+')').join(' · ')
    : 'Aucun appareil connecté.';
  $('us-f-deconnecter').disabled=!liste.length;
}
async function usDeconnecterAppareils(){
  const nom=usCourant;
  if(!await demander('Déconnecter « '+nom+' » partout ?',
    "Ses appareils devront se reconnecter. Son mot de passe et ses clés d'accès ne changent pas.",
    'Déconnecter')) return;
  const r=await fetch('/api/utilisateurs/'+encodeURIComponent(nom)+'/appareils',{method:'DELETE'});
  if(!r.ok){ notifier(await echec(r,'Déconnexion impossible.'),'err'); return; }
  notifier('« '+nom+' » est déconnecté de tous ses appareils.');
  usChargerAppareils(nom);
}

async function usRetirerCles(){
  const nom=usCourant;
  if(!await demander("Retirer les clés d'accès ?",
    `Les appareils de « ${nom} » ne l'ouvriront plus. Elle pourra en enregistrer de nouveaux après `
    + `s'être connectee avec son mot de passe.`, 'Retirer')) return;
  if(await usEcrire({retirer_passkeys:true}))
    notifier("Clés d'accès retirées pour « " + nom + " ».");
}

async function usSuspendre(){
  const c=usCompte(); if(!c) return;
  const nom=c.nom, suspendre=!c.suspendu;
  if(suspendre && !await demander('Suspendre le compte « ' + nom + ' » ?',
      "Il ne pourra plus se connecter, et sa session ouverte cessera d'autoriser quoi que ce "
      + "soit. Ses projets autorisés, son adresse et ses clés d'accès sont conservés.",
      'Suspendre', true)) return;
  if(await usEcrire({suspendu: suspendre}))
    notifier(suspendre ? 'Compte « ' + nom + ' » suspendu.'
                       : 'Compte « ' + nom + ' » réactivé.');
}

async function usSupprimer(){
  const nom=usCourant;
  if(!await demander('Supprimer le compte « ' + nom + ' » ?',
    "Le compte et ses clés d'accès disparaissent. Ses sessions ouvertes n'autoriseront plus rien.",
    'Supprimer', true)) return;
  const r=await fetch('/api/utilisateurs/'+encodeURIComponent(nom),{method:'DELETE'});
  if(!r.ok){ const d=await r.json(); usFicheErreur(d.error||'Suppression impossible.'); return; }
  hide('ov-user');
  notifier('Compte « ' + nom + ' » supprimé.');
  chargerUtilisateurs();
}

/* ---------------- alertes par mail ---------------- */
let alTls='starttls';
let alEtat={};

function alSetTls(t){
  alTls=t;
  document.querySelectorAll('#al-tls-toggle button')
    .forEach(b=>b.classList.toggle('active', b.dataset.t===t));
}

/* Un message affiche dans l'onglet qu'on ne regarde pas est un message
   perdu : chaque onglet a le sien, et l'appelant dit lequel. */
function alMessage(texte, erreur, ou){
  const prefixe = ou || 'al';
  const e=$(prefixe+'-err'), m=$(prefixe+'-msg');
  e.style.display='none'; m.style.display='none';
  if(!texte) return;
  const cible = erreur ? e : m;
  cible.textContent=texte; cible.style.display='block';
}

async function chargerAlertes(){
  svCharger();
  let d;
  try{ d=await (await fetch('/api/alertes')).json(); }catch(e){ return; }
  alEtat = d;

  const pill=$('al-etat');
  pill.className='pill '+(d.actif?'on':'off');
  pill.innerHTML='<i></i>'+(d.actif?'Activées':'Désactivées');
  $('al-toggle').textContent = d.actif ? 'Désactiver' : 'Activer';

  /* L'etat du SERVEUR ne depend pas des alertes : un serveur pret l'est
     meme si personne n'est encore prevenu quand une application tombe. */
  const ps=$('sm-etat');
  ps.className='pill '+(d.smtp_ok?'on':'off');
  ps.innerHTML='<i></i>'+(d.smtp_ok ? 'Configuré' : 'Non configuré');
  majSmtpRequis(!!d.smtp_ok);

  /* Le formulaire montre la configuration PERSONNALISEE, jamais celle
     d'origine : sinon enregistrer la recopierait au coffre, et l'on
     perdrait la distinction qui fait tout l'interet du repli. */
  const perso = d.personnalisee || {};
  $('al-host').value = perso.host||'';
  $('al-port').value = perso.port||'';
  $('al-user').value = perso.user||'';
  $('al-pass').value = '';
  $('al-pass').placeholder = perso.mot_de_passe_defini ? 'inchange' : 'aucun';
  alSetTls(perso.tls||'starttls');

  $('al-admin').value = (d.admin||[]).join(', ');

  const manque = (d.manquants||[]);
  $('sm-msg').style.display='none';
  if(!d.smtp_ok){
    $('sm-msg').innerHTML='Il manque : <b>'+manque.map(esc).join(', ')+'</b>.';
    $('sm-msg').style.display='block';
  }

  $('al-smtp-source').innerHTML = d.smtp_ok
    ? 'Les mails sortent par le serveur réglé dans l\'onglet <b>E-mail</b>.'
    : 'Aucun serveur d\'envoi utilisable : les alertes ne partiront pas. '
      + 'Règle-le dans l\'onglet <b>E-mail</b>.';


  const incidents=(d.incidents||[]);
  alMessage(incidents.length
    ? 'Incident en cours sur : '+incidents.join(', ')+'.' : '', false);
}

/* LA SURVEILLANCE EXTERNE. */
function svPeindre(d){
  $('sv-url').value = d.url || '';
  const p=$('sv-etat');
  const actif=!!d.url;
  p.className='pill '+(!actif ? 'off' : (d.ok===false ? 'err' : 'on'));
  p.innerHTML='<i></i>'+(!actif ? 'Désactivée'
    : d.ok===false ? 'Injoignable' : d.ok ? 'Active' : 'En attente');
  const m=$('sv-msg');
  if(actif && d.dernier_appel){
    m.textContent='Dernier appel : '+new Date(d.dernier_appel*1000).toLocaleString()
      +' ('+(d.detail||'')+').';
    m.style.display='block';
  }else m.style.display='none';
}
async function svCharger(){
  try{
    const r=await fetch('/api/surveillance-externe');
    if(r.ok) svPeindre(await r.json());
  }catch(e){}
}
async function svEnregistrer(){
  const b=$('sv-save'); if(b.disabled) return;
  b.disabled=true; const avant=b.textContent; b.textContent='Vérification...';
  $('sv-err').style.display='none';
  try{
    const r=await fetch('/api/surveillance-externe',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url:$('sv-url').value.trim()})});
    const d=await r.json();
    if(!r.ok){
      $('sv-err').textContent=(d.error||'Enregistrement impossible.')+(d.detail?' — '+d.detail:'');
      $('sv-err').style.display='block';
      return;
    }
    notifier(d.url ? 'Surveillance externe active.' : 'Surveillance externe désactivée.');
    svCharger();
  }catch(e){
    $('sv-err').textContent='Le panneau n\'a pas répondu.'; $('sv-err').style.display='block';
  }finally{ b.disabled=false; b.textContent=avant; }
}

/* Ce que les deux onglets envoient. Les champs des deux sont dans le
   document meme quand l'onglet est masque : enregistrer depuis l'un
   n'efface donc pas ce qui est regle dans l'autre. */
function alCorps(){
  return {
    actif: $('al-etat').classList.contains('on'),
    admin: $('al-admin').value,
    smtp: {
      host: $('al-host').value.trim(), port: $('al-port').value.trim(),
      tls: alTls, user: $('al-user').value.trim(),
      expediteur: $('al-user').value.trim(), password: $('al-pass').value,
    },
  };
}

async function alPoster(corps, bouton, ou){
  const b=$(bouton); const avant=b.textContent;
  b.disabled=true;
  if(corps.smtp && corps.smtp.host) b.textContent='Verification...';
  try{
    const r=await fetch('/api/alertes',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(corps)});
    const d=await r.json();
    if(!r.ok){
      /* Le message du serveur SMTP est la seule chose qui aide vraiment ici
         ("authentification refusée", "relais interdit") : on le montre. */
      alMessage((d.error||'Enregistrement impossible.')
                + (d.detail ? ' — '+d.detail : ''), true, ou);
      return false;
    }
    return true;
  }catch(e){ alMessage('Erreur réseau.', true, ou); return false; }
  finally{ b.disabled=false; b.textContent=avant; }
}

async function alEnregistrer(ou){
  const prefixe = ou || 'al';
  alMessage('', false, prefixe);
  if(await alPoster(alCorps(), prefixe+'-save', prefixe)){
    await chargerAlertes();
    alMessage(prefixe==='sm' ? 'Serveur vérifié et enregistré.'
                             : 'Réglages enregistrés.', false, prefixe);
  }
}

async function alBasculer(){
  alMessage('', false);
  const corps=alCorps();
  corps.actif = !corps.actif;
  /* Basculer l'interrupteur ne doit pas re-verifier le serveur : on
     n'envoie donc pas le bloc SMTP. */
  delete corps.smtp;
  if(await alPoster(corps, 'al-toggle', 'al')) await chargerAlertes();
}

/* ---------------- securite : double authentification ---------------- */
/* ---------------- les appareils connectes ---------------- */
let apTout=false, apDerniers=[];
const AP_VISIBLES=4;
function peindreAppareils(liste){
  /* Cet appareil en tete : c'est lui qu'on cherche, il ne doit jamais
     tomber dans les lignes cachees. */
  liste=[...liste].sort((x,y)=>(y.courant?1:0)-(x.courant?1:0));
  apDerniers=liste;
  const caches = apTout ? 0 : Math.max(0, liste.length-AP_VISIBLES);
  remplir($('ap-liste'), (liste.length ? liste.slice(0, liste.length-caches).map(a=>`<div class="ap-ligne">
      <div class="ap-quoi"><b>${esc(a.appareil)}</b>${a.courant ? ' <span class="pill on"><i></i>Cet appareil</span>' : ''}
        <small>${a.courant ? 'Actif maintenant' : 'Actif '+ilYA(a.vue)}${a.ip ? ' · '+esc(a.ip) : ''} · connecté ${ilYA(a.ouverte)}</small></div>
      <button class="btn btn-default btn-sm" data-agir="appareil-deconnecter" data-cible="${esc(a.id)}"
        ${a.courant ? 'data-courant="1"' : ''}>${a.courant ? 'Me déconnecter' : 'Déconnecter'}</button></div>`).join('')
    : '<p class="hint">Aucun appareil.</p>')
    + (caches ? `<button type="button" class="btn btn-default btn-sm ap-plus" onclick="apTout=true;peindreAppareils(apDerniers)">Afficher les ${caches} autres</button>` : ''));
  $('ap-autres').disabled = !liste.some(a=>!a.courant);
}
async function chargerAppareils(){
  try{
    const r=await fetch('/api/mon-compte/appareils'); if(!r.ok) return;
    peindreAppareils((await r.json()).appareils||[]);
  }catch(e){}
}
async function apDeconnecter(id, courant){
  if(courant){ doLogout(); return; }
  const r=await fetch('/api/mon-compte/appareils/'+encodeURIComponent(id),{method:'DELETE'});
  if(!r.ok){ notifier(await echec(r,'Déconnexion impossible.'),'err'); return; }
  notifier('Appareil déconnecté.');
  chargerAppareils();
}
async function apDeconnecterAutres(){
  if(!await demander('Déconnecter les autres appareils ?',
    "Ils devront se reconnecter. Celui-ci reste connecté, et ton mot de passe ne change pas.",
    'Déconnecter')) return;
  const r=await fetch('/api/mon-compte/appareils/deconnecter-autres',{method:'POST'});
  if(!r.ok){ notifier(await echec(r,'Déconnexion impossible.'),'err'); return; }
  const n=(await r.json()).deconnectes;
  notifier(n ? n+' appareil'+(n>1?'s':'')+' déconnecté'+(n>1?'s':'')+'.' : 'Aucun autre appareil.');
  chargerAppareils();
}

/* ---------------- les sauvegardes ---------------- */
const tailleLisible = o => o>=1e9 ? (o/1e9).toFixed(1).replace('.',',')+' Go'
  : o>=1e6 ? (o/1e6).toFixed(1).replace('.',',')+' Mo' : Math.max(1, Math.round(o/1e3))+' Ko';
function peindreSauvegardes(d){
  $('zone-sauvegardes').hidden=false;
  const pill=$('sg-etat');
  const derniere=d.archives[0];
  if(!d.actives){
    pill.className='pill err'; pill.innerHTML='<i></i>Désactivées';
    $('sg-resume').textContent="Le dossier des sauvegardes n'est pas monté : rien n'est sauvegardé. Ajoute le volume /sauvegardes dans le compose.";
  }else{
    pill.className='pill '+(d.alerte_hors_serveur ? 'warn' : 'on');
    pill.innerHTML='<i></i>'+(d.alerte_hors_serveur ? 'Sur ce serveur seulement' : 'Actives');
    $('sg-resume').textContent='Toutes les '+d.toutes_les_heures+' h, les '+d.gardees+' dernières gardées. '
      + (derniere ? 'Dernière : '+ilYA(derniere.date)+'.' : 'Aucune pour le moment.')
      + (d.derniere_copie ? ' Copiée hors du serveur '+ilYA(d.derniere_copie)+'.' : '');
  }
  $('sg-alerte').hidden=!d.alerte_hors_serveur;
  $('sg-externe').checked=!!d.copie_externe;
  /* La plus recente de chaque genre : c'est elle qu'on emporte. */
  const vues=new Set(), lignes=[];
  for(const a of d.archives){ if(vues.has(a.genre)) continue; vues.add(a.genre); lignes.push(a); }
  remplir($('sg-liste'), lignes.map(a=>`<div class="sg-ligne"><div class="sg-quoi"><b>${a.genre==='panneau'
      ? 'Le panneau' : 'Les projets'}</b><small>${esc(a.nom)} · ${tailleLisible(a.taille)} · ${ilYA(a.date)}</small></div>
    <a class="btn btn-default btn-sm" href="/api/sauvegardes/${encodeURIComponent(a.nom)}" download
       onclick="setTimeout(chargerSauvegardes, 1500)">Télécharger</a></div>`).join(''));
}
async function chargerSauvegardes(){
  try{
    const r=await fetch('/api/sauvegardes'); if(!r.ok) return;
    peindreSauvegardes(await r.json());
  }catch(e){}
}
async function sgCopieExterne(oui){
  const r=await fetch('/api/sauvegardes/copie',{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({externe:oui})});
  if(!r.ok){ notifier(await echec(r,'Réglage non enregistré.'),'err'); chargerSauvegardes(); return; }
  peindreSauvegardes(await r.json());
}

/* ---------------- la version de CodeLab ---------------- */
const COMMANDES_MAJ = `for i in app-manager dev postgres redis mailpit dagster dagster-proxy; do
  docker pull ghcr.io/studio-codelab/codelab-$i:latest
done
docker compose up -d --force-recreate`;
function dateCourte(iso){
  if(!iso) return '';
  const d=new Date(iso); if(isNaN(d)) return '';
  return d.toLocaleDateString('fr-FR',{day:'numeric', month:'long', year:'numeric'});
}
function peindreVersion(d){
  const nom = d.dev ? 'Une version de développement, construite à la main : elle ne porte pas de numéro.'
    : 'Installée : la version '+d.court+(d.date ? ' du '+dateCourte(d.date) : '')+'.';
  $('ver-resume').textContent=nom;
  remplir($('ver-corps'), (d.dev ? '' : `<div class="hint" style="margin-top:10px">Le panneau ne contacte pas GitHub pour savoir s'il existe plus récent.
      Pour voir ce qui a changé depuis ta version : <a href="https://github.com/${esc(d.depot)}/compare/${esc(d.sha)}...main"
      rel="noopener noreferrer" class="ver-lien">les nouveautés sur GitHub</a>.</div>`)
    + '<b style="display:block;margin-top:14px">Pour mettre à jour</b>'
    + '<div class="hint">Dans le dossier du <code>docker-compose.yml</code>, sur le serveur (avec CasaOS : « Mettre à jour » sur l\'application) :</div>'
    + `<pre class="ver-commandes">${esc(COMMANDES_MAJ)}</pre>`
    + `<div class="actions" style="margin-top:8px"><button class="btn btn-default btn-sm" data-agir="ver-copier">Copier les commandes</button></div>`);
}
async function chargerVersion(){
  try{
    const r=await fetch('/api/version'); if(!r.ok) return;
    peindreVersion(await r.json());
  }catch(e){}
}

async function chargerSecurite(){
  let d;
  try{ d=await (await fetch('/api/securite')).json(); }catch(e){ return; }
  const pill=$('totp-etat');
  pill.className = 'pill ' + (d.totp ? 'on' : 'off');
  pill.innerHTML = '<i></i>' + (d.totp ? 'Activée' : 'Désactivée');
  $('totp-etape-1').style.display = d.totp ? 'none' : 'block';
  $('totp-etape-3').style.display = d.totp ? 'block' : 'none';
  $('totp-etape-2').style.display = 'none';

  adressePublique = d.adresse_publique || '';
  /* La fiche ouverte et le formulaire d'ajout suivent : ce qu'ils proposent
     depend de cette reponse. */
  if(ficheNom) peindreFiche();
}

/* L'adresse publique du serveur, apprise par le serveur la premiere fois
   qu'on ouvre le panneau par son nom de domaine. "" = jamais ouvert depuis
   internet, et « publique » n'y voudrait rien dire. */
let adressePublique='';
async function chargerAdressePublique(){
  try{
    const d=await (await fetch('/api/securite')).json();
    adressePublique = d.adresse_publique || '';
  }catch(e){}
}

/* ---------------- mot de passe et preferences ---------------- */
async function mdpChanger(){
  const e=$('mdp-err'), m=$('mdp-msg');
  e.style.display='none'; m.style.display='none';
  const ancien=$('mdp-ancien').value, nouveau=$('mdp-nouveau').value;
  /* La repetition est verifiee ICI et pas au serveur : c'est une faute de
     frappe, pas une regle de securite, et un aller-retour pour le dire
     serait du bruit. */
  if(nouveau !== $('mdp-repeter').value){
    e.textContent='Les deux saisies différent.'; e.style.display='block'; return;
  }
  const b=$('mdp-save'); b.disabled=true;
  try{
    const r=await fetch('/api/compte/mot-de-passe',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ancien, nouveau})});
    const d=await r.json();
    if(!r.ok){ e.textContent=d.error||'Changement impossible.'; e.style.display='block'; }
    else{
      /* Les champs sont vides tout de suite : un mot de passe qui reste
         affiche derriere un onglet est un mot de passe qui traine. */
      $('mdp-ancien').value=$('mdp-nouveau').value=$('mdp-repeter').value='';
      m.textContent='Mot de passe change. Ta session reste ouverte ; '
        + 'la prochaine connexion demandera le nouveau.';
      m.style.display='block';
    }
  }catch(err){ e.textContent='Erreur réseau.'; e.style.display='block'; }
  finally{ b.disabled=false; }
}

async function totpPreparer(){
  const r=await fetch('/api/securite/totp/preparer',{method:'POST'});
  const d=await r.json();
  if(!r.ok){ notifier(d.error||'Préparation impossible.', 'err'); return; }
  $('totp-secret').value=d.secret;
  montrerQr(d.qr);
  $('totp-etape-1').style.display='none';
  $('totp-etape-2').style.display='block';
  $('totp-code').value=''; $('totp-code').focus();
}
function totpAnnuler(){ $('totp-etape-2').style.display='none'; chargerSecurite(); }

/* Le QR code est servi par le serveur a partir de la session : le secret ne
   passe jamais par l'adresse, donc ni par l'historique du navigateur ni par
   les journaux d'acces. Si la route ne repond pas (bibliotheque absente),
   l'image se retire et seule la cle a recopier reste. */
function montrerQr(adresse){
  const img=$('totp-qr');
  if(!adresse){ img.classList.remove('on'); return; }
  img.onload=()=>img.classList.add('on');
  img.onerror=()=>img.classList.remove('on');
  img.src=adresse+'?t='+Date.now();
}

async function totpActiver(){
  const e=$('totp-err'); e.style.display='none';
  const r=await fetch('/api/securite/totp/activer',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:$('totp-code').value})});
  const d=await r.json();
  if(!r.ok){ e.textContent=d.error||'Echec.'; e.style.display='block'; return; }
  chargerSecurite();
}

async function totpDesactiver(){
  const e=$('totp-err-off'); e.style.display='none';
  const r=await fetch('/api/securite/totp/desactiver',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:$('totp-code-off').value})});
  const d=await r.json();
  if(!r.ok){ e.textContent=d.error||'Echec.'; e.style.display='block'; return; }
  $('totp-code-off').value='';
  chargerSecurite();
}

async function doLogout(){ await fetch('/logout',{method:'POST'}); location.href='/login'; }


document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    hide('ov-add'); hide('ov-user');
    if(confirmerResoudre) confirmerReponse(false);
    $('acct-menu').classList.remove('show');
  }
});
document.querySelectorAll('.ov').forEach(o=>o.addEventListener('click',
 e=>{ if(e.target===o) o.classList.remove('show'); }));

ROOT=CODELAB_PAGE.root;
/* Injectes par le serveur au moment de servir la page. Confort d'affichage
   uniquement : chaque route verifie le role de son cote. */
const ROLE=CODELAB_PAGE.role, UTILISATEUR=CODELAB_PAGE.utilisateur;
const estAdmin = ROLE==='admin';
let SMTP_OK = CODELAB_PAGE.smtp;

$('qui-nom').textContent = UTILISATEUR || '';
$('hub-salut').innerHTML = 'Bonjour, <span class="salut-nom">' + esc(UTILISATEUR || 'admin') + '</span>';
$('qui-role').textContent = estAdmin ? 'Administrateur' : 'Utilisateur';
/* Les onglets d'administration n'apparaissent que pour l'administrateur. */
if(estAdmin) document.querySelectorAll('#param-onglets button[data-admin]')
  .forEach(b=>{ b.style.display='block'; });
/* Rien a regler ici pour l'administrateur : son mot de passe est celui de
   credentials.env, et le mot a l'administrateur lui serait adresse. */
if(estAdmin){ $('profil-mdp').style.display='none'; $('profil-message').style.display='none'; }

/* AUCUN ONGLET VIDE. Un onglet dont toutes les rubriques sont masquees pour
   ce compte ne s'affiche pas : on n'ouvre pas une page pour n'y rien
   trouver a regler. Relance quand une rubrique apparait ou disparait. */
function majOngletsParametres(){
  document.querySelectorAll('#param-onglets button').forEach(b=>{
    const panneau=$('param-'+b.dataset.p);
    const autorise = !b.hasAttribute('data-admin') || estAdmin;
    const plein = !!panneau && [...panneau.querySelectorAll(':scope > .zone')]
      .some(z=>z.style.display!=='none');
    b.style.display = (autorise && plein) ? 'block' : 'none';
  });
}
majOngletsParametres();

const visible = id => $(id).style.display!=='none';

/* UN ONGLET EN ARRIERE-PLAN NE DOIT RIEN DEMANDER.
   Les minuteries du panneau interrogeaient le serveur toutes les cinq, dix
   et vingt secondes, que la page soit regardee ou non : un onglet oublie
   derriere une autre fenetre faisait travailler la machine indefiniment, et
   « Applications » mesure la charge de chaque processus a chaque tour.
   Ce relais coupe tout quand l'onglet passe en arriere-plan, et rejoue
   immediatement au retour -- c'est ce retour immediat qui permet de couper
   sans rien perdre : la page est a jour avant meme d'etre relue. */
const _minuteries = [];
/* PAS DE SAUT EN DEFILANT. Un tour de minuterie redessine sa page (le hub
   toutes les dix secondes) : tombe en plein defilement, il remplace ce qui
   passe sous le doigt et l'elan s'arrete net, ou la page saute. Tant qu'un
   doigt est pose ou qu'on a defile il y a moins de 700 ms, le tour attend
   que tout soit immobile. */
let defileLe=0, doigtsPoses=0;
document.addEventListener('scroll', ()=>{ defileLe=performance.now(); }, {capture:true, passive:true});
document.addEventListener('wheel', ()=>{ defileLe=performance.now(); }, {capture:true, passive:true});
document.addEventListener('touchstart', e=>{ doigtsPoses=e.touches.length; }, {capture:true, passive:true});
['touchend','touchcancel'].forEach(t=>document.addEventListener(t, e=>{
  doigtsPoses=e.touches.length; defileLe=performance.now(); }, {capture:true, passive:true}));
const enDefilement = () => doigtsPoses>0 || performance.now()-defileLe < 700;
function quandImmobile(fn){
  if(!enDefilement()) return fn();
  setTimeout(()=>quandImmobile(fn), 250);
}
function surveiller(fn, delai){
  let enAttente=false;
  const tour=()=>{ if(enAttente) return; enAttente=true; quandImmobile(()=>{ enAttente=false; fn(); }); };
  const t = {fn: tour, delai: delai, id: null};
  _minuteries.push(t);
  if(!document.hidden) t.id = setInterval(tour, delai);
  return t;
}
document.addEventListener('visibilitychange', ()=>{
  _minuteries.forEach(t=>{
    if(document.hidden){
      clearInterval(t.id); t.id = null;
    }else if(t.id === null){
      t.fn();
      t.id = setInterval(t.fn, t.delai);
    }
  });
});

if(estAdmin){
  /* « Hub » cote administrateur : il y voit TOUTES les applications, pas
     seulement les siennes -- le titre « Mes applications » serait faux. */
  $('hub-titre').textContent='Hub';
  /* Les compteurs du menu lateral doivent etre justes des la premiere
     seconde, avant meme qu'on ouvre « Applications » ou « Utilisateurs ». */
  refresh();
  chargerUtilisateurs();
  chargerAdressePublique();
  surveiller(()=>{ if(visible('sec-overview')||visible('sec-apps')||visible('sec-app')) refresh(); }, 5000);
  /* Les messages arrivent quand ils arrivent : on les guette depuis
     n'importe quelle page du panneau, pas seulement depuis la leur. */
  chargerMessages();
  surveiller(guetterMessages, 20000);
  /* Les metriques de la fiche ne se rafraichissent que lorsqu'on les regarde. */
  surveiller(()=>{
    if(ficheNom && ficheOngletActif==='metriques' && visible('sec-app')) chargerMetriques(ficheNom);
  }, 5000);
}else{
  /* Compte utilisateur : pas de menu lateral, pas de configuration. Il garde
     ses parametres personnels, eux, dans le menu du compte. */
  /* Le second facteur est celui du compte d'ADMINISTRATION (un seul secret,
     cote serveur) : cette carte n'a rien a regler ici, et elle s'affichait
     sans etat ni bouton -- une case vide de plus a se demander. */
  $('profil-securite').style.display='none';
  document.body.classList.add('compte-utilisateur');
  /* Les categories du hub et la version : l'affaire de l'administrateur. */
  $('zone-categories').remove(); $('zone-version').remove();
}
majOngletsParametres();
/* Le hub est l'accueil de tout le monde. Laisse ouvert il reste juste : les
   etats changent tout seuls quand une application tombe ou repart. */
routerDepuisAdresse();
surveiller(()=>{ if(visible('sec-hub')) chargerHub(); }, 10000);
if(estAdmin) majSmtpRequis(SMTP_OK);
/* Une seule invitation a la fois : l'envoi des mails d'abord, Face ID au
   prochain passage. */
if(!proposerSmtp()) proposerFaceId();


/* Au retour sur l'onglet (page restauree par « Precedent », onglet repris),
   le navigateur peut garder l'icone vue en dernier sur ce nom d'hote --
   celle de Mailpit ou d'une application. Reposer les balises lui fait
   relire celle du panneau. */
function reposerIcone(){
  document.querySelectorAll('link[rel~="icon"]').forEach(l=>l.replaceWith(l.cloneNode()));
}
window.addEventListener('pageshow', reposerIcone);
document.addEventListener('visibilitychange', ()=>{ if(!document.hidden) reposerIcone(); });
