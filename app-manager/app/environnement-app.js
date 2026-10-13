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
     - sa couleur (theme-color, sinon le fond de la page) part au panneau, qui
       en peint la barre d'etat, et la repeint quand elle change.

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

  /* La couleur de la barre d'etat : celle que la page annonce. */
  var derniereCouleur = '';
  function annoncerCouleur(){
    try {
      var m = document.querySelector('meta[name=theme-color]:not([media])')
        || (function(){ var l = document.querySelectorAll('meta[name=theme-color][media]');
             for(var k = 0; k < l.length; k++) if(mmNatif(l[k].getAttribute('media')).matches) return l[k]; return null; })();
      var c = (m && m.getAttribute('content')) || (document.body && getComputedStyle(document.body).backgroundColor) || '';
      if(!c || c === 'rgba(0, 0, 0, 0)' || c === 'transparent' || c === derniereCouleur) return;
      derniereCouleur = c;
      parent.postMessage({ codelab: 'couleur', valeur: c }, PANNEAU);
    } catch(e){}
  }

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
  function pret(){
    regarderViewport();
    poser();
    reecrireTout();
    annoncerCouleur();
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
