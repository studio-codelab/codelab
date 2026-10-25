/* ===========================================================================
   CodeLab -- LE SCRIPT DU PANNEAU (dashboard.html), DECOUPE EN MODULES
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
   MODULES     panneau/00-socle.js a 08-reglages.js, assembles DANS L'ORDRE en
               un seul script par app.py (PANNEAU_MODULES) : une seule portee,
               comme quand le script tenait en un fichier -- une fonction d'un
               module peut appeler celle d'un autre, et les attributs
               onclick/oninput du balisage les trouvent toutes.
   SERVI PAR   /panneau.js (app.py), a une session seulement, avec
               l'empreinte du fichier dans l'adresse (?v=...) : le navigateur
               le garde en cache jusqu'au deploiement suivant.
   CONTRAINTES - Aucune valeur propre a la session ici : ce fichier est le
                 meme pour tous, et il est mis en cache. Une valeur nouvelle
                 passe par CODELAB_PAGE.
               - Les tests lisent ce fichier comme s'il etait encore dans la
                 page (voir _page_panneau dans workspace/diagnostic/tests/commun.py).
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
               - Des fichiers par sujet, assembles par le serveur, plutot que
                 neuf balises script : une seule requete, et aucune
                 difference d'execution avec le script d'un seul tenant.
   A AMELIORER - Passer aux modules ES (import/export) : chaque fichier
                 declarerait ce qu'il expose, mais les fonctions appelees
                 par les attributs onclick/oninput du balisage devraient
                 alors etre posees a la main sur window.
   =========================================================================== */
/* CodeLab -- panneau/00-socle.js : Le socle : appels a l'API (jeton anti-falsification), utilitaires (esc, $), adresses des applications.
   Une partie du script du panneau, assemblee par app.py (PANNEAU_MODULES)
   dans l'ordre des numeros : un seul script, une seule portee. */
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
const lienOuverture = a => a.externe
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
/* La page a-t-elle quitte son haut ? Le bandeau se fond alors sur ce qui
   passe dessous (.topbar::before, dashboard.html). Seul le defilement
   vertical de la page compte : la mosaique qui glisse de cote, une liste
   interieure qui defile, ne touchent pas au bandeau. */
function majPageDefilee(){
  document.documentElement.classList.toggle('page-defilee', defileur().scrollTop > 2);
}
addEventListener('scroll', e=>{
  const t = e.target === document ? document.scrollingElement : e.target;
  if(t === defileur()) majPageDefilee();
}, {capture:true, passive:true});
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
  'app-desarchiver':  n => desarchiver(n),
  'app-archiver':     n => archiver(n),
  'app-supprimer':    n => rm(n),
  'galerie-ajouter': (n,d) => galerieAjouter(n, d.taille, d.app),
  'app-redemarrer':  n => restartApp(n),
  'app-build':       n => buildApp(n),
  'app-visibilite':  n => basculerVisibilite(n),
  'passkey-retirer': id => pkSupprimer(id),
  'compte-ouvrir':   n => usOuvrir(n),
  'invitation-renvoyer': id => invRenvoyer(id),
  'invitation-annuler':  id => invAnnuler(id),
  'invitation-copier':   (_, d) => copierLien(d.lien),
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
