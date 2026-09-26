"""可视化控制台的页面（单文件、零外部依赖、可直接离线打开）。

之所以把页面放在 Python 常量里而不是包内的静态文件：本库支持「把 ``romanbo/``
目录直接拷进你的项目」这种用法（见文档「安装与环境」），页面内联后不依赖打包
配置（``package-data``），也绝不会出现「文件没进 wheel」这种失败模式。

页面通过 ``__TOKEN__`` 占位符注入本次会话的访问令牌（见 `romanbo.webui`）。
"""

from __future__ import annotations

#: 页面 HTML（不含 token；由 `romanbo.webui` 注入）
PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="token" content="__TOKEN__">
<title>ROMANBO 舵机控制台</title>
<style>
:root{
  --bg:#0d1117; --panel:#161b22; --panel2:#1b2230; --line:#2b3446;
  --fg:#e6edf3; --dim:#8b98a9; --acc:#4493f8; --ok:#3fb950; --warn:#d29922; --err:#f85149;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.55 system-ui,-apple-system,"Segoe UI","Noto Sans SC",sans-serif}
button{font:inherit;color:var(--fg);background:var(--panel2);border:1px solid var(--line);
  border-radius:7px;padding:6px 12px;cursor:pointer;transition:.12s}
button:hover:not(:disabled){border-color:var(--acc);background:#20293a}
button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:var(--acc);border-color:var(--acc);color:#06121f;font-weight:600}
button.danger{background:#3d1a1d;border-color:#7d2a2f;color:#ffb4b0}
button.ghost{background:transparent}
input,select{font:inherit;color:var(--fg);background:#0b0f16;border:1px solid var(--line);
  border-radius:7px;padding:5px 8px;width:100%}
input:focus,select:focus{outline:none;border-color:var(--acc)}
input[type=range]{padding:0;border:0;background:transparent;accent-color:var(--acc)}
input[type=checkbox]{width:auto}
label{display:block;color:var(--dim);font-size:12px;margin-bottom:3px}
.row{display:flex;gap:8px;align-items:center}
.grid{display:grid;gap:10px}
header{display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:10px 16px;
  background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
header h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.2px}
header .sep{width:1px;height:22px;background:var(--line)}
.dot{width:9px;height:9px;border-radius:50%;background:#555;flex:0 0 auto}
.dot.on{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.dot.mock{background:var(--warn);box-shadow:0 0 8px var(--warn)}
main{display:grid;grid-template-columns:215px 1fr;gap:14px;padding:14px;align-items:start}
@media(max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card>h2{font-size:13px;margin:0 0 10px;color:var(--dim);font-weight:600;
  text-transform:uppercase;letter-spacing:.6px}
#list{display:flex;flex-direction:column;gap:6px}
.item{display:flex;justify-content:space-between;align-items:center;padding:7px 10px;border-radius:8px;
  border:1px solid var(--line);background:var(--panel2);cursor:pointer}
.item:hover{border-color:var(--acc)}
.item.sel{border-color:var(--acc);background:#152238}
.item b{font-family:var(--mono)}
.readouts{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px}
.ro{background:#0b0f16;border:1px solid var(--line);border-radius:8px;padding:7px 9px}
.ro span{display:block;color:var(--dim);font-size:11px}
.ro b{font-family:var(--mono);font-size:17px;font-weight:600}
fieldset{border:1px solid var(--line);border-radius:9px;margin:0;padding:10px 12px 12px}
legend{color:var(--dim);font-size:12px;padding:0 6px}
#logs{font-family:var(--mono);font-size:12px;height:236px;overflow:auto;background:#080b11;
  border:1px solid var(--line);border-radius:8px;padding:8px 10px;white-space:pre-wrap}
.tx{color:#7ee787}.rx{color:#79c0ff}.ts{color:#5b6675}.note{color:var(--warn)}
#events{max-height:132px;overflow:auto;font-size:12.5px;color:var(--dim)}
#events div{padding:2px 0;border-bottom:1px solid #1c2230}
.pill{font-family:var(--mono);font-size:11px;color:var(--dim);background:#0b0f16;
  border:1px solid var(--line);border-radius:99px;padding:2px 9px}
.led{display:flex;align-items:center;justify-content:center;gap:5px;
  padding:6px 2px;font-size:12.5px}
.led i{width:11px;height:11px;border-radius:50%;border:1px solid #0006;background:#333;flex:0 0 auto}
.led.sel{border-color:var(--acc);box-shadow:inset 0 0 0 1px var(--acc)}
.hint{color:var(--dim);font-size:12px;margin-top:6px}
.warn{color:var(--warn)}
</style>
</head>
<body>
<header>
  <h1>ROMANBO 舵机控制台</h1>
  <span class="dot" id="dot"></span>
  <span class="pill" id="connText">未连接</span>
  <span class="sep"></span>
  <div class="row" style="min-width:260px">
    <select id="portSel"><option value="">（选择串口）</option></select>
    <button class="ghost" id="btnPorts" title="重新枚举串口">刷新</button>
  </div>
  <label class="row" style="margin:0;gap:6px">
    <input type="checkbox" id="chkMock"> <span>模拟模式</span>
  </label>
  <button class="primary" id="btnConn">连接</button>
  <span class="sep"></span>
  <button id="btnScan" disabled>扫描舵机</button>
  <span class="pill" id="onlineText">在线：—</span>
  <span style="flex:1"></span>
  <button class="danger" id="btnStop">紧急停止</button>
</header>

<main>
  <div class="grid">
    <div class="card">
      <h2>在线舵机</h2>
      <div id="list"><div class="hint">连接后点击「扫描舵机」</div></div>
    </div>
    <div class="card">
      <h2>事件</h2>
      <div id="events"><div>—</div></div>
    </div>
  </div>

  <div class="grid" style="gap:14px">
    <div class="card">
      <h2>实时读数 <span class="pill" id="selTitle">未选择舵机</span></h2>
      <div class="readouts">
        <div class="ro"><span>位置 ADC</span><b id="vPos">—</b></div>
        <div class="ro"><span>角度</span><b id="vAng">—</b></div>
        <div class="ro"><span>实测负荷</span><b id="vLoad">—</b></div>
        <div class="ro"><span>温度</span><b id="vTemp">—</b></div>
        <div class="ro"><span>PID</span><b id="vPid" style="font-size:15px">—</b></div>
        <div class="ro"><span>位置限位</span><b id="vLim" style="font-size:15px">—</b></div>
      </div>
      <div class="hint">位置/负荷/温度每 0.7 s 自动刷新；负荷为「实测负荷」（0x18），静止应为 0。</div>
    </div>

    <div class="card">
      <h2>运动控制</h2>
      <div class="grid" style="grid-template-columns:1fr 200px;gap:16px">
        <div>
          <label>目标位置 <span class="pill" id="vTgt">512</span>
            <span class="pill" id="vTgtAng">0.0°</span></label>
          <input type="range" id="rng" min="0" max="1023" value="512">
          <div class="row" style="margin-top:8px">
            <div style="flex:1"><label>目标角度（度）</label>
              <input type="number" id="inAng" step="0.5" value="0"></div>
            <div style="flex:1"><label>速度（度/秒）</label>
              <input type="number" id="inSpeed" value="60" min="1" max="300"></div>
          </div>
          <div class="row" style="margin-top:10px;flex-wrap:wrap">
            <button class="primary" id="btnGo">平滑运动</button>
            <button id="btnJump">立即到位</button>
            <button id="btnCenter">回中位</button>
            <button class="btnRel" data-d="-10">−10°</button>
            <button class="btnRel" data-d="10">+10°</button>
          </div>
          <div class="hint warn">平滑运动走「步进逼近」（固件忽略周期命令）；「立即到位」以最大速度冲到位。</div>
        </div>
        <div class="grid" style="gap:8px;align-content:start">
          <div><label>软件限力阈值（0 = 关闭，默认关闭）</label>
            <input type="number" id="inLoad" value="0" min="0" max="255"></div>
          <div><label>负荷检查间隔（步）</label>
            <input type="number" id="inCheck" value="3" min="1" max="20"></div>
          <div class="hint">真机实测：起步第 1 步的冲击达 128~147（加速涌流，不是卡死），
            运动中只有约 85 → 要么阈值取 ≥150，要么把检查间隔调大（建议 3）跳过起步阶段。</div>
          <div><label>出力档位</label>
            <select id="inLevel">
              <option value="0">H 高</option>
              <option value="1" selected>M 中</option>
              <option value="2">L 低</option>
              <option value="3">W（位置指令不生效）</option>
            </select></div>
          <div class="row"><button id="btnTorqueOn">力矩使能</button>
            <button id="btnTorqueOff">断开力矩</button></div>
          <div class="row"><button class="ghost" id="btnReadCfg">重读全部参数</button></div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>多关节一键下发</h2>
      <div id="multi" class="grid" style="gap:6px"><div class="hint">连接并扫描后出现</div></div>
      <div class="row" style="margin-top:10px">
        <button class="primary" id="btnMultiGo">全部平滑到位</button>
        <button id="btnMultiRead">读取当前位置填表</button>
        <span class="hint">速度/限力沿用上方设置</span>
      </div>
    </div>

    <div class="card">
      <h2>参数写入（掉电保存，写入后回读确认）</h2>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(232px,1fr))">
        <fieldset><legend>PID</legend>
          <div class="row">
            <input type="number" id="inP" placeholder="P"><input type="number" id="inI" placeholder="I">
            <input type="number" id="inD" placeholder="D">
          </div>
          <div class="row" style="margin-top:8px">
            <button id="btnPidWrite">写入</button>
            <button class="ghost" id="btnPidRead">读取</button>
          </div>
        </fieldset>
        <fieldset><legend>位置限位</legend>
          <div class="row">
            <input type="number" id="inLmin" placeholder="min" min="0" max="1023">
            <input type="number" id="inLmax" placeholder="max" min="0" max="1023">
          </div>
          <div class="row" style="margin-top:8px">
            <button id="btnLimWrite">写入</button>
            <button class="ghost" id="btnLimFull">恢复全量程</button>
          </div>
        </fieldset>
        <fieldset><legend>死区 Margin</legend>
          <div class="row">
            <input type="number" id="inMargin" placeholder="0..255">
            <button id="btnMarginWrite">写入</button>
          </div>
          <div class="hint">进入死区即视为「到位」，出厂值 5。</div>
        </fieldset>
        <fieldset><legend>LED</legend>
          <div class="grid" id="ledGrid" style="grid-template-columns:repeat(4,1fr);gap:6px">
            <button class="led" data-r="0" data-g="0" data-b="0"><i style="background:#3a3f4b"></i>灭</button>
            <button class="led" data-r="1" data-g="0" data-b="0"><i style="background:#f85149"></i>红</button>
            <button class="led" data-r="0" data-g="1" data-b="0"><i style="background:#3fb950"></i>绿</button>
            <button class="led" data-r="0" data-g="0" data-b="1"><i style="background:#4493f8"></i>蓝</button>
            <button class="led" data-r="1" data-g="1" data-b="0"><i style="background:#e3b341"></i>黄</button>
            <button class="led" data-r="1" data-g="0" data-b="1"><i style="background:#bc8cff"></i>紫</button>
            <button class="led" data-r="0" data-g="1" data-b="1"><i style="background:#39c5cf"></i>青</button>
            <button class="led" data-r="1" data-g="1" data-b="1"><i style="background:#e6edf3"></i>白</button>
          </div>
          <div class="hint">协议只定义这 8 种组合（无亮度/闪烁）。<b>LED 状态无法回读</b>，
            高亮的是最近一次下发值，不是实测状态。</div>
        </fieldset>
        <fieldset><legend>危险操作</legend>
          <div class="row"><button class="danger" id="btnCalib">当前位置设为零点</button></div>
          <div class="hint warn">改写零点（0x23），需二次确认；做完请重读参数。</div>
        </fieldset>
      </div>
    </div>

    <div class="card">
      <h2>报文日志（原始收发）</h2>
      <div class="row" style="margin-bottom:8px">
        <button class="ghost" id="btnPause">暂停</button>
        <button class="ghost" id="btnClear">清空</button>
        <span class="hint" id="frameCnt">共 0 条</span>
      </div>
      <div id="logs"></div>
      <div class="hint">TX 为下发帧、RX 为回包原始字节；帧内第 5 字节是命令码。</div>
    </div>
  </div>
</main>

<script>
"use strict";
const TOKEN = document.querySelector("meta[name=token]").content;
const $ = s => document.querySelector(s);
const CMD = {1:"GetModel/Reset",2:"GetVersion/Reboot",5:"Status",6:"SetID",7:"SetPID",8:"SetTemp",
  9:"SetPosition/Wheel",10:"SetOffSet",11:"SetPeriod",12:"SetMargin",13:"SetLoadLimit",
  14:"SetAccelerate",15:"SetPositionLimit",16:"SetTorque",17:"SetLED",18:"GetPID",19:"GetTemp",
  20:"GetPosition",21:"GetCalibration",22:"GetMotor",23:"GetMargin",24:"GetLoad",25:"GetAccelerate",
  26:"GetPositionLimit",32:"SetSync",33:"SetNextPosition",34:"SetBaudrate",35:"SetCalibCurrpos",
  71:"SetPID(nosave)",121:"FactoryTestStart",122:"FactoryTestSet",123:"FactoryTestGet"};
const CENTER = 512, RATIO = 0.2932551;

let state = {connected:false, mock:false, port:null, online:[]};
let sel = null, cfg = null, live = {}, lastPos = {}, moveBusy = false;
let frameSeq = 0, paused = false, autoscroll = true, lastTouch = 0;

const adcToAngle = a => (Number(a) - CENTER) * RATIO;
function angleToAdc(d){
  const v = Number(d);
  if (!isFinite(v)) return CENTER;
  return Math.max(0, Math.min(1023, Math.round(CENTER + v / RATIO)));
}
function fmt(v, n){
  if (v === null || v === undefined) return "—";
  return (typeof v === "number") ? v.toFixed(n === undefined ? 1 : n) : String(v);
}

// 不带 body 时发 GET、带 body 时发 POST。**新增接口要同步
// tests/test_webui.py::TestPageApiContract.CALLS**（否则那个契约测试会漏掉它）。
async function api(path, body){
  const opt = { headers: { "X-Robot-Token": TOKEN } };
  if (body !== undefined){
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  let data = {};
  try { data = await res.json(); } catch(e){ data = { error: "响应不是 JSON" }; }
  if (!res.ok || data.error) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}
function stamp(){ return new Date().toLocaleTimeString("zh-CN", {hour12:false}); }
function ev(html){
  const box = $("#events");
  if (box.children.length === 1 && box.children[0].textContent === "—") box.innerHTML = "";
  const d = document.createElement("div");
  d.innerHTML = '<span class="ts">' + stamp() + '</span>  ' + html;
  box.prepend(d);
  while (box.children.length > 80) box.lastChild.remove();
}
const ok = m => ev(m);
const bad = m => ev('<span class="note">' + m + '</span>');

async function loadPorts(){
  try{
    const rows = await api("/api/ports");
    const selEl = $("#portSel");
    selEl.innerHTML = '<option value="">（选择串口）</option>' + rows.map(r =>
      '<option value="' + r.device + '">' + r.device +
      (r.busy ? " · 被占用" : " · 可用") + (r.description ? " · " + r.description : "") +
      '</option>').join("");
    if (state.port && rows.some(r => r.device === state.port)) selEl.value = state.port;
    ok("串口枚举：" + rows.length + " 个设备");
  }catch(e){ bad("枚举串口失败：" + e.message); }
}

async function connect(){
  const mock = $("#chkMock").checked, port = $("#portSel").value;
  if (!mock && !port){ bad("请选择串口，或勾选模拟模式"); return; }
  try{
    await api("/api/connect", { port: port || null, mock: mock, baudrate: 115200 });
    ok("已连接：" + (mock ? "模拟器" : port));
    lastPos = {};
  }catch(e){ bad("连接失败：" + e.message); }
  await refreshState();
}
async function disconnect(){
  try{ await api("/api/disconnect", {}); ok("已断开"); }
  catch(e){ bad("断开失败：" + e.message); }
  sel = null; cfg = null; live = {}; state.online = []; lastPos = {};
  await refreshState(); renderAll();
}
async function scan(){
  const btn = $("#btnScan");
  btn.disabled = true; btn.textContent = "扫描中…";
  try{
    const r = await api("/api/scan", { start: 1, end: 32 });
    state.online = r.found;
    ok("扫描完成，在线 " + r.found.length + " 个：" + (r.found.join(", ") || "无") +
       "（" + r.elapsed_s + " s）");
    if (r.found.length && (sel === null || !r.found.includes(sel))) sel = r.found[0];
  }catch(e){ bad("扫描失败：" + e.message); }
  btn.textContent = "扫描舵机";
  btn.disabled = !state.connected;
  if (sel !== null) await refreshFull();
  renderAll();
}

async function refreshState(){
  try { state = await api("/api/state"); } catch(e){ /* 轮询失败不刷屏 */ }
  const dot = $("#dot");
  dot.className = "dot" + (state.connected ? (state.mock ? " mock" : " on") : "");
  $("#connText").textContent = state.connected ? (state.mock ? "模拟模式" : state.port) : "未连接";
  $("#btnConn").textContent = state.connected ? "断开" : "连接";
  $("#btnScan").disabled = !state.connected;
  const ids = state.online || [];
  $("#onlineText").textContent = "在线：" + (ids.length ? ids.join(", ") : "—");
  renderList(); renderMulti();
}

function renderList(){
  const box = $("#list"), ids = state.online || [];
  if (!ids.length){
    box.innerHTML = '<div class="hint">' +
      (state.connected ? "还没扫描到舵机" : "连接后点击「扫描舵机」") + '</div>';
    return;
  }
  box.innerHTML = ids.map(i => '<div class="item' + (i === sel ? " sel" : "") +
    '" data-id="' + i + '"><b>ID ' + i + '</b><span class="ts">' +
    (lastPos[i] !== undefined ? "ADC " + lastPos[i] : "") + '</span></div>').join("");
  box.querySelectorAll(".item").forEach(el =>
    el.onclick = async () => { sel = Number(el.dataset.id); await refreshFull(); renderAll(); });
}

function renderMulti(){
  const box = $("#multi"), ids = state.online || [];
  if (!ids.length){ box.innerHTML = '<div class="hint">连接并扫描后出现</div>'; return; }
  if (box.querySelectorAll("input[type=range]").length !== ids.length){
    box.innerHTML = ids.map(i =>
      '<div class="row"><span class="ts" style="width:52px">ID ' + i + '</span>' +
      '<input type="range" id="m' + i + '" min="0" max="1023" value="' +
      (lastPos[i] !== undefined ? lastPos[i] : CENTER) + '">' +
      '<input type="number" id="mn' + i + '" style="width:78px" value="' +
      (lastPos[i] !== undefined ? lastPos[i] : CENTER) + '">' +
      '<span class="ts" id="ma' + i + '" style="width:54px;text-align:right">0.0°</span></div>').join("");
    ids.forEach(i => {
      const r = document.getElementById("m" + i), n = document.getElementById("mn" + i);
      const sync = v => { r.value = v; n.value = v;
        document.getElementById("ma" + i).textContent = adcToAngle(v).toFixed(1) + "°"; };
      r.oninput = () => sync(r.value);
      n.oninput = () => sync(n.value);
      sync(r.value);
    });
  }
}

async function refreshFull(){
  if (sel === null || !state.connected) return;
  try{
    cfg = await api("/api/servo/" + sel);
    live = cfg;
    renderReadouts(); renderParamInputs();
  }catch(e){ bad("读取参数失败：" + e.message); }
}
async function refreshLive(){
  if (sel === null || !state.connected || moveBusy) return;
  try{
    const r = await api("/api/servo/" + sel + "/live");
    live = Object.assign(live || {}, r);
    lastPos[sel] = r.position;
    renderReadouts(); renderList();
  }catch(e){ /* 静默 */ }
}

function renderReadouts(){
  const v = live || {};
  $("#vPos").textContent = fmt(v.position, 0);
  $("#vAng").textContent = v.position === null || v.position === undefined
    ? "—" : adcToAngle(v.position).toFixed(2) + "°";
  $("#vLoad").textContent = fmt(v.load, 0);
  $("#vTemp").textContent = v.temperature === null || v.temperature === undefined
    ? "—" : v.temperature + "°C";
  if (cfg){
    $("#vPid").textContent = cfg.pid ? cfg.pid.join(" / ") : "—";
    $("#vLim").textContent = cfg.position_limit
      ? cfg.position_limit[0] + "–" + cfg.position_limit[1] : "—";
  }
}
function renderParamInputs(){
  if (!cfg) return;
  if (cfg.pid){ $("#inP").value = cfg.pid[0]; $("#inI").value = cfg.pid[1]; $("#inD").value = cfg.pid[2]; }
  if (cfg.position_limit){
    $("#inLmin").value = cfg.position_limit[0];
    $("#inLmax").value = cfg.position_limit[1];
  }
  if (cfg.margin !== null && cfg.margin !== undefined) $("#inMargin").value = cfg.margin;
}
const CTRL_IDS = ["btnGo", "btnJump", "btnCenter", "btnTorqueOn", "btnTorqueOff", "btnReadCfg",
  "btnPidWrite", "btnPidRead", "btnLimWrite", "btnLimFull", "btnMarginWrite", "btnCalib"];
function renderPanel(){
  $("#selTitle").textContent = sel === null ? "未选择舵机" : ("ID " + sel);
  const has = sel !== null && state.connected;
  CTRL_IDS.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = !has;
  });
  document.querySelectorAll(".btnRel").forEach(el => { el.disabled = !has; });
  document.querySelectorAll("#ledGrid .led").forEach(el => { el.disabled = !has; });
  if (live && live.position !== null && live.position !== undefined &&
      !moveBusy && Date.now() - lastTouch > 2500){
    setTarget(live.position);
  }
}
function renderAll(){ renderList(); renderMulti(); renderReadouts(); renderPanel(); }

function setTarget(adc){
  const v = Number(adc);
  if (!isFinite(v)) return;
  const a = Math.max(0, Math.min(1023, Math.round(v)));
  $("#rng").value = a;
  $("#vTgt").textContent = a;
  $("#vTgtAng").textContent = adcToAngle(a).toFixed(1) + "°";
  $("#inAng").value = adcToAngle(a).toFixed(1);
}
const loadVal = () => { const v = Number($("#inLoad").value); return v > 0 ? v : null; };
const levelVal = () => Number($("#inLevel").value);
const checkEvery = () => Math.max(1, Number($("#inCheck").value) || 1);

async function moveTo(adc, smooth){
  if (sel === null) return;
  const speed = Number($("#inSpeed").value) || 60;
  moveBusy = true;
  setTarget(adc);
  try{
    const r = await api("/api/servo/" + sel + "/goto",
      { adc: adc, speed_dps: smooth ? speed : null, max_load: loadVal(),
        level: levelVal(), load_check_every: checkEvery() });
    lastPos[sel] = adc;
    ok("ID " + sel + " → ADC " + adc + "（" + adcToAngle(adc).toFixed(1) + "°）" +
       (r.sent ? "，步进 " + r.sent + " 拍" : "") +
       (r.peak_load !== null && r.peak_load !== undefined ? "，峰值负荷 " + r.peak_load : ""));
  }catch(e){ bad("运动失败：" + e.message); }
  moveBusy = false;
  await refreshFull(); renderAll();
}
async function act(fn, label){
  try{ await fn(); ok(label + " 完成"); }catch(e){ bad(label + " 失败：" + e.message); }
  await refreshFull(); renderAll();
}

function markLed(el){
  document.querySelectorAll("#ledGrid .led").forEach(b => b.classList.toggle("sel", b === el));
}
async function setLed(btn){
  if (sel === null) return;
  try{
    // 协议只认 8 种组合，所以直接传三色；服务端返回实际下发的 d[5] 便于对报文
    const r = await api("/api/servo/" + sel + "/led",
      { red: btn.dataset.r === "1", green: btn.dataset.g === "1", blue: btn.dataset.b === "1" });
    markLed(btn);
    ok("LED 已下发：d[5] = 0x" + r.led.byte.toString(16).toUpperCase().padStart(2, "0"));
  }catch(e){ bad("LED 写入失败：" + e.message); }
}

function bind(){
  $("#btnPorts").onclick = loadPorts;
  $("#btnConn").onclick = () => state.connected ? disconnect() : connect();
  $("#btnScan").onclick = scan;

  const touch = () => { lastTouch = Date.now(); };
  $("#rng").oninput = e => { touch(); setTarget(e.target.value); };
  $("#inAng").oninput = e => { touch(); setTarget(angleToAdc(e.target.value)); };
  $("#btnGo").onclick = () => moveTo(Number($("#rng").value), true);
  $("#btnJump").onclick = () => moveTo(Number($("#rng").value), false);
  $("#btnCenter").onclick = () => moveTo(CENTER, true);
  document.querySelectorAll(".btnRel").forEach(el => {
    el.onclick = () => moveTo(angleToAdc(adcToAngle($("#rng").value) + Number(el.dataset.d)), true);
  });
  $("#btnReadCfg").onclick = () => act(refreshFull, "重读全部参数");
  $("#btnTorqueOn").onclick = () => act(() => api("/api/servo/" + sel + "/torque", {on:true}), "力矩使能");
  $("#btnTorqueOff").onclick = () => act(() => api("/api/servo/" + sel + "/torque", {on:false}), "断开力矩");

  $("#btnPidWrite").onclick = () => act(() => api("/api/servo/" + sel + "/pid",
    {p:Number($("#inP").value), i:Number($("#inI").value), d:Number($("#inD").value)}), "写入 PID");
  $("#btnPidRead").onclick = () => act(refreshFull, "读取 PID");
  $("#btnLimWrite").onclick = () => act(() => api("/api/servo/" + sel + "/limit",
    {min:Number($("#inLmin").value), max:Number($("#inLmax").value)}), "写入位置限位");
  $("#btnLimFull").onclick = () => act(() => api("/api/servo/" + sel + "/limit", {min:1, max:1023}),
    "恢复全量程限位");
  $("#btnMarginWrite").onclick = () => act(() => api("/api/servo/" + sel + "/margin",
    {value:Number($("#inMargin").value)}), "写入 Margin");
  document.querySelectorAll("#ledGrid .led").forEach(btn => {
    btn.onclick = () => setLed(btn);
  });
  $("#btnCalib").onclick = () => {
    if (!confirm("把 ID " + sel + " 的当前位置设为零点？这会改写舵机内部标定。")) return;
    act(() => api("/api/servo/" + sel + "/calib", {}), "零点校准");
  };

  $("#btnMultiGo").onclick = async () => {
    const targets = {};
    (state.online || []).forEach(i => {
      const el = document.getElementById("m" + i);
      if (el) targets[i] = Number(el.value);
    });
    if (!Object.keys(targets).length){ bad("没有在线舵机"); return; }
    try{
      const r = await api("/api/move", { targets: targets,
        speed_dps: Number($("#inSpeed").value) || 60, max_load: loadVal(),
        level: levelVal(), load_check_every: checkEvery() });
      Object.keys(targets).forEach(i => { lastPos[i] = targets[i]; });
      ok("多关节下发完成：" + Object.keys(targets).map(i => i + "→" + targets[i]).join("，") +
         (r.peak_load !== null && r.peak_load !== undefined ? "，峰值负荷 " + r.peak_load : ""));
    }catch(e){ bad("多关节下发失败：" + e.message); }
    await refreshFull(); renderAll();
  };
  $("#btnMultiRead").onclick = async () => {
    try{
      const r = await api("/api/capture");
      Object.keys(r.positions).forEach(i => {
        lastPos[i] = r.positions[i];
        const el = document.getElementById("m" + i), n = document.getElementById("mn" + i);
        if (el){ el.value = r.positions[i]; n.value = r.positions[i];
          document.getElementById("ma" + i).textContent = adcToAngle(r.positions[i]).toFixed(1) + "°"; }
      });
      ok("已读回位置：" + JSON.stringify(r.positions));
    }catch(e){ bad("读回失败：" + e.message); }
    renderList();
  };

  $("#btnStop").onclick = async () => {
    if (!confirm("紧急停止：对所有在线舵机断开力矩（0x10=0）。关节会失去保持力，下垂风险自负。确定？")) return;
    try{
      const r = await api("/api/stop_all", {});
      ok("已断开力矩：" + (r.ids.join(", ") || "（无在线舵机）"));
    }catch(e){ bad("紧急停止失败：" + e.message); }
  };

  $("#btnPause").onclick = () => { paused = !paused; $("#btnPause").textContent = paused ? "继续" : "暂停"; };
  $("#btnClear").onclick = () => { $("#logs").innerHTML = ""; };
  $("#logs").onscroll = () => {
    const el = $("#logs");
    autoscroll = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
  };
}

function cmdName(frame){
  const parts = frame.split(" ");
  if (parts.length < 5) return "";
  const c = parseInt(parts[4], 16);
  return CMD[c] || ("0x" + parts[4]);
}
async function pollFrames(){
  if (paused) return;
  try{
    const r = await api("/api/frames?since=" + frameSeq);
    if (!r.frames.length) return;
    const box = $("#logs"), frag = document.createDocumentFragment();
    r.frames.forEach(f => {
      const d = document.createElement("div");
      d.className = f.dir;
      d.innerHTML = '<span class="ts">' + f.at + '</span> ' +
        (f.dir === "tx" ? "TX →" : "RX ←") + " " + f.hex +
        '  <span class="ts">' + cmdName(f.hex) + '</span>';
      frag.appendChild(d);
    });
    box.appendChild(frag);
    frameSeq = r.latest;
    $("#frameCnt").textContent = "共 " + r.latest + " 条";
    while (box.children.length > 400) box.removeChild(box.firstChild);
    if (autoscroll) box.scrollTop = box.scrollHeight;
  }catch(e){ /* 静默 */ }
}

async function tick(){
  await refreshState();
  await refreshLive();
  renderPanel();
  await pollFrames();
}

bind();
renderPanel();
loadPorts().then(refreshState).then(tick);
setInterval(tick, 700);
</script>
</body>
</html>
"""
