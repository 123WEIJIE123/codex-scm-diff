"use strict";
const $ = id => document.getElementById(id);
const token = location.hash.slice(1);
let scope = "workspace", items = [], selected = null, requestId = 0, listId = 0;
const labels = {modified:"M",added:"A",deleted:"D",missing:"!",unversioned:"?",renamed:"R",conflicted:"C",properties:"P","changed-during-turn":"Δ",replaced:"R"};
function node(tag, cls, value) { const el = document.createElement(tag); if(cls)el.className=cls;if(value!==undefined)el.textContent=value;return el; }
async function api(path, extra={}) {
  const query = new URLSearchParams({scm:$("scm").value,scope,...extra});
  const response = await fetch(path+"?"+query,{headers:{Authorization:"Bearer "+token},cache:"no-store"});
  const data = await response.json(); if(!response.ok)throw new Error(data.error||"读取失败");return data;
}
function showFiles() {
  const filter = $("filter").value.toLocaleLowerCase();
  const visible = items.filter(x=>x.path.toLocaleLowerCase().includes(filter)&&(!$("meta").checked||!x.path.endsWith(".meta")));
  $("count").textContent = `${visible.length} / ${items.length} 个改动项`;
  $("files").replaceChildren(...visible.map(item=>{
    const button=node("button","file"+(selected===item.path?" selected":""));
    button.title=item.path+" · "+item.status;
    button.append(node("span","tag "+item.status,labels[item.status]||"·"),node("span","path",item.path));
    button.onclick=()=>selectFile(item.path);return button;
  }));
  if(!visible.length)$("files").append(node("div","empty","没有符合条件的文件"));
}
async function selectFile(path) {
  const current=++requestId; selected=path;showFiles();
  $("filetitle").textContent=path;$("notice").textContent="";
  $("diff").replaceChildren(node("div","empty","正在读取差异…"));
  try {
    const data=await api("/api/diff",{path});if(current!==requestId)return;
    $("filetitle").textContent=`${path}    +${data.added}  −${data.removed}`;
    $("notice").textContent=[data.note,data.truncated?"内容较多，仅展示前 6000 行差异。":""].filter(Boolean).join("\n");
    const fragment=document.createDocumentFragment();
    for(const line of data.rows) {
      const row=node("div","row "+line.kind);
      if(line.kind==="hunk")row.textContent=line.text;
      else row.append(node("span","ln",line.old??""),node("span","ln",line.new??""),node("span","sign",line.kind==="add"?"+":line.kind==="del"?"−":""),node("span","code",line.text));
      fragment.append(row);
    }
    $("diff").replaceChildren(fragment);
    if(!data.rows.length)$("diff").append(node("div","empty",data.unavailable?"此项无法展示文本差异，详见上方说明。":"没有可显示的文本行差异。"));
  } catch(error) {if(current===requestId)$("diff").replaceChildren(node("div","empty",error.message));}
}
async function refresh() {
  const current=++listId;++requestId;$("status").textContent="正在读取…";$("refresh").disabled=true;
  try {
    const data=await api("/api/status");if(current!==listId)return;
    items=data.files;$("badge").textContent=data.scm.toUpperCase();$("root").textContent=data.root;
    $("turn").disabled=!data.snapshotId;$("turn").title=data.snapshotId?"相对修改前快照":"本次打开没有修改前快照，可先查看全部改动";
    $("baseline").textContent=scope==="turn"?"对比本轮修改前快照":data.baseline;
    if(!items.some(item=>item.path===selected))selected=items[0]?.path??null;
    showFiles();$("notice").textContent="";
    if(selected)await selectFile(selected);
    else {$("filetitle").textContent="没有改动";$("diff").replaceChildren(node("div","empty",scope==="turn"?"本轮没有检测到文件内容变化。":"工作副本没有未提交改动。"));}
    $("status").textContent="更新于 "+new Date().toLocaleTimeString()+" · 本地读取，不修改工作区";
  }catch(error){if(current!==listId)return;items=[];selected=null;showFiles();$("filetitle").textContent="读取失败";$("diff").replaceChildren(node("div","empty",error.message));$("status").textContent="读取失败，可切换版本控制或刷新";}
  finally{if(current===listId)$("refresh").disabled=false;}
}
$("filter").oninput=showFiles;$("meta").onchange=showFiles;$("refresh").onclick=refresh;$("scm").onchange=()=>{scope="workspace";updateScope();refresh();};
function updateScope(){$("all").classList.toggle("active",scope==="workspace");$("turn").classList.toggle("active",scope==="turn");}
$("all").onclick=()=>{scope="workspace";updateScope();refresh();};$("turn").onclick=()=>{scope="turn";updateScope();refresh();};
if(!token){$("diff").replaceChildren(node("div","empty","请通过 Local SCM Diff 插件打开此页面。"));$("status").textContent="缺少访问凭证";}else refresh();
