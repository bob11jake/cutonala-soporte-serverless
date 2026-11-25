let chartEstados = null;
let chartCategorias = null;

function renderizarGraficas(tickets) {
  const ctxEst = document.getElementById("chart-estados");
  const ctxCat = document.getElementById("chart-categorias");
  if (!ctxEst || !ctxCat || typeof Chart === "undefined") return;

  const conteoEstados = {
    ABIERTO: tickets.filter(t => t.estado === "ABIERTO").length,
    EN_PROGRESO: tickets.filter(t => t.estado === "EN_PROGRESO").length,
    EN_REVISION: tickets.filter(t => t.estado === "EN_REVISION").length,
    RESUELTO: tickets.filter(t => t.estado === "RESUELTO").length,
  };

  const categorias = ["ELECTRICIDAD", "AGUA", "SEGURIDAD", "INFRAESTRUCTURA", "CONECTIVIDAD"];
  const conteoCats = categorias.map(c => tickets.filter(t => t.categoria === c).length);

  if (chartEstados) chartEstados.destroy();
  chartEstados = new Chart(ctxEst, {
    type: "doughnut",
    data: {
      labels: ["Abiertos", "En Progreso", "En Revision", "Resueltos"],
      datasets: [{
        data: Object.values(conteoEstados),
        backgroundColor: ["#2563eb", "#f59e0b", "#8b5cf6", "#16a34a"],
      }],
    },
    options: { responsive: true, plugins: { legend: { position: "bottom" } } },
  });

  if (chartCategorias) chartCategorias.destroy();
  chartCategorias = new Chart(ctxCat, {
    type: "bar",
    data: {
      labels: categorias,
      datasets: [{
        label: "Reportes por Area del Campus",
        data: conteoCats,
        backgroundColor: "#1d4ed8",
        borderRadius: 6,
      }],
    },
    options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } },
  });
}