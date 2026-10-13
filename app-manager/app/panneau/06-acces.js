/* CodeLab -- panneau/06-acces.js : Les cles d'acces (Face ID), le journal des acces et la carte de chaleur.
   Une partie du script du panneau, assemblee par app.py (PANNEAU_MODULES)
   dans l'ordre des numeros : un seul script, une seule portee. */
/* ---------------- cles d'acces ---------------- */
/* Le navigateur echange des ArrayBuffer, le serveur du base64url : ces deux
   fonctions sont toute la traduction. */
const b64urlVersOctets = s => Uint8Array.from(
  atob(s.replace(/-/g,'+').replace(/_/g,'/')), c=>c.charCodeAt(0));
const octetsVersB64url = b => btoa(String.fromCharCode(...new Uint8Array(b)))
  .replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');

/* ---------------- notifications et installation ----------------
   LES NOTIFICATIONS DE CET APPAREIL (Web Push). Le serveur donne sa cle
   publique ; le navigateur s'abonne aupres de son service de notification
   (Apple, Google, Mozilla) et rend une adresse, que le panneau garde pour
   CE compte et CETTE session. La permission se demande DANS le geste
   (l'interrupteur) : Safari refuse une demande qui n'en vient pas, d'ou la
   cle chargee a l'avance. */
let notifEtat=null;
const surIOS = () => /iP(hone|ad|od)/.test(navigator.userAgent)
  || (navigator.platform==='MacIntel' && navigator.maxTouchPoints>1);
function notifEmpechement(){
  if(!window.isSecureContext) return "Les notifications exigent une connexion HTTPS : le navigateur les refuse en clair. Mets le TLS en place, puis reviens ici.";
  if(!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window))
    return surIOS() && !enModeApp
      ? "Sur iPhone et iPad, installe d'abord CodeLab sur l'écran d'accueil (carte « Installer CodeLab »), puis active-les depuis l'icône."
      : "Ce navigateur ne sait pas recevoir de notifications.";
  if(Notification.permission==='denied') return "Bloquées pour ce site dans les réglages du navigateur : autorise-les, puis reviens ici.";
  if(notifEtat && !notifEtat.disponible) return "Le serveur n'a pas de clé de chiffrement (APP_MANAGER_CHIFFREMENT_CLE) : il ne peut pas signer ses notifications.";
  return '';
}
async function chargerNotifications(){
  try{ notifEtat=await (await fetch('/api/notifications')).json(); }catch(e){ return; }
  peindreNotifications();
}
function peindreNotifications(){
  const b=$('notif-bascule'); if(!b || !notifEtat) return;
  $('notif-quoi').textContent = estAdmin
    ? 'Sur cet appareil : les nouveaux messages, les applications qui tombent et reviennent.'
    : 'Sur cet appareil : l’avancement des messages que tu envoies.';
  const pourquoi=notifEmpechement();
  b.checked = !!notifEtat.abonne;
  b.disabled = !notifEtat.abonne && !!pourquoi;
  $('notif-empechement').style.display = pourquoi && !notifEtat.abonne ? 'block' : 'none';
  $('notif-empechement').innerHTML = pourquoi ? '<b>Indisponible ici.</b> '+esc(pourquoi) : '';
  $('notif-essai').style.display = notifEtat.abonne ? '' : 'none';
}
const octetsDeB64 = t => Uint8Array.from(atob(t.replace(/-/g,'+').replace(/_/g,'/') + '='.repeat((4-t.length%4)%4)), c=>c.charCodeAt(0));
async function notifBasculer(b){
  b.disabled=true;
  try{
    if(b.checked){
      const perm=await Notification.requestPermission();
      if(perm!=='granted'){ notifier("Notifications refusées par l'appareil.",'err'); return; }
      const reg=await navigator.serviceWorker.register('/sw.js');
      await navigator.serviceWorker.ready;
      let ab=await reg.pushManager.getSubscription();
      /* Un abonnement fait pour une autre cle (serveur reinstalle) ne
         recevrait rien : on le remplace. */
      const cle=octetsDeB64(notifEtat.cle);
      const sienne=ab && ab.options && ab.options.applicationServerKey;
      if(ab && sienne && new Uint8Array(sienne).join() !== cle.join()){ await ab.unsubscribe(); ab=null; }
      /* Le service de notification de l'appareil (Apple, Google, Mozilla)
         peut ne jamais repondre -- reseau filtre, navigateur sans ce service :
         au bout de 20 s, on le dit au lieu de laisser l'interrupteur fige. */
      ab = ab || await Promise.race([
        reg.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:cle}),
        new Promise((_, non)=>setTimeout(()=>non(new Error('delai')), 20000))]);
      const r=await fetch('/api/notifications/abonnement',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(ab.toJSON())});
      if(!r.ok){ notifier(await echec(r,'Abonnement refusé.'),'err'); return; }
      notifier('Notifications activées sur cet appareil.');
    }else{
      await fetch('/api/notifications/abonnement',{method:'DELETE'});
      const reg=await navigator.serviceWorker.getRegistration();
      const ab=reg && await reg.pushManager.getSubscription();
      if(ab) await ab.unsubscribe().catch(()=>{});
      notifier('Notifications coupées sur cet appareil.');
    }
  }catch(e){
    notifier(e && e.message==='delai'
      ? "Le service de notification de cet appareil ne répond pas : réessaie plus tard, ou depuis un autre réseau."
      : "Les notifications n'ont pas pu être réglées sur cet appareil.",'err');
  }finally{
    await chargerNotifications();
  }
}
async function notifEssai(bouton){
  bouton.disabled=true;
  try{
    const r=await fetch('/api/notifications/essai',{method:'POST'});
    notifier(r.ok ? 'Notification envoyée : elle arrive dans quelques secondes.' : await echec(r,'Envoi impossible.'), r.ok ? undefined : 'err');
  }finally{ bouton.disabled=false; }
}
/* LA PASTILLE SUR L'ICONE de l'application installee (et du Dock) : ce qui
   attend -- les messages a lire pour l'administrateur, les nouvelles d'un
   message pour un compte. Rien la ou le navigateur ne la connait pas. */
function majPastilleIcone(){
  if(!('setAppBadge' in navigator)) return;
  const n = estAdmin ? ((hubServices.find(s=>s.name==='messages')||{}).non_lues||0) : (mesNouveaux||0);
  try{ (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(()=>{}); }catch(e){}
}
/* INSTALLER CODELAB, depuis le navigateur. Chrome, Edge et Android le
   proposent eux-memes (l'evenement est garde par mode.js) ; Safari ne
   l'expose pas : la carte dit le geste. Installe, rien a proposer. */
function peindreInstallation(){
  const zone=$('zone-installer'); if(!zone) return;
  const proposee=!!window.CODELAB_INSTALLATION;
  const safari=/Safari/.test(navigator.userAgent) && !/Chrome|Chromium|CriOS|FxiOS|EdgiOS|Edg\//.test(navigator.userAgent);
  const consigne = proposee ? ''
    : surIOS() ? (safari ? 'Touche le bouton Partager <b>⬆︎</b>, puis « Sur l’écran d’accueil ».'
                         : 'Ouvre cette page dans Safari, puis Partager <b>⬆︎</b> › « Sur l’écran d’accueil ».')
    : safari ? 'Dans la barre des menus : Fichier › « Ajouter au Dock ».'
    : '';
  zone.hidden = enModeApp || (!proposee && !consigne);
  $('inst-bouton').style.display = proposee ? '' : 'none';
  $('inst-consigne').style.display = consigne ? '' : 'none';
  $('inst-consigne').innerHTML = consigne;
}
async function installerCodelab(){
  const invite=window.CODELAB_INSTALLATION; if(!invite) return;
  invite.prompt();
  try{ await invite.userChoice; }catch(e){}
  window.CODELAB_INSTALLATION=null;
  peindreInstallation();
}
window.addEventListener('codelab-installable', peindreInstallation);
window.addEventListener('appinstalled', ()=>{ window.CODELAB_INSTALLATION=null; peindreInstallation();
  notifier('CodeLab est installé : ouvre-le depuis son icône.'); });

async function chargerPasskeys(){
  let etat={}, liste={};
  try{
    etat=await (await fetch('/api/passkeys/etat')).json();
    liste=await (await fetch('/api/mon-compte/passkeys')).json();
  }catch(e){ return; }
  /* Le navigateur a aussi son mot a dire : sans PublicKeyCredential, meme
     un serveur pret ne peut rien faire. */
  const possible = etat.possible && !!window.PublicKeyCredential;
  const cles=liste.passkeys||[];
  const actif=cles.length>0;
  $('pk-titre').textContent = nomDuGeste().replace(/^ton empreinte$/,'Empreinte')
    .replace(/^une clé d'accès$/,'Clé d\'accès');
  const b=$('pk-bascule');
  b.checked=actif;
  /* Eteindre reste toujours possible ; allumer demande un serveur en HTTPS
     et un appareil qui sait verifier la personne. */
  b.disabled = !actif && !possible;
  $('pk-ajouter').style.display = (actif && possible) ? '' : 'none';
  $('pk-empechement').style.display = (possible || actif) ? 'none' : 'block';
  $('pk-empechement').innerHTML = possible ? '' :
    ('<b>Indisponible ici.</b> ' + esc(etat.empechement
      || 'Ce navigateur ne connait pas les clés d\'accès.'));

  $('pk-liste').innerHTML = cles.length ? cles.map(k=>`<div class="pk-ligne">
    <svg viewBox="0 0 24 24"><circle cx="8" cy="12" r="4"/><path d="M12 12h9"/>
    <path d="M17 12v3"/><path d="M20.5 12v2"/></svg>
    <div><b>${esc(k.nom)}</b>
      <div class="pk-quand">Ajoutée ${esc(ilYA(k.cree))}${
        k.dernier ? ' — dernière utilisation ' + esc(ilYA(k.dernier)) : ' — jamais utilisée'}</div></div>
    <div class="spacer"></div>
    <button class="btn btn-default btn-sm" data-agir="passkey-retirer" data-cible="${esc(k.id)}">Retirer</button>
  </div>`).join('') : '';
}

function pkMessage(texte, erreur){
  $('pk-err').style.display = erreur ? 'block' : 'none';
  $('pk-msg').style.display = (!erreur && texte) ? 'block' : 'none';
  (erreur ? $('pk-err') : $('pk-msg')).textContent = texte;
}

async function pkAjouter(){
  pkMessage('', false);
  try{
    const r=await fetch('/api/mon-compte/passkeys/options',{method:'POST'});
    const o=await r.json();
    if(!r.ok){ pkMessage(o.error||'Préparation impossible.', true); return; }
    /* Le serveur envoie du base64url ; l'API du navigateur veut des octets. */
    o.challenge=b64urlVersOctets(o.challenge);
    o.user.id=b64urlVersOctets(o.user.id);
    (o.excludeCredentials||[]).forEach(c=>{ c.id=b64urlVersOctets(c.id); });
    const cred=await navigator.credentials.create({publicKey:o});
    const envoi={
      id:cred.id, rawId:octetsVersB64url(cred.rawId), type:cred.type,
      response:{
        clientDataJSON:octetsVersB64url(cred.response.clientDataJSON),
        attestationObject:octetsVersB64url(cred.response.attestationObject),
      },
    };
    const r2=await fetch('/api/mon-compte/passkeys',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({credential:envoi, nom:$('pk-nom').value.trim() || nomDeCle()})});
    const d2=await r2.json();
    if(!r2.ok){ pkMessage(d2.error||'Enregistrement impossible.', true); return; }
    $('pk-nom').value='';
    await chargerPasskeys();
    pkMessage('', false);
    notifier(nomDeCle() + ' activé pour tes prochaines connexions.');
  }catch(e){
    /* Annulation de l'utilisateur comprise : ce n'est pas une panne. */
    pkMessage(e && e.name==='NotAllowedError'
      ? 'Enregistrement annule.' : ('Enregistrement impossible : ' + (e&&e.message||e)), true);
  }
}

function nomDeCle(){
  return nomDuGeste().replace(/^ton empreinte$/,'Empreinte').replace(/^une clé d'accès$/,'Clé d\'accès');
}

/* L'interrupteur. Allumer enregistre cet appareil ; eteindre retire TOUTES
   les cles du compte, apres confirmation. En cas d'echec ou de refus, il
   revient de lui-meme a l'etat reel (chargerPasskeys). */
async function pkBasculer(boite){
  pkMessage('', false);
  if(boite.checked){
    boite.disabled=true;
    await pkAjouter();
    boite.disabled=false;
    await chargerPasskeys();
    return;
  }
  if(!await demander('Désactiver ' + nomDeCle() + ' ?',
      "Les clés d'accès de ce compte sont retirées de tous tes appareils. Tu te connecteras "
      + "avec ton mot de passe et un code. Tu pourras le réactiver à tout moment.",
      'Désactiver', true)){
    boite.checked=true; return;
  }
  const r=await fetch('/api/mon-compte/passkeys',{method:'DELETE'});
  const d=await r.json().catch(()=>({}));
  if(!r.ok) pkMessage(d.error||'Désactivation impossible.', true);
  else notifier(nomDeCle() + ' désactivé.');
  chargerPasskeys();
}

async function pkSupprimer(id){
  if(!await demander("Retirer cette clé d'accès ?",
    "L'appareil qui la porte n'ouvrira plus ce compte. Tu peux en enregistrer une autre a tout moment.",
    'Retirer')) return;
  const r=await fetch('/api/mon-compte/passkeys/'+encodeURIComponent(id),{method:'DELETE'});
  const d=await r.json();
  if(!r.ok){ pkMessage(d.error||'Suppression impossible.', true); return; }
  chargerPasskeys();
}

/* ---------------- journal des acces ---------------- */
let activite={evenements:[], resume:{comptes:{}, apps:{}, echecs:0}};

/* Une date lisible sans reflechir : « il y a 3 min », « hier a 14 h ». Un
   horodatage complet demande a etre compare mentalement a l'heure qu'il est. */
function ilYA(ts){
  if(!ts) return 'jamais';
  const s=Math.max(0, Math.floor(Date.now()/1000)-ts);
  if(s<60) return "à l'instant";
  if(s<3600) return 'il y a '+Math.floor(s/60)+' min';
  if(s<86400) return 'il y a '+Math.floor(s/3600)+' h';
  if(s<2592000) return 'il y a '+Math.floor(s/86400)+' j';
  return new Date(ts*1000).toLocaleDateString();
}
function quandPrecis(ts){ return new Date(ts*1000).toLocaleString(); }

/* ---------------- carte de chaleur ---------------- */
/* Les paliers sont relatifs a la journee la plus chargee de la fenetre
   regardee : un seuil absolu ("4 ouvertures = fonce") afficherait une carte
   toute pale sur une installation calme et toute sombre sur une installation
   chargee. Ce qu'on veut lire, c'est le RELIEF. */
function carteNiveau(n, sommet){
  if(!n) return 0;
  if(sommet <= 1) return 4;
  const part = n / sommet;
  return part > .75 ? 4 : part > .5 ? 3 : part > .25 ? 2 : 1;
}

const CARTE_MOIS=['janv.','févr.','mars','avr.','mai','juin','juil.','août',
                  'sept.','oct.','nov.','déc.'];

/* Les annees offertes viennent du serveur : de la premiere utilisation a
   l'annee en cours. Les recalculer ici demanderait de connaitre tout
   l'historique, que la page n'a pas -- et une liste devinee finirait par
   proposer une annee vide ou en oublier une pleine. */
function peindreAnnees(d){
  const sel=$('carte-annee');
  const annees=(d.annees||[]).slice().sort((a,b)=>b-a);
  if(!annees.length) return;
  const voulue=String(d.annee||annees[0]);
  const deja=[...sel.options].map(o=>o.value).join('|');
  if(deja!==annees.map(String).join('|')){
    sel.innerHTML=annees.map(a=>`<option value="${a}">${a}</option>`).join('');
  }
  sel.value=voulue;
}

function carteJourCle(d){
  /* Date locale, et non toISOString() qui repasse en UTC : sur un fuseau a
     l'est de Greenwich, le 1er janvier redeviendrait le 31 decembre. */
  const m=String(d.getMonth()+1).padStart(2,'0'), j=String(d.getDate()).padStart(2,'0');
  return d.getFullYear()+'-'+m+'-'+j;
}

/* Les colonnes de la carte : une par semaine, du lundi qui precede le
   1er janvier au 31 decembre, chaque case portant son niveau et son titre.
   Rend aussi, pour chaque colonne, le mois auquel elle appartient. */
function carteColonnes(annee, parJour, sommet){
  const premierJanvier=new Date(annee,0,1); premierJanvier.setHours(12,0,0,0);
  const trenteEtUnDecembre=new Date(annee,11,31); trenteEtUnDecembre.setHours(12,0,0,0);
  const aujourdhui=new Date(); aujourdhui.setHours(12,0,0,0);
  /* On remonte au lundi qui precede le 1er janvier : sans cela, la premiere
     colonne serait tronquee et tous les jours de la semaine se decaleraient
     d'une ligne. */
  const depart=new Date(premierJanvier);
  depart.setDate(depart.getDate()-((premierJanvier.getDay()+6)%7));

  const cases=[]; const moisParColonne=[]; let colonne=[]; let moisCourant=null;
  for(let d0=new Date(depart); d0<=trenteEtUnDecembre; d0.setDate(d0.getDate()+1)){
    if(d0<premierJanvier){
      /* Avant le 1er janvier : la case garde la place sans rien affirmer. */
      colonne.push('<div class="carte-case vide"></div>');
    }else{
      if(moisCourant===null) moisCourant=d0.getMonth();
      const cle=carteJourCle(d0);
      const e=parJour[cle];
      const n=e?e.ouvertures:0;
      if(d0>aujourdhui){
        /* A VENIR. Une case grise dirait « aucune ouverture » d'un jour qui
           n'est pas arrive : c'est un mensonge, et il peint la fin de
           l'annee en creux. */
        colonne.push(`<div class="carte-case avenir" data-jour="${cle}" aria-label="${esc(d0.toLocaleDateString())} — à venir"></div>`);
      }else{
        const titre = n
          ? `${n} ouverture${n>1?'s':''} le ${d0.toLocaleDateString()}`
            + (e.apps>1 ? ` — ${e.apps} applications` : '')
          : `Aucune ouverture le ${d0.toLocaleDateString()}`;
        /* Le detail du jour vient au survol (voir montrerBulleCarte) : un
           title natif doublerait la bulle. Le resume reste pour un lecteur
           d'ecran. */
        colonne.push(`<div class="carte-case n${carteNiveau(n,sommet)}" data-jour="${cle}" aria-label="${esc(titre)}"></div>`);
      }
    }
    if(colonne.length===7){
      cases.push(colonne.join('')); colonne=[];
      /* La colonne appartient au mois de son premier jour DANS l'annee :
         c'est ce qui fait tomber « janv. » au-dessus de la colonne ou
         l'annee commence. */
      moisParColonne.push(moisCourant===null?0:moisCourant);
      moisCourant=null;
    }
  }
  if(colonne.length){
    const dernier=moisCourant===null?11:moisCourant;
    while(colonne.length<7) colonne.push('<div class="carte-case vide"></div>');
    cases.push(colonne.join('')); moisParColonne.push(dernier);
  }
  return {cases, moisParColonne};
}

/* Le cote d'une case et l'ecart entre deux cases, pour que les colonnes
   tiennent dans la largeur mesuree (voir peindreCarte). */
function carteTaille(large, colonnes){
  /* L'ECART COMPTE AUTANT QUE LA CASE. Cinquante-deux ecarts de 3 px font
     156 px a eux seuls : sur une carte etroite, ils mangent toute la place
     et la grille deborde meme avec des cases minuscules. On resserre donc
     l'ecart avant de rapetisser la case, et on s'arrete au premier couple
     qui tient -- en visant six pixels de cote, en dessous desquels une case
     ne se distingue plus de son voisin. */
  let cote = 11, ecart = 3;
  if(large > 0){
    let trouve = false;
    for(const e of [3,2,1,0]){
      const c = Math.floor((large - (colonnes-1)*e) / colonnes);
      if(c >= 6){ cote = c; ecart = e; trouve = true; break; }
    }
    if(!trouve){
      /* Vraiment trop etroit : on prend ce qui rentre, jusqu'a deux pixels.
         Une carte reduite se lit encore ; une carte coupee, non. */
      ecart = 0;
      cote = Math.max(2, Math.floor(large / colonnes));
    }
  }
  return {cote, ecart};
}

function peindreCarte(d){
  derniereCarte=d;
  const parJour={}; let total=0, sommet=0;
  for(const j of (d.jours||[])){
    parJour[j.jour]=j; total+=j.ouvertures;
    if(j.ouvertures>sommet) sommet=j.ouvertures;
  }
  /* UNE ANNEE CIVILE, du 1er janvier au 31 decembre. La fenetre glissante de
     366 jours commencait un jour quelconque : les mois se decalaient d'un
     cran chaque jour, et deux cartes prises a deux dates ne se comparaient
     pas. Une annee est fixe -- et elle se choisit. */
  const annee = d.annee || (new Date()).getFullYear();
  const {cases, moisParColonne} = carteColonnes(annee, parJour, sommet);

  /* LA LIGNE DES MOIS PREND TOUTE LA LARGEUR DE LA CARTE. Une etiquette par
     colonne laissait onze mois muets et un mot de dix pixels de large : on
     ne lisait rien. Chaque mois occupe donc exactement ses colonnes, et les
     douze se partagent la grille. */
  const mois=[];
  for(let i=0;i<moisParColonne.length;i++){
    const m=moisParColonne[i];
    if(i && m===moisParColonne[i-1]){ mois[mois.length-1].n++; continue; }
    mois.push({m:m, n:1});
  }
  const colonnes=cases.length;
  /* LA TAILLE D'UNE CASE SE CALCULE, elle ne se declare pas.
     Onze pixels par semaine debordaient d'une carte etroite, et la moitie de
     l'annee partait derriere une barre de defilement -- qu'on ne fait jamais
     glisser. Des colonnes en « 1fr » ne marchent pas davantage : la hauteur
     d'une case viendrait alors de sa largeur (aspect-ratio), qui vient de la
     largeur de la grille, qui vient de la hauteur... le navigateur tranche
     ce cercle comme il peut, et rendait des cases de 54 px.
     On mesure donc la place disponible et on en deduit un cote, en pixels,
     pose sur les colonnes ET sur les lignes. */
  const large = $('carte-zone').clientWidth
    || $('carte-zone').getBoundingClientRect().width;
  const {cote, ecart} = carteTaille(large, colonnes);
  const gabarit=`gap:${ecart}px;grid-template-columns:repeat(${colonnes},${cote}px);`
    + `grid-template-rows:repeat(7,${cote}px)`;
  const gabaritMois=`gap:${ecart}px;grid-template-columns:repeat(${colonnes},${cote}px)`;
  cacherBulleCarte();
  $('carte-zone').innerHTML =
    `<div class="carte-mois" style="${gabaritMois}">`
    + mois.map(x=>`<span style="grid-column:span ${x.n}">${CARTE_MOIS[x.m]}</span>`).join('')
    + `</div>`
    + `<div class="carte-grille" style="${gabarit}">${cases.join('')}</div>`;

  const jours=Object.keys(parJour).length;
  $('carte-total').textContent = total
    ? `${total} ouverture${total>1?'s':''} sur ${jours} jour${jours>1?'s':''}`
      + (sommet>1 ? ` — au plus ${sommet} dans une journée.` : '.')
    : "Aucune ouverture d'application sur la période.";

  /* D'ou viennent les chiffres. La base locale du panneau ne garde que les
     50 000 derniers evenements : une carte batie sur elle peut montrer des
     cases vides qui ne prouvent aucune inactivite, et le taire serait mentir
     par omission. */
  const src=$('carte-source');
  if(d.source==='postgres'){ src.style.display='none'; }
  else{
    src.style.display='block';
    src.innerHTML="Historique lu dans la <b>base locale</b> du panneau, qui garde "
      + "les 50 000 derniers événements : les jours vides les plus anciens peuvent "
      + "simplement avoir été oubliés. Postgres garde tout.";
  }
}

/* ---------------- le detail d'une case, au survol ----------------
   Vecu : « Ajoute la possibilite d'avoir du detail pour chaque case ». Le
   title natif ne disait que « 12 ouvertures -- 3 applications », apres une
   seconde d'attente, et jamais au doigt. La bulle dit le jour en toutes
   lettres, puis, en chiffres, qui a ouvert et quoi : par utilisateur et
   par application, du plus grand au plus petit.
   Elle est en position fixe, hors de la carte : la carte coupe ce qui
   deborde, et une case du bord la laisserait a moitie cachee. Au doigt, un
   appui sur une case l'ouvre ; un appui ailleurs ou un defilement la
   ferme. */
const CARTE_BULLE_LIGNES=5;
/* Une liste de la bulle : ses premieres lignes, nom et chiffre, puis le
   reste additionne. Des chiffres et pas des barres : on vient lire combien,
   pas comparer des longueurs. */
function listeBulleCarte(titre, lignes, nom){
  if(!lignes || !lignes.length) return '';
  const vues=lignes.slice(0, CARTE_BULLE_LIGNES), reste=lignes.slice(CARTE_BULLE_LIGNES);
  const nReste=reste.reduce((t,d)=>t+d.ouvertures, 0);
  return `<span class="cb-titre">${titre}</span><span class="cb-liste">`
    + vues.map(d=>`<span class="cb-ligne"><span class="cb-nom">${esc(nom(d))}</span><span class="cb-n">${d.ouvertures}</span></span>`).join('')
    + (reste.length ? `<span class="cb-ligne cb-reste"><span class="cb-nom">${reste.length} autre${reste.length>1?'s':''}</span><span class="cb-n">${nReste}</span></span>` : '')
    + '</span>';
}
function contenuBulleCarte(cle){
  const [a, m, j]=cle.split('-').map(Number);
  const date=new Date(a, m-1, j, 12);
  const titre=date.toLocaleDateString('fr-FR', {weekday:'long', day:'numeric', month:'long', year:'numeric'});
  const aujourdhui=new Date(); aujourdhui.setHours(23,59,59,999);
  if(date>aujourdhui) return `<b>${esc(titre)}</b><span class="cb-vide">À venir</span>`;
  const e=((derniereCarte && derniereCarte.jours) || []).find(x=>x.jour===cle);
  if(!e || !e.ouvertures) return `<b>${esc(titre)}</b><span class="cb-vide">Aucune ouverture</span>`;
  const nomApp=d=>{ const x=(apps||[]).find(y=>y.name===d.app); return d.app ? (x ? nomAffiche(x) : d.app) : 'Sans nom'; };
  const nomQui=d=>d.qui || 'Visiteur anonyme';
  return `<b>${esc(titre)}</b>
    <span class="cb-total">${e.ouvertures} ouverture${e.ouvertures>1?'s':''}</span>
    ${listeBulleCarte('Par utilisateur', e.par_qui, nomQui)}
    ${listeBulleCarte('Par application', e.par_app, nomApp)}`;
}
function montrerBulleCarte(c){
  let bulle=$('carte-bulle');
  if(!bulle){
    bulle=document.createElement('div'); bulle.id='carte-bulle'; bulle.className='carte-bulle';
    bulle.setAttribute('role', 'tooltip'); document.body.appendChild(bulle);
  }
  remplir(bulle, contenuBulleCarte(c.dataset.jour));
  bulle.hidden=false;
  /* Au-dessus de la case, centree, sans sortir de la fenetre ; en dessous
     quand il n'y a pas la place en haut. */
  const r=c.getBoundingClientRect(), l=bulle.offsetWidth, h=bulle.offsetHeight, marge=8;
  const x=Math.min(innerWidth-l-marge, Math.max(marge, r.left+r.width/2-l/2));
  const y=r.top-h-8 >= marge ? r.top-h-8 : r.bottom+8;
  bulle.style.left=Math.round(x)+'px'; bulle.style.top=Math.round(y)+'px';
  document.querySelectorAll('.carte-case.pointee').forEach(n=>n.classList.remove('pointee'));
  c.classList.add('pointee');
}
function cacherBulleCarte(){
  const bulle=$('carte-bulle'); if(bulle) bulle.hidden=true;
  document.querySelectorAll('.carte-case.pointee').forEach(n=>n.classList.remove('pointee'));
}
$('carte-zone').addEventListener('pointerover', e=>{
  const c=e.target.closest('.carte-case[data-jour]');
  if(c) montrerBulleCarte(c);
});
$('carte-zone').addEventListener('pointerout', e=>{
  if(e.pointerType!=='mouse') return;
  const c=e.target.closest('.carte-case[data-jour]');
  const vers=e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('.carte-case[data-jour]');
  if(c && !vers) cacherBulleCarte();
});
document.addEventListener('pointerdown', e=>{
  if(!e.target.closest || !e.target.closest('.carte-case[data-jour]')) cacherBulleCarte();
});
addEventListener('scroll', cacherBulleCarte, {capture:true, passive:true});

/* La derniere reponse, gardee pour repeindre a l'identique : la taille des
   cases depend de la largeur de la fenetre, et une carte calculee pour 1400
   px deborde a 900. Repeindre ne redemande rien au serveur. */
let derniereCarte=null, repeintureCarte=null;
window.addEventListener('resize', ()=>{
  if(!derniereCarte) return;
  clearTimeout(repeintureCarte);
  repeintureCarte=setTimeout(()=>peindreCarte(derniereCarte), 120);
});

async function chargerCarte(){
  /* La liste des personnes vient du resume du journal. Il n'est charge qu'a
     l'ouverture de la page Utilisateurs : sans cette ligne, le filtre
     n'aurait aucun nom tant qu'on n'y serait pas passe. */
  if(!Object.keys(activite.resume.comptes||{}).length) await chargerActivite();
  /* Les deux listes deroulantes suivent ce qui existe, sans perdre le choix
     en cours. */
  const selQui=$('carte-qui'), selApp=$('carte-app');
  const gardeQui=selQui.value, gardeApp=selApp.value;
  const comptes=Object.keys((activite&&activite.resume&&activite.resume.comptes)||{})
    .filter(n=>n).sort();
  selQui.innerHTML='<option value="">Tout le monde</option>'
    + comptes.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
  selQui.value = comptes.includes(gardeQui) ? gardeQui : '';
  const noms=(apps||[]).map(a=>a.name).sort();
  selApp.innerHTML='<option value="">Toutes les applications</option>'
    + noms.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
  selApp.value = noms.includes(gardeApp) ? gardeApp : '';

  const p=new URLSearchParams();
  if(selQui.value) p.set('qui', selQui.value);
  if(selApp.value) p.set('app', selApp.value);
  /* L'annee choisie tient dans la page : le serveur renvoie la liste des
     annees proposees a chaque reponse, et c'est elle qui fait autorite. */
  if($('carte-annee').value) p.set('annee', $('carte-annee').value);
  try{
    const d=await (await fetch('/api/activite/carte?'+p.toString())).json();
    peindreAnnees(d);
    peindreCarte(d);
  }catch(e){
    $('carte-zone').innerHTML='<div class="hint">Carte indisponible.</div>';
  }
}

async function chargerActivite(){
  try{ activite=await (await fetch('/api/activite')).json(); }catch(e){ return; }
  peindreConnexions();
  peindreComptesActivite();
}
function ligneActivite(e){
  const qui = e.qui ? esc(e.qui) : 'visiteur anonyme';
  let quoi;
  if(e.genre==='connexion'){
    quoi = 's\'est connecte' + (e.role==='admin' ? ' (administrateur)' : '');
    /* Par quel moyen : un mot de passe vole ne produit pas la meme ligne
       qu'une cle d'acces, et c'est ce qu'on regarde en cas de doute. */
    if(e.moyen) quoi += ' par ' + esc(e.moyen);
  }
  else if(e.genre==='echec') quoi = '<b style="color:var(--err)">connexion refusée</b> — ' + esc(e.motif||'');
  else if(e.genre==='ouverture') quoi = 'a ouvert <b>' + esc(e.app||'') + '</b>';
  /* Les cles d'acces ajoutees ou retirees passaient par la branche des
     ouvertures : le journal affichait « a ouvert » suivi de rien. */
  else if(e.genre==='passkey') quoi = "clé d'accès — " + esc(e.action||'');
  else quoi = esc(e.genre||'');
  return `<tr>
    <td class="act-quand" title="${esc(quandPrecis(e.ts))}">${esc(ilYA(e.ts))}</td>
    <td class="act-qui">${qui}</td>
    <td class="act-quoi">${quoi}</td>
    <td class="act-ip">${esc(e.ip||'—')}</td></tr>`;
}
/* Le journal, en tableau. Vide, il rend la phrase qui dit ce qui le
   remplira -- pas un tableau a une seule case. */
function tableauActivite(evenements, vide){
  if(!evenements.length) return `<div class="act-vide">${vide}</div>`;
  return `<div class="tbl-wrap"><div class="tbl-scroll"><table class="tbl tbl-journal">
    <thead><tr><th>Quand</th><th>Qui</th><th>Evenement</th><th>Adresse</th></tr></thead>
    <tbody>${evenements.map(ligneActivite).join('')}</tbody></table></div></div>`;
}
/* Le journal des connexions, page par page.

   C'est LUI qui est long : il grandit a chaque visite, la ou une liste de
   comptes tient a l'ecran. Le reglage 25/50 etait pose sur les comptes, ou
   il ne servait a rien ; il est ici.

   Avant, une coupe seche a 40 lignes -- ni reglable, ni annoncee, et les
   lignes au-dela etaient simplement invisibles. */
let acPage=1;

/* Memorise par navigateur : c'est une preference d'affichage, pas un reglage
   du serveur. Une valeur illisible ou un stockage refuse (navigation privee)
   ne doit pas casser la page -- d'ou le try. */
function acParPage(){
  try{
    const v=parseInt(localStorage.getItem('codelab.journal.taille'), 10);
    if(v===10 || v===25 || v===50) return v;
  }catch(e){}
  return 10;
}

/* CE QU'ON REGARDE. Chaque ouverture d'application s'ajoutait aux
   connexions, et la liste debordait de lignes qu'on ne cherchait pas. Par
   defaut : qui est entre, et qui a ete refuse -- la question de securite.
   Les ouvertures sont a un choix. Memorise comme la taille. */
const AC_GENRES={connexions:['connexion','echec','refus'], ouvertures:['ouverture'], tout:null};
function acGenreCourant(){
  try{
    const v=localStorage.getItem('codelab.journal.genre');
    if(v && v in AC_GENRES) return v;
  }catch(e){}
  return 'connexions';
}
function acGenre(){
  try{ localStorage.setItem('codelab.journal.genre', $('ac-genre').value); }catch(e){}
  acPage=1;
  peindreConnexions();
}
/* Au plus deux cents evenements dans la page : au-dela, c'est une recherche,
   et le journal complet reste dans la base (et dans Postgres). */
const AC_MAX=200;

function acTaille(){
  const v=$('ac-taille').value;
  try{ localStorage.setItem('codelab.journal.taille', v); }catch(e){}
  acPage=1;
  peindreConnexions();
}

function acAllerA(n){
  acPage=n;
  peindreConnexions();
  $('ac-connexions').scrollIntoView({block:'nearest', behavior:'smooth'});
}

function peindreConnexions(){
  const genres=AC_GENRES[acGenreCourant()];
  const tous=(activite.evenements||[])
    .filter(e=>!genres || genres.includes(e.genre)).slice(0, AC_MAX);
  const gs=$('ac-genre'); if(gs) gs.value=acGenreCourant();
  const taille=acParPage();
  const pages=Math.max(1, Math.ceil(tous.length/taille));
  /* Rester sur une page qui n'existe plus -- le journal a ete vide, ou la
     taille a double -- donnerait un tableau vide et ferait croire a une
     panne. */
  if(acPage>pages) acPage=pages;
  if(acPage<1) acPage=1;
  const debut=(acPage-1)*taille;
  const tranche=tous.slice(debut, debut+taille);
  $('ac-connexions').innerHTML = tableauActivite(tranche,
    'Rien encore. Le journal se remplit à la première connexion.');

  const sel=$('ac-taille'); if(sel) sel.value=String(taille);
  const barre=$('ac-pagination'); if(!barre) return;
  barre.style.display = pages>1 ? 'flex' : 'none';
  $('ac-prec').disabled = acPage<=1;
  $('ac-suiv').disabled = acPage>=pages;
  $('ac-compte').textContent =
    `${debut+1}\u2013${debut+tranche.length} de ${tous.length} — page ${acPage} sur ${pages}`;
}
/* La derniere connexion de chaque compte, sous son nom : c'est ce qu'on
   cherche en ouvrant cette page, avant meme le journal complet. */
function peindreComptesActivite(){
  const r=(activite.resume||{}).comptes||{};
  document.querySelectorAll('#us-liste [data-activite]').forEach(n=>{
    const c=r[n.dataset.activite];
    /* c.derniere, SANS accent : c'est le nom du champ renvoye par
       /api/activite. En accentuant les textes de l'interface, l'accent
       avait ete pose ici aussi -- la lecture valait alors undefined,
       ilYA(undefined) repond "jamais", et tout compte deja connecte
       s'affichait comme ne s'etant jamais connecte. Un texte accentue se
       lit mieux ; un identifiant accentue ne correspond plus a rien, et se
       tait. Les noms de champs ne s'accentuent pas. */
    n.textContent = c && c.derniere
      ? ` — dernière connexion ${ilYA(c.derniere)}`
      : ' — jamais connecté';
  });
}

/* L'activite d'une application, dans sa fiche. */
function peindreActiviteFiche(){
  const a=((activite.resume||{}).apps||{})[ficheNom] || {ouvertures:0, derniere:0, qui:{}};
  $('ac-total').textContent=a.ouvertures;
  const gens=Object.keys(a.qui||{});
  $('ac-gens').textContent=gens.length;
  $('ac-derniere').textContent = a.derniere
    ? 'Dernière ouverture ' + ilYA(a.derniere) + '.'
    : 'Jamais ouverte depuis que le journal existe.';
  const paires=gens.map(q=>[q, a.qui[q]]).sort((x,y)=>y[1]-x[1]);
  $('ac-repartition').innerHTML = paires.length
    ? paires.map(([q,n])=>`<div class="kv"><span class="k">${q?esc(q):'Visiteurs anonymes'}</span>
        <span class="v">${n}</span></div>`).join('')
    : '<div class="act-vide">Personne pour l\'instant.</div>';
  const propres=(activite.evenements||[]).filter(e=>e.genre==='ouverture' && e.app===ficheNom).slice(0,20);
  $('ac-liste').innerHTML = tableauActivite(propres, 'Aucune ouverture enregistrée.');
}
