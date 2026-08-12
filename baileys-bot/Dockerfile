FROM node:20-alpine

WORKDIR /app

# Instalar dependencias de compilación y git (necesario para algunos paquetes de Baileys)
RUN apk add --no-cache python3 make g++ git

COPY package.json ./
RUN npm install --omit=dev

COPY . .

EXPOSE 3000

CMD ["node", "index.js"]
