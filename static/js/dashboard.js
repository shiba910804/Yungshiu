(function () {
  "use strict";

  const state = {
    fundPeriod: "all",
    fxPeriod: "all",
    navChart: null,
    fxChart: null,
  };

  function formatNumber(value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "--";
    }
    return Number(value).toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function formatPercent(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      return "--";
    }
    const sign = Number(value) > 0 ? "+" : "";
    return `${sign}${Number(value).toFixed(2)}%`;
  }

  function setSummary(targetId, summary, digits) {
    const box = document.getElementById(targetId);
    if (!box) return;
    const values = box.querySelectorAll("strong");
    values[0].textContent = formatNumber(summary.latest, digits);
    values[1].textContent = formatNumber(summary.max, digits);
    values[2].textContent = formatNumber(summary.min, digits);
    values[3].textContent = formatPercent(summary.change_pct);
    values[3].classList.toggle("positive", Number(summary.change_pct) > 0);
    values[3].classList.toggle("negative", Number(summary.change_pct) < 0);
  }

  function setActivePeriod(chartName, period) {
    document
      .querySelectorAll(`.range-group[data-chart="${chartName}"] .range-btn`)
      .forEach((button) => {
        button.classList.toggle("active", button.dataset.period === period);
      });
  }

  function showChart(loadingId, panelSelector) {
    const loading = document.getElementById(loadingId);
    const wrap = document.querySelector(`${panelSelector} .chart-wrap`);
    if (loading) loading.classList.add("hidden");
    if (wrap) wrap.classList.remove("hidden");
  }

  function makeChart(canvasId, color, fill) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined") return null;
    return new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            data: [],
            borderColor: color,
            backgroundColor: fill,
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 10,
            tension: 0.18,
            fill: true,
          },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#101827",
            padding: 11,
            displayColors: false,
            callbacks: {
              title: (items) => items[0].label,
              label: (item) => ` ${formatNumber(item.raw, 4)}`,
            },
          },
        },
        scales: {
          x: {
            ticks: { maxTicksLimit: 8, autoSkip: true, font: { size: 11 } },
            grid: { display: false },
            border: { color: "#d9e0ea" },
          },
          y: {
            ticks: { font: { size: 11 } },
            grid: { color: "#e9edf3" },
            border: { display: false },
          },
        },
      },
    });
  }

  function updateChart(chart, payload, label) {
    if (!chart) return;
    chart.data.labels = payload.labels || [];
    chart.data.datasets[0].data = payload.data || [];
    chart.data.datasets[0].label = label;
    chart.update("none");
  }

  async function fetchJson(url) {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }
    return response.json();
  }

  async function initFundChart() {
    const selector = document.getElementById("fundSelector");
    if (!selector) return;
    const funds = await fetchJson("/api/fund_list");
    selector.innerHTML = "";
    funds.forEach((fund) => {
      const option = document.createElement("option");
      option.value = fund.id;
      option.textContent = `${fund.name} (${fund.id})`;
      selector.appendChild(option);
    });

    state.navChart = makeChart("navCanvas", "#2563eb", "rgba(37, 99, 235, 0.08)");
    if (funds.length) {
      selector.value = funds[0].id;
      await loadFundNav(funds[0].id);
    }

    selector.addEventListener("change", () => {
      if (selector.value) loadFundNav(selector.value);
    });

    document.querySelectorAll('.range-group[data-chart="fund"] .range-btn').forEach((button) => {
      button.addEventListener("click", () => {
        state.fundPeriod = button.dataset.period;
        setActivePeriod("fund", state.fundPeriod);
        if (selector.value) loadFundNav(selector.value);
      });
    });
  }

  async function loadFundNav(fundId) {
    const payload = await fetchJson(
      `/api/fund_nav/${encodeURIComponent(fundId)}?period=${encodeURIComponent(state.fundPeriod)}`
    );
    showChart("navLoading", "#nav-chart");
    updateChart(state.navChart, payload, payload.name || fundId);
    setSummary("fundSummary", payload.summary || {}, 2);
  }

  async function initFxChart() {
    const selector = document.getElementById("fxSelector");
    if (!selector) return;
    const currencies = await fetchJson("/api/currency_list");
    selector.innerHTML = "";
    currencies.forEach((currency) => {
      const option = document.createElement("option");
      option.value = currency;
      option.textContent = currency;
      selector.appendChild(option);
    });

    state.fxChart = makeChart("fxCanvas", "#15803d", "rgba(21, 128, 61, 0.08)");
    if (currencies.length) {
      selector.value = currencies.includes("USD") ? "USD" : currencies[0];
      await loadFxRate(selector.value);
    }

    selector.addEventListener("change", () => {
      if (selector.value) loadFxRate(selector.value);
    });

    document.querySelectorAll('.range-group[data-chart="fx"] .range-btn').forEach((button) => {
      button.addEventListener("click", () => {
        state.fxPeriod = button.dataset.period;
        setActivePeriod("fx", state.fxPeriod);
        if (selector.value) loadFxRate(selector.value);
      });
    });
  }

  async function loadFxRate(currency) {
    const payload = await fetchJson(
      `/api/historical_fx/${encodeURIComponent(currency)}?period=${encodeURIComponent(state.fxPeriod)}`
    );
    showChart("fxLoading", "#hfx-chart");
    updateChart(state.fxChart, payload, `1 ${currency} to TWD`);
    setSummary("fxSummary", payload.summary || {}, 4);
  }

  function initScrollSpy() {
    const links = Array.from(document.querySelectorAll(".side-nav a[href^='#']"));
    if (!links.length) return;

    const linkById = new Map();
    links.forEach((link) => {
      const id = decodeURIComponent(link.getAttribute("href").slice(1));
      const section = document.getElementById(id);
      if (section) linkById.set(id, link);
    });

    function setActive(id) {
      links.forEach((link) => link.classList.remove("active"));
      const active = linkById.get(id);
      if (active) active.classList.add("active");
    }

    const visible = new Map();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            visible.set(entry.target.id, entry.intersectionRatio);
          } else {
            visible.delete(entry.target.id);
          }
        });

        if (!visible.size) return;
        const [activeId] = Array.from(visible.entries()).sort((a, b) => b[1] - a[1])[0];
        setActive(activeId);
      },
      {
        root: null,
        rootMargin: "-18% 0px -58% 0px",
        threshold: [0.1, 0.25, 0.5, 0.75],
      }
    );

    linkById.forEach((_, id) => {
      const section = document.getElementById(id);
      if (section) observer.observe(section);
    });

    const currentHash = window.location.hash.replace("#", "");
    if (currentHash && linkById.has(currentHash)) {
      setActive(currentHash);
    } else {
      setActive(linkById.keys().next().value);
    }
  }

  initFundChart().catch((error) => {
    console.error(error);
    const loading = document.getElementById("navLoading");
    if (loading) loading.textContent = "基金圖表載入失敗";
  });

  initFxChart().catch((error) => {
    console.error(error);
    const loading = document.getElementById("fxLoading");
    if (loading) loading.textContent = "匯率圖表載入失敗";
  });

  initScrollSpy();
})();
