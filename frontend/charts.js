let chartEstados = null;
let chartCategorias = null;

const CATEGORIAS = ["ELECTRICIDAD", "AGUA", "SEGURIDAD", "INFRAESTRUCTURA", "CONECTIVIDAD"];

// Recibe los contadores globales de /metricas (c_<CATEGORIA>, e_<ESTADO>).
function renderizarGraficas(c) {
  window.ultimosContadores = c;
  const ctxEst = document.getElementById("chart-estados");
  const ctxCat = document.getElementById("chart-categorias");
  // Si el CDN de Chart.js no carga, la aplicacion sigue funcionando sin graficas.
  if (!ctxEst || !ctxCat || typeof Chart === "undefined") {
    document.getElementById("analytics-section")?.classList.add("sin-graficas");
    return;
  }
  document.getElementById("analytics-section")?.classList.remove("sin-graficas");

  if (chartEstados) chartEstados.destroy();
  chartEstados = new Chart(ctxEst, {
    type: "doughnut",
    data: {
      labels: ["Abiertos", "En Progreso", "En Revision", "Resueltos"],
      datasets: [{
        data: [c.e_ABIERTO || 0, c.e_EN_PROGRESO || 0, c.e_EN_REVISION || 0, c.e_RESUELTO || 0],
        backgroundColor: ["#2563eb", "#f59e0b", "#8b5cf6", "#16a34a"],
      }],
    },
    options: { responsive: true, plugins: { legend: { position: "bottom" } } },
  });

  if (chartCategorias) chartCategorias.destroy();
  chartCategorias = new Chart(ctxCat, {
    type: "bar",
    data: {
      labels: CATEGORIAS,
      datasets: [{
        label: "Reportes por Area del Campus",
        data: CATEGORIAS.map(cat => c[`c_${cat}`] || 0),
        backgroundColor: "#1d4ed8",
        borderRadius: 6,
      }],
    },
    options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } },
  });
}
