/**
 * Guard Power Flow Card v0.2
 * HA Lovelace custom card — animated energy flow for Guard FVE platform.
 * Port z PortalHA/PowerFlow2.tsx (SVG + particle flows).
 * Autor: Roman Černý / Guard platform.
 *
 * Nody: FVE (slunce) / Baterie / Síť / Dům / Spirály / AKU / SR208C.
 */

//CC- Helper: format watts
const fmtW = (w) => {
  const n = Number(w) || 0;
  return Math.abs(n) >= 1000 ? (n / 1000).toFixed(1) + ' kW' : Math.round(n) + ' W';
};

//CC- Helper: get numeric state
const num = (hass, eid, def = 0) => {
  if (!eid || !hass?.states?.[eid]) return def;
  const v = parseFloat(hass.states[eid].state);
  return isNaN(v) ? def : v;
};

//CC- Helper: get raw string state
const sstate = (hass, eid) => hass?.states?.[eid]?.state;

//CC- Particle density by power — v0.2: smaller sizes to prevent giant-dot artifacts
function particleLevel(w) {
  const aw = Math.abs(w);
  if (aw < 300) return { dur: 2.4, count: 4, size: 1.5 };
  if (aw < 800) return { dur: 1.8, count: 6, size: 1.8 };
  if (aw < 2000) return { dur: 1.3, count: 9, size: 2.1 };
  if (aw < 4000) return { dur: 0.85, count: 13, size: 2.5 };
  return { dur: 0.5, count: 18, size: 2.8 };
}

//CC- Build particles along an SVG path
function makeParticles(pathId, color, glow, w) {
  const fl = particleLevel(w);
  let s = '';
  // subtle glow orbs (smaller multiplier than v0.1)
  const gc = Math.ceil(fl.count / 3);
  for (let j = 0; j < gc; j++) {
    const gd = ((j / gc) * fl.dur).toFixed(3);
    s += `<circle r="${(fl.size * 1.8).toFixed(2)}" fill="${glow}" opacity="0.22" filter="url(#pBlur)"><animateMotion dur="${fl.dur}s" begin="-${gd}s" repeatCount="indefinite" rotate="auto"><mpath href="#${pathId}"/></animateMotion></circle>`;
  }
  // main particles
  for (let i = 0; i < fl.count; i++) {
    const delay = ((i / fl.count) * fl.dur).toFixed(3);
    const sz = (fl.size * (0.7 + Math.random() * 0.6)).toFixed(2);
    const op = (0.65 + Math.random() * 0.35).toFixed(2);
    s += `<circle r="${sz}" fill="${color}" opacity="${op}"><animateMotion dur="${fl.dur}s" begin="-${delay}s" repeatCount="indefinite" rotate="auto"><mpath href="#${pathId}"/></animateMotion></circle>`;
  }
  // white highlight core
  const hc = Math.ceil(fl.count * 0.35);
  for (let h = 0; h < hc; h++) {
    const hd = ((h / hc) * fl.dur).toFixed(3);
    s += `<circle r="${(fl.size * 0.45).toFixed(2)}" fill="rgba(255,255,255,.92)" opacity="0.9"><animateMotion dur="${fl.dur}s" begin="-${hd}s" repeatCount="indefinite" rotate="auto"><mpath href="#${pathId}"/></animateMotion></circle>`;
  }
  return s;
}

//CC- Node card renderer (rectangle with icon + value + label)
function nodeCard(x, y, w, h, color, glow, bg, icon, val, dir, sub, active) {
  const barH = 56, topH = h - barH, cx = x + w / 2;
  const cid = `cl${Math.round(x * 17 + y * 31 + w * 7 + h * 3)}`;
  let s = '';
  s += `<defs><clipPath id="${cid}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="13"/></clipPath></defs>`;
  if (active) {
    s += `<rect x="${x - 2}" y="${y - 2}" width="${w + 4}" height="${h + 4}" rx="14" fill="none" stroke="${glow}" stroke-width="5" filter="url(#pBlur)" opacity="0.5"><animate attributeName="opacity" values="0.2;0.65;0.2" dur="2s" repeatCount="indefinite"/></rect>`;
  }
  s += `<g clip-path="url(#${cid})">`;
  s += `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${bg}"/>`;
  s += `<ellipse cx="${cx}" cy="${y + topH}" rx="${w * 0.42}" ry="${topH * 0.28}" fill="${glow}" opacity="0.15" filter="url(#pBlur)"/>`;
  s += `</g>`;
  s += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="13" fill="none" stroke="${color}" stroke-width="${active ? 1.8 : 1.2}" opacity="${active ? 1 : 0.35}"/>`;
  s += icon;
  s += `<rect x="${x}" y="${y + topH}" width="${w}" height="${barH}" fill="rgba(0,5,14,.92)" clip-path="url(#${cid})"/>`;
  s += `<line x1="${x}" y1="${y + topH}" x2="${x + w}" y2="${y + topH}" stroke="${color}" stroke-width="1" opacity="0.65"/>`;
  s += `<text x="${cx}" y="${y + topH + 23}" text-anchor="middle" fill="${color}" font-size="20" font-weight="900" font-family="Inter,-apple-system,sans-serif">${val}</text>`;
  s += `<text x="${cx}" y="${y + topH + 37}" text-anchor="middle" fill="${color}" font-size="10" font-weight="700" opacity="0.9">${dir}</text>`;
  s += `<text x="${cx}" y="${y + topH + 50}" text-anchor="middle" fill="rgba(200,230,255,.55)" font-size="9">${sub}</text>`;
  return s;
}

//CC- Battery icon
function batteryIcon(cx, iy, ah, soc, charging) {
  const bw = 58, bh = 26, bx = cx - bw / 2, by = iy + (ah - bh) / 2 - 6;
  const fw = Math.max(2, Math.round((bw - 8) * soc / 100));
  const fillC = soc > 50 ? 'rgba(60,220,110,.95)' : soc > 20 ? 'rgba(255,200,50,.95)' : 'rgba(255,70,70,.95)';
  let s = '<g>';
  s += `<rect x="${bx}" y="${by}" width="${bw}" height="${bh}" rx="6" fill="rgba(0,22,14,.96)" stroke="rgba(40,230,160,.92)" stroke-width="1.5"/>`;
  s += `<rect x="${bx + bw}" y="${by + bh / 2 - 5}" width="7" height="10" rx="3" fill="rgba(30,180,110,.4)" stroke="rgba(40,230,160,.8)" stroke-width="1.1"/>`;
  s += `<rect x="${bx + 4}" y="${by + 4}" width="${fw}" height="${bh - 8}" rx="3" fill="${fillC}"/>`;
  s += `<text x="${cx}" y="${by + bh / 2 + 4.5}" text-anchor="middle" fill="#fff" font-size="10" font-weight="800">${Math.round(soc)}%</text>`;
  if (charging) {
    const bcx = bx + fw * 0.6 + 4;
    s += `<polygon points="${bcx + 3},${by + 4} ${bcx},${by + bh / 2} ${bcx + 2.5},${by + bh / 2} ${bcx - 1},${by + bh - 4} ${bcx + 5},${by + bh / 2} ${bcx + 2.5},${by + bh / 2}" fill="rgba(255,255,220,.95)"/>`;
  }
  s += '</g>';
  return s;
}

//CC- Grid pylon icon
function gridIcon(cx, iy, ah) {
  const oy = iy + (ah - 50) / 2;
  let s = '<g>';
  s += `<line x1="${cx}" y1="${oy}" x2="${cx}" y2="${oy + 50}" stroke="rgba(0,215,255,.7)" stroke-width="2"/>`;
  s += `<line x1="${cx - 15}" y1="${oy + 10}" x2="${cx + 15}" y2="${oy + 10}" stroke="rgba(0,215,255,.6)" stroke-width="1.5"/>`;
  s += `<line x1="${cx - 12}" y1="${oy + 22}" x2="${cx + 12}" y2="${oy + 22}" stroke="rgba(0,215,255,.5)" stroke-width="1.2"/>`;
  s += `<line x1="${cx - 9}" y1="${oy + 34}" x2="${cx + 9}" y2="${oy + 34}" stroke="rgba(0,215,255,.4)" stroke-width="1"/>`;
  s += `<circle cx="${cx - 13}" cy="${oy + 10}" r="2" fill="rgba(0,215,255,.8)"/>`;
  s += `<circle cx="${cx + 13}" cy="${oy + 10}" r="2" fill="rgba(0,215,255,.8)"/>`;
  s += '</g>';
  return s;
}

//CC- Home icon
function homeIcon(cx, iy, ah) {
  const sc = 0.78, ox = cx - 32 * sc, oy = iy + (ah - 68 * sc) / 2;
  const p = (x, y) => `${ox + x * sc},${oy + y * sc}`;
  let s = '<g>';
  s += `<polygon points="${p(2, 22)} ${p(30, 6)} ${p(62, 22)} ${p(62, 26)} ${p(30, 10)} ${p(2, 26)}" fill="rgba(185,80,16,.88)" stroke="rgba(232,132,44,.88)" stroke-width="1"/>`;
  s += `<polygon points="${p(2, 24)} ${p(30, 24)} ${p(30, 68)} ${p(2, 68)}" fill="rgba(55,25,7,.96)" stroke="rgba(182,90,28,.55)" stroke-width="1"/>`;
  s += `<polygon points="${p(30, 24)} ${p(62, 24)} ${p(62, 68)} ${p(30, 68)}" fill="rgba(34,14,3,.97)" stroke="rgba(148,66,20,.48)" stroke-width="1"/>`;
  const wn = (wx, wy) => `<rect x="${ox + wx * sc}" y="${oy + wy * sc}" width="${8 * sc}" height="${7 * sc}" rx="1" fill="rgba(255,218,95,.22)" stroke="rgba(255,198,62,.72)" stroke-width="0.8"/>`;
  s += wn(4, 28) + wn(14, 28) + wn(4, 44) + wn(14, 44);
  s += `<rect x="${ox + 33 * sc}" y="${oy + 26 * sc}" width="${25 * sc}" height="${16 * sc}" rx="1" fill="rgba(255,218,95,.2)" stroke="rgba(255,202,66,.72)" stroke-width="0.8"/>`;
  s += `<rect x="${ox + 21 * sc}" y="${oy + 50 * sc}" width="${8 * sc}" height="${18 * sc}" rx="1" fill="rgba(8,4,1,.96)" stroke="rgba(255,175,42,.5)" stroke-width="0.8"/>`;
  s += '</g>';
  return s;
}

//CC- Heating spiral icon (3 coils ~ L1/L2/L3)
function spiralyIcon(cx, iy, ah, activeCount) {
  const oy = iy + (ah - 60) / 2 + 2;
  const color = activeCount > 0 ? 'rgba(255,120,40,.95)' : 'rgba(255,140,60,.45)';
  const glow = activeCount > 0 ? 'rgba(255,100,0,.5)' : 'rgba(255,120,40,.15)';
  let s = '<g>';
  for (let i = 0; i < 3; i++) {
    const sx = cx - 28 + i * 28;
    const bright = i < activeCount ? color : 'rgba(255,140,60,.35)';
    s += `<circle cx="${sx}" cy="${oy + 30}" r="13" fill="none" stroke="${bright}" stroke-width="2.2" opacity="0.95"/>`;
    s += `<path d="M${sx - 9},${oy + 22} Q${sx},${oy + 18} ${sx + 9},${oy + 22} Q${sx},${oy + 26} ${sx - 9},${oy + 30} Q${sx},${oy + 34} ${sx + 9},${oy + 30} Q${sx},${oy + 38} ${sx - 9},${oy + 38}" fill="none" stroke="${bright}" stroke-width="1.8" stroke-linecap="round"/>`;
    s += `<text x="${sx}" y="${oy + 56}" text-anchor="middle" fill="${bright}" font-size="8" font-weight="700">L${i + 1}</text>`;
  }
  if (activeCount > 0) {
    s += `<circle cx="${cx}" cy="${oy + 30}" r="45" fill="${glow}" opacity="0.25" filter="url(#pBlur)"><animate attributeName="opacity" values="0.12;0.35;0.12" dur="1.6s" repeatCount="indefinite"/></circle>`;
  }
  s += '</g>';
  return s;
}

//CC- AKU tank icon (vertical tank, dual temperature)
function akuIcon(cx, iy, ah, topT, botT) {
  const tw = 46, th = 66, tx = cx - tw / 2, ty = iy + (ah - th) / 2 - 2;
  const avg = (topT + botT) / 2;
  const hotColor = avg > 65 ? 'rgba(255,80,40,.95)' : avg > 45 ? 'rgba(255,150,50,.9)' : avg > 30 ? 'rgba(255,200,80,.85)' : 'rgba(120,200,255,.8)';
  let s = '<g>';
  // tank body
  s += `<rect x="${tx}" y="${ty}" width="${tw}" height="${th}" rx="7" fill="rgba(15,25,45,.8)" stroke="${hotColor}" stroke-width="1.5"/>`;
  // hot top layer
  const hotH = Math.max(6, Math.min(th - 6, (topT - 20) * 1.1));
  s += `<rect x="${tx + 2}" y="${ty + 2}" width="${tw - 4}" height="${hotH * 0.4}" rx="4" fill="${hotColor}" opacity="0.6"/>`;
  // cold bottom layer
  s += `<rect x="${tx + 2}" y="${ty + th - 14}" width="${tw - 4}" height="12" rx="3" fill="rgba(80,150,220,.5)"/>`;
  // labels
  s += `<text x="${tx - 4}" y="${ty + 14}" text-anchor="end" fill="rgba(255,200,100,.95)" font-size="9" font-weight="700">${Math.round(topT)}°</text>`;
  s += `<text x="${tx - 4}" y="${ty + th - 4}" text-anchor="end" fill="rgba(120,180,230,.95)" font-size="9" font-weight="700">${Math.round(botT)}°</text>`;
  // connector lines
  s += `<line x1="${tx - 20}" y1="${ty + 11}" x2="${tx}" y2="${ty + 11}" stroke="rgba(255,180,80,.5)" stroke-width="1.2"/>`;
  s += `<line x1="${tx - 20}" y1="${ty + th - 7}" x2="${tx}" y2="${ty + th - 7}" stroke="rgba(100,160,220,.5)" stroke-width="1.2"/>`;
  s += '</g>';
  return s;
}

//CC- SR208C collector icon (sun + tank)
function sr208cIcon(cx, iy, ah, collectorT, tankT, elActive) {
  const oy = iy + (ah - 66) / 2 + 2;
  let s = '<g>';
  // collector (top panel)
  const pw = 50, ph = 22, px = cx - pw / 2, py = oy;
  const collC = collectorT > 60 ? 'rgba(255,100,40,.9)' : collectorT > 40 ? 'rgba(255,180,60,.85)' : 'rgba(100,180,230,.75)';
  s += `<rect x="${px}" y="${py}" width="${pw}" height="${ph}" rx="3" fill="${collC}" stroke="rgba(255,230,100,.8)" stroke-width="1.2"/>`;
  // panel grid lines
  for (let i = 1; i < 4; i++) {
    s += `<line x1="${px + (pw / 4) * i}" y1="${py}" x2="${px + (pw / 4) * i}" y2="${py + ph}" stroke="rgba(0,0,0,.3)" stroke-width="0.6"/>`;
  }
  s += `<text x="${cx}" y="${py + ph + 10}" text-anchor="middle" fill="rgba(255,220,100,.95)" font-size="9" font-weight="700">${Math.round(collectorT)}°</text>`;
  // tank below
  const tw = 36, th = 28, tx = cx - tw / 2, ty = py + ph + 14;
  s += `<rect x="${tx}" y="${ty}" width="${tw}" height="${th}" rx="5" fill="rgba(15,25,45,.8)" stroke="rgba(180,210,255,.7)" stroke-width="1.2"/>`;
  const tankColor = tankT > 55 ? 'rgba(255,120,50,.7)' : tankT > 35 ? 'rgba(255,180,60,.65)' : 'rgba(120,180,230,.55)';
  s += `<rect x="${tx + 2}" y="${ty + 2}" width="${tw - 4}" height="${th - 4}" rx="3" fill="${tankColor}"/>`;
  s += `<text x="${cx}" y="${ty + th / 2 + 4}" text-anchor="middle" fill="#fff" font-size="10" font-weight="800">${Math.round(tankT)}°</text>`;
  // electric element indicator
  if (elActive) {
    s += `<circle cx="${tx + tw - 4}" cy="${ty + 4}" r="3" fill="rgba(255,60,60,.95)"><animate attributeName="opacity" values="0.3;1;0.3" dur="1s" repeatCount="indefinite"/></circle>`;
  }
  s += '</g>';
  return s;
}

//CC- Sun icon (smaller, gentler pulse than v0.1)
function sunIcon(cx, cy, pvPower) {
  const r = 12 + Math.min(7, Math.abs(pvPower) / 500);
  const dur = (2.6 - Math.min(1.6, Math.abs(pvPower) / 6000)).toFixed(2);
  let s = '<g>';
  s += `<circle cx="${cx}" cy="${cy}" r="${r + 6}" fill="rgba(255,200,60,.22)" filter="url(#pBlur)"><animate attributeName="r" values="${r + 6};${r + 10};${r + 6}" dur="${dur}s" repeatCount="indefinite"/></circle>`;
  s += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="rgba(255,235,100,.95)" stroke="rgba(255,255,200,.85)" stroke-width="1.5"/>`;
  s += '</g>';
  return s;
}

// ═══════════════════════════════════════════════════════════════════════════
// Main card
// ═══════════════════════════════════════════════════════════════════════════
class GuardPowerFlowCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  setConfig(config) {
    if (!config) throw new Error('Config required');
    const need = ['solar', 'battery_soc', 'battery_flow', 'grid', 'home'];
    for (const k of need) {
      if (!config[k]) throw new Error(`Missing entity: ${k}`);
    }
    this._config = config;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() { return 11; }

  static getStubConfig() {
    return {
      solar: 'sensor.solax_pv_power_total',
      battery_soc: 'sensor.solax_battery_capacity',
      battery_flow: 'sensor.solax_battery_power_charge',
      battery_temp: 'sensor.solax_battery_temperature',
      grid: 'sensor.solax_measured_power',
      home: 'sensor.solax_house_load',
      inverter_power: 'sensor.solax_inverter_power',
      spiraly_power: 'sensor.spiraly_celkovy_vykon',
      spiraly_active_count: 'sensor.spiraly_pocet_aktivnich',
      aku_top: 'sensor.aku_ostra',
      aku_bot: 'sensor.aku_zpatecka',
      sr208c_collector_temp: 'sensor.sr208c_solar_solar_collector_temperature',
      sr208c_tank_temp: 'sensor.sr208c_solar_upper_tank_temperature',
      sr208c_electric: 'binary_sensor.sr208c_solar_electric_element',
    };
  }

  _render() {
    if (!this._hass || !this._config) return;
    const h = this._hass;
    const c = this._config;

    const pv = num(h, c.solar);
    const soc = num(h, c.battery_soc);
    const battFlow = num(h, c.battery_flow); // +charge, -discharge
    const grid = num(h, c.grid); // +import, -export
    const home = num(h, c.home);
    const battTemp = c.battery_temp ? num(h, c.battery_temp) : null;

    const batCharging = battFlow > 50;
    const batDisch = battFlow < -50;
    const gridImport = grid > 50;
    const gridExport = grid < -50;

    // Heating row entities (optional)
    const hasHeating = !!(c.spiraly_power || c.aku_top || c.sr208c_tank_temp);
    const spiralyW = c.spiraly_power ? num(h, c.spiraly_power) : 0;
    const spiralyActive = c.spiraly_active_count ? num(h, c.spiraly_active_count) : (spiralyW > 100 ? Math.ceil(spiralyW / 2000) : 0);
    const akuTop = c.aku_top ? num(h, c.aku_top) : 0;
    const akuBot = c.aku_bot ? num(h, c.aku_bot) : akuTop;
    const sr208cColT = c.sr208c_collector_temp ? num(h, c.sr208c_collector_temp) : 0;
    const sr208cTankT = c.sr208c_tank_temp ? num(h, c.sr208c_tank_temp) : 0;
    const sr208cElActive = c.sr208c_electric ? sstate(h, c.sr208c_electric) === 'on' : false;

    // Viewbox (taller if heating row present)
    const vbH = hasHeating ? 780 : 520;
    const vb = { w: 400, h: vbH };
    const nodeW = 92, nodeH = 126;
    const sunCx = vb.w / 2, sunCy = 48;

    // Row 1: Battery left, Grid right
    const bat = { x: 30, y: 180, w: nodeW, h: nodeH };
    const gri = { x: vb.w - nodeW - 30, y: 180, w: nodeW, h: nodeH };
    // Row 2: Home center
    const hom = { x: (vb.w - nodeW) / 2, y: 360, w: nodeW, h: nodeH };
    // Row 3: Spirály / AKU / SR208C
    const heatW = 100, heatH = 144, heatY = 570;
    const spi = { x: 18, y: heatY, w: heatW, h: heatH };
    const aku = { x: (vb.w - heatW) / 2, y: heatY, w: heatW, h: heatH };
    const sr2 = { x: vb.w - heatW - 18, y: heatY, w: heatW, h: heatH };

    // Flow paths (Bezier curves)
    const paths = {
      // sun → home: slight S-curve via left control so it's not a straight line
      sun2home: `M${sunCx},${sunCy + 18} C${sunCx - 30},${180} ${sunCx + 30},${280} ${hom.x + hom.w / 2},${hom.y}`,
      sun2bat: `M${sunCx - 12},${sunCy + 12} Q${bat.x + bat.w + 40},${110} ${bat.x + bat.w},${bat.y + 25}`,
      sun2grid: `M${sunCx + 12},${sunCy + 12} Q${gri.x - 40},${110} ${gri.x},${gri.y + 25}`,
      bat2hom: `M${bat.x + bat.w},${bat.y + bat.h / 2 + 10} Q${vb.w / 2 - 50},${bat.y + bat.h + 10} ${hom.x + 18},${hom.y + 30}`,
      grid2hom: `M${gri.x},${gri.y + gri.h / 2 + 10} Q${vb.w / 2 + 50},${gri.y + gri.h + 10} ${hom.x + hom.w - 18},${hom.y + 30}`,
      // Heating: home → spiraly / home → sr208c. AKU has no electric flow (thermal only).
      hom2spi: `M${hom.x + 15},${hom.y + hom.h} Q${hom.x - 20},${heatY - 20} ${spi.x + spi.w / 2},${spi.y}`,
      hom2sr2: `M${hom.x + hom.w - 15},${hom.y + hom.h} Q${hom.x + hom.w + 20},${heatY - 20} ${sr2.x + sr2.w / 2},${sr2.y}`,
    };

    // Active flows
    const flows = [];
    if (pv > 50) flows.push({ id: 'f1', path: paths.sun2home, color: '#ffd54f', glow: '#ff9800', w: pv });
    if (pv > 50 && batCharging) flows.push({ id: 'f2', path: paths.sun2bat, color: '#b39ddb', glow: '#7c4dff', w: battFlow });
    if (pv > 50 && gridExport) flows.push({ id: 'f3', path: paths.sun2grid, color: '#66bb6a', glow: '#0f9d58', w: Math.abs(grid) });
    if (batDisch) flows.push({ id: 'f4', path: paths.bat2hom, color: '#b39ddb', glow: '#7c4dff', w: Math.abs(battFlow) });
    if (gridImport) flows.push({ id: 'f5', path: paths.grid2hom, color: '#ef5350', glow: '#db4437', w: grid });
    if (hasHeating && spiralyW > 100) flows.push({ id: 'f6', path: paths.hom2spi, color: '#ff9a3c', glow: '#ff5722', w: spiralyW });
    if (hasHeating && sr208cElActive) flows.push({ id: 'f7', path: paths.hom2sr2, color: '#ff9a3c', glow: '#ff5722', w: 1500 });

    let flowsSvg = '';
    for (const f of flows) {
      flowsSvg += `<path id="${f.id}" d="${f.path}" fill="none" stroke="none"/>`;
    }
    for (const f of flows) {
      flowsSvg += `<path d="${f.path}" fill="none" stroke="${f.color}" stroke-width="1" stroke-dasharray="3,14" opacity="0.18"/>`;
      flowsSvg += makeParticles(f.id, f.color, f.glow, f.w);
    }

    // Node icons
    const fveIcon = sunIcon(sunCx, sunCy, pv);
    const batCardIcon = batteryIcon(bat.x + bat.w / 2, bat.y + 8, 70, soc, batCharging);
    const gridCardIcon = gridIcon(gri.x + gri.w / 2, gri.y + 8, 70);
    const homeCardIcon = homeIcon(hom.x + hom.w / 2, hom.y + 8, 70);

    const batDir = batCharging ? 'Nabíjí' : batDisch ? 'Vybíjí' : 'Standby';
    const gridDir = gridImport ? 'Import' : gridExport ? 'Export' : 'Idle';

    const mainCards = [
      nodeCard(bat.x, bat.y, bat.w, bat.h, '#b39ddb', '#7c4dff', 'rgba(40,20,80,.55)', batCardIcon,
               fmtW(battFlow), batDir, battTemp != null ? `${battTemp.toFixed(0)} °C` : '', batCharging || batDisch),
      nodeCard(gri.x, gri.y, gri.w, gri.h, '#81d4fa', '#0288d1', 'rgba(10,30,60,.55)', gridCardIcon,
               fmtW(grid), gridDir, '', gridImport || gridExport),
      nodeCard(hom.x, hom.y, hom.w, hom.h, '#ffb74d', '#ff6f00', 'rgba(50,25,0,.55)', homeCardIcon,
               fmtW(home), 'Spotřeba', '', home > 50),
    ];

    // Heating row cards
    let heatCards = '';
    if (hasHeating) {
      const spiralyIc = spiralyIcon(spi.x + spi.w / 2, spi.y + 8, 80, spiralyActive);
      const akuIc = akuIcon(aku.x + aku.w / 2, aku.y + 8, 80, akuTop, akuBot);
      const sr2Ic = sr208cIcon(sr2.x + sr2.w / 2, sr2.y + 8, 80, sr208cColT, sr208cTankT, sr208cElActive);

      const spiDir = spiralyActive > 0 ? `${spiralyActive}/3 aktivní` : 'Standby';
      const akuAvg = ((akuTop + akuBot) / 2).toFixed(0);
      const sr2Dir = sr208cElActive ? 'El. topení' : (sr208cColT > 40 ? 'Solar' : 'Idle');

      heatCards += nodeCard(spi.x, spi.y, spi.w, spi.h, '#ffab40', '#ff5722', 'rgba(60,20,0,.6)', spiralyIc,
                            fmtW(spiralyW), 'Spirály', spiDir, spiralyActive > 0);
      heatCards += nodeCard(aku.x, aku.y, aku.w, aku.h, '#ff8a65', '#d84315', 'rgba(40,15,0,.6)', akuIc,
                            `${akuAvg} °C`, 'AKU 2000 l', `${Math.round(akuTop)}° / ${Math.round(akuBot)}°`, akuTop > 40);
      heatCards += nodeCard(sr2.x, sr2.y, sr2.w, sr2.h, '#ffd740', '#ff6f00', 'rgba(45,30,0,.6)', sr2Ic,
                            `${Math.round(sr208cTankT)} °C`, 'SR208C', sr2Dir, sr208cElActive || sr208cColT > 40);
    }

    // Sun "card" — labels BELOW the sun, far enough from pulse circle
    const sunTopCard = `
      <g>
        ${fveIcon}
        <text x="${sunCx}" y="${sunCy + 52}" text-anchor="middle" fill="#ffd54f" font-size="19" font-weight="900" font-family="Inter,sans-serif">${fmtW(pv)}</text>
        <text x="${sunCx}" y="${sunCy + 68}" text-anchor="middle" fill="#ffd54f" font-size="10" font-weight="700" opacity="0.85">FVE</text>
      </g>
    `;

    const svg = `
      <svg viewBox="0 0 ${vb.w} ${vb.h}" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
        <defs>
          <filter id="pBlur" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/></filter>
          <radialGradient id="bgGrad" cx="50%" cy="30%" r="80%">
            <stop offset="0%" stop-color="rgba(30,50,100,.6)"/>
            <stop offset="100%" stop-color="rgba(5,10,25,.98)"/>
          </radialGradient>
        </defs>
        <rect width="100%" height="100%" fill="url(#bgGrad)" rx="14"/>
        ${flowsSvg}
        ${sunTopCard}
        ${mainCards.join('')}
        ${heatCards}
      </svg>
    `;

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 0; overflow: hidden; border-radius: 14px; }
        svg { display: block; width: 100%; height: auto; }
      </style>
      <ha-card>
        ${svg}
      </ha-card>
    `;
  }
}

customElements.define('guard-power-flow-card', GuardPowerFlowCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'guard-power-flow-card',
  name: 'Guard Power Flow',
  description: 'Animated energy flow card for Guard FVE platform (FVE + Bat + Grid + Dům + Spirály + AKU + SR208C)',
  preview: false,
});

console.info(
  '%c GUARD-POWER-FLOW-CARD %c v0.2 ',
  'color: #fff; background: #ff6b35; padding: 2px 6px; border-radius: 3px 0 0 3px;',
  'color: #ff6b35; background: #1a1a1a; padding: 2px 6px; border-radius: 0 3px 3px 0;'
);
