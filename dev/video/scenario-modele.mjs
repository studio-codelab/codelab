// Le scenario de la video de presentation de ce projet.
//
// « codelab video » le joue dans un vrai navigateur, filme l'ecran, ajoute
// la voix off (synthese locale, en francais) et monte le tout dans
// video/presentation.mp4 -- que le hub montre ensuite sur la tuile de
// l'application. Rien n'est envoye a un service en ligne.
//
// Chaque etape : un geste (ce que fait le navigateur) et une voix (ce qu'on
// entend pendant ce temps). L'etape dure au moins le temps de sa phrase.
// Outils du geste : page (Playwright), cliquer(selecteur),
// taper(selecteur, texte), survoler(selecteur), defiler(pixels), pause(s).
//
// Une musique ? Depose video/musique.mp3 : elle passe doucement sous la voix.

export default {
  titre: "__TITRE__",
  accroche: "Une phrase qui dit à quoi sert l’application.",
  // L'adresse a filmer. Pour une application publique, celle du hub ; pour
  // une application privee, lance-la dans un AUTRE terminal du poste, laisse-
  // la tourner pendant le tournage, et mets son adresse locale (par exemple
  // http://127.0.0.1:8000/ ; « localhost » est compris comme 127.0.0.1).
  adresse: "__ADRESSE__",
  taille: { largeur: 1600, hauteur: 900 },   // 16:9, comme la vidéo
  etapes: [
    {
      voix: "Voici __TITRE__.",
      geste: async ({ page }) => { await page.goto("./"); },
      duree: 3,
    },
    {
      voix: "Décris ici ce que montre cette étape.",
      geste: async ({ defiler }) => { await defiler(400); },
    },
  ],
  fin: { titre: "__TITRE__", texte: "Disponible sur ton hub CodeLab." },
};
