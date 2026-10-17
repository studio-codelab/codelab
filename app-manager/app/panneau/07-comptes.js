/* CodeLab -- panneau/07-comptes.js : Les comptes : liste, confirmation et notifications, creation, fiche, invitations, Face ID propose, serveur d'envoi requis.
   Une partie du script du panneau, assemblee par app.py (PANNEAU_MODULES)
   dans l'ordre des numeros : un seul script, une seule portee. */
/* ---------------- comptes utilisateurs ---------------- */
function usErreur(texte){
  const e=$('us-err');
  if(!texte){ e.style.display='none'; return; }
  e.textContent=texte; e.style.display='block';
}

/* La liste complete arrive du serveur, le filtrage se fait ici. C'est le bon
   compromis a cette echelle -- quelques dizaines de comptes sur une machine
   de salon -- et c'est deja ce que fait la recherche d'applications.

   PAS de pagination : elle etait ici, elle n'y servait a rien. Une liste de
   comptes tient a l'ecran, et les deux filtres au-dessus suffisent a s'y
   retrouver. C'est le JOURNAL des connexions qui est long -- il grandit a
   chaque visite -- et c'est lui qui a recu le reglage. */
let usTous=[];

function usFiltrer(){ usPeindreListe(); }

function usRetenus(){
  const q=($('us-q').value||'').trim().toLowerCase();
  /* Plus de filtre par application ici : il doublait l'onglet Acces de la
     fiche d'application, ou la question se REGLE au lieu de se poser. */
  return usTous.filter(c=>{
    if(!q) return true;
    return (c.nom+' '+(c.email||'')).toLowerCase().includes(q);
  });
}

function usLigne(c){
  /* Ce qui empeche quelqu'un d'entrer passe en premier, et en rouge : un
     compte qui n'a jamais confirme son adresse ne se connecte pas. */
  const badges=[];
  /* Suspendu passe avant tout le reste : c'est la seule chose a savoir sur
     ce compte tant qu'il l'est. */
  if(c.suspendu) badges.push('<span class="pill err"><i></i>Suspendu</span>');
  if(c.suppression) badges.push('<span class="pill warn"><i></i>Suppression le '
    + new Date(c.suppression*1000).toLocaleDateString('fr-FR',{day:'numeric',month:'long'}) + '</span>');
  if(c.attente_email) badges.push('<span class="pill err"><i></i>Inscription a confirmer</span>');
  /* Une adresse verifiee suffit desormais : le code arrive par mail. Le
     second facteur n'est en attente que sans adresse, sans application et
     sans cle d'acces. */
  if(!c.totp && !c.email_verifie && !c.passkeys)
    badges.push('<span class="pill off"><i></i>2FA en attente</span>');
  if(c.passkeys) badges.push(`<span class="pill on"><i></i>${c.passkeys} clé${c.passkeys>1?'s':''}</span>`);
  const projets = c.projets.length
    ? c.projets.length + ' projet' + (c.projets.length>1?'s':'')
    : 'aucun projet';
  const adresse = c.email
    ? esc(c.email) + (c.email_verifie ? ' (vérifiée)' : ' (non vérifiée)')
    : 'sans adresse';
  /* La ligne entiere ouvre le compte : un bouton « Modifier » repete sur
     chaque ligne n'apprenait rien de plus que le chevron. */
  return `<div class="us-ligne${c.suspendu?' us-suspendu':''}" data-agir="compte-ouvrir"
    data-cible="${esc(c.nom)}" role="button" tabindex="0" title="Ouvrir le compte">
    <div class="us-rond">${esc((c.nom||'?').slice(0,1))}</div>
    <div class="us-corps">
      <div class="us-nom"><b>${esc(c.nom)}</b>${badges.join('')}</div>
      <div class="us-meta">${adresse} — ${projets}<span data-activite="${esc(c.nom)}"></span></div>
    </div>
    <span class="us-chevron"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
      stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg></span>
  </div>`;
}

function usPeindreListe(){
  const zone=$('us-liste'), barre=$('us-pagination');
  if(!usTous.length){
    zone.innerHTML='<div class="vide"><b>Aucun compte</b>'
      +'Toi seul peux ouvrir les projets privés. Crée un compte pour partager '
      +'un projet avec quelqu\'un sans lui donner l\'administration.</div>';
    barre.style.display='none';
    return;
  }
  const retenus=usRetenus();
  if(!retenus.length){
    /* Dire ce qui a ete cherche : "aucun resultat" tout court laisse penser
       que la liste est vide, alors que c'est le filtre qui l'a videe. */
    zone.innerHTML='<div class="vide"><b>Aucun compte ne correspond</b>'
      +'Ni au texte saisi, ni a l\'application choisie. Efface les filtres '
      +'pour retrouver les '+usTous.length+' comptes.</div>';
    barre.style.display='none';
    return;
  }
  zone.innerHTML=retenus.map(usLigne).join('');
  peindreComptesActivite();

  /* Le compte ne s'affiche que si un filtre agit : il dit alors combien de
     comptes ont ete ecartes. Sans filtre, la liste se compte a l'oeil. */
  const filtre = retenus.length !== usTous.length;
  barre.style.display = filtre ? 'flex' : 'none';
  $('us-compte').textContent =
    `${retenus.length} compte${retenus.length>1?'s':''} sur ${usTous.length}`;
}

async function chargerUtilisateurs(){
  let d;
  try{ d=await (await fetch('/api/utilisateurs')).json(); }catch(e){ return; }
  const comptes=d.utilisateurs||[];
  $('side-count-users').textContent=comptes.length;
  usTous=comptes;
  usComptes=comptes;

  usPeindreListe();
  /* La fiche ouverte suit : les projets ont pu changer pendant qu'elle
     l'etait, et les cases a cocher doivent rester justes. */
  if(usCourant && $('ov-user').classList.contains('show')) usPeindreFiche();
}

/* ---------------- confirmation et notifications ---------------- */
let confirmerResoudre=null;
/* Une promesse plutot qu'un rappel : le code appelant se lit comme avec
   confirm(), en une ligne, sans imbriquer une fonction. */
function demander(titre, texte, libelle, danger){
  $('cf-titre').textContent=titre;
  $('cf-texte').textContent=texte;
  const ok=$('cf-ok');
  ok.textContent=libelle||'Confirmer';
  ok.className='btn ' + (danger ? 'btn-default danger' : 'btn-primary');
  $('ov-confirm').classList.add('show');
  setTimeout(()=>ok.focus(), 40);
  return new Promise(r=>{ confirmerResoudre=r; });
}
function confirmerReponse(reponse){
  hide('ov-confirm');
  const r=confirmerResoudre; confirmerResoudre=null;
  if(r) r(reponse);
}

/* UNE ERREUR RESTE jusqu'a ce qu'on la ferme : elle disparaissait au bout
   de 7 s, souvent avant d'avoir ete lue -- et avec elle la reference a
   transmettre. Un succes, lui, s'en va seul.
   action : {libelle, faire} -- un bouton dans le message (« Annuler »),
   le temps de changer d'avis (8 s). */
function notifier(texte, genre, action){
  const err = genre==='err';
  const n=document.createElement('div');
  n.className='toast ' + (genre||'ok');
  n.setAttribute('role', err ? 'alert' : 'status');
  const icone = err
    ? '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5v.01"/></svg>'
    : '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>';
  n.innerHTML = icone + '<div class="toast-texte">' + esc(texte) + '</div>'
    + (action ? '<button type="button" class="toast-action">' + esc(action.libelle) + '</button>' : '')
    + (err ? '<button type="button" class="toast-fermer" aria-label="Fermer le message">×</button>' : '');
  if(action) n.querySelector('.toast-action').onclick=()=>{ n.remove(); action.faire(); };
  if(err) n.querySelector('.toast-fermer').onclick=()=>n.remove();
  /* Trois erreurs a l'ecran au plus : la plus ancienne laisse sa place. */
  const erreurs=$('toasts').querySelectorAll('.toast.err');
  if(err && erreurs.length>=3) erreurs[0].remove();
  $('toasts').appendChild(n);
  if(!err) setTimeout(()=>n.remove(), action ? 8000 : 4000);
}


function notifierMessage(texte){
  const precedent=$('toast-messages');
  if(precedent) precedent.remove();
  const n=document.createElement('div');
  n.id='toast-messages';
  n.className='toast toast-message ok';
  n.innerHTML='<svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
    +'<div style="flex:1"><b>'+esc(texte)+'</b><div class="hint">Clique pour ouvrir Messages.</div></div>'
    +'<button type="button">Voir</button>';
  n.onclick=()=>{ location.href='/messages'; };
  $('toasts').appendChild(n);
}

/* ---------------- creation d'un compte ---------------- */
function usFormulaire(ouvert){
  $('us-nouveau').style.display = ouvert ? 'block' : 'none';
  usErreur('');
  if(ouvert) setTimeout(()=>$('us-nom').focus(), 40);
}

/* ---------------- fiche d'un compte ---------------- */
let usComptes=[], usCourant='';

async function usOuvrir(nom){
  usCourant=nom;
  usPeindreFiche();
  $('ov-user').classList.add('show');
  /* « Projets autorises » se peint a partir de la liste des applications.
     Si elle n'est pas encore arrivee -- fiche ouverte dans la seconde qui
     suit le chargement de la page, ou premier passage par Utilisateurs --
     la rubrique restait vide, sans rien dire. On va la chercher, puis on
     repeint. */
  if(!apps.length){
    await refresh();
    if(usCourant===nom) usPeindreFiche();
  }
}

function usCompte(){ return usComptes.find(c=>c.nom===usCourant); }

function usPeindreFiche(){
  const c=usCompte();
  if(!c){ hide('ov-user'); return; }
  $('us-f-titre').textContent=c.nom;
  $('us-f-sous').textContent = c.cree
    ? 'Compte créé ' + ilYA(c.cree) + '.'
    : 'Compte utilisateur.';
  $('us-f-err').style.display='none';
  $('us-f-mail').value=c.email||'';
  $('us-f-mdp').value='';
  $('us-f-mail-etat').textContent = !c.email
    ? "Aucune adresse. La personne peut en déclarer une elle-même depuis ses paramètres."
    : (c.attente_email
      ? "Adresse jamais confirmée : ce compte ne peut pas se connecter tant qu'un code envoyé dessus n'est pas revenu."
      : (c.email_verifie
        ? "Adresse vérifiée par un code reçu dessus."
        : "Adresse non vérifiée : la personne la confirmera depuis ses paramètres."));

  /* Une LIGNE par projet declare, avec son icone, sa description et sa
     visibilite : la liste complete rend lisible ce qui est autorise ET ce
     qui ne l'est pas. Une rangee de cases nues ne disait ni de quelle
     application il s'agissait, ni lesquelles etaient publiques -- donc
     lesquelles cocher ne changeait rien. */
  const declares = apps.slice().sort((a,b)=>nomAffiche(a).localeCompare(nomAffiche(b),'fr'));
  $('us-f-projets').innerHTML = declares.length ? declares.map(a=>{
    /* Une application reservee a l'administrateur (Diagnostic...) reste
       dans la liste, pour qu'on sache pourquoi elle manque, mais sa case
       est grisee : le serveur refuserait de toute facon de l'accorder. */
    const reservee = !!a.par_defaut;
    const coche = !reservee && c.projets.includes(a.name) ? ' checked' : '';
    const prive = a.visibility==='privee';
    return `<label class="choix-ligne proj-ligne${prive?'':' publique'}${reservee?' reservee':''}">
      <input type="checkbox" value="${esc(a.name)}"${coche}${reservee?' disabled':''}>
      <img src="${icone(a.name)}" alt="">
      <div class="proj-txt"><b>${esc(nomAffiche(a))}</b>
        ${a.description?`<span>${esc(a.description)}</span>`:''}</div>
      ${reservee ? '<span class="pill priv"><i></i>Réservée à l\'administrateur</span>' : badgeVis(a)}</label>`;
  }).join('') : '<div class="hint">Aucun projet déclaré.</div>';

  const morceaux=[];
  const codes=[];
  if(c.totp) codes.push("son application d'authentification");
  if(c.email && c.email_verifie) codes.push('mail');
  morceaux.push(codes.length
    ? 'Code de connexion par ' + codes.join(', ') + '.'
    : "Second facteur en attente : il sera enregistré à la première connexion.");
  morceaux.push(c.passkeys
    ? `${c.passkeys} clé${c.passkeys>1?'s':''} d'accès enregistrée${c.passkeys>1?'s':''}.`
    : "Aucune clé d'accès.");
  morceaux.push("Tu ne connais jamais ces secrets : en cas d'appareil perdu, remets-les a zero "
    + "et la personne en enregistrera de nouveaux.");
  $('us-f-facteurs').textContent = morceaux.join(' ');
  $('us-f-totp').disabled = !c.totp;
  $('us-f-cles').disabled = !c.passkeys;
  usChargerAppareils(c.nom);

  /* L'etat de l'acces, dit en une phrase, et le bouton qui le change. */
  $('us-f-suspendu-etat').innerHTML = c.suspendu
    ? "<b>Accès suspendu.</b> Ce compte n'ouvre plus rien et ne peut plus se connecter."
    : "Accès ouvert : ce compte peut se connecter et ouvrir ses projets.";
  $('us-f-suspendre').textContent = c.suspendu ? 'Réactiver' : 'Suspendre';
  $('us-f-suspendre').classList.toggle('danger', !c.suspendu);
}

function usFicheErreur(texte){
  const e=$('us-f-err');
  e.textContent=texte||'';
  e.style.display = texte ? 'block' : 'none';
}

async function usEcrire(corps, message){
  usFicheErreur('');
  const r=await fetch('/api/utilisateurs/'+encodeURIComponent(usCourant),{method:'PUT',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(corps)});
  const d=await r.json();
  if(!r.ok){ usFicheErreur(d.error||'Modification impossible.'); return false; }
  await chargerUtilisateurs();
  if(message) usFicheErreur('');
  return true;
}

async function usEnregistrer(){
  const mdp=$('us-f-mdp').value;
  if(mdp && mdp.length<12) return usFicheErreur('Mot de passe : 12 caractères au minimum.');
  const projets=[...document.querySelectorAll('#us-f-projets input:checked')].map(i=>i.value);
  const corps={email:$('us-f-mail').value.trim(), projets};
  if(mdp) corps.mot_de_passe=mdp;
  $('us-f-save').disabled=true;
  try{
    if(await usEcrire(corps)) hide('ov-user');
  } finally { $('us-f-save').disabled=false; }
}

async function usCreer(){
  usErreur('');
  const nom=$('us-nom').value, mdp=$('us-mdp').value, email=$('us-mail').value.trim();
  const r=await fetch('/api/utilisateurs',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({nom, mot_de_passe:mdp, email, projets:[]})});
  const d=await r.json();
  if(!r.ok){ usErreur(d.error||'Création impossible.'); return; }
  $('us-nom').value=''; $('us-mdp').value=''; $('us-mail').value='';
  usFormulaire(false);
  await chargerUtilisateurs();
  notifier('Compte « ' + d.nom + ' » créé. Ouvre-le pour lui autoriser des projets.');
}

/* ---------------- invitations ---------------- */
function invFormulaire(ouvert){
  $('inv-nouveau').style.display = ouvert ? 'block' : 'none';
  $('inv-err').style.display='none';
  $('inv-resultat').style.display='none';
  if(ouvert){ usFormulaire(false); setTimeout(()=>$('inv-mail').focus(), 40); }
}

/* Le lien est TOUJOURS montre a l'administrateur : sans serveur d'envoi (ou
   Mailpit en mode test), le mail est garde sans partir, et c'est a lui de le
   transmettre. Il ne se relit plus ensuite -- seule son empreinte est gardee. */
function invMontrerResultat(d, adresse){
  const z=$('inv-resultat');
  const etat = !d.envoye
    ? `<b>Le mail n'est pas parti</b> (${esc(d.detail||'')}). Transmets ce lien toi-même :`
    : (d.relaye
      ? `Invitation envoyée à <b>${esc(adresse)}</b>. Le lien, si besoin :`
      : `Invitation remise à Mailpit, mais <b>aucun serveur d'envoi n'est réglé</b> : elle y est `
        + `gardée sans partir. Transmets ce lien toi-même :`);
  z.innerHTML = `<div class="hint">${etat}</div>
    <div class="ligne-champ" style="margin-top:6px">
      <input readonly value="${esc(d.lien)}" style="flex:1" onclick="this.select()">
      <button class="btn btn-default btn-sm" data-agir="invitation-copier" data-lien="${esc(d.lien)}">Copier</button>
    </div>`;
  z.style.display='block';
}

async function invEnvoyer(){
  const e=$('inv-err'); e.style.display='none';
  const adresse=$('inv-mail').value.trim();
  if(!adresse){ e.textContent='Saisis une adresse mail.'; e.style.display='block'; return; }
  $('inv-envoyer').disabled=true;
  try{
    const r=await fetch('/api/invitations',{method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:adresse})});
    const d=await r.json();
    if(!r.ok){ e.textContent=d.error||'Invitation impossible.'; e.style.display='block'; return; }
    $('inv-mail').value='';
    invMontrerResultat(d, adresse);
    chargerInvitations();
  }finally{ $('inv-envoyer').disabled=false; }
}

async function chargerInvitations(){
  let d;
  try{ d=await (await fetch('/api/invitations')).json(); }catch(e){ return; }
  const liste=d.invitations||[];
  $('inv-liste').innerHTML = liste.length
    ? '<div class="mb-titre">Invitations en attente</div>' + liste.map(i=>`<div class="us-ligne">
        <div class="us-rond">@</div>
        <div class="us-corps">
          <div class="us-nom"><b>${esc(i.email)}</b><span class="pill off"><i></i>Invitation envoyée</span></div>
          <div class="us-meta">Envoyée ${esc(ilYA(i.cree))} — le lien expire dans ${
            Math.max(1, Math.round((i.expire - Date.now()/1000)/86400))} j</div>
        </div>
        <button class="btn btn-default btn-sm" data-agir="invitation-renvoyer" data-cible="${esc(i.id)}">Renvoyer</button>
        <button class="btn btn-default btn-sm" data-agir="invitation-annuler" data-cible="${esc(i.id)}">Annuler</button>
      </div>`).join('')
    : '';
}

async function invRenvoyer(id){
  const r=await fetch('/api/invitations/'+encodeURIComponent(id)+'/renvoyer',{method:'POST'});
  const d=await r.json();
  if(!r.ok){ notifier(d.error||'Renvoi impossible.', 'err'); return; }
  invFormulaire(true);
  invMontrerResultat(d, '');
  if(d.envoye) notifier('Nouveau lien envoyé. L\'ancien ne vaut plus rien.');
  chargerInvitations();
}

async function invAnnuler(id){
  if(!await demander("Annuler l'invitation ?",
    "Le lien envoyé ne permettra plus de créer le compte.", 'Annuler l\'invitation', true)) return;
  const r=await fetch('/api/invitations/'+encodeURIComponent(id),{method:'DELETE'});
  if(!r.ok){ const d=await r.json(); notifier(d.error||'Annulation impossible.', 'err'); return; }
  chargerInvitations();
}

function copierLien(lien, message='Lien copié.'){
  navigator.clipboard?.writeText(lien).then(()=>notifier(message))
    .catch(()=>notifier('Copie impossible : sélectionne le lien à la main.', 'err'));
}

/* ---------------- proposer Face ID ---------------- */
/* Juste apres l'arrivee (invitation acceptee, ou connexion par mot de passe),
   si l'appareil sait verifier la personne et qu'aucune cle n'existe encore.
   « Plus tard » est retenu sur cet appareil : on ne repose pas la question a
   chaque page. */
function nomDuGeste(){
  const ua=navigator.userAgent||'';
  if(/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints>1)) return 'Face ID';
  if(/Macintosh/.test(ua)) return 'Touch ID';
  if(/Android/.test(ua)) return 'ton empreinte';
  if(/Windows/.test(ua)) return 'Windows Hello';
  return 'une clé d\'accès';
}
/* ---------------- serveur d'envoi requis ---------------- */
function majSmtpRequis(ok){
  SMTP_OK = ok;
  $('us-smtp-requis').style.display = ok ? 'none' : 'block';
  $('inv-ouvrir').disabled = !ok;
  if(!ok){ invFormulaire(false); usFormulaire(false); }
}
function smtpPlusTard(){
  try{ sessionStorage.setItem('codelab-smtp-plus-tard', '1'); }catch(e){}
  hide('ov-smtp');
}
function smtpRegler(){
  hide('ov-smtp');
  allerA('parametres');
  paramOnglet('email');
}
/* A la connexion de l'administrateur, tant que rien n'est regle. Une fois
   par session : « Plus tard » ne doit pas revenir a chaque page. */
function proposerSmtp(){
  if(!estAdmin || SMTP_OK) return false;
  try{ if(sessionStorage.getItem('codelab-smtp-plus-tard')) return false; }catch(e){}
  $('ov-smtp').classList.add('show');
  return true;
}

async function proposerFaceId(){
  const bienvenue = new URLSearchParams(location.search).has('bienvenue');
  if(bienvenue) history.replaceState(null, '', location.pathname);
  try{ if(!bienvenue && localStorage.getItem('codelab-faceid-plus-tard')) return; }catch(e){}
  if(!window.PublicKeyCredential || !PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable) return;
  try{
    if(!await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()) return;
    const etat=await (await fetch('/api/passkeys/etat')).json();
    if(!etat.possible) return;
    const liste=await (await fetch('/api/mon-compte/passkeys')).json();
    if((liste.passkeys||[]).length) return;
  }catch(e){ return; }
  const geste=nomDuGeste();
  $('fid-titre').textContent = (bienvenue ? 'Bienvenue ! ' : '') + 'Activer ' + geste + ' ?';
  $('fid-texte').textContent = 'La prochaine fois, connecte-toi avec ' + geste
    + ' : ni mot de passe, ni code à recopier. Tu pourras le retirer à tout moment dans '
    + 'Paramètres > Sécurité.';
  $('ov-faceid').classList.add('show');
}
function faceIdPlusTard(){
  try{ localStorage.setItem('codelab-faceid-plus-tard', '1'); }catch(e){}
  hide('ov-faceid');
}
async function faceIdActiver(){
  const e=$('fid-err'); e.style.display='none';
  $('fid-ok').disabled=true;
  /* Meme enregistrement que Parametres > Securite : on nomme la cle d'apres
     l'appareil, pour la reconnaitre dans la liste. */
  $('pk-nom').value = nomDuGeste().replace(/^ton /,'').replace(/^une clé d'accès$/,'Clé d\'accès');
  await pkAjouter();
  $('fid-ok').disabled=false;
  if($('pk-err').style.display==='block'){
    e.textContent=$('pk-err').textContent; e.style.display='block'; return;
  }
  hide('ov-faceid');
}
