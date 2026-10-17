/* ===========================================================================
   CodeLab -- L'ENVIRONNEMENT D'UNE APPLICATION DU HUB
   ---------------------------------------------------------------------------
   Glisse par le proxy en TETE de chaque page HTML d'une application (avant
   ses propres scripts), dans une balise script. Il repond a une seule
   question, la meme partout : « dans quoi suis-je affichee ? », et la pose
   la ou l'application la lit sans rien installer :

     <html class="mode-app">        ou "mode-site"
     <html data-appareil="telephone">  ou "tablette", "ordinateur"
     <html data-plateforme="ios">      ou "android", "mac", "windows", "autre"
     var(--codelab-bord-haut)       ... -droite, -bas, -gauche : l'encoche,
                                    la barre d'etat, la barre de geste
     window.CODELAB.environnement   le tout, en objet ; l'evenement
                                    « codelab-environnement » a chaque changement

   OUVERTE DEPUIS LE HUB INSTALLE, une application est ENCADREE par le panneau
   (/ouvrir.html) : iOS ne lui dit alors rien de ce qu'une vraie application
   recoit -- ni display-mode: standalone, ni navigator.standalone, ni les
   bords de l'ecran (env(safe-area-inset-*) vaut 0 dans un cadre). Ce script
   SIMULE ce qu'iOS lui donnerait si elle etait installee seule :

     - navigator.standalone vaut true, et matchMedia('(display-mode:
       standalone)') repond oui : le code ecrit pour une app installee marche
       tel quel ;
     - comme iOS, une page qui declare viewport-fit=cover s'etend sous
       l'encoche et recoit les bords ; sinon le panneau la pose dans la zone
       sure et peint autour la couleur qu'elle annonce ;
     - pour une page qui s'etend, env(safe-area-inset-*) est reecrit dans ses
       feuilles de style en var(--codelab-bord-*) : la regle CSS ecrite pour
       iOS donne les memes marges dans le cadre ;
     - la couleur reellement peinte en haut et en bas de la page (comme
       Safari, et non la theme-color declaree) part au panneau, qui en peint
       la barre d'etat et la barre de geste, et les repeint quand la page
       defile ou change ;
     - son manifeste part aussi au panneau, qui en suit le fond, la barre et
       l'orientation (voir annoncerManifeste).

   Ouverte seule (onglet, ou installee elle-meme sur l'ecran d'accueil),
   l'application garde ce que le navigateur lui donne : le script ne fait
   que le lire et le poser au meme endroit.

   Bloque par une politique de securite (CSP) stricte de l'application : rien
   ne casse, la simulation manque. Ecrit en ES5 : il tourne avant tout, meme
   sur un vieux navigateur.
   =========================================================================== */
(function(){
  var PANNEAU = __PANNEAU__;               // l'origine du panneau ("" : visiteur anonyme)
  var racine = document.documentElement;
  var encadree = false;
  try { encadree = window.top !== window && window.name === 'codelab-app' && !!PANNEAU; } catch(e){}
  var ua = navigator.userAgent || '';
  var ios = /iP(hone|ad|od)/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  var natif = false;
  try { natif = (window.matchMedia && matchMedia('(display-mode: standalone)').matches) || navigator.standalone === true; } catch(e){}

  function plateforme(){
    return ios ? 'ios' : /Android/.test(ua) ? 'android' : /Mac OS X|Macintosh/.test(ua) ? 'mac'
      : /Windows/.test(ua) ? 'windows' : 'autre';
  }
  /* Les memes seuils que le panneau (theme.css) : 760 et 1180 px. */
  function appareil(l){ return l <= 760 ? 'telephone' : l <= 1180 ? 'tablette' : 'ordinateur'; }

  var env = {
    mode: (encadree || natif) ? 'app' : 'site',
    encadree: encadree,
    plateforme: plateforme(),
    appareil: appareil(window.innerWidth || 1280),
    orientation: (window.innerWidth > window.innerHeight) ? 'paysage' : 'portrait',
    theme: (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches) ? 'sombre' : 'clair',
    bords: { haut: 0, droite: 0, bas: 0, gauche: 0 },
    pleinEcran: false
  };
  window.CODELAB = window.CODELAB || {};
  window.CODELAB.environnement = env;

  function poser(){
    racine.classList.remove(env.mode === 'app' ? 'mode-site' : 'mode-app');
    racine.classList.add(env.mode === 'app' ? 'mode-app' : 'mode-site');
    racine.setAttribute('data-appareil', env.appareil);
    racine.setAttribute('data-plateforme', env.plateforme);
    var b = env.bords, st = racine.style;
    st.setProperty('--codelab-bord-haut', b.haut + 'px');
    st.setProperty('--codelab-bord-droite', b.droite + 'px');
    st.setProperty('--codelab-bord-bas', b.bas + 'px');
    st.setProperty('--codelab-bord-gauche', b.gauche + 'px');
    try { window.dispatchEvent(new CustomEvent('codelab-environnement', { detail: env })); } catch(e){}
  }

  /* Ouverte seule : les bords sont ceux du navigateur, lus une fois la page
     construite (env() ne se lit qu'a travers un element). */
  function lireBordsNatifs(){
    var d = document.createElement('div');
    d.style.cssText = 'position:fixed;visibility:hidden;pointer-events:none;'
      + 'padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left)';
    (document.body || racine).appendChild(d);
    var c = getComputedStyle(d);
    env.bords = { haut: parseFloat(c.paddingTop) || 0, droite: parseFloat(c.paddingRight) || 0,
                  bas: parseFloat(c.paddingBottom) || 0, gauche: parseFloat(c.paddingLeft) || 0 };
    d.parentNode.removeChild(d);
    poser();
  }

  if(!encadree){
    poser();
    if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', lireBordsNatifs);
    else lireBordsNatifs();
    window.addEventListener('resize', function(){
      env.appareil = appareil(window.innerWidth);
      env.orientation = window.innerWidth > window.innerHeight ? 'paysage' : 'portrait';
      lireBordsNatifs();
    });
    return;
  }

  /* ----------------------- encadree : la simulation ----------------------- */
  try { Object.defineProperty(navigator, 'standalone', { get: function(){ return true; }, configurable: true }); } catch(e){}
  if(window.matchMedia){
    var mmNatif = window.matchMedia.bind(window);
    var faux = function(q, oui){
      return { matches: oui, media: q, onchange: null,
               addListener: function(){}, removeListener: function(){},
               addEventListener: function(){}, removeEventListener: function(){},
               dispatchEvent: function(){ return false; } };
    };
    window.matchMedia = function(q){
      var t = String(q);
      if(/display-mode\s*:\s*standalone/.test(t)) return faux(t, true);
      if(/display-mode\s*:\s*(browser|minimal-ui|fullscreen)/.test(t)) return faux(t, false);
      return mmNatif(q);
    };
  }

  /* Comme une app installee : ni zoom, ni rebond, ni surlignage au toucher.
     viewport-fit=cover, s'il est declare, est garde : c'est lui qui dit si
     la page veut s'etendre sous l'encoche. Lu une fois l'en-tete construit :
     ce script passe AVANT la balise viewport de la page. */
  var cover = false;
  function regarderViewport(){
    var l = document.querySelectorAll('meta[name=viewport]');
    for(var i = 0; i < l.length; i++) if(/viewport-fit\s*=\s*cover/.test(l[i].getAttribute('content') || '')) cover = true;
    if(!l.length){ var m = document.createElement('meta'); m.name = 'viewport'; (document.head || racine).appendChild(m); l = [m]; }
    for(var j = 0; j < l.length; j++) l[j].setAttribute('content',
      'width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no' + (cover ? ',viewport-fit=cover' : ''));
    env.pleinEcran = cover;
  }
  ['gesturestart', 'gesturechange', 'gestureend'].forEach(function(t){
    document.addEventListener(t, function(e){ e.preventDefault(); }, { passive: false });
  });
  var s = racine.style;
  s.touchAction = 'pan-x pan-y'; s.overscrollBehavior = 'none'; s.webkitTapHighlightColor = 'transparent';
  poser();

  /* env(safe-area-inset-*) vaut 0 dans un cadre : on le reecrit, regle par
     regle, en var(--codelab-bord-*). Seulement pour une page qui s'etend
     (cover) : les autres sont deja posees dans la zone sure. Les feuilles
     d'une autre origine ne se lisent pas : elles sont laissees telles
     quelles. */
  var NOMS = { top: 'haut', right: 'droite', bottom: 'bas', left: 'gauche' };
  var motif = /env\(\s*safe-area-inset-(top|right|bottom|left)\s*(?:,([^)]*))?\)/g;
  function reecrire(regles){
    for(var i = 0; i < regles.length; i++){
      var r = regles[i];
      if(r.cssRules) reecrire(r.cssRules);
      if(r.style && /safe-area-inset/.test(r.style.cssText)){
        r.style.cssText = r.style.cssText.replace(motif, function(_, cote, repli){
          return 'var(--codelab-bord-' + NOMS[cote] + (repli ? ',' + repli : ', 0px') + ')';
        });
      }
    }
  }
  function reecrireTout(){
    if(!cover) return;
    for(var i = 0; i < document.styleSheets.length; i++){
      try { reecrire(document.styleSheets[i].cssRules || []); } catch(e){}
    }
    var enLigne = document.querySelectorAll('[style*="safe-area-inset"]');
    for(var j = 0; j < enLigne.length; j++){
      enLigne[j].setAttribute('style', enLigne[j].getAttribute('style').replace(motif, function(_, cote, repli){
        return 'var(--codelab-bord-' + NOMS[cote] + (repli ? ',' + repli : ', 0px') + ')';
      }));
    }
  }

  /* La couleur de la barre d'etat, et celle de la barre de geste : comme
     Safari, celle qui est REELLEMENT PEINTE en haut (et en bas) de la page,
     pas la theme-color declaree -- une theme-color qui ne colle pas au haut
     de la page faisait un bandeau a part, qui ne faisait pas application.
     Le point est lu a travers les couches (un bandeau translucide sur le
     fond), et relu quand la page defile ou change. La theme-color ne sert
     que si rien ne se lit. */
  function rgba(c){
    var m = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,\/]\s*([\d.]+)(%?))?\s*\)/.exec(c || '');
    if(!m) return null;
    var a = m[4] === undefined ? 1 : (+m[4] / (m[5] ? 100 : 1));
    return [+m[1], +m[2], +m[3], a];
  }
  function couches(el){
    var l = [];
    for(var n = el; n && n.nodeType === 1; n = n.parentElement){
      var cs = getComputedStyle(n), c = rgba(cs.backgroundColor);
      if((!c || c[3] === 0) && /gradient/.test(cs.backgroundImage)) c = rgba(cs.backgroundImage);
      if(c && c[3] > 0) l.push(c);
      if(c && c[3] >= 1) break;
    }
    return l;
  }
  function couleurA(y){
    var x = Math.round((window.innerWidth || 0) / 2), el = null;
    try { el = document.elementFromPoint(x, y); } catch(e){}
    var l = couches(el || document.body || racine);
    /* Le fond du body peint toute la fenetre quand celui de <html> est vide
       (meme hors du body, par exemple au-dessus d'une marge qui deborde). */
    if((!l.length || l[l.length - 1][3] < 1) && document.body && el !== document.body
       && !(document.body.contains(el))){
      var fond = couches(document.body);
      l = l.concat(fond.slice(-1));
    }
    if(!l.length || l[l.length - 1][3] < 1){
      var sombre = /dark/.test(getComputedStyle(racine).colorScheme || '');
      l.push(sombre ? [18, 18, 18, 1] : [255, 255, 255, 1]);
    }
    var r = l[l.length - 1].slice(0, 3);
    for(var i = l.length - 2; i >= 0; i--){
      var c = l[i];
      for(var k = 0; k < 3; k++) r[k] = c[k] * c[3] + r[k] * (1 - c[3]);
    }
    return 'rgb(' + Math.round(r[0]) + ', ' + Math.round(r[1]) + ', ' + Math.round(r[2]) + ')';
  }
  function couleurDeclaree(){
    var m = document.querySelector('meta[name=theme-color]:not([media])');
    if(!m){
      var l = document.querySelectorAll('meta[name=theme-color][media]');
      for(var k = 0; k < l.length; k++) if(mmNatif(l[k].getAttribute('media')).matches){ m = l[k]; break; }
    }
    return (m && m.getAttribute('content')) || '';
  }
  var derniereCouleur = '';
  function annoncerCouleur(){
    try {
      var haut = '', bas = '';
      if(document.body){ haut = couleurA(1); bas = couleurA(Math.max(1, (window.innerHeight || 2) - 2)); }
      if(!haut) haut = bas = couleurDeclaree();
      if(!haut || haut + bas === derniereCouleur) return;
      derniereCouleur = haut + bas;
      parent.postMessage({ codelab: 'couleur', valeur: haut, bas: bas || haut }, PANNEAU);
    } catch(e){}
  }
  var relecture = null;
  function relireBientot(){
    if(relecture) return;
    relecture = setTimeout(function(){ relecture = null; annoncerCouleur(); }, 120);
  }
  window.addEventListener('scroll', relireBientot, { capture: true, passive: true });
  window.addEventListener('resize', relireBientot);

  /* Le panneau connait l'ecran, la page dans son cadre ne le connait pas :
     il repond a « pret » par l'environnement, et le renvoie quand l'appareil
     tourne ou change de theme. */
  window.addEventListener('message', function(e){
    if(e.source !== window.parent || e.origin !== PANNEAU || !e.data || e.data.codelab !== 'environnement') return;
    var d = e.data;
    if(d.plateforme) env.plateforme = String(d.plateforme);
    if(d.appareil) env.appareil = String(d.appareil);
    if(d.orientation) env.orientation = String(d.orientation);
    if(d.theme) env.theme = String(d.theme);
    if(d.bords) env.bords = { haut: +d.bords.haut || 0, droite: +d.bords.droite || 0,
                              bas: +d.bords.bas || 0, gauche: +d.bords.gauche || 0 };
    poser();
  });
  /* LE MANIFESTE DE L'APPLICATION : celui qu'elle declare, ou celui que le
     proxy lui donne. Installee seule, le systeme le lirait ; encadree, c'est
     le panneau qui le suit -- son fond et sa barre en attendant la premiere
     peinture, et son orientation (Android : iOS ne laisse pas une page web
     tourner l'ecran). Lu sur l'origine de l'application ; une CSP qui
     l'interdit ne casse rien, le panneau garde ses reglages. */
  function annoncerManifeste(){
    try {
      var lien = document.querySelector('link[rel~="manifest"]');
      if(!lien || !lien.href || !window.XMLHttpRequest) return;
      var x = new XMLHttpRequest();
      x.open('GET', lien.href, true);
      x.onload = function(){
        try {
          if(x.status !== 200) return;
          var m = JSON.parse(x.responseText) || {};
          parent.postMessage({ codelab: 'manifeste', fond: String(m.background_color || ''),
                               barre: String(m.theme_color || ''),
                               orientation: String(m.orientation || '') }, PANNEAU);
        } catch(e){}
      };
      x.send();
    } catch(e){}
  }
  function pret(){
    regarderViewport();
    poser();
    reecrireTout();
    annoncerManifeste();
    annoncerCouleur();
    /* Une application a page unique change d'ecran sans recharger : la
       couleur se relit quand la page change. */
    try { new MutationObserver(relireBientot).observe(document.body || racine,
      { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'] }); } catch(e){}
    try { parent.postMessage({ codelab: 'pret', pleinEcran: cover }, PANNEAU); } catch(e){}
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', pret);
  else pret();
  window.addEventListener('load', function(){ reecrireTout(); annoncerCouleur(); });
  /* Une feuille ou une theme-color ajoutee apres coup (application a page
     unique, changement de theme) : relue au vol. */
  try {
    new MutationObserver(function(liste){
      for(var i = 0; i < liste.length; i++){
        var n = liste[i].target;
        if(liste[i].type === 'attributes' || /^(STYLE|LINK|META)$/.test(n.nodeName) || liste[i].addedNodes.length){
          reecrireTout(); annoncerCouleur(); return;
        }
      }
    }).observe(document.head || racine, { childList: true, subtree: true, attributes: true, attributeFilter: ['content'] });
  } catch(e){}
  try { mmNatif('(prefers-color-scheme: dark)').addEventListener('change', function(){ setTimeout(annoncerCouleur, 0); }); } catch(e){}
})();
