// Cliente HTTP con reintentos y backoff exponencial ante fallas del servidor.
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

  async verificarSalud() {
    try {
      const data = await this.request("/health", {}, 1);
      this.actualizarSalud(true, `Servicio Operativo (${data.latencia_ms} ms)`);
    } catch (_) {
      // request() ya actualizo el indicador
    }
  },

  async request(path = "", options = {}, reintentos = 2) {
    const url = `${API_URL.replace(/\/$/, "")}${path}`;
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;

    for (let intento = 0; intento <= reintentos; intento++) {
      try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 10000);
        const res = await fetch(url, { ...options, headers, signal: controller.signal });
        clearTimeout(timer);
        const data = await res.json().catch(() => ({}));

        // 429 (throttling) y 5xx son transitorios: se reintenta con backoff.
        if ((res.status >= 500 || res.status === 429) && intento < reintentos) {
          this.actualizarSalud(false, `Reintentando conexion (${intento + 1}/${reintentos})...`);
          await new Promise(r => setTimeout(r, 600 * 2 ** intento));
          continue;
        }
        if (res.status >= 500 || res.status === 429) {
          this.actualizarSalud(false, `Servicio Degradado (${res.status})`);
        } else {
          this.actualizarSalud(true, "Servicio Operativo");
        }
        if (res.status === 401 && path !== "/auth/login") {
          // token vencido o invalido: se cierra la sesion del personal
          const err = new Error("Tu sesion expiro. Inicia sesion de nuevo.");
          err.sesionExpirada = true;
          throw err;
        }
        if (!res.ok) {
          const err = new Error(data.error || `Error HTTP ${res.status}`);
          err.status = res.status;
          throw err;
        }
        return data;
      } catch (err) {
        if (err.status || err.sesionExpirada) throw err; // errores de negocio: no reintentar
        if (intento === reintentos) {
          this.actualizarSalud(false, "Sin Conexion con el Servidor");
          throw new Error("No se pudo contactar al servidor. Revisa tu conexion o intenta mas tarde.");
        }
        await new Promise(r => setTimeout(r, 600 * 2 ** intento));
      }
    }
  },

  // Reduce fotos de celular (3-8 MB) a JPEG de max. 1600 px (~200-400 KB):
  // baja el costo de almacenamiento y transferencia de S3 y acelera la subida.
  async comprimirImagen(archivo, maxLado = 1600, calidad = 0.8) {
    if (archivo.size < 400 * 1024) return archivo;
    try {
      const bitmap = await createImageBitmap(archivo);
      const escala = Math.min(1, maxLado / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(bitmap.width * escala);
      canvas.height = Math.round(bitmap.height * escala);
      canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise(r => canvas.toBlob(r, "image/jpeg", calidad));
      return blob && blob.size < archivo.size ? new File([blob], "evidencia.jpg", { type: "image/jpeg" }) : archivo;
    } catch (_) {
      return archivo; // si el navegador no puede procesarla, se sube la original
    }
  },

  // Sube la imagen directo a S3 con la URL prefirmada que entrega la API.
  async subirEvidencia(ticketId, original) {
    if (!["image/jpeg", "image/png", "image/webp"].includes(original.type)) {
      throw new Error("Solo se permiten imagenes JPG, PNG o WEBP");
    }
    const archivo = await this.comprimirImagen(original);
    if (archivo.size > 5 * 1024 * 1024) throw new Error("La imagen supera 5 MB");
    const sub = await this.request("/tickets/evidencia", {
      method: "POST",
      body: JSON.stringify({ ticket_id: ticketId, content_type: archivo.type }),
    }, 0);
    const form = new FormData();
    Object.entries(sub.fields).forEach(([k, v]) => form.append(k, v));
    form.append("file", archivo);
    const res = await fetch(sub.url, { method: "POST", body: form });
    if (!res.ok) throw new Error(`S3 rechazo la imagen (HTTP ${res.status})`);
    return this.request("/tickets/evidencia/confirmar", {
      method: "POST",
      body: JSON.stringify({ ticket_id: ticketId, key: sub.key, autor: this.usuario?.nombre || "usuario" }),
    }, 1);
  },
};
