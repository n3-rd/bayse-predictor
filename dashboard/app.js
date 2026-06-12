let balanceHistory = [];
let timeLabels = [];
let balanceChart = null;

// Initialize Chart
function initChart() {
    const ctx = document.getElementById('balanceChart').getContext('2d');
    
    // Default gradient
    const gradient = ctx.createLinearGradient(0, 0, 0, 300);
    gradient.addColorStop(0, 'rgba(6, 182, 212, 0.3)');
    gradient.addColorStop(1, 'rgba(6, 182, 212, 0.01)');

    balanceChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: timeLabels,
            datasets: [{
                label: 'Available Balance (NGN)',
                data: balanceHistory,
                borderColor: '#06b6d4',
                borderWidth: 2,
                pointRadius: 1,
                pointHoverRadius: 4,
                backgroundColor: gradient,
                fill: true,
                tension: 0.3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.03)' },
                    ticks: { color: '#64748b', font: { size: 10 } }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.03)' },
                    ticks: { color: '#64748b', font: { size: 10 } }
                }
            }
        }
    });
}

function updateChart(newBalance) {
    if (!balanceChart) {
        initChart();
    }
    
    const now = new Date();
    const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    
    // Add data point
    balanceHistory.push(newBalance);
    timeLabels.push(timeStr);
    
    // Keep last 30 points
    if (balanceHistory.length > 30) {
        balanceHistory.shift();
        timeLabels.shift();
    }
    
    balanceChart.update();
}

// Format currency
function formatCurrency(val, currency = 'NGN') {
    return Number(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ' + currency;
}

// Poll API Status
async function pollStatus() {
    try {
        const response = await fetch('/api/status');
        if (!response.ok) throw new Error('Network response not ok');
        const data = await response.json();
        
        // 1. Update Global State
        document.getElementById('server-time').innerText = new Date().toISOString().replace('T', ' ').substring(0, 19) + ' UTC';
        
        // DRY_RUN badge
        const dryRunBadge = document.getElementById('dry-run-badge');
        if (data.dry_run) {
            dryRunBadge.className = 'badge secondary';
            dryRunBadge.innerText = 'DRY RUN (PAPER)';
        } else {
            dryRunBadge.className = 'badge live';
            dryRunBadge.innerText = 'LIVE TRADING';
        }

        // ML Model badge
        const mlBadge = document.getElementById('ml-model-badge');
        if (data.ml_status) {
            if (data.ml_status.is_trained) {
                mlBadge.className = 'badge success';
                mlBadge.innerText = 'ML: ' + data.ml_status.model_type.replace('Classifier', '');
            } else {
                mlBadge.className = 'badge secondary';
                mlBadge.innerText = 'ML: COLD-STARTING';
            }
        }
        
        // Kill Switch status
        const ksBadge = document.getElementById('kill-switch-badge');
        if (data.kill_switch_active) {
            ksBadge.className = 'badge danger';
            ksBadge.innerText = 'KILL SWITCH TRIGGERED';
        } else {
            ksBadge.className = 'badge success';
            ksBadge.innerText = 'RISK CONTROLS OK';
        }

        // Update Bot Control states
        const pBadge = document.getElementById('predictor-status-badge');
        const pStartBtn = document.getElementById('start-predictor-btn');
        const pStopBtn = document.getElementById('stop-predictor-btn');
        if (data.predictor_running) {
            pBadge.className = 'badge success';
            pBadge.innerText = 'RUNNING';
            pStartBtn.style.display = 'none';
            pStopBtn.style.display = 'inline-block';
        } else {
            pBadge.className = 'badge secondary';
            pBadge.innerText = 'STOPPED';
            pStartBtn.style.display = 'inline-block';
            pStopBtn.style.display = 'none';
        }

        const ctBadge = document.getElementById('copytrader-status-badge');
        const ctStartBtn = document.getElementById('start-copytrader-btn');
        const ctStopBtn = document.getElementById('stop-copytrader-btn');
        if (data.copytrader_running) {
            ctBadge.className = 'badge success';
            ctBadge.innerText = 'RUNNING';
            ctStartBtn.style.display = 'none';
            ctStopBtn.style.display = 'inline-block';
        } else {
            ctBadge.className = 'badge secondary';
            ctBadge.innerText = 'STOPPED';
            ctStartBtn.style.display = 'inline-block';
            ctStopBtn.style.display = 'none';
        }

        if (data.target_traders && data.target_traders.length > 0) {
            document.getElementById('copytrader-targets').innerText = data.target_traders.join(', ');
        } else {
            document.getElementById('copytrader-targets').innerText = 'None';
        }

        // 2. Update Metrics
        document.getElementById('avail-balance').innerText = formatCurrency(data.available_balance, data.currency);
        document.getElementById('total-equity').innerText = 'Starting Baseline: ' + formatCurrency(data.starting_equity, data.currency);
        
        // Daily P&L
        const pnl = data.daily_pnl;
        const pnlPct = data.starting_equity > 0 ? (pnl / data.starting_equity) * 100 : 0;
        const pnlEl = document.getElementById('daily-pnl');
        pnlEl.innerText = (pnl >= 0 ? '+' : '') + formatCurrency(pnl, data.currency);
        pnlEl.className = pnl >= 0 ? 'positive-edge' : 'negative-edge';
        document.getElementById('pnl-pct').innerText = `Daily P&L: ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}% (Limit: 5%)`;
        
        // Markets / Positions counts
        document.getElementById('active-markets-count').innerText = data.active_markets_count;
        document.getElementById('active-positions-count').innerText = data.positions_held_count + ' Positions Held';
        
        // 3. Update Chart
        updateChart(data.available_balance);
        
        // 4. Update Market Table
        const evalsBody = document.getElementById('market-evals-body');
        if (data.evaluations && data.evaluations.length > 0) {
            evalsBody.innerHTML = '';
            data.evaluations.forEach(ev => {
                const tr = document.createElement('tr');
                
                // Classify edges
                const yesEdgeClass = ev.yes_edge >= 0.05 ? 'positive-edge' : ev.yes_edge < 0 ? 'negative-edge' : '';
                const noEdgeClass = ev.no_edge >= 0.05 ? 'positive-edge' : ev.no_edge < 0 ? 'negative-edge' : '';
                
                tr.innerHTML = `
                    <td class="market-title-cell">${ev.title}</td>
                    <td><span class="badge secondary">${ev.symbol}</span></td>
                    <td>${ev.current_price.toFixed(2)} / ${ev.threshold.toFixed(2)}</td>
                    <td>${(ev.true_prob * 100).toFixed(1)}%</td>
                    <td>${(ev.ask * 100).toFixed(1)} / ${(ev.bid * 100).toFixed(1)}</td>
                    <td class="${yesEdgeClass}">${ev.yes_edge >= 0 ? '+' : ''}${(ev.yes_edge * 100).toFixed(1)}%</td>
                    <td class="${noEdgeClass}">${ev.no_edge >= 0 ? '+' : ''}${(ev.no_edge * 100).toFixed(1)}%</td>
                `;
                evalsBody.appendChild(tr);
            });
        } else {
            evalsBody.innerHTML = '<tr><td colspan="7" class="empty-row">No active markets matched filters or books are empty.</td></tr>';
        }
        
        // 5. Update Log Terminal
        if (data.logs) {
            const consoleEl = document.getElementById('log-console');
            // Check if user scrolled to bottom
            const isScrolledToBottom = consoleEl.scrollHeight - consoleEl.clientHeight <= consoleEl.scrollTop + 50;
            
            consoleEl.innerHTML = '';
            data.logs.forEach(line => {
                const div = document.createElement('div');
                div.className = 'terminal-line';
                
                if (line.includes('[ERROR]') || line.includes('rejected:')) {
                    div.classList.add('error-line');
                } else if (line.includes('[WARNING]')) {
                    div.classList.add('warning-line');
                } else if (line.includes('Approved') || line.includes('success') || line.includes('FILLED')) {
                    div.classList.add('success-line');
                } else if (line.includes('[SYSTEM]')) {
                    div.classList.add('system-line');
                } else {
                    div.classList.add('info-line');
                }
                div.innerText = line;
                consoleEl.appendChild(div);
            });
            
            // Auto scroll if was at bottom
            if (isScrolledToBottom) {
                consoleEl.scrollTop = consoleEl.scrollHeight;
            }
        }

    } catch (error) {
        console.error('Polling failed:', error);
        document.getElementById('server-time').innerText = 'CONNECTION ERROR';
        document.querySelector('.status-dot').className = 'status-dot'; // Offline
    }
}

// Clear Logs button event handler
document.getElementById('clear-logs-btn').addEventListener('click', () => {
    document.getElementById('log-console').innerHTML = '';
});

// Bot Control Event Handlers
document.getElementById('start-predictor-btn').addEventListener('click', async () => {
    try {
        await fetch('/api/control/predictor/start', { method: 'POST' });
        pollStatus();
    } catch (e) { console.error(e); }
});

document.getElementById('stop-predictor-btn').addEventListener('click', async () => {
    try {
        await fetch('/api/control/predictor/stop', { method: 'POST' });
        pollStatus();
    } catch (e) { console.error(e); }
});

document.getElementById('start-copytrader-btn').addEventListener('click', async () => {
    try {
        await fetch('/api/control/copytrader/start', { method: 'POST' });
        pollStatus();
    } catch (e) { console.error(e); }
});

document.getElementById('stop-copytrader-btn').addEventListener('click', async () => {
    try {
        await fetch('/api/control/copytrader/stop', { method: 'POST' });
        pollStatus();
    } catch (e) { console.error(e); }
});

// Start loop
initChart();
pollStatus();
setInterval(pollStatus, 2000);
