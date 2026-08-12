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
const FLASK_API_KEY = process.env.FLASK_API_KEY || "";
const SESSION_DIR = process.env.BAILEYS_SESSION_DIR || "./sessions";

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

  // Función para enviar mensajes (usada por Flask vía HTTP)
  sock.sendText = async (numero, texto) => {
    const remoteJid = `${numero}@s.whatsapp.net`;
    await sock.sendMessage(remoteJid, { text: texto });
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
        await sock.sendText(payload.number, payload.text);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "ok" }));
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
