/* Le theme choisi dans le panneau (clair, sombre ou automatique).
   Le panneau le pose dans le cookie « codelab-theme » : un cookie appartient
   a l'HOTE et ignore le port, il traverse donc jusqu'ici (8025). Lu AVANT
   le premier affichage -- ce script est charge sans defer -- pour ne pas
   montrer un eclair de la mauvaise couleur. */
(function () {
  try {
    var m = document.cookie.match(/(?:^|;\s*)codelab-theme=([^;]*)/);
    var t = m ? decodeURIComponent(m[1]) : "auto";
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* theme du systeme */ }
})();
