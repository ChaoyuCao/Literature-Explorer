// Local library controls and metadata analysis for the standalone explorer.
// Citation/trend controls below use only embedded metadata; no remote scripts.
if(report.selection)$('status').textContent='Selection: '+papers.length+' papers from search '+report.search.id+'. Original search: '+report.search.status+', '+report.search.retrieved+' records retrieved / '+report.search.total+' database hits.';
const panel=element('section',undefined,'panel');panel.style.margin='16px 0';
panel.append(element('h2','Library and analysis'));
const explanation=element('p','Browser edits are stored locally. Export annotations and apply them with the annotations command to update the catalogue.','muted');panel.append(explanation);
const controls=element('div',undefined,'filters');
function control(labelText,id,tag='select'){const label=element('label',labelText),input=element(tag);input.id=id;label.append(input);controls.append(label);return input;}
const authorInput=control('Author contains','author-filter','input');authorInput.type='search';
const statusFilter=control('Review status','review-filter');option(statusFilter,'','All statuses');for(const status of ['unreviewed','included','excluded','uncertain'])option(statusFilter,status,status);
const tagFilter=control('Tag','tag-filter');
const favoriteFilter=control('Favorites','favorite-filter');option(favoriteFilter,'','All papers');option(favoriteFilter,'yes','Favorites only');
const termMode=control('Term source','term-mode');option(termMode,'keywords','Author keywords');option(termMode,'mesh_terms','MeSH terms');option(termMode,'title_terms','Title words');
for(const [id,label] of [['export-csv','Export filtered CSV'],['export-annotations','Export annotations'],['export-journals','Export journal chart (SVG)']]){const button=element('button',label);button.id=id;controls.append(button);}
panel.append(controls);
const analysisGrid=element('div',undefined,'grid');for(const [id,title] of [['authors-chart','Authors (structured names)'],['terms-chart','Terms · papers containing each term']]){const section=element('section');section.append(element('h2',title));const chart=element('div',undefined,'chart');chart.id=id;section.append(chart);analysisGrid.append(section);}panel.append(analysisGrid);
const citationSummary=element('p',undefined,'muted');citationSummary.id='citation-summary';panel.append(citationSummary);
document.querySelector('#results-title').closest('section').before(panel);
const storageKey='literature.annotations.'+(report.library_id||report.search.source+'.'+report.search.id+'.'+report.search.query);
let edits={};try{edits=JSON.parse(localStorage.getItem(storageKey)||'{}');if(!edits||Array.isArray(edits)||typeof edits!=='object')edits={};}catch{edits={};}
for(const p of papers){p.library=p.library||{note:'',tags:[],favorite:false,status:'unreviewed',updated:null};const saved=edits[p.id];if(saved?.library?.updated&&saved.library.updated>(p.library.updated||'')){p.library=saved.library;}else delete edits[p.id];}
function tagOptions(){const old=tagFilter.value;tagFilter.replaceChildren();option(tagFilter,'','All tags');for(const tag of [...new Set(papers.flatMap(p=>p.library.tags||[]))].sort())option(tagFilter,tag,tag);if([...tagFilter.options].some(o=>o.value===old))tagFilter.value=old;}
tagOptions();
const baseFilters=filters;filters=function(){return baseFilters().filter(p=>(!authorInput.value||(p.authors||'').toLocaleLowerCase().includes(authorInput.value.toLocaleLowerCase()))&&(!statusFilter.value||p.library.status===statusFilter.value)&&(!tagFilter.value||(p.library.tags||[]).includes(tagFilter.value))&&(!favoriteFilter.value||p.library.favorite));};
function downloadFile(name,content,type){const url=URL.createObjectURL(new Blob([content],{type})),link=element('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
const stopWords=new Set('a an and are as at be been by can could for from has have in into is it its may of on or our than that the their these this to using was we were which with without study studies results analysis effect effects'.split(' '));
function analysisCounts(rows){const counts={authors:new Map(),keywords:new Map(),mesh_terms:new Map(),title_terms:new Map(),tags:new Map(),review_status:new Map()},citations={};for(const p of rows){const values={authors:p.author_list||[],keywords:(p.keywords||[]).map(x=>x.trim().toLocaleLowerCase()),mesh_terms:(p.mesh_terms||[]).map(x=>x.trim().toLocaleLowerCase()),title_terms:(p.title||'').toLocaleLowerCase().match(/[\p{L}\p{N}_-]+/gu)||[],tags:p.library.tags||[],review_status:[p.library.status||'unreviewed']};for(const [key,list] of Object.entries(values)){for(const value of new Set(list)){if(!value||key==='title_terms'&&(value.length<3||stopWords.has(value)||/^\d+$/.test(value)))continue;counts[key].set(value,(counts[key].get(value)||0)+1);}}if(p.citation_count!==null&&p.citation_count!==undefined){const source=p.citation_source||'Unspecified source';(citations[source]??=[]).push(Number(p.citation_count));}}
const summary={};for(const [source,values] of Object.entries(citations)){values.sort((a,b)=>a-b);const middle=Math.floor(values.length/2);summary[source]={papers_with_counts:values.length,total:values.reduce((a,b)=>a+b,0),median:values.length%2?values[middle]:(values[middle-1]+values[middle])/2,max:values.at(-1)};}return {counts,summary};}
document.querySelector('#papers').closest('table').querySelector('thead tr').append(element('th','Library'));
const baseRender=render;render=function(){baseRender();const data=analysisCounts(filtered);for(const [id,key] of [['authors-chart','authors'],['terms-chart',termMode.value]])bars(id,[...data.counts[key]].sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,30).map(([name,value])=>({name,value})));citationSummary.textContent=Object.entries(data.summary).map(([source,s])=>source+': counts available for '+s.papers_with_counts+'/'+filtered.length+' papers; median '+s.median+', total '+s.total+'.').join(' ')||'Citation counts unavailable for the selected papers.';
const pageRows=filtered.slice(currentPage*size,(currentPage+1)*size);[...$('papers').children].forEach((row,index)=>{const p=pageRows[index];if(!p){row.firstChild.colSpan=7;return;}const cell=element('td');cell.style.minWidth='220px';cell.append(element('div',(p.library.favorite?'★ · ':'')+p.library.status+(p.library.tags?.length?' · '+p.library.tags.join(', '):''),'muted'));const details=element('details');details.append(element('summary','Edit note and tags'));const note=element('textarea');note.value=p.library.note||'';note.rows=4;note.style.width='100%';note.setAttribute('aria-label','Paper note');const tags=element('input');tags.value=(p.library.tags||[]).join(', ');tags.style.minWidth='0';tags.style.width='100%';tags.setAttribute('aria-label','Paper tags');const status=element('select');status.setAttribute('aria-label','Paper review status');for(const value of ['unreviewed','included','excluded','uncertain'])option(status,value,value);status.value=p.library.status||'unreviewed';const favorite=element('input');favorite.type='checkbox';favorite.checked=!!p.library.favorite;favorite.style.minWidth='0';favorite.setAttribute('aria-label','Favorite paper');const favoriteLabel=element('label','Favorite');favoriteLabel.append(favorite);const save=element('button','Save annotation');save.addEventListener('click',()=>{const base=edits[p.id]?.base_updated??p.library.updated??null;p.library={...p.library,note:note.value,tags:[...new Set(tags.value.split(',').map(x=>x.trim()).filter(Boolean))],status:status.value,favorite:favorite.checked,updated:new Date().toISOString()};edits[p.id]={id:p.id,base_updated:base,library:p.library};try{localStorage.setItem(storageKey,JSON.stringify(edits));}catch{explanation.textContent='Browser storage is unavailable. Export annotations to preserve this session’s edits.';}tagOptions();render();});details.append(element('div','Note'),note,element('div','Tags (comma-separated)'),tags,status,favoriteLabel,save);cell.append(details);if(p.search_memberships)cell.append(element('small','Found in '+p.search_memberships.map(m=>m.source+' (search '+m.search_id+', result '+m.rank+')').join(', ')));row.append(cell);});};
for(const input of [authorInput,statusFilter,tagFilter,favoriteFilter,termMode])input.addEventListener(input===authorInput?'input':'change',()=>{currentPage=0;render();});
$('reset').addEventListener('click',()=>{authorInput.value=statusFilter.value=tagFilter.value=favoriteFilter.value='';currentPage=0;render();});
$('export-csv').addEventListener('click',()=>{const keys=[...new Set(filtered.flatMap(p=>Object.keys(p)))].sort(),escape=value=>'"'+String(value??'').replaceAll('"','""')+'"';const rows=[keys.map(escape).join(',')];for(const p of filtered)rows.push(keys.map(key=>escape(typeof p[key]==='object'&&p[key]!==null?JSON.stringify(p[key]):p[key])).join(','));downloadFile('literature-filtered.csv','\ufeff'+rows.join('\r\n'),'text/csv;charset=utf-8');});
$('export-annotations').disabled=!report.library_id;
$('export-annotations').addEventListener('click',()=>downloadFile('literature-annotations.json',JSON.stringify({schema_version:1,library_id:report.library_id,annotations:Object.values(edits)},null,2),'application/json'));
// Replace the original JSON export handler so every filtered statistic stays consistent.
const oldExport=$('export'),newExport=oldExport.cloneNode(true);oldExport.replaceWith(newExport);newExport.addEventListener('click',()=>{const groups=groupJournals(filtered),data=analysisCounts(filtered),statistics={paper_count:filtered.length,journal_count:groups.length,abstract_count:filtered.filter(p=>p.abstract).length,doi_count:filtered.filter(p=>p.doi).length,journal_metadata_count:filtered.filter(p=>p.journal).length,citation_metadata_count:filtered.filter(p=>p.citation_count!==null&&p.citation_count!==undefined).length,structured_author_count:filtered.filter(p=>p.author_list?.length).length,journals:groups.map(j=>({journal:j.name,paper_count:j.count,metric:j.metric,metric_reference:j.reference,metric_status:j.status,metric_note:j.note})),years:Object.fromEntries(countValues(filtered,'year')),publication_types:Object.fromEntries(countValues(filtered,'publication_types')),citation_summary:data.summary};for(const [key,value] of Object.entries(data.counts))statistics[key]=Object.fromEntries([...value].sort((a,b)=>b[1]-a[1]));const packet={...report,papers:filtered,filter:{text:$('text-filter').value,journal:$('journal-filter').value,year:$('year-filter').value,type:$('type-filter').value,author:authorInput.value,status:statusFilter.value,tag:tagFilter.value,favorite:!!favoriteFilter.value,sort:$('paper-sort').value},statistics,journal_metrics:{...report.journal_metrics,matched_papers:filtered.filter(p=>p.journal_metric).length,matched_journals:groups.filter(j=>j.metric).length,source_kind_counts:groups.filter(j=>j.metric).reduce((counts,j)=>{const key=j.metric.source_kind||'supplied';counts[key]=(counts[key]||0)+1;return counts;},{})}};packet.keyword_trends=trendData(filtered);if(report.citation_network)packet.citation_network=filteredNetwork(filtered);if(report.comparison)packet.selection_note='Comparison overlap statistics describe the original searches; paper statistics describe this filtered selection.';downloadFile('search-'+report.search.id+'-filtered.json',JSON.stringify(packet,null,2),'application/json');});
$('export-journals').addEventListener('click',()=>{const groups=groupJournals(filtered),mode=$('journal-sort').value;groups.sort((a,b)=>(mode==='impact'?descending(a.metric?.impact_factor,b.metric?.impact_factor):mode==='count'?b.count-a.count:0)||a.name.localeCompare(b.name));const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('xmlns',ns);svg.setAttribute('width','1200');svg.setAttribute('height',String(75+groups.length*32));const maximum=Math.max(1,...groups.map(j=>j.count));function shape(tag,attrs,text){const node=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));if(text!==undefined)node.textContent=text;svg.append(node);}shape('rect',{width:'100%',height:'100%',fill:'white'});shape('text',{x:20,y:30,'font-family':'sans-serif','font-size':20},'Retrieved papers by journal · JIF metric year '+(report.journal_metrics.year||'unavailable'));groups.forEach((j,i)=>{const y=60+i*32;shape('text',{x:20,y,'font-family':'sans-serif','font-size':12},j.name);shape('rect',{x:720,y:y-14,width:340*j.count/maximum,height:18,fill:'#087f8c'});shape('text',{x:1075,y,'font-family':'sans-serif','font-size':12},j.count+' · IF '+(j.metric?.impact_factor??'N/A'));});downloadFile('journal-counts.svg',new XMLSerializer().serializeToString(svg),'image/svg+xml');});
if(report.comparison){
  const section=element('section',undefined,'panel');section.id='search-overlap';section.style.margin='16px 0';
  section.append(element('h2',report.merge?'Merged database searches':'Search comparison'));
  if(report.merge){
    section.append(element('p',report.merge.input_paper_count+' records across searches → '+report.merge.unique_paper_count+' unique papers; '+report.merge.duplicates_removed+' duplicate entries removed.'));
    section.append(element('p',report.merge.matching_policy+' '+report.merge.metadata_policy,'muted'));
    const attempted=new Set(report.comparison.searches.map(s=>s.source));
    const skipped=(report.merge.requested_sources||[]).filter(s=>!attempted.has(s));
    if(skipped.length)section.append(element('p','Databases not attempted: '+skipped.join(', ')+'.'));
  }
  section.append(element('p',report.comparison.union_count+' papers in the union; '+report.comparison.shared_by_all+' found by every search. Counts describe retrieved papers.'));
  const wrap=element('div',undefined,'table-wrap'),table=element('table'),head=element('tr');
  for(const label of ['Search','Database / query','Status','Retrieved unique','Exclusive'])head.append(element('th',label));table.append(head);
  for(const search of report.comparison.searches){
    const row=element('tr');
    for(const value of [search.id,search.source+' · '+search.query,search.status,search.unique_papers,search.exclusive_papers])row.append(element('td',String(value)));table.append(row);
    if(search.error)section.append(element('p',search.source+' search '+search.id+': '+search.error));
  }
  wrap.append(table);section.append(wrap);
  for(const pair of report.comparison.pairwise)section.append(element('p','Searches '+pair.left+' / '+pair.right+': '+pair.shared+' shared papers; Jaccard overlap '+(pair.jaccard===null?'unavailable':(100*pair.jaccard).toFixed(1)+'%')+'.','muted'));
  $('cards').after(section);
}
if(report.merge)$('status').textContent='Combined search: '+report.search.status+'. '+papers.length+' unique papers. Database ranks are retained separately.'+(report.search.error?' '+report.search.error:'');
render();

function trendData(rows) {
  const yearCounts = {}, sources = {};
  for (const p of rows) if (/^\d{4}$/.test(String(p.year || ''))) yearCounts[String(p.year)] = (yearCounts[String(p.year)] || 0) + 1;
  const years = Object.keys(yearCounts).sort();
  for (const field of ['keywords', 'mesh_terms', 'title_terms', 'title_phrases']) {
    const counts = new Map(), annual = {}, paperTerms = [];
    for (const p of rows) {
      const words = (p.title || '').toLocaleLowerCase().match(/[\p{L}\p{N}_-]+/gu) || [];
      const good = w => w.length > 2 && !stopWords.has(w) && !/^\d+$/.test(w);
      let values;
      if (field === 'keywords' || field === 'mesh_terms') values = (p[field] || []).map(v => String(v).replace(/\s+/g, ' ').trim().toLocaleLowerCase()).filter(Boolean);
      else if (field === 'title_terms') values = words.filter(good);
      else values = words.slice(1).map((w, i) => good(words[i]) && good(w) ? words[i] + ' ' + w : '').filter(Boolean);
      const terms = new Set(values); paperTerms.push(terms);
      for (const term of terms) {
        counts.set(term, (counts.get(term) || 0) + 1);
        if (yearCounts[String(p.year)]) { const bucket = annual[String(p.year)] ||= {}; bucket[term] = (bucket[term] || 0) + 1; }
      }
    }
    const chosen = [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a) || a.localeCompare(b)).slice(0, 30), pairs = new Map();
    for (const terms of paperTerms) {
      const active = chosen.filter(t => terms.has(t)).sort();
      for (let i = 0; i < active.length; i++) for (let j = i + 1; j < active.length; j++) {
        const key = JSON.stringify([active[i], active[j]]); pairs.set(key, (pairs.get(key) || 0) + 1);
      }
    }
    sources[field] = { covered_papers: paperTerms.filter(t => t.size).length,
      terms: chosen.map(term => ({term, paper_count: counts.get(term), years: Object.fromEntries(years.map(year => [year,
        {paper_count: annual[year]?.[term] || 0, total_papers: yearCounts[year], share: (annual[year]?.[term] || 0) / yearCounts[year]}]))})),
      cooccurrence: [...pairs].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 100).map(([key, count]) => {
        const [left, right] = JSON.parse(key); return {left, right, paper_count: count};
      }) };
  }
  return {paper_count: rows.length, year_counts: yearCounts, unknown_year_papers: rows.length - Object.values(yearCounts).reduce((a, b) => a + b, 0), sources,
    policy: 'Unique-paper counts. Year shares use all selected papers in that year; missing terms do not prove absence. Title phrases are adjacent word pairs.'};
}

function filteredNetwork(rows) {
  const ids = new Set(rows.map(p => p.id)), original = report.citation_network;
  if (!original) return null;
  const edges = original.edges.filter(e => e.citing !== e.cited && ids.has(e.citing) && ids.has(e.cited)), incoming = new Map(), outgoing = new Map();
  const outside = [...(original.resolved_outside_network || []).filter(e => ids.has(e.citing) || ids.has(e.cited)),
    ...original.edges.filter(e => e.citing !== e.cited && (ids.has(e.citing) || ids.has(e.cited)) && !(ids.has(e.citing) && ids.has(e.cited)))];
  const selfLinks = [...(original.flagged_self_links || []), ...original.edges.filter(e => e.citing === e.cited)].filter(e => ids.has(e.citing));
  const coverage = original.coverage.filter(p => ids.has(p.paper_id));
  for (const edge of edges) { incoming.set(edge.cited, (incoming.get(edge.cited) || 0) + 1); outgoing.set(edge.citing, (outgoing.get(edge.citing) || 0) + 1); }
  return {...original, nodes: original.nodes.filter(n => ids.has(n.id)).map(n => ({...n, incoming: incoming.get(n.id) || 0, outgoing: outgoing.get(n.id) || 0})),
    edges, flagged_self_links: selfLinks, resolved_outside_network: outside, resolved_outside_network_count: outside.length,
    unresolved_observations: coverage.reduce((sum,p) => sum+(p.unresolved_observations || 0), 0),
    ambiguous_observations: coverage.reduce((sum,p) => sum+(p.ambiguous_observations || 0), 0),
    statistics: {distinct_edges: edges.length, flagged_self_links: selfLinks.length, resolved_outside_network: outside.length},
    coverage, selection_note: 'Edges, degrees and outside relationships describe these filtered nodes. Legacy unmatched reference counts describe the original analysis.'};
}

const trendsPanel = element('section', undefined, 'panel'); trendsPanel.style.margin = '16px 0';
trendsPanel.append(element('h2', 'Keyword trends and relationships'));
const trendControls = element('div', undefined, 'filters');
function labeledSelect(parent, labelText, id) { const label = element('label', labelText), select = element('select'); select.id = id; label.append(select); parent.append(label); return select; }
const trendSource = labeledSelect(trendControls, 'Term source', 'trend-source');
for (const [value, label] of [['keywords', 'Author keywords'], ['mesh_terms', 'MeSH terms'], ['title_terms', 'Title words'], ['title_phrases', 'Title phrases']]) option(trendSource, value, label);
const trendTerm = labeledSelect(trendControls, 'Term', 'trend-term');
const trendMeasure = labeledSelect(trendControls, 'Measure', 'trend-measure'); option(trendMeasure, 'share', 'Share of papers (%)'); option(trendMeasure, 'count', 'Paper count');
const trendExport = element('button', 'Export trend JSON'); trendExport.id = 'export-trends'; trendControls.append(trendExport);
trendsPanel.append(trendControls);
const trendNote = element('p', undefined, 'muted'); trendNote.id = 'trend-note'; trendsPanel.append(trendNote);
const trendChart = element('div'); trendChart.id = 'trend-chart'; trendsPanel.append(trendChart);
const trendTable = element('div', undefined, 'table-wrap'); trendTable.id = 'trend-table'; trendsPanel.append(trendTable);
const pairsTable = element('div', undefined, 'table-wrap'); pairsTable.id = 'term-pairs';
const pairsDetails = element('details'); pairsDetails.append(element('summary', 'Co-occurring terms'), pairsTable); trendsPanel.append(pairsDetails);
document.querySelector('#results-title').closest('section').before(trendsPanel);

function simpleTable(parent, headers, rows) {
  parent.replaceChildren(); const table = element('table'), head = element('thead'), heading = element('tr');
  for (const text of headers) heading.append(element('th', text)); head.append(heading); table.append(head);
  const body = element('tbody'); for (const row of rows) { const tr = element('tr'); for (const text of row) tr.append(element('td', String(text))); body.append(tr); } table.append(body); parent.append(table);
}
function svgElement(tag, attrs, text) { const node = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value)); if (text !== undefined) node.textContent = text; return node; }
function drawTrend() {
  const data = trendData(filtered), source = data.sources[trendSource.value], previous = trendTerm.value;
  trendTerm.replaceChildren(); for (const term of source.terms) option(trendTerm, term.term, term.term + ' (' + term.paper_count + ' papers)');
  if (source.terms.some(t => t.term === previous)) trendTerm.value = previous;
  const selected = source.terms.find(t => t.term === trendTerm.value);
  trendNote.textContent = source.covered_papers + '/' + filtered.length + ' papers have terms from this source. ' + data.unknown_year_papers + ' papers have no valid publication year. Shares use all selected papers in each year. Title phrases are adjacent word pairs, not inferred topics.';
  trendChart.replaceChildren(); trendTable.replaceChildren();
  if (selected) {
    const points = Object.entries(selected.years), share = trendMeasure.value === 'share';
    simpleTable(trendTable, ['Year', 'Papers with term', 'All selected papers', 'Share'], points.map(([year, d]) => [year, d.paper_count, d.total_papers, (100 * d.share).toFixed(1) + '%']));
    const svg = svgElement('svg', {viewBox: '0 0 800 240', role: 'img', 'aria-label': 'Publication-year trend for ' + selected.term}); svg.style.width = '100%'; svg.style.maxHeight = '260px';
    const values = points.map(([, d]) => share ? 100 * d.share : d.paper_count), max = Math.max(1, ...values);
    const coords = values.map((v, i) => [60 + i * 680 / Math.max(1, values.length - 1), 190 - v * 160 / max]);
    svg.append(svgElement('line', {x1: 60, y1: 190, x2: 740, y2: 190, stroke: '#94a3b8'}));
    svg.append(svgElement('polyline', {points: coords.map(p => p.join(',')).join(' '), fill: 'none', stroke: '#087f8c', 'stroke-width': 3}));
    coords.forEach(([x, y], i) => { const dot = svgElement('circle', {cx: x, cy: y, r: 5, fill: '#087f8c'}); dot.append(svgElement('title', {}, points[i][0] + ': ' + values[i].toFixed(1) + (share ? '%' : ' papers'))); svg.append(dot, svgElement('text', {x, y: 218, 'text-anchor': 'middle', 'font-size': 12}, points[i][0])); });
    svg.append(svgElement('text', {x: 12, y: 25, 'font-size': 12}, share ? 'Share (%)' : 'Papers')); trendChart.append(svg);
  } else trendChart.append(element('p', 'No terms available for this source and selection.', 'empty'));
  simpleTable(pairsTable, ['Term 1', 'Term 2', 'Papers containing both'], source.cooccurrence.map(p => [p.left, p.right, p.paper_count]));
}
for (const control of [trendSource, trendTerm, trendMeasure]) control.addEventListener('change', drawTrend);
trendExport.addEventListener('click', () => downloadFile('literature-trends.json', JSON.stringify(trendData(filtered), null, 2), 'application/json'));

let networkPanel, networkNode, networkNote, networkDrawing, networkTable;
if (report.citation_network) {
  networkPanel = element('section', undefined, 'panel'); networkPanel.style.margin = '16px 0'; networkPanel.append(element('h2', 'Citation network'));
  const controls = element('div', undefined, 'filters'); networkNode = labeledSelect(controls, 'Connections for paper', 'network-node');
  const exportButton = element('button', 'Export network JSON'); exportButton.id = 'export-network'; controls.append(exportButton); networkPanel.append(controls);
  networkNote = element('p', undefined, 'muted'); networkNote.id = 'network-note'; networkPanel.append(networkNote);
  networkDrawing = element('div'); networkDrawing.id = 'citation-graph'; networkPanel.append(networkDrawing);
  networkTable = element('div', undefined, 'table-wrap'); networkTable.id = 'network-edges'; networkPanel.append(networkTable);
  document.querySelector('#results-title').closest('section').before(networkPanel);
  networkNode.addEventListener('change', drawNetwork);
  exportButton.addEventListener('click', () => downloadFile('literature-network.json', JSON.stringify(filteredNetwork(filtered), null, 2), 'application/json'));
}
function drawNetwork() {
  if (!networkPanel) return;
  const data = filteredNetwork(filtered), previous = networkNode.value;
  networkNode.replaceChildren(); option(networkNode, '', 'All connections');
  for (const n of data.nodes) option(networkNode, String(n.id), n.id + ' · ' + n.title);
  if (data.nodes.some(n => String(n.id) === previous)) networkNode.value = previous;
  const selected = networkNode.value, edges = data.edges.filter(e => !selected || String(e.citing) === selected || String(e.cited) === selected);
  const connected = new Set(edges.flatMap(e => [e.citing, e.cited])), nodes = data.nodes.filter(n => connected.has(n.id)).sort((a, b) => b.incoming + b.outgoing - a.incoming - a.outgoing).slice(0, 60);
  const visible = new Set(nodes.map(n => n.id)), names = new Map(data.nodes.map(n => [n.id, n.title]));
  networkNote.textContent = data.nodes.length + ' papers; ' + data.edges.length + ' in-corpus links. ' + data.coverage.filter(p => p.reference_count > 0).length + ' papers have identified references. Degrees count links only within the displayed network, not global citations. ' + (data.flagged_self_links?.length || 0) + ' article-to-itself links flagged and excluded. Resolved outside the displayed network: ' + (data.resolved_outside_network_count ?? 'unknown') + '; unresolved observations: ' + (data.unresolved_observations ?? 'unknown') + '; ambiguous/review observations: ' + (data.ambiguous_observations ?? 'unknown') + '. Arrows run from citing to cited paper. Missing lists can hide links. Graph displays up to 60 connected nodes; JSON exports all filtered nodes and edges.';
  networkDrawing.replaceChildren();
  const svg = svgElement('svg', {viewBox: '0 0 800 440', role: 'img', 'aria-label': 'Directed citation network'}); svg.style.width = '100%'; svg.style.maxHeight = '500px';
  const defs = svgElement('defs', {}), marker = svgElement('marker', {id: 'citation-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto'});
  marker.append(svgElement('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: '#94a3b8'})); defs.append(marker); svg.append(defs);
  const positions = new Map(nodes.map((n, i) => { const angle = i * 2 * Math.PI / Math.max(1, nodes.length); return [n.id, [400 + 310 * Math.cos(angle), 220 + 165 * Math.sin(angle)]]; }));
  for (const e of edges) if (visible.has(e.citing) && visible.has(e.cited)) {
    const [x1, y1] = positions.get(e.citing), [x2, y2] = positions.get(e.cited), length = Math.hypot(x2 - x1, y2 - y1) || 1;
    const line = svgElement('line', {x1, y1, x2: x2 - 13 * (x2 - x1) / length, y2: y2 - 13 * (y2 - y1) / length, stroke: '#94a3b8', 'marker-end': 'url(#citation-arrow)'}); line.append(svgElement('title', {}, e.citing + ' cites ' + e.cited)); svg.append(line);
  }
  for (const n of nodes) {
    const [x, y] = positions.get(n.id), group = svgElement('g', {tabindex: 0, role: 'button', 'aria-label': 'Connections for ' + n.title}); group.style.cursor = 'pointer';
    group.append(svgElement('circle', {cx: x, cy: y, r: 12, fill: '#087f8c'}), svgElement('text', {x, y: y + 4, 'text-anchor': 'middle', 'font-size': 10, fill: 'white'}, n.id), svgElement('title', {}, n.title + '\nIncoming (displayed network): ' + n.incoming + '; outgoing (displayed network): ' + n.outgoing));
    const select = () => { networkNode.value = String(n.id); drawNetwork(); }; group.addEventListener('click', select); group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') select(); }); svg.append(group);
  }
  if (edges.length) networkDrawing.append(svg); else networkDrawing.append(element('p', 'No exact citation links between these papers were found. Reference coverage may be incomplete.', 'empty'));
  simpleTable(networkTable, ['Citing paper', 'Cited paper', 'Providers'], edges.slice(0, 100).map(e => [e.citing + ' · ' + names.get(e.citing), e.cited + ' · ' + names.get(e.cited), e.providers.join(', ')]));
}
const beforeResearchRender = render; render = function() { beforeResearchRender(); drawTrend(); drawNetwork(); };
render();

if (report.reference_analysis) {
  const analysis = report.reference_analysis, c = analysis.coverage;
  const panel = element('section', undefined, 'panel'); panel.style.margin = '16px 0'; panel.id = 'reference-coverage';
  panel.append(element('h2', 'Reference coverage and publication identities'));
  panel.append(element('p', `${c.papers_processed}/${c.papers_requested} papers processed; ${c.raw_observations} raw provider observations; ${c.resolved_distinct_publications} distinct identified referenced publications. Unresolved: ${c.unresolved_observations}; ambiguous: ${c.ambiguous_matches}; review candidates: ${c.review_required_observations}; conflicts: ${c.conflicting_observations}. Citation edges: ${c.distinct_citation_edges}; article-to-itself links flagged: ${c.flagged_self_links}.`));
  panel.append(element('p', 'Counts describe the original reference analysis. Identifiers may consolidate further after bridging. Missing lists are unavailable, never zero references. Retrieval: ' + c.retrieval_status + '; resolution: ' + c.resolution_status + '; bibliography completeness: unknown.', 'muted'));
  const browse = element('a', 'Browse and export referenced publications and original evidence');
  browse.href = analysis.reference_dashboard_href || analysis.reference_dashboard.replace(/\\/g, '/').split('/').pop(); browse.target = '_blank'; browse.rel = 'noopener noreferrer'; panel.append(browse);
  const details = element('details'), coverage = element('div', undefined, 'table-wrap');
  details.append(element('summary', 'Provider retrieval and pagination'), coverage);
  simpleTable(coverage, ['Paper', 'Provider', 'Outcome', 'Reported references', 'Raw observations', 'Pagination'], analysis.provider_outcomes.map(p => [p.paper_id, p.provider, p.status, p.reported_count ?? 'Unavailable', p.raw_observations, p.pagination_status || 'Unknown']));
  panel.append(details); document.querySelector('#results-title').closest('section').before(panel);
}
