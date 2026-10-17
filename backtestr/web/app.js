"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = {config: null, strategy: null, assumptions: [], answers: {}, report: null,
  dataset: "demo", datasets: new Map(), revision: 0, tradePage: 0, dirtySpec: false};
const labels = {name:"Strategy name",description:"Description",symbols:"Stock symbols",benchmark:"Benchmark",
  signal:"Signal",fast_window:"Fast SMA · days",slow_window:"Slow SMA · days",lookback:"Lookback · days",
  entry_threshold:"Entry threshold",exit_threshold:"Exit threshold",initial_cash:"Starting capital · USD",
  max_position_weight:"Position cap · fraction",rebalance:"Rebalance",commission_bps:"Commission · bps",
  slippage_bps:"Slippage · bps",start_date:"Start date",end_date:"End date"};
const signalNames = {sma_cross:"SMA trend",rsi:"RSI mean reversion",momentum:"Price momentum",buy_hold:"Buy & hold"};
const basePrompt = "Initial capital $100,000, maximum 50% per stock. Rebalance daily from 2020-01-01 to 2025-12-31. Benchmark SPY, commission 1 bps and slippage 5 bps.";
const examples = {
  trend: "Trade AAPL and MSFT with a 20/100 SMA crossover. " + basePrompt,
  rsi: "Trade AAPL and MSFT using 14-day RSI. Buy when RSI is below 30 and sell when RSI is above 70. " + basePrompt,
  hold: "Buy and hold AAPL and MSFT. " + basePrompt
};
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const pct = (value, decimals=1) => Number.isFinite(value) ? `${(value*100).toFixed(decimals)}%` : "—";
const number = (value, decimals=2) => Number.isFinite(value) ? value.toLocaleString("en-US", {minimumFractionDigits:decimals,maximumFractionDigits:decimals}) : "—";
const money = (value) => Number.isFinite(value) ? new Intl.NumberFormat("en-US", {style:"currency",currency:"USD",maximumFractionDigits:2}).format(value) : "—";

async function api(path, body) {
  let response;
  try {response = await fetch(path, body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});}
  catch {throw new Error("The local server could not be reached. Restart it with python -m backtestr, then retry.");}
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {message = (await response.json()).error || message;} catch {}
    throw new Error(message);
  }
  return response.headers.get("Content-Type")?.includes("application/zip") ? response.blob() : response.json();
}
function feedback(element, text, error=false) {
  element.textContent = text;
  element.classList.toggle("error", error);
  element.hidden = !text;
}
let toastTimer;
function toast(message) {
  $("#toast").textContent = message; $("#toast").hidden=false;
  clearTimeout(toastTimer); toastTimer=setTimeout(() => {$("#toast").hidden=true;}, 4000);
}
function busy(button, active, text, original) {
  button.disabled = active; button.textContent = active ? text : original;
  button.setAttribute("aria-busy", String(active));
}
function download(blob, filename) {
  const url=URL.createObjectURL(blob); const anchor=document.createElement("a");
  anchor.href=url;anchor.download=filename;document.body.append(anchor);anchor.click();anchor.remove();
  setTimeout(()=>URL.revokeObjectURL(url), 30000);
}
function saveJSON(value, filename) {download(new Blob([JSON.stringify(value,null,2)],{type:"application/json"}),filename);}
function switchView(view) {
  $$(".view").forEach(v => {v.hidden=v.id!==`view-${view}`;});
  $$(".nav-item").forEach(b => {const active=b.dataset.view===view;b.classList.toggle("active",active);if(active)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");});
  const heading=$(`#view-${view} h1`);heading.tabIndex=-1;heading.focus({preventScroll:true});
  if(view==="lab" && state.report)renderChart();
}
function invalidateReport() {
  state.revision++;state.report=null;state.tradePage=0;
  $("#report-content").hidden=true;$("#report-loading").hidden=true;$("#report-empty").hidden=false;
  $("#report-state").textContent="No study yet";$("#report-state").className="badge neutral";
  $("#report-subtitle").textContent="An honest view of your strategy, across time.";
}
function invalidateStrategy() {
  invalidateReport();state.strategy=null;state.answers={};state.assumptions=[];state.dirtySpec=false;
  $("#strategy-review").hidden=true;$("#clarification-form").hidden=true;
  $("#acknowledge").checked=false;feedback($("#interpret-feedback"),"");
}
function updateRunButton() {
  $("#run-button").disabled=!state.strategy || !$("#acknowledge").checked || state.dirtySpec;
  $("#fetch-alpaca").disabled=!state.strategy || !state.config?.alpaca_configured;
  $("#export-button").disabled=!state.report || !$("#acknowledge").checked || state.dirtySpec;
}
function updateDescription() {
  $("#character-count").textContent=`${$("#description").value.length.toLocaleString()} / 12,000`;
  try {localStorage.setItem("backtestr-draft",$("#description").value);} catch {}
}
function renderQuestions(questions) {
  const hints={symbols:"AAPL, MSFT",signal:"sma_cross, rsi, momentum, or buy_hold",initial_cash:"100000",max_position_weight:"50%",rebalance:"daily, weekly, or monthly",start_date:"2020-01-01",end_date:"2025-12-31",fast_window:"20",slow_window:"100",lookback:"14",entry_threshold:"30 for RSI; 0.05 for 5% momentum",exit_threshold:"70 for RSI"};
  $("#questions").innerHTML=questions.map((q,i)=>`<div class="question"><label for="question-${i}">${escapeHTML(q.question)}</label><input id="question-${i}" name="${escapeHTML(q.id)}" list="options-${i}" placeholder="${escapeHTML(hints[q.id]||"Your answer")}" value="${escapeHTML(state.answers[q.id]||"")}" required autocomplete="off"><datalist id="options-${i}">${(q.options||[]).map(o=>`<option value="${escapeHTML(o)}"></option>`).join("")}</datalist></div>`).join("");
  $("#clarification-form").hidden=false;
  $("#questions input")?.focus({preventScroll:true});
}
async function interpret() {
  const description=$("#description").value.trim();
  if(description.length<5){feedback($("#interpret-feedback"),"Add a strategy description of at least five characters.",true);return;}
  invalidateReport();state.strategy=null;$("#strategy-review").hidden=true;
  const revision=state.revision;
  busy($("#interpret-button"),true,"Interpreting your rules…","Build strategy →");
  busy($("#answer-button"),true,"Applying answers…","Apply answers →");
  feedback($("#interpret-feedback"),"");
  try {
    const result=await api("/api/interpret",{description,answers:state.answers,provider:$("#provider").value});
    if(revision!==state.revision)return;
    $("#provider-label").textContent=result.provider==="openai" ? "AI interpretation" : "Local parser";
    feedback($("#interpret-feedback"),result.message+(result.unsupported?.length ? " "+result.unsupported.join(" ") : ""),result.status==="unsupported");
    $("#clarification-form").hidden=true;
    if(result.status==="ready" && result.strategy) {
      state.strategy=result.strategy;state.assumptions=result.assumptions||[];renderReview();
    } else if(result.questions?.length)renderQuestions(result.questions);
  } catch(error) {if(revision===state.revision)feedback($("#interpret-feedback"),error.message,true);}
  finally {busy($("#interpret-button"),false,"","Build strategy →");busy($("#answer-button"),false,"","Apply answers →");updateRunButton();}
}
function ruleDescription(spec) {
  if(spec.signal==="sma_cross")return `Hold when the ${spec.fast_window}-day simple moving average is above the ${spec.slow_window}-day average.`;
  if(spec.signal==="rsi")return `Enter when ${spec.lookback}-day rolling RSI ≤ ${spec.entry_threshold}; exit when RSI ≥ ${spec.exit_threshold}.`;
  if(spec.signal==="momentum")return `Hold when the trailing ${spec.lookback}-day price return exceeds ${pct(spec.entry_threshold)}.`;
  return "Hold all selected stocks, with weights reset on the selected rebalance schedule.";
}
function renderReview() {
  const s=state.strategy;if(!s)return;
  $("#strategy-summary").textContent=ruleDescription(s);
  const values=[["Universe",s.symbols.join(" · ")],["Signal",signalNames[s.signal]],["Capital",money(s.initial_cash)],["Position cap",pct(s.max_position_weight)],["Rebalance",s.rebalance],["Period",`${s.start_date} → ${s.end_date}`]];
  $("#rule-summary").innerHTML=values.map(([key,value])=>`<div><dt>${escapeHTML(key)}</dt><dd>${escapeHTML(value)}</dd></div>`).join("");
  const always=state.assumptions.some(a=>a.includes("Long-only US"))?[]:["Long only; whole shares; no leverage. Equal weights among active symbols, capped per position; remainder stays in cash.","Completed-close signals execute at the next session’s open, on rebalance dates. Weekly/monthly means the first available session of the week/month. The final portfolio is marked at the closing price.",`Commission ${s.commission_bps} bps and slippage ${s.slippage_bps} bps per side. Benchmark: ${s.benchmark}.`,...(s.signal==="rsi"?["RSI uses simple rolling mean gains and losses, not Wilder smoothing. The initial state depends on the supplied warmup history."]:[])];
  const items=[...new Set([...state.assumptions,...always])];
  $("#assumption-list").innerHTML=items.map(text=>`<li>${escapeHTML(text)}</li>`).join("");
  const activeFields={sma_cross:["fast_window","slow_window"],rsi:["lookback","entry_threshold","exit_threshold"],momentum:["lookback","entry_threshold"],buy_hold:[]};
  const hiddenParams=["fast_window","slow_window","lookback","entry_threshold","exit_threshold"].filter(k=>!activeFields[s.signal].includes(k));
  $("#spec-fields").innerHTML=Object.entries(s).filter(([k])=>k!=="description"&&!hiddenParams.includes(k)).map(([key,value])=>{
    const options=key==="signal"?Object.keys(signalNames):key==="rebalance"?["daily","weekly","monthly"]:null;
    const control=options ? `<select data-field="${key}">${options.map(o=>`<option value="${o}"${o===value?" selected":""}>${escapeHTML(signalNames[o]||o)}</option>`).join("")}</select>` : `<input data-field="${key}" type="${key.endsWith("date")?"date":typeof value==="number"?"number":"text"}"${typeof value==="number"?' step="any"':""} value="${escapeHTML(Array.isArray(value)?value.join(", "):value)}">`;
    return `<label class="${["name","symbols"].includes(key)?"wide":""}">${escapeHTML(labels[key]||key)}${control}</label>`;
  }).join("");
  $("#strategy-review").hidden=false;$("#acknowledge").checked=false;state.dirtySpec=false;
  updateDataset();updateRunButton();
}
function updateDataset() {
  const data=state.datasets.get(state.dataset);if(!data)return;
  $("#dataset-select").value=state.dataset;
  $("#dataset-caption").textContent=`${data.synthetic?"Synthetic prices · demonstration only":"Imported/provider prices"} · ${data.start_date} to ${data.end_date}`;
  $("#loaded-dataset").innerHTML=`<dl><dt>Source</dt><dd>${escapeHTML(data.source)}</dd><dt>Coverage</dt><dd>${escapeHTML(data.start_date)} → ${escapeHTML(data.end_date)}</dd><dt>Symbols</dt><dd>${escapeHTML(data.symbols.join(", "))}</dd><dt>Rows</dt><dd>${number(data.rows,0)}</dd><dt>Price basis</dt><dd>${escapeHTML(data.adjustment)}</dd></dl>`;
}
function addDataset(data) {
  state.datasets.set(data.id,data);state.dataset=data.id;
  const option=document.createElement("option");option.value=data.id;option.textContent=`${data.source} · ${data.symbols.join(", ")}`;
  $("#dataset-select").append(option);updateDataset();invalidateReport();updateRunButton();
}
function metric(label,value,note) {return `<div><div class="metric-label">${label}</div><div class="metric-value">${value}</div><div class="metric-note">${note}</div></div>`;}
function table(headers,rows) {return `<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th scope="col">${escapeHTML(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;}
function metricsTable(rows,labelKey="label") {
  if(!rows?.length)return '<p class="small-text">Not enough observations for this diagnostic.</p>';
  return table(["Period / scenario","Return","CAGR","Sharpe","Drawdown"],rows.map(r=>{
    const m=r.metrics||r;const label=r[labelKey]??r.name??r.period??"Period";
    return `<tr><td>${escapeHTML(label)}${r.start_date?`<br><span class="small-text">${escapeHTML(r.start_date)} → ${escapeHTML(r.end_date)}</span>`:""}</td><td class="numeric">${pct(m.total_return)}</td><td class="numeric">${pct(m.cagr)}</td><td class="numeric">${number(m.sharpe)}</td><td class="numeric">${pct(m.max_drawdown)}</td></tr>`;
  }));
}
function renderRobustness() {
  const r=state.report.robustness;const split=r.split||{};
  const splitRows=[split.training?{label:"Development · first 70%",...split.training}:null,split.holdout?{label:"Holdout · final 30%",...split.holdout}:null].filter(Boolean);
  const bootstrap=r.bootstrap||{};
  $("#robustness").innerHTML=`<div class="diagnostic-note">${state.report.dataset.synthetic?"This study uses synthetic prices. The diagnostics demonstrate the workflow; they are not evidence of a historical trading edge.":"A positive return is one observation. Compare the holdout, independent periods, and cost sensitivity before drawing a conclusion."}</div>
    <div class="robust-section"><h4>Chronological holdout</h4><p>${escapeHTML(split.method||"Fixed parameters; each period starts with fresh capital and prior indicator history.")}</p>${metricsTable(splitRows)}<p>Viewing the holdout repeatedly while editing the rules makes it part of your development process.</p></div>
    <div class="robust-section"><h4>Transaction-cost stress</h4><p>Commission and slippage are increased together. The signal and capital stay fixed.</p>${metricsTable((r.cost_stress||[]).map(x=>({...x,label:`${x.multiplier}× base costs`})))} </div>
    <div class="robust-section"><h4>Block-bootstrap range</h4><p>${escapeHTML(bootstrap.method||"Resampled daily returns in contiguous blocks.")}</p><div class="bootstrap-range"><div><small>Lower return estimate</small>${pct(bootstrap.lower_return)}</div><div><small>${pct(bootstrap.confidence,0)} interval</small>${number(bootstrap.samples,0)} samples</div><div><small>Upper return estimate</small>${pct(bootstrap.upper_return)}</div></div><p>${escapeHTML(bootstrap.note||"Conditional on the observed return series; this is not a forecast or a probability of profitability.")}</p></div>
    <div class="robust-section"><h4>Independent calendar periods</h4><p>Each period starts from cash; prior history warms the indicators.</p>${metricsTable(r.annual)}</div>
    <details class="robust-section"><summary>Inspect rolling periods (${r.rolling?.length||0})</summary>${metricsTable(r.rolling)}</details>`;
}
function renderTrades() {
  const trades=state.report?.trades||[];const pages=Math.max(1,Math.ceil(trades.length/20));
  state.tradePage=Math.max(0,Math.min(state.tradePage,pages-1));const start=state.tradePage*20;
  $("#trade-table").innerHTML=trades.length ? table(["Date","Symbol","Side","Shares","Fill price","Commission"],trades.slice(start,start+20).map(t=>`<tr><td>${escapeHTML(t.date)}</td><td class="ticker">${escapeHTML(t.symbol)}</td><td><span class="${t.side.toLowerCase()==="buy"?"buy-badge":"sell-badge"}">${escapeHTML(t.side)}</span></td><td class="numeric">${number(t.shares,0)}</td><td class="numeric">${money(t.price)}</td><td class="numeric">${money(t.commission)}</td></tr>`)) : '<p class="small-text">No fills were generated in this period. Check the signal, capital, and share prices.</p>';
  $("#trade-page").textContent=`${trades.length?start+1:0}–${Math.min(start+20,trades.length)} of ${number(trades.length,0)} fills`;
  $("#trades-prev").disabled=state.tradePage===0;$("#trades-next").disabled=state.tradePage>=pages-1;
}
function renderChart() {
  if($("#panel-overview").hidden || $("#view-lab").hidden)return;
  const rows=state.report.equity;const initial=state.report.strategy.initial_cash;
  const width=Math.max(300,$("#equity-chart").clientWidth),height=window.innerWidth<570?240:270,pad={left:48,right:12,top:14,bottom:32};
  const values=rows.flatMap(r=>[r.equity/initial-1,r.benchmark/initial-1]);
  let low=0,high=0.01;for(const value of values){low=Math.min(low,value);high=Math.max(high,value);}const spread=high-low;low-=spread*.05;high+=spread*.08;
  const x=i=>pad.left+i/(rows.length-1)*(width-pad.left-pad.right);
  const y=v=>pad.top+(high-v)/(high-low)*(height-pad.top-pad.bottom);
  const step=Math.max(1,Math.floor(rows.length/850));const indices=[];for(let i=0;i<rows.length;i+=step)indices.push(i);if(indices.at(-1)!==rows.length-1)indices.push(rows.length-1);
  const line=key=>indices.map((i,n)=>`${n?"L":"M"}${x(i).toFixed(2)},${y(rows[i][key]/initial-1).toFixed(2)}`).join(" ");
  let axes="";
  for(let i=0;i<5;i++){const value=low+(high-low)*i/4;const py=y(value);axes+=`<line class="grid-line" x1="${pad.left}" x2="${width-pad.right}" y1="${py}" y2="${py}"/><text class="axis-label" text-anchor="end" x="${pad.left-10}" y="${py+4}">${escapeHTML(pct(value,0))}</text>`;}
  const tickCount=window.innerWidth<570?3:5;
  for(let i=0;i<tickCount;i++){const index=Math.round((rows.length-1)*i/(tickCount-1));axes+=`<text class="axis-label" x="${x(index)}" y="${height-8}" text-anchor="${i===0?"start":i===tickCount-1?"end":"middle"}">${escapeHTML(rows[index].date.slice(0,7))}</text>`;}
  const path=line("equity");const area=`${path} L${x(rows.length-1)},${height-pad.bottom} L${pad.left},${height-pad.bottom} Z`;
  $("#equity-chart").innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Portfolio return ${escapeHTML(pct(state.report.metrics.total_return))} versus benchmark ${escapeHTML(pct(state.report.metrics.benchmark_return))}. Full daily values are available in the downloadable report."><path class="equity-area" d="${area}"/>${axes}<path class="benchmark-line" d="${line("benchmark")}"/><path class="equity-line" d="${path}"/><line id="chart-cursor" class="chart-cursor" y1="${pad.top}" y2="${height-pad.bottom}" visibility="hidden"/><circle id="chart-point" r="4" fill="#173e33" visibility="hidden"/></svg>`;
  const svg=$("#equity-chart svg");
  svg.addEventListener("pointermove",event=>{
    const box=svg.getBoundingClientRect();const px=(event.clientX-box.left)/box.width*width;
    const index=Math.max(0,Math.min(rows.length-1,Math.round((px-pad.left)/(width-pad.left-pad.right)*(rows.length-1))));
    const row=rows[index];const cursor=$("#chart-cursor"),point=$("#chart-point");
    cursor.setAttribute("x1",x(index));cursor.setAttribute("x2",x(index));cursor.setAttribute("visibility","visible");
    point.setAttribute("cx",x(index));point.setAttribute("cy",y(row.equity/initial-1));point.setAttribute("visibility","visible");
    $("#chart-value").textContent=`${row.date} · ${money(row.equity)} · ${pct(row.equity/initial-1)}`;
  });
  svg.addEventListener("pointerleave",()=>{$("#chart-cursor").setAttribute("visibility","hidden");$("#chart-point").setAttribute("visibility","hidden");$("#chart-value").textContent="Hover to inspect";});
}
function renderReport() {
  const report=state.report,m=report.metrics,s=report.strategy;
  $("#report-loading").hidden=true;$("#report-empty").hidden=true;$("#report-content").hidden=false;
  $("#report-state").textContent=report.dataset.synthetic?"Synthetic study":"Study complete";
  $("#report-state").className=`badge ${report.dataset.synthetic?"synthetic":""}`;
  $("#report-subtitle").textContent=`${s.name} · ${s.symbols.join(" / ")}`;
  $("#report-provenance").className=`provenance${report.dataset.synthetic?"":" real"}`;
  $("#report-provenance").textContent=report.dataset.synthetic?"Synthetic demonstration prices — these returns do not represent real stock performance.":`${report.dataset.source} · ${report.dataset.adjustment} prices · Verify corporate actions and point-in-time universe coverage.`;
  $("#chart-range").textContent=`${report.equity[0].date} → ${report.equity.at(-1).date}`;
  $("#benchmark-name").textContent=s.benchmark;
  $("#metrics").innerHTML=metric("Total return",pct(m.total_return),`Benchmark ${pct(m.benchmark_return)}`)+metric("Annualized return",pct(m.cagr),"Calendar-time CAGR")+metric("Maximum drawdown",pct(m.max_drawdown),"Peak-to-trough loss")+metric("Sharpe ratio",number(m.sharpe),"252 days · 0% risk-free");
  const positions=report.portfolio.filter(p=>p.shares>0);
  const rows=positions.map(p=>`<tr><td class="ticker">${escapeHTML(p.symbol)}</td><td class="numeric">${number(p.shares,0)}</td><td class="numeric">${money(p.value)}</td><td class="numeric">${pct(p.weight)}</td></tr>`);
  rows.push(`<tr class="cash-row"><td>Cash (USD)</td><td class="numeric">—</td><td class="numeric">${money(report.cash)}</td><td class="numeric">${pct(report.cash/m.final_equity)}</td></tr>`);
  rows.push(`<tr><td><strong>Total portfolio</strong></td><td></td><td class="numeric"><strong>${money(m.final_equity)}</strong></td><td class="numeric">100.0%</td></tr>`);
  $("#portfolio").innerHTML=table(["Holding","Shares","Market value","Weight"],rows);
  $("#report-warnings").innerHTML=(report.warnings||[]).map(w=>`<li>${escapeHTML(w)}</li>`).join("");
  renderChart();renderRobustness();renderTrades();switchTab("overview");updateRunButton();
}
function switchTab(tab) {
  $$("[data-tab]").forEach(button=>{const selected=button.dataset.tab===tab;button.setAttribute("aria-selected",String(selected));button.tabIndex=selected?0:-1;});
  $$(".tab-panel").forEach(panel=>{panel.hidden=panel.id!==`panel-${tab}`;});
  if(tab==="overview" && state.report)renderChart();
  if(tab==="code" && state.report)loadSource();
}
async function loadSource() {
  const revision=state.revision;
  try {const result=await api("/api/preview",{strategy:state.strategy,dataset_id:state.dataset});if(revision===state.revision)$("#source-preview").textContent=result.source;}
  catch(error){$("#source-preview").textContent=error.message;}
}
async function runStudy() {
  if(!state.strategy||!$("#acknowledge").checked||state.dirtySpec)return;
  invalidateReport();const revision=state.revision;
  $("#report-empty").hidden=true;$("#report-loading").hidden=false;$("#report-state").textContent="Testing…";
  $(".results").scrollIntoView({block:"start",behavior:window.matchMedia("(prefers-reduced-motion: reduce)").matches?"instant":"smooth"});
  busy($("#run-button"),true,"Running study…","Run backtest →");feedback($("#interpret-feedback"),"");
  try {const report=await api("/api/backtest",{strategy:state.strategy,dataset_id:state.dataset});if(revision!==state.revision)return;state.report=report;renderReport();toast("Study complete. Inspect the robustness checks before exporting.");}
  catch(error){if(revision===state.revision){invalidateReport();feedback($("#interpret-feedback"),error.message,true);}}
  finally {busy($("#run-button"),false,"","Run backtest →");updateRunButton();}
}

$$(".nav-item").forEach(b=>b.addEventListener("click",()=>switchView(b.dataset.view)));
$$(".return-lab").forEach(b=>b.addEventListener("click",()=>switchView("lab")));
$("#go-data").addEventListener("click",()=>switchView("data"));
$("#idea-form").addEventListener("submit",event=>{event.preventDefault();state.answers={};interpret();});
$("#clarification-form").addEventListener("submit",event=>{event.preventDefault();state.answers={...state.answers,...Object.fromEntries(new FormData(event.target))};interpret();});
$("#description").addEventListener("input",()=>{invalidateStrategy();updateDescription();updateRunButton();});
$("#provider").addEventListener("change",()=>{invalidateStrategy();updateRunButton();});
$$("[data-example]").forEach(b=>b.addEventListener("click",()=>{invalidateStrategy();$("#description").value=examples[b.dataset.example];updateDescription();$("#description").focus();}));
$("#acknowledge").addEventListener("change",updateRunButton);
$("#run-button").addEventListener("click",runStudy);
$("#spec-fields").addEventListener("input",()=>{state.dirtySpec=true;$("#acknowledge").checked=false;invalidateReport();updateRunButton();});
$("#apply-spec").addEventListener("click",async()=>{
  if(!state.strategy)return;
  const next={...state.strategy},revision=state.revision;
  $$("[data-field]").forEach(input=>{const k=input.dataset.field;next[k]=k==="symbols"?input.value.split(/[,\s]+/).filter(Boolean):typeof next[k]==="number"?Number(input.value):input.value;});
  try {await api("/api/preview",{strategy:next});if(revision!==state.revision)return;state.strategy=next;state.assumptions=["The specification was manually edited after interpretation. The displayed rules replace the original interpretation and its assumptions.",ruleDescription(next),"Parameters belonging to inactive indicators have no effect."];invalidateReport();renderReview();feedback($("#interpret-feedback"),"Specification updated. Review the rules and assumptions again.");}
  catch(error){feedback($("#interpret-feedback"),error.message,true);}
});
$("#dataset-select").addEventListener("change",event=>{state.dataset=event.target.value;invalidateReport();updateDataset();updateRunButton();});
$("#save-strategy").addEventListener("click",()=>{if(state.dirtySpec){toast("Apply specification changes before saving.");return;}if(state.strategy)saveJSON(state.strategy,"strategy.json");});
$("#new-study").addEventListener("click",()=>{invalidateStrategy();$("#description").value="";updateDescription();updateRunButton();$("#description").focus();toast("New study started. Save any strategy you want to keep as JSON.");});
$("#open-strategy").addEventListener("click",()=>$("#strategy-file").click());
$("#strategy-file").addEventListener("change",async event=>{
  const file=event.target.files[0];if(!file)return;
  try {if(file.size>100000)throw new Error("Strategy JSON must be smaller than 100 KB.");const spec=JSON.parse(await file.text());await api("/api/preview",{strategy:spec});invalidateStrategy();state.strategy=spec;state.assumptions=["Loaded from the supplied JSON file. The specification, rather than an English interpretation, defines this strategy."];$("#description").value=spec.description;updateDescription();renderReview();switchView("lab");feedback($("#interpret-feedback"),"Strategy loaded. Review its rules and assumptions, then run a study.");}
  catch(error){toast(error.message);}finally{event.target.value="";}
});
$$("[data-tab]").forEach(button=>{button.addEventListener("click",()=>switchTab(button.dataset.tab));button.addEventListener("keydown",event=>{if(!["ArrowRight","ArrowLeft","Home","End"].includes(event.key))return;event.preventDefault();const tabs=$$("[data-tab]");let i=tabs.indexOf(button);i=event.key==="Home"?0:event.key==="End"?tabs.length-1:(i+(event.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length;switchTab(tabs[i].dataset.tab);tabs[i].focus();});});
$("#trades-prev").addEventListener("click",()=>{state.tradePage--;renderTrades();});
$("#trades-next").addEventListener("click",()=>{state.tradePage++;renderTrades();});
$$(".download-report").forEach(b=>b.addEventListener("click",()=>{if(state.report)saveJSON({...state.report,interpretation_assumptions:state.assumptions},"backtestr-report.json");}));
$("#download-trades").addEventListener("click",()=>{
  if(!state.report)return;const fields=["date","symbol","side","shares","price","commission","slippage"];
  const rows=[fields.join(","),...state.report.trades.map(t=>fields.map(k=>`"${String(t[k]??"").replaceAll('"','""')}"`).join(","))];
  download(new Blob([rows.join("\r\n")],{type:"text/csv"}),"backtestr-trades.csv");
});
$("#copy-code").addEventListener("click",async()=>{try{await navigator.clipboard.writeText($("#source-preview").textContent);toast("Generated source copied.");}catch{toast("Clipboard unavailable. Download the C++ project to get the source.");}});
$("#export-button").addEventListener("click",async()=>{
  if(!state.report||!$("#acknowledge").checked)return;
  busy($("#export-button"),true,"Packaging C++ project…","Download C++ project ↓");
  try{const revision=state.revision;const blob=await api("/api/export",{strategy:state.strategy,dataset_id:state.dataset});if(revision===state.revision){download(blob,"backtestr-cpp.zip");toast("C++ project downloaded. Start with its README.");}}
  catch(error){toast(error.message);}finally{busy($("#export-button"),false,"","Download C++ project ↓");updateRunButton();}
});
$("#upload-csv").addEventListener("click",async()=>{
  const file=$("#csv-file").files[0];if(!file){feedback($("#data-feedback"),"Choose a CSV file first.",true);return;}
  if(file.size>15*1024*1024){feedback($("#data-feedback"),"The CSV file must be smaller than 15 MB.",true);return;}
  busy($("#upload-csv"),true,"Validating daily bars…","Import CSV");
  try{const data=await api("/api/datasets",{csv:await file.text(),adjustment:$("#adjustment").value});addDataset(data);feedback($("#data-feedback"),`Loaded ${number(data.rows,0)} bars for ${data.symbols.join(", ")}. This is now the selected dataset. ${data.warnings.join(" ")}`);toast("CSV loaded. Return to the strategy lab to run it.");}
  catch(error){feedback($("#data-feedback"),error.message,true);}finally{busy($("#upload-csv"),false,"","Import CSV");}
});
$("#fetch-alpaca").addEventListener("click",async()=>{
  if(!state.strategy){feedback($("#data-feedback"),"Build and review a strategy before fetching its history.",true);return;}
  busy($("#fetch-alpaca"),true,"Fetching daily history…","Fetch strategy history");
  try{const data=await api("/api/data/alpaca",{strategy:state.strategy});addDataset(data);feedback($("#data-feedback"),`Loaded ${number(data.rows,0)} Alpaca bars. ${data.warnings.join(" ")}`);}
  catch(error){feedback($("#data-feedback"),error.message,true);}finally{busy($("#fetch-alpaca"),false,"","Fetch strategy history");updateRunButton();}
});
let resizeTimer;
window.addEventListener("resize",()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(state.report)renderChart();},180);});

async function initialize() {
  try {
    state.config=await api("/api/config");state.datasets.set("demo",state.config.demo);
    $("#ai-status").textContent=state.config.ai_configured?"OpenAI configured":"Local parser · no AI key";
    $("#ai-dot").classList.toggle("muted",!state.config.ai_configured);
    $("#alpaca-status").textContent=state.config.alpaca_configured?"Alpaca configured":"Alpaca · not connected";
    $("#alpaca-dot").classList.toggle("muted",!state.config.alpaca_configured);
    $("#data-alpaca-badge").textContent=state.config.alpaca_configured?"Configured":"Not configured";
    $("#provider-label").textContent=state.config.ai_configured?"AI available":"Local parser";
    updateDataset();updateRunButton();
    try{$("#description").value=localStorage.getItem("backtestr-draft")||"";}catch{}
    updateDescription();
  } catch(error){feedback($("#interpret-feedback"),error.message,true);$("#ai-status").textContent="Server unavailable";}
}
initialize();
