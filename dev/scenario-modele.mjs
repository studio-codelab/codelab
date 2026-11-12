// Le scenario de la video de presentation de ce projet.
//
// « codelab video » le joue dans un vrai navigateur, filme l'ecran, ajoute
// la voix off (synthese locale, en francais), l'habille (titre anime, fenetre
// sur fond vivant, sous-titres, musique) et monte le tout dans
// video/presentation.mp4 -- que le hub montre ensuite sur la tuile de
// l'application. Rien n'est envoye a un service en ligne.
//
// TEL QUEL, IL MARCHE : il ouvre l'application et la parcourt de haut en
// bas. Pour une vraie demonstration, remplace la deuxieme etape par les
// gestes qui montrent ce que l'application fait (ou demande-le a ton agent :
// section 14 du AGENTS.md du projet), puis relance « codelab video ».
//
// Chaque etape : un geste (ce que fait le navigateur) et une voix (ce qu'on
// entend, et lit en sous-titre). L'etape dure au moins le temps de sa phrase.
// Outils du geste : page (Playwright), cliquer(selecteur),
// taper(selecteur, texte), survoler(selecteur), defiler(pixels), pause(s),
// visiter() (parcourt la page sans rien cliquer).
//
// Une musique a toi ? Depose video/musique.mp3 : elle remplace celle que
// CodeLab compose.

export default {
  titre: "__TITRE__",
  // Vide : la description de l'application au panneau (celle de sa tuile).
  accroche: "",
  // L'adresse a filmer, par le hub : une application privee y est connectee
  // d'office, et demarree si elle est arretee.
  adresse: "__ADRESSE__",
  taille: { largeur: 1600, hauteur: 900 },   // 16:9, comme la vidéo
  etapes: [
    {
      voix: "Voici __TITRE__.",
      geste: async ({ page }) => { await page.goto("./"); },
      duree: 3,
    },
    {
      voix: "",
      geste: async ({ visiter }) => { await visiter(); },
    },
  ],
  fin: { titre: "__TITRE__", texte: "Disponible sur ton hub CodeLab." },
};
