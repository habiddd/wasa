# Plan: equipos del dashboard → catálogo de WhatsApp Business (2026-09-30)

Pedido: cuando se crea un equipo desde el dashboard, que aparezca solo en el
catálogo de WhatsApp Business del número del bot (573245927008, vinculado
como dispositivo desde la app Business). Por ahora **sin imagen**.

## Investigación (hecha)

- Baileys instalado: `@whiskeysockets/baileys` 6.7.24. Trae
  `sock.productCreate(create)`, `sock.productUpdate(id, update)`,
  `sock.productDelete(ids)`, `sock.getCatalog({ jid, limit, cursor })`.
- `ProductCreate = { name, description, price, currency, retailerId?, url?,
  isHidden?, originCountryCode, images }`. En `Utils/business.js`
  (`toProductNode`): si `images` está vacío no manda el nodo `media`; si
  `price`/`currency` son `undefined` no los manda;
  `originCountryCode: undefined` manda `compliance_category=COUNTRY_ORIGIN_EXEMPT`.
  → Del lado de Baileys se puede sin imagen y sin precio. **Si el servidor de
  WhatsApp lo acepta no se sabe**: hay que probarlo (fase 0).
- No se puede abrir una segunda conexión de Baileys con la misma sesión para
  probar (WhatsApp tumba una de las dos, `conflict`). Toda prueba pasa por el
  proceso que ya corre → primero hay que exponer los endpoints (fase 1).
- Hoy Flask habla con Baileys por HTTP con `x-api-key` (`claveValida` en
  `baileys-bot/index.js`, `whatsapp_client._headers`). Se reusa igual.
- Los equipos se crean en `POST /dashboard/api/activos`
  (`routes/dashboard.py::api_crear_activo`). Un "equipo" del catálogo es un
  **modelo**, no una unidad: varias filas `Recurso` con el mismo `nombre` son
  unidades del mismo producto (así las agrupa el bot, `_grupos_activos`).
- Las migraciones de esquema en vivo las bloquea el permiso automático (pasó
  hoy). Diseño **sin columna nueva**: el vínculo recurso↔producto va por
  `retailerId` (el "SKU" del producto), que se puede buscar en el catálogo.

## Decisiones

- Un producto por `nombre` distinto de `tipo == "equipo"` (incluye accesorios
  como lentes, que el bot deja reservar). Los espacios no van (el pedido dice
  equipos).
- `retailerId = "lacs-" + slug(nombre)` (minúsculas, sin tildes, `-`, máx. 100).
  Antes de crear se busca en el catálogo: si ese `retailerId` ya existe (otra
  unidad del mismo modelo), no se duplica.
- `description`: `descripcion` del recurso; si no hay, "Marca Modelo" o la
  subcategoría; si nada, "Equipo de la LACS". Recortar a 1000 caracteres.
- Precio: sin precio si WhatsApp lo acepta; si no, `price: 0, currency: "COP"`
  (lo que funcione en la fase 0; dejarlo escrito en el código con el motivo).
- `url`: `CATALOGO_URL` de la config si está (la página de equipos de la LACS).
- `originCountryCode: undefined`, `isHidden: false`, `images: []`.
- **Crear el equipo nunca falla por el catálogo.** La sincronización corre
  después del commit, con timeout corto (15 s); si Baileys no está conectado o
  WhatsApp rechaza, el recurso queda creado, se loguea el error y la respuesta
  JSON trae `"catalogo": {"estado": "error", "detalle": "..."}`. En éxito:
  `{"estado": "creado" | "ya_existia", "id": "<id del producto>"}`.
  Para espacios: `{"estado": "no_aplica"}`.
- Solo creación (lo pedido). Editar/borrar un equipo NO toca el catálogo
  todavía; queda anotado como siguiente paso.

## Fase 0+1 — Endpoints en Baileys y prueba de lo que acepta WhatsApp

En `baileys-bot/index.js`, con el mismo `claveValida` y el mismo 503 si
`connectionState !== "open"`:
- `GET /catalog` → lista de productos propios (`getCatalog` con el JID propio
  normalizado — `jidNormalizedUser(sock.user.id)` —, recorriendo el cursor
  hasta el final). Devuelve `[{id, name, retailerId, isHidden, reviewStatus}]`.
- `POST /catalog/product` body `{name, description, retailerId, url?, price?,
  currency?}` → si ya hay un producto con ese `retailerId` devuelve
  `{status:"ya_existia", id}`; si no, `productCreate` y `{status:"creado", id}`.
  Errores de WhatsApp → 502 con el mensaje.
- `DELETE /catalog/product/:id` → `productDelete([id])`.

Luego, **probar contra WhatsApp real** (el usuario lo autorizó) con un
producto de prueba de nombre "PRUEBA LACS - borrar" y `retailerId`
"lacs-prueba-borrar", en este orden, parando en el primero que funcione:
1. sin imagen, sin precio;
2. sin imagen, `price: 0, currency: "COP"`;
3. sin imagen, `price: 1000, currency: "COP"`.
Anotar la respuesta exacta de cada intento. Verificar con `GET /catalog` que
aparece, y **borrarlo** con `DELETE`. Si los tres fallan, detenerse y
reportar (no seguir con Flask): en ese caso la imagen es obligatoria y hay
que hablarlo con el usuario.

## Fase 2 — Flask

- `whatsapp_client.py`: `crear_producto_catalogo(nombre, descripcion,
  retailer_id, url=None) -> dict` que llama `POST /catalog/product` (timeout
  15 s) y devuelve el dict de estado de arriba; nunca lanza.
- Nuevo módulo chico `app/catalogo_whatsapp.py` (o función en
  `whatsapp_client`, lo que dé menos código): `sincronizar_recurso(recurso)`
  arma nombre/descripcion/retailerId y llama al cliente; `no_aplica` si no es
  equipo.
- `api_crear_activo`: después del commit, `catalogo =
  sincronizar_recurso(recurso)` y agregarlo a la respuesta
  (`{**_serializar_recurso(recurso), "catalogo": catalogo}`).
- Frontend (`ActivoNuevoForm.tsx`): si `catalogo.estado == "error"`, mostrar un
  aviso no bloqueante ("El equipo se creó, pero no se pudo publicar en el
  catálogo de WhatsApp: …"), con el mismo componente de aviso/toast que ya use
  el formulario. Si es `creado`, un aviso corto de éxito. Nada más.

## Pruebas

- `entregables/OE3/pruebas/verificar_catalogo_whatsapp.py`: crea por `test_client` (con las
  credenciales del dashboard, como los otros verificar que usan endpoints) un
  equipo "PRUEBA LACS catálogo" → la respuesta trae `catalogo.estado ==
  "creado"`; `GET /catalog` de Baileys lo lista con su `retailerId`; crear otra
  unidad con el mismo nombre → `ya_existia` y el catálogo sigue con uno solo;
  crear un espacio → `no_aplica`. Al final **borra** el producto del catálogo
  y las filas de recurso de prueba. Con Baileys detenido o sin conectar no
  hace falta probarlo en vivo: basta un caso con `WHATSAPP_API_URL` apuntando
  a un puerto cerrado → el equipo se crea igual y `catalogo.estado == "error"`.
- Correr también `verificar_robustez_bot.py` y `verificar_celular_registro.py`
  para confirmar que nada se rompió.

Despliegue: `docker compose up -d --build baileys-bot flask-app
dashboard-frontend` y luego `docker compose restart dashboard-frontend`.
Reconstruir baileys-bot **no** debería pedir QR (la sesión vive en el volumen);
si lo pide, detenerse y avisar.
