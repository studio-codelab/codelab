/* CodeLab -- panneau/02-hub.js : Les categories, le hub (tuiles, recherche, masquage) et le mot a l'administrateur.
   Une partie du script du panneau, assemblee par app.py (PANNEAU_MODULES)
   dans l'ordre des numeros : un seul script, une seule portee. */
/* ---------------- categories ---------------- */
/* Un rangement, rien de plus : aucune categorie ne donne de droit. La liste
   est gardee en memoire pour peupler les deux menus deroulants (ajout et
   fiche) sans un aller-retour reseau a chaque ouverture. */
let categories=[];
/* « CodeLab » : la categorie des applications de la stack (Dagster, Mailpit,
   Messages, le Diagnostic), posee par le serveur. Montree en tete, jamais
   modifiable ni proposee pour un projet. */
let categoriesVerrouillees=['CodeLab'];
const categoriesLibres = () => categories.filter(c=>!categoriesVerrouillees.includes(c));

async function chargerCategories(){
  try{
    const d=await (await fetch('/api/categories')).json();
    categories=d.categories||[];
    if(Array.isArray(d.verrouillees)) categoriesVerrouillees=d.verrouillees;
  }catch(e){ return; }
  peindreCategories();
}

/* Les deux menus deroulants d'un projet. La categorie enregistree est
   proposee meme si elle vient de disparaitre de la liste : on ne veut pas
   qu'un simple passage sur la fiche la remplace en douce. */
function optionsCategorie(id, valeur){
  const libres=categoriesLibres();
  const liste = valeur && !libres.includes(valeur) && !categoriesVerrouillees.includes(valeur)
    ? libres.concat([valeur]) : libres;
  $(id).innerHTML = '<option value="">Sans catégorie</option>'
    + liste.map(c=>`<option value="${esc(c)}"${c===valeur?' selected':''}>${esc(c)}</option>`).join('');
}

/* La liste editable de Configuration. Elle vit dans le DOM : ce qu'on lit au
   moment d'enregistrer est exactement ce qui est affiche, ordre compris. */
function peindreCategories(){
  const libres=categoriesLibres();
  $('cat-liste').innerHTML = categoriesVerrouillees.map(c=>`<div class="cat-ligne cat-verrou">
        <svg class="cat-cadenas" viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>
        <div class="cat-verrou-txt"><b>${esc(c)}</b><span>Les applications de CodeLab réservées à l'administrateur : Dagster, Mailpit, Messages, Diagnostic. Posée d'office, non modifiable.</span></div>
      </div>`).join('')
    + (libres.length
    ? libres.map((c,i)=>`<div class="cat-ligne">
        <input value="${esc(c)}" maxlength="30" aria-label="Catégorie ${i+1}">
        <button class="icon-btn" title="Retirer" onclick="catRetirer(${i})">
          <svg viewBox="0 0 24 24"><path d="M6 6l12 12"/><path d="M18 6L6 18"/></svg></button>
      </div>`).join('')
    : '<div class="cat-vide">Aucune autre catégorie pour l’instant.</div>');
}
function catLues(){
  return Array.from(document.querySelectorAll('#cat-liste .cat-ligne:not(.cat-verrou) input'))
    .map(i=>i.value.trim()).filter(Boolean);
}
function catAjouter(){
  const v=$('cat-neuve').value.trim();
  if(!v) return;
  if(categoriesVerrouillees.some(c=>c.toLowerCase()===v.toLowerCase())){
    notifier('« '+v+' » est réservée aux applications de CodeLab.','err'); return; }
  categories = categoriesVerrouillees.concat(catLues(), [v]);
  $('cat-neuve').value='';
  peindreCategories();
}
function catRetirer(i){
  const l=catLues(); l.splice(i,1); categories=categoriesVerrouillees.concat(l); peindreCategories();
}
async function catEnregistrer(){
  const e=$('cat-err'), m=$('cat-msg');
  e.style.display='none'; m.style.display='none';
  $('cat-save').disabled=true;
  try{
    const r=await fetch('/api/categories',{method:'PUT',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({categories:catLues()})});
    const d=await r.json();
    if(!r.ok){ e.textContent=d.error||'Enregistrement impossible.'; e.style.display='block'; return; }
    categories=d.categories||[];
    if(Array.isArray(d.verrouillees)) categoriesVerrouillees=d.verrouillees;
    peindreCategories();
    /* Supprimer une categorie deplace des projets : le dire, plutot que de
       laisser decouvrir le changement au prochain passage sur le hub. */
    m.textContent = d.declasses
      ? `Enregistré. ${d.declasses} projet${d.declasses>1?'s':''} ${d.declasses>1?'passent':'passe'} sous « Autres ».`
      : 'Enregistre.';
    m.style.display='block';
    if(estAdmin) refresh();
  } finally { $('cat-save').disabled=false; }
}

/* ---------------- hub ---------------- */
let hubApps=[];
/* ---------------- les messages recus (administrateur) ----------------
   Ils se traitent dans l'application « Messages », une application du hub :
   boite de reception, planning, projets. Le panneau ne fait plus que les
   GUETTER, pour annoncer dans le coin celles qui arrivent pendant qu'on
   travaille ailleurs. */
let msgDernierVu=0, msgPremierChargement=true;

async function chargerMessages(){
  if(!estAdmin) return;
  let d;
  try{ d=await (await fetch('/api/messages/nouveaux')).json(); }catch(e){ return; }
  msgDernierVu = Math.max(msgDernierVu, ...(d.messages||[]).map(m=>m.ts||0), 0);
  msgPremierChargement=false;
}

/* UNE DEMANDE QUI ARRIVE SE VOIT, ou que l'on soit dans le panneau. La page
   demande toutes les vingt secondes ce qui est arrive DEPUIS ce qu'elle a
   deja vu -- pas la liste entiere -- et l'annonce dans le coin. */
async function guetterMessages(){
  if(!estAdmin || msgPremierChargement) return;
  let d;
  try{
    d=await (await fetch('/api/messages/nouveaux?apres='+encodeURIComponent(msgDernierVu))).json();
  }catch(e){ return; }
  const neufs=d.messages||[];
  if(!neufs.length) return;
  msgDernierVu = Math.max(msgDernierVu, ...neufs.map(m=>m.ts||0));
  const premier=neufs[0];
  notifierMessage(neufs.length===1
    ? 'Nouvelle demande de « ' + (premier.qui||'?') + ' ».'
    : neufs.length + ' nouveaux messages.');
  /* La tuile « Messages » du hub porte le compte : on la repeint. */
  if(sectionCourante==='hub') chargerHub();
}

/* ---------------- un mot a l'administrateur ---------------- */
/* Plus de bouton « Écrire » : la rubrique a un onglet a elle, et l'ouvrir
   EST le geste. Un repli de plus sous un onglet deja choisi ne cachait plus
   rien d'encombrant -- il ajoutait un clic. */
function msgCible(){
  /* La liste ne propose que les applications du hub de CE compte : on ne
     peut parler que de ce qu'on peut ouvrir. */
  const zone=$('msg-app-zone');
  if(!zone) return;
  const app = $('msg-cible').value==='application';
  zone.style.display = app ? '' : 'none';
  const noms=(hubApps||[]).map(a=>a.name).sort();
  $('msg-app').innerHTML = noms.length
    ? noms.map(n=>`<option value="${esc(n)}">${esc(nomAffiche(hubApps.find(a=>a.name===n)))}</option>`).join('')
    : '<option value="">aucune application accessible</option>';
}

async function msgEnvoyer(){
  const b=$('msg-envoyer'), e=$('msg-err'), ok=$('msg-ok');
  e.style.display='none'; ok.style.display='none';
  const pieces=piecesPretes('form'); if(pieces===null) return;
  const corps={cible:$('msg-cible').value, texte:$('msg-texte').value.trim(),
               app:$('msg-app').value||'', pieces};
  if(!corps.texte && !pieces.length){ e.textContent='Écris ton message.'; e.style.display='block'; return; }
  b.disabled=true; b.textContent='Envoi...';
  try{
    const r=await fetch('/api/messages',{method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(corps)});
    const d=await r.json();
    if(!r.ok){ e.textContent=d.error||'Envoi impossible.'; e.style.display='block'; }
    else{
      $('msg-texte').value=''; viderPieces('form');
      /* On dit ce qui s'est REELLEMENT passe : le message est range dans
         tous les cas, le mail depend d'un serveur d'envoi configure. */
      ok.textContent = d.envoye
        ? 'Message envoyé à ton administrateur. Merci.'
        : "Message enregistré. Aucun serveur d'envoi n'est configuré : ton "
          + 'administrateur le verra dans le panneau.';
      ok.style.display='block';
      chargerSuivi(true);
    }
  }catch(err){ e.textContent='Erreur réseau.'; e.style.display='block'; }
  finally{ b.disabled=false; b.textContent='Envoyer'; }
}

/* ---------------- les pieces jointes ----------------
   Une capture ou une maquette part avec le message. Rien a apprendre : le
   selecteur de fichiers du systeme, un collage (une capture d'ecran), ou un
   glisser sur le message. Chaque fichier se depose aussitot ; le message
   n'emporte que leurs identifiants. */
const PIECES_MAX=6, PIECE_MAX_OCTETS=10*1024*1024;
const piecesBrouillon={form:[], widget:[]};
const SVG_TROMBONE='<svg viewBox="0 0 24 24"><path d="M21 11.5l-8.6 8.6a5 5 0 01-7.1-7.1l8.6-8.6a3.3 3.3 0 014.7 4.7l-8.6 8.6a1.7 1.7 0 01-2.4-2.4l7.9-7.9"/></svg>';
function vignettePiece(p){
  const image=(p.type||'').startsWith('image/');
  /* La vignette vient du serveur une fois le fichier depose : la page
     n'affiche que des images de sa propre origine (CSP). */
  const src=p.id ? '/api/pieces/'+encodeURIComponent(p.id) : '';
  return image && src ? `<img src="${esc(src)}" alt="" loading="lazy">`
    : `<i class="pj-doc">${esc(((p.nom||'').split('.').pop()||'').toUpperCase().slice(0,4))}</i>`;
}
function piecesHtml(cle){
  return piecesBrouillon[cle].map((p,i)=>`<div class="pj${p.etat==='envoi'?' envoi':''}" title="${esc(p.nom)}">${vignettePiece(p)}
    <span title="${esc(p.nom)}">${esc(p.nom)}</span>
    <button type="button" aria-label="Retirer ${esc(p.nom)}" data-agir="pj-retirer" data-cible="${cle}" data-rang="${i}">×</button></div>`).join('');
}
function peindrePieces(cle){
  if(cle==='form') remplir($('pj-form'), piecesHtml('form'));
  else if(sectionCourante==='hub') peindreHub();
}
async function deposerPieces(cle, fichiers){
  for(const f of [...(fichiers||[])]){
    if(piecesBrouillon[cle].length>=PIECES_MAX){ notifier(PIECES_MAX+' pièces jointes au plus.','err'); break; }
    if(f.size>PIECE_MAX_OCTETS){ notifier(f.name+' : 10 Mo au maximum.','err'); continue; }
    const p={nom: f.name || 'capture.png', type: f.type};
    p.etat = 'envoi';
    piecesBrouillon[cle].push(p); peindrePieces(cle);
    try{
      const r=await fetch('/api/pieces',{method:'POST', body:f,
        headers:{'X-Nom-Fichier':encodeURIComponent(p.nom), 'Content-Type':f.type||'application/octet-stream'}});
      if(r.ok) Object.assign(p, await r.json(), {etat:'ok'});
      else{ notifier(await echec(r,'Pièce refusée.'),'err'); piecesBrouillon[cle]=piecesBrouillon[cle].filter(x=>x!==p); }
    }catch(e){ notifier('Envoi de la pièce impossible.','err'); piecesBrouillon[cle]=piecesBrouillon[cle].filter(x=>x!==p); }
    peindrePieces(cle);
  }
}
/* Ce qui part avec le message : null tant qu'un depot n'est pas fini. */
function piecesPretes(cle){
  if(piecesBrouillon[cle].some(p=>p.etat!=='ok')){ notifier('Une pièce est encore en cours d’envoi.','err'); return null; }
  return piecesBrouillon[cle].map(p=>p.id);
}
function viderPieces(cle){
  piecesBrouillon[cle]=[]; peindrePieces(cle);
}
/* Coller une capture, ou glisser des fichiers, sur le texte d'un message. */
document.addEventListener('paste', e=>{
  const cle=e.target && e.target.dataset && e.target.dataset.pieces; if(!cle) return;
  const fichiers=[...(e.clipboardData && e.clipboardData.files || [])];
  if(!fichiers.length) return;
  e.preventDefault(); deposerPieces(cle, fichiers);
});
document.addEventListener('dragover', e=>{
  const t=e.target && e.target.closest && e.target.closest('[data-pieces]'); if(!t) return;
  e.preventDefault(); t.classList.add('depot-survol');
});
document.addEventListener('dragleave', e=>{
  const t=e.target && e.target.closest && e.target.closest('[data-pieces]'); if(t) t.classList.remove('depot-survol');
});
document.addEventListener('drop', e=>{
  const t=e.target && e.target.closest && e.target.closest('[data-pieces]'); if(!t) return;
  e.preventDefault(); t.classList.remove('depot-survol');
  deposerPieces(t.dataset.pieces, e.dataTransfer && e.dataTransfer.files);
});

/* ---------------- le suivi de mes messages ----------------
   Ce que chaque message est devenu, deduit cote serveur de ce que
   l'administrateur en fait dans Messages. Un message qui a avance depuis la
   derniere visite porte une pastille ; ouvrir la liste les eteint. */
let mesMessages=null, mesNouveaux=0, suiviLu=0;
const NOM_ETAPE={envoye:'Envoyé', lu:'Lu', etude:'À l’étude', planifie:'Planifié',
                 en_cours:'En cours', fait:'Réalisé', refuse:'Non retenu'};
const RANG_ETAPE={envoye:0, lu:1, etude:1, planifie:2, en_cours:3, fait:4};
function sujetSuivi(m){
  if(m.cible==='application') return 'Application · '+m.app;
  return m.cible==='idee' ? 'Nouveau projet' : 'Le hub';
}
function dateCourte(iso){
  const d=new Date(typeof iso==='number' ? iso*1000 : iso+'T12:00:00');
  return isNaN(d) ? '' : d.toLocaleDateString('fr-FR',{day:'numeric', month:'long'});
}
async function chargerSuivi(force){
  if(estAdmin) return;
  if(!force && Date.now()-suiviLu<60000 && mesMessages) return;
  suiviLu=Date.now();
  try{ const d=await (await fetch('/api/mes-messages')).json();
       mesMessages=d.messages||[]; mesNouveaux=d.nouveaux||0; }catch(e){ return; }
  peindreSuivi();
  if(sectionCourante==='hub') peindreHub();
}
function etapesSuivi(etape){
  if(etape==='refuse') return '';
  const r=RANG_ETAPE[etape]||0;
  const noms=['Envoyé', etape==='etude' ? 'À l’étude' : 'Lu', 'Planifié', 'En cours', 'Réalisé'];
  return `<ol class="suivi-etapes" aria-label="Avancement">${noms.map((n,i)=>
    `<li class="${i<r ? 'passe' : i===r ? 'ici' : ''}" ${i===r ? 'aria-current="step"' : ''}>${n}</li>`).join('')}</ol>`;
}
function itemSuivi(m){
  const s=m.suivi, detail=[];
  if(s.projet) detail.push(`Projet <b>${esc(s.projet)}</b>`);
  if(s.total) detail.push(`Tâches faites : <b>${s.faites}/${s.total}</b>`);
  if(s.prevu) detail.push(`Prévu pour le <b>${dateCourte(s.prevu)}</b>`);
  if(s.fait_le) detail.push(`Réalisé le <b>${dateCourte(s.fait_le)}</b>`);
  return `<div class="suivi-item ${m.nouveau?'nouveau':''}">
    <div class="suivi-tete"><span class="suivi-sujet">${esc(sujetSuivi(m))}</span>
      <span class="suivi-date">${depuis(m.ts)}</span>
      ${m.nouveau ? '<span class="suivi-nouveau">Du nouveau</span>' : ''}
      <span class="suivi-etat e-${s.etape}">${NOM_ETAPE[s.etape]||''}</span></div>
    ${m.texte ? `<p class="suivi-texte">${esc(m.texte)}</p>` : ''}
    ${(m.pieces||[]).length ? `<div class="pj-liste">${m.pieces.map(p=>`<a class="pj" href="/api/pieces/${encodeURIComponent(p.id)}">${vignettePiece(p)}<span>${esc(p.nom)}</span></a>`).join('')}</div>` : ''}
    ${etapesSuivi(s.etape)}
    ${detail.length ? `<div class="suivi-detail">${detail.map(x=>`<span>${x}</span>`).join('')}</div>` : ''}
    ${s.taches.length ? `<ul class="suivi-taches">${s.taches.slice(0,6).map(t=>
      `<li class="${t.faite?'faite':''}"><i>${t.faite?'✓':'•'}</i>${esc(t.titre)}</li>`).join('')}</ul>` : ''}
  </div>`;
}
function peindreSuivi(){
  majPastilleIcone();
  const b=$('side-count-suivi');
  if(b){ b.textContent=mesNouveaux; b.style.display=mesNouveaux ? '' : 'none'; }
  if(!mesMessages) return;
  remplir($('suivi-liste'), mesMessages.length ? mesMessages.map(itemSuivi).join('')
    : '<p class="hint">Aucun message envoyé pour l’instant.</p>');
}
/* La liste est ouverte : ce qu'elle montre est vu. Les cartes gardent leur
   mise en avant jusqu'a la prochaine visite, la pastille s'eteint. */
async function suiviConsulte(){
  await chargerSuivi(true);
  if(!mesNouveaux) return;
  try{ await fetch('/api/mes-messages/vus',{method:'POST'}); }catch(e){ return; }
  mesNouveaux=0;
  const b=$('side-count-suivi'); if(b) b.style.display='none';
  if(sectionCourante==='hub') peindreHub();
}
function widgetSuivi(taille){
  const liste=mesMessages||[];
  const actifs=liste.filter(m=>!['fait','refuse'].includes(m.suivi.etape));
  const titre='<div class="bw-titre">Mes messages</div>';
  if(!liste.length) return titre+'<p class="bw-vide">Aucun message envoyé.</p>';
  const n=mesNouveaux;
  if(taille==='petit') return titre+`<div><div class="bento-chiffre">${n||actifs.length}</div>
    <p>${n ? (n>1 ? 'mises à jour' : 'mise à jour') : 'en cours'}</p></div>`;
  return titre+`<div class="bw-liste">${liste.slice(0,3).map(m=>`<div><span>${esc(sujetSuivi(m))}</span>
      <span class="suivi-etat e-${m.suivi.etape}">${NOM_ETAPE[m.suivi.etape]||''}</span></div>`).join('')}</div>
    <button type="button" class="bento-bouton" onclick="allerA('message')">${n ? n+' nouveauté'+(n>1?'s':'') : 'Tout voir'} ${SVG_FLECHE}</button>`;
}

/* ---------------- le hub, en grille et seulement en grille ----------------
   Il offrait deux tailles de tuile, retenues par navigateur. C'etait une
   decision de plus a prendre pour une page ou l'on ne vient que pour ouvrir
   une application : la grille d'icones montre d'un coup d'oeil ce qui est
   la, et l'etat d'une application se lit dans « Applications », pas ici. */

let hubMasquees=[], hubServices=[], hubServicesMasques=[];

/* Au-dela de ce nombre d'applications, le reglage d'affichage se replie
   derriere une ligne qu'on deroule. Huit : c'est a peu pres ce qui tient
   sous les yeux sans faire defiler la page. */
const MASQUAGE_SEUIL_DEROULANT = 12;

/* Une seule bascule pour les deux sens : cocher retire l'application du hub,
   decocher l'y remet. Le serveur ne connait que deux verbes sur la meme
   adresse, POST pour masquer et DELETE pour afficher. */
async function basculerMasquage(nom, masquer){
  try{
    const r=await fetch('/api/mes-apps/'+encodeURIComponent(nom)+'/masquer',
      {method: masquer ? 'POST' : 'DELETE'});
    if(!r.ok) throw new Error();
    await chargerHub();
    if(masquer) notifier('« '+nom+' » masquée de ton hub.', 'ok',
                         {libelle:'Annuler', faire:()=>basculerMasquage(nom, false)});
    else notifier('« '+nom+' » est de retour dans ton hub.');
  }catch(e){
    notifier("Impossible de changer l'affichage de cette application.",'err');
    /* La case a deja bouge sous le doigt : on redessine depuis le serveur
       plutot que de laisser un reglage affiche qui n'existe pas. Le HTML
       retenu par remplir() est oublie : identique a celui du serveur, il ne
       serait pas repose, et la case garderait l'etat refuse. */
    _dernierHtml.delete($('masquees-liste'));
    chargerHub();
  }
}

/* TOUTES les applications du compte, masquees comprises : c'est la liste
   entiere qui se coche et se decoche. N'afficher que les masquees obligeait
   a en retirer une depuis le hub avant de pouvoir la retrouver ici. */
function peindreMasquees(){
  const zone=$('masquees-liste');
  if(!zone) return;
  const cachees = new Set((hubMasquees||[]).concat(hubServicesMasques||[])
    .map(a=>a.name));
  /* Les services de la stack figurent ici comme les applications : c'est la
     liste de ce que MON hub affiche, et Dagster y occupe une tuile comme les
     autres. Ils restent hors de hubApps partout ailleurs -- le choix « de
     quelle application veux-tu parler » n'a toujours rien a en faire. */
  const toutes = (hubApps||[]).concat(hubMasquees||[])
    .concat(hubServices||[]).concat(hubServicesMasques||[])
    .slice().sort((a,b)=>nomAffiche(a).localeCompare(nomAffiche(b),'fr'));
  if(!toutes.length){
    remplir(zone, '<div class="hint">Aucune application à ranger pour le moment.</div>');
    return;
  }
  /* RETENUE VEUT DIRE AFFICHEE, et pas l'inverse. La case cochee signifiait
     « masquee » : on selectionnait ce qu'on voulait faire disparaitre, et la
     ligne retenue etait celle qu'on ne verrait plus. En choisissant ce qu'on
     GARDE, la liste se lit comme le hub qu'elle decrit. */
  /* UNE LIGNE, UN NOM. Chaque ligne portait l'icone, le nom, la description
     complete et le mot « Affichée » ou « Masquée » : quatre informations pour
     un reglage qui en a une, et une liste deux fois plus haute que la carte
     qui la contient. La description est deja sur la tuile du hub, et l'etat
     est dans la case -- cochee veut dire affichee, c'est tout le propos. Il
     ne reste que ce qu'on vient chercher : le nom, et la case. */
  const lignes = toutes.map(a=>{
    const cachee = cachees.has(a.name);
    return `<label class="choix-ligne masquee-ligne">
    <input type="checkbox"${cachee?'':' checked'}
      data-changer="app-masquer" data-cible="${esc(a.name)}">
    <img src="${icone(a.name)}" alt="">
    <b class="masquee-txt">${esc(nomAffiche(a))}</b>
  </label>`;}).join('');

  /* AU-DELA DE HUIT, LA LISTE SE DEROULE. Quinze applications faisaient de
     ce reglage la moitie de la page des parametres, et repoussaient tout le
     reste hors de l'ecran -- pour une liste qu'on ouvre trois fois par an.
     Repliee, elle tient en une ligne qui dit l'essentiel : combien sont
     masquees. En dessous de huit, dérouler n'economise rien et ajoute un
     clic. */
  if(toutes.length <= MASQUAGE_SEUIL_DEROULANT){
    remplir(zone, lignes);
    return;
  }
  const nb = cachees.size;
  const resume = nb
    ? `${nb} masquée${nb>1?'s':''} sur ${toutes.length} applications`
    : `${toutes.length} applications, aucune masquée`;
  /* L'etat ouvert/ferme se garde d'un rafraichissement a l'autre : la liste
     est redessinee a chaque changement de case, et elle se refermerait sous
     le doigt a chaque clic. */
  const ouverte = zone.querySelector('details') ? zone.querySelector('details').open : false;
  remplir(zone, `<details class="masquee-repli"${ouverte?' open':''}>
    <summary><span>${esc(resume)}</span>
      <svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg></summary>
    <div class="masquee-corps">${lignes}</div></details>`);
}

async function chargerHub(){
  try{
    const d=await (await fetch('/api/mes-apps')).json();
    hubApps=d.apps||[];
    /* Les services de la stack -- Dagster, Mailpit -- vivent a part :
       ils se peignent dans la meme grille, mais ils ne sont pas des
       applications declarees. Les meler a hubApps les ferait entrer dans la
       liste de ce qu'on masque et dans le choix « de quelle application
       veux-tu parler », ou ils n'ont rien a faire. */
    hubServices=d.services||[];
    hubServicesMasques=d.services_masques||[];
    hubMasquees=d.masquees||[];
    peindreMasquees();
    chargerSuivi();
    msgCible();
    /* Les categories arrivent avec les projets : une seule requete, et le
       hub ne peut pas afficher un rangement decale d'un tour. */
    if(Array.isArray(d.categories)) categories=d.categories;
    hubSauvegardes=d.sauvegardes||null;
  }catch(e){ return; }
  peindreHub();
  chargerPouls();
}
/* NE REPEINDRE QUE CE QUI CHANGE. Le hub se recharge toutes les dix secondes ;
   remplacer le HTML a chaque tour, meme identique, recreait toutes les tuiles
   et leurs images : l'ecran clignotait. On compare au dernier HTML pose.
   ET QUAND IL CHANGE, LES IMAGES RESTENT. Une pastille, un etat, le « il y a
   3 min » d'un widget suffisent a changer le HTML du hub entier : chaque
   <img> etait recree, et une image neuve, meme en cache, se decode a part --
   une image vide le temps d'un affichage, sur toutes les tuiles a la fois.
   Les images deja chargees sont donc remises a la place des neuves de meme
   adresse : le navigateur n'a rien a redessiner.
   ESSAYE ET ECARTE : un cache plus long de /api/icon. Il evitait la requete,
   pas le decodage d'une image neuve : le clignotement restait. */
const _dernierHtml = new WeakMap();
function remplir(el, html){
  if(!el || _dernierHtml.get(el) === html) return;
  _dernierHtml.set(el, html);
  const images = imagesChargees(el);
  el.innerHTML = html;
  reposerImages(el, images);
}
/* Les images affichees de el, par adresse (une meme icone peut y etre deux
   fois : sur sa tuile et dans le widget « a la une »). */
function imagesChargees(el){
  const parSource = new Map();
  el.querySelectorAll('img[src]').forEach(i=>{
    if(!i.complete || !i.naturalWidth) return;
    const s = i.getAttribute('src');
    if(!parSource.has(s)) parSource.set(s, []);
    parSource.get(s).push(i);
  });
  return parSource;
}
/* Chaque image neuve cede la place a l'ancienne de meme adresse, qui prend
   ses attributs -- sauf src, inchange : le reposer relancerait le
   chargement. */
function reposerImages(el, parSource){
  if(!parSource.size) return;
  el.querySelectorAll('img[src]').forEach(neuve=>{
    const vieille = (parSource.get(neuve.getAttribute('src')) || []).shift();
    if(!vieille) return;
    [...vieille.attributes].forEach(({name})=>{ if(!neuve.hasAttribute(name)) vieille.removeAttribute(name); });
    [...neuve.attributes].forEach(({name, value})=>{
      if(name!=='src' && vieille.getAttribute(name)!==value) vieille.setAttribute(name, value);
    });
    neuve.replaceWith(vieille);
  });
}
/* Une image seule (la fiche, la presentation) : son adresse ne se repose que
   si elle change, pour la meme raison. */
function poserSource(img, src){
  if(img && img.getAttribute('src')!==src) img.src = src;
}

function peindreHub(){
  const filtre = ($('hub-filtre').value||'').trim().toLowerCase();
  const correspond = a => !filtre
    || (nomAffiche(a)+' '+(a.description||'')).toLowerCase().includes(filtre);

  /* Applications et services dans la meme grille : on vient au hub pour
     OUVRIR quelque chose, et ce qui l'ouvre ne change rien a l'endroit ou
     l'oeil le cherche. Dagster, Mailpit et Messages ont la meme tuile
     qu'une application. Dans l'ordre que le compte a compose (mode
     Modifier) ; les applications jamais rangees suivent, par nom. */
  const toutes = hubApps.concat(hubServices);
  const liste=toutes.filter(correspond)
    .sort((a,b)=>rangDansLeHub(a.name)-rangDansLeHub(b.name) || a.name.localeCompare(b.name,'fr'));
  /* Une liste silencieusement plus courte inquiete plus qu'elle ne
     simplifie : le hub dit ce qu'il cache, et ou le retrouver. */
  const nbCaches = (hubMasquees||[]).length;
  $('hub-masquees').style.display = nbCaches ? 'block' : 'none';
  if(nbCaches){
    remplir($('hub-masquees'), nbCaches + (nbCaches>1
      ? ' applications masquées — ' : ' application masquée — ')
      + '<a href="#" onclick="event.preventDefault();allerA(\'parametres\');'
      + 'paramOnglet(\'compte\')">à décocher dans tes paramètres</a> pour '
      + (nbCaches>1 ? 'les' : 'la') + ' revoir.');
  }
  peindreBento(liste, filtre);
}

/* Les messages a lire, que la tuile de l'application Messages porte (le
   serveur ne la remplit que pour l'administrateur). */
const nonLuesMessages = () => (hubApps.find(a=>a.name==='messages')||{}).non_lues||0;
/* Le nombre de messages non lus, sur la tuile « Messages ». */
const pastille = a => a.non_lues
  ? `<span class="hub-pastille" title="${a.non_lues} nouvelle${a.non_lues>1?'s':''} demande${a.non_lues>1?'s':''}">${a.non_lues}</span>` : '';

/* STYLE 4 : LA MOSAIQUE. Sur un ecran large, elle DEFILE A L'HORIZONTALE et
   jamais a la verticale : trois rangees qui tiennent dans la hauteur de la
   fenetre, des colonnes qui s'ajoutent sur la droite. Sur un telephone, elle
   redevient une colonne qu'on fait defiler vers le bas.

   ELLE SE COMPOSE, comme l'ecran d'accueil d'un telephone ou les widgets d'un
   Mac : « Modifier » fait trembler les tuiles, qu'on glisse ou qu'on deplace
   aux fleches ; « + » ouvre la galerie des widgets ; chaque widget change de
   taille sur place. La disposition est gardee par compte, cote serveur. */
let hubDisposition=null, hubCatalogue=[], hubEdition=false, hubMessageBrouillon='';
async function chargerDisposition(){
  try{
    const d=await (await fetch('/api/hub/disposition')).json();
    hubDisposition=d.elements||[]; hubCatalogue=d.catalogue||[];
  }catch(e){ if(hubDisposition===null) hubDisposition=[]; }
  if(hubApps.length || hubServices.length) peindreHub();
}
const cleElement = e => e.t==='a' ? 'a:'+e.id : 'w:'+e.id+':'+(e.app||'');
/* La disposition, completee des applications qu'on n'a pas encore rangees :
   elles viennent a la suite, par ordre alphabetique. */
function elementsDuHub(){
  const toutes=hubApps.concat(hubServices);
  const rangees=new Set((hubDisposition||[]).filter(e=>e.t==='a').map(e=>e.id));
  return (hubDisposition||[]).filter(e=>e.t!=='a' || toutes.some(a=>a.name===e.id))
    .concat(toutes.filter(a=>!rangees.has(a.name))
      .sort((x,y)=>nomAffiche(x).localeCompare(nomAffiche(y),'fr')).map(a=>({t:'a', id:a.name})));
}
/* Le rang d'une application dans la disposition : la recherche garde
   l'ordre que le compte a compose. */
function rangDansLeHub(nom){ const i=elementsDuHub().findIndex(e=>e.t==='a' && e.id===nom); return i<0 ? 1e6 : i; }
async function enregistrerDisposition(elements){
  hubDisposition=elements; peindreHub();
  const r=await fetch('/api/hub/disposition',{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({elements})});
  if(!r.ok){ notifier(await echec(r,'Disposition non enregistrée.'),'err'); chargerDisposition(); }
}

/* L'application « a la une » : la derniere ouverte par ce compte (le serveur
   le sait, d'un appareil a l'autre, et ne note qu'une application qui
   fonctionne), sinon la premiere en ligne.
   ESSAYE ET ECARTE : retenir aussi le dernier appui dans ce navigateur
   (localStorage). Il notait l'appui, pas l'ouverture : une application en
   panne passait « a la une ». */
function appALaUne(apps){
  const vues = apps.filter(a=>a.ouverte_le).sort((x,y)=>y.ouverte_le-x.ouverte_le);
  if(vues.length) return [vues[0], true];
  return [apps.find(a=>a.running) || apps[0] || null, false];
}
function depuis(ts){
  const s=Math.max(0, Date.now()/1000-(ts||0));
  if(s<60) return "à l'instant";
  if(s<3600) return 'il y a '+Math.floor(s/60)+' min';
  if(s<86400) return 'il y a '+Math.floor(s/3600)+' h';
  const j=Math.floor(s/86400); return j===1 ? 'hier' : 'il y a '+j+' jours';
}
/* « Nouvelle version » : l'application a ete deployee, rebuildee ou a recu
   un commit depuis la derniere fois que CE compte l'a ouverte. */
/* « Nouveau » : une application a laquelle ce compte a acces, et qu'il n'a
   encore jamais ouverte -- elle passe avant « Nouvelle version ». */
const badgeMaj = a => a.nouvelle
  ? '<span class="badge-maj" title="Tu n’as pas encore ouvert cette application">Nouveau</span>'
  : a.nouvelle_version
  ? '<span class="badge-maj" title="Nouvelle version depuis ta dernière ouverture">Nouvelle version</span>' : '';
/* La tuile se detache (bordure, point) pour l'un comme pour l'autre. */
const aDuNouveau = a => !!(a.nouvelle || a.nouvelle_version);
const SVG_FLECHE='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';
