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
     tablette et l'ordinateur tournent librement. Deux verrous, et rien
     d'autre :
       - le manifeste demande le portrait au telephone (app.py,
         est_un_telephone), lu a l'installation ;
       - Android le verrouille aussi ici (screen.orientation.lock).
     iOS ne verrouille pas une page web : sur iPhone, le verrou d'orientation
     du Centre de controle fait le reste.
     ESSAYE ET ECARTE : une page qui tournait d'un quart de tour en sens
     inverse, avec l'ecran redresse pour la mise en
     page (media queries, vw/vh, bords, innerWidth, coordonnees des doigts).
     iOS jouait d'abord sa propre animation de rotation, que la page ne peut
     pas empecher : on voyait la page tourner puis revenir. Vecu : « je ne
     veux pas que la rotation soit autorisee tout court » -- retiree, avec
     ses 160 lignes. Avant elle, une page « tiens ton telephone droit » :
     refusee aussi. */
  var telephone = Math.min(screen.width, screen.height) < 600
                  && !!(window.matchMedia && matchMedia('(pointer: coarse)').matches);
  var o = screen.orientation;
  if(telephone && o && o.lock) o.lock('portrait').catch(function(e){});
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
