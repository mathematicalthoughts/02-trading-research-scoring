// Dashboard: toggle del rail en mobile, charts (Chart.js) y el fetch de
// "Explicar" contra GET /api/scores/<symbol>/explain/ -- la ÚNICA
// llamada HTTP interna de todo el frontend (ver CLAUDE.md, "Frontend").

(function () {
  var toggle = document.getElementById("railToggle");
  var rail = document.getElementById("rail");
  if (toggle && rail) {
    toggle.addEventListener("click", function () {
      var isOpen = rail.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
  }
})();

function readJsonScript(id) {
  var el = document.getElementById(id);
  if (!el) {
    return null;
  }
  return JSON.parse(el.textContent);
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function initPriceChart(canvasId, dataScriptId) {
  var data = readJsonScript(dataScriptId);
  var canvas = document.getElementById(canvasId);
  if (!data || !canvas || typeof Chart === "undefined") {
    return;
  }

  new Chart(canvas, {
    type: "line",
    data: {
      labels: data.labels,
      datasets: [
        {
          label: "Cierre",
          data: data.close,
          borderColor: cssVar("--color-chart-price"),
          backgroundColor: "transparent",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.15,
        },
        {
          label: "SMA20",
          data: data.sma20,
          borderColor: cssVar("--color-chart-sma20"),
          backgroundColor: "transparent",
          borderWidth: 1.5,
          borderDash: [4, 3],
          pointRadius: 0,
          spanGaps: true,
          tension: 0.15,
        },
        {
          label: "SMA50",
          data: data.sma50,
          borderColor: cssVar("--color-chart-sma50"),
          backgroundColor: "transparent",
          borderWidth: 1.5,
          pointRadius: 0,
          spanGaps: true,
          tension: 0.15,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: { color: cssVar("--color-text-muted"), maxTicksLimit: 8 }, grid: { display: false } },
        y: { ticks: { color: cssVar("--color-text-muted") }, grid: { color: cssVar("--color-border") } },
      },
      plugins: {
        legend: { display: false },
      },
    },
  });
}

function initBacktestChart(canvasId, dataScriptId) {
  var data = readJsonScript(dataScriptId);
  var canvas = document.getElementById(canvasId);
  if (!data || !canvas || typeof Chart === "undefined") {
    return;
  }

  var bullish = cssVar("--color-bullish");
  var bearish = cssVar("--color-bearish");
  var colors = data.avg_return_pct.map(function (value) {
    return value >= 0 ? bullish : bearish;
  });

  new Chart(canvas, {
    type: "bar",
    data: {
      labels: data.labels,
      datasets: [
        {
          label: "Retorno promedio (%)",
          data: data.avg_return_pct,
          backgroundColor: colors,
          borderRadius: 4,
          maxBarThickness: 56,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: cssVar("--color-text-muted") }, grid: { display: false } },
        y: {
          ticks: { color: cssVar("--color-text-muted") },
          grid: { color: cssVar("--color-border") },
          grace: "10%",
        },
      },
      plugins: {
        legend: { display: false },
      },
    },
  });
}

// --- Agente explicador ---

function explainScore(symbol, opts) {
  var button = document.getElementById(opts.buttonId);
  var statusEl = document.getElementById(opts.statusId);
  var resultEl = document.getElementById(opts.resultId);

  if (!button || !statusEl || !resultEl) {
    return;
  }

  button.addEventListener("click", function () {
    button.disabled = true;
    statusEl.innerHTML =
      '<span class="spinner" aria-hidden="true"></span> ' +
      "Consultando al agente (el tool-calling real puede tardar varios segundos)…";
    statusEl.className = "agent-status";
    resultEl.innerHTML = "";

    fetch("/api/scores/" + encodeURIComponent(symbol) + "/explain/")
      .then(function (response) {
        return response.json().then(function (body) {
          return { status: response.status, body: body };
        });
      })
      .then(function (result) {
        button.disabled = false;

        if (result.status === 200) {
          statusEl.textContent = "";
          resultEl.innerHTML = renderExplanation(result.body);
          return;
        }

        if (result.status === 429) {
          statusEl.textContent =
            "Alcanzaste el límite de consultas al agente (5 por hora). Probá de nuevo más tarde.";
          statusEl.className = "agent-status agent-status--error";
          return;
        }

        if (result.status === 502) {
          statusEl.textContent =
            "El agente no pudo responder: " + (result.body.detail || "error de la API de Gemini.");
          statusEl.className = "agent-status agent-status--error";
          return;
        }

        if (result.status === 404) {
          statusEl.textContent = result.body.detail || "Todavía no hay un score calculado para este ticker.";
          statusEl.className = "agent-status agent-status--error";
          return;
        }

        statusEl.textContent = "No se pudo obtener una explicación (error inesperado).";
        statusEl.className = "agent-status agent-status--error";
      })
      .catch(function () {
        button.disabled = false;
        statusEl.textContent = "No se pudo conectar con el servidor. Revisá tu conexión e intentá de nuevo.";
        statusEl.className = "agent-status agent-status--error";
      });
  });
}

function renderExplanation(explanation) {
  var toolsHtml = (explanation.tool_calls || [])
    .map(function (call) {
      return (
        '<span class="agent-trace-item">' +
        escapeHtml(call.tool) +
        "(" +
        escapeHtml(JSON.stringify(call.args)) +
        ")</span>"
      );
    })
    .join("");

  return (
    '<div class="agent-box">' +
    '<p class="agent-text">' + escapeHtml(explanation.texto) + "</p>" +
    '<div class="mono">' + (toolsHtml || '<span class="agent-status">(sin tools invocadas)</span>') + "</div>" +
    "</div>"
  );
}

function escapeHtml(value) {
  var div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}
