/* CodeLab -- panneau/01-navigation.js : Le theme, la colonne et le menu du compte, la navigation entre rubriques (et l'adresse), les listes deroulantes, les onglets des parametres, les etats d'une application.
   Une partie du script du panneau, assemblee par app.py (PANNEAU_MODULES)
   dans l'ordre des numeros : un seul script, une seule portee. */
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
  widgets:['melanges','haut'],
  couleurs:['lagon','ocean','foret','prune','corail','ardoise','framboise','ambre','indigo','olive','perso']};
/* La couleur personnalisee : six chiffres hexadecimaux, portes par le jeton
   « x… » du meme cookie (voir couleur_perso dans le serveur). */
const COULEUR_PERSO = /^[0-9a-fA-F]{6}$/;
function couleurPerso(){
  const v=document.documentElement.getAttribute('data-couleur-perso')||'';
  return COULEUR_PERSO.test(v) ? v.toUpperCase() : '';
}
function reglageHub(cle){
  const v = document.documentElement.getAttribute('data-'+cle);
  return REGLAGES_HUB[cle].includes(v) ? v : REGLAGES_HUB[cle][0];
}
function majReglagesHub(){
  document.querySelectorAll('#hub-reglages [data-r]').forEach(b=>
    b.setAttribute('aria-pressed', reglageHub(b.dataset.r)===b.dataset.v));
  /* La pastille personnalisee prend sa couleur une fois choisie. */
  const perso=couleurPerso(), pastille=document.getElementById('couleur-perso-pastille');
  if(perso && pastille){ pastille.style.background='#'+perso;
    document.getElementById('couleur-perso-choix').value='#'+perso.toLowerCase(); }
}
function appliquerReglagesHub(reglages){
  const racine = document.documentElement;
  for(const [cle, v] of Object.entries(reglages))
    if(REGLAGES_HUB[cle] && REGLAGES_HUB[cle].includes(v)) racine.setAttribute('data-'+cle, v);
  const perso = couleurPerso();
  const valeurs = Object.keys(REGLAGES_HUB).map(reglageHub).concat(perso ? ['x'+perso] : []).join('.');
  try{ document.cookie='codelab-hub='+valeurs+';path=/;max-age=31536000;SameSite=Lax'; }catch(e){}
  majSensDuHub();
  majReglagesHub();
  setTimeout(majBarreEtat, 0);
  /* Le hub change de FORME : il se redessine -- une fois charge seulement,
     pour ne pas afficher « aucune application ». */
  if(hubApps.length || hubServices.length) peindreHub();
}
function setHub(cle, valeur){ appliquerReglagesHub({[cle]: valeur}); }
/* Choisir sa couleur : la feuille calculee par le serveur remplace celle
   d'avant (ou se pose), puis le reglage passe a « perso ». La couleur reste
   dans le cookie meme si l'on repasse a une teinte du nuancier : y revenir
   la retrouve. */
/* Un appui sur la pastille reprend la couleur deja choisie, avant meme d'en
   choisir une autre dans le selecteur qui s'ouvre. */
function revenirCouleurPerso(){ const c=couleurPerso(); if(c) choisirCouleurPerso(c); }
function choisirCouleurPerso(valeur){
  const hexa=String(valeur||'').replace('#','');
  if(!COULEUR_PERSO.test(hexa)) return;
  let l=document.getElementById('couleur-perso');
  if(!l){ l=document.createElement('link'); l.rel='stylesheet'; l.id='couleur-perso'; document.head.appendChild(l); }
  l.href='/couleur.css?c='+hexa.toUpperCase();
  document.documentElement.setAttribute('data-couleur-perso', hexa.toUpperCase());
  appliquerReglagesHub({couleurs:'perso'});
}
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

/* La liste ouverte est en position:fixed (voir .dl.ouvert .dl-liste) : elle
   se pose ici contre son bouton, au-dessous ou au-dessus. Le moindre
   defilement la referme -- elle ne suivrait pas son bouton. */
function dlPlacer(enveloppe){
  const r = enveloppe.querySelector('.dl-btn').getBoundingClientRect();
  const liste = enveloppe.querySelector('.dl-liste');
  liste.style.left = r.left + 'px';
  liste.style.width = r.width + 'px';
  if(enveloppe.classList.contains('vers-le-haut')){
    liste.style.top = 'auto';
    liste.style.bottom = (window.innerHeight - r.top + 4) + 'px';
  }else{
    liste.style.bottom = 'auto';
    liste.style.top = (r.bottom + 4) + 'px';
  }
}
addEventListener('scroll', e=>{
  if(!(e.target instanceof Element && e.target.closest('.dl-liste'))) dlFermerTout();
}, true);
addEventListener('resize', ()=>dlFermerTout());

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
    if(ouvrir) dlPlacer(enveloppe);
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
     main : l'etiquette doit suivre sans que chaque appel ait a y penser.
     Les options, par l'observateur ; la valeur, par ses deux proprietes,
     redefinies sur CE select (le navigateur ne signale pas un .value pose
     par le script : ni « change », ni mutation).
     Vecu : la duree de conservation du journal, reglee a 12 mois, affichait
     « 3 mois » -- la premiere option -- tant qu'on n'ouvrait pas la liste. */
  new MutationObserver(()=>dlPeindre(enveloppe)).observe(select, {childList:true});
  for(const nom of ['value', 'selectedIndex']){
    const d = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, nom);
    Object.defineProperty(select, nom, {configurable:true,
      get(){ return d.get.call(this); },
      set(v){ d.set.call(this, v); dlPeindre(enveloppe); }});
  }
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
    if(estAdmin) chargerConservation();
    if(estAdmin) chargerSecurite(); else chargerTotpCompte();
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
  /* Archivee : un statut a part entiere, au meme rang qu'« En ligne » ou
     « Arretee » (voir « l'archivage d'une application » cote serveur). */
  if(a.archivee) return ['arch','Archivée'];
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
