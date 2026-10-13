/* CodeLab -- panneau/03-widgets.js : Les widgets du hub, et le mode Modifier (deplacer, retirer, changer de taille).
   Une partie du script du panneau, assemblee par app.py (PANNEAU_MODULES)
   dans l'ordre des numeros : un seul script, une seule portee. */
/* ---- les widgets : peu, et tous sur le hub ou ses applications ---- */
function widgetApercu(a, taille, surtitre, enDirect=true){
  if(!a) return `<div class="bw-corps"><div class="bw-titre">${esc(surtitre)}</div>
    <p class="bw-vide">Ouvre une application : elle s'affichera ici.</p></div>`;
  /* L'APERCU est la page de l'application elle-meme, reduite et inerte : on
     la reconnait d'un coup d'oeil, et un clic l'ouvre en grand. Arretee, elle
     n'a rien a montrer : son icone la remplace. */
  /* Un compte utilisateur ne connait pas l'etat : l'apercu montre toujours
     la page (la page neutre si l'application ne repond pas). */
  /* « A la une », l'application qui a une video de presentation la joue en
     boucle, sans le son, comme une bande-annonce (videoALaUne). */
  const video = !enDirect && videoALaUne(a);
  const apercu = enDirect && (a.running || !estAdmin) && !a.externe && !a.interne
    ? `<iframe src="${esc(urlApplication(a.name))}" title="Aperçu de ${esc(nomAffiche(a))}"
         loading="lazy" tabindex="-1" aria-hidden="true" referrerpolicy="no-referrer"
         sandbox="allow-scripts allow-same-origin allow-forms"></iframe>`
    : `<img class="bw-une-icone" src="${icone(a.name)}" alt="">${video}`;
  return `<div class="bw-apercu${enDirect ? '' : ' sans-direct'}${video ? ' en-video' : ''}"${enDirect ? '' : ` data-teinte="${esc(a.name)}"`}
      data-garder="apercu-${esc(a.name)}-${a.running?1:0}-${taille}-${enDirect?1:0}-${video ? a.presentation.maj : 0}">${apercu}</div>
    <a class="bw-une-pied" href="${lienOuverture(a)}" data-app="${esc(a.name)}">
      <span class="bw-sur">${esc(surtitre)}</span>
      <span class="bw-une-ligne"><img src="${icone(a.name)}" alt=""><span class="bw-une-nom">${esc(nomAffiche(a))}</span>${badgeMaj(a)}</span></a>`;
}
/* La bande-annonce : la video de presentation, muette, en boucle, lancee
   seule (muted + playsinline : iOS l'accepte). Pas quand l'appareil demande
   moins d'animations ou economise ses donnees : l'icone reste. */
function videoALaUne(a){
  if(!a.presentation) return '';
  try{
    if(matchMedia('(prefers-reduced-motion: reduce)').matches) return '';
    if(navigator.connection && navigator.connection.saveData) return '';
  }catch(e){}
  const base='/api/presentations/'+encodeURIComponent(a.name)+'/', v=a.presentation.maj;
  return `<video class="bw-une-video" src="${base}video?v=${v}"${a.presentation.affiche ? ` poster="${base}affiche?v=${v}"` : ''}
    muted loop autoplay playsinline preload="metadata" disablepictureinpicture tabindex="-1" aria-hidden="true"></video>`;
}
/* LES COULEURS DE L'APPLICATION. Le widget « a la une » prend la couleur
   dominante de son icone au lieu de l'accent du panneau : chaque application
   a son ambiance. Mesuree une fois par icone (un canevas de 32 px), puis
   gardee : chaque repeint la repose sans attendre. Le texte passe au blanc ou
   au presque-noir, celui des deux qui contraste le plus. */
const teintesIcones=new Map();
function luminance([r,g,b]){
  const c=v=>{ v/=255; return v<=.03928 ? v/12.92 : Math.pow((v+.055)/1.055, 2.4); };
  return .2126*c(r)+.7152*c(g)+.0722*c(b);
}
function teinteDominante(img){
  const n=32, cv=document.createElement('canvas'); cv.width=cv.height=n;
  const cx=cv.getContext('2d', {willReadFrequently:true});
  cx.drawImage(img, 0, 0, n, n);
  const px=cx.getImageData(0, 0, n, n).data;
  /* Douze familles de teinte, chaque pixel pese sa saturation : un fond
     blanc ou un contour noir ne l'emporte pas sur la couleur du logo. */
  const familles=Array.from({length:12}, ()=>({poids:0, r:0, g:0, b:0}));
  for(let i=0; i<px.length; i+=4){
    if(px[i+3]<200) continue;
    const r=px[i], g=px[i+1], b=px[i+2], max=Math.max(r,g,b), min=Math.min(r,g,b);
    const sat = max ? (max-min)/max : 0;
    if(sat<.25 || max<40) continue;
    let h = max===min ? 0 : max===r ? (g-b)/(max-min) : max===g ? 2+(b-r)/(max-min) : 4+(r-g)/(max-min);
    h=((h*60)+360)%360;
    const f=familles[Math.floor(h/30)%12], w=sat*sat;
    f.poids+=w; f.r+=r*w; f.g+=g*w; f.b+=b*w;
  }
  const f=familles.reduce((x,y)=>y.poids>x.poids ? y : x);
  if(f.poids<1) return null;               // une icone en gris : l'accent reste
  return [f.r/f.poids, f.g/f.poids, f.b/f.poids].map(Math.round);
}
function poserTeinte(bloc, t){
  if(!t){ bloc.style.removeProperty('--une-fond'); bloc.style.removeProperty('--une-fond-h'); bloc.style.removeProperty('--une-txt'); return; }
  const [r,g,b]=t, L=luminance(t);
  const clair = (1.05)/(L+.05) >= (L+.05)/(luminance([16,20,26])+.05);
  const h=t.map(v=>Math.round(v*.88));
  bloc.style.setProperty('--une-fond', `rgb(${r},${g},${b})`);
  bloc.style.setProperty('--une-fond-h', `rgb(${h.join(',')})`);
  bloc.style.setProperty('--une-txt', clair ? '#ffffff' : '#10141a');
}
function teinterALaUne(racine){
  racine.querySelectorAll('[data-teinte]').forEach(n=>{
    const bloc=n.closest('.bw'), nom=n.dataset.teinte;
    if(!bloc) return;
    if(teintesIcones.has(nom)){ poserTeinte(bloc, teintesIcones.get(nom)); return; }
    const img=new Image();
    img.onload=()=>{
      let t=null; try{ t=teinteDominante(img); }catch(e){}
      teintesIcones.set(nom, t);
      const ici=racine.querySelector(`[data-teinte="${CSS.escape(nom)}"]`);
      if(ici && ici.closest('.bw')) poserTeinte(ici.closest('.bw'), t);
    };
    img.onerror=()=>teintesIcones.set(nom, null);
    img.src=icone(nom);
  });
}
function widgetEtat(taille){
  const liste=hubApps.concat(hubServices);
  const compte=c=>liste.filter(a=>etatApp(a)[0]===c).length;
  const enLigne=compte('on'), arretees=compte('off');
  /* LE BILAN DIT TOUT CE QUI NE VA PAS, pas seulement le premier souci :
     « Tout répond » s'affichait alors que deux services ne repondaient pas. */
  const soucis=liste.filter(a=>['err','warn'].includes(etatApp(a)[0]));
  const nErr=compte('err'), nWarn=soucis.length-nErr;
  const bilan=[nErr ? `${nErr} en erreur` : '', nWarn ? `${nWarn} à surveiller` : ''].filter(Boolean).join(' · ');
  const lien = soucis.length===1
    ? `<a href="${lienOuverture(soucis[0])}">${esc(nomAffiche(soucis[0]))} : ${esc(etatApp(soucis[0])[1].toLowerCase())}</a>`
    : soucis.length ? `<a href="/applications">${esc(bilan)}</a>` : '';
  return `<div class="bw-titre">État</div>
    <div><div class="bento-chiffre">${chiffre('etat-en-ligne', enLigne)}<span>/${liste.length}</span></div><p>en ligne${arretees ? ` · ${arretees} arrêtée${arretees>1?'s':''}` : ''}</p></div>
    <div class="bento-pied"><div class="bento-barres" aria-hidden="true" data-apparait="etat-barres">${liste.map((a,i)=>`<i class="${etatApp(a)[0]}" style="--i:${i}"></i>`).join('')}</div>
    ${lien || '<p>Tout répond.</p>'}</div>`;
}
/* Les sauvegardes, pour l'administrateur : la derniere, et l'alerte tant
   qu'aucune copie n'est sortie du serveur. Le detail est dans Parametres >
   Securite. */
let hubSauvegardes=null;
function widgetSauvegardes(taille){
  const d=hubSauvegardes;
  const lien=`<a class="bento-bouton" href="/parametres/securite">Voir ${SVG_FLECHE}</a>`;
  if(!d || !d.actives) return `<div class="bw-titre">Sauvegardes</div><p class="bw-vide">Désactivées sur ce serveur.</p>${taille!=='petit' ? lien : ''}`;
  if(!d.derniere) return `<div class="bw-titre">Sauvegardes</div><p class="bw-vide">Aucune pour le moment : la première arrive bientôt.</p>`;
  return `<div class="bw-titre">Sauvegardes</div>
    <div><div class="sg-widget-quand">${esc(ilYA(d.derniere.date))}</div>
    <p>${esc(tailleLisible(d.derniere.taille))}${d.alerte_hors_serveur ? ' · <b class="sg-widget-alerte">sur ce serveur seulement</b>' : ''}</p></div>
    ${taille!=='petit' ? lien : ''}`;
}
/* ---------------- le mouvement ----------------
   LES CHIFFRES DEFILENT, LES GRAPHIQUES SE DESSINENT. Un nombre porte
   data-anim="nombre", une jauge data-anim="anneau" : chacun a sa cle
   (data-cle) et sa valeur finale (data-val), deja ecrite dans le HTML --
   sans script, ou quand l'appareil demande moins de mouvement, la valeur
   juste s'affiche d'emblee. La page retient la derniere valeur montree pour
   chaque cle : un repeint qui ne change rien ne rejoue rien, et une valeur
   qui change part de l'ancienne, pas de zero.
   Un bloc data-apparait (barres, courbe) ne joue son entree qu'a sa
   premiere apparition : le hub se repeint toutes les dix secondes, et un
   graphique qui se redessinerait a chaque tour fatiguerait l'oeil. */
const moinsDeMouvement = () => { try{ return matchMedia('(prefers-reduced-motion: reduce)').matches; }catch(e){ return false; } };
const valeursVues=new Map(), dejaApparus=new Set();
const nombreFr = (v, dec=0) => Number(v).toLocaleString('fr-FR', {minimumFractionDigits:dec, maximumFractionDigits:dec});
const chiffre = (cle, v, dec=0) =>
  `<span data-anim="nombre" data-cle="${esc(cle)}" data-val="${Number(v)||0}" data-dec="${dec}">${nombreFr(Number(v)||0, dec)}</span>`;
function poserAnim(n, v){
  if(n.dataset.anim==='nombre') n.textContent=nombreFr(v, +(n.dataset.dec||0));
  else if(n.dataset.anim==='anneau') n.style.strokeDashoffset=String(1 - Math.max(0, Math.min(100, v))/100);
  else if(n.dataset.anim==='largeur') n.style.width=Math.max(0, Math.min(100, v))+'%';
}
function animerChiffres(racine){
  if(!racine) return;
  const sobre=moinsDeMouvement();
  racine.querySelectorAll('[data-apparait]').forEach(n=>{
    const cle=n.dataset.apparait;
    if(dejaApparus.has(cle)) return;
    dejaApparus.add(cle);
    if(!sobre) n.classList.add('apparait');
  });
  racine.querySelectorAll('[data-anim]').forEach(n=>{
    const cle=n.dataset.cle, fin=parseFloat(n.dataset.val);
    if(!cle || !isFinite(fin)) return;
    const avant = valeursVues.has(cle) ? valeursVues.get(cle) : 0;
    valeursVues.set(cle, fin);
    /* Deja en route vers cette valeur : on ne coupe pas son elan. */
    if(n._vers===fin) return;
    if(sobre || avant===fin){ poserAnim(n, fin); return; }
    n._vers=fin;
    const debut=performance.now(), duree=n.dataset.anim==='nombre' ? 1200 : 1400;
    poserAnim(n, avant);
    const pas=t=>{
      if(n._vers!==fin) return;
      const k=Math.min(1, (t-debut)/duree), e=1-Math.pow(1-k, 4);
      poserAnim(n, avant+(fin-avant)*e);
      if(k<1) requestAnimationFrame(pas); else n._vers=undefined;
    };
    requestAnimationFrame(pas);
  });
}

/* ---------------- le pouls : Serveur et Activite ----------------
   Deux widgets de l'administrateur, nourris par /api/hub/pouls. Leur corps
   n'est PAS dans le HTML du hub : la tuile n'y porte qu'un emplacement
   garde (data-garder), rempli a part. Sans cela, le processeur qui change
   d'un point toutes les dix secondes repeignait le hub entier. */
let hubPouls={}, poulsEnCours=false, poulsActiviteLe=0, poulsEssaiLe=0;
function widgetsPoses(){ return new Set((hubDisposition||[]).filter(e=>e.t==='w').map(e=>e.id)); }
async function chargerPouls(){
  if(!estAdmin || poulsEnCours) return;
  const poses=widgetsPoses(), quoi=[];
  if(poses.has('serveur')) quoi.push('serveur');
  if(poses.has('activite') && Date.now()-poulsActiviteLe > 60000) quoi.push('activite');
  if(!quoi.length) return;
  poulsEnCours=true; poulsEssaiLe=Date.now();
  try{
    const r=await fetch('/api/hub/pouls?quoi='+quoi.join(','));
    if(!r.ok) return;
    const d=await r.json();
    if(d.activite) poulsActiviteLe=Date.now();
    Object.assign(hubPouls, d);
  }catch(e){ return; }
  finally{ poulsEnCours=false; }
  peindrePouls();
}
const emplacementPouls = (id, taille) =>
  `<div class="bw-pouls" data-pouls="${id}" data-taille="${taille}" data-garder="pouls-${id}-${taille}"></div>`;
function peindrePouls(){
  let manque=false;
  document.querySelectorAll('#hub-liste [data-pouls]').forEach(n=>{
    const id=n.dataset.pouls, taille=n.dataset.taille, d=hubPouls[id];
    if(!d){ manque=true; remplir(n, `<div class="bw-titre">${id==='serveur' ? 'Serveur' : 'Activité'}</div><p class="bw-vide">Mesure en cours…</p>`); return; }
    remplir(n, id==='serveur' ? corpsServeur(d, taille) : corpsActivite(d, taille));
    animerChiffres(n);
  });
  if(manque && Date.now()-poulsEssaiLe > 10000) chargerPouls();
}
/* AU-DELA DE 80 %, A SURVEILLER ; AU-DELA DE 92 %, SATURE. La jauge change
   de couleur ET le dit en toutes lettres : jamais la couleur seule. */
const niveauCharge = v => v>=92 ? ['err','saturé'] : v>=80 ? ['warn','élevé'] : ['',''];
function jauge(cle, v, nom, detail){
  const p=Math.round(Math.max(0, Math.min(100, v||0)));
  const [c, mot]=niveauCharge(p);
  return `<div class="jauge ${c}" role="img" aria-label="${esc(nom)} : ${p} %${mot ? ', '+mot : ''}">
    <div class="jauge-cadran"><svg viewBox="0 0 48 48" aria-hidden="true"><circle class="jauge-rail" cx="24" cy="24" r="20"/>
      <circle class="jauge-arc" cx="24" cy="24" r="20" pathLength="1" stroke-dasharray="1"
        stroke-dashoffset="${1-p/100}" data-anim="anneau" data-cle="${esc(cle)}-arc" data-val="${p}"/></svg>
      <div class="jauge-val">${chiffre(cle, p)}<small>%</small></div></div>
    <div class="jauge-nom">${esc(nom)}${mot ? ` <b>${mot}</b>` : ''}</div>
    ${detail ? `<div class="jauge-detail">${esc(detail)}</div>` : ''}</div>`;
}
function dureeCourte(s){
  if(s==null) return '';
  const j=Math.floor(s/86400), h=Math.floor(s%86400/3600), m=Math.floor(s%3600/60);
  return j ? j+' j '+h+' h' : h ? h+' h '+m+' min' : m+' min';
}
function corpsServeur(d, taille){
  const mem=d.memoire||{}, dq=d.disque;
  const allume=d.allume_depuis!=null ? `<span class="bw-sous">allumé depuis ${dureeCourte(d.allume_depuis)}</span>` : '';
  const tete=`<div class="bw-tete"><div class="bw-titre"><i class="pouls-point" aria-hidden="true"></i>Serveur</div>${taille!=='petit' ? allume : ''}</div>`;
  if(taille==='petit'){
    return tete + jauge('srv-cpu', d.cpu, 'Processeur')
      + `<p class="bw-ligne">Mémoire <b>${chiffre('srv-mem-p', Math.round(mem.pct||0))} %</b>${dq ? ` · Disque <b>${chiffre('srv-dq-p', Math.round(dq.pct))} %</b>` : ''}</p>`;
  }
  const jauges=`<div class="jauges">${jauge('srv-cpu', d.cpu, 'Processeur', d.coeurs+' cœur'+(d.coeurs>1?'s':''))}
    ${jauge('srv-mem', mem.pct, 'Mémoire', mem.total ? tailleLisible(mem.utilise)+' / '+tailleLisible(mem.total) : '')}
    ${dq ? jauge('srv-dq', dq.pct, 'Disque', tailleLisible(dq.utilise)+' / '+tailleLisible(dq.total)) : ''}</div>`;
  return tete + jauges + (taille==='grand' ? courbeServeur(d.historique||[]) : '');
}
/* LA COURBE DES DIX DERNIERES MINUTES : processeur et memoire sur la meme
   echelle (0 a 100 %), donc un seul axe. Le processeur porte l'accent et
   une aire ; la memoire, un trait neutre. Une legende nomme les deux, et le
   survol donne la valeur exacte. */
function courbeServeur(h){
  if(h.length<2) return `<p class="bw-vide">La courbe se dessine : un point toutes les dix secondes.</p>`;
  const W=300, H=90, n=h.length;
  const x=i=>(i/(n-1))*W, y=v=>H-2-(Math.max(0, Math.min(100, v))/100)*(H-4);
  const ligne=k=>h.map((p,i)=>(i?'L':'M')+x(i).toFixed(1)+' '+y(p[k]).toFixed(1)).join('');
  const aire=ligne('cpu')+`L${W} ${H}L0 ${H}Z`;
  const pts=esc(JSON.stringify(h.map(p=>[p.t, p.cpu, p.mem])));
  return `<div class="courbe" data-points="${pts}" data-apparait="srv-courbe">
    <div class="courbe-legende"><span><i class="l-cpu"></i>Processeur</span><span><i class="l-mem"></i>Mémoire</span>
      <span class="courbe-duree">${Math.round((h[n-1].t-h[0].t)/60)||1} dernières min</span></div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id="courbe-degrade" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="var(--accent)" stop-opacity=".32"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
      <line class="courbe-grille" x1="0" x2="${W}" y1="${y(50)}" y2="${y(50)}"/>
      <g class="courbe-traits"><path class="courbe-aire" d="${aire}"/>
      <path class="courbe-mem" d="${ligne('mem')}"/>
      <path class="courbe-cpu" d="${ligne('cpu')}"/></g>
      <line class="courbe-curseur" x1="0" x2="0" y1="0" y2="${H}"/>
    </svg><div class="courbe-bulle" hidden></div></div>`;
}
const JOURS_COURTS=['dim.','lun.','mar.','mer.','jeu.','ven.','sam.'];
function corpsActivite(d, taille){
  const jours=d.jours||[], total=d.total||0, avant=d.precedent||0;
  const max=Math.max(1, ...jours.map(j=>j.ouvertures));
  const tendance = avant ? Math.round((total-avant)/avant*100) : null;
  const badge = tendance===null ? '' : `<span class="tendance ${tendance>=0 ? 'hausse' : 'baisse'}"
      title="Par rapport aux ${jours.length} jours d'avant (${avant})">${tendance>=0 ? '▲' : '▼'} ${chiffre('act-tendance', Math.abs(tendance))} %</span>`;
  const barres=jours.map((j,i)=>{
    const dt=new Date(j.jour+'T12:00:00');
    const nom=JOURS_COURTS[dt.getDay()]+' '+dt.getDate();
    return `<i style="--h:${(j.ouvertures/max).toFixed(3)};--i:${i}" class="${i===jours.length-1 ? 'auj' : ''}${j.ouvertures ? '' : ' nul'}"
      title="${esc(nom)} : ${j.ouvertures} ouverture${j.ouvertures>1?'s':''}"></i>`;
  }).join('');
  const debut=jours[0] ? new Date(jours[0].jour+'T12:00:00') : null;
  const axe = debut ? `<div class="barres-axe"><span>${debut.getDate()} ${debut.toLocaleDateString('fr-FR',{month:'short'})}</span><span>aujourd'hui</span></div>` : '';
  const apps = taille==='grand' && (d.apps||[]).length ? `<div class="act-apps">${d.apps.map((a,i)=>`
      <a class="act-app" href="${appDuHub(a.name) ? lienOuverture(appDuHub(a.name)) : '/applications/'+encodeURIComponent(a.name)}" data-app="${esc(a.name)}"><img src="${icone(a.name)}" alt="">
        <span class="act-app-nom">${esc(a.titre||a.name)}</span><b>${chiffre('act-app-'+a.name, a.ouvertures)}</b>
        <span class="act-app-rail"><i data-anim="largeur" data-cle="act-rail-${esc(a.name)}" data-val="${Math.round(a.ouvertures/d.apps[0].ouvertures*100)}"
          style="width:${Math.round(a.ouvertures/d.apps[0].ouvertures*100)}%"></i></span></a>`).join('')}</div>` : '';
  return `<div class="bw-tete"><div class="bw-titre">Activité</div>${badge}</div>
    <div><div class="bento-chiffre act-chiffre">${chiffre('act-total', total)}</div>
      <p class="bw-ligne">ouverture${total>1?'s':''} en ${jours.length} jours</p></div>
    ${total ? `<div class="barres-jours" data-apparait="act-barres" role="img"
      aria-label="Ouvertures par jour, sur ${jours.length} jours : jusqu'à ${max} par jour">${barres}</div>${axe}` : '<p class="bw-vide">Aucune ouverture ces deux dernières semaines.</p>'}
    ${apps}`;
}
/* Le survol de la courbe : un curseur vertical et la valeur du point le
   plus proche. */
document.addEventListener('pointermove', e=>{
  const c=e.target.closest && e.target.closest('.courbe');
  if(!c) return;
  const svg=c.querySelector('svg'), bulle=c.querySelector('.courbe-bulle'), curseur=c.querySelector('.courbe-curseur');
  let pts; try{ pts=JSON.parse(c.dataset.points); }catch(err){ return; }
  const r=svg.getBoundingClientRect(); if(!r.width || pts.length<2) return;
  const k=Math.max(0, Math.min(1, (e.clientX-r.left)/r.width)), i=Math.round(k*(pts.length-1)), p=pts[i];
  const xs=i/(pts.length-1)*300;
  curseur.setAttribute('x1', xs); curseur.setAttribute('x2', xs); c.classList.add('survolee');
  bulle.hidden=false;
  bulle.innerHTML=`<b>${new Date(p[0]*1000).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'})}</b>
    <span><i class="l-cpu"></i>Processeur ${nombreFr(p[1],1)} %</span><span><i class="l-mem"></i>Mémoire ${nombreFr(p[2],1)} %</span>`;
  const bx=Math.min(r.width-bulle.offsetWidth, Math.max(0, e.clientX-r.left-bulle.offsetWidth/2));
  bulle.style.left=bx+'px';
});
document.addEventListener('pointerout', e=>{
  const c=e.target.closest && e.target.closest('.courbe');
  if(!c || (e.relatedTarget && c.contains(e.relatedTarget))) return;
  c.classList.remove('survolee'); const b=c.querySelector('.courbe-bulle'); if(b) b.hidden=true;
});

function widgetMessages(taille){
  const d=hubServices.find(s=>s.name==='messages');
  const n=d ? (d.non_lues||0) : 0;
  return `<div class="bw-titre">Messages</div>
    <div><div class="bento-chiffre">${chiffre('messages', n)}</div><p>${n ? (n>1 ? 'nouveaux messages' : 'nouveau message') : 'Rien de nouveau.'}</p></div>
    ${taille!=='petit' ? `<a class="bento-bouton" href="/messages">Ouvrir ${SVG_FLECHE}</a>` : ''}`;
}
function widgetNouveautes(taille){
  const maj=hubApps.filter(a=>a.nouvelle_version);
  if(!maj.length) return `<div class="bw-titre">Nouvelles versions</div><p class="bw-vide">Tout est à jour.</p>`;
  return `<div class="bw-titre">Nouvelles versions</div>
    ${taille==='petit' ? `<div><div class="bento-chiffre">${chiffre('nouveautes', maj.length)}</div><p>application${maj.length>1?'s':''} à redécouvrir</p></div>`
      : `<div class="bw-liste">${maj.slice(0,4).map(a=>`<a href="${lienOuverture(a)}" data-app="${esc(a.name)}"><img src="${icone(a.name)}" alt=""><span>${esc(nomAffiche(a))}</span></a>`).join('')}</div>`}`;
}
/* Les trois sujets d'un message : ce que l'administrateur en fera depend
   d'abord de cela -- une amelioration du hub, un nouveau projet, ou le
   travail sur une application qui existe deja. */
const SUJETS_MESSAGE=[['hub','Le hub'],['idee','Nouveau projet'],['application','Une application']];
let hubMessageCible='hub', hubMessageApp='';
function widgetMessage(taille){
  const apps=hubApps.slice().sort((x,y)=>nomAffiche(x).localeCompare(nomAffiche(y),'fr'));
  if(!hubMessageApp && apps.length) hubMessageApp=apps[0].name;
  return `<div class="bw-titre">Un mot à l'admin ?</div>
    <div class="bw-sujets" role="radiogroup" aria-label="C'est pour">${SUJETS_MESSAGE.map(([v,t])=>
      `<button type="button" role="radio" aria-checked="${hubMessageCible===v}" data-agir="bw-sujet" data-cible="${v}">${t}</button>`).join('')}</div>
    ${hubMessageCible==='application' ? `<label class="sr" for="bw-message-app">Quelle application</label>
      <select id="bw-message-app" onchange="hubMessageApp=this.value">${apps.map(a=>
        `<option value="${esc(a.name)}" ${a.name===hubMessageApp?'selected':''}>${esc(nomAffiche(a))}</option>`).join('')}</select>` : ''}
    <label class="sr" for="bw-message-texte">Ton message</label>
    <textarea id="bw-message-texte" data-garder="message" data-pieces="widget" maxlength="2000"
      placeholder="Une idée, un souci, une demande d'accès."
      oninput="hubMessageBrouillon=this.value">${esc(hubMessageBrouillon)}</textarea>
    <div class="bw-bas">
      <label class="joindre" title="Joindre une image ou un PDF"><span class="sr">Joindre une image ou un PDF</span>
        <input type="file" accept="image/png,image/jpeg,image/webp,image/gif,application/pdf" multiple
               onchange="deposerPieces('widget', this.files); this.value=''">${SVG_TROMBONE}</label>
      <div class="pj-liste">${piecesHtml('widget')}</div>
      <button class="btn btn-primary" onclick="bwEnvoyer(this)">Envoyer</button></div>`;
}
async function bwEnvoyer(b){
  const t=$('bw-message-texte'), texte=(t.value||'').trim();
  const pieces=piecesPretes('widget'); if(pieces===null) return;
  if(!texte && !pieces.length){ t.focus(); return; }
  b.disabled=true;
  try{
    const r=await fetch('/api/messages',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({cible:hubMessageCible, texte, pieces, app: hubMessageCible==='application' ? hubMessageApp : ''})});
    if(!r.ok){ notifier(await echec(r,'Envoi impossible.'),'err'); return; }
    t.value=''; hubMessageBrouillon=''; viderPieces('widget'); notifier('Message envoyé.'); chargerSuivi(true);
  }finally{ b.disabled=false; }
}
/* ---------------- le menu d'une application ----------------
   La tuile ouvre l'application, et rien d'autre. Le reste -- la video de
   presentation, copier le lien, ranger hors du hub, les reglages -- passe
   par un menu : trois points dans le coin de la tuile a la souris, un appui
   long au doigt (comme sur l'ecran d'accueil d'un telephone), ou le clic
   droit. */
const SVG_POINTS='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>';
const SVG_MENU={
  ouvrir:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  video:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l6-3.5z"/></svg>',
  lien:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>',
  ranger:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 5.1A9.8 9.8 0 0 1 12 5c6 0 9.5 7 9.5 7a17 17 0 0 1-2.7 3.6M6.3 6.3C3.8 8 2.5 12 2.5 12S6 19 12 19a9 9 0 0 0 4.2-1"/></svg>',
  reglages:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
};
function boutonMenuApp(a){
  if(!a) return '';
  return `<button type="button" class="btn-menu-app" data-agir="menu-app" data-cible="${esc(a.name)}"
    aria-haspopup="menu" aria-expanded="false" aria-label="Options de ${esc(nomAffiche(a))}" title="Options">${SVG_POINTS}</button>`;
}
const appDuHub = nom => hubApps.find(x=>x.name===nom) || hubServices.find(x=>x.name===nom);
let menuAppAncre=null;
/* ou : le bouton (menu sous lui), {x, y} (clic droit : menu au pointeur), ou
   'feuille' (appui long : en bas de l'ecran). */
function ouvrirMenuApp(nom, ou){
  const a=appDuHub(nom); if(!a) return;
  const item=(agir, svg, texte, extra='') =>
    `<button type="button" role="menuitem" class="menu-app-item" data-agir="${agir}" data-cible="${esc(a.name)}" ${extra}>${svg}<span>${texte}</span></button>`;
  remplir($('menu-app-panneau'), `<div class="menu-app-tete"><img src="${icone(a.name)}" alt=""><b id="menu-app-nom">${esc(nomAffiche(a))}</b></div>
    ${item('menu-ouvrir', SVG_MENU.ouvrir, "Ouvrir")}
    ${a.presentation ? item('menu-presentation', SVG_MENU.video, 'Voir la présentation') : ''}
    ${item('menu-copier', SVG_MENU.lien, 'Copier le lien')}
    ${estAdmin && !a.externe && !a.interne ? item('menu-reglages', SVG_MENU.reglages, "Réglages de l'application") : ''}
    ${item('menu-ranger', SVG_MENU.ranger, 'Masquer du hub')}`);
  const menu=$('menu-app'), panneau=$('menu-app-panneau');
  menu.classList.toggle('feuille', ou==='feuille');
  panneau.style.left=panneau.style.top='';
  menu.hidden=false;
  if(menuAppAncre) menuAppAncre.setAttribute('aria-expanded','false');
  menuAppAncre = ou instanceof Element ? ou : null;
  if(ou!=='feuille'){
    /* Sous le bouton, aligne sur son bord droit ; au pointeur pour le clic
       droit. Retourne au-dessus, ou vers la gauche, s'il deborderait. */
    const r = ou instanceof Element ? ou.getBoundingClientRect()
      : {left:ou.x, right:ou.x, top:ou.y, bottom:ou.y};
    const w=panneau.offsetWidth, h=panneau.offsetHeight, marge=8;
    let x = ou instanceof Element ? r.right-w : r.left;
    let y = r.bottom+6;
    if(x+w > innerWidth-marge) x=innerWidth-marge-w;
    if(y+h > innerHeight-marge) y=Math.max(marge, r.top-6-h);
    panneau.style.left=Math.max(marge, x)+'px'; panneau.style.top=y+'px';
  }
  if(menuAppAncre) menuAppAncre.setAttribute('aria-expanded','true');
  const premier=panneau.querySelector('.menu-app-item'); if(premier) premier.focus({preventScroll:true});
}
function fermerMenuApp(rendreFocus){
  const menu=$('menu-app'); if(menu.hidden) return;
  menu.hidden=true;
  if(menuAppAncre){ menuAppAncre.setAttribute('aria-expanded','false'); if(rendreFocus) menuAppAncre.focus(); }
  menuAppAncre=null;
}
function actionMenuApp(quoi, nom){
  fermerMenuApp(false);
  const a=appDuHub(nom); if(!a) return;
  if(quoi==='ouvrir'){
    try{ localStorage.setItem('codelab-hub-derniere', nom); }catch(err){}
    if(hubApps.some(x=>x.name===nom))
      fetch('/api/mes-apps/'+encodeURIComponent(nom)+'/ouverte', {method:'POST', keepalive:true}).catch(()=>{});
    location.href=lienOuverture(a);
  }
  if(quoi==='presentation') ouvrirPresentation(nom);
  if(quoi==='copier') copierLien(new URL(lienOuverture(a), location.href).href);
  if(quoi==='reglages') location.href='/applications/'+encodeURIComponent(nom);
  if(quoi==='ranger') basculerMasquage(nom, true);
}
document.addEventListener('keydown', e=>{
  const menu=$('menu-app'); if(menu.hidden) return;
  if(e.key==='Escape'){ e.preventDefault(); e.stopImmediatePropagation(); fermerMenuApp(true); return; }
  if(e.key==='Tab'){ fermerMenuApp(false); return; }
  if(e.key!=='ArrowDown' && e.key!=='ArrowUp') return;
  e.preventDefault();
  const items=[...menu.querySelectorAll('.menu-app-item')], i=items.indexOf(document.activeElement);
  const j=(i + (e.key==='ArrowDown' ? 1 : -1) + items.length) % items.length;
  items[j].focus();
}, true);
window.addEventListener('resize', ()=>fermerMenuApp(false));
$('hub-liste').addEventListener('scroll', ()=>fermerMenuApp(false), {passive:true});

/* L'appui long, au doigt : 450 ms sans bouger. Un doigt qui glisse (le
   defilement du hub) annule tout. Au relachement, le navigateur emet -- ou
   pas, selon l'appareil -- un clic sur ce qui est sous le doigt : le fond du
   menu qui vient de s'ouvrir. Ce clic-la seul est avale, et seulement dans
   l'instant : un drapeau qui attendrait un clic jamais venu avalerait le
   toucher suivant, celui qui doit refermer le menu. */
let appuiLong=null, avalerClicJusqua=0;
$('hub-liste').addEventListener('pointerdown', e=>{
  if(e.pointerType!=='touch' || hubEdition) return;
  const t=e.target.closest('[data-app]'); if(!t || !appDuHub(t.dataset.app)) return;
  appuiLong={x:e.clientX, y:e.clientY, fait:false, minuterie:setTimeout(()=>{
    appuiLong.fait=true;
    if(navigator.vibrate) try{ navigator.vibrate(10); }catch(err){}
    ouvrirMenuApp(t.dataset.app, 'feuille');
  }, 450)};
});
document.addEventListener('pointermove', e=>{
  if(appuiLong && !appuiLong.fait && Math.hypot(e.clientX-appuiLong.x, e.clientY-appuiLong.y) > 10){
    clearTimeout(appuiLong.minuterie); appuiLong=null; }
});
['pointerup','pointercancel'].forEach(t=>document.addEventListener(t, ()=>{
  if(!appuiLong) return;
  clearTimeout(appuiLong.minuterie);
  if(appuiLong.fait) avalerClicJusqua=performance.now()+400;
  appuiLong=null;
}));
document.addEventListener('click', e=>{
  if(performance.now() > avalerClicJusqua) return;
  avalerClicJusqua=0; e.preventDefault(); e.stopImmediatePropagation();
}, true);
/* Le fond du menu a son propre ecouteur : Safari sur iOS n'envoie pas le
   toucher d'un simple <div> a un ecouteur pose sur le document. */
document.querySelector('#menu-app .menu-app-fond').addEventListener('click', ()=>fermerMenuApp(false));
/* Le clic droit (et l'appui long d'Android, qui le declenche aussi). */
$('hub-liste').addEventListener('contextmenu', e=>{
  /* En mode Modifier, l'appui long saisit la tuile : pas de menu du
     navigateur par-dessus. */
  if(hubEdition){ e.preventDefault(); return; }
  const t=e.target.closest('[data-app]'); if(!t || !appDuHub(t.dataset.app)) return;
  e.preventDefault();
  if(!$('menu-app').hidden) return;
  ouvrirMenuApp(t.dataset.app, e.pointerType==='touch' || appuiLong ? 'feuille' : {x:e.clientX, y:e.clientY});
});

/* ---------------- la video de presentation ----------------
   Ouverte depuis le menu de l'application (« Voir la présentation ») quand
   le projet a une video (video/presentation.mp4, produite par « codelab
   video ») : une feuille avec le lecteur du navigateur. */
function ouvrirPresentation(nom){
  const a=hubApps.find(x=>x.name===nom) || (typeof appParNom==='function' && appParNom(nom));
  if(!a || !a.presentation) return;
  const v=a.presentation.maj, base='/api/presentations/'+encodeURIComponent(nom)+'/';
  const video=$('pr-video');
  video.poster = a.presentation.affiche ? base+'affiche?v='+v : '';
  video.src = base+'video?v='+v;
  $('pr-icone').src=icone(nom);
  $('pr-nom').textContent=nomAffiche(a);
  $('pr-desc').textContent=a.description||'';
  $('pr-ouvrir').href=lienOuverture(a);
  $('pr-ouvrir').style.display = a.externe ? 'none' : '';
  $('ov-presentation').classList.add('show');
  video.play().catch(()=>{});
}
function peindrePresentationFiche(a){
  const carte=$('fa-presentation'); if(!carte) return;
  carte.style.display = a.externe ? 'none' : '';
  const p=a.presentation;
  $('fa-pr-sous').textContent = p
    ? (a.presentation_fichier ? a.presentation_fichier+' · ' : '')
      + 'modifiée le '+new Date(p.maj*1000).toLocaleString('fr-FR',{dateStyle:'long', timeStyle:'short'})
    : 'Aucune vidéo pour l’instant.';
  const base='/api/presentations/'+encodeURIComponent(a.name)+'/';
  remplir($('fa-pr-corps'), (p
    ? `<video class="fiche-video" controls playsinline preload="metadata" src="${base}video?v=${p.maj}"
         ${p.affiche ? `poster="${base}affiche?v=${p.maj}"` : ''}></video>
       <label class="choix-ligne" style="margin-top:12px"><input type="checkbox" data-changer="presentation-visible"
         data-cible="${esc(a.name)}" ${a.presentation_masquee ? '' : 'checked'}> Visible dans le hub</label>`
    : `<p class="hint" style="margin:6px 0 0">Dans le terminal du poste : <code>codelab video ${esc(a.name)}</code>,
       ou choisis ci-dessous une vidéo déjà dans le projet (un rendu Remotion, par exemple).</p>`)
    + `<label class="fa-pr-choix">Fichier vidéo
       <select data-changer="presentation-fichier" data-cible="${esc(a.name)}">${optionsVideos(a)}</select></label>`);
  chargerVideosProjet(a.name);
}
/* LE FICHIER DE LA PRESENTATION : video/presentation.mp4 par defaut, ou
   toute autre video du dossier du projet (le serveur en dresse la liste,
   et refuse ce qui n'y est pas). La liste se charge a l'ouverture de la
   fiche, une fois par application. */
const videosProjet={};
function optionsVideos(a){
  const v=videosProjet[a.name];
  const choisi=a.presentation_fichier||'';
  const autres=(v ? v.fichiers : (choisi ? [choisi] : [])).filter(f=>f!==(v ? v.defaut : 'video/presentation.mp4'));
  return `<option value="">video/presentation.mp4 (par défaut)</option>`
    + autres.map(f=>`<option value="${esc(f)}" ${f===choisi ? 'selected' : ''}>${esc(f)}</option>`).join('')
    + (v ? '' : '<option disabled>Recherche des vidéos…</option>');
}
async function chargerVideosProjet(nom, forcer){
  if(videosProjet[nom] && !forcer) return;
  try{
    const r=await fetch('/api/apps/'+encodeURIComponent(nom)+'/videos');
    if(!r.ok) return;
    videosProjet[nom]=await r.json();
  }catch(e){ return; }
  const a=appParNom(nom); if(a) peindrePresentationFiche(a);
}
async function choisirVideo(nom, fichier){
  const r=await fetch('/api/apps/'+encodeURIComponent(nom)+'/presentation',{method:'PUT',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({fichier})});
  if(!r.ok){ notifier(await echec(r,'Vidéo non retenue.'),'err'); return; }
  notifier(fichier ? 'La présentation montre '+fichier+'.' : 'La présentation reprend video/presentation.mp4.');
  refresh(); chargerHub();
}
async function basculerPresentation(nom, visible){
  const r=await fetch('/api/apps/'+encodeURIComponent(nom)+'/presentation',{method:'PUT',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({visible})});
  if(!r.ok){ notifier(await echec(r,'Modification impossible.'),'err'); return; }
  notifier(visible ? 'Présentation visible dans le hub.' : 'Présentation retirée du hub.');
  refresh(); chargerHub();
}
function fermerPresentation(){
  const video=$('pr-video');
  video.pause(); video.removeAttribute('src'); video.load();
  $('ov-presentation').classList.remove('show');
}
$('ov-presentation').addEventListener('click', e=>{ if(e.target===e.currentTarget) fermerPresentation(); });
document.addEventListener('keydown', e=>{
  if(e.key==='Escape' && $('ov-presentation').classList.contains('show')) fermerPresentation();
});

const CLASSE_WIDGET={serveur:'bw-serveur', activite:'bw-activite', une:'bw-une', apercu:'bw-une', etat:'bento-etat', messages:'bw-messages', nouveautes:'bw-nouveautes', message:'bw-message', suivi:'bw-suivi', sauvegardes:'bw-sauvegardes'};
function corpsWidget(e){
  if(e.id==='une'){ const [a, vue]=appALaUne(hubApps); return widgetApercu(a, e.taille, (vue ? 'Dernière ouverte' : 'À la une')+(a && a.ouverte_le ? ' · '+depuis(a.ouverte_le) : ''), false); }
  if(e.id==='apercu') return widgetApercu(hubApps.find(a=>a.name===e.app), e.taille, 'Aperçu');
  if(e.id==='etat') return widgetEtat(e.taille);
  if(e.id==='messages') return widgetMessages(e.taille);
  if(e.id==='nouveautes') return widgetNouveautes(e.taille);
  if(e.id==='sauvegardes') return widgetSauvegardes(e.taille);
  if(e.id==='message') return widgetMessage(e.taille);
  if(e.id==='suivi') return widgetSuivi(e.taille);
  if(e.id==='serveur' || e.id==='activite') return emplacementPouls(e.id, e.taille);
  return '';
}
const TAILLES_NOM={petit:'Petit', moyen:'Moyen', grand:'Grand'};
/* En mode Modifier, chaque tuile porte ses gestes : la retirer, la deplacer
   d'un cran (les fleches valent le glisser, au clavier et au doigt), et pour
   un widget, changer de taille. */
function gestesEdition(e, nom){
  const cle=esc(cleElement(e));
  const def=e.t==='w' ? hubCatalogue.find(c=>c.id===e.id) : null;
  return `<button type="button" class="ed-retirer" data-agir="hub-retirer" data-cible="${cle}"
      aria-label="${e.t==='a' ? 'Masquer' : 'Retirer'} « ${esc(nom)} »" title="${e.t==='a' ? 'Masquer du hub' : 'Retirer le widget'}">−</button>
    <div class="ed-barre">
      <button type="button" data-agir="hub-deplacer" data-cible="${cle}" data-sens="-1" aria-label="Avancer « ${esc(nom)} »">←</button>
      ${def && def.tailles.length>1 ? def.tailles.map(t=>`<button type="button" data-agir="hub-taille" data-cible="${cle}" data-taille="${t}"
        aria-pressed="${e.taille===t}" title="${TAILLES_NOM[t]}">${TAILLES_NOM[t].charAt(0)}</button>`).join('') : ''}
      <button type="button" data-agir="hub-deplacer" data-cible="${cle}" data-sens="1" aria-label="Reculer « ${esc(nom)} »">→</button>
    </div>`;
}
/* Un nom long prend un corps plus petit (voir .bento-carte-nom.long). */
const classeNomLong = n => n.length > 34 ? ' tres-long' : n.length > 20 ? ' long' : '';
/* Ce que montre la tuile du hub : deux lignes de description, soit environ
   DESCRIPTION_VISIBLE caracteres sur un ordinateur (moins au telephone). La
   fiche le dit sous le champ, compteur compris. */
const DESCRIPTION_VISIBLE=45, DESCRIPTION_MAX=140;
function compterDescription(champ){
  const aide=$(champ.id+'-compte'); if(!aide) return;
  const n=champ.value.trim().length;
  aide.textContent = n
    ? `${n} / ${DESCRIPTION_MAX} caractères — ${n<=DESCRIPTION_VISIBLE ? 'tout se voit sur la tuile.' : `les ${DESCRIPTION_VISIBLE} premiers environ se voient sur la tuile, le reste au survol.`}`
    : `Deux lignes sous le nom, dans le hub : environ ${DESCRIPTION_VISIBLE} caractères se voient, ${DESCRIPTION_MAX} au plus.`;
  aide.classList.toggle('desc-au-dela', n>DESCRIPTION_VISIBLE);
}
function peindreBento(liste, filtre){
  const toutes=hubApps.concat(hubServices);
  const desc = a => esc(a.description || (a.externe ? 'Outil de la stack' : 'Application'));
  const balise = hubEdition ? 'div' : 'a';
  const lien = a => hubEdition ? '' : `href="${lienOuverture(a)}"`;
  /* Une application : sa carte (nom, etat, description), ou son icone seule
     -- reglage « Applications ». Dans les deux cas, le meme lien, le meme
     menu, les memes gestes en mode Modifier. service-stack = la plaque
     claire sous un logo transparent (Dagster). */
  const carte = reglageHub('tuiles')==='icones'
    ? (a, e) => { const [c]=etatApp(a);
        return `<${balise} class="hub-icone ${c}${a.nouvelle_version?' maj':''}${a.externe && a.plaque ? ' service-stack' : ''}" ${lien(a)} data-app="${esc(a.name)}" data-el="${esc(cleElement(e))}"
          title="${esc(nomAffiche(a))}${a.description ? ' — '+esc(a.description) : ''}">
          <img src="${icone(a.name)}" alt=""><span class="hub-icone-nom">${esc(nomAffiche(a))}</span>${pastille(a)}${hubEdition ? gestesEdition(e, nomAffiche(a)) : boutonMenuApp(a)}</${balise}>`; }
    : (a, e) => { const [c]=etatApp(a);
        return `<${balise} class="bento-carte ${c}${a.nouvelle_version?' maj':''}" ${lien(a)} data-app="${esc(a.name)}" data-el="${esc(cleElement(e))}">
          <div class="bento-carte-haut"><img src="${icone(a.name)}" alt="">${badgeEtat(a)}</div>
          <div class="bento-carte-nom${classeNomLong(nomAffiche(a))}">${esc(nomAffiche(a))}</div>
          ${badgeMaj(a)}<div class="bento-carte-desc" title="${desc(a)}">${desc(a)}</div>${pastille(a)}${hubEdition ? gestesEdition(e, nomAffiche(a)) : boutonMenuApp(a)}</${balise}>`; };
  const tuile = e => {
    if(e.t==='w') return widgetDuHub(e);
    const a=toutes.find(x=>x.name===e.id); return a ? carte(a, e) : '';
  };
  const elements=elementsDuHub();
  const aucune = !toutes.length ? (estAdmin
      ? '<div class="hub-vide bento-vide"><b>Aucune application déclarée</b>Ouvre « Applications » dans le menu de gauche pour en ajouter une.</div>'
      : '<div class="hub-vide bento-vide"><b>Aucune application pour le moment</b>Ton administrateur ne t\'a pas encore donné accès à une application.</div>') : '';
  /* Widgets EN HAUT : deux grilles, l'une sous l'autre (ou l'une apres
     l'autre, a l'horizontale) ; chaque tuile se reorganise dans la sienne.
     La recherche ne montre que les applications qui correspondent. */
  const enZones = !filtre && reglageHub('widgets')==='haut';
  let html;
  if(filtre)
    html = liste.map(a=>carte(a, {t:'a', id:a.name})).join('')
      + (liste.length ? '' : '<div class="hub-vide bento-vide"><b>Aucune application ne correspond</b>Essaie un autre mot.</div>');
  else if(enZones){
    const widgets=elements.filter(e=>e.t==='w');
    html = (widgets.length ? `<div class="hub-grille hub-zone-widgets">${widgets.map(tuile).join('')}</div>` : '')
      + `<div class="hub-grille hub-zone-apps">${elements.filter(e=>e.t==='a').map(tuile).join('')}${aucune}</div>`;
  } else html = elements.map(tuile).join('') + aucune;
  const el=$('hub-liste');
  /* Pas de repeint sous la main : le rechargement periodique attendra. */
  if(glisse && glisse.parti) return;
  el.className='bento '+(enZones ? 'zones' : 'hub-grille')+(hubEdition ? ' edition' : '');
  remplirEnGardant(el, html);
  teinterALaUne(el);
  animerChiffres(el);
  peindrePouls();
  entreeDuHub(el);
  peindreResumeHub();
  majBoutonsComposer();
  ajusterBento();
}
/* L'ENTREE DU HUB : a la premiere peinture, les tuiles montent l'une apres
   l'autre, en cascade. Une seule fois par visite de la page -- le repeint
   periodique ne la rejoue pas. */
let hubEntre=false;
function entreeDuHub(el){
  if(hubEntre || !el.querySelector('[data-el]')) return;
  hubEntre=true;
  if(moinsDeMouvement()) return;
  el.querySelectorAll('[data-el]').forEach((n,i)=>n.style.setProperty('--i', Math.min(i, 16)));
  el.classList.add('entree');
  setTimeout(()=>el.classList.remove('entree'), 1800);
}
/* SOUS LE BONJOUR, LA JOURNEE EN UNE LIGNE : ce qui tourne, ce qui attend.
   Les chiffres defilent comme ceux des widgets. */
function peindreResumeHub(){
  majPastilleIcone();
  const r=$('hub-resume'); if(!r) return;
  const toutes=hubApps.concat(hubServices);
  if(!toutes.length){ remplir(r, ''); return; }
  let morceaux;
  if(estAdmin){
    const enLigne=toutes.filter(a=>etatApp(a)[0]==='on').length;
    const soucis=toutes.filter(a=>['err','warn'].includes(etatApp(a)[0])).length;
    const msg=(hubServices.find(s=>s.name==='messages')||{}).non_lues||0;
    morceaux=[`<b>${chiffre('resume-en-ligne', enLigne)}</b> sur ${toutes.length} en ligne`,
      soucis ? `<b class="resume-souci">${chiffre('resume-soucis', soucis)}</b> à regarder` : '',
      msg ? `<b>${chiffre('resume-msg', msg)}</b> message${msg>1?'s':''} à lire` : ''];
  }else{
    const maj=hubApps.filter(a=>a.nouvelle_version).length;
    morceaux=[`<b>${chiffre('resume-apps', hubApps.length)}</b> application${hubApps.length>1?'s':''} pour toi`,
      maj ? `<b>${chiffre('resume-maj', maj)}</b> mise${maj>1?'s':''} à jour` : ''];
  }
  remplir(r, morceaux.filter(Boolean).map(m=>`<span>${m}</span>`).join('<i aria-hidden="true">·</i>'));
  animerChiffres(r);
}
/* LE RELIEF AU SURVOL, a la souris seulement : la tuile se souleve et un
   reflet discret suit le pointeur. Plus d'inclinaison : en perspective, une
   tuile devenait un trapeze, ses bords penchaient, et l'oeil y lisait un
   defaut d'affichage. Ni au doigt, ni en mode Modifier,
   ni quand l'appareil demande moins de mouvement. */
(()=>{
  let fine=false; try{ fine=matchMedia('(hover:hover) and (pointer:fine)').matches; }catch(e){}
  if(!fine) return;
  let tuile=null;
  const lacher=()=>{ if(tuile) tuile.classList.remove('survol'); tuile=null; };
  $('hub-liste').addEventListener('pointermove', e=>{
    if(hubEdition || moinsDeMouvement() || e.pointerType!=='mouse'){ lacher(); return; }
    const t=e.target.closest('.bw, .bento-carte');
    if(t!==tuile){ lacher(); tuile=t; }
    if(!t) return;
    const r=t.getBoundingClientRect(), px=(e.clientX-r.left)/r.width, py=(e.clientY-r.top)/r.height;
    t.style.setProperty('--mx', (px*100).toFixed(1)+'%');
    t.style.setProperty('--my', (py*100).toFixed(1)+'%');
    t.classList.add('survol');
  });
  $('hub-liste').addEventListener('pointerleave', lacher);
})();
function widgetDuHub(e){
  const def=hubCatalogue.find(c=>c.id===e.id);
  const nom=e.id==='apercu' ? 'Aperçu de '+nomAffiche(hubApps.find(a=>a.name===e.app)||{name:e.app}) : (def ? def.titre : e.id);
  return `<section class="bw bw-${e.taille} ${CLASSE_WIDGET[e.id]||''}" aria-label="${esc(nom)}" data-el="${esc(cleElement(e))}">
    ${corpsWidget(e)}${hubEdition ? gestesEdition(e, nom) : ''}</section>`;
}
function majBoutonsComposer(){
  $('hub-modifier').innerHTML = hubEdition ? 'Terminé' : SVG_REORGANISER;
  $('hub-modifier').classList.toggle('actif', hubEdition);
  $('hub-ajouter-widget').style.display = hubEdition ? '' : 'none';
}
/* Ce qui doit SURVIVRE au repeint -- l'apercu (sinon il se recharge), le
   texte en cours de frappe -- est mis de cote puis remis a sa place. */
function remplirEnGardant(el, html){
  const gardes={};
  el.querySelectorAll('[data-garder]').forEach(n=>{ gardes[n.dataset.garder]=n; });
  const actif=document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.garder : null;
  const debut=actif && gardes[actif] ? gardes[actif].selectionStart : null;
  const avant=el.innerHTML;
  remplir(el, html);
  if(el.innerHTML!==avant){
    el.querySelectorAll('[data-garder]').forEach(n=>{
      const ancien=gardes[n.dataset.garder];
      if(ancien && ancien!==n) n.replaceWith(ancien);
    });
    /* preventScroll : rendre le focus au champ ne doit pas ramener la page
       jusqu'a lui -- c'etait un saut a chaque repeint, ou qu'on soit. */
    if(actif && gardes[actif]){ gardes[actif].focus({preventScroll:true}); if(debut!=null) try{ gardes[actif].setSelectionRange(debut, debut); }catch(e){} }
  }
}
/* A l'horizontale, la mosaique tient dans la fenetre : sa hauteur se
   calcule depuis son haut. En icones, autant de rangees qu'il en tient (au
   moins quatre : la hauteur d'un grand widget). A la verticale, et sur
   telephone, CSS reprend la main : la page defile. */
function horizontal(){ return !ecranMobile() && !document.documentElement.classList.contains('hub-vertical'); }
function ajusterBento(){
  const el=$('hub-liste');
  if(!el.classList.contains('bento')) return;
  if(!horizontal()){ el.style.removeProperty('--bento-h'); el.style.removeProperty('--rangees'); return; }
  const d=defileur();
  const haut=el.getBoundingClientRect().top + d.scrollTop;
  el.style.setProperty('--bento-h', Math.max(420, window.innerHeight - haut - 28) + 'px');
  const exces=d.scrollHeight - d.clientHeight;
  if(exces>0) el.style.setProperty('--bento-h', Math.max(420, el.clientHeight - exces) + 'px');
  const interieur=el.clientHeight - 14;
  /* 112 px la case, 22 px entre deux rangees (CSS, mode icones). */
  el.style.setProperty('--rangees', Math.max(4, Math.floor((interieur + 22)/(112 + 22))));
}
window.addEventListener('resize', ()=>{ majSensDuHub(); ajusterBento(); });
/* La molette verticale fait defiler la mosaique a l'horizontale : on n'a pas
   tous une souris qui sait aller de cote. Pas dans un champ de texte.
   SANS A-COUP : un cran de molette (100 px d'un coup, ou des lignes) ne
   saute pas, il glisse -- une cible, que la mosaique rejoint en quelques
   images -- et ne va qu'aux deux tiers d'un cran vertical. Un pave tactile
   envoie deja un flot de petits pas : ils passent tels quels. Aucun aimant
   (scroll-snap) : la mosaique s'arrete la ou on la lache. */
const MOLETTE_PAS=2/3;
let molette=null;
function glisserMolette(){
  const m=molette, el=$('hub-liste');
  const avant=el.scrollLeft, reste=m.cible-avant;
  /* Au moins un pixel par image : le navigateur arrondit scrollLeft, et un
     pas plus petit ne bougerait plus rien. */
  el.scrollLeft = Math.abs(reste)<=1 ? m.cible : avant + Math.sign(reste)*Math.max(1, Math.abs(reste)*.2);
  if(Math.abs(reste)<=1 || el.scrollLeft===avant){ molette=null; return; }
  m.raf=requestAnimationFrame(glisserMolette);
}
$('hub-liste').addEventListener('wheel', e=>{
  const el=e.currentTarget;
  if(!el.classList.contains('bento') || !horizontal() || e.target.closest('textarea')) return;
  if(Math.abs(e.deltaY) <= Math.abs(e.deltaX) || el.scrollWidth <= el.clientWidth) return;
  e.preventDefault();
  const pas = e.deltaMode===1 ? e.deltaY*40 : e.deltaMode===2 ? e.deltaY*el.clientWidth : e.deltaY;
  const cran = e.deltaMode!==0 || (Math.abs(pas)>=50 && Number.isInteger(pas));
  if(!cran && !molette){ el.scrollLeft += pas; return; }
  const max=el.scrollWidth-el.clientWidth;
  const depart = molette ? molette.cible : el.scrollLeft;
  const cible=Math.max(0, Math.min(max, depart + pas*(cran ? MOLETTE_PAS : 1)));
  if(molette){ molette.cible=cible; return; }
  molette={cible}; molette.raf=requestAnimationFrame(glisserMolette);
}, {passive:false});
/* Un doigt ou une barre qui reprend la main arrete le glissement. */
['pointerdown','touchstart'].forEach(t=>$('hub-liste').addEventListener(t, ()=>{
  if(molette){ cancelAnimationFrame(molette.raf); molette=null; }
}, {passive:true}));

/* ---- composer : modifier, deplacer, retirer, changer de taille ---- */
function basculerEdition(){
  hubEdition=!hubEdition;
  if(hubEdition) $('hub-filtre').value='';
  peindreHub();
}
function hubRetirer(cle){
  const elements=elementsDuHub();
  const e=elements.find(x=>cleElement(x)===cle); if(!e) return;
  if(e.t==='a'){
    /* Une application se MASQUE (elle se retrouve dans Parametres > Compte),
       elle ne quitte pas le serveur. */
    fetch('/api/mes-apps/'+encodeURIComponent(e.id)+'/masquer',{method:'POST'}).then(r=>{
      if(r.ok){ notifier('Masquée du hub — à retrouver dans tes paramètres.', 'ok',
                         {libelle:'Annuler', faire:()=>basculerMasquage(e.id, false)}); chargerHub(); }
    });
    return;
  }
  /* Retirer un widget se rattrape : la disposition d'avant est remise. */
  enregistrerDisposition(elements.filter(x=>x!==e)).then(()=>
    notifier('Widget retiré.', 'ok', {libelle:'Annuler', faire:()=>enregistrerDisposition(elements)}));
}
function hubDeplacer(cle, sens){
  const elements=elementsDuHub();
  const i=elements.findIndex(x=>cleElement(x)===cle);
  let j=i+Number(sens);
  /* Widgets en haut : chacun reste dans sa grille -- un widget passe au
     widget voisin, une application a l'application voisine. */
  if(i>=0 && reglageHub('widgets')==='haut')
    while(j>=0 && j<elements.length && elements[j].t!==elements[i].t) j+=Number(sens);
  if(i<0 || j<0 || j>=elements.length) return;
  [elements[i], elements[j]]=[elements[j], elements[i]];
  enregistrerDisposition(elements).then(()=>{
    const b=document.querySelector(`[data-el="${CSS.escape(cle)}"] [data-sens="${sens}"]`); if(b) b.focus(); });
}
function hubTailleWidget(cle, taille){
  enregistrerDisposition(elementsDuHub().map(x=>cleElement(x)===cle ? {...x, taille} : x));
}
/* GLISSER UNE TUILE, COMME SUR UN IPHONE. En mode edition, on attrape une
   tuile : elle suit le pointeur (souris ou doigt), et les autres glissent
   pour lui faire place a mesure qu'elle passe sur elles. Pres d'un bord,
   la mosaique defile toute seule. Au lacher, la tuile se pose a sa place et
   la disposition part au serveur. */
const SVG_REORGANISER='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><path d="M17 14v6M14 17h6"/></svg>';
var glisse=null;   // var : lu par peindreBento, qui peut tourner avant cette ligne
/* L'ordre des tuiles a l'ecran, d'une grille ou des deux. */
const ordreDesTuiles = () => [...$('hub-liste').querySelectorAll('[data-el]')].map(x=>x.dataset.el);
function positionsTuiles(){
  const m=new Map();
  $('hub-liste').querySelectorAll('[data-el]').forEach(n=>m.set(n, n.getBoundingClientRect()));
  return m;
}
/* Les tuiles qui ont change de place partent de l'ancienne et glissent vers
   la nouvelle (FLIP). Le tremblement passe par « rotate » : « transform »
   reste libre pour ce glissement. */
function glisserLesAutres(avant, sauf){
  for(const [n, r] of avant){
    if(n===sauf || !n.isConnected) continue;
    const r2=n.getBoundingClientRect(), dx=r.left-r2.left, dy=r.top-r2.top;
    if(!dx && !dy) continue;
    n.style.transition='none'; n.style.transform=`translate(${dx}px,${dy}px)`;
    requestAnimationFrame(()=>requestAnimationFrame(()=>{
      n.style.transition='transform .24s cubic-bezier(.2,.8,.2,1)'; n.style.transform=''; }));
  }
}
function suivrePointeur(){
  const g=glisse; if(!g || !g.parti) return;
  g.t.style.transform='none';
  const r=g.t.getBoundingClientRect();
  g.t.style.transform=`translate(${g.x-g.dx-r.left}px,${g.y-g.dy-r.top}px)`;
}
function chercherPlace(){
  const g=glisse; if(!g || !g.parti || performance.now()<g.calme) return;
  const sous=document.elementFromPoint(g.x, g.y);
  const cible=sous && sous.closest && sous.closest('#hub-liste [data-el]');
  /* Une tuile ne quitte pas sa grille (widgets en haut : widgets d'un
     cote, applications de l'autre). */
  if(!cible || cible===g.t || cible.parentNode!==g.t.parentNode) return;
  const enfants=[...g.t.parentNode.children];
  const avant=positionsTuiles();
  if(enfants.indexOf(g.t) < enfants.indexOf(cible)) cible.after(g.t); else cible.before(g.t);
  glisserLesAutres(avant, g.t);
  /* Le temps que les autres se posent : sinon la tuile survolee, qui vient
     de bouger, serait aussitot reprise pour cible, et tout oscillerait. */
  g.calme=performance.now()+200;
  suivrePointeur();
}
function defilerPresDesBords(){
  const g=glisse; if(!g || !g.parti) return;
  const liste=$('hub-liste'), r=liste.getBoundingClientRect(), bord=70;
  let v=0;
  if(g.x < r.left+bord) v=-Math.ceil((r.left+bord-g.x)/5);
  else if(g.x > r.right-bord) v=Math.ceil((g.x-(r.right-bord))/5);
  if(v && horizontal()){ liste.scrollLeft+=v; suivrePointeur(); chercherPlace(); }
  /* A la verticale, c'est la page qui defile, pres du haut et du bas. */
  if(!horizontal()){
    const h=window.innerHeight; let w=0;
    if(g.y < bord) w=-Math.ceil((bord-g.y)/5); else if(g.y > h-bord) w=Math.ceil((g.y-(h-bord))/5);
    if(w){ defileur().scrollBy(0, w); suivrePointeur(); chercherPlace(); }
  }
  g.raf=requestAnimationFrame(defilerPresDesBords);
}
/* AU DOIGT, on tient avant de deplacer : un appui de 350 ms « saisit » la
   tuile (elle se souleve, le telephone vibre), et seulement alors elle suit
   le doigt. Un doigt qui glisse tout de suite fait defiler la page, comme
   partout ailleurs -- sans cela, le mode Modifier empechait de faire
   defiler le hub. A la souris, rien ne change : on attrape et on tire. */
const SAISIE_TACTILE_MS = 350;
$('hub-liste').addEventListener('pointerdown', e=>{
  if(!hubEdition || e.button>0 || e.target.closest('button,select,textarea,input,label')) return;
  const t=e.target.closest('#hub-liste [data-el]'); if(!t) return;
  const r=t.getBoundingClientRect();
  const tactile = e.pointerType==='touch';
  glisse={t, x:e.clientX, y:e.clientY, x0:e.clientX, y0:e.clientY, dx:e.clientX-r.left, dy:e.clientY-r.top,
          parti:false, calme:0, raf:0, avant:ordreDesTuiles().join('|'),
          tactile, saisie:!tactile, minuterie:0};
  if(tactile) glisse.minuterie=setTimeout(()=>{
    const g=glisse; if(!g || g.t!==t) return;
    g.saisie=true; t.classList.add('saisie');
    if(navigator.vibrate) try{ navigator.vibrate(12); }catch(err){}
  }, SAISIE_TACTILE_MS);
});
/* Une fois la tuile saisie, le doigt ne fait plus defiler : c'est le
   premier touchmove annule qui l'empeche (touch-action laisse defiler tant
   que rien n'est saisi). */
document.addEventListener('touchmove', e=>{
  if(glisse && glisse.tactile && glisse.saisie) e.preventDefault();
}, {passive:false});
document.addEventListener('pointermove', e=>{
  const g=glisse; if(!g) return;
  g.x=e.clientX; g.y=e.clientY;
  if(!g.saisie){
    /* Le doigt a bouge avant la saisie : il fait defiler, on le laisse. */
    if(Math.hypot(g.x-g.x0, g.y-g.y0) > 8){ clearTimeout(g.minuterie); glisse=null; }
    return;
  }
  if(!g.parti){
    /* Quelques pixels avant de partir : un simple appui reste un appui. */
    if(Math.hypot(g.x-g.x0, g.y-g.y0) < 6) return;
    g.parti=true;
    g.t.classList.add('glissee'); $('hub-liste').classList.add('en-glisse');
    g.raf=requestAnimationFrame(defilerPresDesBords);
  }
  e.preventDefault();
  suivrePointeur(); chercherPlace();
}, {passive:false});
function finirGlisse(){
  const g=glisse; glisse=null; if(!g) return;
  clearTimeout(g.minuterie); g.t.classList.remove('saisie');
  cancelAnimationFrame(g.raf);
  $('hub-liste').classList.remove('en-glisse');
  if(!g.parti) return;
  /* La tuile se pose a sa place, puis la disposition est enregistree. */
  g.t.style.transition='transform .2s cubic-bezier(.2,.8,.2,1)';
  g.t.classList.remove('glissee');
  g.t.style.transform='';
  const ordre=ordreDesTuiles();
  setTimeout(()=>{
    g.t.style.transition='';
    if(ordre.join('|')===g.avant) return;
    const parCle=Object.fromEntries(elementsDuHub().map(x=>[cleElement(x), x]));
    enregistrerDisposition(ordre.map(k=>parCle[k]).filter(Boolean));
  }, 220);
}
document.addEventListener('pointerup', finirGlisse);
document.addEventListener('pointercancel', finirGlisse);
/* Echap termine la reorganisation. */
document.addEventListener('keydown', e=>{
  if(e.key==='Escape' && hubEdition && !document.querySelector('.ov.show')) basculerEdition();
});

/* ---- la galerie des widgets, comme celle d'un Mac : a gauche ce qu'on
   peut poser (le hub, puis chaque application), a droite les tailles,
   dessinees a leur proportion ; un clic pose le widget en tete du hub. ---- */
let galerieChoix='une';
function ouvrirGalerie(){ peindreGalerie(); $('ov-galerie').classList.add('show'); }
function peindreGalerie(){
  const hub=hubCatalogue.filter(c=>c.portee==='hub');
  const parApp=hubCatalogue.filter(c=>c.portee==='app');
  const apps=hubApps.slice().sort((x,y)=>nomAffiche(x).localeCompare(nomAffiche(y),'fr'));
  const items=hub.map(c=>({cle:c.id, titre:c.titre, icone:''}))
    .concat(parApp.length ? apps.map(a=>({cle:'app:'+a.name, titre:nomAffiche(a), icone:icone(a.name)})) : []);
  if(!items.some(i=>i.cle===galerieChoix)) galerieChoix=items[0] ? items[0].cle : '';
  const choisi=items.find(i=>i.cle===galerieChoix);
  const estApp=galerieChoix.startsWith('app:');
  const def=estApp ? parApp[0] : hubCatalogue.find(c=>c.id===galerieChoix);
  const appNom=estApp ? galerieChoix.slice(4) : null;
  const deja=elementsDuHub().filter(e=>e.t==='w' && e.id===(def&&def.id) && (e.app||null)===appNom);
  $('galerie-liste').innerHTML=`<div class="gl-titre">Hub</div>`
    + items.filter(i=>!i.cle.startsWith('app:')).map(i=>`<button type="button" class="gl-item" aria-pressed="${i.cle===galerieChoix}" data-agir="galerie-choisir" data-cible="${esc(i.cle)}">${esc(i.titre)}</button>`).join('')
    + (items.some(i=>i.cle.startsWith('app:')) ? `<div class="gl-titre">Applications</div>` : '')
    + items.filter(i=>i.cle.startsWith('app:')).map(i=>`<button type="button" class="gl-item" aria-pressed="${i.cle===galerieChoix}" data-agir="galerie-choisir" data-cible="${esc(i.cle)}"><img src="${i.icone}" alt="">${esc(i.titre)}</button>`).join('');
  $('galerie-detail').innerHTML = !def ? '' : `<h4>${esc(estApp ? def.titre+' — '+choisi.titre : def.titre)}</h4>
    <p class="hint">${esc(def.description)}</p>
    <div class="gl-tailles">${def.tailles.map(t=>`<button type="button" class="gl-taille gl-${t}" data-agir="galerie-ajouter" data-cible="${esc(def.id)}" data-taille="${t}" ${appNom ? `data-app="${esc(appNom)}"` : ''}
        aria-label="Ajouter « ${esc(def.titre)} » en ${TAILLES_NOM[t].toLowerCase()}">
        <span class="gl-forme">${estApp ? `<img src="${icone(appNom)}" alt="">` : ''}<b>${esc(estApp ? choisi.titre : def.titre)}</b></span>
        <span class="gl-nom">${TAILLES_NOM[t]}</span></button>`).join('')}</div>
    ${deja.length ? '<p class="hint">Déjà sur ton hub : l\'ajouter remplace sa taille.</p>' : ''}`;
}
function galerieAjouter(id, taille, app){
  const nouveau={t:'w', id, taille}; if(app) nouveau.app=app;
  const cle=cleElement(nouveau);
  enregistrerDisposition([nouveau].concat(elementsDuHub().filter(e=>cleElement(e)!==cle)));
  hide('ov-galerie'); notifier('Widget ajouté.');
  $('hub-liste').scrollLeft=0;
}

/* L'application ouverte : retenue par ce navigateur, et signalee au serveur
   -- qui note la version vue (indicateur « Nouvelle version ») et la date
   (widget « A la une »). keepalive : la page s'en va aussitot. */
$('hub-liste').addEventListener('click', e=>{
  const t = e.target.closest('[data-app]');
  /* Les trois points sont DANS la tuile : les ouvrir n'ouvre pas l'application. */
  if(!t || hubEdition || e.target.closest('.btn-menu-app')) return;
  try{ localStorage.setItem('codelab-hub-derniere', t.dataset.app); }catch(err){}
  if(hubApps.some(a=>a.name===t.dataset.app))
    fetch('/api/mes-apps/'+encodeURIComponent(t.dataset.app)+'/ouverte', {method:'POST', keepalive:true}).catch(()=>{});
});
