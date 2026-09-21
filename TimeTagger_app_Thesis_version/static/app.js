// Swabian TimeTagger Scientific Dashboard - Client Logic

// Websocket and Config
let socket = null;
let currentConfig = {};
let disabledPixels = new Set();
let lastJsiData = null;

// Load saved disabled pixels from local storage
try {
    const saved = localStorage.getItem("jsi_disabled_pixels");
    if (saved) {
        disabledPixels = new Set(JSON.parse(saved));
    }
} catch (e) {
    console.error("Failed to load disabled pixels:", e);
}

// Chart.js Instances
let ratesBarChart = null;
let traceLineChart = null;
let coincidenceHistChart = null;

// Tab Selection DOM
const tabBtnConfig = document.getElementById("tab-btn-config");
const tabBtnDash = document.getElementById("tab-btn-dash");
const pageConfig = document.getElementById("page-config");
const pageDash = document.getElementById("page-dash");

// Device Status DOM
const deviceStatus = document.getElementById("device-status");
const reconnectBtn = document.getElementById("reconnect-btn");

// Config inputs DOM
const cfgMeasureTrace = document.getElementById("cfg-measure-trace");
const cfgMeasureHist = document.getElementById("cfg-measure-hist");
const cfgMeasureJsi = document.getElementById("cfg-measure-jsi");
const cfgIntegration = document.getElementById("cfg-integration");
const cfgNormalize = document.getElementById("cfg-normalize");
const cfgSimulationMode = document.getElementById("cfg-simulation-mode");
const cfgHistStart = document.getElementById("cfg-hist-start");
const cfgHistClick = document.getElementById("cfg-hist-click");
const cfgHistBinwidth = document.getElementById("cfg-hist-binwidth");
const cfgHistBins = document.getElementById("cfg-hist-bins");
const cfgJsiBinwidth = document.getElementById("cfg-jsi-binwidth");
const cfgJsiBins = document.getElementById("cfg-jsi-bins");
const configChannelsTbody = document.getElementById("config-channels-tbody");
const saveConfigBtn = document.getElementById("save-config-btn");

// Dashboard controls DOM
const dashNormalizeToggle = document.getElementById("dash-normalize-toggle");
const dashIntegrationLabel = document.getElementById("dash-integration-label");
const dashRecordBtn = document.getElementById("dash-record-btn");
const dashRecordStatus = document.getElementById("dash-record-status");
const dashRecDuration = document.getElementById("dash-rec-duration");
const dashRecPoints = document.getElementById("dash-rec-points");

// Dashboard panel containers (for conditional rendering)
const panelTraceContainer = document.getElementById("panel-trace-container");
const panelHistContainer = document.getElementById("panel-hist-container");
const panelJsiContainer = document.getElementById("panel-jsi-container");
const jsiHeatmap = document.getElementById("jsi-heatmap");
const labelHistStart = document.getElementById("label-hist-start");
const labelHistClick = document.getElementById("label-hist-click");

// Histogram Axis Bounds DOM
const yAuto = document.getElementById("y-auto");
const yMax = document.getElementById("y-max");
const xAuto = document.getElementById("x-auto");
const xMin = document.getElementById("x-min");
const xMax = document.getElementById("x-max");
const clearHistogramBtn = document.getElementById("clear-histogram-btn");

// Custom Sizing, Visibility, CSV Export, Theme & Config utils DOM
const themeToggleBtn = document.getElementById("theme-toggle-btn");

const exportConfigBtn = document.getElementById("export-config-btn");
const importConfigBtn = document.getElementById("import-config-btn");
const importConfigFile = document.getElementById("import-config-file");

const showRatesChart = document.getElementById("show-rates-chart");
const showTraceChart = document.getElementById("show-trace-chart");
const showHistChart = document.getElementById("show-hist-chart");
const showJsiChart = document.getElementById("show-jsi-chart");

const panelRatesContainer = document.getElementById("panel-rates-container");

const barYAuto = document.getElementById("bar-y-auto");
const barYMax = document.getElementById("bar-y-max");
const exportRatesBtn = document.getElementById("export-rates-btn");
const wrapperBarChart = document.getElementById("wrapper-bar-chart");

const traceYAuto = document.getElementById("trace-y-auto");
const traceYMin = document.getElementById("trace-y-min");
const traceYMax = document.getElementById("trace-y-max");
const exportTraceBtn = document.getElementById("export-trace-btn");
const wrapperTraceChart = document.getElementById("wrapper-trace-chart");

const exportHistBtn = document.getElementById("export-hist-btn");
const wrapperHistChart = document.getElementById("wrapper-hist-chart");

// JSI controls DOM
const jsiColorAuto = document.getElementById("jsi-color-auto");
const jsiColorMax = document.getElementById("jsi-color-max");
const jsiClearDisabledBtn = document.getElementById("jsi-clear-disabled-btn");
const jsiToggleDiagonalBtn = document.getElementById("jsi-toggle-diagonal-btn");
const exportJsiBtn = document.getElementById("export-jsi-btn");

// Standard scientific high-contrast colors (expanded to 20 channels)
const SCI_COLORS = {
    1: "#1d4ed8", // Blue
    2: "#b91c1c", // Red
    3: "#15803d", // Green
    4: "#b45309", // Orange
    5: "#6d28d9", // Purple
    6: "#0f766e", // Teal
    7: "#be185d", // Pink
    8: "#a21caf", // Magenta
    9: "#3b82f6", // Bright Blue
    10: "#ef4444", // Bright Red
    11: "#22c55e", // Bright Green
    12: "#f59e0b", // Yellow/Gold
    13: "#8b5cf6", // Medium Purple
    14: "#14b8a6", // Bright Teal
    15: "#ec4899", // Bright Pink
    16: "#d946ef", // Bright Violet
    17: "#6366f1", // Indigo
    18: "#84cc16", // Lime Green
    19: "#06b6d4", // Cyan
    20: "#f97316"  // Orange
};

// Tab Management
function setupTabs() {
    tabBtnConfig.addEventListener("click", () => {
        tabBtnConfig.classList.add("active");
        tabBtnDash.classList.remove("active");
        pageConfig.classList.remove("hidden");
        pageDash.classList.add("hidden");
    });

    tabBtnDash.addEventListener("click", () => {
        tabBtnDash.classList.add("active");
        tabBtnConfig.classList.remove("active");
        pageDash.classList.remove("hidden");
        pageConfig.classList.add("hidden");
        
        // Trigger resize on charts to make sure they render correctly inside unhidden container
        setTimeout(() => {
            if (ratesBarChart) ratesBarChart.resize();
            if (traceLineChart) traceLineChart.resize();
            if (coincidenceHistChart) coincidenceHistChart.resize();
        }, 50);
    });
}

// WebSocket connection management
function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    
    console.log(`Connecting to WebSocket: ${wsUrl}`);
    socket = new WebSocket(wsUrl);
    
    socket.onopen = () => {
        console.log("WebSocket connected.");
        updateStatusBadge(true, false);
    };
    
    socket.onclose = () => {
        console.warn("WebSocket disconnected. Retrying in 2 seconds...");
        updateStatusBadge(false, false);
        setTimeout(connectWebSocket, 2000);
    };
    
    socket.onerror = (err) => {
        console.error("WebSocket error:", err);
    };
    
    socket.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleServerMessage(msg);
        } catch (e) {
            console.error("Error decoding message:", e);
        }
    };
}

// Update status badges
function updateStatusBadge(connected, isVirtual) {
    deviceStatus.className = "status-indicator";
    const label = deviceStatus.querySelector(".status-label");
    
    if (!connected) {
        deviceStatus.classList.add("status-disconnected");
        label.innerText = "OFFLINE";
    } else {
        if (isVirtual) {
            deviceStatus.classList.add("status-virtual");
            label.innerText = "SIMULATOR";
        } else {
            deviceStatus.classList.add("status-hardware");
            label.innerText = "HARDWARE ACTIVE";
        }
    }
}

// Handle incoming messages
function handleServerMessage(msg) {
    if (msg.type === "config") {
        currentConfig = msg.config;
        syncConfigUI();
    } else if (msg.type === "data") {
        updateRealtimePlots(msg);
    } else if (msg.type === "recording_started") {
        setRecordingUI(true);
    } else if (msg.type === "recording_stopped") {
        setRecordingUI(false);
    }
}

// Synchronize Settings Page elements
function syncConfigUI() {
    updateStatusBadge(true, currentConfig.is_virtual);
    
    // Checkboxes
    cfgMeasureTrace.checked = currentConfig.measure_counter_trace;
    cfgMeasureHist.checked = currentConfig.measure_coincidence_histogram;
    cfgMeasureJsi.checked = currentConfig.measure_jsi;
    cfgNormalize.checked = currentConfig.normalize;
    cfgSimulationMode.checked = currentConfig.simulation_mode;
    
    // Select dropdowns
    cfgIntegration.value = currentConfig.integration_time_sec;
    
    // Populate start/click channels selections
    populateHistogramChannelOptions();
    cfgHistStart.value = currentConfig.hist_start_channel;
    cfgHistClick.value = currentConfig.hist_click_channel;
    
    // Bin counts and size
    cfgHistBinwidth.value = currentConfig.hist_binwidth_ps;
    cfgHistBins.value = currentConfig.hist_n_bins;
    
    // JSI settings
    cfgJsiBinwidth.value = currentConfig.jsi_binwidth_ps;
    cfgJsiBins.value = currentConfig.jsi_n_bins;
    
    // Normalize checkbox on Dashboard
    dashNormalizeToggle.checked = currentConfig.normalize;
    
    // Integration Label on Dashboard
    updateIntegrationLabel(currentConfig.integration_time_sec);
    
    // Recording UI state
    setRecordingUI(currentConfig.recording_active);
    
    // Build channels table
    renderChannelsTable();
    
    // Conditional rendering of dashboard plots
    if (currentConfig.measure_counter_trace) {
        panelTraceContainer.classList.remove("hidden");
    } else {
        panelTraceContainer.classList.add("hidden");
    }
    
    if (currentConfig.measure_coincidence_histogram) {
        panelHistContainer.classList.remove("hidden");
        labelHistStart.innerText = currentConfig.hist_start_channel;
        labelHistClick.innerText = currentConfig.hist_click_channel;
    } else {
        panelHistContainer.classList.add("hidden");
    }
    
    if (currentConfig.measure_jsi) {
        panelJsiContainer.classList.remove("hidden");
    } else {
        panelJsiContainer.classList.add("hidden");
    }
    
    // Adjust layout columns depending on which plots are visible
    adjustDashboardGrid();
}

function updateIntegrationLabel(seconds) {
    if (seconds < 1.0) {
        dashIntegrationLabel.innerText = `${Math.round(seconds * 1000)} ms`;
    } else {
        dashIntegrationLabel.innerText = `${seconds.toFixed(1)} s`;
    }
}

function adjustDashboardGrid() {
    const ratesVisible = showRatesChart.checked;
    const traceVisible = showTraceChart.checked;
    const histVisible = showHistChart.checked;
    const jsiVisible = showJsiChart.checked;
    const dashLayout = document.querySelector(".dash-layout");
    
    if (ratesVisible) {
        panelRatesContainer.classList.remove("hidden");
    } else {
        panelRatesContainer.classList.add("hidden");
    }
    
    if (traceVisible) {
        panelTraceContainer.classList.remove("hidden");
    } else {
        panelTraceContainer.classList.add("hidden");
    }
    
    if (histVisible) {
        panelHistContainer.classList.remove("hidden");
    } else {
        panelHistContainer.classList.add("hidden");
    }
    
    if (jsiVisible) {
        panelJsiContainer.classList.remove("hidden");
    } else {
        panelJsiContainer.classList.add("hidden");
    }
    
    const visibleCount = [ratesVisible, traceVisible, histVisible, jsiVisible].filter(Boolean).length;
    
    if (visibleCount === 4) {
        dashLayout.style.gridTemplateColumns = "1fr 1fr";
        panelRatesContainer.style.gridColumn = "1 / -1";
        panelTraceContainer.style.gridColumn = "1 / -1";
        panelHistContainer.style.gridColumn = "span 1";
        panelJsiContainer.style.gridColumn = "span 1";
    } else if (visibleCount === 3) {
        dashLayout.style.gridTemplateColumns = "1fr 1fr";
        if (ratesVisible) {
            panelRatesContainer.style.gridColumn = "1 / -1";
            if (traceVisible) panelTraceContainer.style.gridColumn = "span 1";
            if (histVisible) panelHistContainer.style.gridColumn = "span 1";
            if (jsiVisible) panelJsiContainer.style.gridColumn = "span 1";
        } else {
            panelTraceContainer.style.gridColumn = "1 / -1";
            panelHistContainer.style.gridColumn = "span 1";
            panelJsiContainer.style.gridColumn = "span 1";
        }
    } else if (visibleCount === 2) {
        dashLayout.style.gridTemplateColumns = "1fr 1fr";
        if (ratesVisible) panelRatesContainer.style.gridColumn = "span 1";
        if (traceVisible) panelTraceContainer.style.gridColumn = "span 1";
        if (histVisible) panelHistContainer.style.gridColumn = "span 1";
        if (jsiVisible) panelJsiContainer.style.gridColumn = "span 1";
    } else if (visibleCount === 1) {
        dashLayout.style.gridTemplateColumns = "1fr";
        if (ratesVisible) panelRatesContainer.style.gridColumn = "1 / -1";
        if (traceVisible) panelTraceContainer.style.gridColumn = "1 / -1";
        if (histVisible) panelHistContainer.style.gridColumn = "1 / -1";
        if (jsiVisible) panelJsiContainer.style.gridColumn = "1 / -1";
    }
}

// Populates dropdown elements for Start / Click
function populateHistogramChannelOptions() {
    const prevStart = cfgHistStart.value;
    const prevClick = cfgHistClick.value;
    
    cfgHistStart.innerHTML = "";
    cfgHistClick.innerHTML = "";
    
    for (let ch = 1; ch <= 20; ch++) {
        const alias = currentConfig.channel_aliases?.[ch] || `Channel ${ch}`;
        
        const startOpt = document.createElement("option");
        startOpt.value = ch;
        startOpt.text = `Channel ${ch} (${alias})`;
        cfgHistStart.appendChild(startOpt);
        
        const clickOpt = document.createElement("option");
        clickOpt.value = ch;
        clickOpt.text = `Channel ${ch} (${alias})`;
        cfgHistClick.appendChild(clickOpt);
    }
    
    if (prevStart) cfgHistStart.value = prevStart;
    if (prevClick) cfgHistClick.value = prevClick;
}

// Renders the tabular channels layout
function renderChannelsTable() {
    if (!currentConfig.channel_aliases) return;
    
    // Save active element details
    const activeEl = document.activeElement;
    const activeId = activeEl ? activeEl.id : null;
    const selectionStart = activeEl ? activeEl.selectionStart : null;
    const selectionEnd = activeEl ? activeEl.selectionEnd : null;

    configChannelsTbody.innerHTML = "";
    
    for (let ch = 1; ch <= 20; ch++) {
        const isActive = currentConfig.active_channels.includes(ch);
        const alias = currentConfig.channel_aliases[ch] || "";
        const voltage = currentConfig.trigger_voltages[ch] !== undefined ? currentConfig.trigger_voltages[ch] : 0.5;
        const color = SCI_COLORS[ch];
        
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td class="table-checkbox">
                <input type="checkbox" id="cfg-ch-active-${ch}" ${isActive ? 'checked' : ''} style="accent-color: ${color}; width:16px; height:16px;">
            </td>
            <td class="ch-cell-num" style="border-left: 4px solid ${color}">${ch}</td>
            <td>
                <input type="text" class="form-control sm" id="cfg-ch-alias-${ch}" value="${alias}" placeholder="Channel ${ch} Name" style="font-weight: 500;">
            </td>
            <td>
                <div class="trig-cell">
                    <input type="number" class="form-control sm" id="cfg-ch-voltage-${ch}" value="${voltage}" step="0.05" min="-2.5" max="2.5">
                    <span>V</span>
                </div>
            </td>
        `;
        
        configChannelsTbody.appendChild(tr);
    }
    
    // Restore focus
    if (activeId) {
        const el = document.getElementById(activeId);
        if (el) {
            el.focus();
            if (selectionStart !== null && selectionEnd !== null) {
                el.setSelectionRange(selectionStart, selectionEnd);
            }
        }
    }
}

// Gather config settings from DOM and transmit to backend
function sendSaveConfig() {
    const configPayload = {
        measure_counter_trace: cfgMeasureTrace.checked,
        measure_coincidence_histogram: cfgMeasureHist.checked,
        measure_jsi: cfgMeasureJsi.checked,
        integration_time_sec: parseFloat(cfgIntegration.value),
        normalize: cfgNormalize.checked,
        simulation_mode: cfgSimulationMode.checked,
        hist_start_channel: parseInt(cfgHistStart.value),
        hist_click_channel: parseInt(cfgHistClick.value),
        hist_binwidth_ps: parseInt(cfgHistBinwidth.value),
        hist_n_bins: parseInt(cfgHistBins.value),
        jsi_binwidth_ps: parseInt(cfgJsiBinwidth.value),
        jsi_n_bins: parseInt(cfgJsiBins.value),
        
        active_channels: [],
        channel_aliases: {},
        trigger_voltages: {}
    };
    
    for (let ch = 1; ch <= 20; ch++) {
        if (document.getElementById(`cfg-ch-active-${ch}`).checked) {
            configPayload.active_channels.push(ch);
        }
        
        const aliasVal = document.getElementById(`cfg-ch-alias-${ch}`).value.trim();
        configPayload.channel_aliases[ch] = aliasVal || `Channel ${ch}`;
        
        const voltVal = parseFloat(document.getElementById(`cfg-ch-voltage-${ch}`).value);
        configPayload.trigger_voltages[ch] = isNaN(voltVal) ? 0.5 : voltVal;
    }
    
    sendWebSocketMsg({
        type: "save_config",
        config: configPayload
    });
    
    // Switch to Dashboard Tab automatically when user saves
    tabBtnDash.click();
}

// Update charts in real-time
function updateRealtimePlots(msg) {
    const channels = msg.cps.channels;
    const latestRates = msg.cps.latest;
    const aliases = msg.cps.aliases;
    const unit = msg.normalize ? "CPS" : "Counts";
    
    // 1. Update Rates Bar Chart
    updateRatesBarChart(channels, latestRates, aliases, unit);
    
    // 2. Update Rolling Trace Chart
    if (currentConfig.measure_counter_trace && msg.cps.history) {
        updateTraceLineChart(msg.cps, unit);
    }
    
    // 3. Update Coincidence Histogram
    if (currentConfig.measure_coincidence_histogram && msg.histogram) {
        updateHistogramPlot(msg.histogram);
    }
    
    // 4. Update JSI Heatmap
    if (currentConfig.measure_jsi && msg.jsi) {
        lastJsiData = msg.jsi;
        renderJsiHeatmap(msg.jsi);
    }
    
    // 5. Update Recorder Stats
    if (msg.recording.active) {
        dashRecPoints.innerText = msg.recording.points;
        dashRecDuration.innerText = `${msg.recording.elapsed.toFixed(1)}s`;
    }
}

// Update Count Rates Bar Chart
function updateRatesBarChart(channels, values, aliases, unit) {
    if (!ratesBarChart) return;
    
    // Map standard colors for each active channel bar
    const backgroundColors = channels.map(ch => SCI_COLORS[ch]);
    const borderColors = channels.map(ch => SCI_COLORS[ch]);
    
    ratesBarChart.data.labels = aliases;
    ratesBarChart.data.datasets[0].data = values;
    ratesBarChart.data.datasets[0].backgroundColor = backgroundColors;
    ratesBarChart.data.datasets[0].borderColor = borderColors;
    ratesBarChart.options.scales.y.title.text = `Current Count Rate (${unit})`;
    
    // Apply Y-axis bounds
    if (barYAuto.checked) {
        ratesBarChart.options.scales.y.max = undefined;
    } else {
        const val = parseFloat(barYMax.value);
        if (!isNaN(val) && val > 0) {
            ratesBarChart.options.scales.y.max = val;
        }
    }
    
    ratesBarChart.update("none");
}

// Update Rolling Line Trace Chart
function updateTraceLineChart(cpsData, unit) {
    if (!traceLineChart) return;
    
    const channels = cpsData.channels;
    const history = cpsData.history;
    const timeIndex = cpsData.time_index;
    const aliases = cpsData.aliases;
    
    const datasets = [];
    
    channels.forEach((ch, idx) => {
        const color = SCI_COLORS[ch];
        datasets.push({
            label: aliases[idx],
            data: history[idx],
            borderColor: color,
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            tension: 0.1,
            pointRadius: 0,
            pointHoverRadius: 4,
            fill: false
        });
    });
    
    traceLineChart.data.labels = timeIndex;
    traceLineChart.data.datasets = datasets;
    traceLineChart.options.scales.y.title.text = `Counts (${unit})`;
    
    // Apply Y-axis bounds
    if (traceYAuto.checked) {
        traceLineChart.options.scales.y.min = undefined;
        traceLineChart.options.scales.y.max = undefined;
    } else {
        const ymin = parseFloat(traceYMin.value);
        const ymax = parseFloat(traceYMax.value);
        if (!isNaN(ymin)) traceLineChart.options.scales.y.min = ymin;
        if (!isNaN(ymax)) traceLineChart.options.scales.y.max = ymax;
    }
    
    traceLineChart.update("none");
}

// Update Coincidence Histogram Chart
function updateHistogramPlot(histData) {
    if (!coincidenceHistChart) return;
    
    const values = histData.values;
    const index = histData.index; // in nanoseconds
    const startCh = histData.start_channel;
    const clickCh = histData.click_channel;
    
    // Use the Click channel color as the line color
    const lineColor = SCI_COLORS[clickCh] || "#334155";
    
    coincidenceHistChart.data.labels = index;
    coincidenceHistChart.data.datasets[0].data = values;
    coincidenceHistChart.data.datasets[0].borderColor = lineColor;
    coincidenceHistChart.data.datasets[0].backgroundColor = `${lineColor}15`; // Flat light fill
    
    // Axis Scaling Bounds (Y Axis)
    if (yAuto.checked) {
        coincidenceHistChart.options.scales.y.max = undefined;
    } else {
        const manualMax = parseInt(yMax.value);
        if (!isNaN(manualMax) && manualMax > 0) {
            coincidenceHistChart.options.scales.y.max = manualMax;
        }
    }
    
    // Axis Scaling Bounds (X Axis)
    if (xAuto.checked) {
        coincidenceHistChart.options.scales.x.min = undefined;
        coincidenceHistChart.options.scales.x.max = undefined;
    } else {
        const manualMin = parseFloat(xMin.value);
        const manualMax = parseFloat(xMax.value);
        if (!isNaN(manualMin)) coincidenceHistChart.options.scales.x.min = manualMin;
        if (!isNaN(manualMax)) coincidenceHistChart.options.scales.x.max = manualMax;
    }
    
    coincidenceHistChart.update("none");
}

// Setup Chart.js structures
function initScientificCharts() {
    const gridColor = '#e2e8f0';
    const labelColor = '#475569';
    const fontConfig = { family: 'Liberation Sans, Roboto, Arial, sans-serif', size: 10 };

    // 1. Rates Bar Chart
    const ctxBar = document.getElementById("bar-chart-rates").getContext("2d");
    ratesBarChart = new Chart(ctxBar, {
        type: 'bar',
        data: {
            labels: [],
            datasets: [{
                data: [],
                backgroundColor: [],
                borderColor: [],
                borderWidth: 1,
                barThickness: 25
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleFont: { size: 11, weight: 'bold' },
                    bodyFont: { size: 11 }
                }
            },
            scales: {
                x: {
                    grid: { color: gridColor },
                    ticks: { color: labelColor, font: fontConfig }
                },
                y: {
                    beginAtZero: true,
                    grid: { color: gridColor },
                    ticks: { color: labelColor, font: fontConfig },
                    title: {
                        display: true,
                        text: 'Counts',
                        color: labelColor,
                        font: { size: 11, weight: 'bold' }
                    }
                }
            }
        }
    });

    // 2. Rolling Trace Chart
    const ctxTrace = document.getElementById("trace-chart").getContext("2d");
    traceLineChart = new Chart(ctxTrace, {
        type: 'line',
        data: {
            labels: [],
            datasets: []
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: {
                    position: 'top',
                    labels: { color: labelColor, font: fontConfig, boxWidth: 12 }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: '#1e293b'
                }
            },
            scales: {
                x: {
                    type: 'linear',
                    grid: { color: gridColor },
                    ticks: { color: labelColor, font: fontConfig },
                    title: {
                        display: true,
                        text: 'Elapsed Time (seconds)',
                        color: labelColor,
                        font: { size: 11, weight: 'bold' }
                    }
                },
                y: {
                    grid: { color: gridColor },
                    ticks: { color: labelColor, font: fontConfig },
                    title: {
                        display: true,
                        text: 'Counts',
                        color: labelColor,
                        font: { size: 11, weight: 'bold' }
                    }
                }
            }
        }
    });

    // 3. Coincidence Histogram
    const ctxHist = document.getElementById("dash-hist-chart").getContext("2d");
    coincidenceHistChart = new Chart(ctxHist, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                data: [],
                borderWidth: 1.5,
                fill: true,
                pointRadius: 0,
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#1e293b',
                    callbacks: {
                        label: (ctx) => `Counts: ${ctx.parsed.y}`,
                        title: (ctx) => `Delay: ${parseFloat(ctx[0].label).toFixed(3)} ns`
                    }
                }
            },
            scales: {
                x: {
                    type: 'linear',
                    grid: { color: gridColor },
                    ticks: { color: labelColor, font: fontConfig },
                    title: {
                        display: true,
                        text: 'Time Delay (ns)',
                        color: labelColor,
                        font: { size: 11, weight: 'bold' }
                    }
                },
                y: {
                    type: 'linear',
                    grid: { color: gridColor },
                    ticks: { color: labelColor, font: fontConfig },
                    title: {
                        display: true,
                        text: 'Coincidences',
                        color: labelColor,
                        font: { size: 11, weight: 'bold' }
                    }
                }
            }
        }
    });
}

function setRecordingUI(isRecording) {
    if (isRecording) {
        dashRecordBtn.innerHTML = `⏹️ Stop Recording`;
        dashRecordBtn.classList.add("recording");
        dashRecordStatus.classList.remove("hidden");
    } else {
        dashRecordBtn.innerHTML = `🔴 Start Data Record`;
        dashRecordBtn.classList.remove("recording");
        dashRecordStatus.classList.add("hidden");
    }
}

// Helpers
function sendWebSocketMsg(payload) {
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(payload));
    }
}

function setupGlobalEvents() {
    // Save Config Button on page 1
    saveConfigBtn.addEventListener("click", sendSaveConfig);
    
    // Reconnect hardware button
    reconnectBtn.addEventListener("click", () => {
        updateStatusBadge(true, false);
        sendWebSocketMsg({ type: "reconnect_hardware" });
    });
    
    // Normalize toggle on Dashboard Toolbar
    dashNormalizeToggle.addEventListener("change", (e) => {
        sendWebSocketMsg({
            type: "update_normalize",
            normalize: e.target.checked
        });
    });
    
    // Recorder Button
    dashRecordBtn.addEventListener("click", () => {
        if (dashRecordBtn.classList.contains("recording")) {
            sendWebSocketMsg({ type: "stop_recording" });
        } else {
            sendWebSocketMsg({ type: "start_recording" });
        }
    });
    
    // Clear Histogram Data button
    clearHistogramBtn.addEventListener("click", () => {
        // Send current histogram parameters to recreate (which clears accumulated counts)
        sendWebSocketMsg({
            type: "save_config",
            config: currentConfig // Sends existing config to trigger apply_configuration
        });
    });
    
    // Scales Auto/Manual disabling
    yAuto.addEventListener("change", (e) => {
        yMax.disabled = e.target.checked;
        if (e.target.checked) yMax.value = "";
    });
    
    xAuto.addEventListener("change", (e) => {
        xMin.disabled = e.target.checked;
        xMax.disabled = e.target.checked;
        if (e.target.checked) {
            xMin.value = "";
            xMax.value = "";
        }
    });

    barYAuto.addEventListener("change", (e) => {
        barYMax.disabled = e.target.checked;
        if (e.target.checked) barYMax.value = "";
    });
    
    traceYAuto.addEventListener("change", (e) => {
        traceYMin.disabled = e.target.checked;
        traceYMax.disabled = e.target.checked;
        if (e.target.checked) {
            traceYMin.value = "";
            traceYMax.value = "";
        }
    });

    // JSI Color Auto/Manual disabling
    jsiColorAuto.addEventListener("change", (e) => {
        jsiColorMax.disabled = e.target.checked;
        if (e.target.checked) jsiColorMax.value = "";
        if (lastJsiData) renderJsiHeatmap(lastJsiData);
    });
    
    jsiColorMax.addEventListener("input", () => {
        if (lastJsiData) renderJsiHeatmap(lastJsiData);
    });
    
    // Clear Disabled Pixels
    jsiClearDisabledBtn.addEventListener("click", () => {
        disabledPixels.clear();
        localStorage.removeItem("jsi_disabled_pixels");
        if (lastJsiData) renderJsiHeatmap(lastJsiData);
    });
    
    // Toggle Diagonal Pixels
    jsiToggleDiagonalBtn.addEventListener("click", () => {
        if (!lastJsiData) return;
        const channels = lastJsiData.channels;
        
        let allDiagonalDisabled = true;
        channels.forEach(ch => {
            const key = `${ch},${ch}`;
            if (!disabledPixels.has(key)) allDiagonalDisabled = false;
        });
        
        channels.forEach(ch => {
            const key = `${ch},${ch}`;
            if (allDiagonalDisabled) {
                disabledPixels.delete(key);
            } else {
                disabledPixels.add(key);
            }
        });
        
        localStorage.setItem("jsi_disabled_pixels", JSON.stringify(Array.from(disabledPixels)));
        renderJsiHeatmap(lastJsiData);
    });
    
    // Export JSI CSV
    exportJsiBtn.addEventListener("click", exportJsiCSV);

    // CSV Exports
    exportRatesBtn.addEventListener("click", exportRatesCSV);
    exportTraceBtn.addEventListener("click", exportTraceCSV);
    exportHistBtn.addEventListener("click", exportHistogramCSV);
}

// Helper: Download client-side CSV file
function downloadCSV(filename, csvContent) {
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", filename);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// Export Rates CSV
function exportRatesCSV() {
    if (!ratesBarChart) return;
    let csv = "Channel Alias,Rate\n";
    const labels = ratesBarChart.data.labels || [];
    const data = ratesBarChart.data.datasets[0].data || [];
    labels.forEach((lbl, idx) => {
        const val = data[idx] !== undefined ? data[idx] : 0.0;
        csv += `"${lbl}",${val}\n`;
    });
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    downloadCSV(`rates_export_${timestamp}.csv`, csv);
}

// Export Trace CSV
function exportTraceCSV() {
    if (!traceLineChart) return;
    const labels = traceLineChart.data.labels || []; // Time index
    const datasets = traceLineChart.data.datasets || [];
    
    let header = "Elapsed Time (s)";
    datasets.forEach(ds => {
        header += `,"${ds.label}"`;
    });
    header += "\n";
    
    let rows = "";
    for (let i = 0; i < labels.length; i++) {
        let row = `${labels[i]}`;
        datasets.forEach(ds => {
            const val = ds.data[i] !== undefined ? ds.data[i] : "";
            row += `,${val}`;
        });
        rows += row + "\n";
    }
    
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    downloadCSV(`trace_export_${timestamp}.csv`, header + rows);
}

// Export Histogram CSV
function exportHistogramCSV() {
    if (!coincidenceHistChart) return;
    const labels = coincidenceHistChart.data.labels || []; // ns delays
    const data = coincidenceHistChart.data.datasets[0].data || [];
    
    let csv = "Delay (ns),Coincidences\n";
    for (let i = 0; i < labels.length; i++) {
        csv += `${labels[i]},${data[i]}\n`;
    }
    
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    downloadCSV(`histogram_export_${timestamp}.csv`, csv);
}

// Render dynamic JSI Heatmap Grid
function renderJsiHeatmap(jsiData) {
    const channels = jsiData.channels;
    const matrix = jsiData.matrix;
    
    if (!jsiHeatmap) return;
    
    const n_ch = channels.length;
    if (n_ch === 0 || !matrix || matrix.length === 0) {
        jsiHeatmap.innerHTML = "<div class='no-data-msg' style='color: var(--text-muted); font-style: italic; padding: 20px; text-align: center; width: 100%; grid-column: 1 / -1;'>No active JSI channels. Select multiple channels in configuration.</div>";
        return;
    }
    
    // Set up CSS Grid columns: 1 column for row labels + n_ch columns for cells
    jsiHeatmap.style.gridTemplateColumns = `40px repeat(${n_ch}, 1fr)`;
    jsiHeatmap.innerHTML = "";
    
    console.log("renderJsiHeatmap starting. disabledPixels Set content:", Array.from(disabledPixels));
    
    // 1. Column Headers (Click / Stop Channels)
    // Corner empty cell
    const corner = document.createElement("div");
    corner.className = "jsi-header-cell jsi-corner";
    corner.innerHTML = "";
    jsiHeatmap.appendChild(corner);
    
    for (let j = 0; j < n_ch; j++) {
        const colHeader = document.createElement("div");
        colHeader.className = "jsi-header-cell jsi-col-header";
        colHeader.innerText = channels[j];
        colHeader.title = `Stop Channel ${channels[j]} (${currentConfig.channel_aliases?.[channels[j]] || ''})`;
        jsiHeatmap.appendChild(colHeader);
    }
    
    // Find min and max value for normalization (excluding disabled cells)
    let maxVal = 0;
    let minVal = Infinity;
    
    for (let i = 0; i < n_ch; i++) {
        for (let j = 0; j < n_ch; j++) {
            const val = matrix[i][j];
            const ch1 = channels[i];
            const ch2 = channels[j];
            const key = `${ch1},${ch2}`;
            
            if (i === 0 && j === 1) {
                console.log("JSI check: key =", key, "type of key =", typeof key, "has key =", disabledPixels.has(key));
            }
            
            if (!disabledPixels.has(key)) {
                if (val > maxVal) maxVal = val;
                if (val < minVal) minVal = val;
            }
        }
    }
    
    if (minVal === Infinity) minVal = 0;
    
    // If manual scaling is selected
    if (jsiColorAuto && !jsiColorAuto.checked) {
        const manualMax = parseFloat(jsiColorMax.value);
        if (!isNaN(manualMax) && manualMax > 0) {
            maxVal = manualMax;
        }
    }
    
    // 2. Rows
    for (let i = 0; i < n_ch; i++) {
        const ch1 = channels[i];
        
        // Row header label (Start Channel)
        const rowHeader = document.createElement("div");
        rowHeader.className = "jsi-header-cell jsi-row-header";
        rowHeader.innerText = ch1;
        rowHeader.title = `Start Channel ${ch1} (${currentConfig.channel_aliases?.[ch1] || ''})`;
        jsiHeatmap.appendChild(rowHeader);
        
        // Cells
        for (let j = 0; j < n_ch; j++) {
            const ch2 = channels[j];
            const val = matrix[i][j];
            const key = `${ch1},${ch2}`;
            const isDisabled = disabledPixels.has(key);
            
            const cell = document.createElement("div");
            cell.className = "jsi-cell";
            if (isDisabled) {
                cell.classList.add("disabled-pixel");
            }
            
            // Calculate color intensity
            let percent = 0;
            if (maxVal > minVal && !isDisabled) {
                percent = Math.min(1.0, Math.max(0.0, (val - minVal) / (maxVal - minVal)));
            }
            
            if (!isDisabled) {
                // Hue from 220 (cool blue) to 0 (vibrant red)
                const hue = 220 - percent * 220;
                cell.style.backgroundColor = `hsl(${hue}, 95%, ${35 + percent * 20}%)`;
                cell.style.color = percent > 0.6 ? "#ffffff" : "#cbd5e1";
            } else {
                cell.style.backgroundColor = "transparent";
            }
            
            cell.title = `Start: Ch ${ch1} (${currentConfig.channel_aliases?.[ch1] || 'Ch ' + ch1})\nStop: Ch ${ch2} (${currentConfig.channel_aliases?.[ch2] || 'Ch ' + ch2})\nCoincidences: ${val.toLocaleString()}${isDisabled ? ' (Hidden)' : ''}`;
            
            // Add click listener to toggle pixel visibility
            cell.addEventListener("click", () => {
                if (disabledPixels.has(key)) {
                    disabledPixels.delete(key);
                } else {
                    disabledPixels.add(key);
                }
                console.log("Toggled pixel key:", key, "type of key =", typeof key, "disabledPixels now:", Array.from(disabledPixels));
                localStorage.setItem("jsi_disabled_pixels", JSON.stringify(Array.from(disabledPixels)));
                renderJsiHeatmap(jsiData);
            });
            
            jsiHeatmap.appendChild(cell);
        }
    }
}

// Export JSI Matrix to CSV
function exportJsiCSV() {
    if (!lastJsiData || !lastJsiData.matrix || lastJsiData.matrix.length === 0) return;
    
    const channels = lastJsiData.channels;
    const matrix = lastJsiData.matrix;
    const aliases = channels.map(ch => currentConfig.channel_aliases?.[ch] || `Channel ${ch}`);
    
    let csv = "";
    // Header row
    csv += "," + aliases.map(a => `"${a}"`).join(",") + "\n";
    
    // Data rows
    for (let i = 0; i < channels.length; i++) {
        csv += `"${aliases[i]}",` + matrix[i].join(",") + "\n";
    }
    
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    downloadCSV(`jsi_export_${timestamp}.csv`, csv);
}

// Theme management
function setupTheme() {
    const savedTheme = localStorage.getItem("scientific-theme") || "light";
    if (savedTheme === "dark") {
        document.body.classList.add("dark-theme");
    } else {
        document.body.classList.remove("dark-theme");
    }
    applyThemeToCharts();
    
    themeToggleBtn.addEventListener("click", () => {
        const isDark = document.body.classList.toggle("dark-theme");
        localStorage.setItem("scientific-theme", isDark ? "dark" : "light");
        applyThemeToCharts();
    });
}

function applyThemeToCharts() {
    const isDark = document.body.classList.contains("dark-theme");
    const gridColor = isDark ? "#334155" : "#e2e8f0";
    const labelColor = isDark ? "#cbd5e1" : "#475569";
    
    [ratesBarChart, traceLineChart, coincidenceHistChart].forEach(chart => {
        if (chart) {
            // Update grid colors
            if (chart.options.scales.x && chart.options.scales.x.grid) {
                chart.options.scales.x.grid.color = gridColor;
            }
            if (chart.options.scales.x && chart.options.scales.x.ticks) {
                chart.options.scales.x.ticks.color = labelColor;
            }
            if (chart.options.scales.x && chart.options.scales.x.title) {
                chart.options.scales.x.title.color = labelColor;
            }
            
            if (chart.options.scales.y && chart.options.scales.y.grid) {
                chart.options.scales.y.grid.color = gridColor;
            }
            if (chart.options.scales.y && chart.options.scales.y.ticks) {
                chart.options.scales.y.ticks.color = labelColor;
            }
            if (chart.options.scales.y && chart.options.scales.y.title) {
                chart.options.scales.y.title.color = labelColor;
            }
            
            // Legend
            if (chart.options.plugins && chart.options.plugins.legend && chart.options.plugins.legend.labels) {
                chart.options.plugins.legend.labels.color = labelColor;
            }
            chart.update("none");
        }
    });
}

// Config Import / Export Setup
function setupConfigUtilities() {
    exportConfigBtn.addEventListener("click", () => {
        const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(currentConfig, null, 4));
        const downloadAnchor = document.createElement('a');
        downloadAnchor.setAttribute("href", dataStr);
        downloadAnchor.setAttribute("download", "timetagger_config.json");
        document.body.appendChild(downloadAnchor);
        downloadAnchor.click();
        downloadAnchor.remove();
    });
    
    importConfigBtn.addEventListener("click", () => {
        importConfigFile.click();
    });
    
    importConfigFile.addEventListener("change", (e) => {
        const file = e.target.files[0];
        if (!file) return;
        
        const reader = new FileReader();
        reader.onload = (evt) => {
            try {
                const parsed = JSON.parse(evt.target.result);
                if (parsed.active_channels && parsed.channel_aliases && parsed.trigger_voltages) {
                    sendWebSocketMsg({
                        type: "save_config",
                        config: parsed
                    });
                    alert("Configuration imported successfully!");
                } else {
                    alert("Invalid config file structure. Missing required properties.");
                }
            } catch (err) {
                alert("Failed to parse JSON configuration file.");
            }
        };
        reader.readAsText(file);
        importConfigFile.value = "";
    });
}

// Sizing & Visible panels setup
function setupDisplayCustomizations() {
    // Visibility toggles
    const visibilityKeys = ["showRatesChart", "showTraceChart", "showHistChart", "showJsiChart"];
    const visibilityControls = [showRatesChart, showTraceChart, showHistChart, showJsiChart];
    
    visibilityControls.forEach((ctrl, idx) => {
        const key = visibilityKeys[idx];
        const savedVal = localStorage.getItem(key);
        if (savedVal !== null) {
            ctrl.checked = savedVal === "true";
        }
        
        ctrl.addEventListener("change", () => {
            localStorage.setItem(key, ctrl.checked);
            adjustDashboardGrid();
            setTimeout(() => {
                if (ratesBarChart) ratesBarChart.resize();
                if (traceLineChart) traceLineChart.resize();
                if (coincidenceHistChart) coincidenceHistChart.resize();
            }, 50);
        });
    });
    
    // Resizable chart wrappers height load & Observer
    const resizableWrappers = [
        { wrapper: wrapperBarChart, chart: () => ratesBarChart, key: "barChartHeight" },
        { wrapper: wrapperTraceChart, chart: () => traceLineChart, key: "traceChartHeight" },
        { wrapper: wrapperHistChart, chart: () => coincidenceHistChart, key: "histChartHeight" },
        { wrapper: document.getElementById("wrapper-jsi-matrix"), chart: () => null, key: "jsiMatrixHeight" }
    ];
    
    resizableWrappers.forEach(({ wrapper, chart, key }) => {
        if (!wrapper) return;
        
        // Load saved height
        const savedHeight = localStorage.getItem(key) || "250";
        wrapper.style.height = `${savedHeight}px`;
        
        // Listen to size changes (with ResizeObserver) and save
        let resizeTimeout;
        const observer = new ResizeObserver(() => {
            const c = chart();
            if (c) {
                c.resize();
            }
            
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(() => {
                localStorage.setItem(key, wrapper.clientHeight);
            }, 500);
        });
        observer.observe(wrapper);
    });
    
    // Initial display adjustment
    adjustDashboardGrid();
}

// Initialize on load
window.addEventListener("DOMContentLoaded", () => {
    setupTabs();
    restorePanelOrder();
    initScientificCharts();
    setupDisplayCustomizations();
    makePanelsDraggable();
    setupTheme();
    setupConfigUtilities();
    setupGlobalEvents();
    connectWebSocket();
});

function savePanelOrder() {
    const container = document.querySelector(".dash-layout");
    if (!container) return;
    const order = Array.from(container.children).map(child => child.id);
    localStorage.setItem("dash_panel_order", JSON.stringify(order));
}

function restorePanelOrder() {
    const container = document.querySelector(".dash-layout");
    if (!container) return;
    const saved = localStorage.getItem("dash_panel_order");
    if (saved) {
        try {
            const order = JSON.parse(saved);
            order.forEach(id => {
                const el = document.getElementById(id);
                if (el && el.parentNode === container) {
                    container.appendChild(el);
                }
            });
        } catch (e) {
            console.error("Failed to restore panel order:", e);
        }
    }
}

function makePanelsDraggable() {
    const panels = document.querySelectorAll(".dash-layout .panel");
    const container = document.querySelector(".dash-layout");
    if (!container) return;
    
    panels.forEach(panel => {
        const header = panel.querySelector(".panel-header");
        if (!header) return;
        
        header.style.cursor = "move";
        
        header.addEventListener("mousedown", () => {
            panel.setAttribute("draggable", "true");
        });
        
        header.addEventListener("mouseup", () => {
            panel.removeAttribute("draggable");
        });
        
        panel.addEventListener("dragstart", (e) => {
            e.dataTransfer.setData("text/plain", panel.id);
            panel.classList.add("dragging");
            e.dataTransfer.effectAllowed = "move";
        });
        
        panel.addEventListener("dragend", () => {
            panel.classList.remove("dragging");
            panel.removeAttribute("draggable");
            panels.forEach(p => p.classList.remove("drag-over"));
        });
        
        panel.addEventListener("dragover", (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
        });
        
        panel.addEventListener("dragenter", (e) => {
            e.preventDefault();
            const targetPanel = e.target.closest(".panel");
            if (targetPanel && targetPanel.id !== panel.id) {
                targetPanel.classList.add("drag-over");
            }
        });
        
        panel.addEventListener("dragleave", (e) => {
            const targetPanel = e.target.closest(".panel");
            if (targetPanel) {
                targetPanel.classList.remove("drag-over");
            }
        });
        
        panel.addEventListener("drop", (e) => {
            e.preventDefault();
            const targetPanel = e.target.closest(".panel");
            if (targetPanel) {
                targetPanel.classList.remove("drag-over");
            }
            
            const draggedId = e.dataTransfer.getData("text/plain");
            const draggedPanel = document.getElementById(draggedId);
            
            if (draggedPanel && draggedPanel !== targetPanel && targetPanel) {
                const children = Array.from(container.children);
                const draggedIdx = children.indexOf(draggedPanel);
                const targetIdx = children.indexOf(targetPanel);
                
                if (draggedIdx < targetIdx) {
                    container.insertBefore(draggedPanel, targetPanel.nextSibling);
                } else {
                    container.insertBefore(draggedPanel, targetPanel);
                }
                savePanelOrder();
            }
        });
    });
}
