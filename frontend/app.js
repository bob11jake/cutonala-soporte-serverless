let ticketsCache = [];

// Todo texto que viene del usuario se escapa antes de insertarlo en el HTML (previene XSS).
function esc(valor) {
  return String(valor ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Las URLs prefirmadas cambian en cada consulta; reutilizar la primera (valida 1 h)
// permite que el navegador use su cache y no descargue la misma foto en cada refresco.
const cacheUrls = new Map();
function urlEvidencia(ev) {
  const previa = cacheUrls.get(ev.key);
  if (previa && Date.now() - previa.t < 50 * 60 * 1000) return previa.url;
  cacheUrls.set(ev.key, { url: ev.url, t: Date.now() });
  return ev.url;
}

const esStaff = () => ["ADMIN", "TECNICO"].includes(ApiClient.usuario?.rol);

function manejarError(err) {
  if (err.sesionExpirada) {
    alert(err.message);
    cerrarSesion();
    return;
  }
  alert(err.message);
}

function actualizarVistaPrincipal() {
  const portalView = document.getElementById("portal-view");
  const appView = document.getElementById("app-view");
  const u = ApiClient.usuario;

  ApiClient.verificarSalud();

  if (!u) {
    portalView.style.display = "block";
    appView.style.display = "none";
    return;
  }

  portalView.style.display = "none";
  appView.style.display = "block";

  document.getElementById("user-session-info").innerHTML = `
    Sesion activa: <strong>${esc(u.nombre)}</strong>
    <span class="badge badge-cat">${esc(u.rol)}</span>
    ${u.isGuest ? '<small style="color:#64748b;">(Acceso rapido sin contrasena)</small>' : ""}
  `;

  document.getElementById("analytics-section").style.display = esStaff() ? "grid" : "none";
  document.getElementById("audit-section").style.display = esStaff() ? "block" : "none";

  cargarTickets();
  if (esStaff()) {
    cargarMetricas();
    cargarAuditoria();
  }
}

document.getElementById("form-guest").addEventListener("submit", (e) => {
  e.preventDefault();
  const nombre = document.getElementById("guest-name").value.trim();
  if (nombre.length < 2) return alert("Ingresa tu nombre para continuar");
  ApiClient.setGuestSession(nombre);
  e.target.reset();
  actualizarVistaPrincipal();
});

document.getElementById("form-staff-login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("login-email").value.trim();
  const password = document.getElementById("login-pass").value;
  try {
    const res = await ApiClient.request("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }, 0);
    ApiClient.setSession(res.token, res.usuario);
    e.target.reset();
    actualizarVistaPrincipal();
  } catch (err) {
    alert("Error de acceso: " + err.message);
  }
});

function rellenarCredenciales(email, pass) {
  document.getElementById("login-email").value = email;
  document.getElementById("login-pass").value = pass;
}

function cerrarSesion() {
  ApiClient.clearSession();
  actualizarVistaPrincipal();
}

// ------------------------------------------------------------------ KPIs

async function cargarMetricas() {
  try {
    const { contadores: c } = await ApiClient.request("/metricas");
    document.getElementById("kpi-total").textContent = c.total || 0;
    document.getElementById("kpi-abierto").textContent = c.e_ABIERTO || 0;
    document.getElementById("kpi-progreso").textContent = (c.e_EN_PROGRESO || 0) + (c.e_EN_REVISION || 0);
    renderizarGraficas(c);
  } catch (err) {
    if (err.sesionExpirada) manejarError(err);
  }
}

function kpisDesdeLista(tickets) {
  // Para la comunidad (sin acceso a /metricas) los KPIs se calculan con lo visible.
  document.getElementById("kpi-total").textContent = tickets.length;
  document.getElementById("kpi-abierto").textContent = tickets.filter(t => t.estado === "ABIERTO").length;
  document.getElementById("kpi-progreso").textContent =
    tickets.filter(t => t.estado === "EN_PROGRESO" || t.estado === "EN_REVISION").length;
}

// --------------------------------------------------------------- tickets

async function cargarTickets() {
  const estado = document.getElementById("filtro-estado").value;
  const categoria = document.getElementById("filtro-categoria").value;
  const contenedor = document.getElementById("lista-tickets");

  let query = "";
  if (estado) query = `?estado=${encodeURIComponent(estado)}`;
  else if (categoria) query = `?categoria=${encodeURIComponent(categoria)}`;

  try {
    const data = await ApiClient.request(query);
    ticketsCache = data.tickets || [];
    if (!estado && !categoria) {
      document.getElementById("kpi-vencidos").textContent = ticketsCache.filter(t => t.sla_estado === "VENCIDO").length;
      if (!esStaff()) kpisDesdeLista(ticketsCache);
    }
    renderizarTickets();
  } catch (err) {
    if (err.sesionExpirada) return manejarError(err);
    contenedor.innerHTML = `<p class="alerta-resiliencia">Alerta de Resiliencia Activa: ${esc(err.message)}</p>`;
  }
}

function renderizarTickets() {
  const busqueda = document.getElementById("filtro-texto").value.toLowerCase().trim();
  const contenedor = document.getElementById("lista-tickets");
  const filtrados = ticketsCache.filter(t =>
    t.titulo.toLowerCase().includes(busqueda) || t.ticket_id.toLowerCase().includes(busqueda)
  );

  if (filtrados.length === 0) {
    contenedor.innerHTML = "<p>No se encontraron incidencias con los filtros actuales.</p>";
    return;
  }

  const rol = ApiClient.usuario?.rol || "";
  const nombre = ApiClient.usuario?.nombre || "";

  contenedor.innerHTML = filtrados.map(t => {
    const id = esc(t.ticket_id);
    const evidencias = (t.evidencias || []).filter(e => e.url);
    return `
      <div class="ticket ${esc(t.prioridad)}">
        <div class="ticket-header">
          <div class="ticket-title">
            <span class="ticket-id">#${id}</span>${esc(t.titulo)}
          </div>
          <div class="badges">
            <span class="badge badge-cat">${esc(t.categoria)}</span>
            <span class="badge badge-prio-${esc(t.prioridad)}">${esc(t.prioridad)}</span>
            <span class="badge">${esc(t.estado)}</span>
            <span class="badge sla-${esc(t.sla_estado)}">SLA: ${esc(t.sla_estado)}</span>
          </div>
        </div>
        <p class="ticket-desc">${esc(t.descripcion)}</p>
        <div class="meta">Reportado por: <strong>${esc(t.solicitante || "Sistema")}</strong>
          | Ultimo evento: ${esc(t.historial?.slice(-1)[0]?.evento || "Creado")}</div>
        ${evidencias.length ? `<div class="evidencias">${evidencias.map(e =>
          `<a href="${esc(urlEvidencia(e))}" target="_blank" rel="noopener"><img src="${esc(urlEvidencia(e))}" alt="Evidencia del ticket ${id}" loading="lazy" /></a>`
        ).join("")}</div>` : ""}
        <div class="acciones">
          ${esStaff() && t.estado !== "RESUELTO" ? `<button data-accion="avanzar" data-id="${id}" data-estado="${esc(t.estado)}">Avanzar flujo</button>` : ""}
          ${rol === "ADMIN" ? `<button class="danger" data-accion="borrar" data-id="${id}">Eliminar</button>` : ""}
          ${(t.evidencias || []).length < 5 ? `<label class="outline boton-archivo">Adjuntar foto
            <input type="file" accept="image/jpeg,image/png,image/webp" data-accion="evidencia" data-id="${id}" hidden /></label>` : ""}
        </div>
        <div class="comments-box">
          <strong>Seguimiento y comentarios (${(t.comentarios || []).length}):</strong>
          ${(t.comentarios || []).map(c => `<div class="comment-item"><strong>${esc(c.autor)}</strong> (${esc(c.rol)}): ${esc(c.texto)}</div>`).join("")}
          <div class="comment-form">
            <input type="text" id="com-${id}" maxlength="1000" placeholder="Escribir comentario como ${esc(nombre)}..." />
            <button class="secondary" data-accion="comentar" data-id="${id}">Comentar</button>
          </div>
        </div>
      </div>`;
  }).join("");
}

// Delegacion de eventos: no se usan onclick con datos interpolados.
document.getElementById("lista-tickets").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-accion]");
  if (!btn) return;
  const { accion, id, estado } = btn.dataset;
  if (accion === "avanzar") avanzarEstado(id, estado);
  if (accion === "borrar") borrarTicket(id);
  if (accion === "comentar") enviarComentario(id);
});

document.getElementById("lista-tickets").addEventListener("change", async (e) => {
  const input = e.target.closest("input[data-accion='evidencia']");
  if (!input || !input.files.length) return;
  try {
    await ApiClient.subirEvidencia(input.dataset.id, input.files[0]);
    cargarTickets();
  } catch (err) {
    manejarError(err);
  }
});

document.getElementById("filtro-texto").addEventListener("input", renderizarTickets);

function refrescarTodo() {
  cargarTickets();
  if (esStaff()) {
    cargarMetricas();
    cargarAuditoria();
  }
}

document.getElementById("form-ticket").addEventListener("submit", async (e) => {
  e.preventDefault();
  const boton = e.target.querySelector("button[type=submit]");
  const archivo = document.getElementById("evidencia").files[0];
  boton.disabled = true;
  try {
    const res = await ApiClient.request("", {
      method: "POST",
      body: JSON.stringify({
        solicitante: ApiClient.usuario?.nombre || "usuario",
        titulo: document.getElementById("titulo").value,
        categoria: document.getElementById("categoria").value,
        prioridad: document.getElementById("prioridad").value,
        descripcion: document.getElementById("descripcion").value,
      }),
    }, 0); // crear no se reintenta automaticamente para no duplicar tickets
    if (archivo) {
      try {
        await ApiClient.subirEvidencia(res.ticket.ticket_id, archivo);
      } catch (err) {
        alert(`El ticket #${res.ticket.ticket_id} se creo, pero la foto no se pudo subir: ${err.message}`);
      }
    }
    e.target.reset();
    refrescarTodo();
  } catch (err) {
    manejarError(err);
  } finally {
    boton.disabled = false;
  }
});

async function avanzarEstado(id, actual) {
  const flujo = { ABIERTO: "EN_PROGRESO", EN_PROGRESO: "EN_REVISION", EN_REVISION: "RESUELTO" };
  const siguiente = flujo[actual] || "RESUELTO";
  const nota = prompt(`Nota de avance hacia ${siguiente}:`, "Diagnostico aplicado por soporte");
  if (nota === null) return;
  try {
    await ApiClient.request("", {
      method: "PUT",
      body: JSON.stringify({ ticket_id: id, estado: siguiente, nota }),
    });
    refrescarTodo();
  } catch (err) {
    manejarError(err);
  }
}

async function enviarComentario(id) {
  const input = document.getElementById(`com-${id}`);
  if (!input.value.trim()) return;
  try {
    await ApiClient.request("/tickets/comentarios", {
      method: "POST",
      body: JSON.stringify({ ticket_id: id, autor: ApiClient.usuario?.nombre || "usuario", texto: input.value }),
    }, 0);
    refrescarTodo();
  } catch (err) {
    manejarError(err);
  }
}

async function borrarTicket(id) {
  if (!confirm(`Eliminar permanentemente el ticket #${id}?`)) return;
  try {
    await ApiClient.request(`?ticket_id=${encodeURIComponent(id)}`, { method: "DELETE" });
    refrescarTodo();
  } catch (err) {
    manejarError(err);
  }
}

async function cargarAuditoria() {
  try {
    const data = await ApiClient.request("/auditoria");
    document.getElementById("audit-body").innerHTML = (data.auditoria || []).map(a => `
      <tr>
        <td>${esc(a.timestamp.replace("T", " ").slice(0, 19))} UTC</td>
        <td><strong>${esc(a.actor)}</strong> (${esc(a.rol)})</td>
        <td><span class="badge badge-cat">${esc(a.accion)}</span></td>
        <td>${esc(a.detalle)}</td>
      </tr>`).join("");
  } catch (err) {
    if (err.sesionExpirada) manejarError(err);
  }
}

function exportarCSV() {
  if (!ticketsCache.length) return alert("No hay datos para exportar");
  const celda = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const filas = [
    ["ID", "Titulo", "Categoria", "Prioridad", "Estado", "SLA", "Solicitante", "FechaCreacion"],
    ...ticketsCache.map(t => [t.ticket_id, t.titulo, t.categoria, t.prioridad, t.estado, t.sla_estado, t.solicitante, t.fecha_creacion]),
  ];
  const csv = "﻿" + filas.map(f => f.map(celda).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `reporte_cutonala_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
}

// Refresco automatico cada 60 s mientras la pestana esta visible.
setInterval(() => {
  if (ApiClient.usuario && document.visibilityState === "visible") refrescarTodo();
}, 60000);

actualizarVistaPrincipal();
