# Exponer el dashboard a la red

Cómo se accede al dashboard desde otros equipos de la red local, por HTTP y
por HTTPS, y qué falta para sacarlo a internet.

## Qué queda expuesto y qué no

Solo el contenedor `dashboard-frontend` (Nginx) escucha en todas las
interfaces. Es la única puerta de entrada: pide el login del dashboard y pasa
`/dashboard/api/` a `flask-app` por la red interna de Docker.

| Servicio | Puerto en el host | Alcance |
|---|---|---|
| dashboard-frontend (HTTP) | `8080` | red local |
| dashboard-frontend (HTTPS) | `8443` | red local |
| flask-app | `127.0.0.1:5000` | solo este PC |
| baileys-bot | `127.0.0.1:3000` | solo este PC |
| postgres | `127.0.0.1:5432` | solo este PC |

`baileys-bot` no debe salir de localhost nunca: su `POST /send` hace que el
número de la LACS envíe mensajes.

## Direcciones

Con la IP actual del PC (`10.30.78.111`, red UNINORTE):

- HTTP: `http://10.30.78.111:8080/dashboard/`
- HTTPS: `https://10.30.78.111:8443/dashboard/`

La IP la asigna el DHCP y puede cambiar. Para verla: `ipconfig` →
"Dirección IPv4". Si se va a usar a diario, conviene reservarla en el router.

## Cómo está armado

- [docker-compose.yml](../docker-compose.yml): `dashboard-frontend` publica
  `"8080:80"` y `"8443:443"` (antes era `"127.0.0.1:8080:80"`) y monta
  `./dashboard-frontend/certs` en `/etc/nginx/certs`.
- [nginx.conf](../dashboard-frontend/nginx.conf): el mismo bloque `server`
  escucha en `80` y en `443 ssl`, así que las dos vías tienen exactamente las
  mismas rutas y el mismo login.
- [docker-entrypoint.sh](../dashboard-frontend/docker-entrypoint.sh): al
  arrancar, si no hay `fullchain.pem` y `privkey.pem` en
  `/etc/nginx/certs`, genera un certificado autofirmado (RSA 2048, 825 días).
  Sin esto, Nginx no arrancaría el puerto 443.
- `dashboard-frontend/certs/` está en `.gitignore`: ahí vive la llave privada.

Aplicar cambios:

```
docker compose up -d --build dashboard-frontend
```

## HTTP o HTTPS

**HTTP (8080)** funciona sin avisos, pero el usuario y la contraseña del
dashboard viajan en texto plano (Basic Auth). En una red compartida como
UNINORTE, cualquiera conectado podría capturarlos. Sirve para pruebas.

**HTTPS (8443)** cifra todo. Con el certificado autofirmado, el navegador
muestra "la conexión no es privada" la primera vez; se acepta con
"Avanzado → Continuar".

Cuando HTTPS esté en uso de forma estable, conviene quitar la línea
`"8080:80"` del compose para que nadie entre por la vía sin cifrar.

## Certificado real

Con un dominio que apunte a este equipo:

1. Obtener el certificado (por ejemplo con Let's Encrypt / certbot).
2. Copiar `fullchain.pem` y `privkey.pem` en `dashboard-frontend/certs/`,
   reemplazando los autofirmados.
3. `docker compose restart dashboard-frontend`.

El entrypoint no los toca si ya existen.

## Firewall de Windows

Docker Desktop ya trae una regla de entrada ("Docker Desktop Backend") para
el perfil Público, que es el de la red UNINORTE. Si aun así no carga desde
otro equipo, en PowerShell como administrador:

```
New-NetFirewallRule -DisplayName "LACS dashboard" -Direction Inbound -Protocol TCP -LocalPort 8080,8443 -Action Allow -Profile Public
```

## Sacarlo a internet (pendiente)

Esto es solo red local. Para internet, por ejemplo para registrar el webhook
de Meta (`/webhook/meta`, que Nginx ya expone sin login), hace falta HTTPS
con un dominio y certificado válido. Opciones:

- **Cloudflare Tunnel**: no abre puertos en el router y da HTTPS con dominio.
- **Tailscale**: si solo deben entrar dispositivos autorizados, sin exponer
  nada a internet.
- Proxy con certificado en un servidor con IP pública.

## Verificación hecha

Desde este PC, contra la IP de red (no `localhost`):

| URL | Sin credenciales | Con credenciales |
|---|---|---|
| `http://10.30.78.111:8080/dashboard/` | 401 | 200 |
| `https://10.30.78.111:8443/dashboard/` | 401 | 200 |

Falta la prueba desde otro equipo de la red.
