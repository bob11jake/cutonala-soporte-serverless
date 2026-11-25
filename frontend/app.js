let ticketsCache = [];

function actualizarVistaPrincipal() {
  const portalView = document.getElementById("portal-view");
  const appView = document.getElementById("app-view");
  const u = ApiClient.usuario;

  if (!u) {
    portalView.style.display = "block";
    appView.style.display = "none";
    ApiClient.request("").catch(() => {});
    return;
  }

  portalView.style.display = "none";
  appView.style.display = "block";

  const esStaff = u.rol === "ADMIN" || u.rol === "TECNICO";
  document.getElementById("user-session-info").innerHTML = `
    Sesion activa: <strong>${u.nombre}</strong>
    <span class="badge badge-cat">${u.rol}</span>
    ${u.isGuest ? '<small style="color:#64748b;">(Acceso rapido sin contrasena)</small>' : ''}
  `;

  document.getElementById("analytics-section").style.display = esStaff ? "grid" : "none";
  document.getElementById("audit-section").style.display = esStaff ? "block" : "none";

  cargarTickets();
  if (esStaff) cargarAuditoria();
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

async function cargarTickets() {
  const estado = document.getElementById("filtro-estado").value;
  const categoria = document.getElementById("filtro-categoria").value;
  const busqueda = document.getElementById("filtro-texto").value.toLowerCase();
  const contenedor = document.getElementById("lista-tickets");

  let query = "";
  if (estado) query = `?estado=${estado}`;
  else if (categoria) query = `?categoria=${categoria}`;

  try {
    const data = await ApiClient.request(query);
    ticketsCache = data.tickets || [];

    if (!estado && !categoria) {
      document.getElementById("kpi-total").textContent = ticketsCache.length;
      document.getElementById("kpi-abierto").textContent = ticketsCache.filter(t => t.estado === "ABIERTO").length;
      document.getElementById("kpi-progreso").textContent = ticketsCache.filter(t => t.estado === "EN_PROGRESO" || t.estado === "EN_REVISION").length;
      document.getElementById("kpi-vencidos").textContent = ticketsCache.filter(t => t.sla_estado === "VENCIDO").length;
      if (ApiClient.usuario?.rol !== "SOLICITANTE") {
        renderizarGraficas(ticketsCache);
      }
    }

    const filtrados = ticketsCache.filter(t =>
      t.titulo.toLowerCase().includes(busqueda) || t.ticket_id.toLowerCase().includes(busqueda)
    );

    if (filtrados.length === 0) {
      contenedor.innerHTML = "<p>No se encontraron incidencias con los filtros actuales.</p>";
      return;
    }

    const rol = ApiClient.usuario?.rol || "";
    const puedeOperar = rol === "ADMIN" || rol === "TECNICO";

    contenedor.innerHTML = filtrados.map(t => `
      <div class="ticket ${t.prioridad}">
        <div class="ticket-header">
          <div class="ticket-title">
            <span class="ticket-id">#${t.ticket_id}</span>${t.titulo}
          </div>
          <div class="badges">
            <span class="badge badge-cat">${t.categoria}</span>
            <span class="badge badge-prio-${t.prioridad}">${t.prioridad}</span>
            <span class="badge">${t.estado}</span>
            <span class="badge sla-${t.sla_estado}">SLA: ${t.sla_estado}</span>
          </div>
        </div>
        <p style="margin:0.5rem 0; color:#334155; line-height:1.45;">${t.descripcion}</p>
        <div class="meta">Reportado por: <strong>${t.solicitante || "Sistema"}</strong> | Ultimo evento: ${(t.historial?.slice(-1)[0]?.evento) || "Creado"}</div>
        <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
          ${puedeOperar && t.estado !== "RESUELTO" ? `<button onclick="avanzarEstado('${t.ticket_id}', '${t.estado}')">Avanzar flujo</button>` : ""}
          ${rol === "ADMIN" ? `<button class="danger" onclick="borrarTicket('${t.ticket_id}')">Eliminar</button>` : ""}
        </div>
        <div class="comments-box">
          <strong>Seguimiento y comentarios (${(t.comentarios || []).length}):</strong>
          ${(t.comentarios || []).map(c => `<div class="comment-item"><strong>${c.autor}</strong> (${c.rol}): ${c.texto}</div>`).join("")}
          <div class="comment-form">
            <input type="text" id="com-${t.ticket_id}" placeholder="Escribir comentario como ${ApiClient.usuario.nombre}..." />
            <button class="secondary" onclick="enviarComentario('${t.ticket_id}')">Comentar</button>
          </div>
        </div>
      </div>
    `).join("");
  } catch (err) {
    contenedor.innerHTML = `<p style="color:#dc2626;font-weight:700;">Alerta de Resiliencia Activa: ${err.message}</p>`;
  }
}

document.getElementById("form-ticket").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await ApiClient.request("", {
      method: "POST",
      body: JSON.stringify({
        solicitante: ApiClient.usuario?.nombre || "usuario",
        titulo: document.getElementById("titulo").value,
        categoria: document.getElementById("categoria").value,
        prioridad: document.getElementById("prioridad").value,
        descripcion: document.getElementById("descripcion").value,
      }),
    });
    e.target.reset();
    cargarTickets();
    if (ApiClient.usuario?.rol !== "SOLICITANTE") cargarAuditoria();
  } catch (err) {
    alert(err.message);
  }
});

async function avanzarEstado(id, actual) {
  const flujo = { ABIERTO: "EN_PROGRESO", EN_PROGRESO: "EN_REVISION", EN_REVISION: "RESUELTO" };
  const siguiente = flujo[actual] || "RESUELTO";
  const nota = prompt(`Nota de avance hacia ${siguiente}:`, "Diagnostico aplicado por soporte") || "";
  try {
    await ApiClient.request("", {
      method: "PUT",
      body: JSON.stringify({ ticket_id: id, estado: siguiente, nota }),
    });
    cargarTickets();
    cargarAuditoria();
  } catch (err) {
    alert(err.message);
  }
}

async function enviarComentario(id) {
  const input = document.getElementById(`com-${id}`);
  if (!input.value.trim()) return;
  try {
    await ApiClient.request("/tickets/comentarios", {
      method: "POST",
      body: JSON.stringify({
        ticket_id: id,
        autor: ApiClient.usuario?.nombre || "usuario",
        texto: input.value,
      }),
    });
    cargarTickets();
    if (ApiClient.usuario?.rol !== "SOLICITANTE") cargarAuditoria();
  } catch (err) {
    alert(err.message);
  }
}

async function borrarTicket(id) {
  if (!confirm(`Eliminar permanentemente el ticket #${id}?`)) return;
  try {
    await ApiClient.request(`?ticket_id=${id}`, { method: "DELETE" });
    cargarTickets();
    cargarAuditoria();
  } catch (err) {
    alert(err.message);
  }
}

async function cargarAuditoria() {
  try {
    const data = await ApiClient.request("/auditoria");
    const tbody = document.getElementById("audit-body");
    tbody.innerHTML = (data.auditoria || []).map(a => `
      <tr>
        <td>${a.timestamp.replace("T", " ").slice(0, 19)} UTC</td>
        <td><strong>${a.actor}</strong> (${a.rol})</td>
        <td><span class="badge badge-cat">${a.accion}</span></td>
        <td>${a.detalle}</td>
      </tr>
    `).join("");
  } catch (_) {}
}

function exportarCSV() {
  if (!ticketsCache.length) return alert("No hay datos para exportar");
  const filas = [
    ["ID", "Titulo", "Categoria", "Prioridad", "Estado", "SLA", "Solicitante", "FechaCreacion"],
    ...ticketsCache.map(t => [t.ticket_id, `"${t.titulo}"`, t.categoria, t.prioridad, t.estado, t.sla_estado, `"${t.solicitante || ""}"`, t.fecha_creacion])
  ];
  const csv = filas.map(f => f.join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `reporte_cutonala_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
}

actualizarVistaPrincipal();