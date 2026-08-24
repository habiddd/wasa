const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require("@whiskeysockets/baileys");
const { Boom } = require("@hapi/boom");
const QRCode = require("qrcode");
const axios = require("axios");

// Configuración desde variables de entorno
const FLASK_WEBHOOK_URL = process.env.FLASK_WEBHOOK_URL || "http://flask-app:5000/webhook/whatsapp";
const FLASK_QR_URL = process.env.FLASK_QR_URL || "http://flask-app:5000/webhook/qr";
const FLASK_ACK_URL = process.env.FLASK_ACK_URL || "http://flask-app:5000/webhook/ack";
const FLASK_API_KEY = process.env.FLASK_API_KEY || "";
const SESSION_DIR = process.env.BAILEYS_SESSION_DIR || "./sessions";

// proto.WebMessageInfo.Status: ERROR=0, PENDING=1, SERVER_ACK=2,
// DELIVERY_ACK=3, READ=4, PLAYED=5 (confirmado contra la versión instalada).
const ESTADO_POR_STATUS = {
  0: "fallido",
  1: "pendiente",
  2: "enviado",
  3: "entregado",
  4: "leido",
  5: "reproducido",
};

// Estado global del socket y de la conexión (usado por /status y /send).
// Declarado aquí -no dentro de connectToWhatsApp- para que no quede
// shadowed por una variable local: antes `sock` nunca se asignaba a la
// instancia real y /send fallaba siempre con "Socket no conectado".
let sock;
let connectionState = "connecting"; // "connecting" | "qr" | "open" | "close"
let lastQrAt = null;
let phoneNumber = null;

async function connectToWhatsApp() {
  const { version } = await fetchLatestBaileysVersion();
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

  sock = makeWASocket({
    version,
    auth: state,
    browser: ["CPA Reservas Bot", "Chrome", "1.0.0"],
  });

  // Guardar credenciales cuando cambien
  sock.ev.on("creds.update", saveCreds);

  // Salvaguarda contra un bug conocido de Baileys: su buffer interno de
  // eventos (ev.buffer()/ev.flush()) a veces se queda atascado en modo
  // "buffering" para siempre si alguna tarea interna de sincronización
  // nunca resuelve (lo confirmamos en pruebas: tras mandar un mensaje,
  // ev.isBuffering() quedó en true indefinidamente). Mientras está
  // atascado, eventos como messages.update (y hasta messages.upsert) dejan
  // de llegar en silencio. Forzamos un flush si lleva "atascado" más de
  // unos segundos -- en operación normal isBuffering() vuelve a false casi
  // de inmediato, así que esto no debería disparar nunca en el camino feliz.
  const bufferWatchdog = setInterval(() => {
    if (sock?.ev?.isBuffering?.()) {
      console.warn("⚠️ Buffer de eventos de Baileys atascado, forzando flush");
      sock.ev.flush();
    }
  }, 5000);

  // Manejar códigos QR (viene en connection.update en versiones nuevas)
  sock.ev.on("qr", async (qr) => {
    console.log("📱 Escanea este código QR con WhatsApp:");
    const qrImage = await QRCode.toString(qr, { type: "terminal", small: true });
    console.log(qrImage);

    connectionState = "qr";
    lastQrAt = Date.now();

    // También enviamos el QR al webhook de Flask para que lo muestre en el dashboard
    try {
      await axios.post(
        `${FLASK_QR_URL}`,
        { qr: qr },
        { headers: { "x-api-key": FLASK_API_KEY, "Content-Type": "application/json" } }
      );
    } catch (err) {
      console.error("Error enviando QR al webhook:", err.message);
    }
  });

  // Manejar cambios de conexión (incluye QR en versiones nuevas)
  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;

    // Si el QR viene en connection.update (versiones nuevas de Baileys)
    if (qr) {
      console.log("📱 Escanea este código QR con WhatsApp:");
      QRCode.toString(qr, { type: "terminal", small: true })
        .then((qrImage) => console.log(qrImage))
        .catch(console.error);

      connectionState = "qr";
      lastQrAt = Date.now();

      // Enviar QR al webhook de Flask
      axios.post(
        `${FLASK_QR_URL}`,
        { qr: qr },
        { headers: { "x-api-key": FLASK_API_KEY, "Content-Type": "application/json" } }
      ).catch((err) => console.error("Error enviando QR al webhook:", err.message));
    }

    if (connection === "close") {
      connectionState = "close";
      phoneNumber = null;
      clearInterval(bufferWatchdog);

      const shouldReconnect =
        lastDisconnect.error instanceof Boom
          ? lastDisconnect.error.output.statusCode !== DisconnectReason.loggedOut
          : true;

      console.log("🔌 Conexión cerrada:", lastDisconnect.error?.message || "desconocido");

      if (shouldReconnect) {
        console.log("🔄 Reconectando...");
        setTimeout(() => connectToWhatsApp(), 3000);
      }
    } else if (connection === "open") {
      connectionState = "open";
      phoneNumber = sock.user?.id ? sock.user.id.split(":")[0].split("@")[0] : null;
      console.log("✅ Conectado a WhatsApp!");
    }
  });

  // Manejar mensajes entrantes
  sock.ev.on("messages.upsert", async (m) => {
    const messageInfo = m.messages[0];

    // Ignorar mensajes enviados por el propio bot
    if (messageInfo.key.fromMe) return;

    const remoteJid = messageInfo.key.remoteJid;
    const numero = remoteJid.split("@")[0];

    // Extraer texto del mensaje
    let texto = null;
    if (messageInfo.message?.conversation) {
      texto = messageInfo.message.conversation;
    } else if (messageInfo.message?.extendedTextMessage?.text) {
      texto = messageInfo.message.extendedTextMessage.text;
    }

    if (!texto) return;

    console.log(`📨 Mensaje de ${numero}: ${texto}`);

    // Enviar al webhook de Flask
    try {
      const response = await axios.post(
        FLASK_WEBHOOK_URL,
        {
          event: "messages.upsert",
          instance: "cpa-uninorte",
          data: {
            key: {
              remoteJid: remoteJid,
              fromMe: false,
            },
            message: messageInfo.message,
          },
        },
        {
          headers: {
            "x-api-key": FLASK_API_KEY,
            "Content-Type": "application/json",
          },
          timeout: 30000,
        }
      );

      // Si Flask responde con un mensaje para enviar, lo enviamos
      if (response.data?.reply) {
        await sock.sendMessage(remoteJid, { text: response.data.reply });
      }
    } catch (err) {
      console.error("Error enviando al webhook de Flask:", err.message);
    }
  });

  // Estado de entrega de mensajes salientes (server-ack/delivery-ack/leído/
  // error). Lo reenviamos a Flask para que quede en la auditoría y se vea
  // en el dashboard. `key.fromMe` filtra que solo nos importen los mensajes
  // que nosotros mandamos, no los que llegan del usuario.
  sock.ev.on("messages.update", (updates) => {
    for (const { key, update } of updates) {
      if (!key.fromMe || update.status === undefined) continue;
      const estado = ESTADO_POR_STATUS[update.status] || `desconocido_${update.status}`;
      axios.post(
        FLASK_ACK_URL,
        { id: key.id, estado },
        { headers: { "x-api-key": FLASK_API_KEY, "Content-Type": "application/json" } }
      ).catch((err) => console.error("Error enviando ack al webhook:", err.message));
    }
  });

  // Función para enviar mensajes (usada por Flask vía HTTP).
  // Devuelve el id del mensaje para poder trackear su estado de entrega.
  //
  // `destino` debe ser el JID completo tal cual llegó en messages.upsert
  // (ej: "573001234567@s.whatsapp.net" o "144186850431118@lid" -- WhatsApp
  // usa JIDs "@lid" para algunas cuentas en vez de "@s.whatsapp.net", y
  // reconstruirlo asumiendo siempre "@s.whatsapp.net" manda el mensaje a un
  // JID que no existe: Baileys lo acepta sin error pero WhatsApp nunca lo
  // entrega). Si no trae "@" (número suelto), se asume "@s.whatsapp.net".
  sock.sendText = async (destino, texto) => {
    const remoteJid = destino.includes("@") ? destino : `${destino}@s.whatsapp.net`;
    const enviado = await sock.sendMessage(remoteJid, { text: texto });
    return enviado?.key?.id || null;
  };
}

// Servidor HTTP simple para recibir comandos de envío de mensajes
const http = require("http");
const server = http.createServer(async (req, res) => {
  if (req.method === "POST" && req.url === "/send") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", async () => {
      let payload;
      try {
        payload = JSON.parse(body);
      } catch (err) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Bad request" }));
        return;
      }

      // connectionState !== "open" cubre tanto "todavía no hay socket"
      // (sock es undefined mientras arranca) como "hay socket pero la
      // sesión no está autenticada" (sin QR escaneado). Intentar enviar
      // en ese último caso hacía que Baileys lanzara una excepción no
      // capturada que tumbaba todo el proceso.
      if (connectionState !== "open" || !sock) {
        res.writeHead(503, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "WhatsApp no está vinculado/conectado" }));
        return;
      }

      try {
        // `jid` (JID completo, ej. "...@lid") tiene prioridad sobre `number`
        // (número suelto, se asume "@s.whatsapp.net") -- ver comentario en
        // sock.sendText sobre por qué reconstruir el JID desde el número
        // pierde chats que usan direccionamiento "@lid".
        const destino = payload.jid || payload.number;
        const mensajeId = await sock.sendText(destino, payload.text);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "ok", id: mensajeId }));
      } catch (err) {
        console.error("Error enviando mensaje por WhatsApp:", err.message);
        res.writeHead(502, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "No se pudo enviar el mensaje" }));
      }
    });
  } else if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok" }));
  } else if (req.method === "GET" && req.url === "/status") {
    // Estado de la conexión de WhatsApp en sí (no solo si el proceso vive).
    // Lo consume el dashboard de Flask.
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        status: connectionState,
        phoneNumber: phoneNumber,
        lastQrAt: lastQrAt,
        // Ver el comentario sobre bufferWatchdog más arriba. Si esto
        // aparece en true por más de unos segundos seguidos, algo se
        // atascó -- el watchdog debería resolverlo solo.
        eventBufferAtascado: sock?.ev?.isBuffering ? sock.ev.isBuffering() : null,
      })
    );
  } else {
    res.writeHead(404);
    res.end("Not found");
  }
});

connectToWhatsApp().catch((err) => {
  console.error("Error fatal:", err);
  process.exit(1);
});

server.listen(3000, () => {
  console.log("🚀 Servidor HTTP de Baileys escuchando en puerto 3000");
});
