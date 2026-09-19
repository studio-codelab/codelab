"""AIChat -- lightweight CodeLab chat client for the internal LiteLLM gateway."""
import base64, json, os, time, urllib.error, urllib.request
from flask import Flask, Response, jsonify, request

APP_NAME="aichat"; LLM_URL=os.environ.get("CODELAB_LLM_URL","http://codelab-llm:8080"); SSO_ISSUER="codelab"
app=Flask(__name__)

def _env_file():
    values={}
    try:
        with open(os.environ.get("CODELAB_ENV_FILE","/var/lib/codelab/config/credentials.env"),encoding="utf-8") as f:
            for line in f:
                line=line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k,v=line.split("=",1); values[k]=v
    except OSError: pass
    return values

def _b64(v): return base64.urlsafe_b64decode(v+"="*(-len(v)%4))

def _identity(assertion):
    if not assertion: return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        values=_env_file(); public_b64=os.environ.get("CODELAB_SSO_PUBLIC_KEY") or values.get("CODELAB_SSO_PUBLIC_KEY","")
        parts=assertion.split(".")
        if len(parts)!=3 or not public_b64: return None
        header=json.loads(_b64(parts[0]).decode()); payload=json.loads(_b64(parts[1]).decode())
        if header.get("alg")!="EdDSA" or header.get("typ")!="JWT": return None
        if payload.get("iss")!=SSO_ISSUER or payload.get("aud")!=APP_NAME: return None
        if not payload.get("sub") or int(payload.get("exp",0))<=int(time.time()): return None
        Ed25519PublicKey.from_public_bytes(_b64(public_b64)).verify(_b64(parts[2]),(parts[0]+"."+parts[1]).encode("ascii"))
        return {"user":str(payload["sub"]),"role":str(payload.get("role") or ""),"email":str(payload.get("email") or "")}
    except (ImportError,ValueError,TypeError,KeyError,json.JSONDecodeError): return None

def _auth():
    identity=_identity(request.headers.get("X-CodeLab-Auth",""))
    return identity if identity else (jsonify({"error":"Session CodeLab absente ou invalide."}),401)

def _llm(method,path,body=None,assertion=""):
    data=None; headers={"Accept":"application/json"}
    if body is not None:
        data=json.dumps(body,ensure_ascii=False).encode(); headers["Content-Type"]="application/json"
    if assertion: headers["X-CodeLab-Auth"]=assertion
    req=urllib.request.Request(LLM_URL.rstrip("/") + path,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=95) as r: return r.status,json.loads(r.read().decode()),dict(r.headers)
    except urllib.error.HTTPError as e:
        try: payload=json.loads(e.read().decode())
        except (ValueError,UnicodeDecodeError): payload={"error":"Le service LLM a refusé la requête."}
        return e.code,payload,{}
    except (urllib.error.URLError,TimeoutError,OSError) as e:
        return 503,{"error":f"Service LLM indisponible : {e}"},{}

@app.get("/health")
def health(): return jsonify({"status":"ok","service":"aichat"})

@app.get("/")
def index():
    identity=_auth()
    if isinstance(identity,tuple): return identity
    return PAGE

@app.get("/api/me")
def me():
    identity=_auth()
    if isinstance(identity,tuple): return identity
    return jsonify(identity)

@app.get("/api/models")
def models():
    identity=_auth()
    if isinstance(identity,tuple): return identity
    status,payload,_=_llm("GET","/v1/models",assertion=request.headers.get("X-CodeLab-Auth",""))
    return jsonify(payload),status

@app.post("/api/chat")
def chat():
    identity=_auth()
    if isinstance(identity,tuple): return identity
    body=request.get_json(silent=True) or {}; model=body.get("model"); messages=body.get("messages"); conversation_id=body.get("conversation_id") or ""
    if not isinstance(model,str) or not model: return jsonify({"error":"Choisis un modèle."}),400
    if not isinstance(messages,list) or not messages or len(messages)>80: return jsonify({"error":"Conversation invalide."}),400
    clean=[]
    for message in messages:
        if not isinstance(message,dict) or message.get("role") not in {"user","assistant","system"}: return jsonify({"error":"Message invalide."}),400
        content=message.get("content","")
        if not isinstance(content,str) or not content.strip() or len(content)>12000: return jsonify({"error":"Message vide ou trop long."}),400
        clean.append({"role":message["role"],"content":content})
    payload={"model":model,"messages":clean}
    if conversation_id: payload["conversation_id"]=conversation_id
    assertion=request.headers.get("X-CodeLab-Auth","")
    status,result,response_headers=_llm("POST","/v1/chat/completions",payload,assertion)
    if isinstance(result,dict): result["conversation_id"]=response_headers.get("X-CodeLab-Conversation-ID",conversation_id)
    return jsonify(result),status

PAGE=r"""<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CodeLab · AIChat</title>
<link rel="stylesheet" href="/theme.css"><style>
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:var(--t-b)/1.5 var(--sans);min-height:100vh}
.bar{height:52px;background:var(--nav);color:var(--nav-txt);border-bottom:1px solid var(--nav-line);display:flex;align-items:center;padding:0 16px;gap:12px;position:sticky;top:0;z-index:3}
.brand{display:flex;align-items:center;gap:9px;font-weight:700}.brand b{font-size:15px}.brand span{color:var(--nav-dim);font-weight:550}.brand-icon{width:26px;height:26px;border-radius:7px;background:var(--accent);display:grid;place-items:center;color:var(--sur-accent)}.brand-icon svg{width:15px;height:15px}.spacer{flex:1}.user{color:var(--nav-dim);font-size:12px}
.layout{width:min(1100px,100%);margin:0 auto;display:grid;grid-template-rows:auto 1fr;min-height:calc(100vh - 52px);padding:22px}.top{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.top h1{font-size:22px;letter-spacing:-.03em;margin:0}.top p{margin:2px 0 0;color:var(--dim);font-size:13px}.controls{margin-left:auto;display:flex;gap:8px;align-items:center}.controls select,.controls button{font:inherit;font-size:13px}
select,button{border:1px solid var(--line2);border-radius:7px;background:var(--surface);color:var(--txt);padding:7px 10px}button{cursor:pointer;font-weight:650}button:hover{border-color:var(--accent);color:var(--accent)}
.chat{margin-top:18px;background:var(--surface);border:1px solid var(--line);border-radius:var(--r);display:flex;flex-direction:column;min-height:0;overflow:hidden}.messages{flex:1;overflow:auto;padding:24px;display:flex;flex-direction:column;gap:16px;min-height:52vh}.msg{max-width:min(820px,92%);padding:13px 15px;border-radius:10px;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}.msg.user{align-self:flex-end;background:var(--accent);color:var(--sur-accent)}.msg.assistant{align-self:flex-start;background:var(--surface2);border:1px solid var(--line)}.empty{text-align:center;color:var(--dim);margin:auto;max-width:55ch}.empty b{display:block;color:var(--txt);font-size:16px;margin-bottom:6px}
.compose{border-top:1px solid var(--line);padding:14px;display:flex;gap:10px;background:var(--surface2)}textarea{flex:1;resize:none;min-height:48px;max-height:180px;border:1px solid var(--line2);border-radius:8px;background:var(--surface);color:var(--txt);padding:10px 12px;font:inherit;outline:none}textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-ring)}.send{align-self:flex-end;background:var(--accent);color:var(--sur-accent);border-color:var(--accent);height:42px}.send:hover{background:var(--accent-h);color:var(--sur-accent)}.status{font-size:12px;color:var(--dim);min-height:18px;padding:0 16px 10px}.status.err{color:var(--err)}@media(max-width:700px){.layout{padding:14px}.controls{margin-left:0}.messages{padding:16px}.compose{padding:10px}.msg{max-width:96%}}
</style></head><body>
<header class="bar"><div class="brand"><span class="brand-icon"><svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z"/><circle cx="9" cy="10" r="1"/><circle cx="12" cy="10" r="1"/><circle cx="15" cy="10" r="1"/></svg></span><b>CodeLab</b><span>· AIChat</span></div><div class="spacer"></div><div class="user" id="user"></div></header>
<main class="layout"><div class="top"><div><h1>AIChat</h1><p>Une interface légère pour les modèles exposés par LiteLLM.</p></div><div class="controls"><select id="model" aria-label="Modèle"></select><button id="new" type="button">Nouvelle conversation</button></div></div>
<section class="chat"><div class="messages" id="messages"><div class="empty"><b>Prêt à discuter.</b>Choisis un modèle puis écris ton premier message.</div></div><div class="status" id="status"></div><form class="compose" id="form"><textarea id="input" rows="1" placeholder="Écris ton message…" autocomplete="off"></textarea><button class="send" id="send" type="submit">Envoyer</button></form></section></main>
<script>
const $=id=>document.getElementById(id);let history=[],conversationId="",sending=false;
function draw(){const box=$("messages");if(!history.length){box.innerHTML='<div class="empty"><b>Prêt à discuter.</b>Choisis un modèle puis écris ton premier message.</div>';return}box.innerHTML=history.map(m=>'<div class="msg '+m.role+'"></div>').join('');history.forEach((m,i)=>box.children[i].textContent=m.content);box.scrollTop=box.scrollHeight}
function status(text,error=false){$("status").textContent=text||"";$("status").className="status"+(error?" err":"")}
async function boot(){const me=await (await fetch("/api/me")).json();if(me.error){location.reload();return}$("user").textContent=me.user;const r=await fetch("/api/models");const d=await r.json();if(!r.ok){status(d.error||"Impossible de charger les modèles.",true);return}const models=(d.data||[]).map(x=>x.id).filter(Boolean);$("model").innerHTML=models.map(m=>'<option value="'+m.replaceAll('"','&quot;')+'">'+m+"</option>").join("");draw()}
$("form").onsubmit=async e=>{e.preventDefault();if(sending)return;const text=$("input").value.trim();if(!text)return;const model=$("model").value;if(!model){status("Aucun modèle disponible.",true);return}sending=true;$("send").disabled=true;status("Réponse en cours…");history.push({role:"user",content:text});$("input").value="";draw();try{const r=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({model,messages:history,conversation_id:conversationId})});const d=await r.json();if(!r.ok)throw new Error(d.error||d.detail||"Le service LLM a refusé la requête.");conversationId=d.conversation_id||conversationId;const answer=d.choices?.[0]?.message?.content||"Réponse vide.";history.push({role:"assistant",content:answer});draw();status("")}catch(err){history.pop();draw();status(err.message||"Erreur de communication avec LiteLLM.",true)}finally{sending=false;$("send").disabled=false;$("input").focus()}};
$("new").onclick=()=>{history=[];conversationId="";status("");draw();$("input").focus()};$("input").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();$("form").requestSubmit()}});boot();
</script></body></html>"""
if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT","9101")),debug=False)
