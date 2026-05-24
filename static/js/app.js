const { createApp, ref, computed, onMounted } = Vue;
createApp({
  delimiters: ['[[', ']]'],
  setup() {
    // Core state
    const tab = ref('scan');
    const loading = ref(false); const fetched = ref(false);
    const status = ref(''); const message = ref(''); const cached = ref(false);
    const stocks = ref([]); const total = ref(0);

    // Sell check
    const sellCode = ref(''); const sellPrice = ref(''); const sellResult = ref(null);

    // Positions
    const stats = ref({total_trades:0,win_rate:0,total_profit:0,floating_pnl:0,positions:[]});
    const trades = ref([]);

    // Diary
    const newDiary = ref(''); const diaryList = ref([]);

    // Market data
    const marketTicker = ref({}); const aiBrief = ref('');
    const heatmapData = ref([]); const moneyIn = ref([]); const moneyOut = ref([]); const moneyflowDate = ref('');
    const marketEnvText = ref(''); const sectorRotation = ref([]);
    const newsList = ref([]); const currentTime = ref(''); const currentDate = ref('');

    // Auto scan
    const autoScanEnabled = ref(false); const scanTimer = ref(null);

    // Charts
    const equityCurveData = ref([]); const monthlyPnlData = ref([]);

    // Backtest
    const backtestDays = ref('60'); const backtestTopN = ref('3');
    const backtestMode = ref('simple'); const backtestResult = ref(null); const backtesting = ref(false);

    // Strategy tools
    const strategyComparison = ref(null);

    // Factor evolution
    const evolving = ref(false); const evolveResult = ref(null);

    // Long term eval
    const years = ref(3); const longTermResult = ref(null); const longTermLoading = ref(false);

    // K-line
    const klineVisible = ref(false); const klineCode = ref('');
    const klineImage = ref(''); const klineLoading = ref(false); const klineError = ref('');

    // Factor detail
    const factorDetailVisible = ref(false); const factorDetailCode = ref('');
    const factorDetailData = ref([]); const factorDetailSignal = ref(0);
    const factorDetailReason = ref(''); const factorDetailLoading = ref(false);

    // Diary preview
    const previewReminders = computed(() => {
      const kw = { '止损':'严格执行止损','追高':'避免追高','仓位':'注意仓位' };
      const r = [];
      for (const [k,v] of Object.entries(kw)) { if (newDiary.value.includes(k)) r.push(v); }
      return r.length ? r.slice(0,3) : ['认真复盘'];
    });

    // ==================== Core Functions ====================

    function updateClock() {
      const n = new Date();
      currentTime.value = n.toLocaleTimeString('zh-CN', {hour12:false});
      currentDate.value = n.toLocaleDateString('zh-CN', {month:'2-digit',day:'2-digit',weekday:'short'});
    }

    async function executeScan() {
      loading.value = true; fetched.value = false;
      try {
        const r = await fetch('/api/analyze', {method:'POST'});
        const d = await r.json();
        status.value = d.status || ''; message.value = d.message || '';
        stocks.value = d.stocks || []; total.value = d.total || 0;
        cached.value = !!d.cached;
      } catch(e) { message.value = '扫描失败'; }
      finally { loading.value = false; fetched.value = true; }
    }

    async function loadAIBrief() {
      try { const r = await fetch('/api/ai/brief'); const d = await r.json(); aiBrief.value = d.brief || ''; } catch(e) {}
    }

    async function checkSell() {
      if (!sellCode.value) return;
      try {
        const r = await fetch('/api/sell_check', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({code:sellCode.value, buy_price:parseFloat(sellPrice.value)||0})
        });
        sellResult.value = await r.json();
      } catch(e) { sellResult.value = {error:'请求失败'}; }
    }

    async function buyStock(code, price) {
      try {
        await fetch('/api/position/buy', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({code, price})});
        loadStats();
      } catch(e) {}
    }

    async function quickSell(code) {
      const price = prompt('卖出价格:');
      if (!price) return;
      try {
        await fetch('/api/position/sell', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({code, price:parseFloat(price)})});
        loadStats();
      } catch(e) {}
    }

    async function loadStats() {
      try { const r = await fetch('/api/stats'); stats.value = await r.json(); } catch(e) {}
      try { const r = await fetch('/api/trades'); trades.value = await r.json(); } catch(e) {}
    }

    async function loadDiary() {
      try { const r = await fetch('/api/diary'); diaryList.value = await r.json(); } catch(e) {}
    }

    async function saveDiary() {
      if (!newDiary.value.trim()) return;
      try {
        await fetch('/api/diary', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:newDiary.value})});
        newDiary.value = ''; loadDiary();
      } catch(e) {}
    }

    async function deleteDiary(id) {
      try { await fetch(`/api/diary/${id}`, {method:'DELETE'}); loadDiary(); } catch(e) {}
    }

    async function fetchHeatmap() {
      try { const r = await fetch('/api/heatmap'); const d = await r.json(); heatmapData.value = d.data || []; } catch(e) {}
    }

    async function fetchMoney() {
      try {
        const r = await fetch('/api/moneyflow'); const d = await r.json();
        moneyflowDate.value = d.date || ''; moneyIn.value = d.top_inflow || [];
        moneyOut.value = d.top_outflow || [];
      } catch(e) {}
    }

    async function fetchMarketEnv() {
      try { const r = await fetch('/api/market_env'); const d = await r.json(); marketEnvText.value = `${d.environment} ${d.advice}`; } catch(e) {}
    }

    async function fetchSectorRotation() {
      try { const r = await fetch('/api/sector_rotation'); sectorRotation.value = await r.json(); } catch(e) {}
    }

    async function fetchMarketTicker() {
      try { const r = await fetch('/api/market_ticker'); const d = await r.json(); if (!d.error) marketTicker.value = d; } catch(e) {}
    }

    function fv(v) {
      if (!v) return '--';
      const n = Number(v);
      return n >= 1e8 ? (n/1e8).toFixed(2)+'亿' : (n/1e4).toFixed(2)+'万';
    }

    // ==================== Auto Scan ====================

    function toggleAutoScan() {
      autoScanEnabled.value = !autoScanEnabled.value;
      if (autoScanEnabled.value) {
        if ('Notification' in window && Notification.permission === 'default') {
          Notification.requestPermission();
        }
        scanTimer.value = setInterval(() => {
          const now = new Date();
          if (now.getHours() === 14 && now.getMinutes() >= 30 && now.getMinutes() <= 37 && !loading.value) {
            executeScan();
          }
        }, 60000);
      } else {
        if (scanTimer.value) { clearInterval(scanTimer.value); scanTimer.value = null; }
      }
    }

    // ==================== Charts ====================

    function renderECharts(elId, option) {
      const el = document.getElementById(elId);
      if (!el) return;
      const chart = echarts.getInstanceByDom(el) || echarts.init(el);
      chart.setOption(option, true);
      chart.resize();
    }

    async function loadEquityCurve() {
      try {
        const r = await fetch('/api/equity_curve'); const d = await r.json();
        if (!d.curve || !d.curve.length) return;
        equityCurveData.value = d.curve; monthlyPnlData.value = d.monthly || [];
        renderECharts('equityChart', {
          tooltip: {trigger:'axis'},
          grid: {left:'8%',right:'5%',top:'5%',bottom:'8%'},
          xAxis: {type:'category',data:d.curve.map(x=>x.date),axisLabel:{color:'#787b86',fontSize:10}},
          yAxis: {type:'value',axisLabel:{color:'#787b86'},splitLine:{lineStyle:{color:'#2a2e39'}}},
          series: [{type:'line',data:d.curve.map(x=>x.equity),
            lineStyle:{color:'#f0b90b',width:2},
            areaStyle:{color:'rgba(240,185,11,0.06)'},smooth:true}]
        });
        renderECharts('drawdownChart', {
          tooltip: {trigger:'axis'},
          grid: {left:'8%',right:'5%',top:'5%',bottom:'8%'},
          xAxis: {type:'category',data:d.drawdown.map(x=>x.date),axisLabel:{color:'#787b86',fontSize:10}},
          yAxis: {type:'value',axisLabel:{color:'#787b86'},splitLine:{lineStyle:{color:'#2a2e39'}}},
          series: [{type:'line',data:d.drawdown.map(x=>x.drawdown),
            lineStyle:{color:'#f23645',width:1.5},
            areaStyle:{color:'rgba(242,54,69,0.1)'},smooth:true}]
        });
        renderECharts('monthlyChart', {
          tooltip: {trigger:'axis'},
          grid: {left:'8%',right:'5%',top:'5%',bottom:'8%'},
          xAxis: {type:'category',data:d.monthly.map(x=>x.month),axisLabel:{color:'#787b86',fontSize:10,rotate:30}},
          yAxis: {type:'value',axisLabel:{color:'#787b86'},splitLine:{lineStyle:{color:'#2a2e39'}}},
          series: [{type:'bar',data:d.monthly.map(x=>x.pnl),
            itemStyle:{color:function(p){return p.value>=0?'#089981':'#f23645';}}}]
        });
      } catch(e) {}
    }

    // ==================== K-line ====================

    async function showKline(code) {
      klineVisible.value = true; klineCode.value = code;
      klineLoading.value = true; klineError.value = '';
      try {
        const r = await fetch(`/api/kline/${code}`);
        if (!r.ok) { klineError.value = '数据获取失败'; return; }
        const d = await r.json();
        if (d.error) { klineError.value = d.error; return; }
        klineImage.value = d.image || '';
      } catch(e) { klineError.value = '请求失败'; }
      finally { klineLoading.value = false; }
    }

    // ==================== Factor Detail ====================

    async function showFactorDetail(s) {
      factorDetailVisible.value = true; factorDetailCode.value = s.code;
      factorDetailLoading.value = true;
      try {
        const r = await fetch('/api/factor_backtest', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({factor_name:s.code,days:60})
        });
        const d = await r.json();
        factorDetailData.value = d.data || []; factorDetailSignal.value = s.signal;
        factorDetailReason.value = s.priority_reason;
      } catch(e) {}
      finally { factorDetailLoading.value = false; }
    }

    // ==================== Factor Evolution ====================

    async function evolveFactors() {
      evolving.value = true; evolveResult.value = null;
      try {
        const r = await fetch('/api/ai/evolve_factors', {method:'POST'});
        evolveResult.value = await r.json();
      } catch(e) { evolveResult.value = {error:'请求失败'}; }
      finally { evolving.value = false; }
    }

    // ==================== Strategy ====================

    async function loadStrategyComparison() {
      try { const r = await fetch('/api/strategy_comparison'); strategyComparison.value = await r.json(); } catch(e) {}
    }

    async function runBacktest() {
      backtesting.value = true; backtestResult.value = null;
      try {
        const r = await fetch('/api/backtest', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({days:parseInt(backtestDays.value)||30, top_n:parseInt(backtestTopN.value)||3, mode:backtestMode.value})
        });
        backtestResult.value = await r.json();
      } catch(e) {}
      finally { backtesting.value = false; }
    }

    async function runLongTermEval() {
      longTermLoading.value = true;
      try {
        const r = await fetch('/api/long_term_eval', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({years:years.value})
        });
        longTermResult.value = await r.json();
      } catch(e) {}
      finally { longTermLoading.value = false; }
    }

    // ==================== Mount ====================

    onMounted(async () => {
      updateClock(); setInterval(updateClock, 1000);
      await fetchMarketTicker(); await executeScan();
      await loadAIBrief(); await fetchHeatmap(); await fetchMoney();
      await loadStats(); await loadEquityCurve();
      try { const r = await fetch('/api/news'); newsList.value = (await r.json()).map(n=>({...n,expanded:false})); } catch(e) {}
      setInterval(fetchMarketTicker, 30000);
    });

    return {
      tab, loading, fetched, cached, status, message, stocks, total,
      sellCode, sellPrice, sellResult, checkSell,
      stats, trades, loadStats,
      newDiary, diaryList, loadDiary, saveDiary, deleteDiary, previewReminders,
      marketTicker, aiBrief, loadAIBrief,
      heatmapData, moneyIn, moneyOut, moneyflowDate,
      marketEnvText, fetchMarketEnv, sectorRotation, fetchSectorRotation,
      newsList, currentTime, currentDate, fv,
      autoScanEnabled, scanTimer, toggleAutoScan,
      equityCurveData, monthlyPnlData, loadEquityCurve,
      executeScan, buyStock, quickSell,
      backtestDays, backtestTopN, backtestMode, backtestResult, backtesting, runBacktest,
      strategyComparison, loadStrategyComparison,
      evolving, evolveResult, evolveFactors,
      years, longTermResult, longTermLoading, runLongTermEval,
      klineVisible, klineCode, klineImage, klineLoading, klineError, showKline,
      factorDetailVisible, factorDetailCode, factorDetailData, factorDetailSignal,
      factorDetailReason, factorDetailLoading, showFactorDetail,
    };
  }
}).mount('#app');
