/* CodeLab -- panneau/08-reglages.js : Les reglages du compte : adresse mail, alertes, securite (appareils), sauvegardes, version, mot de passe et preferences ; puis le demarrage de la page.
   Une partie du script du panneau, assemblee par app.py (PANNEAU_MODULES)
   dans l'ordre des numeros : un seul script, une seule portee. */
/* ---------------- mon adresse mail ---------------- */
let monCompte={};
async function chargerMonCompte(){
  try{ monCompte=await (await fetch('/api/mon-compte')).json(); }catch(e){ return; }
  /* Le compte d'administration n'a pas d'adresse propre : son mot de passe
     vit dans credentials.env, et les alertes ont deja leur destinataire. */
  if(monCompte.role==='admin'){ $('profil-mail').style.display='none'; return; }
  $('profil-mail').style.display='';
  $('mail-adresse').value = monCompte.email || '';
  const p=$('mail-etat');
  p.className = 'pill ' + (monCompte.email_verifie ? 'on' : 'off');
  p.innerHTML = '<i></i>' + (monCompte.email_verifie ? 'Verifiee'
    : (monCompte.email ? 'Non vérifiée' : 'Aucune adresse'));
  $('mail-sans-smtp').style.display = monCompte.smtp ? 'none' : 'block';
  $('mail-save').disabled = !monCompte.smtp;
  $('mail-renvoyer').style.display =
    (monCompte.smtp && monCompte.email && !monCompte.email_verifie) ? '' : 'none';
  $('mail-confirmation').style.display =
    (monCompte.email && !monCompte.email_verifie) ? 'block' : 'none';
}
function mailMessage(texte, erreur){
  $('mail-err').style.display = erreur ? 'block' : 'none';
  $('mail-msg').style.display = (!erreur && texte) ? 'block' : 'none';
  (erreur ? $('mail-err') : $('mail-msg')).textContent = texte;
}
async function mailEnregistrer(){
  mailMessage('', false);
  const r=await fetch('/api/mon-compte/email',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email:$('mail-adresse').value.trim()})});
  const d=await r.json();
  if(!r.ok){ mailMessage(d.error||'Enregistrement impossible.', true); chargerMonCompte(); return; }
  await chargerMonCompte();
  mailMessage('Un code vient de partir. Saisis-le ci-dessous.', false);
  $('mail-code').focus();
}
async function mailRenvoyer(){
  mailMessage('', false);
  const r=await fetch('/api/mon-compte/email/code',{method:'POST'});
  const d=await r.json();
  mailMessage(r.ok ? 'Un nouveau code vient de partir.' : (d.error||'Envoi impossible.'), !r.ok);
}
async function mailConfirmer(){
  mailMessage('', false);
  const r=await fetch('/api/mon-compte/email/confirmer',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:$('mail-code').value})});
  const d=await r.json();
  if(!r.ok){ mailMessage(d.error||'Confirmation impossible.', true); return; }
  $('mail-code').value='';
  await chargerMonCompte();
  mailMessage('Adresse vérifiée.', false);
}

async function usReinitTotp(){
  const nom=usCourant;
  if(!await demander('Réinitialiser le second facteur ?',
    `« ${nom} » enregistrera une nouvelle clé a sa prochaine connexion. A faire quand la personne `
    + `a change ou perdu son téléphone.`, 'Reinitialiser')) return;
  if(await usEcrire({reinitialiser_totp:true}))
    notifier('Second facteur remis a zero pour « ' + nom + ' ».');
}

/* Les appareils d'un compte, vus par l'administrateur. */
async function usChargerAppareils(nom){
  $('us-f-appareils').textContent='…'; $('us-f-deconnecter').disabled=true;
  let liste=[];
  try{ const r=await fetch('/api/utilisateurs/'+encodeURIComponent(nom)+'/appareils');
       if(r.ok) liste=(await r.json()).appareils||[]; }catch(e){}
  if(usCourant!==nom) return;
  $('us-f-appareils').textContent = liste.length
    ? liste.map(a=>a.appareil+' ('+ilYA(a.vue)+')').join(' · ')
    : 'Aucun appareil connecté.';
  $('us-f-deconnecter').disabled=!liste.length;
}
async function usDeconnecterAppareils(){
  const nom=usCourant;
  if(!await demander('Déconnecter « '+nom+' » partout ?',
    "Ses appareils devront se reconnecter. Son mot de passe et ses clés d'accès ne changent pas.",
    'Déconnecter')) return;
  const r=await fetch('/api/utilisateurs/'+encodeURIComponent(nom)+'/appareils',{method:'DELETE'});
  if(!r.ok){ notifier(await echec(r,'Déconnexion impossible.'),'err'); return; }
  notifier('« '+nom+' » est déconnecté de tous ses appareils.');
  usChargerAppareils(nom);
}

async function usRetirerCles(){
  const nom=usCourant;
  if(!await demander("Retirer les clés d'accès ?",
    `Les appareils de « ${nom} » ne l'ouvriront plus. Elle pourra en enregistrer de nouveaux après `
    + `s'être connectee avec son mot de passe.`, 'Retirer')) return;
  if(await usEcrire({retirer_passkeys:true}))
    notifier("Clés d'accès retirées pour « " + nom + " ».");
}

async function usSuspendre(){
  const c=usCompte(); if(!c) return;
  const nom=c.nom, suspendre=!c.suspendu;
  if(suspendre && !await demander('Suspendre le compte « ' + nom + ' » ?',
      "Il ne pourra plus se connecter, et sa session ouverte cessera d'autoriser quoi que ce "
      + "soit. Ses projets autorisés, son adresse et ses clés d'accès sont conservés.",
      'Suspendre', true)) return;
  if(await usEcrire({suspendu: suspendre}))
    notifier(suspendre ? 'Compte « ' + nom + ' » suspendu.'
                       : 'Compte « ' + nom + ' » réactivé.');
}

async function usSupprimer(){
  const nom=usCourant;
  if(!await demander('Supprimer le compte « ' + nom + ' » ?',
    "Le compte et ses clés d'accès disparaissent. Ses sessions ouvertes n'autoriseront plus rien.",
    'Supprimer', true)) return;
  const r=await fetch('/api/utilisateurs/'+encodeURIComponent(nom),{method:'DELETE'});
  if(!r.ok){ const d=await r.json(); usFicheErreur(d.error||'Suppression impossible.'); return; }
  hide('ov-user');
  notifier('Compte « ' + nom + ' » supprimé.');
  chargerUtilisateurs();
}

/* ---------------- alertes par mail ---------------- */
let alTls='starttls';
let alEtat={};

function alSetTls(t){
  alTls=t;
  document.querySelectorAll('#al-tls-toggle button')
    .forEach(b=>b.classList.toggle('active', b.dataset.t===t));
}

/* Un message affiche dans l'onglet qu'on ne regarde pas est un message
   perdu : chaque onglet a le sien, et l'appelant dit lequel. */
function alMessage(texte, erreur, ou){
  const prefixe = ou || 'al';
  const e=$(prefixe+'-err'), m=$(prefixe+'-msg');
  e.style.display='none'; m.style.display='none';
  if(!texte) return;
  const cible = erreur ? e : m;
  cible.textContent=texte; cible.style.display='block';
}

async function chargerAlertes(){
  svCharger();
  let d;
  try{ d=await (await fetch('/api/alertes')).json(); }catch(e){ return; }
  alEtat = d;

  const pill=$('al-etat');
  pill.className='pill '+(d.actif?'on':'off');
  pill.innerHTML='<i></i>'+(d.actif?'Activées':'Désactivées');
  $('al-toggle').textContent = d.actif ? 'Désactiver' : 'Activer';

  /* L'etat du SERVEUR ne depend pas des alertes : un serveur pret l'est
     meme si personne n'est encore prevenu quand une application tombe. */
  const ps=$('sm-etat');
  ps.className='pill '+(d.smtp_ok?'on':'off');
  ps.innerHTML='<i></i>'+(d.smtp_ok ? 'Configuré' : 'Non configuré');
  majSmtpRequis(!!d.smtp_ok);

  /* Le formulaire montre la configuration PERSONNALISEE, jamais celle
     d'origine : sinon enregistrer la recopierait au coffre, et l'on
     perdrait la distinction qui fait tout l'interet du repli. */
  const perso = d.personnalisee || {};
  $('al-host').value = perso.host||'';
  $('al-port').value = perso.port||'';
  $('al-user').value = perso.user||'';
  $('al-pass').value = '';
  $('al-pass').placeholder = perso.mot_de_passe_defini ? 'inchange' : 'aucun';
  alSetTls(perso.tls||'starttls');

  $('al-admin').value = (d.admin||[]).join(', ');

  const manque = (d.manquants||[]);
  $('sm-msg').style.display='none';
  if(!d.smtp_ok){
    $('sm-msg').innerHTML='Il manque : <b>'+manque.map(esc).join(', ')+'</b>.';
    $('sm-msg').style.display='block';
  }

  $('al-smtp-source').innerHTML = d.smtp_ok
    ? 'Les mails sortent par le serveur réglé dans l\'onglet <b>E-mail</b>.'
    : 'Aucun serveur d\'envoi utilisable : les alertes ne partiront pas. '
      + 'Règle-le dans l\'onglet <b>E-mail</b>.';


  const incidents=(d.incidents||[]);
  alMessage(incidents.length
    ? 'Incident en cours sur : '+incidents.join(', ')+'.' : '', false);
}

/* LA SURVEILLANCE EXTERNE. */
function svPeindre(d){
  $('sv-url').value = d.url || '';
  const p=$('sv-etat');
  const actif=!!d.url;
  p.className='pill '+(!actif ? 'off' : (d.ok===false ? 'err' : 'on'));
  p.innerHTML='<i></i>'+(!actif ? 'Désactivée'
    : d.ok===false ? 'Injoignable' : d.ok ? 'Active' : 'En attente');
  const m=$('sv-msg');
  if(actif && d.dernier_appel){
    m.textContent='Dernier appel : '+new Date(d.dernier_appel*1000).toLocaleString()
      +' ('+(d.detail||'')+').';
    m.style.display='block';
  }else m.style.display='none';
}
async function svCharger(){
  try{
    const r=await fetch('/api/surveillance-externe');
    if(r.ok) svPeindre(await r.json());
  }catch(e){}
}
async function svEnregistrer(){
  const b=$('sv-save'); if(b.disabled) return;
  b.disabled=true; const avant=b.textContent; b.textContent='Vérification...';
  $('sv-err').style.display='none';
  try{
    const r=await fetch('/api/surveillance-externe',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url:$('sv-url').value.trim()})});
    const d=await r.json();
    if(!r.ok){
      $('sv-err').textContent=(d.error||'Enregistrement impossible.')+(d.detail?' — '+d.detail:'');
      $('sv-err').style.display='block';
      return;
    }
    notifier(d.url ? 'Surveillance externe active.' : 'Surveillance externe désactivée.');
    svCharger();
  }catch(e){
    $('sv-err').textContent='Le panneau n\'a pas répondu.'; $('sv-err').style.display='block';
  }finally{ b.disabled=false; b.textContent=avant; }
}

/* Ce que les deux onglets envoient. Les champs des deux sont dans le
   document meme quand l'onglet est masque : enregistrer depuis l'un
   n'efface donc pas ce qui est regle dans l'autre. */
function alCorps(){
  return {
    actif: $('al-etat').classList.contains('on'),
    admin: $('al-admin').value,
    smtp: {
      host: $('al-host').value.trim(), port: $('al-port').value.trim(),
      tls: alTls, user: $('al-user').value.trim(),
      expediteur: $('al-user').value.trim(), password: $('al-pass').value,
    },
  };
}

async function alPoster(corps, bouton, ou){
  const b=$(bouton); const avant=b.textContent;
  b.disabled=true;
  if(corps.smtp && corps.smtp.host) b.textContent='Verification...';
  try{
    const r=await fetch('/api/alertes',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(corps)});
    const d=await r.json();
    if(!r.ok){
      /* Le message du serveur SMTP est la seule chose qui aide vraiment ici
         ("authentification refusée", "relais interdit") : on le montre. */
      alMessage((d.error||'Enregistrement impossible.')
                + (d.detail ? ' — '+d.detail : ''), true, ou);
      return false;
    }
    return true;
  }catch(e){ alMessage('Erreur réseau.', true, ou); return false; }
  finally{ b.disabled=false; b.textContent=avant; }
}

async function alEnregistrer(ou){
  const prefixe = ou || 'al';
  alMessage('', false, prefixe);
  if(await alPoster(alCorps(), prefixe+'-save', prefixe)){
    await chargerAlertes();
    alMessage(prefixe==='sm' ? 'Serveur vérifié et enregistré.'
                             : 'Réglages enregistrés.', false, prefixe);
  }
}

async function alBasculer(){
  alMessage('', false);
  const corps=alCorps();
  corps.actif = !corps.actif;
  /* Basculer l'interrupteur ne doit pas re-verifier le serveur : on
     n'envoie donc pas le bloc SMTP. */
  delete corps.smtp;
  if(await alPoster(corps, 'al-toggle', 'al')) await chargerAlertes();
}

/* ---------------- securite : double authentification ---------------- */
/* ---------------- les appareils connectes ---------------- */
let apTout=false, apDerniers=[];
const AP_VISIBLES=4;
function peindreAppareils(liste){
  /* Cet appareil en tete : c'est lui qu'on cherche, il ne doit jamais
     tomber dans les lignes cachees. */
  liste=[...liste].sort((x,y)=>(y.courant?1:0)-(x.courant?1:0));
  apDerniers=liste;
  const caches = apTout ? 0 : Math.max(0, liste.length-AP_VISIBLES);
  remplir($('ap-liste'), (liste.length ? liste.slice(0, liste.length-caches).map(a=>`<div class="ap-ligne">
      <div class="ap-quoi"><b>${esc(a.appareil)}</b>${a.courant ? ' <span class="pill on"><i></i>Cet appareil</span>' : ''}
        <small>${a.courant ? 'Actif maintenant' : 'Actif '+ilYA(a.vue)}${a.ip ? ' · '+esc(a.ip) : ''} · connecté ${ilYA(a.ouverte)}</small></div>
      <button class="btn btn-default btn-sm" data-agir="appareil-deconnecter" data-cible="${esc(a.id)}"
        ${a.courant ? 'data-courant="1"' : ''}>${a.courant ? 'Me déconnecter' : 'Déconnecter'}</button></div>`).join('')
    : '<p class="hint">Aucun appareil.</p>')
    + (caches ? `<button type="button" class="btn btn-default btn-sm ap-plus" onclick="apTout=true;peindreAppareils(apDerniers)">Afficher les ${caches} autres</button>` : ''));
  $('ap-autres').disabled = !liste.some(a=>!a.courant);
}
async function chargerAppareils(){
  try{
    const r=await fetch('/api/mon-compte/appareils'); if(!r.ok) return;
    peindreAppareils((await r.json()).appareils||[]);
  }catch(e){}
}
async function apDeconnecter(id, courant){
  if(courant){ doLogout(); return; }
  const r=await fetch('/api/mon-compte/appareils/'+encodeURIComponent(id),{method:'DELETE'});
  if(!r.ok){ notifier(await echec(r,'Déconnexion impossible.'),'err'); return; }
  notifier('Appareil déconnecté.');
  chargerAppareils();
}
async function apDeconnecterAutres(){
  if(!await demander('Déconnecter les autres appareils ?',
    "Ils devront se reconnecter. Celui-ci reste connecté, et ton mot de passe ne change pas.",
    'Déconnecter')) return;
  const r=await fetch('/api/mon-compte/appareils/deconnecter-autres',{method:'POST'});
  if(!r.ok){ notifier(await echec(r,'Déconnexion impossible.'),'err'); return; }
  const n=(await r.json()).deconnectes;
  notifier(n ? n+' appareil'+(n>1?'s':'')+' déconnecté'+(n>1?'s':'')+'.' : 'Aucun autre appareil.');
  chargerAppareils();
}

/* ---------------- les sauvegardes ---------------- */
const tailleLisible = o => o>=1e9 ? (o/1e9).toFixed(1).replace('.',',')+' Go'
  : o>=1e6 ? (o/1e6).toFixed(1).replace('.',',')+' Mo' : Math.max(1, Math.round(o/1e3))+' Ko';
function peindreSauvegardes(d){
  $('zone-sauvegardes').hidden=false;
  const pill=$('sg-etat');
  const derniere=d.archives[0];
  if(!d.actives){
    pill.className='pill err'; pill.innerHTML='<i></i>Désactivées';
    $('sg-resume').textContent="Le dossier des sauvegardes n'est pas monté : rien n'est sauvegardé. Ajoute le volume /sauvegardes dans le compose.";
  }else{
    pill.className='pill '+(d.alerte_hors_serveur ? 'warn' : 'on');
    pill.innerHTML='<i></i>'+(d.alerte_hors_serveur ? 'Sur ce serveur seulement' : 'Actives');
    $('sg-resume').textContent='Toutes les '+d.toutes_les_heures+' h, les '+d.gardees+' dernières gardées. '
      + (derniere ? 'Dernière : '+ilYA(derniere.date)+'.' : 'Aucune pour le moment.')
      + (d.derniere_copie ? ' Copiée hors du serveur '+ilYA(d.derniere_copie)+'.' : '');
  }
  $('sg-alerte').hidden=!d.alerte_hors_serveur;
  $('sg-externe').checked=!!d.copie_externe;
  /* La plus recente de chaque genre : c'est elle qu'on emporte. */
  const vues=new Set(), lignes=[];
  for(const a of d.archives){ if(vues.has(a.genre)) continue; vues.add(a.genre); lignes.push(a); }
  remplir($('sg-liste'), lignes.map(a=>`<div class="sg-ligne"><div class="sg-quoi"><b>${a.genre==='panneau'
      ? 'Le panneau' : 'Les projets'}</b><small>${esc(a.nom)} · ${tailleLisible(a.taille)} · ${ilYA(a.date)}</small></div>
    <a class="btn btn-default btn-sm" href="/api/sauvegardes/${encodeURIComponent(a.nom)}" download
       onclick="setTimeout(chargerSauvegardes, 1500)">Télécharger</a></div>`).join(''));
}
async function chargerSauvegardes(){
  try{
    const r=await fetch('/api/sauvegardes'); if(!r.ok) return;
    peindreSauvegardes(await r.json());
  }catch(e){}
}
async function sgCopieExterne(oui){
  const r=await fetch('/api/sauvegardes/copie',{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({externe:oui})});
  if(!r.ok){ notifier(await echec(r,'Réglage non enregistré.'),'err'); chargerSauvegardes(); return; }
  peindreSauvegardes(await r.json());
}

/* ---------------- la conservation du journal ----------------
   Raccourcir la duree efface des lignes, tout de suite et pour de bon : le
   choix se confirme, bouton au verbe. L'allonger n'efface rien : il passe
   sans question. */
let cjMois=null;
async function chargerConservation(){
  try{
    const r=await fetch('/api/journal/conservation'); if(!r.ok) return;
    cjMois=(await r.json()).mois;
  }catch(e){ return; }
  $('cj-mois').value=String(cjMois);
  $('zone-conservation').hidden=false;
}
const cjDuree = m => m ? m+' mois' : 'sans limite';
async function cjChanger(sel){
  const mois=+sel.value;
  const raccourcit = mois && (!cjMois || mois<cjMois);
  if(raccourcit && !await demander('Effacer le journal au-delà de '+mois+' mois ?',
      'Les connexions et ouvertures plus anciennes seront effacées tout de suite, ici et dans Postgres. On ne pourra pas les retrouver.',
      'Effacer', true)){
    sel.value=String(cjMois); return;
  }
  const r=await fetch('/api/journal/conservation',{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({mois})});
  if(!r.ok){ notifier(await echec(r,'Réglage non enregistré.'),'err'); sel.value=String(cjMois); return; }
  const d=await r.json(); cjMois=d.mois;
  notifier('Journal gardé '+cjDuree(cjMois)+(d.retirees ? ' — '+d.retirees+' ligne'+(d.retirees>1?'s':'')+' effacée'+(d.retirees>1?'s':'')+'.' : '.'));
}

/* ---------------- la version de CodeLab ---------------- */
const COMMANDES_MAJ = `for i in app-manager dev postgres redis mailpit dagster dagster-proxy; do
  docker pull ghcr.io/studio-codelab/codelab-$i:latest
done
docker compose up -d --force-recreate`;
/* Distincte de dateCourte (02-hub.js) : les modules partagent une portee,
   et une seconde declaration du meme nom remplacait l'autre en silence. */
function dateDeVersion(iso){
  if(!iso) return '';
  const d=new Date(iso); if(isNaN(d)) return '';
  return d.toLocaleDateString('fr-FR',{day:'numeric', month:'long', year:'numeric'});
}
function peindreVersion(d){
  const nom = d.dev ? 'Une version de développement, construite à la main : elle ne porte pas de numéro.'
    : 'Installée : la version '+d.court+(d.date ? ' du '+dateDeVersion(d.date) : '')+'.';
  $('ver-resume').textContent=nom;
  remplir($('ver-corps'), (d.dev ? '' : `<div class="hint" style="margin-top:10px">Le panneau ne contacte pas GitHub pour savoir s'il existe plus récent.
      Pour voir ce qui a changé depuis ta version : <a href="https://github.com/${esc(d.depot)}/compare/${esc(d.sha)}...main"
      rel="noopener noreferrer" class="ver-lien">les nouveautés sur GitHub</a>.</div>`)
    + '<b style="display:block;margin-top:14px">Pour mettre à jour</b>'
    + '<div class="hint">Dans le dossier du <code>docker-compose.yml</code>, sur le serveur (avec CasaOS : « Mettre à jour » sur l\'application) :</div>'
    + `<pre class="ver-commandes">${esc(COMMANDES_MAJ)}</pre>`
    + `<div class="actions" style="margin-top:8px"><button class="btn btn-default btn-sm" data-agir="ver-copier">Copier les commandes</button></div>`);
}
async function chargerVersion(){
  try{
    const r=await fetch('/api/version'); if(!r.ok) return;
    peindreVersion(await r.json());
  }catch(e){}
}

async function chargerSecurite(){
  let d;
  try{ d=await (await fetch('/api/securite')).json(); }catch(e){ return; }
  const pill=$('totp-etat');
  pill.className = 'pill ' + (d.totp ? 'on' : 'off');
  pill.innerHTML = '<i></i>' + (d.totp ? 'Activée' : 'Désactivée');
  $('totp-etape-1').style.display = d.totp ? 'none' : 'block';
  $('totp-etape-3').style.display = d.totp ? 'block' : 'none';
  $('totp-etape-2').style.display = 'none';

  adressePublique = d.adresse_publique || '';
  /* La fiche ouverte et le formulaire d'ajout suivent : ce qu'ils proposent
     depend de cette reponse. */
  if(ficheNom) peindreFiche();
}

/* L'adresse publique du serveur, apprise par le serveur la premiere fois
   qu'on ouvre le panneau par son nom de domaine. "" = jamais ouvert depuis
   internet, et « publique » n'y voudrait rien dire. */
let adressePublique='';
async function chargerAdressePublique(){
  try{
    const d=await (await fetch('/api/securite')).json();
    adressePublique = d.adresse_publique || '';
  }catch(e){}
}

/* ---------------- mot de passe et preferences ---------------- */
async function mdpChanger(){
  const e=$('mdp-err'), m=$('mdp-msg');
  e.style.display='none'; m.style.display='none';
  const ancien=$('mdp-ancien').value, nouveau=$('mdp-nouveau').value;
  /* La repetition est verifiee ICI et pas au serveur : c'est une faute de
     frappe, pas une regle de securite, et un aller-retour pour le dire
     serait du bruit. */
  if(nouveau !== $('mdp-repeter').value){
    e.textContent='Les deux saisies différent.'; e.style.display='block'; return;
  }
  const b=$('mdp-save'); b.disabled=true;
  try{
    const r=await fetch('/api/compte/mot-de-passe',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ancien, nouveau})});
    const d=await r.json();
    if(!r.ok){ e.textContent=d.error||'Changement impossible.'; e.style.display='block'; }
    else{
      /* Les champs sont vides tout de suite : un mot de passe qui reste
         affiche derriere un onglet est un mot de passe qui traine. */
      $('mdp-ancien').value=$('mdp-nouveau').value=$('mdp-repeter').value='';
      m.textContent='Mot de passe change. Ta session reste ouverte ; '
        + 'la prochaine connexion demandera le nouveau.';
      m.style.display='block';
    }
  }catch(err){ e.textContent='Erreur réseau.'; e.style.display='block'; }
  finally{ b.disabled=false; }
}

/* LA MEME CARTE POUR TOUS LES COMPTES. L'application d'authentification de
   l'administrateur vit dans credentials.env (/api/securite/totp/...) ; celle
   d'un compte, dans sa fiche (/api/mon-compte/totp/...). */
const TOTP_ROUTES = () => estAdmin
  ? {preparer:'/api/securite/totp/preparer', activer:'/api/securite/totp/activer',
     retirer:'/api/securite/totp/desactiver'}
  : {preparer:'/api/mon-compte/totp/preparer', activer:'/api/mon-compte/totp/activer',
     retirer:'/api/mon-compte/totp/retirer'};
function rechargerTotp(){ if(estAdmin) chargerSecurite(); else chargerTotpCompte(); }
async function chargerTotpCompte(){
  let d;
  try{ d=await (await fetch('/api/mon-compte/totp')).json(); }catch(e){ return; }
  const pill=$('totp-etat');
  pill.className = 'pill ' + (d.actif ? 'on' : 'off');
  pill.innerHTML = '<i></i>' + (d.actif ? 'Activée' : 'Désactivée');
  $('totp-etape-1').style.display = d.actif ? 'none' : 'block';
  $('totp-etape-3').style.display = d.actif ? 'block' : 'none';
  $('totp-etape-2').style.display = 'none';
  /* Le dernier second facteur ne se retire pas : le serveur le refuse, la
     carte le dit avant. */
  const seul = d.actif && !d.retirable;
  $('totp-retrait-impossible').style.display = seul ? 'block' : 'none';
  $('totp-retrait').style.display = seul ? 'none' : 'block';
}
async function totpPreparer(){
  const r=await fetch(TOTP_ROUTES().preparer,{method:'POST'});
  const d=await r.json();
  if(!r.ok){ notifier(d.error||'Préparation impossible.', 'err'); return; }
  $('totp-secret').value=d.secret;
  montrerQr(d.qr);
  $('totp-etape-1').style.display='none';
  $('totp-etape-2').style.display='block';
  $('totp-code').value=''; $('totp-code').focus();
}
function totpAnnuler(){ $('totp-etape-2').style.display='none'; rechargerTotp(); }

/* Le QR code est servi par le serveur a partir de la session : le secret ne
   passe jamais par l'adresse, donc ni par l'historique du navigateur ni par
   les journaux d'acces. Si la route ne repond pas (bibliotheque absente),
   l'image se retire et seule la cle a recopier reste. */
function montrerQr(adresse){
  const img=$('totp-qr');
  if(!adresse){ img.classList.remove('on'); return; }
  img.onload=()=>img.classList.add('on');
  img.onerror=()=>img.classList.remove('on');
  img.src=adresse+'?t='+Date.now();
}

async function totpActiver(){
  const e=$('totp-err'); e.style.display='none';
  const r=await fetch(TOTP_ROUTES().activer,{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:$('totp-code').value})});
  const d=await r.json();
  if(!r.ok){ e.textContent=d.error||'Echec.'; e.style.display='block'; return; }
  rechargerTotp();
}

async function totpDesactiver(){
  const e=$('totp-err-off'); e.style.display='none';
  const r=await fetch(TOTP_ROUTES().retirer,{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({code:$('totp-code-off').value})});
  const d=await r.json();
  if(!r.ok){ e.textContent=d.error||'Echec.'; e.style.display='block'; return; }
  $('totp-code-off').value='';
  rechargerTotp();
}

async function doLogout(){ await fetch('/logout',{method:'POST'}); location.href='/login'; }

/* Supprimer son compte (voir « supprimer son propre compte », serveur/10) :
   le serveur ferme toutes les sessions, celle-ci comprise, et rend la date
   d'effacement. On l'annonce sur la page de connexion, ou l'on revient. */
function ouvrirSuppressionCompte(){
  $('sup-mdp').value=''; $('sup-err').style.display='none';
  $('ov-suppression').classList.add('show');
  setTimeout(()=>$('sup-mdp').focus(), 40);
}
async function confirmerSuppressionCompte(){
  const ok=$('sup-ok'); ok.disabled=true;
  try{
    const r=await fetch('/api/mon-compte/suppression',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({mot_de_passe:$('sup-mdp').value})});
    if(r.ok){
      const d=await r.json();
      hide('ov-suppression');
      notifier('Ton compte sera supprimé le '+d.date+'. Reconnecte-toi d\'ici là pour annuler.');
      setTimeout(()=>{ location.href='/login'; }, 2500);
      return;
    }
    $('sup-err').textContent=await echec(r,'La suppression n\'a pas pu être demandée.');
    $('sup-err').style.display='block';
  }finally{ ok.disabled=false; }
}


document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    hide('ov-add'); hide('ov-user'); hide('ov-suppression');
    if(confirmerResoudre) confirmerReponse(false);
    $('acct-menu').classList.remove('show');
  }
});
document.querySelectorAll('.ov').forEach(o=>o.addEventListener('click',
 e=>{ if(e.target===o) o.classList.remove('show'); }));

ROOT=CODELAB_PAGE.root;
/* Injectes par le serveur au moment de servir la page. Confort d'affichage
   uniquement : chaque route verifie le role de son cote. */
const ROLE=CODELAB_PAGE.role, UTILISATEUR=CODELAB_PAGE.utilisateur;
const estAdmin = ROLE==='admin';
let SMTP_OK = CODELAB_PAGE.smtp;

/* LE PROFIL : le prenom remplace l'identifiant la ou la page parle a la
   personne ; l'identifiant reste celui de la connexion. */
let PROFIL = CODELAB_PAGE.profil || {prenom:'', nom:''};
function peindreProfil(){
  const complet = [PROFIL.prenom, PROFIL.nom].filter(Boolean).join(' ');
  $('qui-nom').textContent = complet || UTILISATEUR || '';
  $('hub-salut').innerHTML = 'Bonjour, <span class="salut-nom">'
    + esc(PROFIL.prenom || PROFIL.nom || UTILISATEUR || 'admin') + '</span>';
  if(!$('profil-prenom')) return;            // administrateur : pas de carte
  $('profil-prenom').value = PROFIL.prenom || '';
  $('profil-nom').value = PROFIL.nom || '';
  $('profil-identifiant').textContent = UTILISATEUR || 'admin';
}
async function profilEnregistrer(){
  $('profil-err').style.display='none';
  const r=await fetch('/api/mon-compte/profil',{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({prenom:$('profil-prenom').value, nom:$('profil-nom').value})});
  if(!r.ok){ $('profil-err').textContent=await echec(r,'Profil non enregistré.');
    $('profil-err').style.display='block'; return; }
  const d=await r.json(); PROFIL={prenom:d.prenom, nom:d.nom};
  peindreProfil(); notifier('Profil enregistré.');
}
peindreProfil();
$('qui-role').textContent = estAdmin ? 'Administrateur' : 'Utilisateur';
/* Les onglets d'administration n'apparaissent que pour l'administrateur. */
if(estAdmin) document.querySelectorAll('#param-onglets button[data-admin]')
  .forEach(b=>{ b.style.display='block'; });
/* Rien a regler ici pour l'administrateur : son mot de passe est celui de
   credentials.env, et le mot a l'administrateur lui serait adresse. */
if(estAdmin){ $('profil-mdp').style.display='none'; $('profil-message').style.display='none'; }

/* LES ARCHIVES. La liste vient du serveur a chaque ouverture de l'onglet :
   une archive peut avoir ete effacee par la purge quotidienne entre-temps. */
function dansCombienDeJours(ts){
  const j=Math.max(0, Math.ceil((ts-Date.now()/1000)/86400));
  return j===0 ? "aujourd'hui" : j===1 ? 'demain' : 'dans '+j+' jours';
}
async function chargerArchives(){
  let d;
  try{ d=await (await fetch('/api/apps/archivees')).json(); }catch(e){ return; }
  const liste=d.apps||[];
  $('arch-resume').textContent = liste.length
    ? liste.length+' application'+(liste.length>1?'s':'')+' aux archives.'
    : 'Aucune application aux archives.';
  remplir($('arch-liste'), liste.length ? liste.map(a=>`<div class="arch-ligne">
      <div class="arch-nom"><b>${esc(a.titre||a.name)}</b>
        <span>/${esc(a.name)}/ · archivée le ${esc(dateDeVersion(new Date(a.archivee_le*1000).toISOString()))}
          · effacée ${esc(dansCombienDeJours(a.suppression_le))}</span></div>
      <div class="arch-gestes">
        <button class="btn btn-default btn-sm" data-agir="archive-ressortir" data-cible="${esc(a.name)}">Désarchiver</button>
        <button class="btn btn-danger-quiet btn-sm" data-agir="archive-effacer" data-cible="${esc(a.name)}">Supprimer</button>
      </div></div>`).join('')
    : `<div class="arch-vide">Archiver une application se fait depuis sa fiche, onglet Configuration.</div>`);
}
async function desarchiver(n){
  const r=await fetch('/api/app/'+encodeURIComponent(n)+'/desarchiver',{method:'POST'});
  if(!r.ok){ notifier(await echec(r, 'Désarchivage impossible.'), 'err'); return; }
  notifier('« '+n+' » est de retour, arrêtée.');
  chargerArchives(); refresh();
}
async function effacerArchive(n){
  if(!await demander('Supprimer « ' + n + ' » maintenant ?',
    "Sa configuration est effacée tout de suite, sans attendre les 30 jours. Le dossier du projet "
    + "dans /workspace n'est pas touché.", 'Supprimer', true)) return;
  const r=await fetch('/api/app/'+encodeURIComponent(n),{method:'DELETE'});
  if(!r.ok){ notifier(await echec(r, 'Suppression impossible.'), 'err'); return; }
  chargerArchives();
}

/* AUCUN ONGLET VIDE. Un onglet dont toutes les rubriques sont masquees pour
   ce compte ne s'affiche pas : on n'ouvre pas une page pour n'y rien
   trouver a regler. Relance quand une rubrique apparait ou disparait. */
function majOngletsParametres(){
  let nombre=0;
  document.querySelectorAll('#param-onglets button').forEach(b=>{
    const panneau=$('param-'+b.dataset.p);
    const autorise = !b.hasAttribute('data-admin') || estAdmin;
    const plein = !!panneau && [...panneau.querySelectorAll(':scope > .zone')]
      .some(z=>z.style.display!=='none');
    b.style.display = (autorise && plein) ? 'block' : 'none';
    if(autorise && plein) nombre++;
  });
  /* Le nombre d'onglets visibles : au telephone, au-dela de cinq, la
     pastille passe sur deux rangees (voir le CSS). */
  $('param-onglets').dataset.nombre=nombre;
}
majOngletsParametres();

const visible = id => $(id).style.display!=='none';

/* UN ONGLET EN ARRIERE-PLAN NE DOIT RIEN DEMANDER.
   Les minuteries du panneau interrogeaient le serveur toutes les cinq, dix
   et vingt secondes, que la page soit regardee ou non : un onglet oublie
   derriere une autre fenetre faisait travailler la machine indefiniment, et
   « Applications » mesure la charge de chaque processus a chaque tour.
   Ce relais coupe tout quand l'onglet passe en arriere-plan, et rejoue
   immediatement au retour -- c'est ce retour immediat qui permet de couper
   sans rien perdre : la page est a jour avant meme d'etre relue. */
const _minuteries = [];
/* PAS DE SAUT EN DEFILANT. Un tour de minuterie redessine sa page (le hub
   toutes les dix secondes) : tombe en plein defilement, il remplace ce qui
   passe sous le doigt et l'elan s'arrete net, ou la page saute. Tant qu'un
   doigt est pose ou qu'on a defile il y a moins de 700 ms, le tour attend
   que tout soit immobile. */
let defileLe=0, doigtsPoses=0;
document.addEventListener('scroll', ()=>{ defileLe=performance.now(); }, {capture:true, passive:true});
document.addEventListener('wheel', ()=>{ defileLe=performance.now(); }, {capture:true, passive:true});
document.addEventListener('touchstart', e=>{ doigtsPoses=e.touches.length; }, {capture:true, passive:true});
['touchend','touchcancel'].forEach(t=>document.addEventListener(t, e=>{
  doigtsPoses=e.touches.length; defileLe=performance.now(); }, {capture:true, passive:true}));
const enDefilement = () => doigtsPoses>0 || performance.now()-defileLe < 700;
function quandImmobile(fn){
  if(!enDefilement()) return fn();
  setTimeout(()=>quandImmobile(fn), 250);
}
function surveiller(fn, delai){
  let enAttente=false;
  const tour=()=>{ if(enAttente) return; enAttente=true; quandImmobile(()=>{ enAttente=false; fn(); }); };
  const t = {fn: tour, delai: delai, id: null};
  _minuteries.push(t);
  if(!document.hidden) t.id = setInterval(tour, delai);
  return t;
}
document.addEventListener('visibilitychange', ()=>{
  _minuteries.forEach(t=>{
    if(document.hidden){
      clearInterval(t.id); t.id = null;
    }else if(t.id === null){
      t.fn();
      t.id = setInterval(t.fn, t.delai);
    }
  });
});

if(estAdmin){
  /* « Hub » cote administrateur : il y voit TOUTES les applications, pas
     seulement les siennes -- le titre « Mes applications » serait faux. */
  $('hub-titre').textContent='Hub';
  /* Les compteurs du menu lateral doivent etre justes des la premiere
     seconde, avant meme qu'on ouvre « Applications » ou « Utilisateurs ». */
  refresh();
  chargerUtilisateurs();
  chargerAdressePublique();
  surveiller(()=>{ if(visible('sec-overview')||visible('sec-apps')||visible('sec-app')) refresh(); }, 5000);
  /* Les messages arrivent quand ils arrivent : on les guette depuis
     n'importe quelle page du panneau, pas seulement depuis la leur. */
  chargerMessages();
  surveiller(guetterMessages, 20000);
  /* Les metriques de la fiche ne se rafraichissent que lorsqu'on les regarde. */
  surveiller(()=>{
    if(ficheNom && ficheOngletActif==='metriques' && visible('sec-app')) chargerMetriques(ficheNom);
  }, 5000);
}else{
  /* Compte utilisateur : pas de menu lateral, pas de configuration. Il garde
     ses parametres personnels, eux, dans le menu du compte. */
  /* La carte de l'application d'authentification sert aussi aux comptes :
     c'est l'une des deux methodes au choix a la connexion, avec le code par
     mail. Ses routes sont celles du compte (TOTP_ROUTES). */
  $('totp-pourquoi').textContent = "À chaque connexion, tu choisis : le code reçu par mail "
    + "ou celui de cette application. La dernière méthode utilisée est proposée d'office.";
  document.body.classList.add('compte-utilisateur');
  /* Les categories du hub et la version : l'affaire de l'administrateur. */
  $('zone-categories').remove(); $('zone-version').remove();
}
/* Le compte d'administration garde son nom (voir « le profil d'un compte »,
   serveur/10) : pas de carte Profil. */
if(estAdmin){
  $('zone-profil').remove();
}
majOngletsParametres();
/* Le hub est l'accueil de tout le monde. Laisse ouvert il reste juste : les
   etats changent tout seuls quand une application tombe ou repart. */
routerDepuisAdresse();
surveiller(()=>{ if(visible('sec-hub')) chargerHub(); }, 10000);
if(estAdmin) majSmtpRequis(SMTP_OK);
/* Une seule invitation a la fois : l'envoi des mails d'abord, Face ID au
   prochain passage. */
if(!proposerSmtp()) proposerFaceId();


/* Au retour sur l'onglet (page restauree par « Precedent », onglet repris),
   le navigateur peut garder l'icone vue en dernier sur ce nom d'hote --
   celle de Mailpit ou d'une application. Reposer les balises lui fait
   relire celle du panneau. */
function reposerIcone(){
  document.querySelectorAll('link[rel~="icon"]').forEach(l=>l.replaceWith(l.cloneNode()));
}
window.addEventListener('pageshow', reposerIcone);
document.addEventListener('visibilitychange', ()=>{ if(!document.hidden) reposerIcone(); });
