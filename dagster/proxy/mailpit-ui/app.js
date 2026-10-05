/* ===========================================================================
   L'interface Mailpit de CodeLab -- la logique.

   Tout passe par l'API publique de Mailpit (/api/v1) et son flux en direct
   (/api/events). Rien d'autre : pas de bibliotheque, pas de rendu cote
   serveur, pas de HTML venu d'un mail dans cette page.

   SECURITE. Le contenu d'un mail n'est JAMAIS insere dans ce document :
   les textes passent par textContent, et le HTML d'un mail ne s'affiche que
   dans un cadre isole (<iframe sandbox> sans allow-scripts), servi par
   Mailpit lui-meme (/view/<id>.html). La politique de securite de la page
   (posee par le proxy) interdit de toute facon tout script en ligne.
   =========================================================================== */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var appli = $("appli");

  /* --------------------------- fabrique de DOM --------------------------- */
  // h("a", {class: "x", href: "/", onclick: fn}, enfant, "texte"...)
  // Les textes sont TOUJOURS des noeuds texte : aucun HTML n'est interprete.
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
        else if (k === "style") Object.keys(v).forEach(function (p) { el.style.setProperty(p, v[p]); });
        else el.setAttribute(k, v === true ? "" : v);
      });
    }
    for (var i = 2; i < arguments.length; i++) ajouter(el, arguments[i]);
    return el;
  }
  function ajouter(el, enfant) {
    if (enfant === null || enfant === undefined || enfant === false) return;
    if (Array.isArray(enfant)) { enfant.forEach(function (e) { ajouter(el, e); }); return; }
    el.appendChild(typeof enfant === "string" || typeof enfant === "number"
      ? document.createTextNode(String(enfant)) : enfant);
  }
  function vider(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  function icone(trace) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    var p = document.createElementNS(ns, "path");
    p.setAttribute("d", trace);
    svg.appendChild(p);
    return svg;
  }
  var TRACES = {
    boite: "M3 12h5l2 3h4l2-3h5M5.5 5h13L21 12v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6z",
    nonlu: "M4 6h16v12H4zM4 7l8 6 8-6",
    pj: "M21 12.5l-8.6 8.6a5 5 0 0 1-7.1-7.1l8.6-8.6a3.5 3.5 0 0 1 5 5l-8.6 8.6a2 2 0 0 1-2.9-2.9l7.9-7.9",
    fichier: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5"
  };

  /* ------------------------------- l'etat -------------------------------- */
  var LIMITE = 50;
  var etat = {
    filtre: "tout",          // tout | nonlus | pj
    etiquette: "",           // filtre par etiquette, en plus
    q: "",                   // texte cherche
    debut: 0,
    messages: [],
    trouves: 0,              // messages qui repondent au filtre
    total: 0,                // toute la boite
    nonLus: 0,
    avecPJ: 0,
    etiquettes: {},          // nom -> nombre
    id: null,                // message ouvert
    message: null,
    onglet: "html",
    controleHtml: null,
    controleLiens: null
  };

  /* -------------------------------- l'API -------------------------------- */
  function api(chemin, options) {
    options = options || {};
    var init = { method: options.method || "GET", credentials: "same-origin",
                 headers: { "Accept": "application/json" } };
    if (options.corps !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.corps);
    }
    return fetch(chemin, init).then(function (r) {
      // Session expiree : le proxy renvoie vers la connexion du panneau.
      if (r.redirected || r.status === 401) {
        throw new Error("Session expirée : reconnecte-toi au panneau, puis recharge cette page.");
      }
      if (!r.ok) {
        return r.text().then(function (t) { throw new Error(t.trim() || ("Erreur " + r.status)); });
      }
      var type = r.headers.get("Content-Type") || "";
      if (options.texte) return r.text();
      return type.indexOf("json") >= 0 ? r.json() : r.text();
    });
  }
  function enc(s) { return encodeURIComponent(s); }

  function requete() {
    var morceaux = [];
    if (etat.filtre === "nonlus") morceaux.push("is:unread");
    if (etat.filtre === "pj") morceaux.push("has:attachment");
    if (etat.etiquette) morceaux.push('tag:"' + etat.etiquette.replace(/"/g, "") + '"');
    if (etat.q) morceaux.push(etat.q);
    return morceaux.join(" ");
  }

  /* ------------------------------ formatage ------------------------------ */
  var fmtHeure = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" });
  var fmtJour = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short" });
  var fmtJourAn = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", year: "numeric" });
  var fmtLong = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", month: "short",
    year: "numeric", hour: "2-digit", minute: "2-digit" });
  function dateCourte(iso) {
    var d = new Date(iso), maintenant = new Date();
    if (isNaN(d)) return "";
    if (d.toDateString() === maintenant.toDateString()) return fmtHeure.format(d);
    var hier = new Date(maintenant); hier.setDate(hier.getDate() - 1);
    if (d.toDateString() === hier.toDateString()) return "Hier";
    return d.getFullYear() === maintenant.getFullYear() ? fmtJour.format(d) : fmtJourAn.format(d);
  }
  function dateLongue(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    if (d.toDateString() === new Date().toDateString()) return "Aujourd'hui, " + fmtHeure.format(d);
    return fmtLong.format(d);
  }
  function taille(o) {
    if (!o && o !== 0) return "";
    if (o < 1024) return o + " o";
    if (o < 1024 * 1024) return (o / 1024).toFixed(1).replace(".", ",") + " ko";
    return (o / 1024 / 1024).toFixed(1).replace(".", ",") + " Mo";
  }
  function nomDe(adr) {
    if (!adr) return "";
    return adr.Name || adr.Address || "";
  }
  function listeAdresses(liste) {
    return (liste || []).map(function (a) { return a.Name ? a.Name + " <" + a.Address + ">" : a.Address; }).join(", ");
  }

  // Une couleur stable par nom : la meme etiquette, le meme expediteur, la
  // meme teinte partout.
  var TEINTES = ["#635BFF", "#22A35A", "#3B82F6", "#E5484D", "#F59E0B", "#14B8A6", "#EC4899", "#8B5CF6", "#0EA5E9", "#EB5424"];
  function teinte(nom) {
    var x = 0, s = String(nom || "").toLowerCase();
    for (var i = 0; i < s.length; i++) x = (x * 31 + s.charCodeAt(i)) >>> 0;
    return TEINTES[x % TEINTES.length];
  }
  function avatar(adr, grand) {
    var nom = nomDe(adr) || "?";
    var lettre = (nom.match(/[\p{L}\p{N}]/u) || ["?"])[0];
    return h("span", { class: "avatar" + (grand ? " grand" : ""), style: { background: teinte(adr && adr.Address || nom) },
      "aria-hidden": "true" }, lettre);
  }
  function etiquette(nom, retirer) {
    return h("span", { class: "etiquette", style: { "--t": teinte(nom) } },
      "#" + nom,
      retirer ? h("button", { type: "button", "aria-label": "Retirer l'étiquette " + nom, title: "Retirer",
        onclick: function (e) { e.preventDefault(); e.stopPropagation(); retirer(nom); } }, "×") : null);
  }

  /* -------------------------------- toast -------------------------------- */
  var minuteurToast = null;
  function toast(texte) {
    var t = $("toast");
    t.textContent = texte;
    t.hidden = false;
    clearTimeout(minuteurToast);
    minuteurToast = setTimeout(function () { t.hidden = true; }, 3200);
  }
  function erreur(e) { toast(e && e.message ? e.message : String(e)); }

  function confirmer(titre, texte, libelle) {
    var d = $("confirmer");
    $("confirmer-titre").textContent = titre;
    $("confirmer-texte").textContent = texte;
    $("confirmer-ok").textContent = libelle || "Supprimer";
    return new Promise(function (ok) {
      d.addEventListener("close", function fin() {
        d.removeEventListener("close", fin);
        ok(d.returnValue === "oui");
      });
      d.showModal();
    });
  }

  /* ------------------------------ l'adresse ------------------------------ */
  // /message/<id>?filtre=nonlus&etiquette=x&q=...&page=2 : chaque vue a son
  // adresse, et un rechargement garde la meme.
  function adresse() {
    var p = new URLSearchParams();
    if (etat.filtre !== "tout") p.set("filtre", etat.filtre);
    if (etat.etiquette) p.set("etiquette", etat.etiquette);
    if (etat.q) p.set("q", etat.q);
    if (etat.debut) p.set("page", String(etat.debut / LIMITE + 1));
    var qs = p.toString();
    return (etat.id ? "/message/" + enc(etat.id) : "/") + (qs ? "?" + qs : "");
  }
  function majAdresse(remplacer) {
    var a = adresse();
    if (a === location.pathname + location.search) return;
    if (remplacer) history.replaceState(null, "", a); else history.pushState(null, "", a);
  }
  function lireAdresse() {
    var p = new URLSearchParams(location.search);
    var f = p.get("filtre");
    etat.filtre = f === "nonlus" || f === "pj" ? f : "tout";
    etat.etiquette = p.get("etiquette") || "";
    etat.q = p.get("q") || "";
    var page = parseInt(p.get("page"), 10);
    etat.debut = page > 1 ? (page - 1) * LIMITE : 0;
    var m = location.pathname.match(/^\/message\/([A-Za-z0-9]+)$/);
    etat.id = m ? m[1] : null;
    $("recherche-champ").value = etat.q;
  }

  /* -------------------------------- le menu ------------------------------ */
  function rendreMenu() {
    var filtres = [
      ["tout", "Réception", TRACES.boite, etat.total],
      ["nonlus", "Non lus", TRACES.nonlu, etat.nonLus],
      ["pj", "Avec pièce jointe", TRACES.pj, etat.avecPJ]
    ];
    vider($("filtres"));
    filtres.forEach(function (f) {
      var actif = etat.filtre === f[0] && !etat.etiquette;
      $("filtres").appendChild(h("button", { type: "button", class: "menu-ligne" + (actif ? " actif" : ""),
        "aria-current": actif ? "true" : null,
        onclick: function () { choisirFiltre(f[0], ""); } },
        icone(f[2]), h("span", { text: f[1] }), h("span", { class: "nombre", text: String(f[3]) })));
    });
    var noms = Object.keys(etat.etiquettes).sort(function (a, b) { return a.localeCompare(b, "fr"); });
    vider($("etiquettes"));
    $("etiquettes-vide").hidden = noms.length > 0;
    noms.forEach(function (nom) {
      var actif = etat.etiquette === nom;
      $("etiquettes").appendChild(h("button", { type: "button", class: "menu-ligne" + (actif ? " actif" : ""),
        "aria-current": actif ? "true" : null,
        onclick: function () { choisirFiltre("tout", actif ? "" : nom); } },
        h("span", { class: "carre", style: { background: teinte(nom) }, "aria-hidden": "true" }, "#"),
        h("span", { text: nom }),
        h("span", { class: "nombre nu", text: String(etat.etiquettes[nom]) })));
    });
    var filtre = !!requete();
    $("tout-lu").lastElementChild.textContent = filtre ? "Marquer ces messages lus" : "Tout marquer lu";
    $("tout-supprimer").lastElementChild.textContent = filtre ? "Supprimer ces messages" : "Tout supprimer";
  }
  function choisirFiltre(filtre, etiq) {
    etat.filtre = filtre;
    etat.etiquette = etiq;
    etat.debut = 0;
    fermerMenu();
    majAdresse();
    chargerListe();
    rendreMenu();
  }
  function ouvrirMenu() {
    appli.classList.add("menu-ouvert");
    $("voile").hidden = false;
    $("menu-btn").setAttribute("aria-expanded", "true");
  }
  function fermerMenu() {
    appli.classList.remove("menu-ouvert");
    $("voile").hidden = true;
    $("menu-btn").setAttribute("aria-expanded", "false");
  }

  /* ------------------------------- compteurs ----------------------------- */
  function chargerCompteurs() {
    return Promise.all([
      api("/api/v1/info"),
      api("/api/v1/search?query=" + enc("has:attachment") + "&limit=1")
    ]).then(function (r) {
      etat.total = r[0].Messages || 0;
      etat.nonLus = r[0].Unread || 0;
      etat.etiquettes = r[0].Tags || {};
      etat.avecPJ = r[1].messages_count || 0;
      etat.version = r[0].Version || "";
      etat.infos = r[0];
      rendreMenu();
      document.title = (etat.nonLus ? "(" + etat.nonLus + ") " : "") + "Mailpit · CodeLab";
    }).catch(erreur);
  }

  /* -------------------------------- la liste ----------------------------- */
  function chargerListe() {
    var rq = requete();
    var url = rq
      ? "/api/v1/search?query=" + enc(rq) + "&start=" + etat.debut + "&limit=" + LIMITE
      : "/api/v1/messages?start=" + etat.debut + "&limit=" + LIMITE;
    return api(url).then(function (d) {
      etat.messages = d.messages || [];
      etat.trouves = d.messages_count || 0;
      // Une page videe (suppressions) : on recule d'une page.
      if (!etat.messages.length && etat.debut > 0) {
        etat.debut = Math.max(0, etat.debut - LIMITE);
        majAdresse(true);
        return chargerListe();
      }
      rendreListe();
    }).catch(erreur);
  }
  function titreListe() {
    if (etat.etiquette) return "#" + etat.etiquette;
    return { tout: "Réception", nonlus: "Non lus", pj: "Avec pièce jointe" }[etat.filtre];
  }
  function rendreListe() {
    $("liste-titre").textContent = etat.q ? "Recherche" : titreListe();
    var n = etat.trouves;
    $("liste-sous-titre").textContent = (n === 0 ? "Aucun message" : n === 1 ? "1 message" : n + " messages")
      + (etat.q ? " pour « " + etat.q + " »" : "")
      + (!etat.q && etat.filtre === "tout" && !etat.etiquette && etat.nonLus ? " · " + etat.nonLus + " non lus" : "");
    var cible = vider($("liste-messages"));
    etat.messages.forEach(function (m) {
      var lien = h("a", { href: "/message/" + enc(m.ID), role: "listitem",
          class: "ligne" + (m.Read ? "" : " non-lue") + (m.ID === etat.id ? " choisie" : ""),
          "aria-current": m.ID === etat.id ? "true" : null,
          onclick: function (e) {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
            e.preventDefault();
            ouvrir(m.ID);
          } },
        avatar(m.From),
        h("span", { class: "ligne-texte" },
          h("span", { class: "ligne-haut" },
            h("span", { class: "point", "aria-label": m.Read ? null : "Non lu", role: m.Read ? null : "img" }),
            h("span", { class: "ligne-de", text: nomDe(m.From) || "(expéditeur inconnu)" }),
            h("span", { class: "ligne-heure", text: dateCourte(m.Created) })),
          h("span", { class: "ligne-sujet", text: m.Subject || "(sans objet)" }),
          h("span", { class: "ligne-extrait", text: m.Snippet || "" }),
          h("span", { class: "ligne-etiquettes" }, (m.Tags || []).map(function (t) { return etiquette(t); }))));
      cible.appendChild(lien);
    });
    var vide = etat.messages.length === 0;
    $("liste-vide").hidden = !vide;
    $("liste-vide-titre").textContent = requete() ? "Aucun message ne correspond" : "Aucun message";
    $("liste-vide-texte").textContent = requete()
      ? "Change de filtre ou de recherche."
      : "Les mails envoyés par tes applications arrivent ici.";
    // Pagination
    var fin = Math.min(etat.debut + LIMITE, n);
    $("pagination-texte").textContent = n ? (etat.debut + 1) + "–" + fin + " sur " + n : "";
    $("page-prec").disabled = etat.debut === 0;
    $("page-suiv").disabled = fin >= n;
  }

  /* ------------------------------- un message ---------------------------- */
  function ouvrir(id, sansHistorique) {
    etat.id = id;
    etat.onglet = "html";
    etat.controleHtml = null;
    etat.controleLiens = null;
    if (!sansHistorique) majAdresse();
    appli.classList.add("avec-message");
    marquerChoisi();
    return api("/api/v1/message/" + enc(id)).then(function (m) {
      if (etat.id !== id) return;
      etat.message = m;
      etat.onglet = m.HTML ? "html" : "texte";
      // Mailpit le marque lu en le servant : la liste suit sans attendre.
      etat.messages.forEach(function (x) { if (x.ID === id && !x.Read) { x.Read = true; etat.nonLus = Math.max(0, etat.nonLus - 1); } });
      rendreListe();
      rendreMenu();
      rendreMessage();
      $("message").scrollTop = 0;
      $("lecture").scrollTop = 0;
      if (m.HTML) chargerControleHtml(id);
    }).catch(function (e) {
      erreur(e);
      fermerMessage();
    });
  }
  function marquerChoisi() {
    Array.prototype.forEach.call($("liste-messages").children, function (el) {
      var choisi = el.getAttribute("href") === "/message/" + enc(etat.id);
      el.classList.toggle("choisie", choisi);
      if (choisi) el.setAttribute("aria-current", "true"); else el.removeAttribute("aria-current");
    });
  }
  function fermerMessage(sansHistorique) {
    etat.id = null;
    etat.message = null;
    appli.classList.remove("avec-message");
    $("message").hidden = true;
    $("details").hidden = true;
    marquerChoisi();
    if (!sansHistorique) majAdresse();
  }

  function rendreMessage() {
    var m = etat.message;
    if (!m) return;
    $("message").hidden = false;
    $("details").hidden = false;
    $("m-sujet").textContent = m.Subject || "(sans objet)";
    var npj = (m.Attachments || []).length;
    $("m-pj").hidden = !npj;
    $("m-pj").textContent = npj + (npj > 1 ? " pièces jointes" : " pièce jointe");
    var av = $("m-avatar");
    var nouveau = avatar(m.From, true);
    nouveau.id = "m-avatar";
    av.replaceWith(nouveau);
    $("m-de").textContent = nomDe(m.From) || "(expéditeur inconnu)";
    var dest = listeAdresses(m.To);
    $("m-adresses").textContent = ((m.From && m.From.Address) || "") + (dest ? " · à " + dest : "");
    $("m-date").textContent = dateLongue(m.Date);
    $("m-date").setAttribute("datetime", m.Date || "");
    var dl = "/api/v1/message/" + enc(m.ID) + "/raw?dl=1";
    $("act-telecharger").setAttribute("href", dl);
    $("d-telecharger").setAttribute("href", dl);
    var i = indexCourant();
    $("act-prec").disabled = i <= 0;
    $("act-suiv").disabled = i < 0 || i >= etat.messages.length - 1;
    rendreEtiquettes();
    rendreOnglets();
    rendreVue();
    rendreDetails();
  }
  function indexCourant() {
    for (var i = 0; i < etat.messages.length; i++) if (etat.messages[i].ID === etat.id) return i;
    return -1;
  }

  /* Les etiquettes du message : retirer, ajouter. */
  function enregistrerEtiquettes(liste) {
    var m = etat.message;
    return api("/api/v1/tags", { method: "PUT", corps: { IDs: [m.ID], Tags: liste } }).then(function () {
      m.Tags = liste;
      etat.messages.forEach(function (x) { if (x.ID === m.ID) x.Tags = liste.slice(); });
      rendreEtiquettes();
      rendreListe();
      chargerCompteurs();
    }).catch(erreur);
  }
  function rendreEtiquettes(enSaisie) {
    var m = etat.message;
    var cible = vider($("m-etiquettes"));
    (m.Tags || []).forEach(function (t) {
      cible.appendChild(etiquette(t, function (nom) {
        enregistrerEtiquettes((m.Tags || []).filter(function (x) { return x !== nom; }));
      }));
    });
    if (enSaisie) {
      var champ = h("input", { class: "champ-etiquette", type: "text", "aria-label": "Nouvelle étiquette",
        placeholder: "nouvelle-etiquette", maxlength: "60",
        onkeydown: function (e) {
          if (e.key === "Escape") { rendreEtiquettes(); return; }
          if (e.key !== "Enter") return;
          e.preventDefault();
          var nom = champ.value.trim();
          if (!nom) { rendreEtiquettes(); return; }
          var tags = (m.Tags || []).slice();
          if (tags.indexOf(nom) < 0) tags.push(nom);
          enregistrerEtiquettes(tags);
        },
        onblur: function () { setTimeout(function () { if (document.activeElement !== champ) rendreEtiquettes(); }, 120); } });
      cible.appendChild(champ);
      champ.focus();
    } else {
      cible.appendChild(h("button", { type: "button", class: "ajout-etiquette",
        onclick: function () { rendreEtiquettes(true); } }, "+ Étiquette"));
    }
  }

  /* Les onglets : HTML, Texte, En-tetes, Source, Controle HTML, Liens. */
  function onglets() {
    var m = etat.message, l = [];
    if (m.HTML) l.push(["html", "HTML"]);
    l.push(["texte", "Texte"]);
    l.push(["entetes", "En-têtes"]);
    l.push(["source", "Source"]);
    if (m.HTML) l.push(["controle", "Contrôle HTML", etat.controleHtml ? pourcent(etat.controleHtml) : ""]);
    l.push(["liens", "Liens", etat.controleLiens ? String((etat.controleLiens.Links || []).length) : ""]);
    if ((m.Attachments || []).length) l.push(["pj", "Pièces jointes", String(m.Attachments.length)]);
    return l;
  }
  function rendreOnglets() {
    var cible = vider($("onglets"));
    onglets().forEach(function (o) {
      var actif = etat.onglet === o[0];
      cible.appendChild(h("button", { type: "button", class: "onglet" + (actif ? " actif" : ""),
        "aria-current": actif ? "page" : null,
        onclick: function () { etat.onglet = o[0]; rendreOnglets(); rendreVue(); } },
        o[1], o[2] ? h("span", { class: "nombre", text: o[2] }) : null));
    });
  }
  function pourcent(c) {
    var t = c && c.Total;
    return t ? Math.round(t.Supported) + " %" : "";
  }
  function rendreVue() {
    var m = etat.message, vue = vider($("vue")), id = m.ID;
    if (etat.onglet === "html") {
      // Le HTML du mail, isole : pas de script, pas de formulaire. Meme
      // origine (allow-same-origin) pour que ses images internes, servies
      // derriere la session, s'affichent -- sans allow-scripts, cela ne lui
      // donne aucun moyen d'agir sur cette page.
      var cadre = h("iframe", { class: "apercu", title: "Aperçu du message",
        src: "/view/" + enc(id) + ".html", referrerpolicy: "no-referrer", loading: "lazy",
        sandbox: "allow-same-origin allow-popups allow-popups-to-escape-sandbox" });
      cadre.addEventListener("load", function () { ajusterCadre(cadre); });
      vue.appendChild(cadre);
    } else if (etat.onglet === "texte") {
      vue.appendChild(h("pre", { class: "texte-brut", text: m.Text || "Ce message n'a pas de partie texte." }));
    } else if (etat.onglet === "entetes") {
      vue.appendChild(h("p", { class: "chargement", text: "Chargement…" }));
      api("/api/v1/message/" + enc(id) + "/headers").then(function (d) {
        if (etat.id !== id || etat.onglet !== "entetes") return;
        var corps = h("tbody");
        Object.keys(d).sort().forEach(function (k) {
          (d[k] || []).forEach(function (v) { corps.appendChild(h("tr", null, h("th", { text: k }), h("td", { text: v }))); });
        });
        vider(vue).appendChild(h("table", { class: "tableau" }, corps));
      }).catch(erreur);
    } else if (etat.onglet === "source") {
      vue.appendChild(h("p", { class: "chargement", text: "Chargement…" }));
      api("/api/v1/message/" + enc(id) + "/raw", { texte: true }).then(function (t) {
        if (etat.id !== id || etat.onglet !== "source") return;
        var MAX = 400000;
        vider(vue).appendChild(h("pre", { class: "texte-brut",
          text: t.length > MAX ? t.slice(0, MAX) + "\n\n[… source tronquée ici : télécharge le .eml pour la suite]" : t }));
      }).catch(erreur);
    } else if (etat.onglet === "controle") {
      rendreControleHtml(vue);
    } else if (etat.onglet === "liens") {
      rendreControleLiens(vue);
    } else if (etat.onglet === "pj") {
      var liste = h("div", { class: "pieces" });
      (m.Attachments || []).forEach(function (p) {
        liste.appendChild(h("a", { class: "piece", href: "/api/v1/message/" + enc(id) + "/part/" + enc(p.PartID),
          download: p.FileName || "piece-jointe" },
          icone(TRACES.fichier), h("span", { text: p.FileName || "(sans nom)" }), h("small", { text: taille(p.Size) })));
      });
      vue.appendChild(liste);
    }
  }
  function ajusterCadre(cadre) {
    try {
      var doc = cadre.contentDocument;
      if (!doc || !doc.body) return;
      // Les liens d'un mail s'ouvrent dans un nouvel onglet, jamais a la
      // place de l'apercu.
      Array.prototype.forEach.call(doc.querySelectorAll("a[href]"), function (a) {
        if ((a.getAttribute("href") || "").charAt(0) === "#") return;
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noopener noreferrer");
      });
      var hauteur = Math.max(doc.documentElement.scrollHeight, doc.body.scrollHeight);
      cadre.style.height = Math.max(360, hauteur + 4) + "px";
    } catch (e) { /* cadre inaccessible : hauteur minimale */ }
  }

  function chargerControleHtml(id) {
    return api("/api/v1/message/" + enc(id) + "/html-check").then(function (d) {
      if (etat.id !== id) return;
      etat.controleHtml = d;
      rendreOnglets();
      rendreDetails();
      if (etat.onglet === "controle") rendreVue();
    }).catch(function () { /* controle indisponible : on n'insiste pas */ });
  }
  function classeScore(p) { return p >= 85 ? "bon" : p >= 60 ? "moyen" : "mauvais"; }
  function rendreControleHtml(vue) {
    var c = etat.controleHtml;
    if (!c) { vue.appendChild(h("p", { class: "chargement", text: "Analyse en cours…" })); return; }
    var t = c.Total || {};
    vue.appendChild(h("div", { class: "carte" },
      h("h4", { text: "Compatibilité avec les messageries : " + Math.round(t.Supported || 0) + " %" }),
      h("p", { text: (t.Tests || 0) + " tests sur " + (t.Nodes || 0) + " éléments du HTML." }),
      h("div", { class: "jauge", role: "img", "aria-label": "Pris en charge " + Math.round(t.Supported || 0) + " %" },
        h("i", { style: { width: (t.Supported || 0) + "%", background: "var(--vert)" } }),
        h("i", { style: { width: (t.Partial || 0) + "%", background: "var(--orange)" } }),
        h("i", { style: { width: (t.Unsupported || 0) + "%", background: "var(--rouge)" } })),
      h("div", { class: "legende" },
        h("span", null, h("b", { text: Math.round(t.Supported || 0) + " %" }), " pris en charge"),
        h("span", null, h("b", { text: Math.round(t.Partial || 0) + " %" }), " partiel"),
        h("span", null, h("b", { text: Math.round(t.Unsupported || 0) + " %" }), " non pris en charge"))));
    (c.Warnings || []).slice().sort(function (a, b) {
      return ((a.Score || {}).Supported || 0) - ((b.Score || {}).Supported || 0);
    }).forEach(function (w) {
      var s = w.Score || {};
      vue.appendChild(h("div", { class: "carte" },
        h("h4", { text: w.Title || w.Slug || "" }),
        h("p", { text: (w.Category ? w.Category.toUpperCase() + " · " : "") + Math.round(s.Supported || 0) + " % pris en charge"
          + (s.Partial ? ", " + Math.round(s.Partial) + " % partiel" : "") })));
    });
  }

  function verifierLiens() {
    var id = etat.id;
    $("d-liens").disabled = true;
    $("d-liens").textContent = "…";
    return api("/api/v1/message/" + enc(id) + "/link-check").then(function (d) {
      if (etat.id !== id) return;
      etat.controleLiens = d;
      rendreOnglets();
      rendreDetails();
      if (etat.onglet === "liens") rendreVue();
    }).catch(erreur).then(function () { $("d-liens").disabled = false; });
  }
  function rendreControleLiens(vue) {
    var c = etat.controleLiens;
    if (!c) {
      vue.appendChild(h("div", { class: "carte" },
        h("h4", { text: "Vérifier les liens du message" }),
        h("p", { text: "Mailpit interroge chaque adresse du message et rapporte sa réponse. Rien ne part sans ton clic." }),
        h("p", null, h("button", { type: "button", class: "bouton principal", style: { "margin-top": "12px" },
          onclick: verifierLiens }, "Vérifier les liens"))));
      return;
    }
    var liens = c.Links || [];
    if (!liens.length) { vue.appendChild(h("div", { class: "carte" }, h("h4", { text: "Aucun lien dans ce message." }))); return; }
    var corps = h("tbody");
    liens.forEach(function (l) {
      var ok = l.StatusCode >= 200 && l.StatusCode < 400;
      corps.appendChild(h("tr", null,
        h("th", null, h("span", { class: "score " + (ok ? "bon" : "mauvais"), text: String(l.StatusCode || "—") })),
        h("td", { text: l.URL + (l.Status ? "  ·  " + l.Status : "") })));
    });
    vue.appendChild(h("table", { class: "tableau" }, corps));
  }

  /* La colonne des details. */
  function rendreDetails() {
    var m = etat.message;
    if (!m) return;
    var lignes = [
      ["De", listeAdresses(m.From ? [m.From] : [])],
      ["À", listeAdresses(m.To)],
      ["Cc", listeAdresses(m.Cc)],
      ["Répondre à", listeAdresses(m.ReplyTo)],
      ["Objet", m.Subject || "(sans objet)"],
      ["Date", dateLongue(m.Date)],
      ["Message-ID", m.MessageID ? "<" + m.MessageID + ">" : ""],
      ["Retour", m.ReturnPath || ""],
      ["Taille", taille(m.Size)]
    ];
    var g = vider($("d-grille"));
    lignes.forEach(function (l) {
      if (!l[1]) return;
      g.appendChild(h("dt", { text: l[0] }));
      g.appendChild(h("dd", { text: l[1] }));
    });
    var sh = $("d-html");
    if (!m.HTML) { sh.textContent = "pas de HTML"; sh.className = "score"; }
    else if (!etat.controleHtml) { sh.textContent = "…"; sh.className = "score"; }
    else {
      var p = Math.round(etat.controleHtml.Total.Supported || 0);
      sh.textContent = p + " %";
      sh.className = "score " + classeScore(p);
    }
    var bl = $("d-liens");
    if (etat.controleLiens) {
      var ls = etat.controleLiens.Links || [], err = etat.controleLiens.Errors || 0;
      bl.textContent = ls.length ? (err ? err + " en erreur" : ls.length + " OK") : "aucun";
      bl.className = "score " + (err ? "mauvais" : "bon");
    } else {
      bl.textContent = "Vérifier";
      bl.className = "lien-btn";
    }
  }

  /* -------------------------------- actions ------------------------------ */
  function marquerNonLu() {
    var id = etat.id;
    api("/api/v1/messages", { method: "PUT", corps: { IDs: [id], Read: false } }).then(function () {
      toast("Marqué comme non lu.");
      etat.messages.forEach(function (x) { if (x.ID === id) x.Read = false; });
      fermerMessage();
      rendreListe();
      chargerCompteurs();
    }).catch(erreur);
  }
  function supprimer() {
    var m = etat.message;
    if (!m) return;
    confirmer("Supprimer ce message ?", "« " + (m.Subject || "(sans objet)") + " » sera supprimé de Mailpit.").then(function (oui) {
      if (!oui) return;
      var i = indexCourant();
      var suivant = etat.messages[i + 1] || etat.messages[i - 1] || null;
      api("/api/v1/messages", { method: "DELETE", corps: { IDs: [m.ID] } }).then(function () {
        toast("Message supprimé.");
        etat.messages = etat.messages.filter(function (x) { return x.ID !== m.ID; });
        if (suivant && window.matchMedia("(min-width: 760px)").matches) ouvrir(suivant.ID);
        else fermerMessage();
        chargerListe();
        chargerCompteurs();
      }).catch(erreur);
    });
  }
  function toutMarquerLu() {
    var rq = requete();
    api("/api/v1/messages", { method: "PUT", corps: rq ? { Search: rq, Read: true } : { Read: true } }).then(function () {
      toast(rq ? "Ces messages sont marqués lus." : "Tout est marqué lu.");
      fermerMenu();
      chargerListe();
      chargerCompteurs();
    }).catch(erreur);
  }
  function toutSupprimer() {
    var rq = requete();
    var n = rq ? etat.trouves : etat.total;
    if (!n) { toast("Rien à supprimer."); return; }
    confirmer(rq ? "Supprimer ces " + n + " messages ?" : "Supprimer les " + n + " messages ?",
      rq ? "Tous les messages qui répondent au filtre actuel seront supprimés de Mailpit."
         : "La boîte sera entièrement vidée. C'est définitif.",
      "Tout supprimer").then(function (oui) {
      if (!oui) return;
      var req = rq
        ? api("/api/v1/search?query=" + enc(rq), { method: "DELETE" })
        : api("/api/v1/messages", { method: "DELETE", corps: { IDs: [] } });
      req.then(function () {
        toast("Messages supprimés.");
        fermerMenu();
        fermerMessage(true);
        etat.debut = 0;
        majAdresse(true);
        chargerListe();
        chargerCompteurs();
      }).catch(erreur);
    });
  }
  function voisin(sens) {
    var i = indexCourant() + sens;
    if (i >= 0 && i < etat.messages.length) ouvrir(etat.messages[i].ID);
  }
  function aPropos() {
    var g = vider($("apropos-grille")), i = etat.infos || {};
    [["Version", i.Version || ""], ["Messages", String(i.Messages || 0)], ["Non lus", String(i.Unread || 0)],
     ["Base", taille(i.DatabaseSize)], ["Direct", connecte ? "connecté" : "hors ligne"]].forEach(function (l) {
      g.appendChild(h("dt", { text: l[0] }));
      g.appendChild(h("dd", { text: l[1] }));
    });
    $("apropos").showModal();
  }

  /* ------------------------------ le direct ------------------------------ */
  // Mailpit annonce chaque arrivee, lecture, suppression par websocket. On
  // ne rejoue pas ses evenements un par un : on recharge, en regroupant les
  // rafales (une application qui envoie 50 mails = un seul rechargement).
  var connecte = false, minuteurDirect = null, attente = 1000;
  function direct() {
    var ws;
    try {
      ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/events");
    } catch (e) { return reessayer(); }
    ws.onopen = function () { connecte = true; attente = 1000; majDirect(); };
    ws.onmessage = function (e) {
      var d;
      try { d = JSON.parse(e.data); } catch (x) { return; }
      if (!d || d.Type === "stats") {
        if (d && d.Data) { etat.total = d.Data.Total; etat.nonLus = d.Data.Unread; rendreMenu(); }
        return;
      }
      if (d.Type === "new" && d.Data && etat.debut === 0) {
        toast("Nouveau message : " + (d.Data.Subject || "(sans objet)"));
      }
      if (d.Type === "delete" && d.Data && d.Data.ID === etat.id) fermerMessage();
      if (d.Type === "truncate" || d.Type === "prune") { if (etat.id) fermerMessage(true); }
      clearTimeout(minuteurDirect);
      minuteurDirect = setTimeout(function () { chargerListe(); chargerCompteurs(); }, 400);
    };
    ws.onclose = function () { connecte = false; majDirect(); reessayer(); };
    ws.onerror = function () { try { ws.close(); } catch (x) { /* deja ferme */ } };
  }
  function reessayer() {
    setTimeout(direct, attente);
    attente = Math.min(attente * 2, 15000);
  }
  function majDirect() {
    var el = $("direct");
    el.classList.toggle("actif", connecte);
    el.lastElementChild.textContent = connecte ? "Direct" : "Hors ligne";
    el.title = connecte ? "Les nouveaux messages arrivent sans recharger"
                        : "Connexion au direct perdue : nouvelle tentative en cours";
  }

  /* --------------------------------- liens -------------------------------- */
  // Le retour au hub : le panneau, sur le meme hote, port 9001.
  $("retour-codelab").setAttribute("href", location.protocol + "//" + location.hostname + ":9001/");

  $("menu-btn").addEventListener("click", function () {
    if (appli.classList.contains("menu-ouvert")) fermerMenu(); else ouvrirMenu();
  });
  $("voile").addEventListener("click", fermerMenu);
  $("marque").addEventListener("click", function (e) {
    e.preventDefault();
    etat.q = "";
    $("recherche-champ").value = "";
    fermerMessage(true);
    choisirFiltre("tout", "");
  });
  var minuteurRecherche = null;
  $("recherche-champ").addEventListener("input", function () {
    clearTimeout(minuteurRecherche);
    minuteurRecherche = setTimeout(function () {
      etat.q = $("recherche-champ").value.trim();
      etat.debut = 0;
      majAdresse(true);
      chargerListe();
      rendreMenu();
    }, 280);
  });
  $("recherche").addEventListener("submit", function (e) { e.preventDefault(); });
  $("page-prec").addEventListener("click", function () {
    etat.debut = Math.max(0, etat.debut - LIMITE); majAdresse(); chargerListe();
  });
  $("page-suiv").addEventListener("click", function () {
    etat.debut += LIMITE; majAdresse(); chargerListe();
  });
  $("apropos-btn").addEventListener("click", aPropos);
  $("tout-lu").addEventListener("click", toutMarquerLu);
  $("tout-supprimer").addEventListener("click", toutSupprimer);
  $("retour-liste").addEventListener("click", function () { fermerMessage(); });
  $("act-nonlu").addEventListener("click", marquerNonLu);
  $("d-nonlu").addEventListener("click", marquerNonLu);
  $("act-supprimer").addEventListener("click", supprimer);
  $("d-supprimer").addEventListener("click", supprimer);
  $("act-prec").addEventListener("click", function () { voisin(-1); });
  $("act-suiv").addEventListener("click", function () { voisin(1); });
  $("d-liens").addEventListener("click", function () {
    if (etat.controleLiens) { etat.onglet = "liens"; rendreOnglets(); rendreVue(); }
    else verifierLiens();
  });
  $("d-etiquettes").addEventListener("click", function () { rendreEtiquettes(true); });
  $("d-entetes").addEventListener("click", function () { etat.onglet = "entetes"; rendreOnglets(); rendreVue(); });

  // « / » pour chercher, Echap pour sortir -- jamais pendant une saisie.
  document.addEventListener("keydown", function (e) {
    var saisie = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || "");
    if (e.key === "/" && !saisie && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      $("recherche-champ").focus();
    } else if (e.key === "Escape") {
      if (appli.classList.contains("menu-ouvert")) fermerMenu();
      else if (saisie && e.target.id === "recherche-champ") e.target.blur();
    }
  });

  window.addEventListener("popstate", function () {
    var avant = etat.id;
    lireAdresse();
    chargerListe();
    rendreMenu();
    if (etat.id && etat.id !== avant) ouvrir(etat.id, true);
    else if (!etat.id) fermerMessage(true);
  });

  /* -------------------------------- depart -------------------------------- */
  lireAdresse();
  rendreMenu();
  chargerCompteurs();
  chargerListe().then(function () {
    if (etat.id) ouvrir(etat.id, true);
  });
  direct();
})();
