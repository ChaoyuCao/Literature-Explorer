"""Self-contained reference browsing, separate from the main paper explorer."""
import json
from pathlib import Path
from .references import reference_summary

TEMPLATE = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Literature Explorer · References</title><style>
body{font:15px system-ui;color:#24364b;background:#f3f6fa;max-width:1300px;margin:24px auto;padding:0 18px}
section{background:white;border:1px solid #d9e3ed;border-radius:10px;padding:18px;margin:18px 0}
table{border-collapse:collapse;width:100%}td,th{text-align:left;vertical-align:top;border-bottom:1px solid #d9e3ed;padding:9px}
th{font-size:13px}button,input,select{font:inherit;padding:7px;margin:5px 8px 5px 0}input{max-width:90%;width:300px}
a{color:#087f8c}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px} .scroll{overflow:auto} .muted{color:#596b7f}
</style><h1>References and cited publications</h1><p id="context"></p>
<p class="muted">Provider observations preserve original evidence. Publication counts consolidate compatible identifiers; further identifier bridging may reduce them. Retrieval completion does not establish bibliography completeness. Article-to-itself links await review and are excluded from expansion and network degrees.</p>
<section><h2>Coverage</h2><div id="coverage"></div><details><summary>Provider retrieval and pagination</summary><div id="providers" class="scroll"></div></details></section>
<section><h2>Browse referenced publications</h2><label>Seed paper <select id="seed"><option value="">All selected papers</option></select></label>
<label>Search <input id="query" placeholder="Title, author, DOI, PMID or journal"></label>
<label>Library <select id="membership"><option value="">All</option><option value="existing">Already in library</option><option value="external">Outside library</option></select></label>
<button id="export-publications">Export publication JSON</button><button id="export-evidence">Export original observations</button>
<p id="count"></p><div id="publications" class="scroll"></div><button id="more">Show 100 more</button></section>
<section><h2>Unresolved observations and matches for review</h2><p>No candidate below is silently accepted as a publication identity.</p><div id="review" class="scroll"></div><button id="more-review">Show 100 more</button></section>
<section><h2>Citation relationships</h2><p>Repeated observations support one citation edge. Self-links are flagged separately; same-author citations to other publications remain valid.</p><div id="relationships" class="scroll"></div></section>
<script type="application/json" id="data">__DATA__</script><script>
'use strict';const data=JSON.parse(document.getElementById('data').textContent), $=id=>document.getElementById(id);
let limit=100,reviewLimit=100;
function node(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=String(text??'Unavailable');return e;}
function download(name,packet){const url=URL.createObjectURL(new Blob([JSON.stringify(packet,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function table(target,headers,rows){target.replaceChildren();const t=node('table'),h=node('tr');headers.forEach(v=>h.append(node('th',v)));t.append(h);for(const row of rows){const tr=node('tr');for(const value of row){const td=node('td');td.append(value instanceof Node?value:node('span',value));tr.append(td);}t.append(tr);}target.append(t);}
function details(label,packet){const d=node('details');d.append(node('summary',label),node('pre',JSON.stringify(packet,null,2)));return d;}
function observations(){return data.observations.filter(o=>!$('seed').value||String(o.seed_paper_id)===$('seed').value);}
function selected(){const ids=new Set(observations().map(o=>o.publication_id));const q=$('query').value.toLowerCase();return data.publications.filter(p=>ids.has(p.id)&&(!$('membership').value||p.in_library===($('membership').value==='existing'))&&JSON.stringify([p.title,p.authors,p.journal,p.identifiers]).toLowerCase().includes(q));}
$('context').textContent=JSON.stringify(data.search_context);for(const p of data.seeds){const o=node('option',p.paper_id+' · '+(p.title||'Title unavailable'));o.value=p.paper_id;$('seed').append(o);}
function draw(){const pubs=selected(),pubIds=new Set(pubs.map(p=>p.id)),obs=observations(),seedIds=new Set(obs.map(o=>o.seed_paper_id));
 const count=data.coverage;$('coverage').textContent=`Requested ${count.papers_requested}; processed ${count.papers_processed}. Raw observations ${count.raw_observations}; resolved distinct referenced identities ${count.resolved_distinct_publications}; unresolved ${count.unresolved_observations}; ambiguous ${count.ambiguous_matches}; review candidates ${count.review_required_observations}; conflicts ${count.conflicting_observations}. Citation edges ${count.distinct_citation_edges}; flagged self-links ${count.flagged_self_links}. Retrieval: ${count.retrieval_status}; resolution: ${count.resolution_status}; bibliography completeness: unknown.`;
 $('count').textContent=`${pubs.length} referenced publication identities · showing ${Math.min(limit,pubs.length)}. Metadata unavailable from providers is shown explicitly.`;
 table($('publications'),['Publication','Authors / journal / year','Identifiers and links','Evidence'],pubs.slice(0,limit).map(p=>{const title=node('div',p.title||'Title unavailable');if(p.abstract)title.append(details('Abstract',p.abstract));const links=node('div');Object.entries(p.identifiers).forEach(([k,v])=>links.append(node('p',k+': '+v)));Object.entries(p.links).forEach(([k,url])=>{if(url){const a=node('a',k+' ');a.href=url;a.target='_blank';a.rel='noopener noreferrer';links.append(a);}});return[title,[p.authors||'Authors unavailable',p.journal||'Journal unavailable',p.year||'Year unavailable'].join(' · '),links,details((p.in_library?'In library':'Outside library')+' · '+p.observation_ids.length+' observations',{publication:p,observations:obs.filter(o=>o.publication_id===p.id)})];}));
 const review=obs.filter(o=>o.resolution_status!=='resolved');table($('review'),['Seed / provider / position','Citation','Status / review evidence'],review.slice(0,reviewLimit).map(o=>[o.seed_paper_id+' / '+o.provider+' / '+o.position,o.raw_citation_text||o.metadata.title||'Citation text unavailable',details(o.resolution_status,o)]));
 const rel=data.relationships.filter(r=>r.seed_paper_ids.some(id=>seedIds.has(id)));table($('relationships'),['Citing identity','Cited identity','Status / evidence'],rel.slice(0,100).map(r=>[r.citing,r.cited,details(r.status+' · '+r.providers.join(', '),r)]));
 $('more').disabled=limit>=pubs.length;$('more-review').disabled=reviewLimit>=review.length;
 table($('providers'),['Paper','Provider','Outcome','Reported','Retrieved observations','Pagination / next page'],data.providers.filter(p=>!$('seed').value||String(p.paper_id)===$('seed').value).map(p=>[p.paper_id,p.provider,p.status,p.reported_count,p.raw_observations,(p.pagination_status||'Unknown')+' / '+(p.next_page??'None')]));}
for(const id of ['seed','query','membership'])$(id).addEventListener('input',()=>{limit=reviewLimit=100;draw();});
$('more').onclick=()=>{limit+=100;draw();};$('more-review').onclick=()=>{reviewLimit+=100;draw();};
$('export-publications').onclick=()=>download('referenced-publications.json',{schema_version:2,kind:'referenced_publications',search_context:data.search_context,search_provenance:data.search_provenance,coverage:data.coverage,selection_note:'Coverage describes the original analysis; publications reflect the current filters.',publications:selected()});
$('export-evidence').onclick=()=>download('reference-observations.json',{schema_version:2,search_context:data.search_context,observations:observations()});draw();
</script></html>'''


def write_reference_report(document, path):
    packet = {k: document[k] for k in ('observations', 'publications', 'relationships', 'coverage', 'search_context', 'search_provenance')}
    packet['providers'] = reference_summary(document)['provider_outcomes']
    packet['seeds'] = [{k: e.get(k) for k in ('paper_id', 'title', 'status')} for e in document['papers']]
    data = json.dumps(packet, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    Path(path).write_text(TEMPLATE.replace('__DATA__', data), encoding='utf-8')
