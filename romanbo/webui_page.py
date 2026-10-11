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
<div class="hint" style="margin:0;padding:4px 14px">
  舵机 LED 状态指示：<b style="color:var(--ok)">绿灯</b> = 通信正常（扫描成功）、
  <b style="color:var(--acc)">蓝灯</b> = 力矩使能（断使能切回绿灯）、灭 = 断开连接。</div>

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
    <div class="card" id="armCard" style="display:none">
      <h2>机械臂 · TF 树 <span class="pill" id="armSelTitle">—</span>
        <span class="pill" id="armLivePill">离线</span></h2>
      <div class="grid" style="grid-template-columns:1fr 264px;gap:14px">
        <div>
          <div style="position:relative">
            <canvas id="armCv" style="width:100%;height:410px;background:#000;
              border:1px solid var(--line);border-radius:8px;cursor:grab;touch-action:none"></canvas>
            <canvas id="armOv" style="position:absolute;left:0;top:0;width:100%;height:100%;
              pointer-events:none"></canvas>
          </div>
          <div class="row" style="margin-top:8px;flex-wrap:wrap">
            <span id="armTabs" class="row" style="gap:6px"></span>
            <label class="row" style="margin:0;gap:6px">
              <input type="checkbox" id="armChkAxes" checked> <span>TF 轴</span></label>
            <label class="row" style="margin:0;gap:6px">
              <input type="checkbox" id="armChkJoints" checked> <span>关节轴</span></label>
            <span style="flex:1"></span>
            <button class="ghost" id="armViewReset">复位视角</button>
          </div>
          <div class="row" style="margin-top:6px;font-size:12px;gap:16px;flex-wrap:wrap;
              color:var(--dim);user-select:none">
            <span><b style="color:var(--acc)">左键拖动</b> 旋转视角</span>
            <span><b style="color:var(--acc)">右键拖动</b> 平移视角</span>
            <span><b style="color:var(--acc)">滚轮</b> 缩放视图</span>
            <span><b style="color:var(--acc)">点击关节</b> 竖向拖动控制（松手平滑下发该关节）</span>
            <span id="armPickInfo" style="color:var(--fg)"></span>
          </div>
          <div class="hint">实体来自 URDF 的 STL mesh（WebGL 渲染，红黄蓝为 3D 打印件本体）；
            红/绿/蓝细线 = 各连杆坐标系的 X/Y/Z 轴（TF）；绿圈 + 红箭头 = 关节旋转轴
            （点绿圈附近的关节可竖向拖动控制）。角度为 URDF 关节角（零位 = 标定姿态）。
            滑块与 3D 显示目标位姿，点「同步当前位姿」把真机实时角度读进滑块
            （编辑中不会被覆盖）；平滑到位 / 立即到位 / 回 home 完成后会**自动**同步
            一次实测位姿。</div>
        </div>
        <div class="grid" style="gap:8px;align-content:start">
          <div id="armSliders" class="grid" style="gap:8px"></div>
          <div class="row" style="flex-wrap:wrap">
            <button id="armSyncBtn" title="把真机当前关节角读进滑块和 3D 视图">同步当前位姿</button>
            <button class="primary" id="armGo">平滑到位</button>
            <button id="armJump">立即到位</button>
            <button id="armHomeBtn">回 home</button>
            <button id="armTorqueOn" title="切换该臂全部关节的力矩使能（0x10）；开启时 shoulder_lift / elbow_flex / wrist_flex 以 H 档保持（承载臂重）">一键使能</button>
          </div>
          <div class="row" style="flex-wrap:wrap">
            <span class="pill" id="followPill">主从：未运行</span>
            <select id="followMaster" title="主动臂：断开力矩，用手扳动示教（低频采样，无需实时）"></select>
            <select id="followFollower" title="从动臂：使能并限速平滑跟随主动臂关节角"></select>
            <label class="hint" style="margin:0">采样
              <input id="followSample" type="number" value="200" min="50" step="10" style="width:4.2em" title="主动臂采样周期（ms）：读 6 关节约占 96 ms 总线，5 Hz 足够，给控制拍让路">ms</label>
            <label class="hint" style="margin:0">控制
              <input id="followControl" type="number" value="20" min="10" step="5" style="width:3.6em" title="从动臂插值控制周期（ms）：越高越平滑，只在有位移时发帧">ms</label>
            <label class="hint" style="margin:0">限速
              <input id="followDps" type="number" value="60" min="5" max="180" style="width:3.6em" title="从动臂关节最大角速度（°/s）：匀速段上限，防过载">°/s</label>
            <label class="hint" style="margin:0">起步
              <input id="followRamp" type="number" value="150" min="20" step="10" style="width:4.2em" title="起步斜坡（ms）：0 → 全速的加速时间，越大越柔和">ms</label>
            <button id="followBtn">开始跟随</button>
          </div>
          <div class="hint">主从模式（平滑限速版）：主动臂**断力矩**（用手扳动示教），低频采样
            （默认 5 Hz，无需实时追踪）；从动臂由**高频控制线程**（默认 50 Hz）做梯形速度
            规划——斜坡起步 → 限速匀速 → 距离减速收尾，起步从**当前实际位置**平滑插值，
            消除跳变冲击（过载报警的来源）。承重关节恒 H 档出力；死区内吸收主动臂读数抖动；
            单个丢答沿用上一拍读数。跟随期间整臂运动/使能按钮停用、TF 视图轮询暂停
            （给总线让路）；整拍连续无应答会自动停止；「紧急停止」也会停跟随。</div>
          <div class="hint">滑块即 URDF 关节角（度），限位来自配置文件；
            速度 / 限力 / 档位沿用「运动控制」区的设置（承重的 shoulder_lift /
            elbow_flex / wrist_flex 恒用 H 档出力，不受档位选择影响）。</div>
        </div>
      </div>
    </div>

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
            运动中只有约 85 → 要么阈值取 ≥150，要么把检查间隔调大（建议 3）跳过起步阶段。
            冲击与步长成正比：以上为 100 ms 节拍的旧数据，现默认节拍 20 ms（50 Hz 插值）、
            步长约缩小 5 倍，冲击相应更低。</div>
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
const CENTER = 512, RATIO = 0.3519062;   // =360/1023，须与 joints.RATIO_MAIN 同步

let state = {connected:false, mock:false, port:null, online:[]};
let scanRoundsSeen = 0;            // 已提示过结论的兜底扫描轮数（用于只提示一次）
let followWasActive = false;       // 上一拍主从跟随是否在运行（用于停止时提示原因）
let tokenDead = false;             // 令牌已被判失效（服务重启过）→ 只解释一次
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
  if (res.status === 401){
    // 令牌是服务启动时随机生成的：服务重启后，旧页面里的令牌会**全部**失效
    // （表现为每个按钮都报「缺少或错误的访问令牌」）。只解释一次原因，
    // 后续操作统一给「用新链接打开」的指引，不再逐条刷屏。
    if (!tokenDead){
      tokenDead = true;
      bad("访问令牌已失效：服务重启过，本页面的令牌已作废——" +
          "请用终端里打印的**最新**链接重新打开页面（在此之前所有操作都会被拒绝）");
    }
    throw new Error("令牌失效：请用终端打印的新链接重新打开页面");
  }
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
    // 只列**已识别**的设备：Linux 上 pyserial 会列出 ttyS0..31 这类没接硬件的占位口
    // （这台机器 32 个），放进下拉框纯属噪音。隐藏了几个仍然如实报出来。
    const shown = rows.filter(r => r.description && r.description !== "n/a");
    const hidden = rows.length - shown.length;
    const selEl = $("#portSel");
    selEl.innerHTML = '<option value="">（选择串口）</option>' + shown.map(r => {
      // 打不开的原因看后端给的 reason（errno 分类）：一律写"被占用"会把权限不足
      // 说成占用，把人引向"找占用进程"。
      const st = r.busy ? (r.reason === "permission" ? "权限不足"
                         : r.reason === "busy" ? "已被占用" : "打不开") : "可用";
      return '<option value="' + r.device + '">' + r.device + " · " + st +
             " · " + r.description + '</option>';
    }).join("");
    if (state.port && shown.some(r => r.device === state.port)) selEl.value = state.port;
    ok("串口枚举：" + shown.length + " 个设备" +
       (hidden ? "（已隐藏 " + hidden + " 个没有接硬件的占位口）" : ""));
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
    state.scan_rounds = 1;
    ok("第 1 轮扫描完成，在线 " + r.found.length + " 个：" +
       (r.found.join(", ") || "无") + "（" + r.elapsed_s + " s）" +
       (r.pending_rounds ? "；后台兜底再扫 " + r.pending_rounds + " 轮" : ""));
    if (r.arms) for (const k in r.arms){
      if (r.arms[k].abnormal)
        bad(r.arms[k].label + " 异常：舵机离线 ID " +
            (r.arms[k].missing.join(", ") || "无"));
    }
    if (r.found.length && (sel === null || !r.found.includes(sel))) sel = r.found[0];
  }catch(e){ bad("扫描失败：" + e.message); }
  btn.textContent = "扫描舵机";
  btn.disabled = !state.connected;
  if (sel !== null) await refreshFull();
  renderAll();
}

function followUI(){
  const f = state.follow, active = !!(f && f.active);
  const btn = $("#followBtn"), pill = $("#followPill");
  if (btn){
    btn.textContent = active ? "停止跟随" : "开始跟随";
    btn.className = active ? "danger" : "";
  }
  if (pill){
    pill.textContent = active
      ? "主从：" + (armInfo && armInfo.arms[f.master] ? armInfo.arms[f.master].label : f.master) +
        " → " + (armInfo && armInfo.arms[f.follower] ? armInfo.arms[f.follower].label : f.follower) +
        "（采样 " + (f.sample_ms ? Math.round(1000 / f.sample_ms) : "?") + " Hz" +
        " · 控制 " + (f.control_ms ? Math.round(1000 / f.control_ms) : "?") + " Hz" +
        " · 限速 " + (f.max_dps || "?") + "°/s" +
        (f.moving ? " · " + f.moving + " 关节在动" : " · 已到位") +
        (f.stale ? "，" + f.stale + " 关节旧值" : "") + "）"
      : "主从：未运行" + (f && f.error ? "（上次错误：" + f.error + "）" : "");
    pill.style.color = active ? "var(--ok)"
      : (f && f.error ? "#e5484d" : "var(--dim)");
  }
  // 跟随期间停用整臂运动/使能（会与跟随指令互相打架）
  ["armGo", "armJump", "armHomeBtn", "armTorqueOn"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = active;
  });
  if (followWasActive && !active && f && f.error)
    bad("主从跟随已自动停止：" + f.error);
  followWasActive = active;
}
async function followToggle(){
  const active = !!(state.follow && state.follow.active);
  try{
    if (active){
      const r = await api("/api/follow/stop", {});
      ok("主从跟随已停止" + (r.cycles ? "（共 " + r.cycles + " 拍）" : "") +
         "；从动臂保持使能、主动臂仍断力矩");
    }else{
      const r = await api("/api/follow/start",
        { master: $("#followMaster").value, follower: $("#followFollower").value,
          sample_ms: +$("#followSample").value || 200,
          control_ms: +$("#followControl").value || 20,
          max_dps: +$("#followDps").value || 60,
          ramp_ms: +$("#followRamp").value || 150 });
      ok("主从跟随已启动：" +
         (armInfo.arms[r.master] ? armInfo.arms[r.master].label : r.master) + " → " +
         (armInfo.arms[r.follower] ? armInfo.arms[r.follower].label : r.follower) +
         "（采样 " + Math.round(1000 / r.sample_ms) + " Hz · 控制 " +
         Math.round(1000 / r.control_ms) + " Hz · 限速 " + r.max_dps +
         "°/s · 起步 " + r.ramp_ms + " ms）");
    }
  }catch(e){ bad("主从跟随失败：" + e.message); }
  await refreshState();
}
async function refreshState(){
  try { state = await api("/api/state"); } catch(e){ /* 轮询失败不刷屏 */ }
  // 兜底扫描完成 → 给一次最终结论（在线集合并集 + 机械臂健康），只在变化时提示
  if (state.scan_rounds >= 3 && scanRoundsSeen < 3){
    scanRoundsSeen = 3;
    const arms = state.arms || {};
    const bad1 = Object.keys(arms).filter(k => arms[k].abnormal);
    if (bad1.length)
      bad1.forEach(k => bad(arms[k].label + " 异常：舵机离线 ID " +
                            (arms[k].missing.join(", ") || "无") +
                            "（3 轮兜底扫描后仍离线）"));
    else
      ok("兜底扫描完成（3 轮并集）：在线 " + (state.online || []).length + " 个，机械臂无离线关节");
  }
  const dot = $("#dot");
  dot.className = "dot" + (state.connected ? (state.mock ? " mock" : " on") : "");
  $("#connText").textContent = state.connected ? (state.mock ? "模拟模式" : state.port) : "未连接";
  $("#btnConn").textContent = state.connected ? "断开" : "连接";
  $("#btnScan").disabled = !state.connected;
  const ids = state.online || [];
  $("#onlineText").textContent = "在线：" + (ids.length ? ids.join(", ") : "—");
  followUI();
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
      Object.keys(armTorqueState).forEach(k => { armTorqueState[k] = false; });
      armTorqueLabel();
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

// ---------- 机械臂 · TF 树（URDF 正运动学 + canvas 3D 投影） ----------
let armInfo = null;               // /api/arm/info：URDF 骨架 + 各臂关节配置
let armSel = null;                // 当前显示/控制的臂 key
const armLive = {};               // key -> {urdf_joint: 实测角度 or null}
const armTgt = {};                // key -> {短名: 滑块目标角}
let armTouch = 0;                 // 最近一次手动动滑块的时间（避免轮询覆盖）

const armView = {yaw: -1.15, pitch: 0.42, zoom: 780, pan: [0, 0]};
const armViewDefault = Object.assign({}, armView);
let armHitPts = [];               // 可动关节的屏幕坐标缓存（点击拾取用，每帧随绘制更新）
let armPicked = null;             // 拖动中的关节（URDF 关节名，叠加层画高亮圈）

function matEye(){ return [1,0,0, 0,1,0, 0,0,1]; }
function matMul(a, b){
  const o = new Array(9);
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++)
    o[r*3+c] = a[r*3]*b[c] + a[r*3+1]*b[3+c] + a[r*3+2]*b[6+c];
  return o;
}
function matVec(a, v){
  return [a[0]*v[0] + a[1]*v[1] + a[2]*v[2],
          a[3]*v[0] + a[4]*v[1] + a[5]*v[2],
          a[6]*v[0] + a[7]*v[1] + a[8]*v[2]];
}
function matRpy(r, p, y){
  const cr = Math.cos(r), sr = Math.sin(r), cp = Math.cos(p), sp = Math.sin(p),
        cy = Math.cos(y), sy = Math.sin(y);
  return [cy*cp, cy*sp*sr - sy*cr, cy*sp*cr + sy*sr,
          sy*cp, sy*sp*sr + cy*cr, sy*sp*cr - cy*sr,
          -sp,   cp*sr,             cp*cr];
}
function matAxis(ax, q){
  const n = Math.hypot(ax[0], ax[1], ax[2]);
  if (n < 1e-12) return matEye();
  const x = ax[0]/n, y = ax[1]/n, z = ax[2]/n;
  const c = Math.cos(q), s = Math.sin(q), t = 1 - c;
  return [t*x*x + c,   t*x*y - s*z,  t*x*z + s*y,
          t*x*y + s*z, t*y*y + c,    t*y*z - s*x,
          t*x*z - s*y, t*y*z - s*x,  t*z*z + c];
}

function armFk(angles){
  const out = {};
  out[armInfo.urdf.root] = [matEye(), [0, 0, 0]];
  let pending = armInfo.urdf.joints.slice();
  while (pending.length){
    let moved = false;
    for (const j of pending.slice()){
      if (!(j.parent in out)) continue;
      const w = out[j.parent];
      let r = matMul(w[0], matRpy(j.rpy[0], j.rpy[1], j.rpy[2]));
      if (j.movable){
        const q = (angles[j.name] || 0) * Math.PI / 180;
        r = matMul(r, matAxis(j.axis, q));
      }
      const off = matVec(w[0], j.xyz);
      out[j.child] = [r, [off[0] + w[1][0], off[1] + w[1][1], off[2] + w[1][2]]];
      pending = pending.filter(x => x !== j);
      moved = true;
    }
    if (!moved) break;
  }
  return out;
}

function armProj(p, w, h){
  const cy = Math.cos(armView.yaw), sy = Math.sin(armView.yaw);
  const x1 = p[0]*cy - p[1]*sy, y1 = p[0]*sy + p[1]*cy;
  const cp = Math.cos(armView.pitch), sp = Math.sin(armView.pitch);
  const up = p[2]*cp - y1*sp, depth = y1*cp + p[2]*sp;
  const s = armView.zoom * (4 / (4 + depth));        // 轻透视
  return [w/2 + x1*s + armView.pan[0], h/2 - up*s + armView.pan[1]];
}

function armDraw2d(){
  const c = document.getElementById("armCv");
  if (!c || !armInfo || !armSel) return;
  const dpr = window.devicePixelRatio || 1;
  const W = c.clientWidth || 600, H = c.clientHeight || 400;
  if (c.width !== Math.round(W*dpr) || c.height !== Math.round(H*dpr)){
    c.width = Math.round(W*dpr); c.height = Math.round(H*dpr);
  }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  // 关节角：实测优先，离线用滑块目标
  // 预览 = 滑块目标（手动同步模式：真机位姿点「同步当前位姿」才进来）
  const angles = {};
  armInfo.arms[armSel].joints.forEach(j => {
    const tgt = (armTgt[armSel] || {})[j.name];
    angles[j.urdf_joint] = tgt === undefined ? 0 : tgt;
  });
  const world = armFk(angles);

  // 地面网格（z = 0）
  ctx.strokeStyle = "#565e69"; ctx.lineWidth = 1;
  for (let g = -0.20; g <= 0.2001; g += 0.05){
    ctx.beginPath();
    ctx.moveTo(...armProj([g, -0.20, 0], W, H));
    ctx.lineTo(...armProj([g, 0.20, 0], W, H));
    ctx.moveTo(...armProj([-0.20, g, 0], W, H));
    ctx.lineTo(...armProj([0.20, g, 0], W, H));
    ctx.stroke();
  }
  // 连杆（父关节原点 → 子关节原点）
  ctx.lineCap = "round";
  armInfo.urdf.joints.forEach(j => {
    if (!(j.parent in world) || !(j.child in world)) return;
    ctx.strokeStyle = "#808a99"; ctx.lineWidth = 6;
    ctx.beginPath();
    ctx.moveTo(...armProj(world[j.parent][1], W, H));
    ctx.lineTo(...armProj(world[j.child][1], W, H));
    ctx.stroke();
  });
  // 每个 link 的坐标系（TF）：红/绿/蓝 = X/Y/Z
  const L = 0.028;
  const axes = [[[L,0,0], "#f85149"], [[0,L,0], "#3fb950"], [[0,0,L], "#4493f8"]];
  ctx.font = "10px " + "ui-monospace,monospace";
  for (const link in world){
    const r = world[link][0], t = world[link][1];
    axes.forEach(a => {
      const end = matVec(r, a[0]).map((v, i) => v + t[i]);
      ctx.strokeStyle = a[1]; ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(...armProj(t, W, H));
      ctx.lineTo(...armProj(end, W, H));
      ctx.stroke();
    });
    const p = armProj(t, W, H);
    ctx.fillStyle = "#a8b3c2";
    ctx.fillText(link, p[0] + 5, p[1] - 5);
  }
  // 拾取点缓存（可动关节），交互逻辑与 GL 路径共用
  armHitPts = [];
  armInfo.urdf.joints.forEach(j => {
    if (j.movable && (j.child in world)){
      const q = armProj(world[j.child][1], W, H);
      armHitPts.push({name: j.name, x: q[0], y: q[1]});
    }
  });
}

// ---------- 机械臂 · mesh 渲染（手写 WebGL，零外部依赖） ----------
// 与参考实现（three.js + STLLoader + urdf-loader）同一技术路线，但全部自己写：
// 二进制/ASCII STL 解析 → 每个链接一个顶点缓冲 → 按 FK 位姿画三角形，
// 深度测试天然处理遮挡，不需要画家算法。无 WebGL 的环境自动退回上面的 2D 骨架图。
let gl = null, progMesh = null, progLine = null, lineBuf = null;
const armGlMeshes = {};           // link -> [{posBuf, norBuf, count, rgba, xyz, rpy}]

function glCompile(type, src){
  const s = gl.createShader(type);
  gl.shaderSource(s, src); gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
  return s;
}
function glProgram(vs, fs){
  const p = gl.createProgram();
  gl.attachShader(p, glCompile(gl.VERTEX_SHADER, vs));
  gl.attachShader(p, glCompile(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  return p;
}

function parseStl(buf){
  // 二进制 STL：80 字节头 + 4 字节三角形数 + 每三角形 50 字节
  const dv = new DataView(buf);
  const n = buf.byteLength >= 84 ? dv.getUint32(80, true) : 0;
  if (84 + 50 * n === buf.byteLength){
    const pos = new Float32Array(n * 9), nor = new Float32Array(n * 9);
    for (let i = 0; i < n; i++){
      const o = 84 + i * 50;
      let nx = dv.getFloat32(o, true), ny = dv.getFloat32(o + 4, true),
          nz = dv.getFloat32(o + 8, true);
      const vs = [];
      for (let v = 0; v < 3; v++)
        for (let c = 0; c < 3; c++) vs.push(dv.getFloat32(o + 12 + v * 12 + c * 4, true));
      if (nx === 0 && ny === 0 && nz === 0){        // 有些导出器不写法线：叉积补
        const ax = vs[3] - vs[0], ay = vs[4] - vs[1], az = vs[5] - vs[2];
        const bx = vs[6] - vs[0], by = vs[7] - vs[1], bz = vs[8] - vs[2];
        nx = ay * bz - az * by; ny = az * bx - ax * bz; nz = ax * by - ay * bx;
      }
      for (let v = 0; v < 3; v++){
        pos.set(vs.slice(v * 3, v * 3 + 3), i * 9 + v * 3);
        nor[i * 9 + v * 3] = nx; nor[i * 9 + v * 3 + 1] = ny; nor[i * 9 + v * 3 + 2] = nz;
      }
    }
    return { pos, nor };
  }
  // ASCII STL
  const text = new TextDecoder().decode(buf);
  const pos = [], nor = [];
  const re = /facet normal\s+(\S+)\s+(\S+)\s+(\S+)[\s\S]*?endfacet/g;
  let m;
  while ((m = re.exec(text))){
    const xyz = [parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3])];
    for (const v of text.slice(m.index, re.lastIndex).matchAll(
        /vertex\s+(\S+)\s+(\S+)\s+(\S+)/g)){
      pos.push(parseFloat(v[1]), parseFloat(v[2]), parseFloat(v[3]));
      nor.push(xyz[0], xyz[1], xyz[2]);
    }
  }
  return { pos: new Float32Array(pos), nor: new Float32Array(nor) };
}

// mat4（列主序，与 WebGL 一致）；r 为上面 3x3 行主序数组
function m4mul(a, b){
  const o = new Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++)
    o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] +
                   a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
  return o;
}
function m4fromRT(r, t){
  return [r[0], r[3], r[6], 0, r[1], r[4], r[7], 0,
          r[2], r[5], r[8], 0, t[0], t[1], t[2], 1];
}
function m4persp(fovy, aspect, near, far){
  const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  return [f / aspect, 0, 0, 0, 0, f, 0, 0,
          0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}

function armInitGl(){
  const c = document.getElementById("armCv");
  gl = c.getContext("webgl", { antialias: true }) ||
       c.getContext("experimental-webgl");
  if (!gl) return false;
  try{
    progMesh = { p: glProgram(
      "attribute vec3 aPos; attribute vec3 aNor;" +
      "uniform mat4 uMVP; uniform mat4 uModel; varying vec3 vNor;" +
      "void main(){ gl_Position = uMVP * uModel * vec4(aPos, 1.0);" +
      " vNor = (uModel * vec4(aNor, 0.0)).xyz; }",
      "precision mediump float; varying vec3 vNor;" +
      "uniform vec3 uColor; uniform vec3 uLight;" +
      "void main(){ vec3 n = normalize(vNor);" +
      " float d = abs(dot(n, uLight));" +       // 双面光照：STL 法线朝向不保证一致
      " gl_FragColor = vec4(uColor * (0.35 + 0.65 * d), 1.0); }") };
    progMesh.a = { pos: gl.getAttribLocation(progMesh.p, "aPos"),
                   nor: gl.getAttribLocation(progMesh.p, "aNor") };
    progMesh.u = { mvp: gl.getUniformLocation(progMesh.p, "uMVP"),
                   model: gl.getUniformLocation(progMesh.p, "uModel"),
                   color: gl.getUniformLocation(progMesh.p, "uColor"),
                   light: gl.getUniformLocation(progMesh.p, "uLight") };
    progLine = { p: glProgram(
      "attribute vec3 aPos; attribute vec3 aCol; uniform mat4 uMVP;" +
      "varying vec3 vCol; void main(){ gl_Position = uMVP * vec4(aPos, 1.0);" +
      " vCol = aCol; }",
      "precision mediump float; varying vec3 vCol;" +
      "void main(){ gl_FragColor = vec4(vCol, 1.0); }") };
    progLine.a = { pos: gl.getAttribLocation(progLine.p, "aPos"),
                   col: gl.getAttribLocation(progLine.p, "aCol") };
    progLine.u = { mvp: gl.getUniformLocation(progLine.p, "uMVP") };
    lineBuf = gl.createBuffer();
    return true;
  }catch(e){ gl = null; return false; }
}

async function armLoadMeshes(){
  const files = {};
  Object.values(armInfo.urdf.links || {}).forEach(
    vf => vf.forEach(v => { files[v.file] = true; }));
  const cache = {};
  for (const f of Object.keys(files)){
    const url = "/api/mesh/" + f.split("/").map(encodeURIComponent).join("/");
    const res = await fetch(url, { headers: { "X-Robot-Token": TOKEN } });
    if (!res.ok) throw new Error(f + " HTTP " + res.status);
    cache[f] = parseStl(await res.arrayBuffer());
  }
  for (const link in armInfo.urdf.links){
    armGlMeshes[link] = armInfo.urdf.links[link].map(v => {
      const g = cache[v.file];
      const posBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
      gl.bufferData(gl.ARRAY_BUFFER, g.pos, gl.STATIC_DRAW);
      const norBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, norBuf);
      gl.bufferData(gl.ARRAY_BUFFER, g.nor, gl.STATIC_DRAW);
      return { posBuf, norBuf, count: g.pos.length / 3,
               rgba: v.rgba, xyz: v.xyz, rpy: v.rpy };
    });
  }
  armDraw();
}

// GL 相机：与旧 2D 投影同视角（yaw 绕 Z、pitch 俯仰；zoom 沿用「像素/米」语义）
function armCameraGl(W, H){
  const fov = 35 * Math.PI / 180;
  const proj = m4persp(fov, W / H, 0.02, 20);
  const cy = Math.cos(armView.yaw), sy = Math.sin(armView.yaw);
  const cp = Math.cos(armView.pitch), sp = Math.sin(armView.pitch);
  const dist = (H / 2) / (Math.tan(fov / 2) * armView.zoom);
  const rv = [cy, -sy, 0, -sy * sp, -cy * sp, cp, -sy * cp, -cy * cp, -sp];
  const view = [rv[0], rv[3], rv[6], 0, rv[1], rv[4], rv[7], 0,
                rv[2], rv[5], rv[8], 0,
                armView.pan[0] / armView.zoom, -armView.pan[1] / armView.zoom, -dist, 1];
  return m4mul(proj, view);
}

// vp 投影到画布 CSS 像素（w<=0 = 在相机背后 → null）
function armToScreen(vp, p, W, H){
  const x = vp[0] * p[0] + vp[4] * p[1] + vp[8] * p[2] + vp[12];
  const y = vp[1] * p[0] + vp[5] * p[1] + vp[9] * p[2] + vp[13];
  const w = vp[3] * p[0] + vp[7] * p[1] + vp[11] * p[2] + vp[15];
  return w <= 0 ? null : [W / 2 + (x / w) * W / 2, H / 2 - (y / w) * H / 2];
}

function armDrawGl(){
  const c = document.getElementById("armCv");
  const W = c.clientWidth || 600, H = c.clientHeight || 400;
  const dpr = window.devicePixelRatio || 1;
  if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)){
    c.width = Math.round(W * dpr); c.height = Math.round(H * dpr);
  }
  gl.viewport(0, 0, c.width, c.height);
  gl.clearColor(0, 0, 0, 1);
  gl.enable(gl.DEPTH_TEST);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

  // 预览 = 滑块目标（手动同步模式：真机位姿点「同步当前位姿」才进来）
  const angles = {};
  armInfo.arms[armSel].joints.forEach(j => {
    const tgt = (armTgt[armSel] || {})[j.name];
    angles[j.urdf_joint] = tgt === undefined ? 0 : tgt;
  });
  const world = armFk(angles);

  const vp = armCameraGl(W, H);

  // mesh：model = world(link) × visual origin
  gl.useProgram(progMesh.p);
  gl.uniformMatrix4fv(progMesh.u.mvp, false, new Float32Array(vp));
  gl.uniform3fv(progMesh.u.light, new Float32Array([0.45, 0.5, 0.74]));
  for (const link in armGlMeshes){
    const w = world[link];
    if (!w) continue;
    const wm = m4fromRT(w[0], w[1]);
    for (const m of armGlMeshes[link]){
      const vm = m4fromRT(matRpy(m.rpy[0], m.rpy[1], m.rpy[2]), m.xyz);
      gl.uniformMatrix4fv(progMesh.u.model, false, new Float32Array(m4mul(wm, vm)));
      gl.uniform3f(progMesh.u.color, m.rgba[0], m.rgba[1], m.rgba[2]);
      gl.bindBuffer(gl.ARRAY_BUFFER, m.posBuf);
      gl.vertexAttribPointer(progMesh.a.pos, 3, gl.FLOAT, false, 0, 0);
      gl.enableVertexAttribArray(progMesh.a.pos);
      gl.bindBuffer(gl.ARRAY_BUFFER, m.norBuf);
      gl.vertexAttribPointer(progMesh.a.nor, 3, gl.FLOAT, false, 0, 0);
      gl.enableVertexAttribArray(progMesh.a.nor);
      gl.drawArrays(gl.TRIANGLES, 0, m.count);
    }
  }

  // 线框：地面网格 + 连杆连线（受遮挡）+ TF 轴（叠加显示）
  const drawLines = (arr) => {
    if (!arr.length) return;
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(arr), gl.DYNAMIC_DRAW);
    gl.drawArrays(gl.LINES, 0, arr.length / 6);
  };
  const segs = [];
  const seg = (p, q, col) => segs.push(
    p[0], p[1], p[2], col[0], col[1], col[2],
    q[0], q[1], q[2], col[0], col[1], col[2]);
  const gridCol = [0.35, 0.38, 0.42];
  for (let g = -0.20; g <= 0.2001; g += 0.05){
    seg([g, -0.20, 0], [g, 0.20, 0], gridCol);
    seg([-0.20, g, 0], [0.20, g, 0], gridCol);
  }
  const linkCol = [0.5, 0.55, 0.62];
  armInfo.urdf.joints.forEach(j => {
    if (!(j.parent in world) || !(j.child in world)) return;
    seg(world[j.parent][1], world[j.child][1], linkCol);
  });
  const axesSegs = [];
  // 注意：seg2 必须定义在两个 if 块之外（const 是块级作用域）——
  // TF 轴和关节轴两段都要往 axesSegs 里追加线段
  const seg2 = (p, q, col) => axesSegs.push(
    p[0], p[1], p[2], col[0], col[1], col[2],
    q[0], q[1], q[2], col[0], col[1], col[2]);
  if (document.getElementById("armChkAxes").checked){
    const L = 0.035;
    const axes = [[[L, 0, 0], [0.973, 0.318, 0.286]],
                  [[0, L, 0], [0.247, 0.725, 0.314]],
                  [[0, 0, L], [0.267, 0.576, 0.973]]];
    for (const link in world){
      const r = world[link][0], t = world[link][1];
      axes.forEach(a => {
        const e = matVec(r, a[0]).map((v, i) => v + t[i]);
        seg2(t, e, a[1]);
      });
    }
  }
  // 关节轴（红箭头 = 转轴方向，绿圈 = 旋转平面）+ 可动关节屏幕坐标缓存（拾取用）。
  // 关节锚点 = 子连杆原点；转轴世界方向 = 子连杆旋转 × URDF axis（绕轴自转不变）。
  armHitPts = [];
  const cross3 = (a, b) => [a[1] * b[2] - a[2] * b[1],
                            a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  armInfo.urdf.joints.forEach(j => {
    if (!(j.child in world)) return;
    const t = world[j.child][1];
    const s = armToScreen(vp, t, W, H);
    if (s) armHitPts.push({name: j.name, x: s[0], y: s[1]});
    if (!j.movable || !document.getElementById("armChkJoints").checked) return;
    const d = matVec(world[j.child][0], j.axis);
    const n = Math.hypot(d[0], d[1], d[2]) || 1;
    const ax = [d[0] / n, d[1] / n, d[2] / n];
    const red = [1, 0.25, 0.25], green = [0.25, 0.95, 0.4];
    const tip = t.map((v, i) => v + ax[i] * 0.05);
    seg2(t.map((v, i) => v - ax[i] * 0.02), tip, red);        // 轴向红箭头
    const u0 = cross3(ax, Math.abs(ax[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0]);
    const un = Math.hypot(u0[0], u0[1], u0[2]) || 1;
    const u = u0.map(x => x / un), v = cross3(ax, u);
    [u, v, [-u[0], -u[1], -u[2]]].forEach(w =>                // 箭头翼
      seg2(tip, tip.map((p, i) => p - ax[i] * 0.014 + w[i] * 0.014), red));
    const R = 0.032, N = 28;                                  // 旋转平面绿圈
    let prev = null;
    for (let k = 0; k <= N; k++){
      const a = k / N * Math.PI * 2;
      const p = t.map((vv, i) => vv + R * (u[i] * Math.cos(a) + v[i] * Math.sin(a)));
      if (prev) seg2(prev, p, green);
      prev = p;
    }
    const a0 = 0.8, ca = Math.cos(a0), sa = Math.sin(a0);     // 圈上切向小箭头
    const rad = u.map((uu, i) => uu * ca + v[i] * sa);
    const tan = u.map((uu, i) => -uu * sa + v[i] * ca);
    const pc = t.map((vv, i) => vv + R * rad[i]);
    seg2(pc, pc.map((p, i) => p + tan[i] * 0.014 + rad[i] * 0.009), green);
    seg2(pc, pc.map((p, i) => p + tan[i] * 0.014 - rad[i] * 0.009), green);
  });
  gl.useProgram(progLine.p);
  gl.uniformMatrix4fv(progLine.u.mvp, false, new Float32Array(vp));
  gl.bindBuffer(gl.ARRAY_BUFFER, lineBuf);
  gl.vertexAttribPointer(progLine.a.pos, 3, gl.FLOAT, false, 24, 0);
  gl.enableVertexAttribArray(progLine.a.pos);
  gl.vertexAttribPointer(progLine.a.col, 3, gl.FLOAT, false, 24, 12);
  gl.enableVertexAttribArray(progLine.a.col);
  drawLines(segs);                    // 网格 + 连杆线：正常深度测试
  gl.disable(gl.DEPTH_TEST);
  drawLines(axesSegs);                // TF 轴：叠在 mesh 之上，坐标系才看得见
  gl.enable(gl.DEPTH_TEST);

  armDrawLabels(vp, W, H, world);
}

// 2D 叠加层：link 名称标注（WebGL 画不了文本）
function armDrawLabels(vp, W, H, world){
  const ov = document.getElementById("armOv");
  const dpr = window.devicePixelRatio || 1;
  if (ov.width !== Math.round(W * dpr) || ov.height !== Math.round(H * dpr)){
    ov.width = Math.round(W * dpr); ov.height = Math.round(H * dpr);
  }
  const ctx = ov.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.font = "10px ui-monospace,monospace";
  ctx.fillStyle = "#a8b3c2";
  for (const link in world){
    const s = armToScreen(vp, world[link][1], W, H);
    if (!s) continue;
    ctx.fillText(link, s[0] + 5, s[1] - 5);
  }
  if (armPicked){                       // 拖动中的关节：琥珀色高亮圈
    const p = armHitPts.find(h => h.name === armPicked);
    if (p){
      ctx.beginPath();
      ctx.strokeStyle = "#e3b341"; ctx.lineWidth = 2;
      ctx.arc(p.x, p.y, 11, 0, Math.PI * 2);
      ctx.stroke();
    }
  }
}

function armDraw(){
  if (!armInfo || !armSel) return;
  if (gl) armDrawGl();
  else armDraw2d();
}

function armSelect(key){
  armSel = key;
  armTgt[key] = armTgt[key] || {};
  document.getElementById("armSelTitle").textContent =
    armInfo.arms[key].label + "（" + key + "）";
  armTorqueLabel();
  document.querySelectorAll("#armTabs button").forEach(b => {
    b.classList.toggle("primary", b.dataset.k === key);
    b.classList.toggle("ghost", b.dataset.k !== key);
  });
  const box = document.getElementById("armSliders");
  box.innerHTML = armInfo.arms[key].joints.map(j => {
    const lo = j.limits_deg[0], hi = j.limits_deg[1];
    return '<div class="row"><span class="ts" style="width:86px;flex:0 0 auto" title="' +
      j.name + " → ID " + j.servo_id + '">' + j.name + '</span>' +
      '<input type="range" id="aj_' + j.name + '" min="' + lo + '" max="' + hi +
      '" step="0.5" value="0">' +
      '<span class="ts" id="av_' + j.name + '" style="width:54px;text-align:right">0.0°</span></div>';
  }).join("");
  armInfo.arms[key].joints.forEach(j => {
    const el = document.getElementById("aj_" + j.name);
    el.oninput = () => {
      armTouch = Date.now();
      armTgt[key][j.name] = Number(el.value);
      document.getElementById("av_" + j.name).textContent =
        Number(el.value).toFixed(1) + "°";
      armDraw();
    };
  });
  armDraw();
}

function armSyncSliders(force){
  if (!armSel) return;
  const recent = Date.now() - armTouch < 2000;
  armInfo.arms[armSel].joints.forEach(j => {
    const el = document.getElementById("aj_" + j.name);
    if (!el) return;
    if (!force && document.activeElement === el && recent) return;
    const a = (armLive[armSel] || {})[j.urdf_joint];
    if (a === null || a === undefined) return;        // 离线：保留滑块目标
    el.value = a;
    armTgt[armSel][j.name] = a;
    document.getElementById("av_" + j.name).textContent = Number(a).toFixed(1) + "°";
  });
}

function armKw(smooth){
  return { speed_dps: smooth ? (Number($("#inSpeed").value) || 60) : null,
           max_load: loadVal(), level: levelVal(), load_check_every: checkEvery() };
}
function armTargets(){
  const t = {};
  armInfo.arms[armSel].joints.forEach(j => {
    const el = document.getElementById("aj_" + j.name);
    if (el) t[j.name] = Number(el.value);
  });
  return t;
}

function armNearTargets(){
  const live = armLive[armSel] || {};
  return armInfo.arms[armSel].joints.every(j => {
    const a = live[j.urdf_joint];
    if (a === null || a === undefined) return true;   // 离线读不到，交给轮询上限
    const t = (armTgt[armSel] || {})[j.name];
    return t === undefined || Math.abs(a - t) < 1;
  });
}
async function armSyncPose(wait_settle){
  // 运动后自动「同步当前位姿」：把实测角读进滑块与 3D 视图，不用再手点。
  // 平滑/回 home：HTTP 返回时步进已走完 → 立即同步；
  // 立即到位：固件以最大速度冲刺、下发即返回 → 轮询到接近目标（≤3 s）再同步，
  // 免得把运动中途的过渡角写进滑块。
  if (wait_settle){
    for (let i = 0; i < 15 && armSel; i++){
      await new Promise(r => setTimeout(r, 200));
      await armTick();
      if (armNearTargets()) break;
    }
  }else{
    await armTick();
  }
  armSyncSliders(true);
  armDraw();
}
async function armMove(smooth){
  if (!armSel) return;
  const t = armTargets();
  try{
    await api("/api/arm/" + armSel + "/goto", Object.assign({ targets: t }, armKw(smooth)));
    ok(armInfo.arms[armSel].label + " 下发完成：" +
       Object.keys(t).map(k => k + "→" + t[k].toFixed(1) + "°").join("，"));
    await armSyncPose(!smooth);
  }catch(e){ bad("机械臂运动失败：" + e.message); }
}
async function armGoHome(){
  if (!armSel) return;
  try{
    await api("/api/arm/" + armSel + "/home", armKw(true));
    ok(armInfo.arms[armSel].label + " 回 home 已下发");
    await armSyncPose(false);
  }catch(e){ bad("回 home 失败：" + e.message); }
}
const armTorqueState = {};       // key -> 力矩状态（true=使能中），按钮文案按它切换
function armTorqueLabel(){
  const btn = document.getElementById("armTorqueOn");
  if (btn && armSel) btn.textContent = armTorqueState[armSel] ? "一键断开力矩" : "一键使能";
}
async function armTorqueAll(on){
  if (!armSel) return;
  const joints = armInfo.arms[armSel].joints;
  try{
    const r = await api("/api/arm/" + armSel + "/torque", { on: !!on });
    armTorqueState[armSel] = !!on;
    armTorqueLabel();
    ok(armInfo.arms[armSel].label + (on ? " 已使能 " : " 已断开 ") + r.enabled.length +
       " 个电机（ID " + joints.map(j => j.servo_id).join(", ") + "）" +
       (on && r.high.length ? "；H 档保持：" + r.high.join("、") : ""));
  }catch(e){
    bad(armInfo.arms[armSel].label + (on ? " 使能" : " 断开力矩") +
        "未完成 → " + e.message);
  }
}

function armBindView(){
  const c = document.getElementById("armCv");
  let drag = null;                  // {mode:"orbit"|"pan"|"joint", ...}
  // 拾取：命中 armHitPts（最近一帧投影出的可动关节）里 22 px 内的最近者
  const pick = e => {
    const r = c.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    let best = null, bd = 22;
    for (const p of armHitPts){
      const d = Math.hypot(p.x - x, p.y - y);
      if (d < bd){ bd = d; best = p; }
    }
    return best;
  };
  c.addEventListener("contextmenu", e => e.preventDefault());   // 右键留给平移
  c.addEventListener("pointerdown", e => {
    e.preventDefault();
    c.setPointerCapture(e.pointerId);
    if (e.button === 2){                              // 右键：平移视角
      drag = {mode: "pan", x: e.clientX, y: e.clientY};
    }else if (e.button === 0){
      const follow = state.follow && state.follow.active;
      const hit = follow ? null : pick(e);            // 跟随期间不让点关节（总线让路）
      if (hit){
        const j = armInfo.arms[armSel].joints.find(x => x.urdf_joint === hit.name);
        if (j){                                       // 点中关节：竖向拖动控制
          drag = {mode: "joint", name: j.name,
                  y0: e.clientY, a0: (armTgt[armSel] || {})[j.name] || 0,
                  lo: j.limits_deg[0], hi: j.limits_deg[1]};
          armPicked = hit.name;
        }
      }
      if (!drag) drag = {mode: "orbit", x: e.clientX, y: e.clientY};
    }else return;
    c.style.cursor = drag.mode === "pan" ? "move"
      : drag.mode === "joint" ? "ns-resize" : "grabbing";
    if (drag.mode === "joint") armDraw();
  });
  c.addEventListener("pointermove", e => {
    if (!drag){                                       // 悬停：关节上换成上下箭头
      c.style.cursor = pick(e) ? "ns-resize" : "grab";
      return;
    }
    if (drag.mode === "orbit"){
      armView.yaw += (e.clientX - drag.x) * 0.01;
      armView.pitch = Math.max(-1.4, Math.min(1.4,
        armView.pitch + (e.clientY - drag.y) * 0.008));
      drag.x = e.clientX; drag.y = e.clientY;
    }else if (drag.mode === "pan"){
      armView.pan[0] += e.clientX - drag.x;
      armView.pan[1] += e.clientY - drag.y;
      drag.x = e.clientX; drag.y = e.clientY;
    }else{                                            // 关节：竖向拖动 → 目标角
      const a = Math.max(drag.lo, Math.min(drag.hi,
        drag.a0 - (e.clientY - drag.y0) * 0.5));      // 上拖 = 增大
      armTouch = Date.now();                          // 视同手动改滑块
      armTgt[armSel][drag.name] = a;
      const el = document.getElementById("aj_" + drag.name);
      if (el) el.value = a;
      const av = document.getElementById("av_" + drag.name);
      if (av) av.textContent = a.toFixed(1) + "°";
      const info = document.getElementById("armPickInfo");
      if (info) info.textContent = drag.name + " → " + a.toFixed(1) + "°（松手下发）";
    }
    armDraw();
  });
  c.addEventListener("pointerup", () => {
    if (drag && drag.mode === "joint"){
      // 松手才下发（拖动中只更新预览）：平滑档按面板速度走，单关节不影响其它关节
      const a = armTgt[armSel][drag.name];
      const t = {}; t[drag.name] = a;
      const name = drag.name;
      api("/api/arm/" + armSel + "/goto",
          Object.assign({targets: t}, armKw(true)))
        .then(() => ok(name + " → " + a.toFixed(1) + "° 已下发"))
        .catch(err => bad("关节运动失败：" + err.message));
    }
    drag = null; armPicked = null;
    const info = document.getElementById("armPickInfo");
    if (info) info.textContent = "";
    c.style.cursor = "grab";
    armDraw();
  });
  c.addEventListener("wheel", e => {
    e.preventDefault();
    armView.zoom = Math.max(150, Math.min(3000, armView.zoom * (e.deltaY < 0 ? 1.12 : 0.89)));
    armDraw();
  }, {passive: false});
  document.getElementById("armViewReset").onclick = () => {
    Object.assign(armView, armViewDefault, {pan: [0, 0]});
    armDraw();
  };
  window.addEventListener("resize", armDraw);
}

async function armTick(){
  if (!armInfo) return;
  if (state.follow && state.follow.active){
    // 跟随时**让出总线**：/api/arm/state 每次要读两条臂 12 个关节（每个
    // 10~30 ms 往返），是跟随节拍外最大的总线抢夺来源。3D 视图暂时停在
    // 最后姿态，停止跟随后自动恢复轮询。
    const pill = document.getElementById("armLivePill");
    if (pill){ pill.textContent = "跟随中"; pill.style.color = "var(--ok)"; }
  }else{
    try{
      const r = await api("/api/arm/state");
      for (const k in r){
        armLive[k] = {};
        r[k].joints.forEach(j => { armLive[k][j.urdf_joint] = j.angle; });
        if (k === armSel){
          // 任一关节读不到 = 异常（红色）；全部读不到才算离线
          const js = r[k].joints, off = js.filter(j => j.adc === null).length;
          const pill = document.getElementById("armLivePill");
          pill.textContent = off === 0 ? "在线"
            : (off < js.length ? "异常（ID " +
               js.filter(j => j.adc === null).map(j => j.servo_id).join(",") + "）" : "离线");
          pill.style.color = off === 0 ? "var(--ok)"
            : (off < js.length ? "#e5484d" : "var(--dim)");
        }
      }
      // 手动同步：只有从没碰过滑块时才自动跟随一次（进页面拉到当前位姿），
      // 之后必须点「同步当前位姿」，避免编辑中途被实时读数覆盖
      if (!Object.keys(armTgt[armSel] || {}).length) armSyncSliders();
    }catch(e){ /* 轮询失败不刷屏 */ }
  }
  armDraw();
}

async function armInit(){
  try{ armInfo = await api("/api/arm/info"); }catch(e){ return; }
  if (!armInfo.urdf || !Object.keys(armInfo.arms).length) return;
  document.getElementById("armCard").style.display = "";
  const tabs = document.getElementById("armTabs");
  tabs.innerHTML = Object.keys(armInfo.arms).map(k =>
    '<button class="ghost" data-k="' + k + '">' + armInfo.arms[k].label + "</button>").join("");
  tabs.querySelectorAll("button").forEach(b => b.onclick = () => armSelect(b.dataset.k));
  document.getElementById("armGo").onclick = () => armMove(true);
  document.getElementById("armJump").onclick = () => armMove(false);
  document.getElementById("armHomeBtn").onclick = armGoHome;
  document.getElementById("armTorqueOn").onclick = () => armTorqueAll(!armTorqueState[armSel]);
  document.getElementById("armSyncBtn").onclick = () => {
    armSyncSliders(true);
    armDraw();
    const live = armLive[armSel] || {};
    const offline = armInfo.arms[armSel].joints.some(j => {
      const a = live[j.urdf_joint];
      return a === null || a === undefined;
    });
    if (offline) bad(armInfo.arms[armSel].label + " 离线，滑块保留原目标");
    else ok(armInfo.arms[armSel].label + " 已把当前位姿同步到滑块与 3D 视图");
  };
  document.getElementById("armChkAxes").onchange = armDraw;
  document.getElementById("armChkJoints").onchange = armDraw;
  // 主从模式：默认主动臂 = leader、从动臂 = follower（配置文件没这条就退回第一/第二条）
  const keys = Object.keys(armInfo.arms);
  const mSel = document.getElementById("followMaster");
  const fSel = document.getElementById("followFollower");
  mSel.innerHTML = keys.map(k =>
    '<option value="' + k + '">' + armInfo.arms[k].label + "</option>").join("");
  fSel.innerHTML = mSel.innerHTML;
  mSel.value = keys.includes("leader") ? "leader" : keys[0];
  fSel.value = keys.includes("follower") ? "follower" : keys[keys.length - 1];
  document.getElementById("followBtn").onclick = followToggle;
  armBindView();
  // WebGL 上下文必须在任何 2D 绘制之前获取（同一 canvas 两者互斥），
  // 所以先建 GL、再 armSelect 触发首帧
  const hasGl = armInfo.urdf.links && armInitGl();
  armSelect(Object.keys(armInfo.arms)[0]);
  if (hasGl){
    armLoadMeshes().catch(
      e => bad("mesh 加载失败（暂只显示骨架）：" + e.message));
  }
}

bind();
renderPanel();
loadPorts().then(refreshState).then(tick).then(armInit);
setInterval(tick, 700);
setInterval(armTick, 700);

</script>
</body>
</html>
"""
