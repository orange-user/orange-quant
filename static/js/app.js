const { createApp, ref, computed, watch, onMounted, onUnmounted } = Vue;
createApp({
  delimiters: ['[[', ']]'],
  setup() {
    // Toast system (GSAP animated)
    function showToast(msg, type='success', duration=2500) {
      const container = document.getElementById('toastContainer');
      if (!container) return;
      const el = document.createElement('div');
      el.className = 'toast' + (type === 'error' ? ' toast-error' : type === 'warning' ? ' toast-warning' : ' toast-success');
      el.textContent = msg;
      container.appendChild(el);
      if (typeof gsap !== 'undefined') {
        gsap.from(el, { y: -20, opacity: 0, duration: 0.25, ease: 'power2.out' });
        gsap.to(el, { opacity: 0, duration: 0.3, delay: duration/1000 - 0.3, ease: 'power2.in',
          onComplete: () => el.remove() });
      } else {
        setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.3s'; setTimeout(() => el.remove(), 300); }, duration);
      }
    }

    // GSAP animation utilities
    function gsapAnim(fn) { if (typeof gsap !== 'undefined') fn(); }

    function animateHeader() {
      gsapAnim(() => {
        gsap.from('.site-header', { y: -30, opacity: 0, duration: 0.5, ease: 'power2.out' });
        gsap.from('.tab-bar .tab', { y: -10, opacity: 0, duration: 0.4, stagger: 0.08, ease: 'back.out(1.5)', delay: 0.2 });
      });
    }

    function animateStockCards() {
      gsapAnim(() => {
        gsap.from('.stock-result-card', { x: -20, opacity: 0, duration: 0.35, stagger: 0.035, ease: 'power2.out' });
      });
    }

    function animateSignalScores() {
      gsapAnim(() => {
        document.querySelectorAll('.signal-score').forEach(el => {
          const final = parseInt(el.textContent);
          if (!isNaN(final)) {
            el.textContent = '0';
            gsap.to({ v: 0 }, { v: final, duration: 0.8, ease: 'power2.out',
              onUpdate: function() { el.textContent = Math.round(this.targets()[0].v); }
            });
          }
        });
      });
    }

    function animateChart(elId) {
      gsapAnim(() => {
        const el = document.getElementById(elId);
        if (el) gsap.from(el, { opacity: 0, scaleY: 0.85, duration: 0.5, ease: 'back.out(1.2)', transformOrigin: 'bottom center' });
      });
    }

    function animateStatNumbers() {
      gsapAnim(() => {
        document.querySelectorAll('.stat-num').forEach(el => {
          const raw = el.textContent.replace(/[^0-9.\-]/g, '');
          const final = parseFloat(raw) || 0;
          const isPct = el.textContent.includes('%');
          const prefix = el.textContent.startsWith('+') ? '+' : '';
          if (!final) return;
          el.textContent = '0' + (isPct ? '%' : '');
          gsap.to({ v: 0 }, {
            v: final, duration: 1.0, ease: 'power2.out',
            onUpdate: function() {
              const v = this.targets()[0].v;
              el.textContent = prefix + (Number.isInteger(v) ? Math.round(v) : v.toFixed(1)) + (isPct ? '%' : '');
            }
          });
        });
      });
    }

    // Fetch with timeout
    function apiFetch(url, options = {}, timeout = 20000) {
      const controller = new AbortController();
      const id = setTimeout(() => controller.abort(), timeout);
      return fetch(url, { ...options, signal: controller.signal }).finally(() => clearTimeout(id));
    }

    // Core state
    const tab = ref('scan');
    const loading = ref(false); const fetched = ref(false);
    const status = ref(''); const message = ref(''); const cached = ref(false);
    const stocks = ref([]); const total = ref(0);
    const scanProgress = ref(''); const prescreenCount = ref(0);
    const progress = ref({pct:0, stage:'', msg:''});

    // Sell check
    const sellCode = ref(''); const sellPrice = ref(''); const sellResult = ref(null);

    // Positions
    const stats = ref({total_trades:0,win_rate:0,total_profit:0,floating_pnl:0,positions:[]});
    const trades = ref([]);

    // Diary
    const newDiary = ref(''); const diaryList = ref([]);

    // Market data
    const marketTicker = ref({});
    const heatmapData = ref([]); const moneyIn = ref([]); const moneyOut = ref([]); const moneyflowDate = ref('');
    const newsList = ref([]); const newsDigest = ref('');
    const currentTime = ref(''); const currentDate = ref('');
    const yiming = ref({id:1,text:'加载中...',translation:'',summary:''});

    // Auto scan
    const autoScanEnabled = ref(false); const scanTimer = ref(null);

    // Charts
    const equityCurveData = ref([]); const monthlyPnlData = ref([]);

    // Backtest
    const backtestDays = ref('60'); const backtestTopN = ref('3');
    const backtestMode = ref('simple'); const backtestResult = ref(null); const backtesting = ref(false);

    // Factor evolution
    const evolving = ref(false); const evolveResult = ref(null);

    // Factor learning
    const factorLearning = ref({factors:[], total_trades:0, last_update:''});
    const factorLearningOpen = ref(false);

    // Server management
    const weightsResult = ref(null); const dataQuality = ref(null); const updateResult = ref(''); const wudaoStatus = ref(null);

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

    let pollTimer = null;

    async function executeScan() {
      loading.value = true; fetched.value = false; scanProgress.value = '启动扫描...'; progress.value = {pct:0, stage:'', msg:''};
      try {
        // 提交扫描任务
        const r = await fetch('/api/analyze', {method:'POST'});
        const d = await r.json();
        status.value = d.status || '';

        if (d.status === 'scanning') {
          // 异步扫描模式：轮询等待结果
          pollForResults();
        } else if (d.status === 'ok') {
          // 缓存命中：直接显示
          handleScanResult(d);
        } else if (d.status === 'non_trading') {
          message.value = d.message || '非交易日';
          loading.value = false;
          fetched.value = true;
        } else {
          message.value = d.message || '未知状态';
          loading.value = false;
          fetched.value = true;
        }
      } catch(e) {
        message.value = '扫描请求失败，请重试';
        loading.value = false;
        fetched.value = true;
      }
    }

    function pollForResults() {
      scanProgress.value = '扫描中...约60秒';
      let attempts = 0;
      const maxAttempts = 120; // 最多轮询2分钟

      function poll() {
        if (attempts >= maxAttempts) {
          loading.value = false;
          fetched.value = true;
          scanProgress.value = '';
          message.value = '扫描超时，请重试';
          return;
        }
        attempts++;
        fetch('/api/scan_status')
          .then(r => r.json())
          .then(d => {
            if (d.status === 'ok') {
              progress.value = {pct:0, stage:'', msg:''};
              handleScanResult(d);
            } else if (d.status === 'scanning') {
              if (d.progress) progress.value = d.progress;
              scanProgress.value = '扫描中...已' + (attempts * 2) + '秒';
              pollTimer = setTimeout(poll, 2000);
            } else if (d.status === 'error') {
              progress.value = {pct:0, stage:'', msg:''};
              message.value = d.message || '扫描出错';
              loading.value = false;
              fetched.value = true;
              scanProgress.value = '';
            } else {
              pollTimer = setTimeout(poll, 2000);
            }
          })
          .catch(() => {
            pollTimer = setTimeout(poll, 3000);
          });
      }
      poll();
    }

    function handleScanResult(d) {
      loading.value = false;
      fetched.value = true;
      scanProgress.value = '';
      message.value = d.message || '';
      stocks.value = d.stocks || [];
      total.value = d.total || 0;
      cached.value = !!d.cached;
      if (d.stocks && d.stocks.length) {
        setTimeout(() => {
          renderSignalDist();
          animateChart('signalDistChart');
          setTimeout(animateStockCards, 50);
          setTimeout(animateSignalScores, 150);
        }, 100);
      }
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

    const buying = ref(false); const selling = ref('');

    async function buyStock(code, price) {
      if (buying.value) return;
      buying.value = true;
      try {
        await apiFetch('/api/position/buy', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({code, price})});
        loadStats();
        showToast(`买入 ${code} 成功`);
      } catch(e) { showToast(`买入失败: ${e.message || '网络错误'}`, 'error'); }
      finally { buying.value = false; }
    }

    async function quickSell(code) {
      if (selling.value) return;
      const price = prompt('卖出价格:');
      if (!price) return;
      selling.value = code;
      try {
        await apiFetch('/api/position/sell', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({code, price:parseFloat(price)})});
        loadStats();
        showToast(`卖出 ${code} 成功`);
      } catch(e) { showToast(`卖出失败: ${e.message || '网络错误'}`, 'error'); }
      finally { selling.value = ''; }
    }

    async function deletePosition(code) {
      if (!confirm(`确定删除 ${code} 持仓？不会产生交易记录。`)) return;
      try {
        await apiFetch(`/api/position/${code}`, {method:'DELETE'});
        loadStats();
        showToast(`已删除 ${code}`);
      } catch(e) { showToast(`删除失败`, 'error'); }
    }

    async function loadStats() {
      try { const r = await fetch('/api/stats'); stats.value = await r.json(); } catch(e) {}
      try { const r = await fetch('/api/trades'); trades.value = await r.json(); } catch(e) {}
      setTimeout(animateStatNumbers, 200);
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
      try {
        const r = await fetch('/api/heatmap'); const d = await r.json();
        heatmapData.value = d.data || [];
        if (d.data && d.data.length) renderHeatmapChart(d.data);
      } catch(e) {}
    }

    async function fetchMoney() {
      try {
        const r = await fetch('/api/moneyflow'); const d = await r.json();
        moneyflowDate.value = d.date || ''; moneyIn.value = d.top_inflow || [];
        moneyOut.value = d.top_outflow || [];
        if (d.top_inflow && d.top_inflow.length) renderMoneyFlowChart(d.top_inflow, d.top_outflow || []);
      } catch(e) {}
    }

    async function fetchMarketTicker() {
      try { const r = await fetch('/api/market_ticker'); const d = await r.json(); if (!d.error) marketTicker.value = d; } catch(e) {}
    }

    function fv(v) {
      if (!v) return '--';
      const n = Number(v);
      const sign = n < 0 ? '-' : '';
      const abs = Math.abs(n);
      return sign + (abs >= 1e8 ? (abs/1e8).toFixed(2)+'亿' : (abs/1e4).toFixed(2)+'万');
    }

    function holdDays(buy, sell) {
      if (!buy || !sell) return '--';
      const b = new Date(buy); const s = new Date(sell);
      return Math.round((s - b) / 86400000);
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

    const _chartRegistry = {};

    function renderECharts(elId, option) {
      const el = document.getElementById(elId);
      if (!el) return;
      if (_chartRegistry[elId]) {
        _chartRegistry[elId].dispose();
      }
      const chart = echarts.init(el);
      _chartRegistry[elId] = chart;
      chart.setOption(option, true);
      chart.resize();
      return chart;
    }

    function disposeCharts() {
      Object.keys(_chartRegistry).forEach(k => {
        try { _chartRegistry[k].dispose(); } catch(e) {}
        delete _chartRegistry[k];
      });
    }

    async function loadEquityCurve() {
      try {
        const r = await fetch('/api/equity_curve'); const d = await r.json();
        if (!d.curve || !d.curve.length) return;
        equityCurveData.value = d.curve; monthlyPnlData.value = d.monthly || [];
        renderECharts('equityChart', {
          tooltip: {trigger:'axis'},
          grid: {left:'8%',right:'5%',top:'5%',bottom:'8%'},
          xAxis: {type:'category',data:d.curve.map(x=>x.date),axisLabel:{color:'#6B7280',fontSize:10}},
          yAxis: {type:'value',axisLabel:{color:'#6B7280'},splitLine:{lineStyle:{color:'#E0E3EB'}}},
          series: [{type:'line',data:d.curve.map(x=>x.equity),
            lineStyle:{color:'#2962FF',width:2},
            areaStyle:{color:'rgba(41,98,255,0.06)'},smooth:true}]
        });
        renderECharts('drawdownChart', {
          tooltip: {trigger:'axis'},
          grid: {left:'8%',right:'5%',top:'5%',bottom:'8%'},
          xAxis: {type:'category',data:d.drawdown.map(x=>x.date),axisLabel:{color:'#6B7280',fontSize:10}},
          yAxis: {type:'value',axisLabel:{color:'#6B7280'},splitLine:{lineStyle:{color:'#E0E3EB'}}},
          series: [{type:'line',data:d.drawdown.map(x=>x.drawdown),
            lineStyle:{color:'#F44336',width:1.5},
            areaStyle:{color:'rgba(244,67,54,0.08)'},smooth:true}]
        });
        renderECharts('monthlyChart', {
          tooltip: {trigger:'axis'},
          grid: {left:'8%',right:'5%',top:'5%',bottom:'8%'},
          xAxis: {type:'category',data:d.monthly.map(x=>x.month),axisLabel:{color:'#6B7280',fontSize:10,rotate:30}},
          yAxis: {type:'value',axisLabel:{color:'#6B7280'},splitLine:{lineStyle:{color:'#E0E3EB'}}},
          series: [{type:'bar',data:d.monthly.map(x=>x.pnl),
            itemStyle:{color:function(p){return p.value>=0?'#F44336':'#26A65B';}}}]
        });
      } catch(e) {}
    }

    // ==================== Signal Distribution Chart ====================

    const signalStats = computed(() => {
      if (!stocks.value.length) return { strong: 0, medium: 0, weak: 0 };
      return {
        strong: stocks.value.filter(s => s.signal >= 75).length,
        medium: stocks.value.filter(s => s.signal >= 55 && s.signal < 75).length,
        weak: stocks.value.filter(s => s.signal < 55).length
      };
    });

    function topStrategies(scores) {
      if (!scores) return [];
      return Object.entries(scores)
        .filter(([_, v]) => v >= 8)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 4);
    }

    function renderSignalDist() {
      const el = document.getElementById('signalDistChart');
      if (!el || !stocks.value.length) return;
      const chart = echarts.getInstanceByDom(el) || echarts.init(el);
      chart.setOption({
        tooltip: { trigger: 'axis' },
        grid: { left: '8%', right: '5%', top: '5%', bottom: '8%' },
        xAxis: { type: 'category', data: stocks.value.map(s => s.code).slice(0, 15), axisLabel: { color: '#6B7280', fontSize: 10 } },
        yAxis: { type: 'value', name: '评分', axisLabel: { color: '#6B7280' }, splitLine: { lineStyle: { color: '#E0E3EB' } } },
        series: [{
          type: 'bar', data: stocks.value.map(s => s.signal).slice(0, 15),
          itemStyle: {
            borderRadius: [4, 4, 0, 0],
            color: function(p) {
              return p.value >= 75 ? '#F44336' : p.value >= 65 ? '#f0b90b' : p.value >= 50 ? '#2962FF' : '#9CA3AF';
            }
          },
          label: { show: true, position: 'top', color: '#1A1A1A', fontSize: 10, fontWeight: 'bold' }
        }]
      }, true);
      chart.resize();
    }

    // ==================== Heatmap Chart ====================

    function renderHeatmapChart(data) {
      const el = document.getElementById('heatmapChart');
      if (!el || !data.length) return;
      const chart = echarts.getInstanceByDom(el) || echarts.init(el);

      // Color mapping: red (up) to green (down)
      const sorted = [...data].sort((a, b) => b['涨跌幅'] - a['涨跌幅']).slice(0, 20);

      chart.setOption({
        tooltip: {
          formatter: function(p) {
            const item = sorted[p.dataIndex];
            return item['板块名称'] + '<br/>涨跌幅: ' + (item['涨跌幅'] > 0 ? '+' : '') + item['涨跌幅'].toFixed(2) + '%';
          }
        },
        grid: { left: '4%', right: '8%', top: '2%', bottom: '2%' },
        xAxis: { type: 'category', data: sorted.map(s => s['板块名称']), axisLabel: { fontSize: 9, color: '#6B7280', rotate: 0, interval: 0 }, axisTick: { show: false } },
        yAxis: { type: 'value', name: '%', axisLabel: { color: '#6B7280', fontSize: 9 }, splitLine: { lineStyle: { color: '#E4E7EB' } } },
        series: [{
          type: 'bar',
          data: sorted.map(s => parseFloat(s['涨跌幅'].toFixed(2))),
          itemStyle: {
            borderRadius: [3, 3, 0, 0],
            color: function(p) { return p.value >= 0 ? '#F44336' : '#26A65B'; }
          },
          label: { show: false }
        }]
      }, true);
      chart.resize();
    }

    // ==================== Money Flow Chart ====================

    function renderMoneyFlowChart(topIn, topOut) {
      const el = document.getElementById('moneyFlowChart');
      if (!el) return;

      const all = [
        ...topIn.slice(0, 5).map(m => ({ name: m['板块名称'], value: parseFloat(m['主力净流入']), sign: 1 })),
        ...topOut.slice(0, 5).map(m => ({ name: m['板块名称'], value: parseFloat(m['主力净流入']), sign: -1 }))
      ].sort((a, b) => a.value - b.value);

      const chart = echarts.getInstanceByDom(el) || echarts.init(el);
      chart.setOption({
        tooltip: {
          formatter: function(p) {
            const item = all[p.dataIndex];
            const abs = Math.abs(item.value);
            const val = abs >= 1e8 ? (abs / 1e8).toFixed(2) + '亿' : (abs / 1e4).toFixed(2) + '万';
            return item.name + '<br/>主力净' + (item.sign > 0 ? '流入' : '流出') + ': ' + val;
          }
        },
        grid: { left: '4%', right: '8%', top: '2%', bottom: '2%' },
        xAxis: { type: 'value', axisLabel: { color: '#6B7280', fontSize: 9, formatter: v => { const abs = Math.abs(v); return (v >= 0 ? '' : '-') + (abs >= 1e8 ? (abs / 1e8).toFixed(1) + '亿' : (abs / 1e4).toFixed(0) + '万'); } }, splitLine: { lineStyle: { color: '#E4E7EB' } } },
        yAxis: { type: 'category', data: all.map(m => m.name), axisLabel: { fontSize: 9, color: '#6B7280' }, axisTick: { show: false } },
        series: [{
          type: 'bar',
          data: all.map(m => m.value),
          itemStyle: {
            borderRadius: [0, 3, 3, 0],
            color: function(p) { return p.value >= 0 ? '#F44336' : '#26A65B'; }
          },
          label: { show: false }
        }]
      }, true);
      chart.resize();
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

    async function updateWeights() {
      try { const r = await fetch('/api/update_weights', {method:'POST'}); weightsResult.value = await r.json(); } catch(e) {}
    }
    async function checkDataQuality() {
      try { const r = await fetch('/api/data_quality'); dataQuality.value = await r.json(); } catch(e) {}
    }
    async function checkWudaoStatus() {
      try { const r = await fetch('/api/wudao_status'); wudaoStatus.value = await r.json(); } catch(e) {}
    }
    async function gitUpdate() {
      try { const r = await fetch('/api/update', {method:'POST'}); const d = await r.json(); updateResult.value = d.output || d.error; } catch(e) {}
    }

    async function loadFactorLearning() {
      try { const r = await fetch('/api/factor_learning'); factorLearning.value = await r.json(); } catch(e) {}
    }

    // ==================== Mount ====================

    // 页面秒开：所有数据异步加载，互不阻塞
    function fireAndForget(fn) { fn().catch(() => {}); }

    onMounted(() => {
      updateClock(); setInterval(updateClock, 1000);

      // Window resize → resize all charts
      window.addEventListener('resize', () => {
        Object.values(_chartRegistry).forEach(c => { try { c.resize(); } catch(e) {} });
      });

      // Network detection
      window.addEventListener('offline', () => showToast('网络已断开，数据可能不是最新的', 'warning', 4000));
      window.addEventListener('online', () => showToast('网络已恢复', 'success', 2000));

      // Page entrance animation
      animateHeader();

      // 第一梯队：轻量级，并发加载
      checkWudaoStatus(); setInterval(checkWudaoStatus, 300000); // 每5分钟刷新悟道状态
      fetchMarketTicker();
      loadStats();
      loadFactorLearning();
      fireAndForget(async () => {
        try { const r = await fetch('/api/yiming'); yiming.value = await r.json(); } catch(e) {}
        try {
        const r = await fetch('/api/news');
        const d = await r.json();
        newsDigest.value = d.ai_digest || '';
        newsList.value = (d.items || []).map(n => ({...n, expanded: false}));
      } catch(e) {}
      });

      // 第二梯队：重量级，延后加载不阻塞UI
      setTimeout(() => { executeScan(); }, 300);
      setTimeout(() => { fetchHeatmap(); fetchMoney(); }, 600);
      setTimeout(() => { loadEquityCurve(); }, 1000);

      setInterval(fetchMarketTicker, 30000);
      setInterval(fetchHeatmap, 120000);
      setInterval(fetchMoney, 120000);

      // Tab switch animation
      watch(tab, () => {
        gsapAnim(() => {
          setTimeout(() => {
            const active = document.querySelector('.page > div[style*="display: block"], .page > div:not([style*="display: none"])');
            if (active) gsap.from(active, { opacity: 0, y: 8, duration: 0.2, ease: 'power1.out' });
          }, 20);
        });
      });
    });

    // 复制TOP3到剪贴板（用于发知识星球）
    function copyTop3() {
      const list = stocks.value;
      if (!list || !list.length) { showToast('暂无信号数据', 'warning'); return; }
      const top3 = list.slice(0, 3);
      const date = new Date().toLocaleDateString('zh-CN');
      let text = `📊 橘子量化 · 尾盘信号 ${date}\n`;
      text += `━━━━━━━━━━━━━━━━━━\n\n`;
      top3.forEach((s, i) => {
        const arrow = s.change_pct >= 0 ? '📈' : '📉';
        text += `【TOP${i+1}】${s.code} ${s.name} ${arrow}\n`;
        text += `  现价: ${s.price}元  |  涨跌幅: ${s.change_pct >= 0 ? '+' : ''}${s.change_pct}%\n`;
        text += `  信号分: ${s.signal}  |  RSI: ${s.rsi}  |  量比: ${s.volume_ratio}\n`;
        if (s.priority_reason) text += `  逻辑: ${s.priority_reason}\n`;
        text += `  策略: ${s.signal >= 70 ? '强烈买入' : s.signal >= 55 ? '建议买入' : '观察'}\n`;
        if (s.kelly_pct) text += `  仓位: Kelly ${s.kelly_pct}% (${s.kelly_shares || '?'}手)\n`;
        text += `\n`;
      });
      text += `━━━━━━━━━━━━━━━━━━\n`;
      text += `⚠️ 量化信号仅供参考，投资有风险\n`;
      text += `更多分析：登录网站查看\n`;
      navigator.clipboard.writeText(text).then(() => {
        showToast('✅ TOP3已复制，去粘贴到知识星球吧！', 'success', 3000);
      }).catch(() => {
        const ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta);
        showToast('✅ TOP3已复制', 'success', 2000);
      });
    }

    return {
      tab, loading, fetched, cached, status, message, stocks, total,
      scanProgress, prescreenCount, progress,
      sellCode, sellPrice, sellResult, checkSell,
      stats, trades, loadStats,
      newDiary, diaryList, loadDiary, saveDiary, deleteDiary, previewReminders,
      marketTicker, yiming,
      heatmapData, moneyIn, moneyOut, moneyflowDate,
      newsList, newsDigest, currentTime, currentDate, fv, holdDays,
      autoScanEnabled, scanTimer, toggleAutoScan,
      equityCurveData, monthlyPnlData, loadEquityCurve,
      executeScan, buyStock, quickSell, deletePosition,
      backtestDays, backtestTopN, backtestMode, backtestResult, backtesting, runBacktest,
      evolving, evolveResult, evolveFactors,
      factorDetailVisible, factorDetailCode, factorDetailData, factorDetailSignal,
      factorDetailReason, factorDetailLoading, showFactorDetail,
      factorLearning, factorLearningOpen, loadFactorLearning,
      weightsResult, dataQuality, updateResult, wudaoStatus,
      updateWeights, checkDataQuality, checkWudaoStatus, gitUpdate,
      signalStats, topStrategies, showToast,
      buying, selling, copyTop3,
    };
  }
}).mount('#app');
