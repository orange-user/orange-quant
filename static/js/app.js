const { createApp, ref, computed, onMounted } = Vue;
createApp({
  delimiters: ['[[', ']]'],
  setup() {
    const tab = ref('trade');
    const loading = ref(false); const fetched = ref(false); const status = ref(''); const message = ref(''); const cached = ref(false);
    const stocks = ref([]); const total = ref(0);
    const yiming = ref({id:1,text:'加载中...',translation:'',summary:''});
    const petMessage = ref('汪汪~'); const petX = ref(window.innerWidth-120); const petY = ref(window.innerHeight-100);
    const dogSleeping = ref(true); const aiBrief = ref('');
    const sellCode = ref(''); const sellPrice = ref(''); const sellResult = ref(null);
    const newDiary = ref(''); const diaryList = ref([]);
    const stats = ref({total_trades:0,win_rate:0,total_profit:0,floating_pnl:0,positions:[]}); const trades = ref([]);
    const newsList = ref([]); const heatmapData = ref([]); const moneyIn = ref([]); const moneyOut = ref([]); const moneyflowDate = ref('');
    const currentTime = ref(''); const currentDate = ref('');
    const petAnimFrame = ref(0); const petWagging = ref(false); const mouseX = ref(200); const mouseY = ref(400);
    const dragging = ref(false); const dragStartX = ref(0); const dragStartY = ref(0); const petStartX = ref(0); const petStartY = ref(0);
    const autoScanEnabled = ref(false); const scanTimer = ref(null);
    const equityCurveData = ref([]); const monthlyPnlData = ref([]);
    const backtestDays = ref('60'); const backtestTopN = ref('3');
    const backtestMode = ref('simple');
    const backtestResult = ref(null); const backtesting = ref(false);
    const evolving = ref(false);
    const evolveResult = ref(null);
    const factorStatus = ref(null);
    const klineVisible = ref(false); const klineCode = ref(''); const klineImage = ref('');
    const klineLoading = ref(false); const klineError = ref('');
    const strategyComparison = ref(null); const strategyCombo = ref(null);
    const factorRegistry = ref([]); const factorRankList = ref([]);
    const factorBacktestResult = ref(null); const factorBacktesting = ref(false);
    const factorDetailVisible = ref(false); const factorDetailCode = ref('');
    const factorDetailData = ref([]); const factorDetailSignal = ref(0);
    const factorDetailReason = ref(''); const factorDetailLoading = ref(false);
    const indicatorCycleList = ref([]); const marketMatch = ref(null);
    const longTermResult = ref(null); const longTermLoading = ref(false);
    const weightsResult = ref(null); const dataQuality = ref(null); const dockerfile = ref(''); const updateResult = ref('');
    const marketTicker = ref({}); const marketEnvText = ref(''); const sectorRotation = ref([]); const signalsHistory = ref([]);

    const previewReminders = computed(() => {
      const kw = { '止损':'严格执行止损','追高':'避免追高','仓位':'注意仓位' };
      const r = [];
      for (const [k,v] of Object.entries(kw)) { if (newDiary.value.includes(k)) r.push(v); }
      return r.length ? r.slice(0,3) : ['认真复盘'];
    });

    function drawDoghouse() {
      const canvas = document.querySelector('.doghouse canvas');
      if (!canvas) return;
      const ctx = canvas.getContext('2d'); ctx.clearRect(0,0,80,64); const p=8;
      ctx.fillStyle='#6b4030'; ctx.fillRect(p,p*3,p*8,p*5);
      ctx.fillStyle='#8b5a3c'; ctx.fillRect(p,p*2,p*8,p*1.5);
      ctx.fillStyle='#4a2010'; ctx.beginPath(); ctx.moveTo(p,p*2); ctx.lineTo(p*5,0); ctx.lineTo(p*9,p*2); ctx.fill();
      ctx.fillStyle='#2a0a00'; ctx.fillRect(p*3,p*4.5,p*4,p*3);
      if(dogSleeping.value) {
        ctx.fillStyle='#f0c880'; ctx.fillRect(p*4,p*5,p*2,p*2);
        ctx.fillStyle='#1a0a00'; ctx.fillRect(p*4.5,p*5.3,4,4); ctx.fillRect(p*5.5,p*5.3,4,4);
      }
    }

    function toggleDog() { dogSleeping.value=!dogSleeping.value; petMessage.value=dogSleeping.value?'zzZ... 回窝睡觉了':'汪汪！出来玩~'; if(!dogSleeping.value){petX.value=window.innerWidth-120; petY.value=window.innerHeight-100;} drawDoghouse(); }

    function drawPet() {
      const canvas = document.querySelector('.pet-container canvas');
      if(!canvas || dogSleeping.value) return;
      const ctx = canvas.getContext('2d'); ctx.clearRect(0,0,64,64); const p=8;
      ctx.fillStyle='#d4a050'; ctx.fillRect(p*2,p*3,p*4,p*3);
      ctx.fillStyle='#f0d8a0'; ctx.fillRect(p*3,p*4,p*2,p*2);
      ctx.fillStyle='#d4a050'; ctx.fillRect(p*1,p*0.5,p*5,p*3);
      ctx.fillStyle='#b87830'; ctx.fillRect(0,0,p*2,p*2.5); ctx.fillRect(p*6,0,p*2,p*2.5);
      ctx.fillStyle='#e8b860'; ctx.fillRect(p*0.5,0,p,p*1.5); ctx.fillRect(p*6.5,0,p,p*1.5);
      const blink = Math.floor(petAnimFrame.value/15)%6===0;
      ctx.fillStyle='#1a0a00';
      if(!blink){ctx.fillRect(p*2,p*1.5,p,p*1.5); ctx.fillRect(p*5,p*1.5,p,p*1.5);}
      ctx.fillStyle='#fff';
      if(!blink){ctx.fillRect(p*2.3,p*1.5,4,4); ctx.fillRect(p*5.3,p*1.5,4,4);}
      ctx.fillStyle='#2a0a00'; ctx.fillRect(p*3.5,p*2.5,p,p*0.7);
      ctx.fillStyle='#6a3010'; ctx.fillRect(p*3,p*3.2,p*2,p*0.4);
      const wag = petWagging.value ? Math.sin(petAnimFrame.value*0.6)*4 : 0;
      ctx.fillStyle='#d4a050'; ctx.fillRect(p*5.5,p*1.5+wag,p,p*2);
      ctx.fillStyle='#c09040'; ctx.fillRect(p*2.2,p*6,p,p*1.5); ctx.fillRect(p*4.8,p*6,p,p*1.5);
      ctx.fillStyle='#e05030'; ctx.fillRect(p*1.5,p*3,p*5,p*0.5);
    }

    function updatePet() {
      if(dogSleeping.value || dragging.value) return;
      const dx=mouseX.value-petX.value-32, dy=mouseY.value-petY.value-32;
      const dist=Math.sqrt(dx*dx+dy*dy);
      if(dist>3){petX.value+=dx*0.08; petY.value+=dy*0.08; petWagging.value=true;}
      else petWagging.value=false;
      petX.value=Math.max(10,Math.min(window.innerWidth-80,petX.value));
      petY.value=Math.max(80,Math.min(window.innerHeight-80,petY.value));
      const houseLeft=20,houseRight=100,houseTop=window.innerHeight-120,houseBottom=window.innerHeight;
      const petRight=petX.value+64, petBottom=petY.value+64;
      if(petRight>houseLeft && petX.value<houseRight && petBottom>houseTop && petY.value<houseBottom){
        petX.value=houseRight+10; petY.value=Math.max(houseTop-80,80);
      }
    }

    function startDrag(e) { if(dogSleeping.value) return; e.preventDefault(); dragging.value=true; dragStartX.value=e.clientX; dragStartY.value=e.clientY; petStartX.value=petX.value; petStartY.value=petY.value; window.addEventListener('mousemove',onDrag); window.addEventListener('mouseup',stopDrag); }
    function onDrag(e) { if(!dragging.value) return; petX.value=petStartX.value+(e.clientX-dragStartX.value); petY.value=petStartY.value+(e.clientY-dragStartY.value); }
    function stopDrag() { dragging.value=false; window.removeEventListener('mousemove',onDrag); window.removeEventListener('mouseup',stopDrag); }
    function onMouseMove(e) { mouseX.value=e.clientX; mouseY.value=e.clientY; }

    async function executeScan() {
      loading.value=true; fetched.value=true; stocks.value=[];
      try{ const r=await fetch('/api/analyze',{method:'POST'}); const d=await r.json(); status.value=d.status||''; message.value=d.message||''; stocks.value=d.stocks||[]; total.value=d.total||0; cached.value=!!d.cached;
        const high = (d.stocks||[]).filter(s => s.signal >= 75);
        if (high.length) { notifyHighSignal(high[0]); for (let i=1;i<high.length&&i<3;i++) setTimeout(()=>notifyHighSignal(high[i]), i*3000); }
      }catch(e){} finally{loading.value=false;}
    }
    async function loadAIBrief() { try{ const r=await fetch('/api/ai/brief'); const d=await r.json(); aiBrief.value=d.brief||''; }catch(e){} }
    async function checkSell() {
      if (!sellCode.value) return;
      try {
        const r = await fetch('/api/sell_check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: sellCode.value, buy_price: parseFloat(sellPrice.value) || 0 }) });
        sellResult.value = await r.json();
      } catch(e) { sellResult.value = { error: '请求失败' }; }
    }
    async function buyStock(code,price) { try{ await fetch('/api/position/buy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,price})}); loadStats(); }catch(e){} }
    async function quickSell(code) { const price=prompt('卖出价格:'); if(!price)return; try{ await fetch('/api/position/sell',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,price:parseFloat(price)})}); loadStats(); }catch(e){} }
    async function loadStats() { try{ const r=await fetch('/api/stats'); stats.value=await r.json(); }catch(e){} try{ const r=await fetch('/api/trades'); trades.value=await r.json(); }catch(e){} }
    async function loadDiary() { try{ const r=await fetch('/api/diary'); diaryList.value=await r.json(); }catch(e){} }
    async function saveDiary() { if(!newDiary.value.trim())return; try{ await fetch('/api/diary',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:newDiary.value})}); newDiary.value=''; loadDiary(); }catch(e){} }
    async function deleteDiary(id) { try{ await fetch(`/api/diary/${id}`,{method:'DELETE'}); loadDiary(); }catch(e){} }
    async function fetchHeatmap() { try{ const r=await fetch('/api/heatmap'); const d=await r.json(); heatmapData.value=d.data||[]; }catch(e){} }
    async function fetchMoney() { try{ const r=await fetch('/api/moneyflow'); const d=await r.json(); moneyflowDate.value=d.date||''; moneyIn.value=d.top_inflow||[]; moneyOut.value=d.top_outflow||[]; }catch(e){} }
    async function fetchMarketEnv() { try{ const r=await fetch('/api/market_env'); const d=await r.json(); marketEnvText.value=`${d.environment} ${d.advice}`; }catch(e){} }
    async function fetchSectorRotation() { try{ const r=await fetch('/api/sector_rotation'); const d=await r.json(); sectorRotation.value=d.top_inflow||[]; }catch(e){} }
    async function fetchSignalsHistory() { try{ const r=await fetch('/api/signals_log'); signalsHistory.value=await r.json(); }catch(e){} }
    async function fetchMarketTicker() { try{ const r=await fetch('/api/market_ticker'); const d=await r.json(); if(!d.error) marketTicker.value=d; }catch(e){} }
    async function evolveFactors() {
      if (evolving.value) return;
      evolving.value = true;
      evolveResult.value = null;
      try {
        const r = await fetch('/api/ai/evolve_factors', { method: 'POST' });
        evolveResult.value = await r.json();
      } catch(e) {
        evolveResult.value = { error: '进化失败' };
      } finally {
        evolving.value = false;
      }
    }

    async function loadFactorStatus() {
      try {
        const r = await fetch('/api/ai/factor_status');
        factorStatus.value = await r.json();
      } catch(e) {}
    }
    async function showKline(code) {
      klineVisible.value = true; klineCode.value = code; klineLoading.value = true; klineImage.value = ''; klineError.value = '';
      try {
        const r = await fetch(`/api/kline/${code}`);
        const d = await r.json();
        if (d.error) { klineError.value = d.error; }
        else { klineImage.value = d.image; }
      } catch(e) { klineError.value = 'K线图加载失败'; }
      finally { klineLoading.value = false; }
    }
    async function loadStrategyComparison() {
      try {
        const r = await fetch('/api/strategy_comparison');
        strategyComparison.value = await r.json();
      } catch(e) {}
    }
    async function loadStrategyCombo() {
      try {
        const r = await fetch('/api/strategy_combo');
        strategyCombo.value = await r.json();
      } catch(e) {}
    }
    async function loadFactorRegistry() {
      try {
        const r = await fetch('/api/factor_registry');
        const d = await r.json();
        factorRegistry.value = d.factors || [];
      } catch(e) {}
    }
    async function loadFactorRank() {
      try {
        const r = await fetch('/api/factor_rank');
        const d = await r.json();
        factorRankList.value = d.factors || [];
        factorRegistry.value = d.factors || [];
      } catch(e) {}
    }
    async function backtestFactor(name) {
      if (factorBacktesting.value) return;
      factorBacktesting.value = true; factorBacktestResult.value = null;
      try {
        const r = await fetch('/api/factor_backtest', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ factor_name: name, days: 60 }) });
        factorBacktestResult.value = await r.json();
      } catch(e) { factorBacktestResult.value = { error: '请求失败' }; }
      finally { factorBacktesting.value = false; }
    }
    async function toggleFactor(name, active) {
      try {
        await fetch('/api/factor_registry', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ factor_name: name, active }) });
        loadFactorRegistry();
      } catch(e) {}
    }
    async function showFactorDetail(stock) {
      factorDetailVisible.value = true; factorDetailCode.value = stock.code || stock.name;
      factorDetailLoading.value = true; factorDetailSignal.value = stock.signal || 0;
      factorDetailReason.value = stock.priority_reason || '';
      try {
        const r = await fetch(`/api/factor_registry`);
        const d = await r.json();
        const registry = d.factors || [];
        const scores = stock.strategy_scores || {};
        const result = [];
        for (const f of registry) {
          const contrib = scores[f.name] ? scores[f.name] * (f.weight || 1) : 0;
          result.push({ name: f.name, category: f.category || '未知', weight: f.weight || 1, ic_30d: f.ic_30d || 0, contribution: Math.round(contrib) });
        }
        result.sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
        factorDetailData.value = result;
      } catch(e) { factorDetailData.value = []; }
      finally { factorDetailLoading.value = false; }
    }
    async function loadIndicatorCycle() {
      try {
        const r = await fetch('/api/indicator_cycle');
        const d = await r.json();
        indicatorCycleList.value = d.factors || [];
      } catch(e) {}
    }
    async function loadMarketMatch() {
      try {
        const r = await fetch('/api/market_indicator_match');
        marketMatch.value = await r.json();
      } catch(e) {}
    }
    async function runLongTermEval(years) {
      longTermLoading.value = true; longTermResult.value = null;
      try {
        const r = await fetch('/api/long_term_eval', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ years }) });
        longTermResult.value = await r.json();
      } catch(e) { longTermResult.value = { error: '请求失败' }; }
      finally { longTermLoading.value = false; }
    }
    async function runBacktest() {
      if(backtesting.value) return;
      backtesting.value=true; backtestResult.value=null;
      try{
        const r=await fetch('/api/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({days:parseInt(backtestDays.value)||30,top_n:parseInt(backtestTopN.value)||3,mode:backtestMode.value})});
        backtestResult.value=await r.json();
      }catch(e){ backtestResult.value={error:'请求失败'}; } finally{ backtesting.value=false; }
    }
    async function updateWeights() { try{ const r=await fetch('/api/update_weights',{method:'POST'}); weightsResult.value=await r.json(); }catch(e){} }
    async function checkDataQuality() { try{ const r=await fetch('/api/data_quality'); dataQuality.value=await r.json(); }catch(e){} }
    async function getDockerfile() { try{ const r=await fetch('/api/dockerfile'); const d=await r.json(); dockerfile.value=d.dockerfile; }catch(e){} }
    async function gitUpdate() { try{ const r=await fetch('/api/update',{method:'POST'}); const d=await r.json(); updateResult.value=d.output||d.error; }catch(e){} }
    function toggleAutoScan() {
      autoScanEnabled.value = !autoScanEnabled.value;
      if (autoScanEnabled.value) {
        if ('Notification' in window && Notification.permission === 'default') {
          Notification.requestPermission();
        }
        scanTimer.value = setInterval(() => {
          const now = new Date();
          if (now.getHours() === 14 && now.getMinutes() >= 30 && now.getMinutes() <= 36 && !loading.value) {
            executeScan();
          }
        }, 60000);
      } else {
        if (scanTimer.value) { clearInterval(scanTimer.value); scanTimer.value = null; }
      }
    }
    function notifyHighSignal(s) {
      if (!('Notification' in window) || Notification.permission !== 'granted') return;
      try { new Notification('橘子量化 买入信号', { body: s.name + ' ' + s.code + ' 评分' + s.signal + '分', icon: '/static/icon-192.png', tag: 'buy-signal', requireInteraction: true }); } catch(e) {}
    }
    function heatClass(v){ const n=Number(v); if(n>2)return'text-green';if(n>0)return'text-sub';if(n>-2)return'text-red';return'text-red'; }
    function fv(v){ return v?(Number(v)>=1e8?(Number(v)/1e8).toFixed(2)+'亿':(Number(v)/1e4).toFixed(2)+'万'):'--'; }
    function updateClock(){ const n=new Date(); currentTime.value=n.toLocaleTimeString('zh-CN',{hour12:false}); currentDate.value=n.toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit',weekday:'short'}); }
    function renderECharts(elId, option) {
      const el = document.getElementById(elId);
      if (!el) return;
      const chart = echarts.getInstanceByDom(el) || echarts.init(el);
      chart.setOption(option, true);
      chart.resize();
    }
    async function loadEquityCurve() {
      try {
        const r = await fetch('/api/equity_curve');
        const d = await r.json();
        if (!d.curve || !d.curve.length) return;
        equityCurveData.value = d.curve;
        monthlyPnlData.value = d.monthly || [];
        renderECharts('equityChart', {
          tooltip: { trigger: 'axis' },
          grid: { left: '8%', right: '5%', top: '5%', bottom: '8%' },
          xAxis: { type: 'category', data: d.curve.map(x => x.date), axisLabel: { color: '#999', fontSize: 10 } },
          yAxis: { type: 'value', axisLabel: { color: '#999' }, splitLine: { lineStyle: { color: '#1a1a1a' } } },
          series: [{ type: 'line', data: d.curve.map(x => x.equity),
            lineStyle: { color: '#e8a020', width: 2 },
            areaStyle: { color: 'rgba(232,160,32,0.08)' }, smooth: true }]
        });
        renderECharts('drawdownChart', {
          tooltip: { trigger: 'axis' },
          grid: { left: '8%', right: '5%', top: '5%', bottom: '8%' },
          xAxis: { type: 'category', data: d.drawdown.map(x => x.date), axisLabel: { color: '#999', fontSize: 10 } },
          yAxis: { type: 'value', axisLabel: { color: '#999' }, splitLine: { lineStyle: { color: '#1a1a1a' } } },
          series: [{ type: 'line', data: d.drawdown.map(x => x.drawdown),
            lineStyle: { color: '#d94a5d', width: 1.5 },
            areaStyle: { color: 'rgba(217,74,93,0.15)' }, smooth: true }]
        });
        renderECharts('monthlyChart', {
          tooltip: { trigger: 'axis' },
          grid: { left: '8%', right: '5%', top: '5%', bottom: '8%' },
          xAxis: { type: 'category', data: d.monthly.map(x => x.month), axisLabel: { color: '#999', fontSize: 10, rotate: 30 } },
          yAxis: { type: 'value', axisLabel: { color: '#999' }, splitLine: { lineStyle: { color: '#1a1a1a' } } },
          series: [{ type: 'bar', data: d.monthly.map(x => x.pnl),
            itemStyle: { color: function(p) { return p.value >= 0 ? '#3aaf7c' : '#d94a5d'; } } }]
        });
      } catch(e) {}
    }

    onMounted(async () => {
      updateClock(); setInterval(updateClock,1000);
      drawDoghouse(); drawPet();
      setInterval(()=>{petAnimFrame.value++; drawPet();},30);
      requestAnimationFrame(function anim(){ updatePet(); requestAnimationFrame(anim); });
      window.addEventListener('mousemove',onMouseMove);
      await fetchMarketTicker(); await executeScan(); await loadAIBrief(); await fetchHeatmap(); await fetchMoney(); await loadStats(); await loadEquityCurve();
      try{ const r=await fetch('/api/yiming'); yiming.value=await r.json(); }catch(e){}
      try{ const r=await fetch('/api/news'); newsList.value=(await r.json()).map(n=>({...n,expanded:false})); }catch(e){}
      try{ const r=await fetch('/api/pet_reminder'); petMessage.value=(await r.json()).text; }catch(e){}
      setInterval(()=>{ fetch('/api/pet_reminder').then(r=>r.json()).then(d=>petMessage.value=d.text); },30000);
      setInterval(fetchMarketTicker, 30000);
    });

    return {
      tab, loading, fetched, cached, status, message, stocks, total, evolving,
      evolveResult, factorStatus, evolveFactors, loadFactorStatus,
      yiming, petMessage, petX, petY, dogSleeping, aiBrief,
      sellCode, sellPrice, sellResult, newDiary, diaryList, stats, trades,
      newsList, heatmapData, moneyIn, moneyOut, moneyflowDate, currentTime, currentDate,
      previewReminders, autoScanEnabled,
      executeScan, loadAIBrief, checkSell, buyStock, quickSell,
      loadStats, loadDiary, saveDiary, deleteDiary,
      toggleDog, startDrag, heatClass, fv,
      backtestDays, backtestTopN, backtestMode, backtestResult, backtesting, runBacktest,
      weightsResult, updateWeights, dataQuality, checkDataQuality,
      dockerfile, getDockerfile, updateResult, gitUpdate,
      marketEnvText, fetchMarketEnv, sectorRotation, fetchSectorRotation,
      signalsHistory, fetchSignalsHistory, toggleAutoScan, fetchMarketTicker, marketTicker,
      klineVisible, klineCode, klineImage, klineLoading, klineError, showKline,
      strategyComparison, loadStrategyComparison, strategyCombo, loadStrategyCombo,
      factorRegistry, factorRankList, loadFactorRegistry, loadFactorRank,
      factorBacktestResult, factorBacktesting, backtestFactor, toggleFactor,
      factorDetailVisible, factorDetailCode, factorDetailData, factorDetailSignal,
      factorDetailReason, factorDetailLoading, showFactorDetail,
      indicatorCycleList, loadIndicatorCycle, marketMatch, loadMarketMatch,
      longTermResult, longTermLoading, runLongTermEval,
      equityCurveData, monthlyPnlData, scanTimer, loadEquityCurve, notifyHighSignal
    };
  }
}).mount('#app');
