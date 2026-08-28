FROM node:22-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM node:22-alpine

WORKDIR /app

COPY --from=build /app/dist ./dist
COPY server-headers.mjs .
COPY server.mjs .

EXPOSE 80

CMD ["node", "server.mjs"]
