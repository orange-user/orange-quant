const { createApp, ref, computed, onMounted, onUnmounted } = Vue;
createApp({
  delimiters: ['[[', ']]'],
  setup() {
    // Toast system
    function showToast(msg, type='success', duration=2500) {
      const container = document.getElementById('toastContainer');
      if (!container) return;
      const el = document.createElement('div');
      el.className = 'toast' + (type === 'error' ? ' toast-error' : type === 'warning' ? ' toast-warning' : ' toast-success');
      el.textContent = msg;
      container.appendChild(el);
      setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity 0.3s'; setTimeout(() => el.remove(), 300); }, duration);
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

    // Pixel pet (小橘子)
    const petMessage = ref('汪汪~');
    const petX = ref(typeof window !== 'undefined' ? window.innerWidth - 100 : 700);
    const petY = ref(typeof window !== 'undefined' ? window.innerHeight - 140 : 400);
    const petSleeping = ref(true);
    const petDragging = ref(false);
    const petAnimFrame = ref(0);
    const petMood = ref('happy'); // happy, excited, curious, sleepy
    const petState = ref('stand'); // stand, sit, stretch
    const petWagging = ref(true);
    const dragStartX = ref(0); const dragStartY = ref(0);
    const petStartX_val = ref(0); const petStartY_val = ref(0);
    let petStateChange = 0; // frame counter for idle state changes

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
    const weightsResult = ref(null); const dataQuality = ref(null); const updateResult = ref('');

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
        setTimeout(() => renderSignalDist(), 100);
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
    async function gitUpdate() {
      try { const r = await fetch('/api/update', {method:'POST'}); const d = await r.json(); updateResult.value = d.output || d.error; } catch(e) {}
    }

    async function loadFactorLearning() {
      try { const r = await fetch('/api/factor_learning'); factorLearning.value = await r.json(); } catch(e) {}
    }

    // ==================== Pixel Pet (小橘子) ====================

    const petMessages = {
      idle: ['今天也要加油哦~', '汪汪！', '盯盘中...', '要有耐心~', '小橘子陪着你', '摸头好舒服~'],
      marketUp: ['行情不错！', '今天吃肉~', '涨了涨了！', '开心~汪!'],
      marketDown: ['稳住心态', '跌了别慌', '机会是跌出来的', '耐心等待'],
      morning: ['早上好！', '新的一天~', '开盘大吉！'],
      afternoon: ['下午加油！', '打起精神~', '收盘前检查一下'],
      evening: ['辛苦了~', '今天复盘了吗？', '记得写复盘'],
      signals: ['有信号！注意看看', '选股机会来了', '扫描结果不错哦'],
      profit: ['赚钱了汪！', '止盈要果断', '落袋为安~'],
      loss: ['设好止损哦', '纪律第一', '错了就认'],
      patted: ['嘿嘿~', '好舒服~', '再摸一下嘛', '汪呜~', '开心！', '嘤嘤嘤~'],
    };

    // Emotion keywords for diary analysis
    const emotionKeywords = {
      焦虑: ['焦虑', '紧张', '不安', '担心', '怕', '慌'],
      贪婪: ['贪婪', '贪心', '追高', '冲动', 'FOMO'],
      恐惧: ['恐惧', '害怕', '恐慌', '大跌', '崩盘'],
      后悔: ['后悔', '早知道', '不应该', '卖飞', '踏空'],
      耐心: ['耐心', '等待', '持有', '坚定', '冷静'],
      自信: ['自信', '成功', '盈利', '赚钱', '开心'],
    };

    function analyzeDiaryEmotion() {
      if (!diaryList.value || !diaryList.value.length) return null;
      const recent = diaryList.value.slice(-5);
      const scores = {};
      for (const entry of recent) {
        const text = entry.text || '';
        for (const [emotion, keywords] of Object.entries(emotionKeywords)) {
          for (const kw of keywords) {
            if (text.includes(kw)) { scores[emotion] = (scores[emotion] || 0) + 1; }
          }
        }
      }
      if (!Object.keys(scores).length) return null;
      const top = Object.entries(scores).sort((a, b) => b[1] - a[1])[0];
      return { emotion: top[0], count: top[1] };
    }

    function getDiaryReminder() {
      const result = analyzeDiaryEmotion();
      if (!result) return null;
      const reminders = {
        焦虑: ['上次你说焦虑了，放松点，按计划执行就好~', '别太紧张，交易是概率游戏', '焦虑的时候少看盘，多休息'],
        贪婪: ['追高容易被套哦，遵守规则', '贪心是交易的大敌，稳一点', '宁可错过，不要做错'],
        恐惧: ['恐惧时别人在贪婪，坚持策略', '大跌往往是最好的买入时机', '别被短期波动吓到'],
        后悔: ['过去的交易已经过去，向前看', '卖飞不可怕，机会总会有', '每一笔交易都是学习'],
        耐心: ['耐心是交易最好的品质，继续保持', '你的耐心会有回报的', '守得住寂寞，等得到花开'],
        自信: ['最近状态不错，继续保持纪律', '自信很好，但别忘了风控', '稳定盈利才是王道'],
      };
      const pool = reminders[result.emotion];
      return pool ? pool[Math.floor(Math.random() * pool.length)] : null;
    }

    function getRandomMsg(category) {
      const pool = petMessages[category] || petMessages.idle;
      return pool[Math.floor(Math.random() * pool.length)];
    }

    function pickPetMessage() {
      // Check diary reminders first (~30% chance)
      if (Math.random() < 0.3) {
        const reminder = getDiaryReminder();
        if (reminder) return reminder;
      }
      const h = new Date().getHours();
      if (h < 9) return getRandomMsg('morning');
      if (h < 12) return Math.random() < 0.3 ? getRandomMsg('morning') : getRandomMsg('idle');
      if (h < 15) return getRandomMsg('afternoon');
      if (h < 18) return Math.random() < 0.3 ? getRandomMsg('afternoon') : getRandomMsg('idle');
      return getRandomMsg('evening');
    }

    function togglePet() {
      petSleeping.value = !petSleeping.value;
      if (petSleeping.value) {
        petMessage.value = 'zzZ... 回窝了';
      } else {
        const card = document.querySelector('.doghouse-card');
        if (card) {
          const r = card.getBoundingClientRect();
          petX.value = r.right + 12;
          petY.value = r.top;
        } else {
          petX.value = 340;
          petY.value = 240;
        }
        petState.value = 'stand';
        petStateChange = 0;
        petMessage.value = '早安！小橘子来啦~';
        petMood.value = 'happy';
      }
      drawDoghouse();
    }

    // ── Drawing ──
    const PXL = 3; // 3px per cell → 32x32 grid on 96x96

    function p(ctx, x, y, w, h, color) {
      ctx.fillStyle = color; ctx.fillRect(x * PXL, y * PXL, w * PXL, h * PXL);
    }

    function drawDoghouse() {
      const canvas = document.querySelector('.doghouse-card canvas');
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, 108, 64);
      const pp = (x, y, w, h, c) => { ctx.fillStyle = c; ctx.fillRect(x, y, w, h); };

      pp(8, 0, 92, 8, '#C62828');
      pp(50, 8, 8, 6, '#B71C1C');
      pp(8, 8, 92, 56, '#8D6E63');
      pp(8, 8, 92, 4, '#6D4C41');
      pp(36, 20, 36, 44, '#4E342E');
      pp(66, 42, 4, 4, '#FFD54F');
      pp(40, 30, 28, 12, '#FFECB3');
      ctx.fillStyle = '#4E342E'; ctx.font = 'bold 8px sans-serif';
      ctx.fillText('小橘子', 44, 40);

      if (petSleeping.value) {
        pp(44, 48, 14, 8, '#FCA311');
        pp(46, 44, 10, 6, '#FFBA3B');
        pp(38, 50, 8, 5, '#E68A00');
        pp(48, 46, 2, 2, '#3B1F00');
        pp(54, 46, 2, 2, '#3B1F00');
        ctx.fillStyle = '#fff'; ctx.font = '8px sans-serif';
        ctx.fillText('z', 24, 18); ctx.fillText('z', 18, 12); ctx.fillText('Z', 12, 6);
      }
    }

    function drawPet() {
      const canvas = document.querySelector('.pet-float canvas');
      if (!canvas || petSleeping.value) return;
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, 64, 64);
      const p = 8;

      // 身体
      ctx.fillStyle = '#d4a050';
      ctx.fillRect(p * 2, p * 3, p * 4, p * 3);
      // 肚子
      ctx.fillStyle = '#f0d8a0';
      ctx.fillRect(p * 3, p * 4, p * 2, p * 2);
      // 头
      ctx.fillStyle = '#d4a050';
      ctx.fillRect(p * 1, p * 0.5, p * 5, p * 3);
      // 耳朵（下垂）
      ctx.fillStyle = '#b87830';
      ctx.fillRect(0, 0, p * 2, p * 2.5);
      ctx.fillRect(p * 6, 0, p * 2, p * 2.5);
      ctx.fillStyle = '#e8b860';
      ctx.fillRect(p * 0.5, 0, p, p * 1.5);
      ctx.fillRect(p * 6.5, 0, p, p * 1.5);

      // 眨眼动画
      const blink = Math.floor(petAnimFrame.value / 15) % 6 === 0;
      ctx.fillStyle = '#1a0a00';
      if (!blink) {
        ctx.fillRect(p * 2, p * 1.5, p, p * 1.5);
        ctx.fillRect(p * 5, p * 1.5, p, p * 1.5);
      }
      // 眼睛高光
      ctx.fillStyle = '#fff';
      if (!blink) {
        ctx.fillRect(p * 2.3, p * 1.5, 4, 4);
        ctx.fillRect(p * 5.3, p * 1.5, 4, 4);
      }

      // 鼻子
      ctx.fillStyle = '#2a0a00';
      ctx.fillRect(p * 3.5, p * 2.5, p, p * 0.7);
      // 嘴巴
      ctx.fillStyle = '#6a3010';
      ctx.fillRect(p * 3, p * 3.2, p * 2, p * 0.4);

      // 摇尾巴
      const wag = petWagging.value ? Math.sin(petAnimFrame.value * 0.6) * 4 : 0;
      ctx.fillStyle = '#d4a050';
      ctx.fillRect(p * 5.5, p * 1.5 + wag, p, p * 2);

      // 小短腿
      ctx.fillStyle = '#c09040';
      ctx.fillRect(p * 2.2, p * 6, p, p * 1.5);
      ctx.fillRect(p * 4.8, p * 6, p, p * 1.5);

      // 项圈
      ctx.fillStyle = '#e05030';
      ctx.fillRect(p * 1.5, p * 3, p * 5, p * 0.5);

      // 摸头反应
      if (petBlush.value > 0) {
        ctx.fillStyle = `rgba(255, 138, 128, ${petBlush.value})`;
        ctx.fillRect(p * 1.8, p * 3.5, p * 0.8, p * 0.5);
        ctx.fillRect(p * 5.4, p * 3.5, p * 0.8, p * 0.5);
      }

      // 爱心
      for (const heart of petHearts.value) {
        const a = heart.life / heart.maxLife;
        ctx.fillStyle = `rgba(255, 82, 82, ${a})`;
        ctx.font = `${heart.size}px sans-serif`;
        ctx.fillText('♥', heart.x, heart.y);
      }
    }

    // ── Head pat ──
    const petBlush = ref(0);
    const petHearts = ref([]);
    let blushTimer = null;

    function patHead(e) {
      if (petSleeping.value) return;
      e && e.preventDefault();
      petMessage.value = getRandomMsg('patted');
      petMood.value = 'happy';
      petBlush.value = 0.8;
      if (blushTimer) clearTimeout(blushTimer);
      blushTimer = setTimeout(() => { petBlush.value = 0; }, 1500);
      // Floating hearts
      const heart = { x: 30 + Math.random() * 20, y: 20, life: 30, maxLife: 30, size: 10 + Math.random() * 6 };
      petHearts.value = [...petHearts.value.slice(-4), heart];
    }

    // ── Drag ──
    function getPos(e) {
      return e.touches ? { x: e.touches[0].clientX, y: e.touches[0].clientY } : { x: e.clientX, y: e.clientY };
    }

    function startDrag(e) {
      if (petSleeping.value) return;
      e.preventDefault();
      petDragging.value = true;
      const pos = getPos(e);
      dragStartX.value = pos.x;
      dragStartY.value = pos.y;
      petStartX_val.value = petX.value;
      petStartY_val.value = petY.value;
      window.addEventListener('mousemove', onDrag);
      window.addEventListener('mouseup', stopDrag);
      window.addEventListener('touchmove', onDrag, { passive: false });
      window.addEventListener('touchend', stopDrag);
    }

    function onDrag(e) {
      if (!petDragging.value) return;
      const pos = getPos(e);
      petX.value = petStartX_val.value + (pos.x - dragStartX.value);
      petY.value = petStartY_val.value + (pos.y - dragStartY.value);
    }

    function stopDrag() {
      petDragging.value = false;
      window.removeEventListener('mousemove', onDrag);
      window.removeEventListener('mouseup', stopDrag);
      window.removeEventListener('touchmove', onDrag);
      window.removeEventListener('touchend', stopDrag);
    }

    // ── Animation loop (requestAnimationFrame for 60fps) ──
    function onVisibilityChange() {
      if (document.hidden) {
        if (petRafId) { cancelAnimationFrame(petRafId); petRafId = null; }
      } else {
        if (!petRafId) { lastPetTime = performance.now(); petRafId = requestAnimationFrame(petLoop); }
      }
    }

    let petRafId = null;
    let lastPetTime = performance.now();
    const PET_FRAME_MS = 1000 / 60;

    function petLoop(now) {
      if (petSleeping.value) {
        // 睡觉时降到约1fps省电
        if (now - lastPetTime >= 1000) {
          lastPetTime = now;
          drawDoghouse();
        }
        petRafId = requestAnimationFrame(petLoop);
        return;
      }

      if (now - lastPetTime >= PET_FRAME_MS) {
        lastPetTime = now;
        drawDoghouse();
        drawPet();
        petAnimFrame.value++;
        petStateChange++;

        if (!petSleeping.value) {
          // Idle state transitions
          if (petStateChange > 300 && Math.random() < 0.006) {
            petState.value = petState.value === 'stand' ? 'sit' : 'stand';
            petStateChange = 0;
          }
          if (petStateChange > 400 && Math.random() < 0.004 && petState.value === 'stand') {
            petState.value = 'stretch';
            setTimeout(() => { if (petState.value === 'stretch') petState.value = 'stand'; }, 2500);
            petStateChange = 0;
          }
          // Rotate messages
          if (petAnimFrame.value % 300 === 0) petMessage.value = pickPetMessage();
          // Market mood
          if (petAnimFrame.value % 360 === 0) {
            const ticker = marketTicker.value;
            if (ticker && ticker.sh_pe !== undefined) {
              if (ticker.sh_pe > 0.5) petMood.value = 'happy';
              else if (ticker.sh_pe < -0.5) petMood.value = 'sad';
              else petMood.value = 'curious';
            }
          }
        }

        // Heart animations
        if (petHearts.value.length > 0) {
          petHearts.value = petHearts.value
            .map(h => ({ ...h, life: h.life - 1, y: h.y - 0.8 }))
            .filter(h => h.life > 0);
        }
      }
      petRafId = requestAnimationFrame(petLoop);
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

      // Pixel pet: draw immediately, start animation loop at 60fps
      drawDoghouse();
      lastPetTime = performance.now();
      petRafId = requestAnimationFrame(petLoop);
      document.addEventListener('visibilitychange', onVisibilityChange);

      // 第一梯队：轻量级，并发加载
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
    });

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
      weightsResult, dataQuality, updateResult,
      updateWeights, checkDataQuality, gitUpdate,
      signalStats, topStrategies, showToast,
      buying, selling,
      // Pixel pet
      petMessage, petX, petY, petSleeping, petDragging, petMood, petBlush, petHearts,
      togglePet, startDrag, patHead,
    };
  }
}).mount('#app');
