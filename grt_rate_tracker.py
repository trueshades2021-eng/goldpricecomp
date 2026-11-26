#!/usr/bin/env python3
"""
GRT Jewels daily rate tracker
=============================
Scrapes GOLD 24 KT/1g, GOLD 22 KT/1g and SILVER 1g from https://www.grtjewels.com/
and appends one row per day to data/gold_rates.csv.

Setup (one time):
    pip install playwright pandas matplotlib
    playwright install chromium

Usage:
    python grt_rate_tracker.py              # scrape today's rates and save
    python grt_rate_tracker.py --headed     # watch the browser (for debugging)
    python grt_rate_tracker.py --html       # build data/report.html and open it in your browser
    python grt_rate_tracker.py --report     # (optional) terminal table + PNG chart

Schedule (cron, every day 10:00 AM IST):
    0 10 * * * cd /path/to/folder && /usr/bin/python3 grt_rate_tracker.py >> data/run.log 2>&1
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://www.grtjewels.com/"
BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
CSV_PATH = DATA_DIR / "gold_rates.csv"
HTML_PATH = DATA_DIR / "report.html"
FIELDS = ["date", "time_ist", "gold_24kt_per_g", "gold_22kt_per_g", "silver_per_g"]

# Text patterns (tolerant to spacing / dash / rupee symbol differences)
NUM = r"[-–:]?\s*(?:₹|Rs\.?)?\s*([\d,]+(?:\.\d+)?)"
PATTERNS = {
    "gold_24kt_per_g": rf"GOLD\s*24\s*KT\s*/\s*1\s*g\s*{NUM}",
    "gold_22kt_per_g": rf"GOLD\s*22\s*KT\s*/\s*1\s*g\s*{NUM}",
    "silver_per_g": rf"SILVER\s*/?\s*1\s*g\s*{NUM}",
}


def parse_rates(text: str) -> dict:
    rates = {}
    for key, pattern in PATTERNS.items():
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            rates[key] = float(m.group(1).replace(",", ""))
    return rates


def page_text(page) -> str:
    """Visible text + text inside hidden elements (dropdowns are often in the DOM but hidden)."""
    import html as _html
    raw = page.content()
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", _html.unescape(raw))


def has_all(rates: dict) -> bool:
    return all(k in rates for k in PATTERNS)


def open_dropdown(page, header) -> dict:
    """Try several ways to open the rate dropdown; return rates as soon as all 3 are found."""
    rates = parse_rates(page_text(page))          # 0) maybe already in the DOM (hidden)
    if has_all(rates):
        print("[info] all rates found in page HTML without clicking")
        return rates

    box = header.bounding_box()
    strategies = [
        ("hover header", lambda: header.hover()),
        ("click header", lambda: header.click()),
        ("force-click header", lambda: header.click(force=True)),
        ("click parent", lambda: header.locator("xpath=..").click(force=True)),
        ("click grandparent", lambda: header.locator("xpath=../..").click(force=True)),
        ("click arrow right of text", lambda: page.mouse.click(box["x"] + box["width"] + 18, box["y"] + box["height"] / 2)),
        ("click arrow right of text (+30)", lambda: page.mouse.click(box["x"] + box["width"] + 30, box["y"] + box["height"] / 2)),
        ("JS click on ancestors", lambda: header.evaluate(
            "el => { let n = el; for (let i = 0; i < 4 && n; i++, n = n.parentElement) "
            "{ n.dispatchEvent(new MouseEvent('mouseover', {bubbles:true})); n.click(); } }")),
    ]
    for name, action in strategies:
        try:
            action()
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"[info] {name}: failed ({str(e)[:60]})")
            continue
        found = parse_rates(page_text(page))
        rates.update(found)
        print(f"[info] {name}: found {sorted(found)}")
        if has_all(rates):
            return rates
    return rates


def scrape(headed: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    DATA_DIR.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)

        header = page.get_by_text(re.compile(r"GOLD\s*22\s*KT", re.I)).first
        header.wait_for(state="visible", timeout=30_000)
        page.wait_for_timeout(2000)               # let the page finish loading
        rates = parse_rates(header.inner_text())
        rates.update(open_dropdown(page, header))

        # Always save evidence when something is missing
        missing = [k for k in PATTERNS if k not in rates]
        if missing:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            page.screenshot(path=str(DATA_DIR / f"debug_{stamp}.png"))
            (DATA_DIR / f"debug_{stamp}.html").write_text(page.content(), encoding="utf-8")
            try:  # the header's surrounding markup is the most useful thing to see
                snippet = header.evaluate(
                    "el => { let n = el; for (let i = 0; i < 4 && n.parentElement; i++) n = n.parentElement; return n.outerHTML; }")
                (DATA_DIR / f"debug_{stamp}_header.html").write_text(snippet, encoding="utf-8")
            except Exception:
                pass
            print(f"[warn] missing values: {missing} (debug files saved in {DATA_DIR})", file=sys.stderr)
            if headed:
                print("Browser left open for 20s - open the dropdown manually and look at it.")
                page.wait_for_timeout(20_000)

        browser.close()
    return rates


def save(rates: dict) -> None:
    import csv

    DATA_DIR.mkdir(exist_ok=True)
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    row = {"date": now.strftime("%Y-%m-%d"), "time_ist": now.strftime("%H:%M")}
    row.update({k: rates.get(k, "") for k in FIELDS[2:]})

    rows = []
    if CSV_PATH.exists():
        with CSV_PATH.open(newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["date"] != row["date"]]  # one row/day
    rows.append(row)
    rows.sort(key=lambda r: r["date"])

    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"Saved: {row}")


def report() -> None:
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not CSV_PATH.exists():
        sys.exit("No data yet. Run the scraper first.")
    df = pd.read_csv(CSV_PATH, parse_dates=["date"]).set_index("date")
    df["24kt_day_change"] = df["gold_24kt_per_g"].diff()
    df["22kt_day_change"] = df["gold_22kt_per_g"].diff()
    df["silver_day_change"] = df["silver_per_g"].diff()
    print(df.drop(columns=["time_ist"]).to_string())

    monthly = df[["gold_24kt_per_g", "gold_22kt_per_g", "silver_per_g"]].resample("MS").agg(
        ["min", "max", "mean"]
    ).round(2)
    print("\nMonthly summary:\n", monthly.to_string())

    fig, ax1 = plt.subplots(figsize=(11, 5))
    ax1.plot(df.index, df["gold_24kt_per_g"], label="Gold 24KT /g", color="goldenrod")
    ax1.plot(df.index, df["gold_22kt_per_g"], label="Gold 22KT /g", color="darkred")
    ax1.set_ylabel("Gold (₹/g)")
    ax2 = ax1.twinx()  # silver is ~100x cheaper, so it gets its own axis
    ax2.plot(df.index, df["silver_per_g"], label="Silver /g", color="gray", linestyle="--")
    ax2.set_ylabel("Silver (₹/g)")
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left")
    ax1.set_title("GRT Jewels – daily rates")
    fig.autofmt_xdate()
    out = DATA_DIR / "rates_chart.png"
    fig.savefig(out, dpi=130, bbox_inches="tight")
    print(f"\nChart saved: {out}")


HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GRT Jewels – Gold &amp; Silver Rate Tracker</title>
<style>
:root{--bg:#f6f3ee;--card:#fff;--ink:#1f1a17;--muted:#6f655e;--line:#e8e1d8;--accent:#8a1c24;--gold:#c28a0e;--g22:#9b1c2c;--silver:#6b7a8c;--up:#17803d;--down:#c0262d;--chip:#f1ebe2;--shadow:0 1px 2px rgba(60,40,20,.06),0 4px 14px rgba(60,40,20,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#14110f;--card:#1e1a17;--ink:#f1ebe4;--muted:#a59a90;--line:#322c27;--accent:#e0646d;--gold:#e4b43c;--g22:#e0646d;--silver:#a9b4c0;--up:#4cc079;--down:#ff7a80;--chip:#2a2420;--shadow:none}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:24px 18px 48px}
header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:10px;margin-bottom:18px}
h1{margin:0;font-size:26px;letter-spacing:-.3px}
h1 span{color:var(--accent)}
.sub{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);padding:16px 18px}
.controls{display:flex;flex-wrap:wrap;gap:14px 22px;align-items:center;margin-bottom:16px}
.grp{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.lbl{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);margin-right:4px}
.chip,.btn{border:1px solid var(--line);background:var(--chip);color:var(--ink);padding:6px 12px;border-radius:999px;font:inherit;font-size:13px;cursor:pointer}
.chip:hover,.btn:hover{border-color:var(--accent)}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.btn{border-radius:8px}
input[type=date]{border:1px solid var(--line);background:var(--card);color:var(--ink);padding:5px 8px;border-radius:8px;font:inherit;font-size:13px}
.tog{display:inline-flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;padding:5px 10px;border:1px solid var(--line);border-radius:999px;background:var(--chip)}
.tog input{accent-color:var(--accent)}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:16px}
.kpi .name{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px}
.kpi .val{font-size:30px;font-weight:700;letter-spacing:-.5px;margin:4px 0 2px}
.kpi .val small{font-size:13px;color:var(--muted);font-weight:500}
.kpi .row{display:flex;justify-content:space-between;font-size:13px;margin-top:4px}
.kpi .row .k{color:var(--muted)}
.up{color:var(--up);font-weight:600}.down{color:var(--down);font-weight:600}.flat{color:var(--muted)}
.chartbox{position:relative;height:380px}
h2{font-size:16px;margin:0 0 12px;flex-wrap:wrap;display:flex;justify-content:space-between;align-items:center;gap:8px}
h2 .sub{font-weight:400}
section{margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:9px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--card);cursor:pointer;user-select:none}
th.nosort{cursor:default}
tbody tr:hover{background:var(--chip)}
.scroll{overflow:auto;max-height:480px;border-radius:8px}
.scroll.x{max-height:none}
td .d{display:block;font-size:12px}
.muted{color:var(--muted)}
.empty{padding:40px;text-align:center;color:var(--muted)}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:24px}
@media(max-width:800px){.kpis{grid-template-columns:1fr}.chartbox{height:300px}}

body{overflow-x:hidden}
.sh{display:none}
.arr{font-style:normal}
@media(max-width:640px){
 body{font-size:14px}
 .wrap{padding:14px 12px 36px}
 h1{font-size:21px;line-height:1.2}
 .sub{font-size:12px}
 header{margin-bottom:12px}
 .card{padding:12px 14px;border-radius:12px}
 .controls{flex-direction:column;align-items:stretch;gap:14px;margin-bottom:12px}
 .grp{display:grid;gap:6px;width:100%}
 .grp .lbl{grid-column:1/-1;margin:0 0 2px}
 .g-period{grid-template-columns:repeat(4,1fr)}
 .g-custom{grid-template-columns:1fr 1fr}
 .g-custom .to{display:none}
 .g-show{grid-template-columns:repeat(3,1fr)}
 .g-mode{grid-template-columns:1fr 1fr}
 .chip{padding:8px 4px;text-align:center}
 input[type=date]{width:100%;min-width:0;padding:8px;font-size:16px}
 .tog{justify-content:center;padding:8px 6px;font-size:12px;gap:5px}
 .full{display:none}.sh{display:inline}
 .kpis{gap:10px;margin-bottom:12px}
 .kpi .val{font-size:24px;margin:2px 0}
 .kpi .row{font-size:12px}
 section{margin-bottom:12px}
 .chartbox{height:280px}
 h2{font-size:15px}
 .scroll{max-height:420px}
 #tbl th,#tbl td{padding:8px 6px;font-size:13px}
 #tbl th{font-size:10px;letter-spacing:.2px}
 #tbl td:first-child{white-space:normal}
 td .d{font-size:11px}
 #stats thead{display:none}
 #stats,#stats tbody,#stats tr,#stats td{display:block;width:100%}
 #stats tr{border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-bottom:10px}
 #stats tr:last-child{margin-bottom:0}
 #stats td{display:flex;justify-content:space-between;align-items:baseline;border:0;padding:5px 0;text-align:right;white-space:normal}
 #stats td::before{content:attr(data-l);color:var(--muted);font-size:12px;text-align:left;margin-right:12px}
 #stats td:first-child{font-weight:600;padding-bottom:6px;border-bottom:1px solid var(--line);margin-bottom:4px}
 #stats td:first-child::before{content:none}
 #stats td .d{display:inline;margin-left:6px}
}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1><span>GRT</span> Gold &amp; Silver Rate Tracker</h1>
      <div class="sub" id="meta"></div>
    </div>
  </header>

  <div class="card controls">
    <div class="grp g-period"><span class="lbl">Period</span>
      <button class="chip" data-r="7">7D</button>
      <button class="chip" data-r="30">30D</button>
      <button class="chip" data-r="90">3M</button>
      <button class="chip" data-r="180">6M</button>
      <button class="chip" data-r="ytd">YTD</button>
      <button class="chip" data-r="365">1Y</button>
      <button class="chip on" data-r="all">All</button>
    </div>
    <div class="grp g-custom"><span class="lbl">Custom</span>
      <input type="date" id="from"> <span class="muted to">to</span> <input type="date" id="to">
    </div>
    <div class="grp g-show"><span class="lbl">Show</span>
      <label class="tog"><input type="checkbox" data-m="g24" checked><span class="dot" style="background:var(--gold)"></span><span class="full">Gold 24 KT</span><span class="sh">24 KT</span></label>
      <label class="tog"><input type="checkbox" data-m="g22" checked><span class="dot" style="background:var(--g22)"></span><span class="full">Gold 22 KT</span><span class="sh">22 KT</span></label>
      <label class="tog"><input type="checkbox" data-m="ag" checked><span class="dot" style="background:var(--silver)"></span>Silver</label>
    </div>
    <div class="grp g-mode"><span class="lbl">Chart</span>
      <button class="chip on" data-mode="abs">Price (₹)</button>
      <button class="chip" data-mode="pct">% change</button>
    </div>
  </div>

  <div class="kpis" id="kpis"></div>

  <section class="card">
    <h2>Trend <span class="sub" id="chartMsg"></span></h2>
    <div class="chartbox"><canvas id="chart"></canvas></div>
  </section>

  <section class="card">
    <h2>Comparison for selected period <span class="sub">price per gram</span></h2>
    <div class="scroll x">
      <table id="stats">
        <thead><tr><th class="nosort">Metal</th><th class="nosort">Lowest</th><th class="nosort">Highest</th><th class="nosort">Average</th><th class="nosort">Start</th><th class="nosort">Latest</th><th class="nosort">Change</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <h2>Daily rates <span><span class="sub" id="count"></span> <button class="btn" id="dl">Download CSV</button></span></h2>
    <div class="scroll">
      <table id="tbl">
        <thead><tr>
          <th data-s="date">Date<i class="arr"></i></th><th data-s="g24"><span class="full">Gold 24 KT /g</span><span class="sh">24 KT</span><i class="arr"></i></th><th data-s="g22"><span class="full">Gold 22 KT /g</span><span class="sh">22 KT</span><i class="arr"></i></th><th data-s="ag"><span class="full">Silver /g</span><span class="sh">Silver</span><i class="arr"></i></th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <footer>Source: grtjewels.com · Changes are versus the previous recorded day · Chart.js loads from the internet</footer>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>
(function(){
const RAW = __DATA__;
const UPDATED = "__UPDATED__";
const M = [
  {k:'g24', label:'Gold 24 KT', css:'--gold'},
  {k:'g22', label:'Gold 22 KT', css:'--g22'},
  {k:'ag',  label:'Silver',     css:'--silver'}
];
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const fmtN = (v,d=2) => v==null ? '–' : Number(v).toLocaleString('en-IN',{maximumFractionDigits:d});
const fmtR = v => v==null ? '–' : '₹ ' + fmtN(v);
const MQ = window.matchMedia('(max-width:640px)');
const mob = () => MQ.matches;
const fmtD = (s,long) => new Date(s+'T00:00:00').toLocaleDateString('en-IN', long ? {day:'numeric',month:'short',year:'numeric',weekday:'short'} : {day:'numeric',month:'short',year:'numeric'});
const iso = d => d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

RAW.sort((a,b) => a.date.localeCompare(b.date));
RAW.forEach((r,i) => M.forEach(m => {
  const p = i>0 ? RAW[i-1][m.k] : null;
  r['d_'+m.k] = (r[m.k]!=null && p!=null) ? r[m.k]-p : null;
  r['p_'+m.k] = (r[m.k]!=null && p) ? (r[m.k]-p)/p*100 : null;
}));

const S = {range:'all', from:'', to:'', on:{g24:true,g22:true,ag:true}, mode:'abs', sk:'date', sd:-1};
let chart = null;

const rows = () => RAW.filter(r => (!S.from || r.date>=S.from) && (!S.to || r.date<=S.to));

function delta(d,p){
  if(d==null) return '<span class="flat">–</span>';
  const c = d>0?'up':d<0?'down':'flat', a = d>0?'▲':d<0?'▼':'■';
  return '<span class="'+c+'">'+a+' '+fmtN(Math.abs(d))+' ('+Math.abs(p).toFixed(2)+'%)</span>';
}

function setRange(r){
  S.range = r;
  if(RAW.length){
    const last = RAW[RAW.length-1].date;
    if(r==='all'){ S.from=''; S.to=''; }
    else if(r==='ytd'){ S.from = last.slice(0,4)+'-01-01'; S.to=''; }
    else if(r!=='custom'){ const d = new Date(last+'T00:00:00'); d.setDate(d.getDate()-Number(r)); S.from = iso(d); S.to=''; }
  }
  $('#from').value = S.from; $('#to').value = S.to;
  $$('[data-r]').forEach(b => b.classList.toggle('on', b.dataset.r===r));
  render();
}

function renderKPIs(){
  const rs = rows(), box = $('#kpis'); box.innerHTML='';
  M.forEach(m => {
    const all = RAW.filter(r => r[m.k]!=null), last = all[all.length-1];
    const v = rs.filter(r => r[m.k]!=null);
    let per = '<span class="flat">–</span>';
    if(v.length>1){ const f=v[0][m.k], l=v[v.length-1][m.k]; per = delta(l-f, f?(l-f)/f*100:0); }
    box.insertAdjacentHTML('beforeend',
      '<div class="card kpi"><div class="name"><span class="dot" style="background:var('+m.css+')"></span>'+m.label+' · per gram</div>'+
      '<div class="val">'+(last?fmtR(last[m.k]):'–')+'</div>'+
      '<div class="row"><span class="k">Vs previous day</span>'+(last?delta(last['d_'+m.k],last['p_'+m.k]):'–')+'</div>'+
      '<div class="row"><span class="k">Over selected period</span>'+per+'</div></div>');
  });
}

function renderChart(){
  const rs = rows(), pct = S.mode==='pct';
  $('#chartMsg').textContent = '';
  if(chart){ chart.destroy(); chart=null; }
  if(typeof Chart==='undefined'){ $('#chartMsg').textContent='Chart library could not load. Connect to the internet and reload.'; return; }
  if(!rs.length){ $('#chartMsg').textContent='No data in this period.'; return; }
  const tick = css('--muted'), grid = css('--line'), mb = mob();
  const ds = M.filter(m => S.on[m.k]).map(m => {
    const base = (rs.find(r => r[m.k]!=null)||{})[m.k];
    return {label:m.label, data:rs.map(r => r[m.k]==null ? null : (pct ? (r[m.k]/base-1)*100 : r[m.k])),
      borderColor:css(m.css), backgroundColor:css(m.css), yAxisID:(pct||m.k!=='ag')?'y':'y1',
      tension:.25, pointRadius:rs.length>(mb?25:60)?0:3, pointHoverRadius:5, borderWidth:2.2, spanGaps:true, borderDash:m.k==='ag'?[6,4]:[]};
  });
  chart = new Chart($('#chart'), {type:'line', data:{labels:rs.map(r=>fmtD(r.date)), datasets:ds}, options:{
    responsive:true, maintainAspectRatio:false, interaction:{mode:'index',intersect:false},
    plugins:{legend:{labels:{color:tick,usePointStyle:true,boxWidth:8,font:{size:mb?11:12}}},
      tooltip:{callbacks:{label:c=>' '+c.dataset.label+': '+(pct?c.parsed.y.toFixed(2)+' %':'₹ '+fmtN(c.parsed.y))}}},
    scales:{
      x:{ticks:{color:tick,maxTicksLimit:mb?4:10,maxRotation:0,font:{size:mb?10:12}},grid:{display:false}},
      y:{display:pct||S.on.g24||S.on.g22, position:'left', ticks:{color:tick,font:{size:mb?10:12},callback:v=>pct?v+'%':'₹'+fmtN(v,0)}, grid:{color:grid}, title:{display:!mb,text:pct?'% change from start':'Gold ₹/g',color:tick}},
      y1:{display:!pct&&S.on.ag, position:'right', ticks:{color:tick,font:{size:mb?10:12},callback:v=>'₹'+fmtN(v,0)}, grid:{drawOnChartArea:false}, title:{display:!mb,text:'Silver ₹/g',color:tick}}
    }}});
}

function renderStats(){
  const rs = rows(), tb = $('#stats tbody'); tb.innerHTML='';
  M.forEach(m => {
    const lab = '<td><span class="dot" style="background:var('+m.css+')"></span> '+m.label+'</td>';
    const v = rs.filter(r => r[m.k]!=null);
    if(!v.length){ tb.insertAdjacentHTML('beforeend','<tr>'+lab+'<td colspan="6" class="muted" data-l="">No data</td></tr>'); return; }
    let lo=v[0], hi=v[0], sum=0;
    v.forEach(r => { if(r[m.k]<lo[m.k]) lo=r; if(r[m.k]>hi[m.k]) hi=r; sum+=r[m.k]; });
    const f=v[0][m.k], l=v[v.length-1][m.k];
    const c = (l,h) => '<td data-l="'+l+'"><span>'+h+'</span></td>';
    tb.insertAdjacentHTML('beforeend','<tr>'+lab+
      c('Lowest', fmtR(lo[m.k])+'<span class="d muted">'+fmtD(lo.date)+'</span>')+
      c('Highest', fmtR(hi[m.k])+'<span class="d muted">'+fmtD(hi.date)+'</span>')+
      c('Average', fmtR(sum/v.length))+c('Start', fmtR(f))+c('Latest', fmtR(l))+
      c('Change', delta(l-f, f?(l-f)/f*100:0))+'</tr>');
  });
}

function renderTable(){
  const rs = rows().slice().sort((a,b) => {
    const x=a[S.sk], y=b[S.sk];
    if(x==null) return 1; if(y==null) return -1;
    return (x<y?-1:x>y?1:0)*S.sd;
  });
  $('#count').textContent = rs.length+' day'+(rs.length===1?'':'s')+' ';
  $$('#tbl th').forEach(th => { th.querySelector('.arr').textContent = th.dataset.s===S.sk ? (S.sd>0?' ▲':' ▼') : ''; });
  const tb = $('#tbl tbody');
  if(!rs.length){ tb.innerHTML = '<tr><td colspan="4" class="empty">No data in this period.</td></tr>'; return; }
  const dc = r => { if(!mob()) return fmtD(r.date,true); const d=new Date(r.date+'T00:00:00'); return d.toLocaleDateString('en-IN',{day:'numeric',month:'short'})+'<span class="d muted">'+d.toLocaleDateString('en-IN',{weekday:'short'})+' · '+d.getFullYear()+'</span>'; };
  tb.innerHTML = rs.map(r => '<tr><td>'+dc(r)+'</td>'+
    M.map(m => '<td>'+fmtR(r[m.k])+'<span class="d">'+delta(r['d_'+m.k],r['p_'+m.k])+'</span></td>').join('')+'</tr>').join('');
}

function render(){ renderKPIs(); renderChart(); renderStats(); renderTable(); }

// events
$$('[data-r]').forEach(b => b.addEventListener('click', () => setRange(b.dataset.r)));
$$('[data-mode]').forEach(b => b.addEventListener('click', () => {
  S.mode = b.dataset.mode; $$('[data-mode]').forEach(x => x.classList.toggle('on', x===b)); renderChart(); }));
$$('[data-m]').forEach(c => c.addEventListener('change', () => { S.on[c.dataset.m] = c.checked; renderChart(); }));
['#from','#to'].forEach(id => $(id).addEventListener('change', () => { S.from=$('#from').value; S.to=$('#to').value; setRange('custom'); }));
$$('#tbl th').forEach(th => th.addEventListener('click', () => {
  if(S.sk===th.dataset.s) S.sd*=-1; else { S.sk=th.dataset.s; S.sd = th.dataset.s==='date'?-1:-1; } renderTable(); }));
$('#dl').addEventListener('click', () => {
  const out = ['Date,Gold 24KT per g,Gold 22KT per g,Silver per g'].concat(rows().map(r => [r.date,r.g24??'',r.g22??'',r.ag??''].join(',')));
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([out.join('\n')],{type:'text/csv'}));
  a.download = 'grt_rates_filtered.csv'; a.click();
});
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
MQ.addEventListener('change', render);

$('#meta').textContent = RAW.length ? ('Data: '+fmtD(RAW[0].date)+' → '+fmtD(RAW[RAW.length-1].date)+' · '+RAW.length+(RAW.length===1?' day':' days')+' · Generated '+UPDATED) : 'No data yet – run the scraper first.';
setRange('all');
})();
</script>
</body>
</html>
'''


def build_html(open_browser: bool = False) -> None:
    """Generate data/report.html (table, filters, charts) from the CSV."""
    import csv
    import json

    if not CSV_PATH.exists():
        sys.exit("No data yet. Run the scraper first.")

    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        data = [
            {
                "date": r["date"],
                "g24": num(r["gold_24kt_per_g"]),
                "g22": num(r["gold_22kt_per_g"]),
                "ag": num(r["silver_per_g"]),
            }
            for r in csv.DictReader(f)
        ]
    stamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %H:%M IST")
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data)).replace("__UPDATED__", stamp)
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"HTML report saved: {HTML_PATH}")
    if open_browser:
        import webbrowser
        webbrowser.open(HTML_PATH.as_uri())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--report", action="store_true", help="print table and save PNG chart")
    ap.add_argument("--html", action="store_true", help="build data/report.html and open it in the browser")
    args = ap.parse_args()

    if args.html:
        build_html(open_browser=True)
    elif args.report:
        report()
    else:
        data = scrape(headed=args.headed)
        if not data:
            sys.exit("No rates found – check data/debug_* files.")
        save(data)
        build_html()  # keep data/report.html fresh after every run
