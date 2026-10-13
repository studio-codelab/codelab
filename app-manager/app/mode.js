/* ===========================================================================
   CodeLab -- LE MODE, ET RIEN D'AUTRE
   ---------------------------------------------------------------------------
   Charge en tete de chaque page du panneau (connexion, panneau, Messages),
   AVANT tout affichage : <script src="/mode.js"></script>. Une seule copie --
   les trois pages en portaient chacune la sienne, et la quatrieme aurait
   oublie une ligne.

   DEUX QUESTIONS DIFFERENTES, ET CHACUNE A SA REGLE :

     LE MODE    site web (un onglet de Safari, Chrome...) ou application
                (l'icone de l'ecran d'accueil). Il se lit ICI, une fois, et se
                pose sur <html> : classe « mode-app » ou « mode-site ». Le CSS
                s'y accroche (theme.css, bloc « mode application ») ; le
                script le lit dans window.CODELAB_MODE_APP.
     LA LARGEUR telephone (jusqu'a 760 px), tablette (761 a 1180 px) et
                ordinateur. Elle se lit en CSS seulement, par media query
                (voir l'en-tete de theme.css), jamais ici.

   Les deux se croisent : le mode application s'ouvre sur un telephone ET sur
   une tablette, le site web aussi. Une regle de mode ne teste jamais une
   largeur, une regle de largeur ne teste jamais le mode.
   =========================================================================== */
/* LE MODE APPLICATION. Ouvert depuis l'icone de l'ecran d'accueil, le
   panneau se comporte comme une application installee : pas de zoom (ni
   pince, ni double tape), pas de rebond en haut et en bas de la page, pas de
   surlignage au toucher ni de selection de texte sur l'interface, et la barre
   d'etat (l'heure, la batterie) prend la couleur du bandeau au lieu d'une
   bande blanche. Dans Safari, rien ne change : le zoom reste permis, c'est un
   reglage d'accessibilite. */
(function(){
  var app = (window.matchMedia && matchMedia('(display-mode: standalone)').matches)
            || window.navigator.standalone === true;
  window.CODELAB_MODE_APP = app;
  document.documentElement.classList.add(app ? 'mode-app' : 'mode-site');
  if(!app) return;
  var v = document.querySelector('meta[name="viewport"]');
  if(v) v.setAttribute('content', 'width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover');
  /* iOS ne tient plus compte de user-scalable=no : la pince se bloque ici. */
  ['gesturestart','gesturechange','gestureend'].forEach(function(t){
    document.addEventListener(t, function(e){ e.preventDefault(); }, {passive:false});
  });
  /* PAS DE ROTATION SUR TELEPHONE. L'application installee reste verticale
     sur un telephone (le petit cote de l'ecran sous 600 px, au doigt) ; la
     tablette et l'ordinateur tournent librement.
       Android, Chrome : le systeme verrouille (screen.orientation.lock).
       iOS : aucun verrou pour une page web. Quand le telephone est tenu en
         paysage, iOS tourne l'ecran ; la page tourne alors d'un quart de
         tour en sens inverse (classe « contre-rotation ») et reste verticale
         par rapport au telephone. Pour que la mise en page ne voie rien, ce
         que la page lit de l'ecran est redresse le temps du paysage : les
         media queries (largeur <-> hauteur, portrait <-> paysage), les unites
         vw/vh, les bords env(safe-area-inset-*), innerWidth/innerHeight,
         matchMedia, getBoundingClientRect, elementFromPoint et les
         coordonnees des doigts et de la souris.
     C'est le SENS DE L'APPAREIL qui compte, jamais la hauteur de la
     fenetre : sur Android, le clavier ouvert rend la page plus large que
     haute sans rien tourner. */
  var html = document.documentElement;
  var telephone = Math.min(screen.width, screen.height) < 600
                  && !!(window.matchMedia && matchMedia('(pointer: coarse)').matches);
  if(telephone){
    html.classList.add('telephone');
    var o = screen.orientation;
    if(o && o.lock) o.lock('portrait').catch(function(e){});
    /* sens : 0 droit ; -90 telephone tourne vers la gauche (la page tourne
       d'un quart de tour a gauche) ; 90 vers la droite. */
    var sens = 0;
    var acces = function(objet, nom){
      for(var x = objet; x; x = Object.getPrototypeOf(x)){
        var d = Object.getOwnPropertyDescriptor(x, nom);
        if(d && d.get) return d.get;
      }
    };
    var lireW = acces(window, 'innerWidth'), lireH = acces(window, 'innerHeight');
    var vraiW = function(){ return lireW.call(window); }, vraiH = function(){ return lireH.call(window); };
    /* LA PAGE NE SAUTE PAS AU PAYSAGE. Vecu (video d'un iPhone) : en
       portrait, la page commence sous la barre d'etat ; en paysage, iOS
       masque la barre d'etat et donne a la page toute la longueur de
       l'ecran. Redressee, la page remontait d'environ 55 px, sous l'encoche,
       puis redescendait au retour en portrait. On retient donc la hauteur
       de la page en portrait (la plus grande vue : le clavier la reduit), et
       la page redressee garde cette hauteur et cette place : le haut de
       l'ecran reste vide, comme sous la barre d'etat. « decale » est ce
       retrait, en pixels, du cote du haut du telephone.
       ESSAYE ET ECARTE : viewport-fit=cover et une barre d'etat
       translucide. Ils changent la mise en page de tout le panneau en
       portrait, pour un defaut qui ne se voit qu'en paysage. */
    /* Ouverte d'emblee en paysage, la page n'a jamais vu le portrait : la
       hauteur se garde d'une ouverture a l'autre (par taille d'ecran). Un
       stockage refuse (navigation privee) ne fait que la perdre. */
    var CLE_PORTRAIT = 'codelab-haut-portrait-' + Math.max(screen.width, screen.height);
    var hautPortrait = 0, decale = 0;
    try{ hautPortrait = parseInt(localStorage.getItem(CLE_PORTRAIT), 10) || 0; }catch(e){}
    var retenirPortrait = function(){
      if(sens || vraiH() <= hautPortrait) return;
      hautPortrait = vraiH();
      try{ localStorage.setItem(CLE_PORTRAIT, String(hautPortrait)); }catch(e){}
    };
    var calculerDecale = function(){
      var long = Math.max(screen.width, screen.height), W = vraiW();
      if(!sens || !hautPortrait || hautPortrait >= W){ decale = 0; return; }
      /* Ce qu'iOS retire deja du cote du haut, en paysage (une marge de
         chaque cote quand il y en a), ote de ce que la barre d'etat
         prenait en portrait. */
      decale = Math.max(0, Math.min(W - hautPortrait, (long - hautPortrait) - (long - W) / 2));
    };
    /* Ecran (X, Y) -> page (x, y), et retour. */
    var versPage = function(X, Y){ return sens < 0 ? [vraiH() - Y, X - decale] : [Y, vraiW() - decale - X]; };
    var versEcran = function(x, y){ return sens < 0 ? [y + decale, vraiH() - x] : [vraiW() - decale - y, x]; };
    if(lireW && lireH){
      Object.defineProperty(window, 'innerWidth', {configurable:true, get:function(){ return sens ? vraiH() : vraiW(); }});
      Object.defineProperty(window, 'innerHeight', {configurable:true, get:function(){ return sens ? vraiW() - decale : vraiH(); }});
    }
    var PERMUTE = {width:'height', height:'width', portrait:'landscape', landscape:'portrait'};
    var permuterMedia = function(t){ return String(t).replace(/width|height|portrait|landscape/g, function(m){ return PERMUTE[m]; }); };
    var mm = window.matchMedia;
    window.matchMedia = function(q){ return mm.call(window, sens ? permuterMedia(q) : q); };
    var rect = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function(){
      var r = rect.call(this);
      if(!sens) return r;
      return sens < 0 ? new DOMRect(vraiH() - r.bottom, r.left - decale, r.height, r.width)
                      : new DOMRect(r.top, vraiW() - decale - r.right, r.height, r.width);
    };
    var sous = document.elementFromPoint;
    document.elementFromPoint = function(x, y){
      if(!sens) return sous.call(document, x, y);
      var e = versEcran(x, y); return sous.call(document, e[0], e[1]);
    };
    [window.MouseEvent && MouseEvent.prototype, window.Touch && Touch.prototype].forEach(function(proto){
      if(!proto) return;
      var gx = acces(proto, 'clientX'), gy = acces(proto, 'clientY');
      if(!gx || !gy) return;
      Object.defineProperty(proto, 'clientX', {configurable:true, get:function(){
        var X = gx.call(this); return sens ? versPage(X, gy.call(this))[0] : X; }});
      Object.defineProperty(proto, 'clientY', {configurable:true, get:function(){
        var Y = gy.call(this); return sens ? versPage(gx.call(this), Y)[1] : Y; }});
    });
    /* Les feuilles de style : chaque retouche garde l'original, rendu au
       retour en portrait (ou avant de tourner dans l'autre sens). */
    var retouches = [];
    var BORDS = {'-90':{top:'left', right:'top', bottom:'right', left:'bottom'},
                 '90':{top:'right', right:'bottom', bottom:'left', left:'top'}};
    var retoucherValeur = function(v){
      var b = BORDS[sens];
      return v.replace(/(\d)([dsl]?v)(w|h)\b/g, function(m, c, u, a){ return c + u + (a === 'w' ? 'h' : 'w'); })
              .replace(/safe-area-inset-(top|right|bottom|left)/g, function(m, cote){ return 'safe-area-inset-' + b[cote]; });
    };
    var parcourir = function(regles){
      for(var i = 0; i < regles.length; i++){
        var r = regles[i];
        if(r.media && r.cssRules && r.media.mediaText){
          retouches.push([r.media, 'mediaText', r.media.mediaText]);
          r.media.mediaText = permuterMedia(r.media.mediaText);
        }
        if(r.style) for(var j = 0; j < r.style.length; j++){
          var nom = r.style[j], v = r.style.getPropertyValue(nom);
          if(!/\dv[wh]\b|\d[dsl]v[wh]\b|safe-area-inset/.test(v)) continue;
          var prio = r.style.getPropertyPriority(nom);
          retouches.push([r.style, nom, v, prio]);
          r.style.setProperty(nom, retoucherValeur(v), prio);
        }
        if(r.cssRules) parcourir(r.cssRules);
      }
    };
    var defaire = function(){
      while(retouches.length){
        var t = retouches.pop();
        if(t[1] === 'mediaText') t[0].mediaText = t[2]; else t[0].setProperty(t[1], t[2], t[3]);
      }
    };
    var poser = function(){
      var b = document.body;
      defaire();
      html.classList.toggle('contre-rotation', !!sens);
      calculerDecale();
      if(!b) return;
      if(!sens){ b.style.removeProperty('width'); b.style.removeProperty('height'); b.style.removeProperty('transform'); return; }
      var W = vraiW(), H = vraiH();
      b.style.setProperty('width', H + 'px', 'important');
      b.style.setProperty('height', (W - decale) + 'px', 'important');
      b.style.setProperty('transform', sens < 0 ? 'translate(' + decale + 'px,' + H + 'px) rotate(-90deg)'
                                                : 'translate(' + (W - decale) + 'px,0) rotate(90deg)', 'important');
      for(var i = 0; i < document.styleSheets.length; i++){
        try{ parcourir(document.styleSheets[i].cssRules); }catch(e){ /* feuille d'une autre origine */ }
      }
    };
    var enCours = false;
    var finPivot = function(){ html.classList.remove('pivote'); };
    var lireSens = function(e){
      if(enCours) return;
      var t = o && o.type, a = window.orientation, nouveau = 0;
      var paysage = t ? t.indexOf('landscape') === 0 : Math.abs(a || 0) === 90;
      if(paysage){
        if(typeof a === 'number') nouveau = a === -90 ? 90 : -90;
        else nouveau = (o && o.angle === 270) ? 90 : -90;
      }
      var change = nouveau !== sens, redimension = !!(e && e.type === 'resize');
      retenirPortrait();
      sens = nouveau;
      retenirPortrait();
      /* Rien ne glisse pendant le quart de tour : une largeur, une couleur
         en transition se verraient filer d'un bord a l'autre pendant que la
         page se redresse. Les transitions seulement : couper les animations
         les rejouerait toutes (la cascade des tuiles) a leur retour. */
      if(change){
        html.classList.add('pivote');
        clearTimeout(lireSens.fin);
        lireSens.fin = setTimeout(finPivot, 700);
      }
      /* iOS annonce parfois la rotation avant d'avoir change la taille de
         la fenetre : la taille se reprend a chaque redimensionnement. */
      if(change || (redimension && sens)) poser();
      if(change && !redimension){
        enCours = true;
        try{ window.dispatchEvent(new Event('resize')); } finally { enCours = false; }
      }
    };
    lireSens();
    document.addEventListener('DOMContentLoaded', poser);
    if(o && o.addEventListener) o.addEventListener('change', lireSens);
    window.addEventListener('orientationchange', lireSens);
    window.addEventListener('resize', lireSens);
    window.addEventListener('pageshow', lireSens);
  }
  /* La page de reserve hors ligne : en mode application seulement. Dans le
     navigateur, le panneau reste un site (et Safari garde sa page d'erreur). */
  if('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(function(){});
})();
/* L'INVITATION A INSTALLER (Chrome, Edge, Android). Le navigateur la
   propose une fois, tot, avant que la page ait fini de se construire : elle
   est gardee ici, et les parametres la montrent quand on y va. */
window.addEventListener('beforeinstallprompt', function(e){
  e.preventDefault();
  window.CODELAB_INSTALLATION = e;
  window.dispatchEvent(new Event('codelab-installable'));
});
/* La barre d'etat prend la couleur du bandeau (ou du fond), theme et style
   compris : relue au chargement et a chaque changement de theme. */
function majBarreEtat(){
  var el = document.querySelector('.topbar') || document.body;
  if(!el) return;
  var c = getComputedStyle(el).backgroundColor;
  if(!c || c === 'rgba(0, 0, 0, 0)' || c === 'transparent') c = getComputedStyle(document.body).backgroundColor;
  document.querySelectorAll('meta[name="theme-color"]').forEach(function(m){
    m.setAttribute('content', c); m.removeAttribute('media');
  });
}
document.addEventListener('DOMContentLoaded', majBarreEtat);
if(window.matchMedia) matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(){ setTimeout(majBarreEtat, 0); });
