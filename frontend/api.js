const ApiClient = {
  token: localStorage.getItem("tf_token") || "",
  usuario: JSON.parse(localStorage.getItem("tf_user") || "null"),

  setSession(token, usuario) {
    this.token = token;
    this.usuario = usuario;
    localStorage.setItem("tf_token", token);
    localStorage.setItem("tf_user", JSON.stringify(usuario));
  },

  setGuestSession(nombre) {
    this.token = "";
    this.usuario = { nombre: nombre.trim(), rol: "SOLICITANTE", isGuest: true };
    localStorage.removeItem("tf_token");
    localStorage.setItem("tf_user", JSON.stringify(this.usuario));
  },

  clearSession() {
    this.token = "";
    this.usuario = null;
    localStorage.removeItem("tf_token");
    localStorage.removeItem("tf_user");
  },

  actualizarSalud(ok, texto) {
    const pill = document.getElementById("health-indicator");
    if (!pill) return;
    pill.textContent = texto;
    pill.className = ok ? "status-pill" : "status-pill degraded";
  },

  async request(path = "", options = {}, reintentos = 2) {
    const url = `${API_URL.replace(/\/$/, "")}${path}`;
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;

    for (let intento = 0; intento <= reintentos; intento++) {
      try {
        const res = await fetch(url, { ...options, headers });
        const data = await res.json();
        if (res.status >= 500 && intento < reintentos) {
          this.actualizarSalud(false, `Reintentando conexion (${intento + 1}/${reintentos})...`);
          await new Promise(r => setTimeout(r, 500 * (intento + 1)));
          continue;
        }
        this.actualizarSalud(res.status < 500, res.status < 500 ? "Servicio Operativo" : "Servicio Degradado (503)");
        if (!res.ok) throw new Error(data.error || `Error HTTP ${res.status}`);
        return data;
      } catch (err) {
        if (intento === reintentos) {
          this.actualizarSalud(false, "Sin Conexion con el Servidor");
          throw err;
        }
        await new Promise(r => setTimeout(r, 500 * (intento + 1)));
      }
    }
  }
};